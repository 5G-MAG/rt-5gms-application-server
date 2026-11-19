'''Test of the dynamic M4d endpoints for the HTTP redirects of the origin (TS 26.512 V19.3.0 clause 8.2)'''

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
import unittest

import rt_5gms_as
from rt_5gms_as.context import Context
from rt_5gms_as.openapi_5g.models.application_server_content_hosting_configuration import ApplicationServerContentHostingConfiguration as CHC
from rt_5gms_as.proxies.openresty import OpenRestyWebProxy

from tests.unit.test_ingest_base_query import CONFIG, OPENRESTY, chc_dict

RESTY = shutil.which('resty') or ('/usr/local/openresty/bin/resty' if os.path.exists('/usr/local/openresty/bin/resty') else None)
PROXIES = os.path.join(os.path.dirname(os.path.abspath(rt_5gms_as.__file__)), 'proxies')
HAVE_NGINX = os.access(OPENRESTY, os.X_OK) and os.path.isfile('/usr/local/openresty/nginx/conf/mime.types')

SCRIPT = '''
package.path = "%(proxies)s/?.lua;" .. package.path
local d = require "dynamicredirect"
%(body)s
'''


def run_lua(body):
    script = SCRIPT % {'proxies': PROXIES, 'body': body}
    out = subprocess.run([RESTY, '--shdict', 'dynredirmap 1m', '-e', script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip().split('\n')


@unittest.skipIf(RESTY is None, 'the resty command of OpenResty is not available')
class EndpointNames(unittest.TestCase):
    def test_the_worker_processes_do_not_share_the_names_of_their_endpoints(self):
        # the workers are forked from the master process with the same state of the random number generator: each one seeds it again
        names = []
        for pid in (4001, 4002):
            names.append(run_lua('ngx.time = function() return 1800000000 end\nngx.worker.pid = function() return %d end\nd.seed()\nprint(d.uuid())' % pid)[0])
        self.assertNotEqual(names[0], names[1])

    def test_the_name_of_an_endpoint_in_use_is_not_given_again(self):
        # the random numbers repeat: the second redirect gets another name and the first endpoint still leads to its own target
        out = run_lua('''
local calls = 0
math.random = function() calls = calls + 1; return calls <= 8 and 1 or 2 end
local first = d.get("/m4d/ps/", "http://a.example/x/")
calls = 0
local second = d.get("/m4d/ps/", "http://b.example/y/")
print(tostring(first ~= second))
print(ngx.shared.dynredirmap:get(first))
print(ngx.shared.dynredirmap:get(second))
''')
        self.assertEqual(out, ['true', 'http://a.example/x/', 'http://b.example/y/'])

    def test_an_endpoint_is_reused_for_the_same_target(self):
        out = run_lua('print(tostring(d.get("/m4d/ps/", "http://a.example/x/") == d.get("/m4d/ps/", "http://a.example/x/")))')
        self.assertEqual(out, ['true'])


@unittest.skipUnless(HAVE_NGINX, 'no openresty binary')
class WorkerSeed(unittest.TestCase):
    def test_every_worker_seeds_the_endpoint_names_and_nginx_accepts_the_configuration(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, 'as.conf')
            with open(cfg, 'w') as fh:
                fh.write(CONFIG.format(d=d))
            for sub in ('log', 'run', 'temp'):
                os.makedirs(os.path.join(d, sub))
            ctx = Context(cfg)
            ctx.setAppLog(logging.getLogger('test'))
            ctx.addContentHostingConfiguration('chc-1', CHC.model_validate(chc_dict('http://origin.example.com/media/')))
            self.assertTrue(asyncio.run(OpenRestyWebProxy(ctx).writeConfiguration()))
            with open(os.path.join(d, 'nginx.conf')) as fh:
                text = fh.read()
            self.assertIn('init_worker_by_lua_block {\n    dynredir.seed()\n', text)
            run = subprocess.run([OPENRESTY, '-t', '-c', os.path.join(d, 'nginx.conf'), '-p', d + '/'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            self.assertEqual(run.returncode, 0, run.stdout)


if __name__ == '__main__':
    unittest.main()
