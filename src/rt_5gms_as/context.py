#!/usr/bin/python3
#
# 5G-MAG Reference Tools: 5GMS Application Server
# ===============================================
#
# File: context.py
# License: 5G-MAG Public License (v1.0)
# Author: David Waring
# Copyright: (C) 2022 British Broadcasting Corporation
#
# For full license terms please see the LICENSE file distributed with this
# program. If this file is missing then the license can be retrieved from
# https://drive.google.com/file/d/1cinCiA778IErENZ3JN52VFW-1ffHpx7Z/view
#
# This is the 5G-MAG Reference Tools 5GMS AS application context module.
# This file handles the class which will hold the current run-time context of
# the AS.
#
'''
5gms_as.context module
======================

This module provides a single Context class which holds the context for the
5G-MAG Reference Tools 5GMS AS Network Function.
'''

import configparser
import json
import os
import os.path

from .openapi_5g.model.content_hosting_configuration import ContentHostingConfiguration
from .openapi_5g.model_utils import deserialize_model

DEFAULT_CONFIG = '''[DEFAULT]
log_dir = /var/log/rt-5gms
run_dir = /run/rt-5gms

[5gms_as]
cache_dir = /var/cache/rt-5gms/as/cache
http_port = 80
https_port = 443
listen_address = ::
access_log = %(log_dir)s/application-server-access.log
error_log = %(log_dir)s/application-server-error.log
pid_path = %(run_dir)s/application-server.pid

[5gms_as.nginx]
root_temp = /var/cache/rt-5gms/as
client_body_temp = %(root_temp)s/client-body-tmp
proxy_temp = %(root_temp)s/proxy-tmp
fastcgi_temp = %(root_temp)s/fastcgi-tmp
uwsgi_temp = %(root_temp)s/uwsgi-tmp
scgi_temp = %(root_temp)s/scgi-tmp
pid_path = %(root_temp)s/rt-5gms-as-nginx.pid
'''

class Context(object):
    '''
    Context class
    -------------

    Class to hold and manipulate the current context for the 5G-MAG Reference
    Tools 5GMS AS Network Function.
    '''

    def __init__(self, config_filename, content_hosting_config):
        '''Constructor

        config_filename (str)        - filename of the application configuration
        content_hosting_config (str) - filename of the ContentHostingConfiguration JSON
        '''
        if config_filename is None:
            config_filename = self.__find_config_file()
        self.__config = configparser.ConfigParser()
        self.__config.read_string(DEFAULT_CONFIG)
        if config_filename is not None:
            self.__config.read(config_filename)
        chc = deserialize_model(json.load(open(content_hosting_config, 'r')), ContentHostingConfiguration, config_filename, True, {}, True)
        self.__chcs = {chc.name: chc}
        # ensure configured directories exist
        for directory in [
            self.__config.get('5gms_as', 'cache_dir'),
            os.path.dirname(self.__config.get('5gms_as', 'access_log')),
            os.path.dirname(self.__config.get('5gms_as', 'error_log')),
            os.path.dirname(self.__config.get('5gms_as', 'pid_path')),
            ]:
            if directory is not None and len(directory) > 0 and not os.path.isdir(directory):
                os.makedirs(directory)

    def contentHostingConfigurations(self):
        '''Get the list of defined ContentHostingConfiguration objects

        Returns a list of the configured ContentHostingConfiguration objects
                associated with the AS.
        '''
        return self.__chcs.values()

    def findContentHostingConfiguration(self, name):
        '''
        Find a named ContentHostingConfiguration

        Return the ContentHostingConfiguration for the given name or None if
        a configuration with that name has not been defined in this context.
        '''
        if name in self.__chcs:
            return self.__chcs[name]
        return None

    def getConfigVar(self, section, varname, default=None):
        return self.__config.get(section, varname, fallback=default)

    def __find_config_file(self):
        cfgs = []
        if os.getuid() != 0:
            cfgs += [os.path.expanduser(os.path.join('~', '.rt-5gms', 'application-server.conf'))]
        cfgs += [os.path.join(os.path.sep, 'etc', 'rt-5gms', 'application-server.conf')]
        for cfgfile in cfgs:
            if os.path.exists(cfgfile):
                return cfgfile
        return None
