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
import io
import json
import os
import os.path
import sys

from .openapi_5g.model.content_hosting_configuration import ContentHostingConfiguration
from .openapi_5g.model_utils import deserialize_model, model_to_dict

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
provisioning_session_id = d54a1fcc-d411-4e32-807b-2c60dbaeaf5f
m4d_path_prefix = /m4d/provisioning-session-%(provisioning_session_id)s/

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

    def __init__(self, config_filename, content_hosting_config, certs_config):
        '''Constructor

        config_filename (str)        - filename of the application configuration
        content_hosting_config (str) - filename of the ContentHostingConfiguration JSON
        '''
        # Keep starting configuration for reload
        self.__config_filename = config_filename
        self.__content_hosting_config = content_hosting_config
        self.__certs_config = certs_config
        # Load the configurations
        self.__loadConfiguration(force=True)
        self.__loadCertificates(force=True)
        self.__loadContentHostingConfiguration(self.__config.get('5gms_as', 'provisioning_session_id'), force=True)
        self.__web_proxy = None
        self.__app_log = None
        self.__app_exit_future = None

    def reload(self):
        '''Reload the configuration files

        Reloads the configuration files this context was created with.

        Returns True if the configuration has changed or False for no change.
        '''
        ret = False
        if self.__loadConfiguration():
            ret = True
        if self.__loadCertificates():
            ret = True
        if self.__loadContentHostingConfiguration(self.__config.get('5gms_as', 'provisioning_session_id')):
            ret = True
        return ret

    def setWebProxy(self, proxy):
        '''Set the current active WebProxy object'''
        self.__web_proxy = proxy

    def webProxy(self):
        '''Get the current active WebProxy object'''
        return self.__web_proxy

    def setAppLog(self, log):
        '''Set the application Logger object'''
        self.__app_log = log

    def appLog(self):
        '''Get the application Logger object'''
        return self.__app_log

    def setAppExitFuture(self, future):
        '''Set the Future to use to signal application exit and return code'''
        self.__app_exit_future = future

    def appExitFuture(self):
        '''Get the application exit Future'''
        return self.__app_exit_future

    def exitWithReturnCode(self, returncode):
        '''Exit the application giving the return code provided

        If the application exit Future is set (i.e. the app is in its async
        event loop) then signal the return code to the Future and return.

        Otherwise this will immediately exit with the return code and this
        method will not exit.
        '''
        if self.__app_exit_future is not None:
            self.__app_exit_future.set_result(returncode)
            return None
        sys.exit(returncode)

    def contentHostingConfigurations(self):
        '''Get the list of defined ContentHostingConfiguration objects

        Returns a list of the configured ContentHostingConfiguration objects
                associated with the AS.
        '''
        return [p['chc'] for p in self.__provisioning_sessions.values()]

    def findContentHostingConfiguration(self, name):
        '''
        Find a named ContentHostingConfiguration

        Return the ContentHostingConfiguration for the given name or None if
        a configuration with that name has not been defined in this context.
        '''
        for chc in self.contentHostingConfigurations():
            if chc['name'] == name:
                return chc
        return None

    def getConfigVar(self, section, varname, default=None):
        '''Get a configuration variable from the application configuration

        Returns the value from the configuration file or _default_ if the value
                cannot be found.
        '''
        return self.__config.get(section, varname, fallback=default)

    def haveCertificate(self, certificate_id):
        '''Check is a certificate ID exists

        Returns True if we have the certificate ID registered, otherwise False.
        '''
        return certificate_id in self.__certificates

    def getCertificateFilename(self, certificate_id):
        '''Get the filename for certificate with given ID

        Return the filename for the certificate with the given _certificate_id_
               or None if the ID doesn't exist.
        '''
        if self.haveCertificate(certificate_id):
            return self.__certificates[certificate_id]
        return None

    #### Exceptions ####
    class ConfigError(RuntimeError):
        '''Configuration Error Exception from the 5GMS AS Context
        '''
        pass

    #### Private methods ####
    def __find_config_file(self):
        '''Find an application configuration file

        Returns the filename of the found application configuration file or
                None if no configuration file is found.
        '''
        cfgs = []
        if os.getuid() != 0:
            cfgs += [os.path.expanduser(os.path.join('~', '.rt-5gms', 'application-server.conf'))]
        cfgs += [os.path.join(os.path.sep, 'etc', 'rt-5gms', 'application-server.conf')]
        for cfgfile in cfgs:
            if os.path.exists(cfgfile):
                return cfgfile
        return None

    def __loadContentHostingConfiguration(self, provisioning_session_id, force=True):
        '''Load a content hosting configuration

        Loads and replaces a ContentHostingConfiguration if it is different
        from the previously loaded configuration. If _force_ is True, replace
        anyway.

        Returns True if the configuration was loaded, False if there was no
                change.
        '''

        chc = deserialize_model(json.load(open(self.__content_hosting_config, 'r')), ContentHostingConfiguration, self.__content_hosting_config, True, {}, True)
        # Validate the certificate IDs
        for distrib in chc['distribution_configurations']:
            if 'certificate_id' in distrib and not self.haveCertificate(distrib['certificate_id']):
                raise Context.ConfigError('Certificate ID %s in ContentHostingConfiguration not found in certificates map'%distrib['certificate_id'])
        # Update configuration
        chc_hash = self.__hashOpenAPIObject(chc)
        if force or provisioning_session_id not in self.__provisioningSessions or chc_hash != self.__provisioning_sessions[provisioning_session_id]['chc_hash']:
            self.__provisioning_sessions = {provisioning_session_id: {'chc': chc, 'chc_hash': chc_hash}}
            return True
        return False

    def __loadConfiguration(self, force=False):
        '''Load application configuration

        Loads and replaces the application configuration if it is different
        from the previously loaded configuration. If _force_ is True, replace
        anyway.

        Returns True if the configuration was loaded, False if there was no
                change.
        '''
        config_filename = self.__configFilename()
        config = configparser.ConfigParser()
        config.read_string(DEFAULT_CONFIG)
        if config_filename is not None:
            config.read(config_filename)
        # ensure configured directories exist
        for directory in [
            config.get('5gms_as', 'cache_dir'),
            os.path.dirname(config.get('5gms_as', 'access_log')),
            os.path.dirname(config.get('5gms_as', 'error_log')),
            os.path.dirname(config.get('5gms_as', 'pid_path')),
            ]:
            if directory is not None and len(directory) > 0 and not os.path.isdir(directory):
                os.makedirs(directory)
        # new config loaded, remember it
        chash = self.__hashConfigParser(config)
        if force or chash != self.__config_hash:
            self.__config = config
            self.__config_hash = chash
            return True
        return False

    def __loadCertificates(self, force=False):
        cert_map = {}
        if self.__certs_config is not None:
            try:
                with open(self.__certs_config,'r') as certs_in:
                    cert_map = json.load(certs_in)
            except json.decoder.JSONDecodeError as e:
                raise Context.ConfigError('Bad JSON in certificates configuration: %s: line %i column %i'%(e.msg, e.lineno, e.colno))
            # Relative pathnames in the configuration are relative to the file
            cert_map = {id: self.__join_paths(self.__certs_config, filename) for id, filename in cert_map.items()}
        if force or cert_map != self.__certificates:
            self.__certificates = cert_map
            return True
        return False

    def __configFilename(self):
        '''Get the configured application configuration filename

        Returns the configuration file given on the command line or finds
        a suitable file in the file system.

        Returns the filename or None if no configuration file is found.
        '''
        if self.__config_filename is not None:
            return self.__config_filename
        return self.__find_config_file()

    @staticmethod
    def __join_paths(base, relpath):
        # Already absolute so return it
        if relpath[0] == os.path.sep:
            return relpath
        # Remove basename from base path if present
        if base[-1] != os.path.sep:
            base = os.path.dirname(base)
        # Return absolute path for base + relpath
        return os.path.realpath(os.path.join(base, relpath))

    @staticmethod
    def __hashOpenAPIObject(obj):
        '''Create a consistent hash for an OpenAPI object'''
        # Just return the hash of the JSON serialization, use sort_keys=True for
        # consistency
        return hash(json.dumps(model_to_dict(obj), sort_keys=True))

    @staticmethod
    def __hashConfigParser(config):
        '''Create a hash for a ConfigParser object'''
        sio = io.StringIO()
        config.write(sio)
        sio.seek(0)
        return hash(sio.read())
