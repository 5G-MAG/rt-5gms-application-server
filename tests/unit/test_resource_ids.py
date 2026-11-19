# Unit tests of the resource identifiers of the M3 API: a ResourceId is any string chosen by the 5GMS AF (TS 26.512 V19.3.0 clause 6.4.2).
# License: 5G-MAG Public License (v1.0)
import asyncio
import os
import tempfile
import unittest

from rt_5gms_as.exceptions import NoProblemException, ProblemException
from rt_5gms_as.proxies.openresty import OpenRestyLocationConfig, OpenRestyWebProxy
from rt_5gms_as.server import M3Server
from rt_5gms_as.templates import TemplateStore
from rt_5gms_as.utils import decode_resource_id, encode_resource_id

from tests.unit.test_context import CONFIG, new_context
from tests.unit.test_reload_failure import PEM
from tests.unit.test_templates import FakeRequest
from tests.unit.test_ingest_base_query import FakeContext
from tests.unit.nginx_rig import HAVE_NGINX, Origin, Proxy, chc_dict

# characters that mean something in a file name, a URL path, the web proxy configuration and a Lua string
ODD = 'a b/../"$x\\\'{};|:é%41.tmp'


class Encoding(unittest.TestCase):
    def test_an_identifier_is_encoded_for_file_names_and_the_proxy_configuration(self):
        for resource_id in (ODD, '..', '.', '.tmp-x', 'plain-1_2.3~4', 'ü'):
            with self.subTest(resource_id=resource_id):
                name = encode_resource_id(resource_id)
                self.assertRegex(name, r'^[A-Za-z0-9_.~%-]+$')
                self.assertFalse(name.startswith('.'))
                self.assertEqual(decode_resource_id(name), resource_id)
        self.assertEqual(encode_resource_id('chc-1.v2'), 'chc-1.v2')


class Certificates(unittest.TestCase):
    def test_a_certificate_with_any_identifier_is_kept_listed_and_destroyed(self):
        with tempfile.TemporaryDirectory() as d:
            config = os.path.join(d, 'as.conf')
            with open(config, 'w') as fh:
                fh.write(CONFIG.format(d=d))
            ctx = new_context(config)
            ctx.addCertificate(ODD, PEM)
            ctx.addCertificate('..', PEM)
            self.assertEqual(sorted(os.listdir(ctx.certificatesCacheDir())), sorted([encode_resource_id(ODD), '%2E.']))
            restarted = new_context(config)
            self.assertEqual(sorted(restarted.getCertificateIds()), sorted([ODD, '..']))
            self.assertEqual(restarted.certificateContents(ODD), PEM)
            self.assertTrue(restarted.deleteCertificate(ODD))
            self.assertEqual(os.listdir(restarted.certificatesCacheDir()), ['%2E.'])
            self.assertTrue(new_context(config).wasCertificateDestroyed(ODD))


class Templates(unittest.TestCase):
    def test_a_template_with_any_identifier_is_kept_listed_and_destroyed(self):
        with tempfile.TemporaryDirectory() as d:
            store = TemplateStore(d)
            for resource_id in (ODD, '..', '.tmp-x'):
                self.assertTrue(store.put(resource_id, b'body', 'text/plain'))
            self.assertEqual(TemplateStore(d).ids(), sorted([ODD, '..', '.tmp-x']))
            self.assertEqual(TemplateStore(d).get(ODD), (b'body', 'text/plain'))
            store.delete(ODD)
            self.assertEqual(TemplateStore(d).ids(), sorted(['..', '.tmp-x']))
            self.assertTrue(TemplateStore(d).wasDestroyed(ODD))


class Handlers(unittest.TestCase):
    def test_the_handlers_take_any_identifier_that_fits_a_file_name(self):
        with tempfile.TemporaryDirectory() as d:
            config = os.path.join(d, 'as.conf')
            with open(config, 'w') as fh:
                fh.write(CONFIG.format(d=d))
            svr = M3Server()
            svr.setContext(new_context(config))
            for resource_id, status in ((ODD, 201), ('x' * 300, 400)):
                with self.assertRaises((ProblemException, NoProblemException)) as caught:
                    asyncio.run(svr.create_content_preparation_template(resource_id, None, request=FakeRequest(resource_id, b'{}')))
                self.assertEqual(caught.exception.status_code, status, resource_id)


class ProxyConfiguration(unittest.TestCase):
    def test_the_identifier_cannot_end_a_string_of_the_configuration_and_the_purge_finds_it_again(self):
        locn = OpenRestyLocationConfig(FakeContext(), '/m4d/ps/', 'http://origin.example.com/p/', ODD)
        text = asyncio.run(locn.config(2))
        token = encode_resource_id(ODD)
        self.assertIn('"%s|%s"' % (text.split('set $m4d_key_prefix "')[1].split('|')[0], token), text)
        self.assertIn('m3d_cmcd.forward("%s")' % token, text)
        self.assertNotIn(ODD, text)
        proxy = OpenRestyWebProxy.__new__(OpenRestyWebProxy)
        key = text.split('set $m4d_key_prefix "')[1].split('"')[0] + ':u=/m4d/ps/a.m4s'
        self.assertEqual(proxy._OpenRestyWebProxy__key_to_prov_sess_and_url_path(key), (ODD, '/m4d/ps/a.m4s'))


@unittest.skipUnless(HAVE_NGINX, 'no openresty binary')
class Running(unittest.TestCase):
    def test_a_configuration_with_an_odd_identifier_is_served_and_its_cache_purged(self):
        origin = Origin()
        origin.routes = {'/media/seg.m4s': origin.answer(200, {}, b'segment')}
        proxy = Proxy({ODD: chc_dict(origin.port)})
        try:
            self.assertEqual(proxy.get('as.example.net', '/m4d/ps1/seg.m4s')[0], 200)
            self.assertEqual(proxy.get('as.example.net', '/m4d/ps1/seg.m4s')[0], 200)
            self.assertEqual(len(origin.log), 1)
            self.assertEqual(asyncio.run(OpenRestyWebProxy(proxy.context).purgeAll(ODD)), 1)
        finally:
            proxy.stop()
            origin.stop()


if __name__ == '__main__':
    unittest.main()
