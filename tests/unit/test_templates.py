# Unit tests of the M3 Content Preparation Templates handlers of the 5GMS Application Server (TS 26.512 clause 4.5.3).
# License: 5G-MAG Public License (v1.0)
import asyncio
import os
import tempfile
import unittest

from rt_5gms_as.exceptions import NoProblemException, ProblemException
from rt_5gms_as.server import M3Server
from tests.unit.test_context import CONFIG, chc, new_context

PATH = '/3gpp-mas-configuration/v3/content-preparation-templates/'


class FakeUrl:
    def __init__(self, path):
        self.path = path

    def __str__(self):
        return 'http://as.example.net:7777' + self.path


class FakeRequest:
    def __init__(self, tid, body=b'', content_type='application/json'):
        self.url = FakeUrl(PATH + tid)
        self.headers = {'content-type': content_type} if content_type else {}
        self.__body = body

    async def body(self):
        return self.__body


def run(coro):
    return asyncio.run(coro)


class Templates(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        config = os.path.join(self.dir.name, 'as.conf')
        with open(config, 'w') as fh:
            fh.write(CONFIG.format(d=self.dir.name))
        self.ctx = new_context(config)
        self.svr = M3Server()
        self.svr.setContext(self.ctx)

    def tearDown(self):
        self.dir.cleanup()

    def status(self, coro):
        try:
            run(coro)
        except (ProblemException, NoProblemException) as err:
            return err.status_code
        self.fail('no status raised')

    def create(self, tid, body=b'{"v":1}', ct='application/json'):
        return self.status(self.svr.create_content_preparation_template(tid, None, request=FakeRequest(tid, body, ct)))

    def replace(self, tid, body, ct='application/json'):
        return self.status(self.svr.replace_content_preparation_template(tid, None, request=FakeRequest(tid, body, ct)))

    def destroy(self, tid):
        return self.status(self.svr.destroy_content_preparation_template(tid, request=FakeRequest(tid)))

    def test_create_enumerate_replace_destroy(self):
        self.assertEqual(self.create('t1', b'\x00\x01binary', 'application/x-spec'), 201)
        self.assertEqual(run(self.svr.enumerate_content_preparation_templates(request=FakeRequest(''))), ['t1'])
        self.assertEqual(self.ctx.contentPreparationTemplates().get('t1'), (b'\x00\x01binary', 'application/x-spec'))
        self.assertEqual(self.replace('t1', b'\x00\x01binary', 'application/x-spec'), 204)
        self.assertEqual(self.replace('t1', b'{"v":2}'), 200)
        self.assertEqual(self.destroy('t1'), 204)
        self.assertEqual(run(self.svr.enumerate_content_preparation_templates(request=FakeRequest(''))), [])

    def test_duplicate_gone_and_missing(self):
        self.assertEqual(self.create('t1'), 201)
        self.assertEqual(self.create('t1'), 409)
        self.assertEqual(self.replace('none', b'{}'), 404)
        self.assertEqual(self.destroy('none'), 404)
        self.assertEqual(self.destroy('t1'), 204)
        self.assertEqual(self.destroy('t1'), 410)
        self.assertEqual(self.create('t1'), 410)
        self.assertEqual(self.replace('t1', b'{}'), 410)

    def test_missing_body_is_rejected_but_not_a_missing_content_type(self):
        # the OpenAPI file takes any content type ("*/*"); a body without a Content-Type is application/octet-stream (RFC 9110 clause 8.3)
        self.assertEqual(self.create('t1', body=b''), 400)
        self.assertEqual(self.create('t1', ct=''), 201)
        self.assertEqual(self.ctx.contentPreparationTemplates().get('t1'), (b'{"v":1}', 'application/octet-stream'))

    def test_destroyed_template_is_remembered_after_a_restart(self):
        self.assertEqual(self.create('t1'), 201)
        self.assertEqual(self.destroy('t1'), 204)
        self.assertEqual(self.create('t2'), 201)
        restarted = new_context(os.path.join(self.dir.name, 'as.conf'))
        store = restarted.contentPreparationTemplates()
        self.assertTrue(store.wasDestroyed('t1'))
        self.assertEqual(store.ids(), ['t2'])

    def test_referenced_template_cannot_be_destroyed(self):
        self.assertEqual(self.create('t1'), 201)
        config = chc()
        config.distribution_configurations[0].content_preparation_template_id = 't1'
        self.ctx.addContentHostingConfiguration('chc-1', config)
        self.assertEqual(self.destroy('t1'), 409)
        self.ctx.deleteContentHostingConfiguration('chc-1')
        self.assertEqual(self.destroy('t1'), 204)

    def test_reference_to_unknown_template_is_invalid(self):
        config = chc()
        config.distribution_configurations[0].content_preparation_template_id = 'nope'
        with self.assertRaises(type(self.ctx).ValueError):
            self.ctx.addContentHostingConfiguration('chc-1', config)


if __name__ == '__main__':
    unittest.main()
