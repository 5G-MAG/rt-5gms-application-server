#!/usr/bin/python3
#
# 5G-MAG Reference Tools: 5GMS Application Server
# ===============================================
#
# File: proxy_factory.py
# License: 5G-MAG Public License (v1.0)
# Author: David Waring
# Copyright: (C) 2022 British Broadcasting Corporation
#
# For full license terms please see the LICENSE file distributed with this
# program. If this file is missing then the license can be retrieved from
# https://drive.google.com/file/d/1cinCiA778IErENZ3JN52VFW-1ffHpx7Z/view
#
# This is the 5G-MAG Reference Tools 5GMS AS proxy factory module.
# This module will load the modules in the proxies subdirectory and pick
# one to be used at run-time. The picked WebProxyInterface derived class is
# stored in the WebProxy variable in this module.
#
# To use the picked WebProxy use:
#     from rt_5gms_as.proxy_factory import WebProxy
#
'''
Proxy Factory module

This collects together all registered WebProxyInterface classes and uses the
list to determine the WebProxyInterface class which should be used to control
the web proxy part of the AS.

WebProxyInterface classes are automatically included from the proxies
subdirectory.

Classes register themselves by calling proxy_factory.add_web_proxy() giving the
class and selection priority as parameters.

This factory tests each WebProxyInterface class by calling its isPresent() class
method and selecting the highest priority one which returns True from the
isPresent() call.

The selected WebProxyInterface class is held in proxy_factory.WebProxy.
'''

import glob
import importlib
import logging
import os.path
import subprocess

__web_proxies = []

def add_web_proxy(cls,prio):
    '''
    Register a WebProxyInterface class with its priority
    '''
    global __web_proxies
    __web_proxies += [(cls,prio)]

def list_registered_web_proxies():
    '''
    Return a list of registered WebProxyInterface classes
    '''
    global __web_proxies
    return [cls for (cls,pri) in __web_proxies]

class WebProxyInterface(object):
    '''
    Base class for web proxy classes
    '''
    def __init__(self, context):
        self._context = context
        self.__daemon = {'proc': None, 'returncode': None, 'stdout': None, 'stderr': None}
        self.log = logging.getLogger(self.__class__.__name__)

    @classmethod
    def isPresent(cls):
        '''
        Test to see if the web proxy is available in the running system.

        This must be implemented by classes inheriting WebProxyInterface.

        Return True if the web proxy is present and usable.
        '''
        return False

    @classmethod
    def name(cls):
        '''
        Return a human readable name as a str for this WebProxyInterface class.

        This must be implemented by classes inheriting WebProxyInterface.
        '''
        return None

    def writeConfiguration(self):
        '''
        Write out the configuration files for the web proxy.

        This must be implemented by classes inheriting WebProxyInterface.

        Return True if the configuration files were successfully written.
        '''
        return False

    def startDaemon(self):
        '''
        Start the web proxy process

        This must be implemented by classes inheriting WebProxyInterface.

        The implementation should call self._startDaemon(cmdlist) to start the
        web proxy process as a foreground process. If the _restart_ parameter
        is False, then this will only start the command in _cmdlist_ if it is
        not already running. With _restart_=True (the default) then any existing
        daemon process is stopped first before starting the command in
        _cmdlist_.

        Return True if the external web proxy process was started successfully.
        '''
        return False

    def wait(self):
        '''
        Wait for the daemon process started by startDaemon() to exit.
        '''
        return self._wait()

    def tidyConfiguration(self):
        '''
        Tidy up the configuration files

        This must be implemented by classes inheriting WebProxyInterface.

        Return True if the configuration was sucessfully tidied.
        '''
        return False

    def stopDaemon(self):
        '''
        Stop a running daemon process

        Return True if the daemon process was stopped successfully
        '''
        if self.__daemon['proc'] is not None:
            self.__daemon['proc'].terminate()
            self._wait()
            self.__daemon['proc'] = None
        return True

    def _startDaemon(self, cmd, restart=True):
        '''
        Internal method to start a daemon

        Will try to start the command in _cmd_ and store the running subprocess
        Popen object.

        If _restart_ is True (the default) then a daemon process that is
        already running is stopped first before the new _cmd_ is started. If
        _restart_ is False then this will return True if a daemon is already
        running without starting the new _cmd_, even if it was started with a
        different command line.

        Return True if the process was started or False is starting the
        subprocess failed.
        '''
        if self.__daemon['proc'] is not None:
            if restart:
                self.stopDaemon()
            else:
                return True
        self.__daemon['proc'] = subprocess.Popen(cmd, stdin=None, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return self.__daemon['proc'] is not None

    def _wait(self, timeout=None):
        '''
        Internal method to wait for the daemon process to exit

        Will wait up to _timeout_ seconds for the daemon process to exit. Will
        wait forever if timeout is None (or not given). If _timeout_ is 0 then
        will immediately exit with the current running status of the daemon
        process.

        On exit the return code, stdout and stderr will be available via the
        daemonReturnCode(), daemonStdout() and daemonStderr() methods.

        Returns True if no daemon process is running or the process exitted
                within the _timeout_ or returns False if the process did not
                exit within the _timeout_ period.
        '''
        if self.__daemon['proc'] is None:
            return True
        try:
            self.__daemon['returncode'] = self.__daemon['proc'].wait(timeout)
            self.__daemon['stdout'] = self.__daemon['proc'].stdout.read().decode('utf-8')
            self.__daemon['stderr'] = self.__daemon['proc'].stderr.read().decode('utf-8')
        except subprocess.TimeoutExpired as e:
            return False
        return True

    def daemonReturnCode(self):
        '''
        Return the last daemon process exit return code.
        '''
        return self.__daemon['returncode']

    def daemonStdout(self):
        '''
        Return the daemon process stdout after exit of the daemon process.

        wait() or _wait() must return True before a value will be available
        from this method.
        '''
        return self.__daemon['stdout']

    def daemonStderr(self):
        '''
        Return the daemon process stderr after exit of the daemon process.

        wait() or _wait() must return True before a value will be available
        from this method.
        '''
        return self.__daemon['stderr']

# Load all proxy modules from the proxies subdirectory
proxy_modules_dir = os.path.join(os.path.dirname(__file__), 'proxies')
for module_path in glob.glob(os.path.join(proxy_modules_dir, '*.py')):
    module_name = os.path.basename(module_path)[:-3]
    if module_name != "__init__":
        importlib.import_module('..proxies.'+module_name, __name__)

# Find the highest priority WebProxyInterface class that indicates its
# underlying implmentation is available on the runtime system and store in
# WebProxy
WebProxy = None
for cls in [p[0] for p in sorted(__web_proxies, key=lambda x: (x[1],x[0].__name__))]:
    if cls.isPresent():
        WebProxy = cls
        break
