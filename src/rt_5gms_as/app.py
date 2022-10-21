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
import argparse
import asyncio
import logging
import signal
import sys

from .proxy_factory import WebProxy, list_registered_web_proxies
from .context import Context
from .utils import async_create_task

def get_arg_parser():
    '''
    Create the ArgumentParser object for this application

    Syntax:
      rt-5gsm-as -h
      rt-5gsm-as [-c <app-config-file>] <content-config-file> [<certificates-config-file>]

    Options:
      -h         --help           Show the help text
      -c CONFIG  --config CONFIG  The application configuration file

    Parameters:
      content-config-file  This is the file path of a file containing a
                           ContentHostingConfiguration in JSON format.
      certificates-config-file
                           This is the file path for a file containing a JSON
                           object mapping certificate IDs to PEM file paths.
    '''
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', '--config', nargs=1, required=False, metavar='CONFIG', help='The application configuration file')
    parser.add_argument('contentconfig', nargs=1, metavar='CHC-JSON-FILE', help='The ContentHostingConfiguration JSON file')
    parser.add_argument('certsconfig', nargs='?', metavar='CERTS-JSON-FILE', help='The certificates JSON file')
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

    # TODO: Create HTTP server to handle M2 interface and add task to main loop
    # TODO: Create HTTP server to handle M3 interface and add task to main loop

    # Main application loop
    while not app_exit.done():
        # Wait for one of the background tasks to exit
        done, pending = await asyncio.wait([app_exit, wait_task], return_when=asyncio.FIRST_COMPLETED)
        # If it was the web proxy daemon task that finished, handle the daemon
        # exit by restarting it
        if wait_task.done():
            # if the web proxy is restarting too rapidly, abort
            if context.webProxy().rapidStarts() > 5:
                context.appLog().error("%s web proxy restarting too quickly, aborting...", context.webProxy().name())
                app_exit.set_result(1)
            # if the daemon won't restart, abort
            elif not await context.webProxy().startDaemon():
                context.appLog().error("Unable to start %s web proxy", context.webProxy().name())
                app_exit.set_result(1)
            # Daemon started ok, start waiting for the new child process to exit
            else:
                context.appLog().info("Web proxy process exited, has been restarted")
                wait_task = async_create_task(context.webProxy().wait(), name='Proxy-monitor')

    # We are exiting, tidy up other tasks
    if not wait_task.done():
        wait_task.cancel()
        await wait_task

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

    # Get the ContentHostingConfiguration file name
    contentconfig = args.contentconfig
    if contentconfig is not None:
        contentconfig = contentconfig[0]

    certsconfig = args.certsconfig

    # Create the application context
    context = Context(config, contentconfig, certsconfig)
    context.setAppLog(log)

    # Create the web proxy daemon handler
    context.setWebProxy(WebProxy(context))

    # Now start the asynchronous operation of this application
    return asyncio.run(__app(context))

if __name__ == "__main__":
    sys.exit(main())
