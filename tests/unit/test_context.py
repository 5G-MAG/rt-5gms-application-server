# Unit tests of the Context of the 5GMS Application Server: the identifiers of destroyed resources.
# License: 5G-MAG Public License (v1.0)
import logging
import os
import tempfile
import unittest

from rt_5gms_as.context import Context
from rt_5gms_as.openapi_5g.models.application_server_content_hosting_configuration import ApplicationServerContentHostingConfiguration as CHC

CONFIG = '''[DEFAULT]
log_dir = {d}/log
run_dir = {d}/run
[5gms_as]
cache_dir = {d}/as/cache
docroot = {d}/as/docroots
certificates_cache = {d}/as/certificates
m3_listen = 127.0.0.1
m3_port = 7777
'''

def chc(external_service_id='com.example.t'):
    # TS 26.512 V19.3.0 clause 9.4.3: the Content Hosting Configuration of TS 26.510 clause 8.8.3.1 with externalServiceId
    return CHC.model_validate({
        'name': 'test', 'externalServiceId': external_service_id,
        'ingestConfiguration': {'mode': 'PULL', 'protocol': 'urn:3gpp:5gms:content-protocol:http-pull', 'baseURL': 'http://origin.example.net/media/'},
        'distributionConfigurations': [{'distributionId': 'd1', 'canonicalDomainName': 'as.example.net',
                                        'baseURL': 'http://as.example.net/m4d/provisioning-session-x/',
                                        'entryPoint': {'relativePath': 'manifest.mpd', 'contentType': 'application/dash+xml'}}]})

def new_context(config):
    ctx = Context(config)
    ctx.setAppLog(logging.getLogger('test'))
    return ctx

class DestroyedIdentifiers(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.config = os.path.join(self.dir.name, 'as.conf')
        with open(self.config, 'w') as fh:
            fh.write(CONFIG.format(d=self.dir.name))

    def tearDown(self):
        self.dir.cleanup()

    def test_destroyed_configuration_is_remembered_after_a_restart(self):
        # clause 4.5.4.6: "Any subsequent attempt to recreate a Content Hosting Configuration resource with the same resource identifier shall fail"
        ctx = new_context(self.config)
        ctx.addContentHostingConfiguration('chc-1', chc())
        self.assertTrue(ctx.haveContentHostingConfiguration('chc-1'))
        self.assertFalse(ctx.wasContentHostingConfigurationDestroyed('chc-1'))
        self.assertTrue(ctx.deleteContentHostingConfiguration('chc-1'))
        self.assertTrue(ctx.wasContentHostingConfigurationDestroyed('chc-1'))
        restarted = new_context(self.config)
        self.assertFalse(restarted.haveContentHostingConfiguration('chc-1'))
        self.assertTrue(restarted.wasContentHostingConfigurationDestroyed('chc-1'))
        self.assertFalse(restarted.wasContentHostingConfigurationDestroyed('chc-2'))

    def test_rolled_back_creation_leaves_no_trace(self):
        ctx = new_context(self.config)
        ctx.addContentHostingConfiguration('chc-1', chc())
        ctx.deleteContentHostingConfiguration('chc-1')
        ctx.forgetDestroyedContentHostingConfiguration('chc-1')
        self.assertFalse(new_context(self.config).wasContentHostingConfigurationDestroyed('chc-1'))

    def test_file_is_private_and_a_broken_file_stops_the_start(self):
        ctx = new_context(self.config)
        ctx.addContentHostingConfiguration('chc-1', chc())
        ctx.deleteContentHostingConfiguration('chc-1')
        name = os.path.join(self.dir.name, 'as', 'destroyed-resources.json')
        self.assertTrue(os.path.isfile(name))
        self.assertEqual(os.stat(name).st_mode & 0o777, 0o600)
        with open(name, 'w') as fh:
            fh.write('{broken')
        with self.assertRaises(Context.ConfigError):
            Context(self.config)

if __name__ == '__main__':
    unittest.main()
