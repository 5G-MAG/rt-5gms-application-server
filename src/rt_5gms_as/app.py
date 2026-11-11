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
import logging
import os
import os.path

logging.basicConfig(level=logging.INFO)

import argparse
import asyncio
import hypercorn
import hypercorn.asyncio
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
import importlib.metadata
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
from .oauth import Authorizer
from . import m3d

# longest request URI (path and query) that the M3 server processes for an operation whose OpenAPI response table lists 414; a longer one is answered with 414
M3_MAX_URI_LENGTH = 8192

# the path prefix of the M3 API and the order in which the OpenAPI files list the methods of a path
M3_PATH_PREFIX = '/3gpp-mas-configuration/v1'
_M3_METHOD_ORDER = ('GET', 'POST', 'PUT', 'PATCH', 'DELETE')
# the enumerate operations (GET of a collection) are the only ones of the M3 OpenAPI files whose response table does not list 414
_M3_COLLECTIONS = tuple(M3_PATH_PREFIX + name for name in ('/certificates', '/content-preparation-templates', '/content-hosting-configurations', '/content-publishing-configurations'))

def lists_uri_too_long(path):
    '''Whether the response table of the M3 operation at path lists 414 (URI Too Long): every operation of the M3 API except the enumerate operations'''
    return path.startswith(M3_PATH_PREFIX + '/') and path.rstrip('/') not in _M3_COLLECTIONS

def allowed_methods(router, path, prefix=M3_PATH_PREFIX):
    '''The methods that the OpenAPI files define for a path of the M3 API: the union of the methods of every route of the router that matches it.

    There is one route per method, and the framework's own 405 lists the methods of the first route that matches the path only. HEAD and
    OPTIONS are not among them: no OpenAPI file of the M3 API defines them, so they are answered 405 like any other method that is not defined.
    '''
    relative = path[len(prefix):] if path.startswith(prefix) else path
    methods = set()
    for route in router.routes:
        if route.path_regex.match(relative):
            methods |= route.methods
    return [m for m in _M3_METHOD_ORDER if m in methods] + sorted(methods.difference(_M3_METHOD_ORDER))

__pkg_version = 'Devel'
try:
    __pkg_version = importlib.metadata.version('rt-5gms-application-server')
except importlib.metadata.PackageNotFoundError:
    pass

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

    # TS 26.512 V19.3.0 clause 4.6.0.1: say whether this AS supports ECN marking for L4S at M4d (the AF reads the equivalent mediaAsSupportsL4s)
    context.appLog().info("L4S (RFC 9330, 9331 and 9332) at reference point M4d: %s", "declared as supported (supports_l4s)" if context.supportsL4s() else "not supported")

    # Reference point M3d: Service Access Information and CMCD reports for the 5GMSd AF, when the section 5gms_as.m3d names its address
    reporter = m3d.from_context(context)
    if reporter is not None:
        await reporter.start(m3d.samples_socket_path(context), int(context.getConfigVar('5gms_as.m3d', 'samples_socket_mode', '666'), 8))
        context.appLog().info("Reporting to the 5GMSd AF at %s (reference point M3d)", context.getConfigVar('5gms_as.m3d', 'af_url'))

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

    m3_app = FastAPI(title='5G-MAG M3', description='5GMS AS M3 API Copyright © 2022 British Broadcasting Corporation All rights reserved.', version='0.0.0', debug=False, default_response_class=AppJSONResponse,
                     docs_url=None, redoc_url=None, openapi_url=None)
    m3_app.debug = False

    @m3_app.exception_handler(ProblemException)
    async def problem_exception_handler(request, exc):
        return AppJSONResponse(status_code=exc.status_code, content=exc.object, headers=exc.headers, media_type='application/problem+json')

    @m3_app.exception_handler(NoProblemException)
    async def no_problem_exception_handler(request, exc):
        return Response(exc.body, status_code=exc.status_code, headers=exc.headers, media_type=exc.media_type)

    @m3_app.exception_handler(RequestValidationError)
    async def request_validation_exception_handler(request, exc):
        path = request.url.path
        # a Content Preparation Template is of any media type: the framework cannot parse it as the declared string, so the handler is called here with the raw body
        if request.method in ('POST', 'PUT') and path.split('/')[-2:-1] == ['content-preparation-templates']:
            handler = server.create_content_preparation_template if request.method == 'POST' else server.replace_content_preparation_template
            try:
                await handler(path.split('/')[-1], None, request=request)
            except ProblemException as problem:
                return await problem_exception_handler(request, problem)
            except NoProblemException as done:
                return await no_problem_exception_handler(request, done)
        # a PUT or POST to a resource that is not there is answered with 410 or 404 before the body is judged (clauses 4.5.2.5, 4.5.4.5, 4.5.4.7, 4.5.4.9)
        missing = server.missing_resource_problem(request)
        if missing is not None:
            return await problem_exception_handler(request, missing)
        # TS 29.500 clause 5.2.7.2: unsupported content format is rejected with 415
        expected = None
        if path.split('/')[-2:-1] == ['certificates']:
            expected = 'application/x-pem-file'
        elif path.endswith('/purge'):
            expected = 'application/x-www-form-urlencoded'
        elif '/content-hosting-configurations/' in path:
            expected = 'application/json'
        got = request.headers.get('content-type', '').split(';')[0].strip()
        if expected is not None and got != expected:
            return AppJSONResponse(status_code=415, content=ProblemException(status_code=415, title='Invalid media type', detail='The request body has to be of type '+expected, instance=path).object, media_type='application/problem+json')
        # TS 29.500 clause 5.2.7.2: a request that does not comply with the schema is rejected with 400 (INVALID_MSG_FORMAT)
        problem = ProblemException(status_code=400, title='Bad Request', detail='The request does not comply with the schema', instance=request.url.path,
                                   invalid_params=[{'param': '.'.join(str(p) for p in err.get('loc', [])), 'reason': str(err.get('msg', ''))} for err in exc.errors()])
        problem.object['cause'] = 'INVALID_MSG_FORMAT'
        return AppJSONResponse(status_code=400, content=problem.object, media_type='application/problem+json')

    @m3_app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request, exc):
        # errors raised by the framework itself (unknown resource 404, method not allowed 405) are also ProblemDetails (clause 7.1.7 of TS 26.510)
        segments = request.url.path.split('/')
        if exc.status_code == 404 and len(segments) > 2 and segments[1] == '3gpp-mas-configuration' and segments[2] != 'v1':
            # TS 29.500 table 5.2.7.2-1: a URI with an unsupported API version is rejected with 400 and the cause INVALID_API
            problem = ProblemException(status_code=400, title='Bad Request', detail='API version [%s] is not supported' % segments[2], instance=request.url.path)
            problem.object['cause'] = 'INVALID_API'
            return AppJSONResponse(status_code=400, content=problem.object, media_type='application/problem+json')
        problem = ProblemException(status_code=exc.status_code, title=str(exc.detail), instance=request.url.path)
        headers = getattr(exc, 'headers', None)
        if exc.status_code == 405:
            # RFC 9110 clause 15.5.6: the Allow header field lists the methods that the OpenAPI files define for the path
            headers = dict(headers or {}, Allow=', '.join(allowed_methods(m3_router, request.url.path)))
        return AppJSONResponse(status_code=exc.status_code, content=problem.object, headers=headers, media_type='application/problem+json')

    # OAuth 2.0 at M3 (TS 26.512 clause 6.6.3): the token endpoint and the check of the access token, if [5gms_as.oauth] is configured
    authorizer = Authorizer(context)

    @m3_app.middleware('http')
    async def oauth_middleware(request, call_next):
        path = request.url.path
        # the YAML tables list 414 (URI Too Long) for every operation but the enumerate operations; the server library would answer 431 for a very long request line
        if lists_uri_too_long(path) and len(request.scope.get('raw_path') or b'') + len(request.scope.get('query_string') or b'') > M3_MAX_URI_LENGTH:
            return AppJSONResponse(status_code=414, content=ProblemException(status_code=414, title='URI Too Long', detail='The request URI is longer than %d bytes' % M3_MAX_URI_LENGTH,
                                                                             instance=path[:200]).object, media_type='application/problem+json')
        if path == '/oauth2/token':
            response = authorizer.token_endpoint(request.method, request.headers.get('content-type'), await request.body(),
                                                 request.headers.get('authorization'))
        elif path.startswith('/3gpp-mas-configuration/'):
            response = authorizer.check(request.method, path, request.headers.get('authorization'))
        else:
            response = None
        if response is None:
            if request.method == 'POST' and path.endswith('/purge'):
                # the form parser of the framework is lenient (a bad percent-escape is kept as it is): the handler checks the body itself
                request.state.raw_body = await request.body()
            return await call_next(request)
        return response

    # TODO: Create HTTP server to handle M2 interface and add task to main loop

    # Create HTTP server to handle M3 interface and add task to main loop
    m3_app.include_router(m3_router, prefix=M3_PATH_PREFIX)

    m3_config = hypercorn.Config.from_mapping(include_server_header=False, h11_max_incomplete_size=4 * 1024 * 1024, bind=context.getConfigVar('5gms_as','m3_listen')+':'+context.getConfigVar('5gms_as','m3_port'), loglevel='DEBUG', accesslog='-', errorlog='-')
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

    if reporter is not None:
        await reporter.stop()

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
