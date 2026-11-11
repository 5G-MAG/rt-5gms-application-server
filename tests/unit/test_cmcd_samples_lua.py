'''Test of the hand-over of CMCD information from an M4d request to the reporter of the AS (TS 26.512 V19.3.0 clauses 4.5.9 and 11.4.3.3)'''

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
local m = require "cmcd_samples"
local real = ngx
local timers = 0
ngx = setmetatable({status = %(status)d, var = {request_uri = %(uri)s, scheme = "http", host = "as.example.net", http_host = %(host)s,
                                                 http_cmcd_session = %(session)s, http_cmcd_object = %(object)s},
                    timer = {at = function() timers = timers + 1; return true end}}, {__index = real})
m.configure(%(socket)s)
print(tostring(m.line("chc-1")))
m.forward("chc-1")
print(timers)
'''


@unittest.skipIf(RESTY is None, 'the resty command of OpenResty is not available')
class Handover(unittest.TestCase):
    def run_lua(self, uri='"/m4d/ps/seg1.m4s?CMCD=sid%3D%22s1%22&token=SECRET"', status=200, host='"as.example.net:8080"',
                session='nil', object='nil', socket='"/run/m3d.sock"'):
        script = SCRIPT % {'proxies': PROXIES, 'uri': uri, 'status': status, 'host': host, 'session': session, 'object': object, 'socket': socket}
        out = subprocess.run([RESTY, '-e', script], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        line, timers = out.stdout.strip().split('\n')
        return (None if line == 'nil' else json.loads(line)), int(timers)

    def test_the_cmcd_query_argument_goes_on_and_nothing_else_of_the_query(self):
        line, timers = self.run_lua()
        self.assertEqual(line, {'ps': 'chc-1', 'url': 'http://as.example.net:8080/m4d/ps/seg1.m4s', 'query': 'CMCD=sid%3D%22s1%22', 'headers': {}})
        self.assertEqual(timers, 1)

    def test_the_cmcd_request_headers_go_on(self):
        line, _ = self.run_lua(uri='"/m4d/ps/seg1.m4s"', session='\'sid="s1"\'', object='"br=3200"')
        self.assertEqual(line['headers'], {'CMCD-Session': 'sid="s1"', 'CMCD-Object': 'br=3200'})
        self.assertNotIn('query', line)

    def test_a_request_without_cmcd_information_is_not_handed_over(self):
        self.assertEqual(self.run_lua(uri='"/m4d/ps/seg1.m4s?token=SECRET"'), (None, 0))

    def test_a_refused_request_is_not_handed_over(self):
        self.assertEqual(self.run_lua(status=403), (None, 0))

    def test_without_a_socket_nothing_is_sent(self):
        self.assertEqual(self.run_lua(socket='""')[1], 0)

    def test_a_request_without_host_header_field_gets_the_server_name(self):
        self.assertEqual(self.run_lua(host='nil')[0]['url'], 'http://as.example.net/m4d/ps/seg1.m4s')


if __name__ == '__main__':
    unittest.main()
