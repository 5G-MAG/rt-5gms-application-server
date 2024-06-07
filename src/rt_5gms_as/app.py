#!/usr/bin/python3
#
# 5G-MAG Reference Tools: 5GMS Application Server
# ===============================================
#
# File: app.py
# License: 5G-MAG Public License (v1.0)
# Author: David Waring
# Copyright: (C) 2022 British Broadcasting Corporation
#
# For full license terms please see the LICENSE file distributed with this
# program. If this file is missing then the license can be retrieved from
# https://drive.google.com/file/d/1cinCiA778IErENZ3JN52VFW-1ffHpx7Z/view
#
# This is the 5G-MAG Reference Tools 5GMS AS main entry module.
# This file handles the command line parsing, creates the application context
# from the configuration file and manages the web server at a high level.
#
'''
Reference Tools: 5GMS Application Server
========================================

This NF provides the configuration interface for an external web proxy daemon.
'''
import os
import os.path

# Promote known Openresty locations to the head of the PATH environment variable
openresty_bin_dir=None
for d in ['/usr/local/openresty/nginx/sbin']:
    if os.path.isdir(d):
        openresty_bin_dir=d
        break
if openresty_bin_dir is not None:
    os.environ['PATH'] = ':'.join([openresty_bin_dir] + [p for p in os.environ['PATH'].split(':') if p != openresty_bin_dir])

import argparse
import asyncio
import hypercorn
import hypercorn.asyncio
import logging
import pkg_resources
import signal
import sys

from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from .proxy_factory import WebProxy, list_registered_web_proxies
from .context import Context
from .server import server
from .utils import async_create_task
from .exceptions import ProblemException, NoProblemException
from .openapi_5g.apis.default_api import router as m3_router

__pkg = None
__pkg_version = 'Devel'
try:
    __pkg = pkg_resources.get_distribution('rt-5gms-application-server')
    if __pkg is not None:
        __pkg_version = __pkg.version
except:
    pass

_app_server_hdr = '5GMSd-AS/'+__pkg_version

def get_arg_parser():
    '''
    Create the ArgumentParser object for this application

    Syntax:
      rt-5gsm-as -h
      rt-5gms-as -v
      rt-5gsm-as [-c <app-config-file>]

    Options:
      -h         --help           Show the help text
      -v         --version        Display the version information for the AS
      -c CONFIG  --config CONFIG  The application configuration file
    '''
    parser = argparse.ArgumentParser()
    parser.add_argument('-v', '--version', action='store_true', help='Display the version information')
    parser.add_argument('-c', '--config', nargs=1, required=False, metavar='CONFIG', help='The application configuration file')
    return parser

def list_join(l, sep1, sep2=None):
    '''
    Join a list to form a string using a choice of separators

    The str representations of the list l are joined together using sep1 to
    separate the items in the list except for the last two items which are
    separated by sep2. If sep2 is not provided then all items are separated by
    sep1.

    Examples
    list_join([1,2,3,4], ', ', ' or ') => '1, 2, 3 or 4'
    list_join([], ', ', ' or ') => ''
    list_join([1], ', ', ' or ') => '1'
    list_join([1,2], ', ', ' or ') => '1 or 2'
    list_join([1,2,3], ', ', ' or ') => '1, 2 or 3'
    list_join([1,2,3], ', ') => '1, 2, 3'
    '''
    if sep2 is None:
        sep2 = sep1
    lstr = [str(v) for v in l]
    return sep1.join(lstr[:-2]+[sep2.join(lstr[-2:])])

def sighup_handler(sig, context):
    '''Signal Handler for reloading the configuration

    Causes the configuration files to be re-read and if the web proxy
    configuration has changed, reload (or restart) the web proxy daemon
    '''
    context.appLog().info("Reloading configuration...")
    # reload configuration
    if context.reload():
        # configuration changed, reload/restart proxy
        async_create_task(context.webProxy().reload(), name='Proxy-reload')

def exit_handler(sig, context):
    '''Signal handler for INT/QUIT/TERM signals

    This will cause the application to exit by setting an exit code of 0 in
    the app_exit Future.
    '''
    # SIGINT or SIGQUIT is a normal app exit
    context.appLog().info("Signal %r received, exitting...", sig)
    context.exitWithReturnCode(0)

class AppJSONResponse(JSONResponse):
    def __init__(self, *args, status_code=200, **kwargs):
        super().__init__(*args, status_code=status_code, **kwargs)
        self.headers['Server'] = _app_server_hdr

async def __app(context):
    '''Asynchronous app entry point

    This is the main application function which is started as part of an
    asynchronous event loop after the application configuration has been
    loaded and checked.
    '''
    # Get our running loop
    loop = asyncio.get_running_loop()

    # Create a Future to be used for app exit, result is the exit code to use
    app_exit = loop.create_future()
    context.setAppExitFuture(app_exit)

    # Add signal handlers for HUP (Reload) and INT/QUIT/TERM for app exit
    loop.add_signal_handler(signal.SIGHUP, sighup_handler, signal.SIGHUP, context)
    loop.add_signal_handler(signal.SIGINT, exit_handler, signal.SIGINT, context)
    loop.add_signal_handler(signal.SIGQUIT, exit_handler, signal.SIGQUIT, context)

    # Write out the web proxy configuration
    if not await context.webProxy().writeConfiguration():
        context.appLog().error("Unable to write out configurations for %s web proxy", context.webProxy().name())
        return 1

    # Start the web proxy dameon child process
    if not await context.webProxy().startDaemon():
        context.appLog().error("Unable to start %s web proxy", context.webProxy().name())
        return 1

    # Create a task which will wait for the web proxy daemon to exit
    wait_task = async_create_task(context.webProxy().wait(), name='Proxy-monitor')

    server.setContext(context)

    m3_app = FastAPI(title='5G-MAG M3', description='5GMS AS M3 API Copyright © 2022 British Broadcasting Corporation All rights reserved.', version='0.0.0', debug=False, default_response_class=AppJSONResponse)
    m3_app.debug = True

    @m3_app.exception_handler(ProblemException)
    async def problem_exception_handler(request, exc):
        return AppJSONResponse(status_code=exc.status_code, content=exc.object, headers=exc.headers, media_type='application/problem+json')

    @m3_app.exception_handler(NoProblemException)
    async def no_problem_exception_handler(request, exc):
        hdrs = exc.headers
        if hdrs is None:
            hdrs = {}
        hdrs['server'] = _app_server_hdr
        return Response(exc.body, status_code=exc.status_code, headers=hdrs, media_type=exc.media_type)

    # TODO: Create HTTP server to handle M2 interface and add task to main loop

    # Create HTTP server to handle M3 interface and add task to main loop
    m3_app.include_router(m3_router, prefix="/3gpp-m3/v1")

    m3_config = hypercorn.Config.from_mapping(include_server_header=False, bind=context.getConfigVar('5gms_as','m3_listen')+':'+context.getConfigVar('5gms_as','m3_port'), loglevel='DEBUG', accesslog='-', errorlog='-')
    m3_serve_task = async_create_task(hypercorn.asyncio.serve(m3_app, m3_config), name='M3-server')

    # Main application loop
    while not app_exit.done():
        # Wait for one of the background tasks to exit
        done, pending = await asyncio.wait([app_exit, wait_task, m3_serve_task], return_when=asyncio.FIRST_COMPLETED)
        # If it was the web proxy daemon task that finished, handle the daemon
        # exit by restarting it
        if wait_task.done():
            # if the web proxy is restarting too rapidly, abort
            if context.webProxy().rapidStarts() > 5:
                context.appLog().error("%s web proxy restarting too quickly, aborting...", context.webProxy().name())
                app_exit.set_result(1)
            # do nothing if we're exiting
            elif app_exit.done():
                pass
            # if the daemon won't restart, abort
            elif not await context.webProxy().startDaemon():
                context.appLog().error("Unable to start %s web proxy", context.webProxy().name())
                app_exit.set_result(1)
            # Daemon started ok, start waiting for the new child process to exit
            else:
                context.appLog().info("Web proxy process exited, has been restarted")
                wait_task = async_create_task(context.webProxy().wait(), name='Proxy-monitor')
        elif m3_serve_task.done():
            # M3 interface has quit, abort app
            context.appLog().error("M3 interface finished, aborting application server")
            app_exit.set_result(1)


    # We are exiting, tidy up other tasks
    if not wait_task.done():
        wait_task.cancel()
        try:
            await wait_task
        except asyncio.exceptions.CancelledError:
            pass
    if not m3_serve_task.done():
        m3_serve_task.cancel()
        try:
            await m3_serve_task
        except asyncio.exceptions.CancelledError:
            pass

    # Stop the web proxy daemon if it's running
    if not await context.webProxy().stopDaemon():
        context.appLog().error("Unable to stop %s web proxy", context.webProxy().name())
        return 1

    # Tidy up the web proxy dameon configuration files
    if not await context.webProxy().tidyConfiguration():
        context.appLog().warning("Unable to tidy up after %s web proxy", context.webProxy().name())
        return 2

    # Return the exit code
    return app_exit.result()

def main():
    '''
    Application entry point
    '''
    # Set default logging level
    logging.basicConfig(level=logging.INFO)

    # Parse command line options
    parser = get_arg_parser()
    args = parser.parse_args()

    if args.version:
        global __pkg_version
        print(f'5G-MAG Reference Tools 5GMS Application Server version {__pkg_version}')
        return 0

    # Create a logger instance
    log = logging.getLogger("rt-5gms-as")

    # Check that we found a suitable web proxy daemon to use
    if WebProxy is None:
        log.error("Please install at least one of: %s", list_join([p.name() for p in list_registered_web_proxies()], ', ', ' or '))
        return 1

    # Get the application configuration file name
    config = args.config
    if config is not None:
        config = config[0]

    # Create the application context
    context = Context(config)
    context.setAppLog(log)

    # Create the web proxy daemon handler
    context.setWebProxy(WebProxy(context))

    # Now start the asynchronous operation of this application
    return asyncio.run(__app(context))

if __name__ == "__main__":
    sys.exit(main())
