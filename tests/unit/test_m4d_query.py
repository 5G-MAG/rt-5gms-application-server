# Tests, with a private nginx and a mock origin, of the query of an M4d request: it goes to the origin with the rest of the URL (TS 26.512 V19.3.0
# clause 8.2), without the CMCD parameter (clause 8.1), and it is part of the cache key of the transport resource (clause 10.1).
# License: 5G-MAG Public License (v1.0)
import asyncio
import base64
import hashlib
import time
import unittest

from rt_5gms_as.proxies.openresty import OpenRestyWebProxy
from tests.unit.nginx_rig import HAVE_NGINX, Origin, Proxy, chc_dict

HOST = 'as.example.net'


@unittest.skipUnless(HAVE_NGINX, 'no openresty binary')
class QueryToTheOrigin(unittest.TestCase):
    def setUp(self):
        self.origin = Origin()
        self.origin.routes = {'/media/seg.m4s': self.origin.answer(200, {'Content-Type': 'video/mp4'}, b'segment'),
                              '/media/redir': self.origin.answer(302, {'Location': 'http://127.0.0.1:%d/other/b.m4s?sig=abc' % self.origin.port}),
                              '/other/b.m4s': lambda h: self.origin.answer(200 if 'sig=abc' in h.path else 403, {}, b'ok')(h)}
        self.proxy = None

    def start(self, **kwargs):
        self.proxy = Proxy({'c1': chc_dict(self.origin.port, **kwargs)})

    def tearDown(self):
        if self.proxy is not None:
            self.proxy.stop()
        self.origin.stop()

    def paths(self):
        return [entry[1] for entry in self.origin.log]

    def test_the_query_goes_to_the_origin_without_the_cmcd_parameter(self):
        self.start()
        self.assertEqual(self.proxy.get(HOST, '/m4d/ps1/seg.m4s?a=1&CMCD=sid%3D%22s%22&b=%20x')[0], 200)
        self.assertEqual(self.paths(), ['/media/seg.m4s?a=1&b=%20x'])
        self.proxy.get(HOST, '/m4d/ps1/seg.m4s?CMCD=sid%3D%22s%22')
        self.assertEqual(self.paths()[-1], '/media/seg.m4s')

    def test_the_query_of_the_ingest_base_url_comes_first(self):
        self.start(ingest={'baseURL': 'http://127.0.0.1:%d/media/?token=t1' % self.origin.port})
        self.proxy.get(HOST, '/m4d/ps1/seg.m4s?a=1')
        self.proxy.get(HOST, '/m4d/ps1/seg.m4s?a=2')
        self.assertEqual(self.paths(), ['/media/seg.m4s?token=t1&a=1', '/media/seg.m4s?token=t1&a=2'])

    def test_resources_that_differ_in_the_query_are_cached_apart(self):
        self.start()
        for query in ('a=1', 'a=2', 'a=1', 'a=2'):
            self.proxy.get(HOST, '/m4d/ps1/seg.m4s?' + query)
        self.assertEqual(self.paths(), ['/media/seg.m4s?a=1', '/media/seg.m4s?a=2'])

    def test_the_cmcd_parameter_does_not_split_the_cache(self):
        self.start()
        for sid in ('s1', 's2'):
            self.proxy.get(HOST, '/m4d/ps1/seg.m4s?CMCD=sid%%3D%%22%s%%22' % sid)
        self.assertEqual(len(self.origin.log), 1)

    def test_the_proof_of_a_url_signature_does_not_split_the_cache(self):
        # clause 7.6.4.5: the token and its expiry are the proof for the AS; the resource is the same for every client
        signature = {'urlPattern': '.*\\.m4s', 'tokenName': 'tok', 'passphraseName': 'pp', 'passphrase': 'secret1', 'tokenExpiryName': 'exp', 'useIPAddress': False}
        self.start(dist={'urlSignature': signature})
        for offset in (100, 200):
            expiry = str(int(time.time()) + offset)
            url = 'http://%s/m4d/ps1/seg.m4s' % HOST
            token = base64.urlsafe_b64encode(hashlib.sha512(('%s&exp=%s&pp=secret1' % (url, expiry)).encode()).digest()).decode().rstrip('=')
            self.assertEqual(self.proxy.get(HOST, '/m4d/ps1/seg.m4s?v=1&exp=%s&tok=%s' % (expiry, token))[0], 200)
        self.assertEqual(len(self.origin.log), 1)

    def test_a_purge_pattern_that_ends_at_the_path_finds_the_cached_resources_with_a_query(self):
        # clause 4.5.4.9: the regular expression identifies the media resource URLs
        self.start()
        for query in ('a=1', 'a=2'):
            self.proxy.get(HOST, '/m4d/ps1/seg.m4s?' + query)
        self.assertEqual(asyncio.run(OpenRestyWebProxy(self.proxy.context).purgeUsingRegex('c1', r'/seg\.m4s$')), 2)
        self.proxy.get(HOST, '/m4d/ps1/seg.m4s?a=1')
        self.assertEqual(len(self.origin.log), 3)

    def test_the_query_of_the_target_of_a_redirect_reaches_the_target(self):
        self.start()
        status, headers, _ = self.proxy.get(HOST, '/m4d/ps1/redir')
        self.assertEqual(status, 302)
        self.assertTrue(headers['location'].endswith('/b.m4s?sig=abc'))
        self.assertEqual(self.proxy.get(HOST, headers['location'])[0], 200)
        self.assertEqual(self.paths()[-1], '/other/b.m4s?sig=abc')


if __name__ == '__main__':
    unittest.main()
