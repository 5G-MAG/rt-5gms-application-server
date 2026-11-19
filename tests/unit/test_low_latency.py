# Tests, with a private nginx and a mock origin, of the HTTP low-latency pull ingest and distribution (TS 26.512 V19.3.0 clauses 8.4 and 10.3).
# License: 5G-MAG Public License (v1.0)
import time
import unittest

from tests.unit.nginx_rig import HAVE_NGINX, Origin, Proxy, chc_dict

HOST = 'as.example.net'
LL = 'urn:3gpp:5gms:content-protocol:http-ll-pull'
DELAY = 1.0


def chunked(origin, status=200, headers=None):
    '''A route that sends two chunks of HTTP/1.1 chunked transfer coding with a delay in between, like a segment that is still being produced'''
    def route(handler):
        handler.send_response(status)
        handler.send_header('Content-Type', 'video/mp4')
        for name, value in (headers or {}).items():
            handler.send_header(name, value)
        handler.send_header('Transfer-Encoding', 'chunked')
        handler.end_headers()
        for part in (b'chunk-one', b'chunk-two'):
            handler.wfile.write(b'%x\r\n%s\r\n' % (len(part), part))
            handler.wfile.flush()
            time.sleep(DELAY)
        handler.wfile.write(b'0\r\n\r\n')
    return route


@unittest.skipUnless(HAVE_NGINX, 'no openresty binary')
class LowLatency(unittest.TestCase):
    def setUp(self):
        self.origin = Origin()
        self.origin.routes = {'/media/seg.m4s': chunked(self.origin),
                              '/media/part.m4s': chunked(self.origin, 206, {'Content-Range': 'bytes 5-*/*'})}
        self.proxy = None

    def start(self, protocol):
        self.proxy = Proxy({'c1': chc_dict(self.origin.port, protocol=protocol)})

    def tearDown(self):
        if self.proxy is not None:
            self.proxy.stop()
        self.origin.stop()

    def first_bytes(self, path, headers=None):
        '''(seconds until the first bytes of the body, seconds until its end, status, header fields, body)'''
        conn = self.proxy.connection()
        started = time.time()
        conn.request('GET', path, headers=dict({'Host': HOST}, **(headers or {})))
        response = conn.getresponse()
        first = response.read(5)
        got_first = time.time() - started
        rest = response.read()
        done = time.time() - started
        conn.close()
        return got_first, done, response.status, {k.lower(): v for k, v in response.getheaders()}, first + rest

    def test_a_partly_received_segment_is_passed_on_at_once(self):
        self.start(LL)
        first, done, status, _, body = self.first_bytes('/m4d/ps1/seg.m4s')
        self.assertEqual((status, body), (200, b'chunk-onechunk-two'))
        self.assertLess(first, DELAY * 0.6)
        self.assertGreater(done, DELAY * 1.5)

    def test_the_pull_ingest_of_clause_8_2_holds_the_segment_back_until_it_is_complete(self):
        # what the low-latency protocol differs from
        self.start('urn:3gpp:5gms:content-protocol:http-pull')
        first, _, status, _, body = self.first_bytes('/m4d/ps1/seg.m4s')
        self.assertEqual((status, body), (200, b'chunk-onechunk-two'))
        self.assertGreater(first, DELAY * 1.5)

    def test_a_byte_range_request_with_no_end_gets_the_aggregating_206_without_content_length(self):
        # clause 10.3: the last position 9007199254740991 asks for what the segment will have; the origin answers 206 without Content-Length
        self.start(LL)
        first, _, status, headers, body = self.first_bytes('/m4d/ps1/part.m4s', {'Range': 'bytes=5-9007199254740991'})
        self.assertEqual(status, 206)
        self.assertNotIn('content-length', headers)
        self.assertEqual(headers['content-range'], 'bytes 5-*/*')
        self.assertLess(first, DELAY * 0.6)
        self.assertEqual(self.origin.log[-1][2]['Range'], 'bytes=5-9007199254740991')


if __name__ == '__main__':
    unittest.main()
