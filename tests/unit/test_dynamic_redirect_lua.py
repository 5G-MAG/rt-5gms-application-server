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

from tests.unit.test_ingest_base_query import CONFIG, OPENRESTY, chc_dict, location_config

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


@unittest.skipIf(RESTY is None, 'the resty command of OpenResty is not available')
class RedirectTarget(unittest.TestCase):
    BASE = 'http://o.example:81/media/sub/a.m4s'

    def split(self, location, base=BASE):
        return run_lua('print(table.concat({d.split_location("%s", "%s")}, "|"))' % (location, base))[0].split('|')

    def test_an_absolute_location_keeps_its_query_in_the_part_after_the_endpoint(self):
        self.assertEqual(self.split('http://cdn.example/other/b.m4s?sig=abc'), ['http://cdn.example/other/', 'b.m4s?sig=abc'])
        # a slash in the query does not end the prefix
        self.assertEqual(self.split('http://cdn.example/x/y.m4s?sig=a/b'), ['http://cdn.example/x/', 'y.m4s?sig=a/b'])

    def test_a_relative_location_is_resolved_against_the_url_requested_from_the_origin(self):
        # RFC 9110 clause 10.2.2 and RFC 3986 clause 5.2.2
        self.assertEqual(self.split('/other/b.m4s?x=1'), ['http://o.example:81/other/', 'b.m4s?x=1'])
        self.assertEqual(self.split('c.m4s'), ['http://o.example:81/media/sub/', 'c.m4s'])
        self.assertEqual(self.split('../c.m4s'), ['http://o.example:81/media/', 'c.m4s'])
        self.assertEqual(self.split('./d/../c.m4s'), ['http://o.example:81/media/sub/', 'c.m4s'])
        self.assertEqual(self.split('//cdn.example/z/w.m4s'), ['http://cdn.example/z/', 'w.m4s'])
        self.assertEqual(self.split('?x=1'), ['http://o.example:81/media/sub/', 'a.m4s?x=1'])
        # the query of the request URL is not part of the base
        self.assertEqual(self.split('c.m4s', 'http://o.example/a/b.m4s?q=1'), ['http://o.example/a/', 'c.m4s'])

    def test_the_target_may_be_a_directory_or_the_root(self):
        self.assertEqual(self.split('http://h.example/other/dir/'), ['http://h.example/other/', 'dir/'])
        self.assertEqual(self.split('http://h.example'), ['http://h.example/', ''])

    def test_the_query_of_the_request_is_cleaned_for_the_origin_and_for_the_cache_key(self):
        self.assertEqual(run_lua('print(d.query("sig=abc&b=2"))'), ['sig=abc&b=2'])
        # what the CMCD handler of the location removed can leave a separator or an empty parameter
        self.assertEqual(run_lua('print(d.query("&b=2&&c="))'), ['b=2&c='])
        # the parameters named in the second argument are left out
        self.assertEqual(run_lua('print(d.query("exp=1&a=b&tok=x&tokx=1", {"tok", "exp"}))'), ['a=b&tokx=1'])
        self.assertEqual(run_lua('print("[" .. d.query("") .. "]")\nprint("[" .. d.query(nil) .. "]")'), ['[]', '[]'])
        self.assertEqual(run_lua('print(d.suffix("a=1") .. "|" .. d.suffix(""))'), ['?a=1|'])

    def test_the_map_tells_whether_the_location_is_an_endpoint_of_a_redirect(self):
        out = run_lua('''
d.set("/m4d/ps/redir-1/", "http://t.example/x/")
local function show(a, b, c) print(a .. "|" .. b .. "|" .. tostring(c)) end
show(d.mapUrl("/m4d/ps/", "http://o.example/", "/redir-1/b.m4s"))
show(d.mapUrl("/m4d/ps/", "http://o.example/", "/a/b.m4s"))
''')
        self.assertEqual(out, ['http://t.example/x/|/b.m4s|true', 'http://o.example/|/a/b.m4s|false'])


@unittest.skipIf(RESTY is None, 'the resty command of OpenResty is not available')
class OriginPath(unittest.TestCase):
    def test_the_decoded_path_of_the_request_is_percent_encoded_for_the_origin(self):
        # a decoded "?" would else start the query, a "#" a fragment, and a space or a "%" would make the URL of the origin invalid
        out = run_lua('print(d.escape_path("a b/é?x#y%z.m4s"))')
        self.assertEqual(out, ['a%20b/%C3%A9%3Fx%23y%25z.m4s'])

    def test_unreserved_characters_and_sub_delimiters_are_kept(self):
        path = "seg-1_a.b~c/d:e@f!$&'()*+,;=/seg.m4s"
        self.assertEqual(run_lua('print(d.escape_path("%s"))' % path), [path])

    def test_the_location_encodes_the_path_that_follows_the_service_location(self):
        self.assertIn('ngx.var.downstream_prefix_url..dynredir.escape_path(ngx.ctx.uri:sub(2))', location_config('http://origin.example.com/p/'))


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


@unittest.skipUnless(HAVE_NGINX, 'no openresty binary')
class RedirectConfiguration(unittest.TestCase):
    def test_the_locations_use_the_resolution_and_the_query_of_the_target_and_nginx_accepts_them(self):
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
            self.assertIn('ngx.ctx.redirected = dynredir.mapUrl(', text)
            self.assertIn('ngx.var.downstream_prefix_url = ngx.var.downstream_prefix_url..dynredir.suffix(query)', text)
            self.assertIn('dynredir.split_location(ngx.var.upstream_http_location, ngx.var.downstream_prefix_url)', text)
            run = subprocess.run([OPENRESTY, '-t', '-c', os.path.join(d, 'nginx.conf'), '-p', d + '/'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            self.assertEqual(run.returncode, 0, run.stdout)


if __name__ == '__main__':
    unittest.main()
