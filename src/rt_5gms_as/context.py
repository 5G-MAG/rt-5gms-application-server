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
import logging
import os
import os.path
import sys
from typing import Optional

from .openapi_5g.models.content_hosting_configuration import ContentHostingConfiguration

DEFAULT_CONFIG = '''[DEFAULT]
log_dir = /var/log/rt-5gms
run_dir = /run/rt-5gms

[5gms_as]
log_level = info
cache_dir = /var/cache/rt-5gms/as/cache
docroot = /var/cache/rt-5gms/as/docroots
certificates_cache = /var/cache/rt-5gms/as/certificates
listen_address = ::
http_port = 80
https_port = 443
m3_listen = localhost
m3_port = 7777

access_log = %(log_dir)s/application-server-access.log
error_log = %(log_dir)s/application-server-error.log
pid_path = %(run_dir)s/application-server.pid

[5gms_as.nginx]
config_file = /tmp/rt_5gms_as.conf
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

    def __init__(self, config_filename):
        '''Constructor

        config_filename (str)        - filename of the application configuration
        content_hosting_config (str) - filename of the ContentHostingConfiguration JSON
        '''
        # Keep starting configuration for reload
        self.__config_filename = config_filename
        # Initialise some member variables
        self.__provisioning_sessions = {}
        self.__certificates = {}
        # Load the configurations
        self.__loadConfiguration(force=True)
        self.__loadCachedCertificates(force=True)
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
        # update the logging level to match new configuration
        if self.__app_log is not None:
            self.__app_log.setLevel(self.__log_level)
        if self.__loadCachedCertificates():
            ret = True
        if self.__reassessContentHostingConfigurations():
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
        log.setLevel(self.__log_level)

    def appLog(self):
        '''Get the application Logger object'''
        return self.__app_log

    def setAppExitFuture(self, future):
        '''Set the Future to use to signal application exit and return code'''
        self.__app_exit_future = future

    def logLevel(self):
        '''Get the configured log level'''
        return self.__log_level

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

    def getProvisioningSessionIds(self):
        '''
        Get a list of provisioning session Ids
        '''
        return self.__provisioning_sessions.keys()

    def haveContentHostingConfiguration(self, provisioning_session_id: str):
        '''
        Check for the presence of a ContentHostingConfiguration
        '''
        return provisioning_session_id in self.__provisioning_sessions

    def findContentHostingConfigurationByProvisioningSession(self, provisioning_session_id: str):
        '''
        Get the ContentHostingConfiguration for the provisioning session
        '''
        if provisioning_session_id not in self.__provisioning_sessions:
            return None
        return self.__provisioning_sessions[provisioning_session_id]['chc']

    def findContentHostingConfigurationByName(self, name):
        '''
        Find a named ContentHostingConfiguration

        Return the ContentHostingConfiguration for the given name or None if
        a configuration with that name has not been defined in this context.
        '''
        for chc in self.contentHostingConfigurations():
            if chc['name'] == name:
                return chc
        return None

    def addContentHostingConfiguration(self, provisioning_session_id: str, content_hosting_configuration: ContentHostingConfiguration):
        '''
        Add a ContentHostingConfiguration.
        '''
        self.__addContentHostingConfiguration(provisioning_session_id, content_hosting_configuration, force=True)
        return None

    def updateContentHostingConfiguration(self, provisioning_session_id: str, content_hosting_configuration: ContentHostingConfiguration) -> Optional[bool]:
        '''
        Update an existing content hosting configuration

        Returns None if the content hosting configuration doesn't exist,
                True if the content hosting configuration was updated or
                False if there was no update needed.
        '''
        if not self.haveContentHostingConfiguration(provisioning_session_id):
            return None
        return self.__addContentHostingConfiguration(provisioning_session_id, content_hosting_configuration, force=False)

    def deleteContentHostingConfiguration(self, provisioning_session_id: str):
        '''
        Delete a content hosting configuration

        Returns True if a configuration was removed and False otherwise.
        '''
        return self.__delContentHostingConfiguration(provisioning_session_id)

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
        self.__debug('Context.haveCertificate(%r) = %r', certificate_id, certificate_id in self.__certificates)
        return certificate_id in self.__certificates

    def getCertificateFilename(self, certificate_id: str) -> Optional[str]:
        '''Get the filename for certificate with given ID

        Return the filename for the certificate with the given _certificate_id_
               or None if the ID doesn't exist.
        '''
        self.__debug('Context.getCertificateFilename(%r)', certificate_id)
        if self.haveCertificate(certificate_id):
            return self.__certificates[certificate_id]
        return None

    def addCertificate(self, certificate_id: str, certificate_pem_text: str):
        '''
        Add a certificate

        Add the PEM data from the given certificate ID.

        Return True if the certificate was added/updated.
        '''
        if certificate_pem_text is None:
            raise Context.ConfigError("Attempt to add a certificate with empty contents");
        return self.__addCertificate(certificate_id, certificate_pem_text, force = True)

    def updateCertificate(self, certificate_id: str, certificate_pem_text: str):
        '''
        Update a certificate

        Store the PEM data under the given certificate ID.

        Return True if the certificate was added/updated, False if there was no change to the existing certificate.
        '''
        if certificate_pem_text is None:
            raise Context.ConfigError("Attempt to update certificate with empty contents");
        return self.__addCertificate(certificate_id, certificate_pem_text, force = False)

    def deleteCertificate(self, certificate_id: str):
        '''
        Delete a certificate
        '''
        return self.__delCertificate(certificate_id)

    def certificatesCacheDir(self):
        '''
        Get the certificates caching directory
        '''
        return self.__config.get('5gms_as','certificates_cache')

    def getCertificateIds(self):
        '''
        Get a list of known certificate IDs

        Returns ["<af-unique-certificate-id>", ...]
        '''
        return self.__certificates.keys()

    #### Exceptions ####
    class ConfigError(RuntimeError):
        '''Configuration Error Exception from the 5GMS AS Context
        '''

    class ValueError(RuntimeError):
        '''Bad value passed in
        '''
        def __init__(self, msg: str, locn: str, *args, **kwargs):
            super().__init__(msg, locn, *args, **kwargs)

        def __str__(self):
            return f"ValueError for {self.args[1]}: {self.args[0]}"

        def __repr__(self):
            return f'{self.__class__.__name__}({", ".join([repr(s) for s in self.args])})'

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

    def __addContentHostingConfiguration(self, provisioning_session_id: str, chc: ContentHostingConfiguration, force: bool = True):
        '''Load a content hosting configuration

        Loads and replaces a ContentHostingConfiguration if it is different
        from the previously loaded configuration. If _force_ is True, replace
        anyway.

        Returns True if the configuration was loaded, False if there was no
                change.
        '''
        # Sanity check the CHC
        if chc is None:
            raise Context.ValueError('ContentHostingConfiguration nbot given', '.')
        if chc.ingest_configuration is None:
            raise Context.ValueError('ContentHostingConfiguration must have an ingestConfiguration', 'ingestConfiguration')
        if chc.distribution_configurations is None or not hasattr(chc.distribution_configurations, '__iter__'):
            raise Context.ValueError('ContentHostingConfiguration must have a distributionConfigurations array', 'distributionConfigurations')
        # Validate the certificate IDs
        for distrib in chc.distribution_configurations:
            if distrib.certificate_id is not None and not self.haveCertificate(distrib.certificate_id):
                raise Context.ValueError('Certificate ID %s in ContentHostingConfiguration for provisioning session Id %s not found in certificates map'%(distrib.certificate_id, provisioning_session_id), 'distributionConfigurations.certificateId')
        # Update configuration
        chc_hash = self.__hashOpenAPIObject(chc)
        old_chc_hash = None
        if provisioning_session_id in self.__provisioning_sessions:
            old_chc_hash = self.__provisioning_sessions[provisioning_session_id]['chc_hash']
        self.__app_log.debug("CHC passed verification, checking for update: force=%r, new hash=%r, old hash=%r", force, chc_hash, old_chc_hash)
        if force or old_chc_hash is None or chc_hash != old_chc_hash:
            self.__provisioning_sessions[provisioning_session_id] = {'chc': chc, 'chc_hash': chc_hash}
            return True
        return False

    def __delContentHostingConfiguration(self, provisioning_session_id: str):
        '''Delete a ContentHostingConfiguration

        Deletes the ContentHostingConfiguration for the given provisioning
        session.
        '''
        if provisioning_session_id not in self.__provisioning_sessions:
            return False
        # Since the CHC is the only thing we store here, just delete the whole
        # entry.
        del self.__provisioning_sessions[provisioning_session_id]
        return True

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
            config.get('5gms_as', 'certificates_cache'),
            os.path.dirname(config.get('5gms_as', 'access_log')),
            os.path.dirname(config.get('5gms_as', 'error_log')),
            os.path.dirname(config.get('5gms_as', 'pid_path')),
            ]:
            if directory is not None and len(directory) > 0 and not os.path.isdir(directory):
                old_umask = os.umask(0)
                try:
                    os.makedirs(directory, mode=0o755)
                finally:
                    os.umask(old_umask)
        # get logging level from the configuration file
        logging_levels = {
                'debug': logging.DEBUG,
                'info': logging.INFO,
                'warn': logging.WARNING,
                'error': logging.ERROR,
                'fatal': logging.FATAL,
                }
        log_level = config.get('5gms_as', 'log_level')
        if log_level in logging_levels:
            log_level = logging_levels[log_level]
        else:
            log_level = logging.INFO
        self.__log_level = log_level
        # new config loaded, remember it
        chash = self.__hashConfigParser(config)
        if force or chash != self.__config_hash:
            self.__config = config
            self.__config_hash = chash
            return True
        return False

    def __addCertificate(self, cert_id: str, cert_contents: str, force=False):
        '''
        Add a certificate to the cache
        '''
        cache_filename = os.path.join(self.certificatesCacheDir(), cert_id)
        if os.path.exists(cache_filename) and not force:
            with open(cache_filename, 'r') as cert_in:
                existing_cert = cert_in.read()
            if existing_cert == cert_contents:
                return False
        with open(cache_filename, 'w') as cert_out:
            cert_out.write(cert_contents)
        self.__certificates[cert_id] = cache_filename
        return True

    def __delCertificate(self, certificate_id: str):
        if certificate_id not in self.__certificates:
            raise Context.ConfigError("No such certificate")
        if self.__certificateInUse(certificate_id):
            raise Context.ConfigError("Certificate still in use by a ContentHostingConfiguration, refusing to delete")
        del self.__certificates[certificate_id]
        os.remove(os.path.join(self.certificatesCacheDir(),certificate_id))
        return True

    def __loadCachedCertificates(self, force: bool = False):
        certs = {}
        if os.path.isdir(self.certificatesCacheDir()):
            for de in os.scandir(self.certificatesCacheDir()):
                if de.is_file():
                    certs[de.name] = de.path
        if force or certs != self.__certificates:
            self.__certificates = certs
            return True
        return False

    def __reassessContentHostingConfigurations(self):
        for provisioning_session_id,provisioning_session in self.__provisioning_sessions.items():
            if 'chc' in provisioning_session:
                chc = provisioning_session['chc']
                for dc in chc['distribution_configurations']:
                    if 'certificate_id' in dc and not self.haveCertificate(dc['certificate_id']):
                        raise Context.ConfigError("Certificate %s not present"%(dc['certificate_id']))
        return True

    def __certificateInUse(self, certificate_id: str):
        for provisioning_session in self.__provisioning_sessions.values():
            if 'chc' not in provisioning_session:
                continue
            for dc in provisioning_session['chc'].distribution_configurations:
                if dc.certificate_id is not None and dc.certificate_id == certificate_id:
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

    def __debug(self, *args, **kwargs):
        '''Log a debug message
        '''
        if self.__app_log is not None:
            self.__app_log.debug(*args, **kwargs)

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
        return hash(obj.json(sort_keys=True))

    @staticmethod
    def __hashConfigParser(config):
        '''Create a hash for a ConfigParser object'''
        sio = io.StringIO()
        config.write(sio)
        sio.seek(0)
        return hash(sio.read())
