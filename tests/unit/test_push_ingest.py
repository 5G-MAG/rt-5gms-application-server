# Tests, with a private nginx and a mock encoder as the ingest source, of the DASH-IF push-based content ingest (TS 26.512 V19.3.0 clause 8.3,
# DASH-IF Live Media Ingest specification interface 1 and interface 2, as identified by http://dashif.org/ingest/v1.2) and of the acceptance
# of its Content Hosting Configuration.
# License: 5G-MAG Public License (v1.0)
import asyncio
import logging
import os
import pwd
import tempfile
import socket
import time
import unittest
import http.client
from unittest import mock

from rt_5gms_as.context import Context
from rt_5gms_as.openapi_5g.models.application_server_content_hosting_configuration import ApplicationServerContentHostingConfiguration as CHC
from rt_5gms_as.proxies.openresty import OpenRestyWebProxy
from tests.unit.nginx_rig import HAVE_NGINX, CONFIG, Proxy, chc_dict

M4D = 'as.example.net'
INGEST = 'ingest.example.net'
IF1 = 'http://dashif.org/ingest/v1.2/interface-1'
IF2 = 'http://dashif.org/ingest/v1.2/interface-2'
DEPRECATED = 'urn:3gpp:5gms:content-protocol:dash-if-ingest'


def push_chc(protocol=IF2, base='http://%s/live1/' % INGEST, dist=None):
    '''A Content Hosting Configuration with push-based ingest: the ingest baseURL is set by the 5GMSd AF (clause 8.3, TS 26.510 table 8.8.3.1-1)'''
    chc = chc_dict(1, dist=dist, ingest={'mode': 'PUSH', 'protocol': protocol, 'baseURL': base})
    return chc


@unittest.skipUnless(HAVE_NGINX, 'no openresty binary')
class PushIngest(unittest.TestCase):
    def start(self, chc=None):
        self.proxy = Proxy({'c1': chc or push_chc()})
        self.addCleanup(self.proxy.stop)
        return self.proxy

    def push(self, path, body=b'segment', method='PUT', **kwargs):
        return self.proxy.send(method, INGEST, '/live1/' + path, body, **kwargs)

    def distributed(self, path):
        return self.proxy.get(M4D, '/m4d/ps1/' + path)

    def test_an_object_that_is_posted_or_put_is_distributed_at_m4d_by_the_path_below_the_ingest_baseurl(self):
        # clause 8.3, annex B.2.1: ingest URL .../asset/video1/segment1000.mp4 is M4d URL .../asset/video1/segment1000.mp4
        for protocol in (IF1, IF2, DEPRECATED):
            with self.subTest(protocol=protocol):
                proxy = self.start(push_chc(protocol))
                self.assertEqual(self.distributed('asset/video1/segment1000.mp4')[0], 404)
                self.assertEqual(self.push('asset/video1/segment1000.mp4', b'one', 'POST')[0], 200)
                self.assertEqual(self.push('asset/video1/segment1001.mp4', b'two', 'PUT')[0], 200)
                self.assertEqual(self.distributed('asset/video1/segment1000.mp4')[2], b'one')
                self.assertEqual(self.distributed('asset/video1/segment1001.mp4')[2], b'two')
                proxy.stop()

    def test_the_ingest_baseurl_may_be_posted_to_without_the_closing_slash(self):
        # the publishing point URL of the examples has none (annex B.2.2)
        self.start(push_chc(base='http://%s/live1' % INGEST))
        self.assertEqual(self.proxy.send('POST', INGEST, '/live1', b'')[0], 200)
        self.assertEqual(self.proxy.send('PUT', INGEST, '/live1/x.mpd', b'<MPD/>')[0], 200)
        self.assertEqual(self.distributed('x.mpd')[2], b'<MPD/>')

    def test_an_object_that_is_sent_again_overwrites_the_one_with_the_same_path(self):
        # interface 2, clause 7.1.2
        self.start()
        self.push('a/seg.cmfv', b'old')
        self.push('a/seg.cmfv', b'newer')
        self.assertEqual(self.distributed('a/seg.cmfv')[2], b'newer')

    def test_chunked_transfer_coding_and_a_large_body_are_received_whole(self):
        # clause 5.2: the ingest source uses chunked transfer coding when the length is not known at the start
        self.start()
        self.assertEqual(self.push('c/seg.m4s', method='POST', chunks=[b'chunk-one', b'chunk-two', b'chunk-three'])[0], 200)
        self.assertEqual(self.distributed('c/seg.m4s')[2], b'chunk-onechunk-twochunk-three')
        big = os.urandom(3 * 1024 * 1024)
        self.assertEqual(self.push('c/big.m4s', big)[0], 200)
        self.assertEqual(self.distributed('c/big.m4s')[2], big)
        parts = [os.urandom(100 * 1024) for _ in range(30)]
        self.assertEqual(self.push('c/bigchunked.m4s', chunks=parts)[0], 200)
        self.assertEqual(self.distributed('c/bigchunked.m4s')[2], b''.join(parts))

    def test_an_empty_request_tests_the_publishing_point_and_replaces_nothing(self):
        # clause 6.2: the ingest source starts with a request with the CMAF header "or an empty request"
        self.start()
        self.push('a/init.header', b'header')
        self.assertEqual(self.push('a/init.header', b'')[0], 200)
        self.assertEqual(self.push('', b'', 'POST')[0], 200)
        self.assertEqual(self.distributed('a/init.header')[2], b'header')

    def test_the_status_of_the_failures_is_the_one_of_clause_5_3(self):
        self.start()
        # a request that leaves the allowed path, or a path that is not the ingest baseURL or below it: 403 ("incorrect path", clause 7.1.2)
        self.assertEqual(self.proxy.send('PUT', INGEST, '/live1/../other/seg.m4s', b'x')[0], 403)
        self.assertEqual(self.proxy.send('PUT', INGEST, '/live1/a/%2e%2e/%2e%2e/seg.m4s', b'x')[0], 403)
        self.assertEqual(self.proxy.send('PUT', INGEST, '/other/seg.m4s', b'x')[0], 403)
        # in all other scenarios 400: a method that is not defined, a body with no object name, a file where a directory is needed
        self.assertEqual(self.proxy.get(INGEST, '/live1/a.m4s')[0], 400)
        self.assertEqual(self.proxy.send('PUT', INGEST, '/live1/dir/', b'x')[0], 400)
        self.assertEqual(self.push('f', b'file')[0], 200)
        self.assertEqual(self.push('f/g', b'x')[0], 400)
        self.assertEqual(self.push('h/i', b'x')[0], 200)
        self.assertEqual(self.push('h', b'x')[0], 400)
        self.assertEqual(self.distributed('f')[2], b'file')
        # a host name that has no publishing point
        self.assertEqual(self.proxy.send('PUT', 'unknown.example.net', '/live1/a.m4s', b'x')[0], 404)

    def test_delete_is_interface_2_only_and_removes_the_object_and_its_last_folder(self):
        # clause 7.1.1: the object is deleted with 200, and the folder when its last file went (not recursively)
        self.start(push_chc(IF2))
        self.push('a/b/seg1.cmfv')
        self.push('a/b/seg2.cmfv')
        self.assertEqual(self.push('a/b/seg1.cmfv', method='DELETE')[0], 200)
        self.assertEqual(self.distributed('a/b/seg1.cmfv')[0], 404)
        self.assertEqual(self.distributed('a/b/seg2.cmfv')[0], 200)
        self.assertTrue(os.path.isdir(os.path.join(self.proxy.dir, 'as/ingest/c1/dist-d1/a/b')))
        self.assertEqual(self.push('a/b/seg2.cmfv', method='DELETE')[0], 200)
        self.assertFalse(os.path.exists(os.path.join(self.proxy.dir, 'as/ingest/c1/dist-d1/a/b')))
        self.assertTrue(os.path.isdir(os.path.join(self.proxy.dir, 'as/ingest/c1/dist-d1/a')))

    def test_interface_1_has_no_delete(self):
        self.start(push_chc(IF1))
        self.push('a/seg.m4s')
        self.assertEqual(self.push('a/seg.m4s', method='DELETE')[0], 400)
        self.assertEqual(self.distributed('a/seg.m4s')[0], 200)

    def test_the_deprecated_term_identifier_serves_a_source_of_either_interface(self):
        # implementation choice, text silent, see AS audit O-29: no interface is named for it, so the DELETE of interface 2 works and neither the 415 of
        # interface 2 nor the 412 of interface 1 is answered
        self.start(push_chc(DEPRECATED))
        self.assertEqual(self.push('a/seg.cmfv', headers={'Content-Type': 'audio/mp4'})[0], 200)
        mpd = (b'<MPD><Period><AdaptationSet>'
               b'<SegmentTemplate initialization="$RepresentationID$/init.header" media="$RepresentationID$/seg-$Number$.cmfv"/>'
               b'</AdaptationSet></Period></MPD>')
        self.assertEqual(self.push('live.mpd', mpd)[0], 200)
        self.assertEqual(self.push('v2/seg-1.cmfv')[0], 200)
        self.assertEqual(self.push('a/seg.cmfv', method='DELETE')[0], 200)
        self.assertEqual(self.distributed('a/seg.cmfv')[0], 404)

    def test_the_mime_type_follows_the_extension_of_table_6(self):
        self.start()
        for name, mime in (('a.mpd', 'application/dash+xml'), ('a.cmfv', 'video/mp4'), ('a.cmfa', 'audio/mp4'), ('a.cmft', 'application/mp4'),
                           ('a.m4s', 'video/iso.segment'), ('a.init', 'video/mp4'), ('a.m3u8', 'application/x-mpegURL'), ('a.key', 'application/octet-stream')):
            with self.subTest(name=name):
                self.push(name)
                self.assertEqual(self.distributed(name)[1]['content-type'], mime)

    def test_every_distribution_configuration_serves_the_object_by_its_own_rules(self):
        # TS 26.510 table 8.8.3.1-1: the rules compare the upload path; the mapped path is the path of the distribution URL
        chc = push_chc()
        chc['distributionConfigurations'] = [
            dict(chc['distributionConfigurations'][0], distributionId='d1', pathRewriteRules=[{'requestPathPattern': '^/asset1/', 'mappedPath': '/video/'}]),
            dict(chc['distributionConfigurations'][0], distributionId='d2', baseURL='http://%s/m4d/other/' % M4D)]
        self.start(chc)
        self.assertEqual(self.push('asset1/seg.m4s', b'one')[0], 200)
        self.assertEqual(self.push('asset2/seg.m4s', b'two')[0], 200)
        self.assertEqual(self.distributed('video/seg.m4s')[2], b'one')
        self.assertEqual(self.distributed('asset2/seg.m4s')[2], b'two')
        self.assertEqual(self.distributed('asset1/seg.m4s')[0], 404)
        self.assertEqual(self.proxy.get(M4D, '/m4d/other/asset1/seg.m4s')[2], b'one')
        self.assertEqual(self.proxy.get(M4D, '/m4d/other/video/seg.m4s')[0], 404)
        self.assertEqual(self.push('asset1/seg.m4s', method='DELETE')[0], 200)
        self.assertEqual(self.distributed('video/seg.m4s')[0], 404)
        self.assertEqual(self.proxy.get(M4D, '/m4d/other/asset1/seg.m4s')[0], 404)

    def test_the_caching_configuration_sets_the_directives_of_the_response(self):
        # clause 7.6.4.2, TS 26.510 table 8.8.3.1-1: noCache marks the object, maxAge is the time-to-live counted from the ingest
        chc = push_chc(dist={'cachingConfigurations': [
            {'urlPatternFilter': r'\.mpd$', 'cachingDirectives': {'noCache': True}},
            {'urlPatternFilter': r'\.m4s$', 'cachingDirectives': {'maxAge': 100}}]})
        self.start(chc)
        for name in ('a.mpd', 'a.m4s', 'a.cmfv'):
            self.push(name)
        self.assertEqual(self.distributed('a.mpd')[1]['cache-control'], 'no-cache, no-store, max-age=0')
        first = int(self.distributed('a.m4s')[1]['cache-control'].split('=')[1])
        self.assertIn(first, (99, 100))
        time.sleep(2.1)
        self.assertLessEqual(int(self.distributed('a.m4s')[1]['cache-control'].split('=')[1]), first - 2)
        self.assertNotIn('cache-control', self.distributed('a.cmfv')[1])

    def test_the_access_rules_of_the_distribution_apply_to_the_pushed_objects(self):
        # clause 7.6.4.5: no token, no object
        chc = push_chc(dist={'urlSignature': {'urlPattern': r'.*\.m4s', 'tokenName': 'token', 'passphraseName': 'pass', 'passphrase': 'secret1',
                                              'tokenExpiryName': 'exp', 'useIPAddress': False}})
        self.start(chc)
        self.push('a.m4s')
        self.assertEqual(self.distributed('a.m4s')[0], 403)

    def test_a_distribution_in_a_directory_is_not_an_object(self):
        self.start()
        self.push('a/seg.m4s')
        self.assertEqual(self.distributed('a/')[0], 404)
        self.assertEqual(self.distributed('a')[0], 404)

    def test_the_ingest_endpoint_may_share_the_host_name_of_the_distribution(self):
        # annex B.2.1 uses two host names, the text does not forbid one
        self.start(push_chc(base='http://%s/live1/' % M4D))
        self.assertEqual(self.proxy.send('PUT', M4D, '/live1/a/seg.m4s', b'shared')[0], 200)
        self.assertEqual(self.distributed('a/seg.m4s')[2], b'shared')
        self.assertEqual(self.proxy.get(M4D, '/live1/a/seg.m4s')[0], 400)

    def test_a_configuration_with_the_ingest_baseurl_of_another_is_not_realised(self):
        # two endpoints cannot be at one URL (as two service locations cannot): the proxy refuses it, so the M3 operation is a 500 and the
        # configuration is not kept
        proxy = Proxy({'c1': push_chc()})
        self.addCleanup(proxy.stop)
        other = push_chc()
        other['distributionConfigurations'][0]['baseURL'] = 'http://%s/m4d/ps2/' % M4D
        proxy.context.addContentHostingConfiguration('c2', CHC.model_validate(other))
        with self.assertRaises(RuntimeError):
            asyncio.run(OpenRestyWebProxy(proxy.context).writeConfiguration())

    def test_interface_2_answers_415_for_a_mime_type_that_table_6_does_not_permit_for_the_extension(self):
        # clause 5.3, point 5b, and table 6 of clause 7.1.2
        self.start(push_chc(IF2))
        self.assertEqual(self.push('a/seg.cmfv', headers={'Content-Type': 'audio/mp4'})[0], 415)
        self.assertEqual(self.push('a/seg.m4s', headers={'Content-Type': 'video/mp4'})[0], 415)
        self.assertEqual(self.distributed('a/seg.cmfv')[0], 404)
        self.assertEqual(self.push('a/seg.cmfv', headers={'Content-Type': 'video/mp4'})[0], 200)
        self.assertEqual(self.push('a/x.mp4', headers={'Content-Type': 'application/mp4'})[0], 200)
        self.assertEqual(self.push('a/x.mpd', headers={'Content-Type': 'Application/DASH+XML; charset=utf-8'})[0], 200)
        # an extension that the table does not list, and a request without a MIME type, are not judged
        self.assertEqual(self.push('a/x.ts', headers={'Content-Type': 'video/mp2t'})[0], 200)
        self.assertEqual(self.push('a/y.cmfv')[0], 200)

    def test_interface_1_has_no_table_of_mime_types(self):
        self.start(push_chc(IF1))
        self.assertEqual(self.push('a/seg.cmfv', headers={'Content-Type': 'audio/mp4'})[0], 200)

    def test_interface_1_answers_412_for_a_segment_whose_initialization_fragment_is_missing(self):
        # clause 5.3, point 5c, and clause 6.2: the manifest (SegmentTemplate with $RepresentationID$) names the initialization fragment of a track
        self.start(push_chc(IF1))
        mpd = (b'<MPD><Period><AdaptationSet>'
               b'<SegmentTemplate initialization="$RepresentationID$/init.header" media="$RepresentationID$/seg-$Number$.cmfv"/>'
               b'</AdaptationSet></Period></MPD>')
        # no manifest yet: nothing says which fragment is the initialization one
        self.assertEqual(self.push('v1/seg-1.cmfv')[0], 200)
        self.assertEqual(self.push('live.mpd', mpd)[0], 200)
        self.assertEqual(self.push('v2/seg-1.cmfv')[0], 412)
        self.assertEqual(self.distributed('v2/seg-1.cmfv')[0], 404)
        self.assertEqual(self.push('v2/init.header', b'header')[0], 200)
        self.assertEqual(self.push('v2/seg-1.cmfv')[0], 200)
        self.assertEqual(self.push('v2/seg-2.cmfv')[0], 200)
        # another track has its own initialization fragment
        self.assertEqual(self.push('v3/seg-7.cmfv')[0], 412)
        # a manifest whose templates are not the same in each AdaptationSet does not follow clause 6.2 and says nothing
        self.assertEqual(self.push('other.mpd', mpd.replace(b'</AdaptationSet>', b'</AdaptationSet><AdaptationSet><SegmentTemplate initialization="x/$RepresentationID$/i" media="x/$RepresentationID$/s-$Number$"/></AdaptationSet>'))[0], 200)

    def test_the_initialization_fragment_is_looked_for_where_the_distribution_has_it(self):
        chc = push_chc(IF1, dist={'pathRewriteRules': [{'requestPathPattern': '^/v1/', 'mappedPath': '/video/'}]})
        self.start(chc)
        mpd = b'<MPD><SegmentTemplate initialization="$RepresentationID$/init.header" media="$RepresentationID$/seg-$Number$.cmfv"/></MPD>'
        self.push('live.mpd', mpd)
        self.assertEqual(self.push('v1/seg-1.cmfv')[0], 412)
        self.assertEqual(self.push('v1/init.header')[0], 200)
        self.assertEqual(self.distributed('video/init.header')[0], 200)
        self.assertEqual(self.push('v1/seg-1.cmfv')[0], 200)

    def test_the_ingest_endpoint_answers_on_ipv6_and_keeps_the_connection(self):
        # DASH-IF clause 5.2: IPv4 and IPv6 transport, persistent connections
        self.start()
        conn = http.client.HTTPConnection('::1', self.proxy.port, timeout=10)
        sock = None
        for n in range(3):
            conn.request('PUT', '/live1/a/%d.m4s' % n, body=b'six', headers={'Host': INGEST})
            sock = sock or conn.sock
            response = conn.getresponse()
            response.read()
            self.assertEqual(response.status, 200)
            # the client reconnects by itself if the server closed the connection: the same socket proves that it did not
            self.assertIs(conn.sock, sock)
        local = conn.sock.getsockname()
        conn.close()
        self.assertEqual(self.distributed('a/2.m4s')[2], b'six')
        self.assertIn(':', local[0])

    def test_the_cors_headers_of_the_domain_name_alias_are_set_for_pushed_objects(self):
        self.start(push_chc(dist={'domainNameAlias': 'alias.example.net'}))
        self.push('a/seg.m4s')
        status, headers, _ = self.proxy.get('alias.example.net', '/m4d/ps1/a/seg.m4s', headers={'Origin': 'http://alias.example.net'})
        self.assertEqual(status, 200)
        self.assertEqual(headers['access-control-allow-origin'], 'http://alias.example.net')

    def test_a_distribution_of_mode_push_sends_every_object_to_the_downstream_as(self):
        # TS 26.510 clause 5.2.8.2 and table 8.8.3.1-1, TS 26.512 clauses 4.10A and 8.3: the downstream AS is provisioned first, its ingest baseURL is
        # the baseURL of the distribution of the upstream one
        down = Proxy({'d1': push_chc(IF1, base='http://127.0.0.1/down/')})
        self.addCleanup(down.stop)
        up_chc = push_chc(IF1, dist={'pathRewriteRules': [{'requestPathPattern': '^/asset1/', 'mappedPath': '/mapped/'}]})
        up_chc['distributionConfigurations'] = [
            {'distributionId': 'to-down', 'mode': 'PUSH', 'baseURL': 'http://127.0.0.1:%d/down/' % down.port,
             'pathRewriteRules': [{'requestPathPattern': '^/asset1/', 'mappedPath': '/mapped/'}]},
            up_chc['distributionConfigurations'][0]]
        self.start(up_chc)
        self.assertEqual(self.push('asset1/seg1.cmfv', b'upstream-object')[0], 200)
        self.assertEqual(self.push('asset2/seg2.cmfv', b'second', 'POST', chunks=[b'sec', b'ond'])[0], 200)
        deadline = time.time() + 10
        got = []
        while time.time() < deadline and len(got) < 2:
            got = [down.get(M4D, '/m4d/ps1/' + path) for path in ('mapped/seg1.cmfv', 'asset2/seg2.cmfv')]
            got = [g for g in got if g[0] == 200]
            time.sleep(0.1)
        self.assertEqual([g[2] for g in got], [b'upstream-object', b'second'])
        # the upstream AS keeps serving its own distribution
        self.assertEqual(self.distributed('mapped/seg1.cmfv')[2], b'upstream-object')

    def test_a_downstream_as_that_is_not_there_does_not_stop_the_ingest(self):
        from tests.unit.nginx_rig import free_port
        chc = push_chc(IF1)
        chc['distributionConfigurations'].append({'distributionId': 'to-down', 'mode': 'PUSH', 'baseURL': 'http://127.0.0.1:%d/down/' % free_port()})
        self.start(chc)
        self.assertEqual(self.push('a/seg.cmfv', b'x')[0], 200)
        self.assertEqual(self.distributed('a/seg.cmfv')[2], b'x')

    def test_a_deactivated_configuration_has_no_publishing_point(self):
        # clause 4.5.4.7: it is not served; the ingest baseURL is not available (DASH-IF clause 5.3, point 5a: 404)
        self.start()
        self.push('a/seg.m4s')
        self.proxy.stop()
        self.proxy = Proxy({'c1': push_chc()}, deactivated=['c1'])
        self.addCleanup(self.proxy.stop)
        self.assertEqual(self.push('a/seg.m4s')[0], 404)
        self.assertEqual(self.distributed('a/seg.m4s')[0], 404)


class PushIngestConfiguration(unittest.TestCase):
    '''What the M3 side accepts for mode PUSH (TS 26.512 V19.3.0 clause 8.3)'''
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        config = os.path.join(self.dir.name, 'as.conf')
        with open(config, 'w') as fh:
            fh.write(CONFIG.format(d=self.dir.name, port=18080, extra=''))
        self.ctx = Context(config)
        self.ctx.setAppLog(logging.getLogger('test'))

    def add(self, name='c1', **ingest):
        chc = chc_dict(1, ingest=dict({'mode': 'PUSH', 'protocol': IF1, 'baseURL': 'http://ingest.example.net/live1/'}, **ingest))
        return self.ctx.addContentHostingConfiguration(name, CHC.model_validate(chc))

    def test_the_three_term_identifiers_with_mode_push_are_accepted(self):
        for n, protocol in enumerate((IF1, IF2, DEPRECATED)):
            self.add('c%d' % n, protocol=protocol, baseURL='http://ingest.example.net/live%d/' % n)
            self.assertTrue(self.ctx.haveContentHostingConfiguration('c%d' % n))

    def test_the_mode_has_to_go_with_the_protocol(self):
        with self.assertRaises(Context.ValueError):
            self.add(mode='PULL')
        with self.assertRaises(Context.ValueError):
            self.add(protocol='urn:3gpp:5gms:content-protocol:http-pull')

    def test_the_ingest_baseurl_is_needed(self):
        chc = chc_dict(1, ingest={'mode': 'PUSH', 'protocol': IF1})
        del chc['ingestConfiguration']['baseURL']
        with self.assertRaises(Context.ValueError):
            self.ctx.addContentHostingConfiguration('c1', CHC.model_validate(chc))

    def test_what_the_text_gives_no_way_to_realise_is_refused(self):
        # no text names the Server Certificate for the ingest host, and none says what a query of the ingest baseURL means
        with self.assertRaises(Context.ConfigError):
            self.add(baseURL='https://ingest.example.net/live1/')
        with self.assertRaises(Context.ConfigError):
            self.add(baseURL='http://ingest.example.net/live1/?a=b')

    def test_the_path_of_the_ingest_baseurl_has_plain_segments(self):
        for path in ('/a//b/', '/a%3Bb/', '/a%22b/'):
            with self.subTest(path=path), self.assertRaises(Context.ValueError):
                self.add(baseURL='http://ingest.example.net' + path)

    def test_a_distribution_of_mode_push_goes_with_a_push_ingest_and_names_the_downstream_ingest_baseurl(self):
        def add(ingest=None, **dist):
            chc = chc_dict(1, ingest=ingest)
            chc['distributionConfigurations'] = [dict({'distributionId': 'p', 'mode': 'PUSH', 'baseURL': 'http://down.example.net/in/'}, **dist)]
            return self.ctx.addContentHostingConfiguration('c1', CHC.model_validate(chc))
        push = {'mode': 'PUSH', 'protocol': IF1, 'baseURL': 'http://ingest.example.net/live1/'}
        add(push)
        self.ctx.deleteContentHostingConfiguration('c1')
        with self.assertRaises(Context.ValueError):
            add()
        with self.assertRaises(Context.ValueError):
            add(push, canonicalDomainName='as.example.net')
        with self.assertRaises(Context.ValueError):
            add(push, domainNameAlias='alias.example.net')
        with self.assertRaises(Context.ValueError):
            add(push, baseURL='http://down.example.net/in/?a=b')
        with self.assertRaises(Context.ConfigError):
            add(push, baseURL='https://down.example.net/in/')

    @unittest.skipUnless(HAVE_NGINX, 'no openresty binary')
    def test_an_as_that_runs_as_root_gives_the_ingest_directories_to_the_user_of_the_nginx_workers(self):
        # the workers create the directories of the objects below them: whatever user the AS runs as, they have to be able to
        try:
            nobody = pwd.getpwnam('nobody')
        except KeyError:
            self.skipTest('no user nobody')
        self.add()
        proxy = OpenRestyWebProxy(self.ctx)
        with mock.patch('os.geteuid', return_value=0), mock.patch('os.chown') as chown:
            asyncio.run(proxy.writeConfiguration())
        owned = {call.args[0] for call in chown.call_args_list if call.args[1] == nobody.pw_uid}
        ingest = self.ctx.ingestDir()
        self.assertTrue({ingest, os.path.join(ingest, 'c1'), os.path.join(ingest, 'c1', 'dist-d1')} <= owned, owned)
        with open(os.path.join(self.dir.name, 'nginx.conf')) as fh:
            self.assertRegex(fh.read(), r'(?m)^user nobody \S+;$')

    @unittest.skipUnless(HAVE_NGINX, 'no openresty binary')
    def test_an_as_that_does_not_run_as_root_leaves_the_user_of_the_workers_alone(self):
        self.add()
        with mock.patch('os.chown') as chown:
            asyncio.run(OpenRestyWebProxy(self.ctx).writeConfiguration())
        chown.assert_not_called()
        with open(os.path.join(self.dir.name, 'nginx.conf')) as fh:
            self.assertNotRegex(fh.read(), r'(?m)^user ')

    def test_the_objects_are_removed_with_the_configuration(self):
        directory = os.path.join(self.ctx.ingestDir(), 'c1')
        os.makedirs(os.path.join(directory, 'dist-d1', 'a'))
        with open(os.path.join(directory, 'dist-d1', 'a', 'x.m4s'), 'wb') as fh:
            fh.write(b'x')
        asyncio.run(OpenRestyWebProxy(self.ctx).purgeAll('c1'))
        self.assertFalse(os.path.exists(directory))


if __name__ == '__main__':
    unittest.main()
