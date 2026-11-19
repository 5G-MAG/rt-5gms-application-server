# Tests, with a private nginx and a mock origin, of the distribution through supplementary networks (TS 26.510 V19.2.0 table 8.8.3.3-1).
# License: 5G-MAG Public License (v1.0)
import unittest

from tests.unit.nginx_rig import HAVE_NGINX, Origin, Proxy, chc_dict

HOST = 'as.example.net'


def network(mode):
    return {'supplementaryDistributionNetworks': [{'distributionNetworkType': 'DISTRIBUTION_NETWORK_EMBMS', 'distributionMode': mode}]}


@unittest.skipUnless(HAVE_NGINX, 'no openresty binary')
class SupplementaryNetworks(unittest.TestCase):
    def test_only_the_exclusive_mode_takes_the_content_away_from_m4(self):
        origin = Origin()
        origin.routes = {'/media/seg.m4s': origin.answer(200, {}, b'segment')}
        chc = chc_dict(origin.port, dist=network('MODE_EXCLUSIVE'))
        for mode, prefix in (('MODE_HYBRID', '/m4d/hybrid/'), ('MODE_DYNAMIC', '/m4d/dynamic/')):
            chc['distributionConfigurations'].append(dict(chc_dict(origin.port, prefix=prefix, dist=network(mode))['distributionConfigurations'][0], distributionId=mode))
        proxy = Proxy({'c1': chc})
        try:
            self.assertEqual(proxy.get(HOST, '/m4d/ps1/seg.m4s')[0], 404)
            self.assertEqual(proxy.get(HOST, '/m4d/hybrid/seg.m4s')[0], 200)
            self.assertEqual(proxy.get(HOST, '/m4d/dynamic/seg.m4s')[0], 200)
        finally:
            proxy.stop()
            origin.stop()


if __name__ == '__main__':
    unittest.main()
