# Unit tests of the query in the ingest baseURL of a Content Hosting Configuration (an AbsoluteUrl of TS 26.510 V19.2.0 clause 8.8.3.1).
# TS 26.512 V19.3.0 clause 8.2: the prefix of the M4d request URL is replaced with the ingest baseURL; the AS keeps its query for the origin.
# License: 5G-MAG Public License (v1.0)
import asyncio
import copy
import logging
import os
import shutil
import subprocess
import tempfile
import unittest

from rt_5gms_as.context import Context
from rt_5gms_as.openapi_5g.models.application_server_content_hosting_configuration import ApplicationServerContentHostingConfiguration as CHC
from rt_5gms_as.proxies.openresty import OpenRestyLocationConfig, OpenRestyWebProxy

OPENRESTY = os.environ.get('OPENRESTY_NGINX', '/home/fivegmag/work-5gms-rel19/openresty/nginx/sbin/nginx')

CONFIG = '''[DEFAULT]
log_dir = {d}/log
run_dir = {d}/run
[5gms_as]
cache_dir = {d}/as/cache
docroot = {d}/as/docroots
certificates_cache = {d}/as/certificates
m3_listen = 127.0.0.1
m3_port = 7777
http_port = 8080
[5gms_as.nginx]
config_file = {d}/nginx.conf
root_temp = {d}/temp
resolvers = 127.0.0.1
'''

def chc_dict(base_url, rules=None):
    return {'name': 'test', 'externalServiceId': 'com.example.t',
            'ingestConfiguration': {'mode': 'PULL', 'protocol': 'urn:3gpp:5gms:content-protocol:http-pull', 'baseURL': base_url},
            'distributionConfigurations': [{'distributionId': 'd1', 'canonicalDomainName': 'as.example.net',
                                            'baseURL': 'http://as.example.net/m4d/provisioning-session-x/',
                                            'entryPoint': {'relativePath': 'manifest.mpd', 'contentType': 'application/dash+xml'},
                                            **({'cachingConfigurations': rules} if rules else {})}]}

class FakeContext(object):
    def getConfigVar(self, section, name, default=None):
        return default
    def appLog(self):
        return logging.getLogger('test')

def location_config(base_prefix, query=''):
    return asyncio.run(OpenRestyLocationConfig(FakeContext(), '/m4d/ps/', base_prefix, 'ps', query).config(2))

class Validation(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.config = os.path.join(self.dir.name, 'as.conf')
        with open(self.config, 'w') as fh:
            fh.write(CONFIG.format(d=self.dir.name))
        self.ctx = Context(self.config)
        self.ctx.setAppLog(logging.getLogger('test'))

    def tearDown(self):
        self.dir.cleanup()

    def add(self, base_url, raw=False):
        chc = CHC.model_validate(chc_dict('http://origin.example.com/p' if raw else base_url))
        if raw:
            # the string as it would be after the model, which percent-encodes a quotation mark and a space and drops a line end
            object.__setattr__(chc.ingest_configuration, 'base_url', base_url)
        self.ctx.addContentHostingConfiguration('chc-1', chc)

    def test_base_url_with_a_query_is_accepted(self):
        # RFC 3986 clause 3.4: query = *( pchar / "/" / "?" )
        for url in ('https://origin.example.com/some/path?x=1', 'http://origin.example.com/p/?a=b&c=d%20e&f=/g?h',
                    "http://origin.example.com?k=(v)*!'+,:@~._-", 'http://origin.example.com/p?', 'http://origin.example.com/p?x'):
            with self.subTest(url=url):
                self.add(url)
                self.assertTrue(self.ctx.haveContentHostingConfiguration('chc-1'))

    def test_base_url_path_with_the_sub_delimiters_of_rfc_3986_is_accepted(self):
        # only what would change the structure of the proxy configuration is refused ("$" and ";" are not in the path either)
        for url in ("http://origin.example.com/vod(1)/a!b/c'd*e&f/", 'http://origin.example.com/a,b=c+d:e@f/'):
            with self.subTest(url=url):
                self.add(url)
                self.assertTrue(self.ctx.haveContentHostingConfiguration('chc-1'))

    def test_query_with_a_character_that_changes_the_proxy_configuration_is_refused(self):
        for char in ('"', ';', '{', '}', '$', ' ', '\n', '\r', '\\', '|', '`', '<', '[', ']', '#', '%zz', '%4'):
            url = 'http://origin.example.com/p?x=1' + char + 'y'
            with self.subTest(char=char):
                with self.assertRaises(Context.ValueError):
                    self.add(url, raw=True)
                self.assertFalse(self.ctx.haveContentHostingConfiguration('chc-1'))

    def test_what_the_model_encodes_is_safe(self):
        # a quotation mark and a space are percent-encoded by the model (a line end is dropped): nothing to refuse
        for char in ('"', ' ', '<'):
            with self.subTest(char=char):
                self.add('http://origin.example.com/p?x=1' + char + 'y')
        for char in (';', '{', '}', '$', '\\', '|', '[', '%zz'):
            with self.subTest(char=char):
                with self.assertRaises(Context.ValueError):
                    self.add('http://origin.example.com/p?x=1' + char + 'y')

    def test_fragment_is_refused(self):
        # an absolute-URI has no fragment (RFC 3986 clause 4.3)
        for url in ('http://origin.example.com/p?x=1#f', 'http://origin.example.com/p#f', 'http://origin.example.com/p?x=1#'):
            with self.subTest(url=url):
                with self.assertRaises(Context.ValueError):
                    self.add(url)

    def test_safe_query_check(self):
        self.assertTrue(Context.isProxySafeQuery(''))
        self.assertTrue(Context.isProxySafeQuery('a=1&b=%2F'))
        self.assertFalse(Context.isProxySafeQuery('a=1\n'))
        self.assertFalse(Context.isProxySafeQuery('a=$b'))
        for char in '"; {}$\n\r\\':
            self.assertFalse(Context.isProxySafeQuery('a=' + char))

class LocationConfig(unittest.TestCase):
    # what the location was before the query of the baseURL was supported
    WITHOUT_QUERY_PROXY_PASS = '    proxy_pass $downstream_prefix_url;\n'

    def test_location_without_a_query_is_unchanged(self):
        conf = location_config('http://origin.example.com/p/')
        self.assertIn('    set $downstream_prefix_url "http://origin.example.com/p/";\n    set $location_prefix "/m4d/ps/";\n    proxy_cache_key', conf)
        self.assertIn(self.WITHOUT_QUERY_PROXY_PASS, conf)
        for word in ('downstream_query', 'downstream_base_query', 'origin_prefix'):
            self.assertNotIn(word, conf)
        self.assertEqual(conf, location_config('http://origin.example.com/p/', ''))

    def test_query_is_set_apart_from_the_prefix_and_appended_after_the_path(self):
        conf = location_config('http://origin.example.com/p/', 'x=1&y=%20')
        self.assertIn('    set $downstream_prefix_url "http://origin.example.com/p/";\n', conf)
        self.assertIn('    set $downstream_base_query "x=1&y=%20";\n    set $downstream_query "";\n', conf)
        self.assertIn('    proxy_pass $downstream_prefix_url$downstream_query;\n', conf)
        self.assertNotIn(self.WITHOUT_QUERY_PROXY_PASS, conf)
        # a location given by a redirect of the origin does not get the query of the baseURL
        self.assertIn('local origin_prefix = ngx.var.downstream_prefix_url\n', conf)
        self.assertIn('if ngx.var.downstream_prefix_url == origin_prefix then\n        ngx.var.downstream_query = "?"..ngx.var.downstream_base_query', conf)
        # the query is not part of the Lua code, so it cannot end a Lua string
        self.assertEqual(conf.count('x=1&y=%20'), 1)

    def test_unsafe_query_is_not_written(self):
        for query in ('a"b', 'a;b', 'a{b}', 'a$b', 'a b', 'a\nb'):
            with self.subTest(query=query):
                with self.assertRaises(ValueError):
                    OpenRestyLocationConfig(FakeContext(), '/m4d/ps/', 'http://o/', 'ps', query)

    def test_locations_differ_by_the_query(self):
        a = OpenRestyLocationConfig(FakeContext(), '/m4d/ps/', 'http://o/p/', 'ps', 'x=1')
        self.assertEqual(a, OpenRestyLocationConfig(FakeContext(), '/m4d/ps/', 'http://o/p/', 'ps', 'x=1'))
        self.assertNotEqual(a, OpenRestyLocationConfig(FakeContext(), '/m4d/ps/', 'http://o/p/', 'ps', 'x=2'))
        self.assertNotEqual(a, OpenRestyLocationConfig(FakeContext(), '/m4d/ps/', 'http://o/p/', 'ps'))

@unittest.skipUnless(os.access(OPENRESTY, os.X_OK) and os.path.isfile('/usr/local/openresty/nginx/conf/mime.types'), 'no openresty binary')
class GeneratedConfiguration(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.config = os.path.join(self.dir.name, 'as.conf')
        with open(self.config, 'w') as fh:
            fh.write(CONFIG.format(d=self.dir.name))
        for sub in ('log', 'run', 'temp'):
            os.makedirs(os.path.join(self.dir.name, sub))
        self.ctx = Context(self.config)
        self.ctx.setAppLog(logging.getLogger('test'))

    def tearDown(self):
        self.dir.cleanup()

    def generate(self, base_url, rules=None):
        self.ctx.addContentHostingConfiguration('chc-1', CHC.model_validate(chc_dict(base_url, rules)))
        self.assertTrue(asyncio.run(OpenRestyWebProxy(self.ctx).writeConfiguration()))
        with open(os.path.join(self.dir.name, 'nginx.conf')) as fh:
            return fh.read()

    def check(self):
        run = subprocess.run([OPENRESTY, '-t', '-c', os.path.join(self.dir.name, 'nginx.conf'), '-p', self.dir.name + '/'],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.assertEqual(run.returncode, 0, run.stdout)

    def test_nginx_accepts_the_configuration_with_a_query(self):
        rules = [{'urlPatternFilter': '.*\\.m4s', 'cachingDirectives': {'noCache': False, 'maxAge': 60}}]
        conf = self.generate('https://origin.example.com/some/path?x=1&y=%2F', rules)
        # the prefix of the origin URL keeps what it was; the query is a separate value, in the location and in the one of the caching rule
        self.assertIn('set $downstream_prefix_url "https://origin.example.com/some/path/";', conf)
        self.assertEqual(conf.count('set $downstream_base_query "x=1&y=%2F";'), 2)
        self.assertEqual(conf.count('proxy_pass $downstream_prefix_url$downstream_query;'), 2)
        self.check()

    def test_nginx_accepts_the_sub_delimiters_in_the_path(self):
        conf = self.generate("https://origin.example.com/vod(1)/a!b/c'd*e&f")
        self.assertIn("set $downstream_prefix_url \"https://origin.example.com/vod(1)/a!b/c'd*e&f/\";", conf)
        self.check()

    def test_nginx_accepts_the_configuration_without_a_query(self):
        conf = self.generate('https://origin.example.com/some/path/')
        self.assertIn('set $downstream_prefix_url "https://origin.example.com/some/path/";', conf)
        self.assertNotIn('downstream_query', conf)
        self.check()

    def test_empty_query_is_the_same_as_none(self):
        conf = self.generate('https://origin.example.com/some/path?')
        self.assertNotIn('downstream_query', conf)
        self.check()

if __name__ == '__main__':
    unittest.main()
