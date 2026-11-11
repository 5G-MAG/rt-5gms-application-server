# Unit tests of the order of the checks of the M3 operations: a resource that is not there (410, 404) is answered before the body is judged.
# TS 26.512 V19.3.0 clauses 4.5.2.5, 4.5.4.5, 4.5.4.6, 4.5.4.7 and 4.5.4.9; the body of the DELETE of a Content Hosting Configuration is empty (clause 4.5.4.6).
# License: 5G-MAG Public License (v1.0)
import asyncio
import logging
import os
import tempfile
import types
import unittest

from rt_5gms_as.app import M3_PATH_PREFIX
from rt_5gms_as.context import Context
from rt_5gms_as.exceptions import NoProblemException, ProblemException
from rt_5gms_as.server import M3Server

from tests.unit.test_context import CONFIG, chc
from tests.unit.test_reload_failure import FakeProxy, PEM


def request(method, path, content_type=None, body=b''):
    async def read():
        return body
    headers = {'content-type': content_type} if content_type else {}
    return types.SimpleNamespace(method=method, url=types.SimpleNamespace(path=M3_PATH_PREFIX + path), headers=headers, body=read)


class Order(unittest.TestCase):
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
        # c-live exists, c-gone was destroyed, c-none never existed (the same for certificates)
        self.ctx.addContentHostingConfiguration('c-live', chc())
        self.ctx.addContentHostingConfiguration('c-gone', chc())
        self.ctx.deleteContentHostingConfiguration('c-gone')
        self.ctx.addCertificate('k-live', PEM)
        self.ctx.addCertificate('k-gone', PEM)
        self.ctx.deleteCertificate('k-gone')

    def status(self, call, *args, **kwargs):
        with self.assertRaises((ProblemException, NoProblemException)) as caught:
            asyncio.run(call(*args, **kwargs))
        return caught.exception.status_code

    def test_replace_content_hosting_configuration_with_an_unusable_body_is_410_or_404_for_a_missing_resource(self):
        for rid, expected in (('c-gone', 410), ('c-none', 404), ('c-live', 400)):
            with self.subTest(rid=rid):
                req = request('PUT', '/content-hosting-configurations/' + rid, 'application/json')
                self.assertEqual(self.status(self.server.replace_content_hosting_configuration, rid, None, request=req), expected)

    def test_replace_certificate_with_an_unusable_body_is_410_or_404_for_a_missing_resource(self):
        for rid, expected in (('k-gone', 410), ('k-none', 404)):
            for media_type, body in (('text/plain', 'x'), ('application/x-pem-file', 'not a pem')):
                with self.subTest(rid=rid, media_type=media_type):
                    req = request('PUT', '/certificates/' + rid, media_type)
                    self.assertEqual(self.status(self.server.replace_server_certificate, rid, body, request=req), expected)
        req = request('PUT', '/certificates/k-live', 'text/plain')
        self.assertEqual(self.status(self.server.replace_server_certificate, 'k-live', 'x', request=req), 415)
        req = request('PUT', '/certificates/k-live', 'application/x-pem-file')
        self.assertEqual(self.status(self.server.replace_server_certificate, 'k-live', 'not a pem', request=req), 400)

    def test_activate_and_purge_with_the_wrong_media_type_are_410_or_404_for_a_missing_resource(self):
        for rid, expected in (('c-gone', 410), ('c-none', 404), ('c-live', 415)):
            with self.subTest(rid=rid):
                req = request('POST', '/content-hosting-configurations/%s/active' % rid, 'text/plain')
                self.assertEqual(self.status(self.server.modify_content_hosting_configuration_state, rid, True, request=req), expected)
                req = request('POST', '/content-hosting-configurations/%s/purge' % rid, 'application/json')
                self.assertEqual(self.status(self.server.purge_content_hosting_cache, rid, '.*', request=req), expected)

    def test_the_framework_asks_for_the_missing_resource_when_it_rejects_a_body(self):
        cases = (('PUT', '/certificates/k-gone', 410), ('PUT', '/certificates/k-none', 404), ('PUT', '/certificates/k-live', None),
                 ('PUT', '/content-hosting-configurations/c-gone', 410), ('PUT', '/content-hosting-configurations/c-none', 404),
                 ('PUT', '/content-hosting-configurations/c-live', None),
                 ('POST', '/content-hosting-configurations/c-gone/active', 410), ('POST', '/content-hosting-configurations/c-none/purge', 404),
                 ('POST', '/content-hosting-configurations/c-live/purge', None),
                 # not a PUT or POST of these resources, or an identifier that the handler refuses itself
                 ('POST', '/content-hosting-configurations/c-none', None), ('PUT', '/content-hosting-configurations/a%2Fb', None),
                 ('PUT', '/content-hosting-configurations', None), ('DELETE', '/certificates/k-none', None))
        for method, path, expected in cases:
            with self.subTest(method=method, path=path):
                problem = self.server.missing_resource_problem(request(method, path))
                self.assertEqual(None if problem is None else problem.status_code, expected)

    def test_delete_content_hosting_configuration_with_a_body_is_413(self):
        # clause 4.5.4.6: the request body is empty; the OpenAPI response table lists 413
        req = request('DELETE', '/content-hosting-configurations/c-live', body=b'{}')
        self.assertEqual(self.status(self.server.destroy_content_hosting_configuration, 'c-live', request=req), 413)
        self.assertTrue(self.ctx.haveContentHostingConfiguration('c-live'))
        req = request('DELETE', '/content-hosting-configurations/c-live')
        self.assertEqual(self.status(self.server.destroy_content_hosting_configuration, 'c-live', request=req), 204)


if __name__ == '__main__':
    unittest.main()
