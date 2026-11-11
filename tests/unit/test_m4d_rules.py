'''Tests of the M4d service location rules written to the web proxy configuration (TS 26.512 V19.3.0 clauses 7.6.4.2 and 7.6.4.5)'''

import asyncio
import logging
import unittest
from types import SimpleNamespace

from rt_5gms_as.proxies.openresty import OpenRestyLocationConfig


class FakeContext:
    def appLog(self):
        return logging.getLogger('test')

    def getConfigVar(self, section, name, default=None):
        return default


def location():
    return OpenRestyLocationConfig(FakeContext(), '/m4d/ps-1/', 'http://origin.example/media/', 'ps-1')


def config(locn):
    return asyncio.run(locn.config(0))


class CachingRules(unittest.TestCase):
    def test_the_rules_are_nested_in_order_and_the_first_match_applies(self):
        locn = location()
        self.assertTrue(locn.addCachingRule(r'\.mpd$', True, None, None))
        self.assertTrue(locn.addCachingRule('^/seg.*', False, 5, None))
        text = config(locn)
        first = text.index('location ~ "\\.mpd$"')
        second = text.index('location ~ "^/m4d/ps\\-1/seg.*"')
        self.assertLess(first, second)
        self.assertIn('proxy_no_cache 1', text[first:second])
        # without statusCodeFilters the directives apply to all origin response status codes (TS 26.510 table 8.8.3.1-1)
        self.assertIn('proxy_cache_valid any 5s', text[second:])
        self.assertIn('m4daccess.signal_ttl(5, {}, "', text[second:])
        self.assertNotIn('add_header Cache-Control', text[second:])

    def test_status_code_filters_limit_the_codes(self):
        locn = location()
        locn.addCachingRule('x', False, 7, [404, 200])
        self.assertIn('proxy_cache_valid 200 404 7s', config(locn))

    def test_without_status_code_filters_the_directives_apply_to_all_status_codes(self):
        locn = location()
        locn.addCachingRule('x', False, None, None)
        text = config(locn)
        self.assertIn('proxy_cache_valid any 10y', text)
        self.assertEqual(locn.status_maps(), {})

    def test_no_cache_and_the_header_apply_only_to_the_status_codes_of_the_filter(self):
        locn = location()
        locn.addCachingRule('x', True, None, [404, 410])
        text = config(locn)
        self.assertNotIn('proxy_no_cache 1', text)
        self.assertNotIn('add_header Cache-Control "no-cache', text)
        maps = locn.status_maps()
        self.assertEqual(len(maps), 2)
        nc = [m for m in maps.values() if 'upstream_status' in m][0]
        cc = [m for m in maps.values() if 'map $status' in m][0]
        self.assertIn('default "0";', nc)
        self.assertIn('404 "1";', nc)
        self.assertIn('410 "1";', nc)
        self.assertIn('default "";', cc)
        self.assertIn('404 "no-cache, no-store, max-age=0";', cc)
        for name in maps:
            self.assertIn('$' + name, text)

    def test_the_max_age_header_applies_only_to_the_status_codes_of_the_filter(self):
        locn = location()
        locn.addCachingRule('x', False, 9, [200])
        text = config(locn)
        self.assertIn('proxy_cache_valid 200 9s', text)
        self.assertNotIn('add_header Cache-Control', text)
        self.assertIn('m4daccess.signal_ttl(9, {[200]=true}, "', text)
        self.assertEqual(locn.status_maps(), {})

    def test_a_no_cache_rule_without_a_filter_applies_to_every_response(self):
        locn = location()
        locn.addCachingRule('x', True, None, None)
        text = config(locn)
        self.assertIn('proxy_no_cache 1', text)
        self.assertIn('add_header Cache-Control "no-cache, no-store, max-age=0" always', text)

    def test_a_pattern_that_cannot_be_written_is_refused(self):
        locn = location()
        self.assertFalse(locn.addCachingRule('a"b', False, 1, None))
        self.assertFalse(locn.addCachingRule('(', False, 1, None))
        self.assertFalse(locn.addCachingRule('a\\', False, 1, None))


class RewriteAndCmcd(unittest.TestCase):
    def test_an_anchored_pattern_is_relative_to_the_service_location(self):
        locn = location()
        self.assertTrue(locn.addRewriteRule('^/old/', '/legacy/'))
        regex, replace = locn.rewrite_rules[0]
        self.assertTrue(regex.startswith('^/m4d/ps\\-1/old/'))
        self.assertTrue(replace.startswith('/m4d/ps-1/legacy/'))

    def test_cmcd_is_not_passed_to_the_origin(self):
        text = config(location())
        # (TS 26.512 V19.3.0 clause 8.1: CMCD is not included in requests at M2d; the header field CMCD is read by the CMCD reporting too)
        for header in ('CMCD', 'CMCD-Request', 'CMCD-Object', 'CMCD-Status', 'CMCD-Session'):
            self.assertIn(f'proxy_set_header {header} ""', text)
        self.assertIn('CMCD=[^&]*', text)

    def test_redirects_303_and_308_are_mapped_too(self):
        self.assertIn('error_page 301 302 303 307 308 = @handle_redirect', config(location()))


class AccessRules(unittest.TestCase):
    def test_url_signature_cors_and_geofencing_are_checked_before_proxying(self):
        locn = location()
        sig = SimpleNamespace(url_pattern='.*', token_name='t', passphrase_name='p', passphrase='secret-pass', token_expiry_name='e',
                              use_ip_address=False, ip_address_name=None)
        self.assertTrue(locn.setUrlSignature(sig))
        locn.setGeoFencing(SimpleNamespace(locator_type='urn:3gpp:media-delivery:locator-type:iso3166', locators=['NL']))
        locn.addCorsAlias('alias.test')
        text = config(locn)
        self.assertIn('m4daccess.verify_signature', text)
        self.assertIn('m4daccess.check_geo', text)
        self.assertIn('m4daccess.cors', text)
        self.assertIn('filter_origin_headers', text)

    def test_locations_that_differ_in_their_rules_are_not_equal(self):
        a, b = location(), location()
        self.assertEqual(a, b)
        b.addCachingRule('x', True, None, None)
        self.assertNotEqual(a, b)


if __name__ == '__main__':
    unittest.main()


class MethodsToTheOrigin(unittest.TestCase):
    def test_only_get_and_head_reach_the_origin(self):
        # TS 26.512 V19.3.0 clause 4.6: a request that the cache cannot satisfy is transformed into a GET request to the origin
        text = config(location())
        self.assertIn('if method ~= "GET" and method ~= "HEAD" then', text)
        self.assertIn('ngx.header["Allow"] = "GET, HEAD"', text)
        self.assertIn('return ngx.exit(405)', text)

    def test_the_cors_preflight_of_the_aliases_is_let_through_to_the_access_phase(self):
        locn = location()
        locn.cors_aliases = ['player.example']
        text = config(locn)
        self.assertIn('method ~= "GET" and method ~= "HEAD" and method ~= "OPTIONS"', text)
        self.assertIn('ngx.header["Allow"] = "GET, HEAD, OPTIONS"', text)
