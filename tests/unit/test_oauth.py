'''Tests of the OAuth 2.0 authorisation of M3 (TS 26.512 V19.3.0 clause 6.6.3, RFC 6749 clause 4.4, RFC 6750)'''

import base64
import json
import unittest

from rt_5gms_as.oauth import Authorizer

FORM = 'application/x-www-form-urlencoded'


class FakeContext:
    def __init__(self, sections):
        self.sections = sections

    def hasConfigSection(self, name):
        return name in self.sections

    def getConfigSections(self, prefix):
        return [s for s in self.sections if s.startswith(prefix)]

    def getConfigVar(self, section, name, default=None):
        return self.sections.get(section, {}).get(name, default)


def basic(user, secret):
    return 'Basic ' + base64.b64encode(f'{user}:{secret}'.encode()).decode()


def authorizer(lifetime='3600'):
    return Authorizer(FakeContext({
        '5gms_as.oauth': {'token_lifetime': lifetime},
        '5gms_as.oauth.client.af': {'secret': 's', 'scopes': '*'},
        '5gms_as.oauth.client.small': {'secret': 't', 'scopes': 'server-certificate_create'},
    }))


def token_of(authz, user, secret, scope=None):
    body = 'grant_type=client_credentials' + (f'&scope={scope}' if scope else '')
    response = authz.token_endpoint('POST', FORM, body.encode(), basic(user, secret))
    assert response.status_code == 200, response.body
    return json.loads(response.body)


class OAuthTest(unittest.TestCase):
    def test_without_the_section_every_request_is_accepted(self):
        authz = Authorizer(FakeContext({}))
        self.assertIsNone(authz.check('GET', '/3gpp-mas-configuration/v1/certificates', None))

    def test_a_request_without_a_token_is_401_without_an_error_code(self):
        response = authorizer().check('GET', '/3gpp-mas-configuration/v1/certificates', None)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers['www-authenticate'], 'Bearer realm="5GMS AS"')

    def test_an_unknown_token_is_401_invalid_token(self):
        response = authorizer().check('GET', '/3gpp-mas-configuration/v1/certificates', 'Bearer nope')
        self.assertEqual(response.status_code, 401)
        self.assertIn('error="invalid_token"', response.headers['www-authenticate'])

    def test_the_token_endpoint_answers_as_rfc_6749_clause_5_1(self):
        answer = token_of(authorizer(), 'af', 's')
        self.assertEqual(answer['token_type'], 'Bearer')
        self.assertEqual(answer['expires_in'], 3600)
        self.assertEqual(len(answer['access_token']), 64)

    def test_the_scope_of_the_operation_is_needed(self):
        authz = authorizer()
        small = 'Bearer ' + token_of(authz, 'small', 't')['access_token']
        self.assertIsNone(authz.check('POST', '/3gpp-mas-configuration/v1/certificates/c1', small))
        denied = authz.check('DELETE', '/3gpp-mas-configuration/v1/certificates/c1', small)
        self.assertEqual(denied.status_code, 403)
        self.assertIn('scope="server-certificate_destroy"', denied.headers['www-authenticate'])

    def test_the_star_scope_covers_every_operation(self):
        authz = authorizer()
        everything = 'Bearer ' + token_of(authz, 'af', 's')['access_token']
        self.assertIsNone(authz.check('POST', '/3gpp-mas-configuration/v1/content-hosting-configurations/x/purge', everything))

    def test_a_token_expires(self):
        authz = authorizer(lifetime='0')
        expired = 'Bearer ' + token_of(authz, 'af', 's')['access_token']
        self.assertEqual(authz.check('GET', '/3gpp-mas-configuration/v1/certificates', expired).status_code, 401)

    def test_the_token_endpoint_refuses_what_rfc_6749_says(self):
        authz = authorizer()
        response = authz.token_endpoint('GET', FORM, b'', None)
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.headers['Allow'], 'POST')
        self.assertEqual(authz.token_endpoint('POST', 'application/json', b'{}', None).status_code, 400)
        self.assertEqual(authz.token_endpoint('POST', FORM, b'grant_type=password', basic('af', 's')).status_code, 400)
        self.assertEqual(authz.token_endpoint('POST', FORM, b'grant_type=client_credentials', basic('af', 'x')).status_code, 401)
        self.assertEqual(authz.token_endpoint('POST', FORM, b'grant_type=client_credentials&scope=x',
                                              basic('small', 't')).status_code, 400)


if __name__ == '__main__':
    unittest.main()
