# Unit tests of the M3 operations when the web proxy does not apply the new configuration.
# TS 26.512 V19.3.0 clauses 4.5.2.5, 4.5.4.3, 4.5.4.5, 4.5.4.6 and 4.5.4.7: success is returned only for a configuration that is applied;
# TS 26.501 V19.4.0 clause 5.4.2 step 3: the AS responds indicating whether the configuration was successful or not.
# License: 5G-MAG Public License (v1.0)
import asyncio
import base64
import logging
import os
import tempfile
import types
import unittest

from rt_5gms_as.app import M3_PATH_PREFIX
from rt_5gms_as.context import Context
from rt_5gms_as.exceptions import NoProblemException, ProblemException
from rt_5gms_as.proxies.openresty import OpenRestyWebProxy
from rt_5gms_as.proxy_factory import WebProxyInterface
from rt_5gms_as.server import M3Server

from tests.unit.test_context import CONFIG, chc

def der(label):
    return '-----BEGIN %s-----\n%s\n-----END %s-----\n' % (label, base64.b64encode(b'\x30\x03\x02\x01\x01').decode(), label)

PEM = der('CERTIFICATE') + der('PRIVATE KEY')
PEM_2 = der('CERTIFICATE') + der('CERTIFICATE') + der('PRIVATE KEY')


def bare_proxy(cls, ctx, *args):
    '''The nginx proxy without its constructor, which creates the directories of the configuration'''
    proxy = object.__new__(cls)
    WebProxyInterface.__init__(proxy, ctx)
    return proxy


class FakeProxy(object):
    '''A web proxy whose reloads give the results in _results_ (an exception is raised), the last one repeated'''
    def __init__(self, results):
        self.results = list(results)
        self.reloads = 0
        self.running = True
    def daemonRunning(self):
        return self.running
    async def reloadAndWait(self):
        self.reloads += 1
        result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(result, Exception):
            raise result
        return result
    async def purgeAll(self, afResourceId):
        return 0


def request(path, content_type=None):
    async def body():
        return b''
    headers = {'content-type': content_type} if content_type else {}
    return types.SimpleNamespace(url=types.SimpleNamespace(path=M3_PATH_PREFIX + path), headers=headers, body=body)


class ReloadFailure(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        config = os.path.join(self.dir.name, 'as.conf')
        with open(config, 'w') as fh:
            fh.write(CONFIG.format(d=self.dir.name))
        self.ctx = Context(config)
        self.ctx.setAppLog(logging.getLogger('test'))
        self.server = M3Server()
        self.server.setContext(self.ctx)

    def proxy(self, *results):
        self.ctx.setWebProxy(FakeProxy(results))
        return self.ctx.webProxy()

    def outcome(self, call, *args, **kwargs):
        '''The status code of the answer (the handlers answer by exception) and the exception'''
        with self.assertRaises((ProblemException, NoProblemException)) as caught:
            asyncio.run(call(*args, **kwargs))
        return caught.exception.status_code, caught.exception

    def create_chc(self, results=(True,)):
        proxy = self.proxy(*results)
        return self.outcome(self.server.create_content_hosting_configuration, 'c1', chc(),
                            request=request('/content-hosting-configurations/c1', 'application/json'))[0], proxy

    FAILURES = (None, False, RuntimeError('signal failed'))

    def test_create_with_a_reload_that_is_not_applied_is_500_and_leaves_no_resource(self):
        for result in self.FAILURES:
            with self.subTest(result=result):
                status, proxy = self.create_chc((result,))
                self.assertEqual(status, 500)
                self.assertFalse(self.ctx.haveContentHostingConfiguration('c1'))
                self.assertFalse(self.ctx.wasContentHostingConfigurationDestroyed('c1'))
                # the proxy is told the state without the resource again
                self.assertEqual(proxy.reloads, 2)

    def test_create_with_an_applied_reload_is_201(self):
        status, proxy = self.create_chc((True,))
        self.assertEqual(status, 201)
        self.assertTrue(self.ctx.haveContentHostingConfiguration('c1'))

    def test_replace_with_a_reload_that_is_not_applied_is_500_and_keeps_the_previous_state(self):
        self.create_chc()
        new = chc()
        new.name = 'changed'
        for result in self.FAILURES:
            with self.subTest(result=result):
                proxy = self.proxy(result)
                status, error = self.outcome(self.server.replace_content_hosting_configuration, 'c1', new,
                                             request=request('/content-hosting-configurations/c1', 'application/json'))
                self.assertEqual(status, 500)
                self.assertEqual(self.ctx.findContentHostingConfigurationByProvisioningSession('c1').name, 'test')
                self.assertEqual(proxy.reloads, 2)

    def test_replace_with_an_applied_reload_is_200(self):
        self.create_chc()
        new = chc()
        new.name = 'changed'
        self.proxy(True)
        status, _ = self.outcome(self.server.replace_content_hosting_configuration, 'c1', new,
                                 request=request('/content-hosting-configurations/c1', 'application/json'))
        self.assertEqual(status, 200)
        self.assertEqual(self.ctx.findContentHostingConfigurationByProvisioningSession('c1').name, 'changed')

    def test_destroy_with_a_reload_that_is_not_applied_is_500(self):
        for result in self.FAILURES:
            with self.subTest(result=result):
                self.ctx.addContentHostingConfiguration('c1', chc())
                self.proxy(result)
                status, error = self.outcome(self.server.destroy_content_hosting_configuration, 'c1',
                                             request=request('/content-hosting-configurations/c1'))
                self.assertEqual(status, 500)
                self.assertTrue(error.detail)

    def test_destroy_with_an_applied_reload_is_204(self):
        self.ctx.addContentHostingConfiguration('c1', chc())
        self.proxy(True)
        self.assertEqual(self.outcome(self.server.destroy_content_hosting_configuration, 'c1',
                                      request=request('/content-hosting-configurations/c1'))[0], 204)

    def test_activate_with_a_reload_that_is_not_applied_is_500_and_keeps_the_state(self):
        for result in self.FAILURES:
            with self.subTest(result=result):
                self.ctx.addContentHostingConfiguration('c1', chc())
                proxy = self.proxy(result)
                req = request('/content-hosting-configurations/c1/active', 'application/json')
                req.json = self.async_value(False)
                status, _ = self.outcome(self.server.modify_content_hosting_configuration_state, 'c1', False, request=req)
                self.assertEqual(status, 500)
                self.assertTrue(self.ctx.isContentHostingConfigurationActive('c1'))
                self.assertEqual(proxy.reloads, 2)

    def test_activate_with_an_applied_reload_is_204(self):
        self.ctx.addContentHostingConfiguration('c1', chc())
        self.proxy(True)
        req = request('/content-hosting-configurations/c1/active', 'application/json')
        req.json = self.async_value(False)
        self.assertEqual(self.outcome(self.server.modify_content_hosting_configuration_state, 'c1', False, request=req)[0], 204)
        self.assertFalse(self.ctx.isContentHostingConfigurationActive('c1'))

    @staticmethod
    def async_value(value):
        async def get():
            return value
        return get

    def put_certificate(self, body):
        return self.outcome(self.server.replace_server_certificate, 'k1', body,
                            request=request('/certificates/k1', 'application/x-pem-file'))[0]

    def test_certificate_replace_with_a_reload_that_is_not_applied_is_500_and_keeps_the_previous_bundle(self):
        self.ctx.addCertificate('k1', PEM)
        for result in self.FAILURES:
            with self.subTest(result=result):
                proxy = self.proxy(result)
                self.assertEqual(self.put_certificate(PEM_2), 500)
                self.assertEqual(self.ctx.certificateContents('k1'), PEM)
                self.assertEqual(proxy.reloads, 2)

    def test_certificate_replace_with_an_applied_reload_is_200(self):
        self.ctx.addCertificate('k1', PEM)
        self.proxy(True)
        self.assertEqual(self.put_certificate(PEM_2), 200)
        self.assertEqual(self.ctx.certificateContents('k1'), PEM_2)

    def test_certificate_replace_without_a_running_proxy_is_200(self):
        # the reload of the real proxy answers True when no nginx runs (the configuration is written at the start)
        self.ctx.addCertificate('k1', PEM)
        self.ctx.setWebProxy(bare_proxy(OpenRestyWebProxy, self.ctx))
        self.assertEqual(self.put_certificate(PEM_2), 200)

    def test_identical_certificate_with_crlf_line_ends_is_204(self):
        # clause 4.5.2.5: a replacement bundle identical to the current one is answered with 204, line ends included
        crlf = PEM.replace('\n', '\r\n')
        self.ctx.addCertificate('k1', crlf)
        proxy = self.proxy(True)
        self.assertEqual(self.put_certificate(crlf), 204)
        self.assertEqual(proxy.reloads, 0)
        self.assertEqual(self.ctx.certificateContents('k1'), crlf)


class ReloadAndWait(unittest.TestCase):
    '''reloadAndWait of the nginx proxy: True only once the worker processes of the old configuration are replaced'''
    class Proxy(OpenRestyWebProxy):
        @classmethod
        def make(cls, workers, reloaded=True):
            proxy = bare_proxy(cls, types.SimpleNamespace())
            proxy.workers = list(workers)
            proxy.reloaded = reloaded
            return proxy
        async def reload(self):
            return self.reloaded
        def _OpenRestyWebProxy__workerPids(self, pid):
            return self.workers.pop(0) if len(self.workers) > 1 else self.workers[0]

    def run_proxy(self, proxy, running=True):
        if running:
            proxy._WebProxyInterface__daemon['proc'] = types.SimpleNamespace(pid=1)
        return asyncio.run(proxy.reloadAndWait(timeout=0.2))

    def test_no_nginx_process_is_nothing_to_reload(self):
        self.assertIs(self.run_proxy(self.Proxy.make([{1}]), running=False), True)

    def test_replaced_workers_are_applied(self):
        self.assertIs(self.run_proxy(self.Proxy.make([{1, 2}, {1, 2}, {3, 4}])), True)

    def test_workers_that_stay_are_not_applied(self):
        # nginx keeps its old workers when it cannot use the new configuration
        self.assertIs(self.run_proxy(self.Proxy.make([{1, 2}])), False)

    def test_failed_reload_is_none(self):
        self.assertIsNone(self.run_proxy(self.Proxy.make([{1, 2}], reloaded=False)))


if __name__ == '__main__':
    unittest.main()
