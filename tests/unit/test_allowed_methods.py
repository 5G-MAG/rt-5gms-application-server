'''Allow header field of the 405 responses of the M3 API: the methods that the OpenAPI files define for the path (RFC 9110 clause 15.5.6)'''
import unittest

from rt_5gms_as.app import allowed_methods, M3_PATH_PREFIX
from rt_5gms_as.openapi_5g.apis.default_api import router


class AllowedMethodsTests(unittest.TestCase):
    def allowed(self, path):
        return allowed_methods(router, M3_PATH_PREFIX + path)

    def test_collections_are_read_only(self):
        for collection in ('/certificates', '/content-preparation-templates', '/content-hosting-configurations'):
            self.assertEqual(['GET'], self.allowed(collection), collection)

    def test_members_have_post_put_delete(self):
        for member in ('/certificates/a', '/content-preparation-templates/a', '/content-hosting-configurations/a'):
            self.assertEqual(['POST', 'PUT', 'DELETE'], self.allowed(member), member)

    def test_operations_of_a_content_hosting_configuration(self):
        self.assertEqual(['GET', 'POST'], self.allowed('/content-hosting-configurations/a/active'))
        self.assertEqual(['POST'], self.allowed('/content-hosting-configurations/a/purge'))

    def test_no_option_or_head_anywhere(self):
        for path in ('/certificates', '/certificates/a', '/content-hosting-configurations/a/active'):
            self.assertFalse({'OPTIONS', 'HEAD'} & set(self.allowed(path)), path)

    def test_unknown_path_has_no_methods(self):
        self.assertEqual([], self.allowed('/nothing'))
        self.assertEqual([], self.allowed('/certificates/a/b'))


if __name__ == '__main__':
    unittest.main()
