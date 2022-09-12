#!/usr/bin/python3
'''
nginx WebProxyInterface module

This module implements the WebProxyInterface class for the nginx web server and
reverse proxy.
'''

import datetime
import os
import os.path

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
    
    __nginx = None
    __last_nginx_check = None

    # Constants which should be replaced by values from a configuration file
    __error_log_path    = '/tmp/rt_5gms_as.error.log'
    __access_log_path   = '/tmp/rt_5gms_as.access.log'
    __client_body_tmp   = '/tmp/rt_5gms_as.client_body'
    __proxy_cache_path  = '/tmp/rt_5gms_as.proxy_cache'
    __proxy_temp_path   = '/tmp/rt_5gms_as.proxy_temp'
    __fastcgi_temp_path = '/tmp/rt_5gms_as.fastcgi_temp'
    __uwsgi_temp_path   = '/tmp/rt_5gms_as.uwsgi_temp'
    __scgi_temp_path   = '/tmp/rt_5gms_as.scgi_temp'
    __pid_path          = '/tmp/rt_5gms_as.pid'
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
        error_log_path = self.__error_log_path
        access_log_path = self.__access_log_path
        pid_path = self.__pid_path
        client_body_tmp = self.__client_body_tmp
        proxy_cache_path = self.__proxy_cache_path
        proxy_temp_path = self.__proxy_temp_path
        fastcgi_temp_path = self.__fastcgi_temp_path
        uwsgi_temp_path = self.__uwsgi_temp_path
        scgi_temp_path = self.__scgi_temp_path
        # provision session should come from the AF
        provision_session='1234abcd'
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
                        rewrite_rules += '      rewrite "%s(.*)" "%s$1" break;\n'%(rr['request_pattern'],rr['mapped_path'])
                server_names = dc['canonical_domain_name']
                if 'domain_name_alias' in dc:
                    server_names += ' ' + dc['domain_name_alias']
                # Use nginx-server.conf.tmpl file as a template for server
                # configurations.
                with open(os.path.join(os.path.dirname(__file__),'nginx-server.conf.tmpl')) as template:
                    for line in template:
                        server_configs += line.format(**locals())
        try:
            # Try to write out the configuration file using nginx.conf.tmpl as
            # a template for the configuration file.
            with open(self.__nginx_conf_path, 'w') as conffile:
                with open(os.path.join(os.path.dirname(__file__),'nginx.conf.tmpl')) as template:
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
        cmd_line = __check_nginx_flags(cmd,[('-e',self.__error_log_path), ('-c',self.__nginx_conf_path), ('-g','daemon off;')])
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

# Register as a WebProxyInterface class with highest priority
add_web_proxy(NginxWebProxy,1)
