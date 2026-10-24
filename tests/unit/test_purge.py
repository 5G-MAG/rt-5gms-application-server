# Unit tests of the purge of the cache of the 5GMS Application Server.
# License: 5G-MAG Public License (v1.0)
import asyncio
import unittest

from rt_5gms_as.proxy_factory import WebProxyInterface, WebProxyRegexTimeout
from rt_5gms_as.proxies.openresty import OpenRestyWebProxy

class Context(object):
    def __init__(self, timeout):
        self.timeout = timeout
    def getConfigVar(self, section, name, default=None):
        return self.timeout if name == 'purge_regex_timeout' else default
    def appLog(self):
        import logging
        return logging.getLogger('test')

class Proxy(WebProxyInterface):
    '''The purge of the interface, over a fixed list of cache entries'''
    def __init__(self, timeout, entries):
        self._context = Context(timeout)
        self.entries = entries
        self.purged = []
    async def _purge(self, key_filter):
        hits = [e for e in self.entries if key_filter(*e)]
        self.purged += hits
        return len(hits)

class PurgeUsingRegex(unittest.TestCase):
    def test_matching_entries_of_the_session_are_purged(self):
        proxy = Proxy('1.0', [('s1', '/a/seg1.m4s'), ('s1', '/b/seg1.m4s'), ('s2', '/a/seg1.m4s')])
        self.assertEqual(asyncio.run(proxy.purgeUsingRegex('s1', '/a/.*')), 1)
        self.assertEqual(proxy.purged, [('s1', '/a/seg1.m4s')])

    def test_pattern_that_takes_too_long_is_refused(self):
        # a bound with a source: the option purge_regex_timeout of the section 5gms_as
        proxy = Proxy('0.3', [('s1', 'a' * 60 + 'b')])
        with self.assertRaises(WebProxyRegexTimeout):
            asyncio.run(proxy.purgeUsingRegex('s1', r'^(a|aa)+$'))

    def test_malformed_pattern_is_an_error_of_the_regex_module(self):
        import regex
        with self.assertRaises(regex.error):
            asyncio.run(Proxy('1.0', []).purgeUsingRegex('s1', '('))

class CacheKey(unittest.TestCase):
    def setUp(self):
        self.proxy = object.__new__(OpenRestyWebProxy)

    def test_url_path_may_contain_the_separator(self):
        convert = self.proxy._OpenRestyWebProxy__key_to_prov_sess_and_url_path
        self.assertEqual(convert('s1:u=/path/seg.m4s'), ('s1', '/path/seg.m4s'))
        self.assertEqual(convert('s1:u=/path:u=/x'), ('s1', '/path:u=/x'))

    def test_key_without_a_session_is_rejected(self):
        with self.assertRaises(ValueError):
            self.proxy._OpenRestyWebProxy__key_to_prov_sess_and_url_path('no separator')

if __name__ == '__main__':
    unittest.main()
