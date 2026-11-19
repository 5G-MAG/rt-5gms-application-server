#!/usr/bin/python3
#
# 5G-MAG Reference Tools: 5GMS Application Server
# ===============================================
#
# File: proxies/openresty.py
# License: 5G-MAG Public License (v1.0)
# Author: David Waring
# Copyright: (C) 2022-2024 British Broadcasting Corporation
#
# For full license terms please see the LICENSE file distributed with this
# program. If this file is missing then the license can be retrieved from
# https://drive.google.com/file/d/1cinCiA778IErENZ3JN52VFW-1ffHpx7Z/view
#
# This is the 5G-MAG Reference Tools 5GMS AS nginx web proxy handling module.
#
# This provides the OpenRestyWebProxy class and registers it with the proxy_factory.
#
'''
nginx WebProxyInterface module

This module implements the WebProxyInterface class for the nginx web server and
reverse proxy.
'''

import aiofiles
import asyncio
import json
import datetime
import hashlib
import importlib.resources
import logging
import os
import os.path
import regex
import shutil
import signal
import subprocess
import traceback

from typing import Dict, Optional, Tuple, List, Any, Set
from urllib.parse import urlparse

from ..proxy_factory import WebProxyInterface, add_web_proxy
from ..utils import find_executable_on_path, traverse_directory_tree, encode_resource_id, decode_resource_id
from ..context import Context, INGEST_PULL_PROTOCOLS, INGEST_LOW_LATENCY_PULL_PROTOCOL
from .. import m3d

class OpenRestyLocationConfig(object):
    '''
    Class to hold and compare location configurations
    '''
    def __init__(self, context: Context, path_prefix: str, downstream_prefix_url: str, provisioning_session: str, downstream_query: str = '', low_latency: bool = False):
        self.__context: Context = context
        if not Context.isProxySafeQuery(downstream_query):
            raise ValueError('The query of the ingest baseURL cannot be written into the web proxy configuration')
        # the query of the ingest baseURL (without the '?'): TS 26.512 V19.3.0 clause 8.2 replaces the prefix of the request URL with the
        # baseURL, so this is not a RFC 3986 clause 5.2 reference resolution (that would drop the last path segment and the base query)
        self.downstream_query: str = downstream_query
        self.path_prefix: str = path_prefix
        self.downstream_prefix_url: str = downstream_prefix_url
        self.provisioning_session: str = provisioning_session
        # TS 26.512 V19.3.0 clauses 8.4 and 10.3: the ingest protocol is the low-latency one, so partially received segments are passed on at once
        self.low_latency: bool = low_latency
        # any string can be a ResourceId (TS 26.512 V19.3.0 clause 6.4.2): the configuration file has it encoded
        self.__session_token: str = encode_resource_id(provisioning_session)
        self.rewrite_rules: List[Tuple[str,str]] = []
        # TS 26.512 V19.3.0 clauses 7.6.4.2, 7.6.4.5 and 7.6.4.6 and the domain name alias of TS 26.510 table 8.8.3.1-1
        self.caching_rules: List[Dict[str, Any]] = []
        self.url_signature: Optional[Dict[str, Any]] = None
        self.geo_fencing: Optional[Dict[str, Any]] = None
        self.cors_aliases: List[str] = []

        # Ensure prefix URLs end with a '/'
        if self.path_prefix[0] != '/':
            self.path_prefix = '/' + self.path_prefix
        if self.path_prefix[-1] != '/':
            self.path_prefix += '/'
        if self.downstream_prefix_url[-1] != '/':
            self.downstream_prefix_url += '/'

    @staticmethod
    def safe_pattern(pattern: str) -> bool:
        '''A regular expression that can be written in a configuration file and that compiles'''
        if '"' in pattern or '\n' in pattern or '\r' in pattern or pattern.endswith('\\'):
            return False
        try:
            regex.compile(pattern)
        except regex.error:
            return False
        return True

    def addCachingRule(self, url_pattern_filter: str, no_cache: Optional[bool], max_age: Optional[int], status_codes: Optional[List[int]]) -> bool:
        if not self.safe_pattern(url_pattern_filter):
            self.__context.appLog().error("Unsafe or invalid urlPatternFilter: %s", url_pattern_filter)
            return False
        self.caching_rules.append({'pattern': url_pattern_filter, 'noCache': bool(no_cache), 'maxAge': max_age,
                                   'statusCodes': sorted(status_codes) if status_codes else None})
        return True

    def setUrlSignature(self, sig: Any) -> bool:
        if not self.safe_pattern(sig.url_pattern):
            self.__context.appLog().error("Unsafe or invalid urlSignature.urlPattern: %s", sig.url_pattern)
            return False
        self.url_signature = {'urlPattern': sig.url_pattern, 'tokenName': sig.token_name, 'passphraseName': sig.passphrase_name,
                              'passphrase': sig.passphrase, 'tokenExpiryName': sig.token_expiry_name,
                              'useIPAddress': bool(sig.use_ip_address), 'ipAddressName': sig.ip_address_name}
        return True

    def setGeoFencing(self, geo: Any) -> None:
        self.geo_fencing = {'locatorType': geo.locator_type, 'locators': list(geo.locators)}

    def addCorsAlias(self, alias: str) -> None:
        if alias not in self.cors_aliases:
            self.cors_aliases.append(alias)

    def addRewriteRule(self, request_path_pattern: str, mapped_path: str) -> bool:
        (regex, replace) = self.__transform_rewrite_rules(request_path_pattern,mapped_path)
        if regex is None:
            self.__context.appLog().error("Unsafe or invalid rewrite rule: %s => %s", request_path_pattern, mapped_path)
            return False
        self.rewrite_rules += [(regex, replace)]
        return True

    def __eq__(self, other: "OpenRestyLocationConfig") -> bool:
        if self.path_prefix != other.path_prefix:
            return False
        if self.provisioning_session != other.provisioning_session or self.low_latency != other.low_latency:
            return False
        if self.downstream_prefix_url != other.downstream_prefix_url or self.downstream_query != other.downstream_query:
            return False
        if len(self.rewrite_rules) != len(other.rewrite_rules):
            return False
        for a in self.rewrite_rules:
            if a not in other.rewrite_rules:
                return False
        if self.caching_rules != other.caching_rules or self.url_signature != other.url_signature or \
                self.geo_fencing != other.geo_fencing or sorted(self.cors_aliases) != sorted(other.cors_aliases):
            return False
        return True

    def __ne__(self, other: "OpenRestyLocationConfig") -> bool:
        return not self == other

    def __lua_json(self) -> str:
        '''The configuration of the access rules of this location as a Lua long string'''
        text = json.dumps({'signature': self.url_signature, 'geo': self.geo_fencing, 'cors': self.cors_aliases})
        # a long string cannot contain its own closing brackets
        assert ']==]' not in text
        return '[==[' + text + ']==]'

    @staticmethod
    def __status_map(kind: str, source: str, codes: List[int], value: str, default: str) -> Tuple[str, str]:
        '''A map (http context) of the status codes of a caching rule to a value: returns the variable name and the map block'''
        key = '|'.join([kind, source, ','.join(str(int(c)) for c in codes), value, default])
        name = 'm4d_' + kind + '_' + hashlib.sha1(key.encode('utf-8')).hexdigest()[:12]
        block = f'  map ${source} ${name} {{\n    default "{default}";\n'
        block += ''.join(f'    {int(c)} "{value}";\n' for c in codes)
        return (name, block + '  }\n')

    def __rule_maps(self, rule: Dict[str, Any]) -> Dict[str, Tuple[str, str]]:
        '''The maps that restrict the no-cache directive and the Cache-Control header of a caching rule to its status code filter

        The status code filter of TS 26.510 table 8.8.3.1-1 limits the cachingDirectives to the origin response status codes
        listed; if it is absent they apply to all of them (no map is needed).
        '''
        maps: Dict[str, Tuple[str, str]] = {}
        codes = rule['statusCodes']
        if not codes:
            return maps
        if rule['noCache']:
            maps['nc'] = self.__status_map('nc', 'upstream_status', codes, '1', '0')
            maps['cc'] = self.__status_map('cc', 'status', codes, 'no-cache, no-store, max-age=0', '')
        return maps

    def status_maps(self) -> Dict[str, str]:
        '''The map blocks (for the http context of the configuration file) that the caching rules of the location refer to, by variable name'''
        maps: Dict[str, str] = {}
        for rule in self.caching_rules:
            for (name, block) in self.__rule_maps(rule).values():
                maps[name] = block
        return maps

    @staticmethod
    def caching_rules_signal_ttl(rule: Optional[Dict[str, Any]]) -> bool:
        '''Whether the responses of a caching rule signal a time-to-live (TS 26.510 table 8.8.3.1-1: noCache false and maxAge present)'''
        return rule is not None and not rule['noCache'] and rule['maxAge'] is not None

    def __cache_directives(self, prefix: str, rule: Optional[Dict[str, Any]]) -> str:
        '''The directives of a caching rule (TS 26.512 V19.3.0 clause 7.6.4.2); a location without a rule keeps what the origin directs'''
        if rule is None:
            return ''
        # TS 26.510 table 8.8.3.1-1: without statusCodeFilters the directives apply to all origin response status codes
        codes = ' '.join(str(int(c)) for c in rule['statusCodes']) if rule['statusCodes'] else 'any'
        maps = self.__rule_maps(rule)
        ret = f'{prefix}  proxy_ignore_headers Cache-Control Expires X-Accel-Expires Set-Cookie Vary;\n'
        ret += f'{prefix}  proxy_hide_header Cache-Control;\n{prefix}  proxy_hide_header Expires;\n'
        if rule['noCache']:
            if maps:
                # only the responses of the status codes in the filter are kept out of the cache and marked
                ret += f'{prefix}  proxy_no_cache ${maps["nc"][0]};\n'
                ret += f'{prefix}  add_header Cache-Control ${maps["cc"][0]} always;\n'
            else:
                ret += f'{prefix}  proxy_no_cache 1;\n{prefix}  proxy_cache_bypass 1;\n'
                ret += f'{prefix}  add_header Cache-Control "no-cache, no-store, max-age=0" always;\n'
        else:
            if rule['maxAge'] is not None:
                ret += f'{prefix}  proxy_cache_valid {codes} {int(rule["maxAge"])}s;\n'
                # the time-to-live that the response signals counts from the ingest: the header is set by the Lua block of the location
            else:
                # no maxAge: cached until the cache is purged or the configuration destroyed
                ret += f'{prefix}  proxy_cache_valid {codes} 10y;\n'
        return ret

    def __location_body(self, prefix: str, rule: Optional[Dict[str, Any]]) -> str:
        '''The directives of the location, with those of the caching rule if there is one'''
        # the cache key holds the request path as the M4d client sent it, so that the pattern of a purge request (TS 26.512 V19.3.0 clause 4.5.4.9,
        # TS 26.510 V19.2.0 clause 5.2.8.6: "the media resource URLs") meets what the Application Provider knows, and not the rewritten path
        # the key starts with a digest of the path rewrite rules: an entry cached under other rules (a 404 for a path that a rule now maps)
        # is not served after the rules changed; a purge takes the part after the '|' as the provisioning session
        rules_digest = hashlib.sha1('\n'.join(f'{a}\t{b}' for (a, b) in self.rewrite_rules).encode('utf-8')).hexdigest()[:8]
        ret = f'''{prefix}  set $m4d_path $uri;
{prefix}  set $m4d_key_prefix "{rules_digest}|{self.__session_token}";
{prefix}  set $downstream_prefix_url "{self.downstream_prefix_url}";
{prefix}  set $location_prefix "{self.path_prefix}";
'''
        if self.downstream_query:
            # sent with every request to the origin, unless a redirect of the origin leads elsewhere (the Lua block below)
            ret += f'{prefix}  set $downstream_base_query "{self.downstream_query}";\n{prefix}  set $downstream_query "";\n'
        for (regex, replace) in self.rewrite_rules:
            ret += f'{prefix}  rewrite "{regex}" "{replace}" break;\n'
        ret += f'{prefix}  proxy_cache_key "$m4d_key_prefix:u=$m4d_path";\n'
        if rule is None:
            # a cookie of the origin is not forwarded (clause 7.6.4.2) and does not keep the resource out of the cache
            ret += f'{prefix}  proxy_ignore_headers Set-Cookie;\n'
        ret += self.__cache_directives(prefix, rule)
        if self.low_latency:
            # clauses 8.4 and 10.3: a segment that is only partly received (a chunk of HTTP/1.1 chunked transfer coding) is available to the Media Player at
            # once, so nothing is buffered or held back for the cache; a byte range request, also the one that ends at 9007199254740991 (RFC 8673) and is
            # answered with 206 without Content-Length, goes to the origin
            ret += f'{prefix}  proxy_http_version 1.1;\n{prefix}  proxy_set_header Connection "";\n{prefix}  proxy_buffering off;\n{prefix}  proxy_cache off;\n'
        # CMCD data is for the collector of the AS and not for the origin (M2d): its request headers are not passed on
        for hdr in ('CMCD', 'CMCD-Request', 'CMCD-Object', 'CMCD-Status', 'CMCD-Session'):
            ret += f'{prefix}  proxy_set_header {hdr} "";\n'
        access = self.url_signature is not None or self.geo_fencing is not None or len(self.cors_aliases) > 0
        if access:
            ret += f'''{prefix}  access_by_lua_block {{
{prefix}    local cfg = m4daccess.cfg("{self.__session_token}:{self.path_prefix}", {self.__lua_json()})
{prefix}    if ngx.req.get_method() == "OPTIONS" and #cfg.cors > 0 then
{prefix}      m4daccess.cors(cfg.cors)
{prefix}      return ngx.exit(204)
{prefix}    end
{prefix}    if cfg.signature and not m4daccess.verify_signature(cfg.signature) then
{prefix}      return ngx.exit(ngx.HTTP_FORBIDDEN)
{prefix}    end
{prefix}    if cfg.geo and not m4daccess.check_geo(cfg.geo, "{self.__context.getConfigVar('5gms_as', 'geo_ranges_file', '')}") then
{prefix}      return ngx.exit(ngx.HTTP_FORBIDDEN)
{prefix}    end
{prefix}  }}
'''
        ret += f'''{prefix}  header_filter_by_lua_block {{
{prefix}    m4daccess.filter_origin_headers()
'''
        if self.caching_rules_signal_ttl(rule):
            codes = ','.join(f'[{int(c)}]=true' for c in rule['statusCodes']) if rule['statusCodes'] else ''
            ret += f'{prefix}    m4daccess.signal_ttl({int(rule["maxAge"])}, {{{codes}}}, "{self.__session_token}")\n'
        if len(self.cors_aliases) > 0:
            ret += f'''{prefix}    local cfg = m4daccess.cfg("{self.__session_token}:{self.path_prefix}", {self.__lua_json()})
{prefix}    m4daccess.cors(cfg.cors)
'''
        # with a query in the ingest baseURL: it is appended to the origin URL, except for a location that a redirect of the origin gave
        origin_prefix = map_query = proxy_query = ''
        forward_query = f'{prefix}    ngx.var.downstream_prefix_url = ngx.var.downstream_prefix_url..dynredir.suffix(query)\n'
        # the names of the parameters of the URL signature are the proof for the AS (clause 7.6.4.5)
        key_drop = ', {' + ', '.join(json.dumps(n) for n in (self.url_signature['tokenName'], self.url_signature['tokenExpiryName'])) + '}' if self.url_signature else ''
        if self.downstream_query:
            origin_prefix = f'{prefix}    local origin_prefix = ngx.var.downstream_prefix_url\n'
            map_query = f'''
{prefix}    if ngx.var.downstream_prefix_url == origin_prefix then
{prefix}      ngx.var.downstream_query = "?"..ngx.var.downstream_base_query
{prefix}    end'''
            proxy_query = '$downstream_query'
            # the query of the baseURL comes first; a request to the endpoint of a redirect has the query of its target only
            forward_query = f'''{prefix}    if query ~= "" then
{prefix}      if ngx.ctx.redirected then
{prefix}        ngx.var.downstream_prefix_url = ngx.var.downstream_prefix_url.."?"..query
{prefix}      else
{prefix}        ngx.var.downstream_query = ngx.var.downstream_query.."&"..query
{prefix}      end
{prefix}    end
'''
        # TS 26.512 V19.3.0 clause 4.6: a request that cannot be served from the cache is transformed into a GET request to the origin, so only
        # GET and HEAD reach it (and the CORS preflight of the aliases, which the access phase answers)
        allowed_methods = ['GET', 'HEAD'] + (['OPTIONS'] if len(self.cors_aliases) > 0 else [])
        method_test = ' and '.join(f'method ~= "{m}"' for m in allowed_methods)
        allow_header = ', '.join(allowed_methods)
        ret += f'''{prefix}  }}
{prefix}  rewrite_by_lua_block {{
{prefix}    local method = ngx.req.get_method()
{prefix}    if {method_test} then
{prefix}      ngx.header["Allow"] = "{allow_header}"
{prefix}      return ngx.exit(405)
{prefix}    end
{prefix}    -- ngx.log(ngx.DEBUG,"rewrite_by_lua_block(",ngx.var.uri,", ",ngx.var.downstream_prefix_url,")")
{prefix}    local uri = ngx.var.uri
{prefix}    if uri:sub(1,{len(self.path_prefix)}) == "{self.path_prefix}" then
{prefix}      uri = uri:sub({len(self.path_prefix)})
{prefix}    end
{prefix}    -- ngx.log(ngx.DEBUG,"rewrite_by_lua_block: uri = ", uri)
{origin_prefix}{prefix}    ngx.var.downstream_prefix_url,ngx.ctx.uri,ngx.ctx.redirected = dynredir.mapUrl("{self.path_prefix}", ngx.var.downstream_prefix_url, uri){map_query}
{prefix}    ngx.req.set_uri(uri)
{prefix}    ngx.var.downstream_prefix_url = ngx.var.downstream_prefix_url..dynredir.escape_path(ngx.ctx.uri:sub(2))
{prefix}    -- ngx.log(ngx.DEBUG,"rewrite_by_lua_block: proxy=", ngx.var.downstream_prefix_url,", uri=",uri)

{prefix}    -- Execute CMCD handler per request 
{prefix}    if uri:find("%.m4s$") or uri:find("%.mp4$") or uri:find("%.m4v$") or uri:find("%.m4a$") or uri:find("%.mpd$") then
{prefix}      if cmcd_response and cmcd_response.reportToDashboard then
{prefix}        local ok, err = pcall(cmcd_response.reportToDashboard)
{prefix}        if not ok then
{prefix}          ngx.log(ngx.ERR, "[cmcd] reportToDashboard() failed: ", err)
{prefix}        end
{prefix}      end
{prefix}    end
{prefix}    -- the CMCD query parameter is not passed to the origin either
{prefix}    local args = ngx.var.args
{prefix}    if args then
{prefix}      ngx.var.args = ngx.re.gsub(args, "(^|&)CMCD=[^&]*", "", "jo")
{prefix}    end
{prefix}    -- clause 8.2: the prefix of the request URL is replaced, so the query goes to the origin (a request to the endpoint of a redirect carries the
{prefix}    -- query of the target of the redirect); the CMCD parameter is removed above (clause 8.1)
{prefix}    local query = dynredir.query(ngx.var.args)
{forward_query}{prefix}    -- clause 10.1: a transport resource is addressed by its URL, so the query is part of the cache key (without the proof of a URL signature,
{prefix}    -- which is for the AS: the resource is the same for every client)
{prefix}    ngx.var.m4d_path = ngx.var.m4d_path..dynredir.suffix(dynredir.query(ngx.var.args{key_drop}))
{prefix}  }}
{prefix}  log_by_lua_block {{
{prefix}    -- the CMCD information of the request goes to the reporter of the AS (reference point M3d), if M3d is configured
{prefix}    if m3d_cmcd then m3d_cmcd.forward("{self.__session_token}") end
{prefix}  }}
{prefix}  proxy_pass $downstream_prefix_url{proxy_query};
{prefix}  proxy_intercept_errors on;
{prefix}  error_page 301 302 303 307 308 = @handle_redirect;
'''
        return ret

    async def config(self, indent: int = 0) -> str:
        prefix = ' ' * indent
        ret = f'{prefix}location {self.path_prefix} {{\n'
        ret += self.__location_body(prefix, None)
        # the caching rules are matched in order, the first match applies (TS 26.512 V19.3.0 clause 7.6.4.2): nginx takes the first
        # matching regular expression location inside this one. A pattern that starts with ^ starts at the service location.
        for rule in self.caching_rules:
            pattern = rule['pattern']
            if pattern.startswith('^'):
                pattern = '^' + regex.escape(self.path_prefix[:-1]) + pattern[1:]
            ret += f'{prefix}  location ~ "{pattern}" {{\n'
            ret += self.__location_body(prefix + '  ', rule)
            ret += f'{prefix}  }}\n'
        ret += f'{prefix}}}\n'
        return ret

    def __transform_rewrite_rules(self, rule_regex, replace):
        '''Modify the rewrite rule to work in nginx

        Checks the regex for bracketed expressions, apply any ECMA regex to
        nginx (perl) regex syntax changes and add a suffix catchall and replace
        for the basename component of the URL.

        Returns the modified regex and replacement strings.
        '''
        # Note: ECMA RegExp and Perl regex (as used by nginx) syntax are
        #       compatible, ECMA appears to be a subset of Perl. Therefore the
        #       regex shouldn't need any transformation.

        # Python regex uses Perl like syntax so check the regex by compiling in
        # Python.
        try:
            compiled_regex = regex.compile(rule_regex)
        except regex.error as e:
            self.__context.appLog().error("Error in request_pattern: %s", traceback.format_exc())
            return (None,None)

        # Get number of bracketed expressions for back-references from regex
        brackets = compiled_regex.groups

        if rule_regex[0] == '^':
            # the pattern is relative to the service location (the baseURL of the distribution), which the request URI carries in front
            location = self.path_prefix[:-1]
            rule_regex = '^' + regex.escape(location) + rule_regex[1:]
            replace = location + replace

        # pathRewriteRule only deals with replacing a path part, so we need to
        # include the rest of the URL path in the nginx rewrite rule.
        if rule_regex[0] != '^':
            # a lazy group, so that the match starts where the pattern first matches and the matched portion (which a leading '.*' of
            # the pattern may stretch over the whole path) is what mappedPath replaces; nginx reads '${1}' as the variable "1", so the
            # group is named and written as a variable, which also keeps a digit at the start of mappedPath from joining the reference
            # (TS 26.512 V19.3.0 clause 7.6.4.2: the rules apply to the path segments after the baseURL, so the head starts with the service
            # location and the pattern cannot match inside it)
            rule_regex = '^(?<m4d_path_head>' + regex.escape(self.path_prefix[:-1]) + '.*?)' + rule_regex
            replace = '${m4d_path_head}' + replace
            brackets += 1
        if rule_regex[-1] != '$':
            rule_regex = rule_regex + '([^?#]*/)?'
            replace = replace + '$%i'%(brackets+1)
            brackets += 1
        else:
            # remove '$' as we need to match entire URL path in nginx
            rule_regex = rule_regex[:-1]
        rule_regex = rule_regex + '([^/]*(?:#[^?/]*)?(?:\\?.*)?)$'
        replace = replace + '$%i'%(brackets+1)

        return (rule_regex, replace)

class OpenRestyServerConfig(object):
    '''
    Class to hold and compare server configurations
    '''
    def __init__(self, context: Context, hostnames: Set[str], use_cache: bool = False, certfile: Optional[str] = None, docroot: Optional[str] = None):
        self.__context: Context = context
        self.hostnames: Set[str] = hostnames
        self.certificate_file: Optional[str] = certfile
        if certfile is not None:
            self.port: int = int(self.__context.getConfigVar('5gms_as','https_port'))
        else:
            self.port: int = int(self.__context.getConfigVar('5gms_as','http_port'))
        self.locations: Dict[str,OpenRestyLocationConfig] = {}
        self.use_cache: bool = use_cache
        if docroot is None:
            master_hosts = [host for host in hostnames if host not in ['localhost', '127.0.0.1']]
            if len(master_hosts) > 0:
                master_host = master_hosts[0]
            else:
                master_host = 'localhost'
            docroot = os.path.join(self.__context.getConfigVar('5gms_as','docroot'), master_host)
        self.docroot: str = docroot
        if not os.path.exists(docroot):
            old_umask = os.umask(0)
            try:
                os.makedirs(docroot, mode=0o755)
            finally:
                os.umask(old_umask)
            for copy_file in ['404.html', '50x.html']:
                src = os.path.join('/usr/share/nginx/html', copy_file)
                if os.path.exists(src):
                    self.__context.appLog().debug("Copy %s => %s", src, docroot)
                    shutil.copy2(src, docroot)

    async def config(self, indent: int = 0) -> str:
        prefix = ' ' * indent
        ssl_flag = ''
        if self.certificate_file is not None:
            ssl_flag = ' ssl'
        ret  = f'''{prefix}server {{
{prefix}  listen {self.port}{ssl_flag};
{prefix}  listen [::]:{self.port}{ssl_flag};
{prefix}  server_name {' '.join(self.hostnames)};
{prefix}  root {self.docroot};

'''
        if self.certificate_file is not None:
            ret += f'''{prefix}  ssl_certificate {self.certificate_file};
{prefix}  ssl_certificate_key {self.certificate_file};

'''
        if self.use_cache:
            ret += f'{prefix}  proxy_cache cacheone;\n\n'
        for locn in self.locations.values():
            ret += await locn.config(indent+2)
            ret += '\n'
        ret += f'''{prefix}  location @handle_redirect {{
{prefix}    rewrite_by_lua_block {{
{prefix}      -- ngx.log(ngx.DEBUG,"rewrite_by_lua_block(",ngx.var.upstream_http_location,",",ngx.var.location_prefix,",",ngx.var.upstream_status,")")
{prefix}      -- the Location may be a relative reference: it is resolved against the URL that was requested from the origin
{prefix}      local origin_redir_prefix, redir_object = dynredir.split_location(ngx.var.upstream_http_location, ngx.var.downstream_prefix_url)
{prefix}      ngx.ctx.origin_redir_prefix = origin_redir_prefix
{prefix}      -- ngx.log(ngx.DEBUG, "ngx.ctx.origin_redir_prefix = '",ngx.ctx.origin_redir_prefix,"', redir_object = '",redir_object,"'")
{prefix}      ngx.ctx.m4_redir_prefix = dynredir.get(ngx.var.location_prefix, ngx.ctx.origin_redir_prefix)
{prefix}      ngx.redirect(ngx.ctx.m4_redir_prefix..redir_object, ngx.var.upstream_status)
{prefix}    }}
{prefix}    body_filter_by_lua_block {{
{prefix}      -- ngx.log(ngx.DEBUG, "body_filter_by_lua_block(",ngx.ctx.origin_redir_prefix,",",ngx.ctx.m4_redir_prefix,")")
{prefix}      if (ngx.arg[1]) then
{prefix}        ngx.arg[1] = ngx.re.sub(ngx.arg[1], re_escape(ngx.ctx.origin_redir_prefix), ngx.ctx.m4_redir_prefix)
{prefix}      end
{prefix}    }}
{prefix}  }}

'''
        #ret += prefix + '  location / {\n'
        #ret += prefix + '    return 404;\n'
        #ret += prefix + '  }\n'
        #ret += '\n'

        ret += f'''{prefix}  error_page 404 /404.html;
{prefix}  location = /404.html {{
{prefix}  }}
{prefix}
{prefix}  error_page 500 502 503 504 /50x.html;
{prefix}  location = /50x.html {{
{prefix}  }}
{prefix}}}
'''
        return ret

    def addLocation(self, locn: OpenRestyLocationConfig) -> None:
        if locn.path_prefix in self.locations:
            if locn != self.locations[locn.path_prefix]:
                raise RuntimeError('Conflicting locations with the same path prefix: '+locn.path_prefix)
        else:
            self.locations[locn.path_prefix] = locn

    def sameLocations(self, other: "OpenRestyServerConfig") -> bool:
        if len(self.locations) != len(other.locations):
            return False
        for a in self.locations:
            if a not in other.locations:
                return False
        return True

    def mergeServer(self, other: "OpenRestyServerConfig") -> bool:
        if self.use_cache != other.use_cache:
            return False
        if self.docroot != other.docroot:
            return False
        if self.certificate_file is None and other.certificate_file is not None:
            return False
        if self.certificate_file is not None and other.certificate_file is None:
            return False
        if self.certificate_file is not None and other.certificate_file != self.certificate_file:
            return False
        if self.port != other.port:
            return False
        if not self.sameLocations(other):
            return False
        self.hostnames.update(other.hostnames)
        return True

class OpenRestyWebProxy(WebProxyInterface):
    '''
    WebProxyInterface class to handle the nginx web server
    '''
    def __init__(self, context):
        '''
        Constructor

        Initialise the nginx WebProxyInterface class.
        '''
        super().__init__(context)
        for directory in [
            context.getConfigVar('5gms_as.nginx', 'client_body_temp'),
            context.getConfigVar('5gms_as.nginx', 'proxy_temp'),
            context.getConfigVar('5gms_as.nginx', 'fastcgi_temp'),
            context.getConfigVar('5gms_as.nginx', 'uwsgi_temp'),
            context.getConfigVar('5gms_as.nginx', 'scgi_temp'),
            os.path.dirname(context.getConfigVar('5gms_as.nginx', 'pid_path', '')),
            ]:
            if directory is not None and len(directory) > 0 and not os.path.isdir(directory):
                old_umask = os.umask(0)
                try:
                    os.makedirs(directory, mode=0o755)
                finally:
                    os.umask(old_umask)

    __nginx = None
    __last_nginx_check = None

    @classmethod
    def isPresent(cls):
        '''
        Check if nginx is present in the system
        '''
        now = datetime.datetime.now()
        if cls.__nginx is None or cls.__last_nginx_check is None or cls.__last_nginx_check + datetime.timedelta(seconds=5) < now:
            # Only recheck if its been more than 5 seconds after the last check
            cls.__last_nginx_check = now
            cls.__nginx = find_executable_on_path("nginx", verify=cls.__ensure_modules, extra_paths=['/usr/local/openresty/nginx/sbin'])
            if cls.__nginx is not None:
                logging.getLogger(cls.__name__).info(f"Found nginx executable supporting LUA at {cls.__nginx}")
        return cls.__nginx is not None

    @classmethod
    def name(cls):
        '''
        Return nginx name
        '''
        return "openresty"

    async def writeConfiguration(self):
        '''
        Write out the nginx configuration file

        Converts the ContentHostingConfigurations from the AS context to an
        nginx configuration file.

        Return True if it the configuration could be generated and writted to a
               file.
        '''
        config_file = self._context.getConfigVar('5gms_as.nginx','config_file')
        resolvers = self._context.getConfigVar('5gms_as.nginx','resolvers')
        http_port = self._context.getConfigVar('5gms_as','http_port')
        error_log_path = self._context.getConfigVar('5gms_as','error_log')
        access_log_path = self._context.getConfigVar('5gms_as','access_log')
        pid_path = self._context.getConfigVar('5gms_as.nginx','pid_path')
        client_body_tmp = self._context.getConfigVar('5gms_as.nginx','client_body_temp')
        proxy_cache_path = self._context.getConfigVar('5gms_as','cache_dir')
        proxy_temp_path = self._context.getConfigVar('5gms_as.nginx','proxy_temp')
        fastcgi_temp_path = self._context.getConfigVar('5gms_as.nginx','fastcgi_temp')
        uwsgi_temp_path = self._context.getConfigVar('5gms_as.nginx','uwsgi_temp')
        scgi_temp_path = self._context.getConfigVar('5gms_as.nginx','scgi_temp')
        nginx_module_includes = '\n'.join([f'include\t{p}/*.conf;' for p in ['/usr/share/nginx/modules'] if os.path.isdir(p)])
        mime_types_file = 'mime.types'
        for mtf in ['/usr/local/openresty/nginx/conf/mime.types', '/etc/nginx/mime.types']:
            if os.path.isfile(mtf):
                mime_types_file = mtf
                self.log.info(f"Found mime.types file at {mime_types_file}")
                break
        else:
            self.log.error("Could not find the mime.types file")
            raise FileNotFoundError
        scriptdir = os.path.dirname(os.path.abspath(__file__))

        cmcd_collector_url = self._context.getConfigVar('5gms_as', 'cmcd_collector_url', '')
        cmcd_timeout_ms = self._context.getConfigVar('5gms_as', 'cmcd_timeout_ms', '')
        cmcd_lua_dir = self._context.getConfigVar('5gms_as', 'cmcd_lua_dir', '')
        # the Unix socket on which the CMCD information of M4d requests is handed to the reporter of the AS (reference point M3d); empty: off
        m3d_samples_socket = m3d.samples_socket_path(self._context)

        # Create caching directives if we have a cache dir configured
        proxy_cache_path_directive = ''
        if proxy_cache_path is not None and len(proxy_cache_path) > 0:
            # nginx removes an entry that was not used for 10 minutes unless told otherwise, which would cut short the minimum caching period
            # of the caching configuration (TS 26.510 V19.2.0 clause 8.8.3.1, maxAge): the idle limit is configurable and long by default
            cache_inactive = self._context.getConfigVar('5gms_as', 'cache_inactive', '30d')
            proxy_cache_path_directive = 'proxy_cache_path %s levels=1:2 use_temp_path=on keys_zone=cacheone:10m inactive=%s;'%(proxy_cache_path, cache_inactive)

        # Create the server configurations from the CHCs
        server_configs: Dict[Tuple[str,Optional[str]], OpenRestyServerConfig] = {}
        for provisioning_session_id in self._context.getProvisioningSessionIds():
            if not self._context.isContentHostingConfigurationActive(provisioning_session_id):
                # a deactivated Content Hosting Configuration stays configured but is not served at M4 (TS 26.512 clause 4.5.4.7)
                continue
            i = self._context.findContentHostingConfigurationByProvisioningSession(provisioning_session_id)
            if i.ingest_configuration.mode != 'PULL' or i.ingest_configuration.protocol not in INGEST_PULL_PROTOCOLS + (INGEST_LOW_LATENCY_PULL_PROTOCOL,):
                self.log.error("Can only handle http-pull sources at present")
                return False
            downstream_origin = i.ingest_configuration.base_url
            if downstream_origin is None:
                self.log.error("Configuration must have an ingestConfiguration.baseURL")
                return False
            if not isinstance(downstream_origin, str):
                downstream_origin = str(downstream_origin)
            # an AbsoluteUrl may have a query (TS 26.510 clause 8.8.3.1): it is kept apart from the path prefix of the origin URL
            downstream_origin, _, downstream_query = downstream_origin.partition('?')
            if downstream_origin[-1] == '/':
                downstream_origin = downstream_origin[:-1]
            for dc in i.distribution_configurations:
                # TS 26.510 V19.2.0 table 8.8.3.3-1: content distributed exclusively via a supplementary network is not available at M4
                if any(n.distribution_mode == 'MODE_EXCLUSIVE' for n in dc.supplementary_distribution_networks or []):
                    continue
                certificate_filename = None
                if dc.certificate_id is not None:
                    certificate_filename = self._context.getCertificateFilename(dc.certificate_id)
                sk = (dc.canonical_domain_name, certificate_filename)
                if sk not in server_configs:
                    server_configs[sk] = OpenRestyServerConfig(self._context, {dc.canonical_domain_name}, proxy_cache_path is not None, certificate_filename)
                if dc.domain_name_alias is not None:
                    dsk = (dc.domain_name_alias, certificate_filename is not None)
                    if dsk not in server_configs:
                        server_configs[dsk] = OpenRestyServerConfig(self._context, {dc.domain_name_alias}, proxy_cache_path is not None, certificate_filename)
                base_url = urlparse(str(dc.base_url))
                m4d_path_prefix = base_url.path
                if m4d_path_prefix[0] != '/':
                    m4d_path_prefix = '/' + m4d_path_prefix
                if m4d_path_prefix[-1] != '/':
                    m4d_path_prefix += '/'
                locn = OpenRestyLocationConfig(self._context, m4d_path_prefix, downstream_origin, provisioning_session_id, downstream_query,
                                               i.ingest_configuration.protocol == INGEST_LOW_LATENCY_PULL_PROTOCOL)
                if dc.path_rewrite_rules is not None:
                    for rr in dc.path_rewrite_rules:
                        if not locn.addRewriteRule(rr.request_path_pattern, rr.mapped_path):
                            return False
                for cc in dc.caching_configurations or []:
                    if not locn.addCachingRule(cc.url_pattern_filter, cc.caching_directives.no_cache, cc.caching_directives.max_age, cc.caching_directives.status_code_filters):
                        return False
                if dc.url_signature is not None and not locn.setUrlSignature(dc.url_signature):
                    return False
                if dc.geo_fencing is not None:
                    locn.setGeoFencing(dc.geo_fencing)
                if dc.domain_name_alias is not None:
                    locn.addCorsAlias(dc.domain_name_alias)
                server_configs[sk].addLocation(locn)
                if dc.domain_name_alias is not None:
                    server_configs[dsk].addLocation(locn)
        changed = True
        while changed:
            changed = False
            keys = list(server_configs.keys())
            for i in range(len(keys)):
                if changed:
                    break
                for j in range(i+1, len(keys)):
                    if server_configs[keys[i]].mergeServer(server_configs[keys[j]]):
                        changed = True
                        del server_configs[keys[j]]
                        break
        config = ''
        status_maps_by_name: Dict[str, str] = {}
        for svr in server_configs.values():
            config += await svr.config(2)
            config += '\n'
            for locn in svr.locations.values():
                status_maps_by_name.update(locn.status_maps())
        server_configs = config
        status_maps = ''.join(status_maps_by_name[name] for name in sorted(status_maps_by_name))
        try:
            # Try to write out the configuration file using nginx.conf.tmpl as
            # a template for the configuration file.
            with open(config_file, 'w') as conffile:
                with importlib.resources.open_text(__package__,'nginx.conf.tmpl') as template:
                    for line in template:
                        conffile.write(line.format(**locals()))
        except:
            raise
        return True

    async def tidyConfiguration(self):
        '''
        Tidy configuration files

        Delete the automatically generated nginx configuration.
        '''
        try:
            os.unlink(self._context.getConfigVar('5gms_as.nginx','config_file'))
        except FileNotFoundError:
            pass
        return True

    async def startDaemon(self):
        '''
        Start the nginx process

        Starts the nginx process in the foreground using the configuration
        written out using the writeConfiguration() method.
        '''
        cmd = self.__class__.__nginx
        if cmd is None:
            return False
        # Only include the command line arguments accepted by the local nginx
        cmd_line = self.__check_nginx_flags(cmd,[('-e',self._context.getConfigVar('5gms_as', 'error_log')), ('-c',self._context.getConfigVar('5gms_as.nginx','config_file')), ('-g','daemon off;')])
        return await self._startDaemon(cmd_line)

    async def wait(self):
        '''
        Wait for nginx process to exit

        Dumps the stdout and/or stderr from the process after exit.
        '''
        ret = await self._wait()
        if ret:
            out = self.daemonStdout()
            if self.daemonReturnCode() != 0:
                if out is not None:
                    self.log.info(out)
                err = self.daemonStderr()
                if err is not None:
                    self.log.error(self.daemonStderr())
            else:
                if out is not None:
                    self.log.info(out)
        else:
            err = self.daemonStderr()
            if err is not None:
                self.log.error(err)
        return ret

    async def reload(self):
        '''Reload nginx configuration

        This will remove the old config, write out the new config and signal
        the daemon to reload.
        '''
        if self.daemonRunning():
            if not await self.tidyConfiguration():
                return False
            if not await self.writeConfiguration():
                return False
            if not await self.signalDaemon(signal.SIGHUP):
                return False
        return True

    @staticmethod
    def __workerPids(master_pid):
        '''The process identifiers of the child processes (the workers) of the nginx master process, or None'''
        try:
            with open('/proc/%d/task/%d/children'%(master_pid, master_pid)) as children:
                return set(int(p) for p in children.read().split())
        except (OSError, ValueError):
            return None

    async def reloadAndWait(self, timeout: float = 5.0):
        '''Reload nginx and wait until all the worker processes of the old configuration have been replaced

        Returns True when the new configuration is in use for new connections, None if the reload failed and False
        if the workers had not been replaced after _timeout_ seconds (the reload is still in progress, or nginx kept the
        old configuration because it could not use the new one) or if the workers cannot be observed. The M3 operations
        report success only for True. With no nginx process running nothing is in use: the configuration is written
        when nginx is started, so that is True.
        '''
        master = self.daemonPid()
        if master is None:
            return True
        before = self.__workerPids(master)
        if not await self.reload():
            return None
        if before is None:
            return False
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            now = self.__workerPids(master)
            if now and not (now & before):
                return True
            await asyncio.sleep(0.05)
        return False

    async def _getCacheFilesAndKeys(self) -> List[Tuple[str,str,str]]:
        self._context.appLog().debug('Getting NGINX cache entries...')
        proxy_cache_path = self._context.getConfigVar('5gms_as','cache_dir')
        result = []
        if proxy_cache_path is not None and len(proxy_cache_path) != 0:
            result = await traverse_directory_tree(proxy_cache_path, self.__add_cache_entry, result)
            #self._context.appLog().debug('Entries = %r', result)
        return result

    async def _postPurgeActions(self):
        self._context.appLog().debug('Sending HUP to NGINX...')
        await self.signalDaemon(signal.SIGHUP)

    async def __add_cache_entry(self, filename: str, isdir: bool, result: Any):
        #self._context.appLog().debug('nginx.__add_cache_entry(%r, %r, ...)', filename, isdir)
        if isdir:
            return result
        keyinfo = await self.__cache_entry_from_filename(filename)
        #self._context.appLog().debug('nginx.__add_cache_entry: keyinfo = %r', keyinfo)
        if keyinfo is not None:
            result += [keyinfo]
        return result

    async def __cache_entry_from_filename(self, filename: str) -> Optional[Tuple[str,str,str]]:
        #self._context.appLog().debug('nginx.__cache_entry_from_filename(%s)', filename)
        try:
            async with aiofiles.open(filename, mode='rb') as cachefile:
                data = await cachefile.read(4096)
            #self._context.appLog().debug('nginx.__cache_entry_from_filename: data = %r', data)
            key_start = data.index(b'\nKEY: ')
            #self._context.appLog().debug('nginx.__cache_entry_from_filename: key_start = %r', key_start)
            key_end = data.index(b'\n', key_start+1)
            #self._context.appLog().debug('nginx.__cache_entry_from_filename: key_end = %r', key_end)
            key = data[key_start+6:key_end]
            #self._context.appLog().debug('nginx.__cache_entry_from_filename: key = %r', key)
            (prov_sess, urlpath) = self.__key_to_prov_sess_and_url_path(key.decode('utf-8'))
        except Exception as err:
            self._context.appLog().error('nginx.__cache_entry_from_filename: exception occurred: %s', str(err))
            return None
        return (filename, prov_sess, urlpath)

    def __key_to_prov_sess_and_url_path(self, key: str) -> Tuple[str,str]:
        # the first separator ends the provisioning session identifier: the URL path may itself contain ":u="
        parts = key.split(':u=', 1)
        if len(parts) != 2:
            raise ValueError('cache key without a provisioning session: %r' % key)
        return (decode_resource_id(parts[0].split('|', 1)[-1]), parts[1])

    @classmethod
    def __ensure_modules(cls, cmd):
        ret = subprocess.run([cmd,'-V'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False)
        if ret.returncode == 0 and 'ngx_lua' in (ret.stdout.decode('utf-8')+ret.stderr.decode('utf-8')):
            return True
        return False

    def __check_nginx_flags(self,cmd,flags):
        '''Check if the command will take the command line flags

        Check "{cmd} -h" output to see if the flags are valid. Will return
        the command line with all valid flags.
        '''
        args = [cmd]
        ret = subprocess.run([cmd,'-h'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False)
        if ret.returncode == 0:
            for line in (ret.stdout.decode('utf-8')+ret.stderr.decode('utf-8')).split('\n'):
                line = line.strip()
                for (flag,value) in flags:
                    if line[:len(flag)] == flag and line[len(flag)] in [' ','\t']:
                        args += [flag]
                        if value is not None:
                            args += [value]
        return args

# Register as a WebProxyInterface class with highest priority
add_web_proxy(OpenRestyWebProxy,1)