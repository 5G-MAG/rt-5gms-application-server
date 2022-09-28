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

import datetime
import importlib.resources
import os
import os.path
import regex
import subprocess
import traceback

from ..proxy_factory import WebProxyInterface, add_web_proxy
from ..utils import find_executable_on_path

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

    def writeConfiguration(self):
        '''
        Write out the nginx configuration file

        Converts the ContentHostingConfigurations from the AS context to an
        nginx configuration file.

        Return True if it the configuration could be generated and writted to a
               file.
        '''
        http_port = self._context.getConfigVar('5gms_as','http_port')
        https_port = self._context.getConfigVar('5gms_as','https_port')
        error_log_path = self._context.getConfigVar('5gms_as','error_log')
        access_log_path = self._context.getConfigVar('5gms_as','access_log')
        pid_path = self._context.getConfigVar('5gms_as.nginx','pid_path')
        client_body_tmp = self._context.getConfigVar('5gms_as.nginx','client_body_temp')
        proxy_cache_path = self._context.getConfigVar('5gms_as','cache_dir')
        proxy_temp_path = self._context.getConfigVar('5gms_as.nginx','proxy_temp')
        fastcgi_temp_path = self._context.getConfigVar('5gms_as.nginx','fastcgi_temp')
        uwsgi_temp_path = self._context.getConfigVar('5gms_as.nginx','uwsgi_temp')
        scgi_temp_path = self._context.getConfigVar('5gms_as.nginx','scgi_temp')
        # provision session should come from the AF
        provision_session='1234abcd'
        # Create caching directives if we have a cache dir configured
        proxy_cache_path_directive = ''
        proxy_cache_directive = ''
        if proxy_cache_path is not None and len(proxy_cache_path) > 0:
            proxy_cache_path_directive = 'proxy_cache_path %s levels=1:2 use_temp_path=on keys_zone=cacheone:10m;'%proxy_cache_path
            proxy_cache_directive = 'proxy_cache cacheone;'
        # Create the server configurations from the CHCs
        server_configs=''
        for i in self._context.contentHostingConfigurations():
            if not i['ingest_configuration']['pull'] or i['ingest_configuration']['protocol'] != 'urn:3gpp:5gms:content-protocol:http-pull-ingest':
                self.log.error("Can only handle http-pull-ingest sources at present")
                return False
            downstream_origin=i['ingest_configuration']['entry_point']
            if downstream_origin[-1] == '/':
                downstream_origin = downstream_origin[:-1]
            for dc in i['distribution_configurations']:
                rewrite_rules=''
                if 'path_rewrite_rules' in dc:
                    for rr in dc['path_rewrite_rules']:
                        (regex, replace) = self.__transform_rewrite_rules(rr['request_pattern'],rr['mapped_path'])
                        if regex is not None:
                            rewrite_rules += '      rewrite "%s" "%s" break;\n'%(regex,replace)
                        else:
                            self.log.error("Unsafe or invalid rewrite rule: %s => %s", rr['request_pattern'], rr['mapped_path'])
                            return False
                server_names = dc['canonical_domain_name']
                if 'domain_name_alias' in dc:
                    server_names += ' ' + dc['domain_name_alias']
                # Use nginx-server.conf.tmpl file as a template for server
                # configurations.
                with importlib.resources.open_text(__package__,'nginx-server.conf.tmpl') as template:
                    for line in template:
                        server_configs += line.format(**locals())
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

    def tidyConfiguration(self):
        '''
        Tidy configuration files

        Delete the automatically generated nginx configuration.
        '''
        os.unlink(self.__nginx_conf_path)
        return True

    def startDaemon(self):
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
        return self._startDaemon(cmd_line)

    def wait(self):
        '''
        Wait for nginx process to exit

        Dumps the stdout and/or stderr from the process after exit.
        '''
        ret = self._wait()
        if ret:
            if self.daemonReturnCode() != 0:
                self.log.info(self.daemonStdout())
                self.log.error(self.daemonStderr())
            else:
                self.log.info(self.daemonStdout())
        else:
            self.log.error(self.daemonStderr())
        return ret

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
            self.log.error("Error in request_pattern: %s", traceback.format_exc())
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

# Register as a WebProxyInterface class with highest priority
add_web_proxy(NginxWebProxy,1)
