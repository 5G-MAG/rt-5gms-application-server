#!/usr/bin/python3
#
# 5G-MAG Reference Tools: 5GMS Application Server
# ===============================================
#
# File: proxies/nginx.py
# License: 5G-MAG Public License (v1.0)
# Author: David Waring
# Copyright: (C) 2022 British Broadcasting Corporation
#
# For full license terms please see the LICENSE file distributed with this
# program. If this file is missing then the license can be retrieved from
# https://drive.google.com/file/d/1cinCiA778IErENZ3JN52VFW-1ffHpx7Z/view
#
# This is the 5G-MAG Reference Tools 5GMS AS nginx web proxy handling module.
#
# This provides the NginxWebProxy class and registers it with the proxy_factory.
#
'''
nginx WebProxyInterface module

This module implements the WebProxyInterface class for the nginx web server and
reverse proxy.
'''

import aiofiles
import datetime
import importlib.resources
import os
import os.path
import regex
import signal
import subprocess
import traceback

from typing import Optional, Tuple, List, Any, Self, Set
from urllib.parse import urlparse

from ..proxy_factory import WebProxyInterface, add_web_proxy
from ..utils import find_executable_on_path, traverse_directory_tree
from ..context import Context

class NginxLocationConfig(object):
    '''
    Class to hold and compare location configurations
    '''
    def __init__(self: Self, context: Context, path_prefix: str, downstream_prefix_url: str, provisioning_session: str) -> Self:
        self.__context: Context = context
        self.path_prefix: str = path_prefix
        self.downstream_prefix_url: str = downstream_prefix_url
        self.provisioning_session: str = provisioning_session
        self.rewrite_rules: List[Tuple[str,str]] = []

    def addRewriteRule(self: Self, request_path_pattern: str, mapped_path: str) -> bool:
        (regex, replace) = self.__transform_rewrite_rules(request_path_pattern,mapped_path)
        if regex is None:
            self.__context.appLog().error("Unsafe or invalid rewrite rule: %s => %s", request_path_pattern, mapped_path)
            return False
        self.rewrite_rules += [(regex, replace)]
        return True

    def __eq__(self: Self, other: "NginxLocationConfig") -> bool:
        if self.path_prefix != other.path_prefix:
            return False
        if len(self.rewrite_rules) != len(other.rewrite_rules):
            return False
        for a in self.rewrite_rules:
            if a not in other.rewrite_rules:
                return False
        return True

    def __ne__(self: Self, other: "NginxLocationConfig") -> bool:
        return not self == other

    async def config(self: Self, indent: int = 0) -> str:
        prefix = ' ' * indent
        ret = prefix + 'location ~ ^%s {\n'%(self.path_prefix)
        for (regex, replace) in self.rewrite_rules:
            ret += prefix + '  rewrite "%s" "%s" break;\n'
        ret += prefix + '  proxy_cache_key "%s:u=$uri";\n'%self.provisioning_session
        ret += prefix + '  proxy_pass %s;\n'%self.downstream_prefix_url
        ret += prefix + '}\n'
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

        # pathRewriteRule only deals with replacing a path part, so we need to
        # include the rest of the URL path in the nginx rewrite rule.
        if rule_regex[0] != '^':
            rule_regex = '^(.*)' + rule_regex
            replace = '${1}' + replace
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

class NginxServerConfig(object):
    '''
    Class to hold and compare server configurations
    '''
    def __init__(self: Self, context: Context, hostnames: Set[str], use_cache: bool = False, certfile: Optional[str] = None) -> Self:
        self.__context: Context = context
        self.hostnames: Set[str] = hostnames
        self.certificate_file: Optional[str] = certfile
        if certfile is not None:
            self.port: int = int(self.__context.getConfigVar('5gms_as','https_port'))
        else:
            self.port: int = int(self.__context.getConfigVar('5gms_as','http_port'))
        self.locations: List[NginxLocationConfig] = []
        self.use_cache: bool = use_cache

    async def config(self: Self, indent: int = 0) -> str:
        prefix = ' ' * indent
        ret  = prefix + 'server {\n'
        ssl_flag = ''
        if self.certificate_file is not None:
            ssl_flag = ' ssl'
        ret += prefix + '  listen %i%s;\n'%(self.port, ssl_flag)
        ret += prefix + '  listen [::]:%i%s;\n'%(self.port, ssl_flag)
        ret += prefix + '  server_name %s;\n'%(' '.join(self.hostnames))
        ret += '\n'
        if self.certificate_file is not None:
            ret += prefix + '  ssl_certificate %s;\n'%self.certificate_file
            ret += prefix + '  ssl_certificate_key %s;\n'%self.certificate_file
            ret += '\n'
        if self.use_cache:
            ret += prefix + '  proxy_cache cacheone;\n'
            ret += '\n'
        ret += prefix + '  location / {\n'
        ret += prefix + '    return 404;\n'
        ret += prefix + '  }\n'
        ret += '\n'
        for locn in self.locations:
            ret += await locn.config(indent+2)
            ret += '\n'
        ret += prefix + '  error_page 404 /404.html;\n'
        ret += prefix + '  location = /404.html {\n'
        ret += prefix + '  }\n'
        ret += '\n'
        ret += prefix + '  error_page 500 502 503 504 /50x.html;\n'
        ret += prefix + '  location = /50x.html {\n'
        ret += prefix + '  }\n'
        ret += prefix + '}\n'
        return ret

    def addLocation(self: Self, locn: NginxLocationConfig) -> None:
        self.locations += [locn]

    def sameLocations(self: Self, other: "NginxServerConfig") -> bool:
        if len(self.locations) != len(other.locations):
            return False
        for a in self.locations:
            if a not in other.locations:
                return False
        return True

    def mergeServer(self: Self, other: "NginxServerConfig") -> bool:
        if self.use_cache != other.use_cache:
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

class NginxWebProxy(WebProxyInterface):
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
                os.makedirs(directory)
    
    __nginx = None
    __last_nginx_check = None

    # Constants which should be replaced by values from a configuration file
    #__nginx_conf_path   = '/etc/nginx/rt_5gms_as.conf'
    __nginx_conf_path   = '/tmp/rt_5gms_as.conf'

    @classmethod
    def isPresent(cls):
        '''
        Check if nginx is present in the system
        '''
        now = datetime.datetime.now()
        if cls.__nginx is None or cls.__last_nginx_check is None or cls.__last_nginx_check + datetime.timedelta(seconds=5) < now:
            # Only recheck if its been more than 5 seconds after the last check
            cls.__last_nginx_check = now
            cls.__nginx = find_executable_on_path("nginx")
        return cls.__nginx is not None

    @classmethod
    def name(cls):
        '''
        Return nginx name
        '''
        return "nginx"

    async def writeConfiguration(self):
        '''
        Write out the nginx configuration file

        Converts the ContentHostingConfigurations from the AS context to an
        nginx configuration file.

        Return True if it the configuration could be generated and writted to a
               file.
        '''
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
        # Create caching directives if we have a cache dir configured
        proxy_cache_path_directive = ''
        if proxy_cache_path is not None and len(proxy_cache_path) > 0:
            proxy_cache_path_directive = 'proxy_cache_path %s levels=1:2 use_temp_path=on keys_zone=cacheone:10m;'%proxy_cache_path
        # Create the server configurations from the CHCs
        server_configs: Dict[Tuple[str,Optional[str]], NginxServerConfig] = {}
        for provisioning_session_id in self._context.getProvisioningSessionIds():
            i = self._context.findContentHostingConfigurationByProvisioningSession(provisioning_session_id)
            if not i.ingest_configuration.pull or i.ingest_configuration.protocol != 'urn:3gpp:5gms:content-protocol:http-pull-ingest':
                self.log.error("Can only handle http-pull-ingest sources at present")
                return False
            downstream_origin = i.ingest_configuration.base_url
            if downstream_origin is None:
                self.log.error("Configuration must have an ingestConfiguration.baseURL")
                return False
            if downstream_origin[-1] == '/':
                downstream_origin = downstream_origin[:-1]
            for dc in i.distribution_configurations:
                certificate_filename = None
                if dc.certificate_id is not None:
                    certificate_filename = self._context.getCertificateFilename(dc.certificate_id)
                sk = (dc.canonical_domain_name, certificate_filename)
                if sk not in server_configs:
                    server_configs[sk] = NginxServerConfig(self._context, {dc.canonical_domain_name}, proxy_cache_path is not None, certificate_filename)
                if dc.domain_name_alias is not None and dc.domain_name_alias not in server_configs:
                    server_configs[(dc.domain_name_alias, certificate_filename is not None)] = NginxServerConfig(self._context, {dc.domain_name_alias}, proxy_cache_path is not None, certificate_filename)
                base_url = urlparse(dc.base_url)
                m4d_path_prefix = base_url.path
                if m4d_path_prefix[0] != '/':
                    m4d_path_prefix = '/' + m4d_path_prefix
                if m4d_path_prefix[-1] != '/':
                    m4d_path_prefix += '/'
                locn = NginxLocationConfig(self._context, m4d_path_prefix, downstream_origin, provisioning_session_id)
                if dc.path_rewrite_rules is not None:
                    for rr in dc.path_rewrite_rules:
                        if not locn.addRewriteRule(rr.request_path_pattern, rr.mapped_path):
                            return False
                server_configs[sk].addLocation(locn)
                if dc.domain_name_alias is not None:
                    server_configs[(dc.domain_name_alias, certificate_filename is not None)].addLocation(locn)
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
        for svr in server_configs.values():
            config += await svr.config(2)
            config += '\n'
        server_configs = config
        try:
            # Try to write out the configuration file using nginx.conf.tmpl as
            # a template for the configuration file.
            with open(self.__nginx_conf_path, 'w') as conffile:
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
        os.unlink(self.__nginx_conf_path)
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
        cmd_line = self.__check_nginx_flags(cmd,[('-e',self._context.getConfigVar('5gms_as', 'error_log')), ('-c',self.__nginx_conf_path), ('-g','daemon off;')])
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
        return tuple(key.split(':u='))

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
add_web_proxy(NginxWebProxy,1)
