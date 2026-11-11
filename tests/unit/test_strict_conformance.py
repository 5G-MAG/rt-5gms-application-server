'''Behaviours of the M3 server that follow the OpenAPI files and the clauses only (strict conformance)'''
import asyncio
import types
import unittest

from rt_5gms_as.app import lists_uri_too_long, M3_PATH_PREFIX
from rt_5gms_as.exceptions import ProblemException
from rt_5gms_as.server import server


class UriTooLongTests(unittest.TestCase):
    def test_every_operation_but_the_enumerate_operations_lists_414(self):
        for path in ('/certificates/a', '/content-preparation-templates/a', '/content-hosting-configurations/a',
                     '/content-hosting-configurations/a/active', '/content-hosting-configurations/a/purge'):
            self.assertTrue(lists_uri_too_long(M3_PATH_PREFIX + path), path)

    def test_enumerate_operations_do_not(self):
        for path in ('/certificates', '/content-preparation-templates', '/content-hosting-configurations',
                     '/content-publishing-configurations', '/certificates/'):
            self.assertFalse(lists_uri_too_long(M3_PATH_PREFIX + path), path)

    def test_other_paths_are_not_operations_of_the_api(self):
        self.assertFalse(lists_uri_too_long('/oauth2/token'))
        self.assertFalse(lists_uri_too_long('/other'))


class ContentPublishingTests(unittest.TestCase):
    '''This 5GMSd AS holds no Content Publishing Configuration: no status that the OpenAPI files do not list is used'''
    def status(self, call, *args, **kwargs):
        server.setContext(object())
        request = types.SimpleNamespace(url=types.SimpleNamespace(path=M3_PATH_PREFIX + '/content-publishing-configurations/a'))
        with self.assertRaises(ProblemException) as caught:
            asyncio.run(call(*args, request=request, **kwargs))
        return caught.exception.status_code

    def test_create_is_500(self):
        self.assertEqual(500, self.status(server.create_content_publishing_configuration, 'a', None))

    def test_the_others_never_existed(self):
        self.assertEqual(404, self.status(server.replace_content_publishing_configuration, 'a', None))
        self.assertEqual(404, self.status(server.destroy_content_publishing_configuration, 'a'))
        self.assertEqual(404, self.status(server.interrogate_content_publishing_configuration_state, 'a'))
        self.assertEqual(404, self.status(server.modify_content_publishing_configuration_state, 'a', True))
        self.assertEqual(404, self.status(server.purge_content_publishing_cache, 'a', 'x'))


if __name__ == '__main__':
    unittest.main()
