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
        self.assertIn('proxy_cache_valid 200 206 301 302 303 307 308 404 5s', text[second:])
        self.assertIn('add_header Cache-Control "max-age=5" always', text[second:])

    def test_status_code_filters_limit_the_codes(self):
        locn = location()
        locn.addCachingRule('x', False, 7, [404, 200])
        self.assertIn('proxy_cache_valid 200 404 7s', config(locn))

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
        for header in ('CMCD-Request', 'CMCD-Object', 'CMCD-Status', 'CMCD-Session'):
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
