# Tests of the readings that the AS takes where the text is silent or two texts conflict (AS audit, open items O-1 to O-8, O-25, O-30 to O-32).
# Each reading is the most conservative one: it keeps working with the other entities and does the least beyond the text. Private nginx and mock origin
# as in nginx_rig.py: ports chosen by the system, nothing of a running AS or nginx is touched.
# License: 5G-MAG Public License (v1.0)
import asyncio
import base64
import hashlib
import logging
import os
import tempfile
import time
import types
import unittest

from rt_5gms_as.app import M3_PATH_PREFIX
from rt_5gms_as.context import Context
from rt_5gms_as.exceptions import NoProblemException, ProblemException
from rt_5gms_as.openapi_5g.models.application_server_content_hosting_configuration import ApplicationServerContentHostingConfiguration as CHC
from rt_5gms_as.proxies.openresty import OpenRestyWebProxy
from rt_5gms_as.server import M3Server

from tests.unit.nginx_rig import HAVE_NGINX, Origin, Proxy, chc_dict
from tests.unit.test_context import CONFIG
from tests.unit.test_reload_failure import FakeProxy

HOST = 'as.example.net'


def caching(max_age=None, no_cache=False, codes=None, pattern='.*'):
    directives = {'noCache': no_cache}
    if max_age is not None:
        directives['maxAge'] = max_age
    if codes is not None:
        directives['statusCodeFilters'] = codes
    return {'cachingConfigurations': [{'urlPatternFilter': pattern, 'cachingDirectives': directives}]}


@unittest.skipUnless(HAVE_NGINX, 'no openresty binary')
class AtM4d(unittest.TestCase):
    def setUp(self):
        self.origin = Origin()
        self.proxy = None
        self.addCleanup(self.origin.stop)

    def start(self, **kwargs):
        self.proxy = Proxy({'c1': chc_dict(self.origin.port, **kwargs)})
        self.addCleanup(self.proxy.stop)
        return self.proxy

    def test_the_directives_of_the_origin_reach_the_media_player_for_the_status_codes_that_the_filter_does_not_list(self):
        # implementation choice, text silent how (7.6.4.2: "for the others the directives of the origin apply"), see AS audit O-2: the rule is for 404,
        # a 200 keeps the Cache-Control and Expires of the origin, which the location hides to be able to override them for the codes of the filter
        self.origin.routes = {'/media/seg.m4s': self.origin.answer(200, {'Cache-Control': 'max-age=3600', 'Expires': 'Thu, 01 Dec 2050 16:00:00 GMT'}, b'x'),
                              '/media/gone.m4s': self.origin.answer(404, {'Cache-Control': 'max-age=1'}, b'')}
        self.start(dist=caching(60, codes=[404]))
        status, headers, _ = self.proxy.get(HOST, '/m4d/ps1/seg.m4s')
        self.assertEqual(status, 200)
        self.assertEqual(headers.get('cache-control'), 'max-age=3600')
        self.assertEqual(headers.get('expires'), 'Thu, 01 Dec 2050 16:00:00 GMT')
        # the rule applies to the code of the filter: the max-age of the rule, not the one of the origin
        status, headers, _ = self.proxy.get(HOST, '/m4d/ps1/gone.m4s')
        self.assertEqual(status, 404)
        self.assertEqual(headers.get('cache-control'), 'max-age=60')

    def test_a_rule_without_a_filter_still_hides_the_directives_of_the_origin(self):
        self.origin.routes = {'/media/seg.m4s': self.origin.answer(200, {'Cache-Control': 'max-age=3600'}, b'x')}
        self.start(dist=caching(60))
        self.assertEqual(self.proxy.get(HOST, '/m4d/ps1/seg.m4s')[1].get('cache-control'), 'max-age=60')

    def test_the_no_cache_rule_for_some_codes_leaves_the_directives_of_the_origin_for_the_others(self):
        self.origin.routes = {'/media/seg.m4s': self.origin.answer(200, {'Cache-Control': 'max-age=3600'}, b'x'),
                              '/media/gone.m4s': self.origin.answer(404, {'Cache-Control': 'max-age=3600'}, b'')}
        self.start(dist=caching(no_cache=True, codes=[404]))
        self.assertEqual(self.proxy.get(HOST, '/m4d/ps1/seg.m4s')[1].get('cache-control'), 'max-age=3600')
        self.assertEqual(self.proxy.get(HOST, '/m4d/ps1/gone.m4s')[1].get('cache-control'), 'no-cache, no-store, max-age=0')

    def test_the_default_directives_cache_a_200_and_other_codes_for_a_while(self):
        # implementation choice, text silent (7.6.4.2 delegates the default directives to 8.8.3.1, which has none), see CR-038 and AS audit O-3: nothing
        # applies, so the AS keeps a 200 for a day and any other status for a minute (nginx.conf.tmpl); the origin is not asked again within that time
        self.origin.routes = {'/media/seg.m4s': self.origin.answer(200, {}, b'x')}
        self.start()
        for _ in range(2):
            self.assertEqual(self.proxy.get(HOST, '/m4d/ps1/seg.m4s')[0], 200)
            self.assertEqual(self.proxy.get(HOST, '/m4d/ps1/missing.m4s')[0], 404)
        self.assertEqual(sorted(entry[1] for entry in self.origin.log), ['/media/missing.m4s', '/media/seg.m4s'])

    def test_the_origin_directives_apply_when_no_rule_matches(self):
        # 7.6.4.2: no caching configuration matches, so the directives received from the ingest source apply (max-age=1: fetched again after 2 seconds)
        self.origin.routes = {'/media/seg.m4s': self.origin.answer(200, {'Cache-Control': 'max-age=1'}, b'x')}
        self.start()
        self.proxy.get(HOST, '/m4d/ps1/seg.m4s')
        self.proxy.get(HOST, '/m4d/ps1/seg.m4s')
        self.assertEqual(len(self.origin.log), 1)
        time.sleep(2.2)
        self.proxy.get(HOST, '/m4d/ps1/seg.m4s')
        self.assertEqual(len(self.origin.log), 2)

    def test_the_header_fields_of_the_origin_are_not_forwarded_except_those_of_the_representation_and_the_caching_directives(self):
        # implementation choice, two texts conflict (26.512 7.6.4.2 names a flag originCacheHeaders that 26.510 8.8.3.1 and the YAML lack), see CR-038
        # and AS audit O-4: the flag cannot be set, so the default stands (no origin field is forwarded), kept to what an HTTP response needs to be
        # understood: the fields of the representation and the caching directives
        headers = {'Content-Type': 'video/mp4', 'Cache-Control': 'max-age=3600', 'ETag': '"a"', 'Last-Modified': 'Thu, 01 Dec 2050 16:00:00 GMT',
                   'X-Secret': 'origin-only', 'Set-Cookie': 'sid=1', 'Via': '1.1 origin'}
        self.origin.routes = {'/media/seg.m4s': self.origin.answer(200, headers, b'x')}
        self.start()
        _, got, _ = self.proxy.get(HOST, '/m4d/ps1/seg.m4s')
        for name in ('x-secret', 'set-cookie', 'via'):
            self.assertNotIn(name, got)
        self.assertEqual(got['content-type'], 'video/mp4')
        self.assertEqual(got['etag'], '"a"')
        self.assertEqual(got['cache-control'], 'max-age=3600')

    def test_the_proof_of_a_url_signature_goes_to_the_origin_with_the_rest_of_the_query(self):
        # implementation choice, text silent, see AS audit O-1: 8.2 replaces the prefix of the request URL and forwards the rest, and nothing says that
        # the token and its expiry (7.6.4.5) are dropped; the origin generated the token, so it is no secret from it
        self.origin.routes = {'/media/seg.m4s': self.origin.answer(200, {}, b'x')}
        signature = {'urlPattern': '.*\\.m4s', 'tokenName': 'tok', 'passphraseName': 'pp', 'passphrase': 'secret1', 'tokenExpiryName': 'exp', 'useIPAddress': False}
        self.start(dist={'urlSignature': signature})
        expiry = str(int(time.time()) + 100)
        url = 'http://%s/m4d/ps1/seg.m4s' % HOST
        token = base64.urlsafe_b64encode(hashlib.sha512(('%s&exp=%s&pp=secret1' % (url, expiry)).encode()).digest()).decode().rstrip('=')
        self.assertEqual(self.proxy.get(HOST, '/m4d/ps1/seg.m4s?v=1&exp=%s&tok=%s' % (expiry, token))[0], 200)
        self.assertEqual([entry[1] for entry in self.origin.log], ['/media/seg.m4s?v=1&exp=%s&tok=%s' % (expiry, token)])

    def test_the_scheme_and_the_port_of_the_distribution_base_url_are_not_used_to_serve(self):
        # implementation choice, text silent (6.0.2.2 names the default ports only), see AS audit O-21: a mismatch is no reason to refuse a configuration
        # that is served at the ports of the AS; the AF finds out by the Service Access Information
        self.origin.routes = {'/media/seg.m4s': self.origin.answer(200, {}, b'x')}
        self.start(dist={'baseURL': 'https://%s:9443/m4d/ps1/' % HOST})
        self.assertEqual(self.proxy.get(HOST, '/m4d/ps1/seg.m4s')[0], 200)


class M3Reading(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        config = os.path.join(self.dir.name, 'as.conf')
        with open(config, 'w') as fh:
            fh.write(CONFIG.format(d=self.dir.name))
        self.ctx = Context(config)
        self.ctx.setAppLog(logging.getLogger('test'))
        self.ctx.setWebProxy(FakeProxy([True]))
        self.server = M3Server()
        self.server.setContext(self.ctx)

    @staticmethod
    def chc(base, ingest_base):
        return CHC.model_validate({
            'name': 't', 'externalServiceId': 'com.example.t',
            'ingestConfiguration': {'mode': 'PULL', 'protocol': 'urn:3gpp:5gms:content-protocol:http-pull', 'baseURL': ingest_base},
            'distributionConfigurations': [{'distributionId': 'd1', 'canonicalDomainName': 'as.example.net', 'baseURL': base}]})

    def request(self, method, path):
        async def body():
            return b''
        return types.SimpleNamespace(method=method, url=types.SimpleNamespace(path=M3_PATH_PREFIX + path), headers={}, body=body)

    def outcome(self, call, *args, **kwargs):
        try:
            return 200, asyncio.run(call(*args, **kwargs))
        except (ProblemException, NoProblemException) as err:
            return err.status_code, None

    def test_the_destroy_of_a_configuration_that_another_one_ingests_from_is_204(self):
        # implementation choice, two texts conflict (table 9.4.2-1 says destroying a referenced configuration is an error, clause 4.5.4.6 lists no such
        # error and the YAML agrees), see CR-040 and AS audit O-7: the clause of the operation wins
        self.ctx.addContentHostingConfiguration('upstream', self.chc('http://as.example.net/m4d/up/', 'http://origin.example.net/media/'))
        self.ctx.addContentHostingConfiguration('downstream', self.chc('http://as.example.net/m4d/down/', 'http://as.example.net/m4d/up/'))
        status, _ = self.outcome(self.server.destroy_content_hosting_configuration, 'upstream', request=self.request('DELETE', '/content-hosting-configurations/upstream'))
        self.assertEqual(status, 204)
        self.assertFalse(self.ctx.haveContentHostingConfiguration('upstream'))
        self.assertTrue(self.ctx.haveContentHostingConfiguration('downstream'))

    def test_a_geofencing_locator_type_that_the_as_cannot_evaluate_is_refused(self):
        # implementation choice, text silent, see AS audit O-16: the tracking area code needs the tracking area of the UE (known to the core only), so
        # the configuration is refused and not kept with a fence that would let every UE in
        chc = self.chc('http://as.example.net/m4d/a/', 'http://origin.example.net/media/').model_dump(by_alias=True, exclude_none=True, mode='json')
        for locator_type, locators in (('urn:3gpp:media-delivery:locator-type:trackingAreaCode', ['tac-lb00.tac-hb00.tac.epc.mnc001.mcc001.3gppnetwork.org']),
                                       ('urn:example:vendor-locator', ['x'])):
            with self.subTest(locator_type=locator_type):
                chc['distributionConfigurations'][0]['geoFencing'] = {'locatorType': locator_type, 'locators': locators}
                with self.assertRaises(Context.ValueError):
                    self.ctx.addContentHostingConfiguration('c1', CHC.model_validate(chc))
                self.assertFalse(self.ctx.haveContentHostingConfiguration('c1'))

    def test_a_created_configuration_is_active_until_the_af_deactivates_it(self):
        # implementation choice, text silent (4.5.4.7 does not give the state of a created resource), see AS audit O-8: active, as a 5GMSd AF that never
        # calls the (de)activation expects the content to be served
        self.ctx.addContentHostingConfiguration('c1', self.chc('http://as.example.net/m4d/a/', 'http://origin.example.net/media/'))
        self.assertTrue(self.ctx.isContentHostingConfigurationActive('c1'))
        # and a replacement keeps the state that the AF set
        self.ctx.setContentHostingConfigurationActive('c1', False)
        self.ctx.updateContentHostingConfiguration('c1', self.chc('http://as.example.net/m4d/a/', 'http://origin.example.net/media2/'))
        self.assertFalse(self.ctx.isContentHostingConfigurationActive('c1'))


if __name__ == '__main__':
    unittest.main()
