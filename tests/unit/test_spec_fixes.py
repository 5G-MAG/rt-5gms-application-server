# Unit tests of the path rewrite rules, the strict reading of the purge body and the time-to-live signalled at M4d.
# TS 26.510 V19.2.0 clause 8.8.3.1 (PathRewriteRule, maxAge), TS 26.512 V19.3.0 clauses 4.5.4.9 and 7.6.4.2.
# License: 5G-MAG Public License (v1.0)
import asyncio
import logging
import os
import re
import shutil
import subprocess
import tempfile
import unittest

import rt_5gms_as
from rt_5gms_as.context import Context
from rt_5gms_as.openapi_5g.models.application_server_content_hosting_configuration import ApplicationServerContentHostingConfiguration as CHC
from rt_5gms_as.proxies.openresty import OpenRestyLocationConfig, OpenRestyWebProxy
from rt_5gms_as.schema_checks import purge_form_problem

from tests.unit.test_ingest_base_query import CONFIG, OPENRESTY, chc_dict, FakeContext

RESTY = shutil.which('resty') or ('/usr/local/openresty/bin/resty' if os.path.exists('/usr/local/openresty/bin/resty') else None)
PROXIES = os.path.join(os.path.dirname(os.path.abspath(rt_5gms_as.__file__)), 'proxies')
HAVE_NGINX = os.access(OPENRESTY, os.X_OK) and os.path.isfile('/usr/local/openresty/nginx/conf/mime.types')


def apply_rewrite(rule, path):
    '''What nginx does with a generated rewrite rule (the named group of PCRE written as ${name} in the replacement)'''
    regex, replace = rule
    match = re.match(regex.replace('(?<', '(?P<'), path)
    if match is None:
        return None
    replace = re.sub(r'\$\{(\w+)\}', lambda m: match.group(m.group(1)), replace)
    return re.sub(r'\$(\d+)', lambda m: match.group(int(m.group(1))), replace)


class PathRewriteRules(unittest.TestCase):
    def location(self, *rules):
        locn = OpenRestyLocationConfig(FakeContext(), '/m4d/ps/', 'http://origin.example.com/media/', 'ps')
        for rule in rules:
            self.assertTrue(locn.addRewriteRule(*rule))
        return locn

    def test_pattern_with_leading_wildcard_matches_the_whole_path_up_to_the_last_slash(self):
        locn = self.location(('.*/a/$', '/first/'))
        # the portion of the path that matches the pattern is replaced, the leaf is kept
        # (the service location stays in front, as with a pattern that starts with ^; the proxy strips it before the request goes to the origin)
        self.assertEqual(apply_rewrite(locn.rewrite_rules[0], '/m4d/ps/a/1.m4s'), '/m4d/ps/first/1.m4s')
        self.assertEqual(apply_rewrite(locn.rewrite_rules[0], '/m4d/ps/x/y/a/1.m4s?q=1'), '/m4d/ps/first/1.m4s?q=1')
        self.assertIsNone(apply_rewrite(locn.rewrite_rules[0], '/m4d/ps/a/b/1.m4s'))

    def test_a_pattern_that_does_not_start_with_a_caret_is_applied_after_the_base_url_only(self):
        # TS 26.512 V19.3.0 clause 7.6.4.2: "the remainder of the request URL (i.e., the path segments following DistributionConfiguration.baseURL)"
        locn = self.location(('ps/', '/first/'), ('m4d/', '/second/'))
        # "ps/" and "m4d/" are found in the base URL path and nowhere else
        self.assertIsNone(apply_rewrite(locn.rewrite_rules[0], '/m4d/ps/1.m4s'))
        self.assertIsNone(apply_rewrite(locn.rewrite_rules[1], '/m4d/ps/1.m4s'))
        # found in the remainder: the first place in the remainder is the portion that is replaced
        self.assertEqual(apply_rewrite(locn.rewrite_rules[0], '/m4d/ps/x/ps/y/1.m4s'), '/m4d/ps/x//first/y/1.m4s')

    def test_the_replacement_does_not_use_the_braced_number_that_nginx_reads_as_a_variable(self):
        locn = self.location(('a/', '/first/'), ('^/b/', '1/'))
        for regex, replace in locn.rewrite_rules:
            self.assertNotIn('${1}', replace)

    @unittest.skipUnless(HAVE_NGINX, 'no openresty binary')
    def test_nginx_accepts_the_configuration(self):
        for rules in ([('.*/a/$', '/first/'), ('.*/a/$', '/second/')], [('seg/', '1/')], [('^/old/', '/new/')]):
            with self.subTest(rules=rules), tempfile.TemporaryDirectory() as d:
                cfg = os.path.join(d, 'as.conf')
                with open(cfg, 'w') as fh:
                    fh.write(CONFIG.format(d=d))
                for sub in ('log', 'run', 'temp'):
                    os.makedirs(os.path.join(d, sub))
                ctx = Context(cfg)
                ctx.setAppLog(logging.getLogger('test'))
                body = chc_dict('http://origin.example.com/media/')
                body['distributionConfigurations'][0]['pathRewriteRules'] = [{'requestPathPattern': p, 'mappedPath': m} for p, m in rules]
                ctx.addContentHostingConfiguration('chc-1', CHC.model_validate(body))
                self.assertTrue(asyncio.run(OpenRestyWebProxy(ctx).writeConfiguration()))
                run = subprocess.run([OPENRESTY, '-t', '-c', os.path.join(d, 'nginx.conf'), '-p', d + '/'],
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                self.assertEqual(run.returncode, 0, run.stdout)


class PurgeBody(unittest.TestCase):
    def test_a_single_pattern_pair_is_well_formed(self):
        for body in (b'pattern=.*', b'pattern=%2E%2A%5C.m4s', b'pattern=a+b', b'pattern=(', b'pattern=', b'pattern=a=b'):
            with self.subTest(body=body):
                self.assertIsNone(purge_form_problem(body))

    def test_a_malformed_body_is_found(self):
        for body in (b'pattern=%zz', b'pattern=%4', b'pattern=%', b'pattern=%ff', b'pattern', b'', b'other=.*', b'pattern=a&pattern=b',
                     b'pattern=.*&x=1', 'pattern=é'.encode('utf-8')):
            with self.subTest(body=body):
                self.assertIsNotNone(purge_form_problem(body))


@unittest.skipIf(RESTY is None, 'the resty command of OpenResty is not available')
class SignalledTimeToLive(unittest.TestCase):
    SCRIPT = '''
package.path = "%(proxies)s/?.lua;" .. package.path
local m = require "m4daccess"
local real = ngx
local stored = {kept = %(kept)s}
local dict = {get = function(self, key) return stored[key] end, set = function(self, key, value, ttl) stored[key] = value; stored.ttl = ttl end}
ngx = {status = %(status)d, var = {upstream_http_date = %(date)s, upstream_cache_status = %(cache)s, uri = "/seg1.m4s", m4d_path = "/seg1.m4s", m4d_key_prefix = "ps"}, header = {},
       time = function() return 1700000100 end, parse_http_time = real.parse_http_time, shared = {m4d_ingest = dict}}
stored["ps:u=/seg1.m4s"] = stored.kept
m.signal_ttl(120, {%(codes)s}, "ps")
print(tostring(ngx.header["Cache-Control"]) .. " " .. tostring(stored["ps:u=/seg1.m4s"]) .. " " .. tostring(stored.ttl))
'''
    INGESTED = '"Tue, 14 Nov 2023 22:13:20 GMT"'     # 1700000000

    def run_ttl(self, date=INGESTED, status=200, codes='', cache='"HIT"', kept='nil'):
        script = self.SCRIPT % {'proxies': PROXIES, 'status': status, 'date': date, 'codes': codes, 'cache': cache, 'kept': kept}
        out = subprocess.run([RESTY, '-e', script], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.split()

    def ttl(self, *args, **kwargs):
        return self.run_ttl(*args, **kwargs)[0]

    def test_the_time_to_live_counts_down_from_the_ingest(self):
        # a cached resource with no ingest time of the AS kept: 100 s after the Date of the origin response
        self.assertEqual(self.ttl(), 'max-age=20')

    def test_a_resource_just_ingested_gets_the_whole_max_age(self):
        self.assertEqual(self.ttl('"Tue, 14 Nov 2023 22:15:00 GMT"'), 'max-age=120')
        self.assertEqual(self.ttl('"Tue, 14 Nov 2023 22:20:00 GMT"'), 'max-age=120')     # origin clock ahead
        self.assertEqual(self.ttl('nil'), 'max-age=120')

    def test_skew_between_the_origin_and_the_clock_of_the_as_does_not_count(self):
        # TS 26.512 V19.3.0 clause 7.6.4.2: the AS compensates for synchronization skew. The resource was ingested 100 s ago on the clock of the AS
        for date in ('"Tue, 14 Nov 2023 22:03:20 GMT"',      # origin clock 600 s behind
                     '"Tue, 14 Nov 2023 22:23:20 GMT"'):     # origin clock 600 s ahead
            with self.subTest(date=date):
                self.assertEqual(self.ttl(date, kept='1700000000'), 'max-age=20')

    def test_a_response_fetched_from_the_origin_now_gets_the_whole_max_age_and_its_ingest_is_kept(self):
        for cache in ('"MISS"', '"EXPIRED"', '"BYPASS"', 'nil'):
            with self.subTest(cache=cache):
                # the Date of the origin is 600 s behind
                self.assertEqual(self.run_ttl('"Tue, 14 Nov 2023 22:03:20 GMT"', cache=cache), ['max-age=120', '1700000100', '120'])

    def test_a_resource_served_from_the_cache_keeps_its_ingest_time(self):
        self.assertEqual(self.run_ttl(kept='1700000090', cache='"HIT"')[:2], ['max-age=110', '1700000090'])

    def test_the_time_to_live_does_not_go_below_zero(self):
        self.assertEqual(self.ttl('"Tue, 14 Nov 2023 22:00:00 GMT"'), 'max-age=0')

    def test_only_the_status_codes_of_the_filter_are_signalled(self):
        self.assertEqual(self.ttl(status=404, codes='[200]=true'), 'nil')
        self.assertEqual(self.ttl(status=200, codes='[200]=true'), 'max-age=20')


class SignalledTimeToLiveConfiguration(unittest.TestCase):
    def test_the_header_comes_from_the_lua_block_and_not_from_a_fixed_directive(self):
        locn = OpenRestyLocationConfig(FakeContext(), '/m4d/ps/', 'http://o/', 'ps')
        locn.addCachingRule('.*\\.m4s', False, 120, None)
        locn.addCachingRule('x', True, None, None)
        locn.addCachingRule('y', False, None, None)
        text = asyncio.run(locn.config(0))
        self.assertEqual(text.count('m4daccess.signal_ttl(120, {}, "ps")'), 1)
        self.assertEqual(text.count('signal_ttl'), 1)
        self.assertNotIn('max-age=120', text)

    @unittest.skipUnless(HAVE_NGINX, 'no openresty binary')
    def test_nginx_accepts_the_configuration(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, 'as.conf')
            with open(cfg, 'w') as fh:
                fh.write(CONFIG.format(d=d))
            for sub in ('log', 'run', 'temp'):
                os.makedirs(os.path.join(d, sub))
            ctx = Context(cfg)
            ctx.setAppLog(logging.getLogger('test'))
            rules = [{'urlPatternFilter': '.*\\.m4s', 'cachingDirectives': {'maxAge': 120, 'statusCodeFilters': [200, 404]}},
                     {'urlPatternFilter': '.*\\.mpd', 'cachingDirectives': {'maxAge': 5}}]
            ctx.addContentHostingConfiguration('chc-1', CHC.model_validate(chc_dict('http://origin.example.com/media/', rules)))
            self.assertTrue(asyncio.run(OpenRestyWebProxy(ctx).writeConfiguration()))
            with open(os.path.join(d, 'nginx.conf')) as fh:
                self.assertIn('m4daccess.signal_ttl(120, {[200]=true,[404]=true}, "chc-1")', fh.read())
            run = subprocess.run([OPENRESTY, '-t', '-c', os.path.join(d, 'nginx.conf'), '-p', d + '/'],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            self.assertEqual(run.returncode, 0, run.stdout)


if __name__ == '__main__':
    unittest.main()
