'''Test of the geofencing check of an M4d service location (TS 26.512 V19.3.0 clause 7.6.4.6, TS 26.510 table B.1-1)'''

import os
import shutil
import subprocess
import tempfile
import unittest

import rt_5gms_as

RESTY = shutil.which('resty') or ('/usr/local/openresty/bin/resty' if os.path.exists('/usr/local/openresty/bin/resty') else None)
PROXIES = os.path.join(os.path.dirname(os.path.abspath(rt_5gms_as.__file__)), 'proxies')

SCRIPT = '''
package.path = "%s/?.lua;" .. package.path
local m = require "m4daccess"
local geo = {locators = {%s}}
print(tostring(m.check_geo(geo, "%s", "%s")))
'''


@unittest.skipIf(RESTY is None, 'the resty command of OpenResty is not available')
class Geofencing(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.ranges = os.path.join(self.dir, 'ranges.csv')
        with open(self.ranges, 'w') as f:
            f.write('192.0.2.0/24,US\n198.51.100.0/24,NL\n')

    def allowed(self, locators, address):
        script = SCRIPT % (PROXIES, ','.join('"%s"' % x for x in locators), self.ranges, address)
        out = subprocess.run([RESTY, '-e', script], capture_output=True, text=True, timeout=60, cwd=self.dir)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip() == 'true'

    def test_a_country_code_locator_gives_access_to_a_ue_of_that_country(self):
        self.assertTrue(self.allowed(['US'], '192.0.2.7'))
        self.assertTrue(self.allowed(['nl', 'US'], '192.0.2.7'))
        self.assertFalse(self.allowed(['NL'], '192.0.2.7'))

    def test_a_subdivision_locator_does_not_widen_access_to_the_whole_country(self):
        self.assertFalse(self.allowed(['US-CA'], '192.0.2.7'))
        self.assertTrue(self.allowed(['US-CA', 'US'], '192.0.2.7'))

    def test_an_address_of_an_unknown_zone_is_refused(self):
        self.assertFalse(self.allowed(['US'], '203.0.113.9'))


if __name__ == '__main__':
    unittest.main()
