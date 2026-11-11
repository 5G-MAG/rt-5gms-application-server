'''Test of the URL signature check of an M4d service location (TS 26.512 V19.3.0 clause 7.6.4.5, RFC 4648 clauses 3.2 and 5)'''

import base64
import hashlib
import json
import os
import shutil
import subprocess
import unittest

import rt_5gms_as

RESTY = shutil.which('resty') or ('/usr/local/openresty/bin/resty' if os.path.exists('/usr/local/openresty/bin/resty') else None)
PROXIES = os.path.join(os.path.dirname(os.path.abspath(rt_5gms_as.__file__)), 'proxies')

SCRIPT = '''
package.path = "%(proxies)s/?.lua;" .. package.path
local m = require "m4daccess"
local real = ngx
ngx = setmetatable({
    var = {scheme = "http", http_host = %(host)s, host = "as.example.net", request_uri = %(uri)s, remote_addr = "192.0.2.7"},
    req = {get_uri_args = function() return {tok = %(token)s, exp = "1900000000"} end},
    time = function() return 1800000000 end,
}, {__index = real})
local sig = {urlPattern = %(pattern)s, tokenName = "tok", tokenExpiryName = "exp", passphraseName = "pass", passphrase = "secret",
             useIPAddress = false}
print(tostring(m.verify_signature(sig)))
'''

URL = 'http://as.example.net/m4d/ps/seg1.m4s'


def token(url=URL, expiry='1900000000'):
    # clause 7.6.4.5: SHA-512 of url&tokenExpiryName=expiry&passphraseName=passphrase, base64url (88 characters with the padding "==")
    digest = hashlib.sha512(f'{url}&exp={expiry}&pass=secret'.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode()


@unittest.skipIf(RESTY is None, 'the resty command of OpenResty is not available')
class UrlSignature(unittest.TestCase):
    def allowed(self, tok, host='"as.example.net"', pattern='^http://as\\\\.example\\\\.net/m4d/', uri='"/m4d/ps/seg1.m4s?x=1"'):
        script = SCRIPT % {'proxies': PROXIES, 'host': host, 'uri': uri, 'pattern': json.dumps(pattern.replace('\\\\', '\\')),
                           'token': json.dumps(tok) if tok is not None else 'nil'}
        out = subprocess.run([RESTY, '-e', script], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip() == 'true'

    def test_the_token_is_accepted_with_the_padding_of_rfc_4648(self):
        padded = token()
        self.assertTrue(padded.endswith('=='))
        self.assertTrue(self.allowed(padded))

    def test_the_token_is_accepted_without_the_padding(self):
        self.assertTrue(self.allowed(token().rstrip('=')))

    def test_a_wrong_padding_or_a_different_token_is_403(self):
        self.assertFalse(self.allowed(token().rstrip('=') + '='))
        self.assertFalse(self.allowed(token().rstrip('=') + '==='))
        self.assertFalse(self.allowed(token(expiry='1900000001')))
        self.assertFalse(self.allowed('=='))
        self.assertFalse(self.allowed('abc$'))
        self.assertFalse(self.allowed(None))

    def test_a_request_without_host_header_field_is_checked_and_not_an_error(self):
        # the authority of the URL is then the name of the server
        self.assertTrue(self.allowed(token(), host='nil'))
        self.assertFalse(self.allowed('abc', host='nil'))

    def test_a_resource_outside_the_pattern_needs_no_token(self):
        self.assertTrue(self.allowed(None, pattern='^http://other\\\\.example\\\\.net/'))


if __name__ == '__main__':
    unittest.main()
