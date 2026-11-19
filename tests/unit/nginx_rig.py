# A private nginx of the AS and a mock origin for the tests that need a running proxy: ports chosen by the system on 127.0.0.1, directories
# in a temporary folder, nothing of a running AS or nginx is touched.
# License: 5G-MAG Public License (v1.0)
import asyncio
import http.client
import http.server
import logging
import os
import socket
import shutil
import socketserver
import subprocess
import tempfile
import threading
import time

from rt_5gms_as.context import Context
from rt_5gms_as.openapi_5g.models.application_server_content_hosting_configuration import ApplicationServerContentHostingConfiguration as CHC
from rt_5gms_as.proxies.openresty import OpenRestyWebProxy

OPENRESTY = os.environ.get('OPENRESTY_NGINX', '/home/fivegmag/work-5gms-rel19/openresty/nginx/sbin/nginx')
HAVE_NGINX = os.access(OPENRESTY, os.X_OK) and os.path.isfile('/usr/local/openresty/nginx/conf/mime.types')

CONFIG = '''[DEFAULT]
log_dir = {d}/log
run_dir = {d}/run
[5gms_as]
cache_dir = {d}/as/cache
docroot = {d}/as/docroots
certificates_cache = {d}/as/certificates
m3_listen = 127.0.0.1
m3_port = 7777
http_port = {port}
{extra}
[5gms_as.nginx]
config_file = {d}/nginx.conf
root_temp = {d}/temp
resolvers = 127.0.0.1
'''


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class Origin(object):
    '''A mock origin: routes maps a path (without query) to a function of the handler returning (status, headers, body); log keeps the requests'''
    def __init__(self):
        self.routes = {}
        self.log = []
        origin = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args):
                pass

            def do_GET(self):
                origin.log.append((self.command, self.path, dict(self.headers)))
                route = origin.routes.get(self.path.split('?')[0])
                if route is None:
                    self.send_response(404)
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                route(self)

            do_HEAD = do_GET

        class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True

        self.server = Server(('127.0.0.1', 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def answer(self, status=200, headers=None, body=b''):
        '''A route that sends a complete response'''
        def route(handler):
            handler.send_response(status)
            for name, value in (headers or {}).items():
                handler.send_header(name, value)
            handler.send_header('Content-Length', str(len(body)))
            handler.end_headers()
            handler.wfile.write(body)
        return route

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class Proxy(object):
    '''The nginx of the AS for the given Content Hosting Configurations (dictionaries, as in the JSON of the M3 API), on a port of its own'''
    def __init__(self, chcs, extra_config=''):
        self.dir = tempfile.mkdtemp(prefix='as-test-')
        self.port = free_port()
        config = os.path.join(self.dir, 'as.conf')
        with open(config, 'w') as fh:
            fh.write(CONFIG.format(d=self.dir, port=self.port, extra=extra_config))
        for sub in ('log', 'run', 'temp'):
            os.makedirs(os.path.join(self.dir, sub))
        self.context = Context(config)
        self.context.setAppLog(logging.getLogger('test'))
        for name, chc in chcs.items():
            self.context.addContentHostingConfiguration(name, CHC.model_validate(chc))
        asyncio.run(OpenRestyWebProxy(self.context).writeConfiguration())
        self.process = subprocess.Popen([OPENRESTY, '-p', self.dir + '/', '-c', os.path.join(self.dir, 'nginx.conf'), '-g', 'daemon off;'],
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        deadline = time.time() + 10
        while time.time() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(self.process.stdout.read().decode())
            try:
                socket.create_connection(('127.0.0.1', self.port), timeout=0.2).close()
                return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError('nginx did not listen')

    def connection(self):
        return http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)

    def get(self, host, path, method='GET', headers=None):
        '''(status, header fields in lower case, body)'''
        conn = self.connection()
        conn.request(method, path, headers=dict({'Host': host}, **(headers or {})))
        response = conn.getresponse()
        body = response.read()
        conn.close()
        return response.status, {k.lower(): v for k, v in response.getheaders()}, body

    def stop(self):
        self.process.terminate()
        try:
            self.process.wait(5)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self.process.stdout.close()
        shutil.rmtree(self.dir, ignore_errors=True)


def chc_dict(origin_port, host='as.example.net', prefix='/m4d/ps1/', origin_path='/media/', protocol='urn:3gpp:5gms:content-protocol:http-pull', dist=None, ingest=None):
    distribution = {'distributionId': 'd1', 'canonicalDomainName': host, 'baseURL': 'http://%s%s' % (host, prefix)}
    distribution.update(dist or {})
    ingest_configuration = {'mode': 'PULL', 'protocol': protocol, 'baseURL': 'http://127.0.0.1:%d%s' % (origin_port, origin_path)}
    ingest_configuration.update(ingest or {})
    return {'name': 't', 'externalServiceId': 'com.example.t', 'ingestConfiguration': ingest_configuration, 'distributionConfigurations': [distribution]}
