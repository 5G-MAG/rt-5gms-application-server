# Unit tests of the AS as a client of the AF at reference point M3d: Service Access Information and CMCD reports.
# TS 26.512 V19.3.0 clauses 4.5.6, 4.5.9, 6.6.5, 10.5.1 and 11.4; TS 26.510 V19.2.0 clauses 5.3.2.3, 5.3.5, 7.4.3 and 9.5.
# License: 5G-MAG Public License (v1.0)
import asyncio
import base64
import json
import logging
import os
import subprocess
import tempfile
import types
import unittest

import httpx

from rt_5gms_as import m3d
from rt_5gms_as.context import Context
from rt_5gms_as.openapi_5g.models.application_server_content_hosting_configuration import ApplicationServerContentHostingConfiguration as CHC
from rt_5gms_as.proxies.openresty import OpenRestyWebProxy

from tests.unit.test_ingest_base_query import CONFIG, OPENRESTY, chc_dict

CMCD = 'urn:3gpp:5gms:metrics:common-media-client-data:'
TERM = 'urn:3gpp:5gms:event-exposure:common-media-client-data#'


class FakeContext(object):
    def __init__(self, options=None):
        self.options = options or {}
        self.chcs = {'chc-1': types.SimpleNamespace(external_service_id='ext-1')}

    def getProvisioningSessionIds(self):
        return self.chcs.keys()

    def findContentHostingConfigurationByProvisioningSession(self, psid):
        return self.chcs.get(psid)

    def getConfigVar(self, section, name, default=None):
        return self.options.get((section, name), default)

    def appLog(self):
        return logging.getLogger('test')


def config_entry(cls='session', **extra):
    entry = {'metricsReportingConfigurationId': 'mrc-' + cls, 'serverAddresses': ['http://af.example:7777/3gpp-maf-session-handling/v1/'],
             'scheme': CMCD + cls, 'samplePercentage': 100.0, 'samplingPeriod': 0}
    entry.update(extra)
    return entry


def sai(*entries, psid='ps-1'):
    return {'provisioningSessionId': psid, 'provisioningSessionType': 'MS_DOWNLINK', 'locationReporting': False,
            'clientMetricsReportingConfigurations': list(entries)}


class FakeAf(object):
    '''The AF at M3d: the requests it got, and the answers'''
    def __init__(self, sai_body=None, report_status=200):
        self.requests = []
        self.sai_body = sai_body
        self.report_status = report_status
        self.token_status = 200
        self.sai_status = 200
        self.sai_headers = {'ETag': '"v1"', 'Cache-Control': 'max-age=30'}
        self.valid_token = 'tok-1'

    def handler(self, request):
        self.requests.append(request)
        if request.url.path == '/oauth2/token':
            return httpx.Response(self.token_status, json={'access_token': self.valid_token, 'token_type': 'Bearer', 'expires_in': 3600})
        if request.headers.get('authorization', 'Bearer ' + self.valid_token) != 'Bearer ' + self.valid_token:
            return httpx.Response(401)
        if request.method == 'GET':
            if self.sai_status != 200:
                return httpx.Response(self.sai_status)
            if request.headers.get('if-none-match') == '"v1"':
                return httpx.Response(304, headers=self.sai_headers)
            return httpx.Response(self.sai_status, json=self.sai_body, headers=self.sai_headers)
        return httpx.Response(self.report_status, headers={'Retry-After': '7'} if self.report_status == 503 else {})

    def transport(self):
        return httpx.MockTransport(self.handler)

    def reports(self):
        return [r for r in self.requests if r.method == 'POST' and r.url.path != '/oauth2/token']


class Clock(object):
    def __init__(self, now=1700000000.0):
        self.now = now

    def __call__(self):
        return self.now


def run(coro):
    return asyncio.run(coro)


class Cmcd(unittest.TestCase):
    def test_a_payload_is_split_in_keys_with_values_strings_and_flags(self):
        # CTA-5004 clause 3.1: key=value pairs, strings in quotation marks, a key without a value is a flag
        pairs = m3d.parse_cmcd('bl=21300,br=3200,nor="..%2Fseg%2C2.m4s",ot=v,su,sid="6e2fb550-c457-11e9-bb97-0800200c9a66"')
        self.assertEqual(pairs, {'bl': '21300', 'br': '3200', 'nor': '..%2Fseg%2C2.m4s', 'ot': 'v', 'su': True,
                                 'sid': '6e2fb550-c457-11e9-bb97-0800200c9a66'})
        self.assertEqual(m3d.parse_cmcd('sid="a,b"'), {'sid': 'a,b'})
        self.assertEqual(m3d.parse_cmcd(''), {})

    def test_the_query_argument_and_the_request_headers_are_read(self):
        pairs = m3d.collect_cmcd('x=1&CMCD=br%3D3200%2Cot%3Dv', {'cmcd-session': 'sid="s1"', 'CMCD-Request': 'su,mtp=25400', 'Other': 'a=b'})
        self.assertEqual(pairs, {'br': '3200', 'ot': 'v', 'sid': 's1', 'su': True, 'mtp': '25400'})

    def test_each_class_has_its_keys_with_their_types_in_alphabetical_order(self):
        pairs = m3d.collect_cmcd(None, {'CMCD-Session': 'sid="s1",pr=1.5,st=v,sf=d,v=1,cid="c1"', 'CMCD-Object': 'tb=5000,br=3200,ot=v,d=4004',
                                        'CMCD-Request': 'su,mtp=25400,dl=18000,bl=21300,nor="seg2.m4s",nrr="0-1000"',
                                        'CMCD-Status': 'rtp=15000,bs'})
        self.assertEqual(list(m3d.client_data('session', pairs, None).items()),
                         [('cid', 'c1'), ('pr', 1.5), ('sf', 'd'), ('sid', 's1'), ('st', 'v'), ('v', 1)])
        self.assertEqual(m3d.client_data('object', pairs, None), {'br': 3200, 'd': 4004, 'ot': 'v', 'tb': 5000})
        self.assertEqual(m3d.client_data('request', pairs, None), {'bl': 21300, 'dl': 18000, 'mtp': 25400, 'nor': 'seg2.m4s', 'nrr': '0-1000', 'su': True})
        self.assertEqual(m3d.client_data('status', pairs, None), {'bs': True, 'rtp': 15000})

    def test_a_value_of_the_wrong_type_is_left_out(self):
        # the AF refuses a report that does not follow the OpenAPI definition, so a malformed value is not reported
        pairs = {'sid': 's1', 'st': 'x', 'pr': 'fast', 'br': '3200.5', 'ot': 'zz', 'nor': 'a b', 'su': 'maybe'}
        self.assertEqual(m3d.client_data('session', pairs, None), {'sid': 's1'})
        self.assertIsNone(m3d.client_data('object', pairs, None))
        self.assertIsNone(m3d.client_data('request', pairs, None))

    def test_the_listed_metrics_limit_the_keys_but_the_session_always_has_its_identifier(self):
        pairs = {'sid': 's1', 'cid': 'c1', 'pr': '1', 'br': '3200', 'd': '4004'}
        self.assertEqual(m3d.client_data('session', pairs, {'pr'}), {'pr': 1, 'sid': 's1'})
        self.assertEqual(m3d.client_data('object', pairs, {'br'}), {'br': 3200})
        self.assertIsNone(m3d.client_data('session', {'pr': '1'}, None))


class ServiceAccessInformation(unittest.TestCase):
    def test_only_the_cmcd_schemes_are_taken(self):
        body = sai(config_entry('object'), config_entry('status'), dict(config_entry(), scheme='urn:3GPP:ns:PSS:DASH:QM10'))
        psid, configs = m3d.parse_service_access_information(body)
        self.assertEqual(psid, 'ps-1')
        self.assertEqual([c.cmcd_class for c in configs], ['object', 'status'])

    def test_a_configuration_without_what_the_report_needs_is_ignored(self):
        body = sai(dict(config_entry(), serverAddresses=[]), {'scheme': CMCD + 'object'}, config_entry('request', samplePercentage=120))
        self.assertEqual(m3d.parse_service_access_information(body)[1], [])
        self.assertEqual(m3d.parse_service_access_information([])[1], [])

    def test_the_configuration_carries_what_the_service_access_information_says(self):
        entry = config_entry('session', reportingInterval=10, reportingStartOffset=5, reportingDuration=60, samplingPeriod=2,
                             urlFilters=['\\.mpd$'], metrics=[TERM + 'session/sid', TERM + 'session/pr', TERM + 'object/br', 'other'])
        config = m3d.parse_service_access_information(sai(entry))[1][0]
        self.assertEqual((config.interval, config.start_offset, config.duration, config.sampling_period), (10, 5, 60, 2))
        self.assertEqual(config.keys, {'sid', 'pr'})
        self.assertTrue(config.url_wanted('http://as/x.mpd'))
        self.assertFalse(config.url_wanted('http://as/x.m4s'))


class AfAccess(unittest.TestCase):
    def client(self, af, **kwargs):
        return m3d.AfClient('http://af.example:7777', transport=af.transport(), **kwargs)

    def test_the_access_token_is_got_with_the_client_credentials_grant_and_used_as_bearer(self):
        # TS 26.510 clause 7.4.3: the Media AS is the client of the AF; RFC 6749 clause 4.4
        af = FakeAf(sai(config_entry()))
        client = self.client(af, client_id='as1', client_secret='s3cret', scope='service-access-information_retrieve')
        response = run(client.request('GET', client.sai_url('ext 1')))
        self.assertEqual(response.status_code, 200)
        token_request, sai_request = af.requests
        self.assertEqual(str(token_request.url), 'http://af.example:7777/oauth2/token')
        self.assertEqual(token_request.headers['authorization'], 'Basic ' + base64.b64encode(b'as1:s3cret').decode())
        self.assertEqual(token_request.content.decode(), 'grant_type=client_credentials&scope=service-access-information_retrieve')
        self.assertEqual(str(sai_request.url), 'http://af.example:7777/3gpp-maf-session-handling/v1/service-access-information/ext%201')
        self.assertEqual(sai_request.headers['authorization'], 'Bearer tok-1')

    def test_the_token_is_kept_until_it_expires(self):
        af = FakeAf(sai(config_entry()))
        clock = Clock()
        client = self.client(af, client_id='as1', client_secret='x', clock=clock)
        run(client.request('GET', client.sai_url('e')))
        run(client.request('GET', client.sai_url('e')))
        self.assertEqual(len([r for r in af.requests if r.url.path == '/oauth2/token']), 1)
        clock.now += 3600
        run(client.request('GET', client.sai_url('e')))
        self.assertEqual(len([r for r in af.requests if r.url.path == '/oauth2/token']), 2)

    def test_an_answer_401_gets_a_new_token_and_one_more_try(self):
        af = FakeAf(sai(config_entry()))
        client = self.client(af, client_id='as1', client_secret='x')
        run(client.request('GET', client.sai_url('e')))
        af.valid_token = 'tok-2'
        self.assertEqual(run(client.request('GET', client.sai_url('e'))).status_code, 200)
        self.assertEqual(af.requests[-1].headers['authorization'], 'Bearer tok-2')

    def test_without_client_credentials_no_token_is_asked_for_or_sent(self):
        af = FakeAf(sai(config_entry()))
        client = self.client(af)
        run(client.request('GET', client.sai_url('e')))
        self.assertEqual(len(af.requests), 1)
        self.assertNotIn('authorization', af.requests[0].headers)

    def test_a_token_endpoint_that_refuses_is_an_error(self):
        af = FakeAf()
        af.token_status = 401
        client = self.client(af, client_id='as1', client_secret='bad')
        with self.assertRaises(m3d.AfError):
            run(client.request('GET', client.sai_url('e')))


class Reporting(unittest.TestCase):
    def setUp(self):
        self.ctx = FakeContext()
        self.clock = Clock()
        self.af = FakeAf()
        self.random = [0.0]

    def reporter(self, *entries, idle=60.0):
        self.af.sai_body = sai(*entries)
        client = m3d.AfClient('http://af.example:7777', transport=self.af.transport(), clock=self.clock)
        reporter = m3d.MetricsReporter(self.ctx, client, clock=self.clock, rng=lambda: self.random[0], session_idle_timeout=idle)
        run(reporter.refresh_due())
        return reporter

    def sample(self, reporter, sid='s1', url='http://as.example/m4d/ps/seg1.m4s', **headers):
        headers = headers or {'CMCD-Session': 'sid="%s",cid="c1"' % sid, 'CMCD-Object': 'br=3200,ot=v', 'CMCD-Request': 'su,mtp=100'}
        reporter.add_sample('chc-1', url, None, headers)

    def test_the_service_access_information_of_each_external_service_identifier_is_got(self):
        reporter = self.reporter(config_entry('object'))
        self.assertEqual(str(self.af.requests[0].url), 'http://af.example:7777/3gpp-maf-session-handling/v1/service-access-information/ext-1')
        self.assertEqual([c.id for c in reporter.configs_for('chc-1')], ['mrc-object'])
        self.assertEqual(reporter.configs_for('unknown'), [])

    def test_the_service_access_information_is_got_again_when_the_response_says_and_conditionally(self):
        # TS 26.510 clause 5.3.2.3: conditional GET, the period guided by max-age
        reporter = self.reporter(config_entry('object'))
        run(reporter.refresh_due())
        self.assertEqual(len(self.af.requests), 1)
        self.clock.now += 29
        run(reporter.refresh_due())
        self.assertEqual(len(self.af.requests), 1)
        self.clock.now += 2
        run(reporter.refresh_due())
        self.assertEqual(len(self.af.requests), 2)
        self.assertEqual(self.af.requests[1].headers['if-none-match'], '"v1"')
        self.assertEqual([c.id for c in reporter.configs_for('chc-1')], ['mrc-object'])

    def test_a_configuration_that_the_af_no_longer_offers_is_dropped(self):
        reporter = self.reporter(config_entry('object'))
        self.af.sai_status = 404
        run(reporter.refresh('ext-1'))
        self.assertEqual(reporter.configs_for('chc-1'), [])

    def test_a_cmcd_report_goes_to_the_af_at_the_end_of_the_media_delivery_session(self):
        # TS 26.512 clauses 4.5.9 and 11.4.3.3: the JSON envelope with mediaStreamingClientData; TS 26.510 clause 9.5.2: the URL
        reporter = self.reporter(config_entry('object'))
        self.sample(reporter)
        run(reporter.flush())
        self.assertEqual(self.af.reports(), [])
        self.clock.now += 61
        run(reporter.flush())
        request, = self.af.reports()
        self.assertEqual(str(request.url), 'http://af.example:7777/3gpp-maf-session-handling/v1/provisioning-sessions/ps-1/metrics-reporting/mrc-object')
        self.assertEqual(request.headers['content-type'], 'application/3gpp-media-delivery-metrics-report+json;version=Rel19')
        body = json.loads(request.content)
        self.assertEqual(body['reportTimestamp'], '2023-11-14T22:14:21.000Z')
        session, = body['sessions']
        self.assertEqual((session['clientId'], session['provisioningSessionId'], session['sessionId'], session['contentId']), ('s1', 'ps-1', 's1', 'c1'))
        sample, = session['samples']
        self.assertEqual(sample, {'sampleTimestamp': '2023-11-14T22:13:20.000Z',
                                  'mediaStreamingClientData': {'requestUrl': 'http://as.example/m4d/ps/seg1.m4s', 'cmcdObjectInfo': {'br': 3200, 'ot': 'v'}}})
        # sent once
        self.clock.now += 61
        run(reporter.flush())
        self.assertEqual(len(self.af.reports()), 1)

    def test_a_session_that_goes_on_is_not_reported_at_the_end(self):
        reporter = self.reporter(config_entry('object'))
        for _ in range(3):
            self.sample(reporter)
            self.clock.now += 30
            run(reporter.flush())
        self.assertEqual(self.af.reports(), [])

    def test_with_a_reporting_interval_the_report_is_sent_every_interval_and_the_timer_starts_again(self):
        reporter = self.reporter(config_entry('request', reportingInterval=10))
        self.sample(reporter)
        self.clock.now += 5
        self.sample(reporter)
        run(reporter.flush())
        self.assertEqual(self.af.reports(), [])
        self.clock.now += 5
        run(reporter.flush())
        self.assertEqual(len(json.loads(self.af.reports()[0].content)['sessions'][0]['samples']), 2)
        self.sample(reporter)
        self.clock.now += 9
        run(reporter.flush())
        self.assertEqual(len(self.af.reports()), 1)
        self.clock.now += 1
        run(reporter.flush())
        self.assertEqual(len(self.af.reports()), 2)

    def test_the_sample_percentage_decides_at_the_start_of_the_session(self):
        # TS 26.510 clause 5.3.5.1: active when a random number in 0 to 100 is lower than the percentage
        reporter = self.reporter(config_entry('object', samplePercentage=30.0))
        self.random[0] = 0.5
        self.sample(reporter, sid='out')
        self.random[0] = 0.29
        self.sample(reporter, sid='in')
        self.random[0] = 0.5
        self.sample(reporter, sid='in')
        self.clock.now += 61
        run(reporter.flush())
        request, = self.af.reports()
        self.assertEqual(json.loads(request.content)['sessions'][0]['sessionId'], 'in')

    def test_the_start_offset_the_duration_the_sampling_period_and_the_url_filter_apply(self):
        reporter = self.reporter(config_entry('object', reportingStartOffset=10, reportingDuration=20, samplingPeriod=5, urlFilters=['seg']))
        start = self.clock.now
        for offset, name in ((0, 'seg'), (5, 'seg'), (10, 'seg'), (11, 'seg'), (12, 'other'), (15, 'seg'), (20, 'seg'), (25, 'seg'), (30, 'seg'), (35, 'seg')):
            self.clock.now = start + offset
            self.sample(reporter, url='http://as.example/m4d/ps/%s1.m4s' % name)
        self.clock.now += 61
        run(reporter.flush())
        # the window is 10 to 30 s after the first request, a sample is kept at the earliest 5 s after the last one kept
        stamps = [s['sampleTimestamp'][11:19] for s in json.loads(self.af.reports()[0].content)['sessions'][0]['samples']]
        self.assertEqual(stamps, ['22:13:30', '22:13:35', '22:13:40', '22:13:45', '22:13:50'])

    def test_the_listed_metrics_and_the_class_of_the_scheme_decide_what_is_reported(self):
        reporter = self.reporter(config_entry('session', metrics=[TERM + 'session/pr']), config_entry('status'))
        self.sample(reporter, **{'CMCD-Session': 'sid="s1",pr=2,st=v', 'CMCD-Status': 'rtp=100'})
        self.clock.now += 61
        run(reporter.flush())
        data = {}
        for request in self.af.reports():
            sample = json.loads(request.content)['sessions'][0]['samples'][0]['mediaStreamingClientData']
            data[request.url.path.rsplit('/', 1)[1]] = {k: v for k, v in sample.items() if k != 'requestUrl'}
        self.assertEqual(data, {'mrc-session': {'cmcdSessionInfo': {'pr': 2, 'sid': 's1'}}, 'mrc-status': {'cmcdStatusInfo': {'rtp': 100}}})

    def test_a_request_without_cmcd_session_identifier_or_without_configuration_is_not_reported(self):
        reporter = self.reporter(config_entry('object'))
        reporter.add_sample('chc-1', 'http://as/x', None, {'CMCD-Object': 'br=1'})
        reporter.add_sample('other', 'http://as/x', None, {'CMCD-Session': 'sid="s"', 'CMCD-Object': 'br=1'})
        self.clock.now += 61
        run(reporter.flush())
        self.assertEqual(self.af.reports(), [])

    def test_one_of_the_server_addresses_is_chosen_at_random(self):
        addresses = ['http://af1.example/3gpp-maf-session-handling/v1/', 'http://af2.example/3gpp-maf-session-handling/v1']
        reporter = self.reporter(config_entry('object', serverAddresses=addresses))
        config = reporter.configs_for('chc-1')[0]
        for value, host in ((0.0, 'af1.example'), (0.6, 'af2.example'), (0.999, 'af2.example')):
            self.random[0] = value
            self.assertEqual(reporter.report_url(config).split('/')[2], host)
        self.assertTrue(reporter.report_url(config).endswith('/v1/provisioning-sessions/ps-1/metrics-reporting/mrc-object'))

    def test_a_report_that_the_af_cannot_take_is_sent_again_later(self):
        # TS 26.510 clause 5.3.5.2: 503, the Retry-After header field says when
        reporter = self.reporter(config_entry('object'))
        self.af.report_status = 503
        self.sample(reporter)
        self.clock.now += 61
        run(reporter.flush())
        self.assertEqual(len(self.af.reports()), 1)
        self.clock.now += 6
        run(reporter.flush())
        self.assertEqual(len(self.af.reports()), 1)
        self.clock.now += 1
        self.af.report_status = 200
        run(reporter.flush())
        self.assertEqual(len(self.af.reports()), 2)
        self.clock.now += 100
        run(reporter.flush())
        self.assertEqual(len(self.af.reports()), 2)

    def test_a_refused_report_makes_the_service_access_information_be_got_again(self):
        # 403: the AF says that metrics reporting is not provisioned
        reporter = self.reporter(config_entry('object'))
        self.af.report_status = 403
        self.sample(reporter)
        self.clock.now += 61
        run(reporter.flush())
        before = len([r for r in self.af.requests if r.method == 'GET'])
        run(reporter.refresh_due())
        self.assertEqual(len([r for r in self.af.requests if r.method == 'GET']), before + 1)
        self.assertEqual(len(self.af.reports()), 1)

    def test_the_samples_socket_takes_one_json_object_per_line(self):
        reporter = self.reporter(config_entry('object'))

        async def go(path):
            await reporter.start(path, 0o600)
            try:
                reader, writer = await asyncio.open_unix_connection(path)
                line = {'ps': 'chc-1', 'url': 'http://as.example/m4d/ps/a.m4s', 'query': 'CMCD=sid%3D%22s1%22%2Cbr%3D1200', 'headers': {}}
                writer.write((json.dumps(line) + '\n' + 'not json\n' + json.dumps(line) + '\n').encode())
                await writer.drain()
                writer.close()
                await asyncio.sleep(0.2)
                self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
                self.clock.now += 61
                await reporter.flush()
            finally:
                await reporter.stop()
        with tempfile.TemporaryDirectory() as d:
            run(go(os.path.join(d, 'm3d.sock')))
        report, = self.af.reports()
        samples = json.loads(report.content)['sessions'][0]['samples']
        self.assertEqual([s['mediaStreamingClientData']['cmcdObjectInfo'] for s in samples], [{'br': 1200}, {'br': 1200}])


class Configuration(unittest.TestCase):
    def test_without_af_url_nothing_is_started(self):
        ctx = FakeContext()
        self.assertIsNone(m3d.from_context(ctx))
        self.assertEqual(m3d.samples_socket_path(ctx), '')

    def test_with_af_url_a_reporter_and_a_socket_path_exist(self):
        ctx = FakeContext({('5gms_as.m3d', 'af_url'): 'http://127.0.0.26:7777', ('5gms_as', 'pid_path'): '/run/rt-5gms/application-server.pid'})
        self.assertIsNotNone(m3d.from_context(ctx))
        self.assertEqual(m3d.samples_socket_path(ctx), '/run/rt-5gms/m3d-cmcd.sock')
        ctx.options[('5gms_as.m3d', 'samples_socket')] = '/tmp/x.sock'
        self.assertEqual(m3d.samples_socket_path(ctx), '/tmp/x.sock')


@unittest.skipUnless(os.access(OPENRESTY, os.X_OK) and os.path.isfile('/usr/local/openresty/nginx/conf/mime.types'), 'no openresty binary')
class WebProxyConfiguration(unittest.TestCase):
    def test_nginx_accepts_the_configuration_with_and_without_m3d(self):
        for extra, socket_in_config in (('', False), ('[5gms_as.m3d]\naf_url = http://127.0.0.26:7777\nsamples_socket = {d}/m3d.sock\n', True)):
            with self.subTest(m3d=socket_in_config), tempfile.TemporaryDirectory() as d:
                cfg = os.path.join(d, 'as.conf')
                with open(cfg, 'w') as fh:
                    fh.write(CONFIG.format(d=d) + extra.format(d=d))
                for sub in ('log', 'run', 'temp'):
                    os.makedirs(os.path.join(d, sub))
                ctx = Context(cfg)
                ctx.setAppLog(logging.getLogger('test'))
                ctx.addContentHostingConfiguration('chc-1', CHC.model_validate(chc_dict('http://origin.example.com/media/')))
                self.assertTrue(run(OpenRestyWebProxy(ctx).writeConfiguration()))
                with open(os.path.join(d, 'nginx.conf')) as fh:
                    text = fh.read()
                self.assertIn('m3d_cmcd.configure("%s")' % (d + '/m3d.sock' if socket_in_config else ''), text)
                self.assertIn('m3d_cmcd.forward("chc-1")', text)
                # a configuration test only: nginx does not start
                result = subprocess.run([OPENRESTY, '-t', '-c', os.path.join(d, 'nginx.conf'), '-p', d + '/'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                self.assertEqual(result.returncode, 0, result.stdout)


if __name__ == '__main__':
    unittest.main()
