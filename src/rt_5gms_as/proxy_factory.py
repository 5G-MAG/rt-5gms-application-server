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

import aiofiles.os
import asyncio
import asyncio.subprocess
import glob
import importlib
import importlib.resources
import logging
import os.path
import regex
import time

from typing import Any, List, Tuple, Callable

from .utils import async_create_task

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
        '''WebProxyInterface constructor

        Takes an app context object which will be accessible to child classes
        via the _context member variable.

        Initialises daemon state management.
        '''
        self._context = context
        self.__daemon = {'proc': None, 'returncode': None, 'stdout': None, 'stderr': None, 'start_times': []}
        # Remember restart times within the last 10 seconds in
        # __daemon['start_times']
        self.__rapidRestartSecs = 10.0
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

    async def writeConfiguration(self):
        '''
        Write out the configuration files for the web proxy.

        This must be implemented by classes inheriting WebProxyInterface.

        Return True if the configuration files were successfully written.
        '''
        return False

    async def startDaemon(self):
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

    async def wait(self):
        '''Wait for the daemon process started by startDaemon() to exit.

        Will wait for the daemon process to exit.

        Returns True of the daemon child process exits, or False if the wait()
        was cancelled.
        '''
        return await self._wait()

    async def tidyConfiguration(self):
        '''Tidy up the configuration files

        This must be implemented by classes inheriting WebProxyInterface.

        Return True if the configuration was sucessfully tidied.
        '''
        return False

    async def stopDaemon(self):
        '''Stop a running daemon process

        Return True if the daemon process was stopped successfully
        '''
        if self.__daemon['proc'] is not None:
            self.__daemon['proc'].terminate()
            await self._wait()
            self.__daemon['proc'] = None
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

    def daemonRunning(self):
        '''Return the daemon process running status.

        Return True if the dameon is running, or False if not.
        '''
        return self.__daemon['proc'] is not None

    async def signalDaemon(self, sig):
        '''Send a signal to the daemon process

        Return True if the signal was sent or False if not.
        '''
        if self.__daemon['proc'] is not None:
            self.__daemon['proc'].send_signal(sig)
            return True
        return False

    def rapidStarts(self):
        '''Return the number of times the daemon has started recently

        This will return the number of times the daemon has been started
        in the last 10 seconds.
        '''
        self.__trimDaemonStartTimes()
        return len(self.__daemon['start_times'])

    async def reload(self):
        '''Reload after context changes

        Regenerate configuration and reload/restart the proxy process.

        The basic method will stop the old proxy, remove old configuration,
        write configuration and start the proxy again if it was already running.

        This should be overridden by WebProxyInterface implementations to
        provide a better reload procedure.

        Return True if the reload was successful or False if there was a
               problem.
        '''
        if self.daemonRunning():
            if not await self.stopDaemon():
                return False
            if not await self.tidyConfiguration():
                return False
            if not await self.writeConfiguration():
                return False
            if not await self.startDaemon():
                return False
        return True

    async def purgeAll(self, provisioningSessionId: str) -> bool:
        '''Purge all cache entries for the provisioning session

        Returns True if the purge succeeded or False if there was an error.
        '''
        self._context.appLog().debug('Purging all entries for %s...', provisioningSessionId)
        return await self._purge(key_filter=lambda psid,path: psid==provisioningSessionId)

    async def purgeUsingRegex(self, provisioningSessionId: str, re: str) -> bool:
        '''Purge cache entries for the provisioning session that match a regex

        Returns True if the purge succeeded or False if there was an error.
        '''
        self._context.appLog().debug('Purging entries for %s using regex %s...', provisioningSessionId, re)
        try:
            comp_regex = regex.compile(re)
        except Exception as err:
            self._context.appLog().error('Exception while handling regex.compile: %s'%str(err))
            return False
        if comp_regex is None:
            self._context.appLog().error('Regular expression %s failed to compile.', re)
            return False
        return await self._purge(key_filter=lambda psid,path: psid==provisioningSessionId and comp_regex.match(path) is not None)

    async def purgeUsingPrefix(self, provisioningSessionId: str, prefix: str) -> bool:
        '''Purge cache entries for the provisioning session with a path prefix

        Returns True if the purge succeeded or False if there was an error.
        '''
        self._context.appLog().debug('Purging entries for %s using URL path prefix %s...', provisioningSessionId, prefix)
        return await self._purge(key_filter=lambda psid,path: psid==provisioningSessionId and path[:len(prefix)]==prefix)

    async def purgePath(self, provisioningSessionId: str, purge_path: str) -> bool:
        '''Purge cache entries for the provisioning session that match a path

        Returns True if the purge succeeded or False if there was an error.
        '''
        self._context.appLog().debug('Purging %s in %s...', purge_path, provisioningSessionId)
        return self._purge(key_filter=lambda psid,path: psid==provisioningSessionId and path==purge_path)

    #### Protected methods ####

    async def _startDaemon(self, cmd, restart=True):
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
                await self.stopDaemon()
            else:
                return True
        self.__daemon['proc'] = await asyncio.create_subprocess_exec(*cmd, stdin=None, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        self.__addDaemonStartTime()
        return self.__daemon['proc'] is not None

    async def _wait(self, timeout=None):
        '''
        Internal method to wait for the daemon process to exit

        Will wait up to _timeout_ seconds for the daemon process to exit. Will
        wait forever if timeout is None (or not given). If _timeout_ is 0 then
        will immediately exit with the current running status of the daemon
        process (False for still running and True for exited).

        When this coroutine returns True (i.e. the daemon has exited) the
        return code, stdout and stderr will be available via the
        daemonReturnCode(), daemonStdout() and daemonStderr() methods.

        Returns True if no daemon process is running or the process exitted
                within the _timeout_, or returns False if the process did not
                exit within the _timeout_ period or this coroutine was
                cancelled.
        '''
        task_name = 'None'
        task = asyncio.current_task()
        if task is not None:
            task_name = task.get_name()
        self.log.debug("WebProxy._wait() called from Task %s", task_name)
        if self.__daemon['proc'] is None:
            return True
        try:
            comm_task = async_create_task(self.__daemon['proc'].communicate(), name='wait-for-web-proxy-daemon')
            await asyncio.wait({comm_task}, timeout=timeout)
            self.__daemon['returncode'] = self.__daemon['proc'].returncode
            (stdout, stderr) = comm_task.result()
            self.__daemon['stdout'] = stdout.decode('utf-8')
            self.__daemon['stderr'] = stderr.decode('utf-8')
            self.__daemon['proc'] = None
        except asyncio.TimeoutError as e:
            self.log.debug("WebProxy._wait() in Task %s timed out", task_name)
            return False
        except asyncio.CancelledError as e:
            # We've been cancelled, so cancel the sub-task
            try:
                comm_task.cancel()
                await comm_task
            except asyncio.CancelledError as e:
                # This exception is expected due to the "comm_task.cancel()"
                # above
                pass
            self.log.debug("WebProxy._wait() in Task %s cancelled", task_name)
            return False
        self.log.debug("WebProxy._wait() in Task %s finished", task_name)
        return True

    async def _purge(self, key_filter: Callable[[str,str], bool]) -> bool:
        try:
            to_purge = [file for (file,psid,urlpath) in await self._getCacheFilesAndKeys() if key_filter(psid,urlpath)]
            self._context.appLog().debug('Matching cache entries: %r', to_purge)
            if len(to_purge) > 0:
                self._context.appLog().debug('Purging entries...')
                await self._purgeCacheFiles(to_purge)
                self._context.appLog().debug('Post purge actions...')
                await self._postPurgeActions()
        except Exception as err:
            self._context.appLog().error('Error while purging cache: %s', str(err))
            return False
        return True

    async def _getCacheFilesAndKeys(self) -> List[Tuple[str,str,str]]:
        '''Return a list of all cache files

        The list returned consists of entries which are a tuple of the cache
        file filepath, the provisioning session id and the url path, e.g.
        (filepath, psid, urlpath)
        '''
        raise NotImplementedError()

    async def _purgeCacheFiles(self, to_purge: List[str]):
        '''Purge all cache files from the list

        This default implementation simply deletes the files from the
        filesystem.
        '''
        for f in to_purge:
            await aiofiles.os.remove(f)

    async def _postPurgeActions(self):
        '''Perform post purge actions

        This default implementation does nothing
        '''
        pass

    #### Private methods ####

    def __trimDaemonStartTimes(self, now=None):
        '''Remove old entries from the list of daemon start times

        Drops entries from the list of daemon start times if they more than
        __rapidRestartSecs seconds old.
        '''
        if now is None:
            now = time.monotonic_ns()
        cutoff = now - (self.__rapidRestartSecs * 1000000000)
        self.__daemon['start_times'] = [t for t in self.__daemon['start_times'] if t > cutoff]

    def __addDaemonStartTime(self):
        '''Add a new daemon start time to the list of recent daemon start times

        This wil tidy old entries from the current list and add a new entry.
        This uses the system monotonic clock.
        '''
        now = time.monotonic_ns()
        self.__trimDaemonStartTimes(now)
        self.__daemon['start_times'] += [now]




# Load all proxy modules from the proxies subdirectory
for module_path in importlib.resources.contents(__package__ + '.proxies'):
    if module_path[-3:] == '.py':
        module_name = os.path.basename(module_path)[:-3]
        if module_name != "__init__":
            importlib.import_module('.proxies.'+module_name, __package__)

# Find the highest priority WebProxyInterface class that indicates its
# underlying implmentation is available on the runtime system and store in
# WebProxy
WebProxy = None
for cls in [p[0] for p in sorted(__web_proxies, key=lambda x: (x[1],x[0].__name__))]:
    if cls.isPresent():
        WebProxy = cls
        break
