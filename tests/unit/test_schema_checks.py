'''Constraints of the M3 schemas that the generated models do not enforce (minItems)'''
import copy
import unittest

from rt_5gms_as.openapi_5g.models.application_server_content_hosting_configuration import ApplicationServerContentHostingConfiguration
from rt_5gms_as.schema_checks import content_hosting_configuration_min_items, content_hosting_configuration_value_problems

BASE = {
    'externalServiceId': 'x', 'name': 'n',
    'ingestConfiguration': {'mode': 'PULL', 'pull': True, 'protocol': 'urn:3gpp:5gms:content-protocol:http-pull', 'baseURL': 'http://o/'},
    'distributionConfigurations': [{'distributionId': 'd1', 'baseURL': 'http://x/', 'canonicalDomainName': 'localhost'}],
}


def problems(mutate):
    body = copy.deepcopy(BASE)
    mutate(body)
    return [p['param'] for p in content_hosting_configuration_min_items(ApplicationServerContentHostingConfiguration.model_validate(body))]


class MinItemsTest(unittest.TestCase):
    def test_a_valid_configuration_has_no_problem(self):
        self.assertEqual(problems(lambda b: None), [])

    def test_every_array_with_min_items_one(self):
        dist = lambda b: b['distributionConfigurations'][0]
        self.assertEqual(problems(lambda b: b.update(distributionConfigurations=[])), ['distributionConfigurations'])
        self.assertEqual(problems(lambda b: dist(b).update(pathRewriteRules=[])), ['distributionConfigurations[0].pathRewriteRules'])
        self.assertEqual(problems(lambda b: dist(b).update(cachingConfigurations=[])), ['distributionConfigurations[0].cachingConfigurations'])
        self.assertEqual(problems(lambda b: dist(b).update(geoFencing={'locatorType': 'urn:x', 'locators': []})), ['distributionConfigurations[0].geoFencing.locators'])
        self.assertEqual(problems(lambda b: dist(b).update(entryPoint={'relativePath': 'a.mpd', 'contentType': 'application/dash+xml', 'profiles': []})), ['distributionConfigurations[0].entryPoint.profiles'])
        self.assertEqual(problems(lambda b: dist(b).update(cachingConfigurations=[{'urlPatternFilter': '.*', 'cachingDirectives': {'statusCodeFilters': []}}])),
                         ['distributionConfigurations[0].cachingConfigurations[0].cachingDirectives.statusCodeFilters'])

    def test_non_empty_arrays_are_fine(self):
        def mutate(b):
            d = b['distributionConfigurations'][0]
            d['pathRewriteRules'] = [{'requestPathPattern': '^/a', 'mappedPath': '/b'}]
            d['cachingConfigurations'] = [{'urlPatternFilter': '.*', 'cachingDirectives': {'statusCodeFilters': [200]}}]
            d['geoFencing'] = {'locatorType': 'urn:x', 'locators': ['CH']}
        self.assertEqual(problems(mutate), [])


class ValueChecksTest(unittest.TestCase):
    def check(self, mutate):
        body = copy.deepcopy(BASE)
        mutate(body)
        return [p['param'] for p in content_hosting_configuration_value_problems(ApplicationServerContentHostingConfiguration.model_validate(body))]

    def test_entry_point_has_exactly_one_of_content_type_and_protocol(self):
        d = lambda b: b['distributionConfigurations'][0]
        self.assertEqual(self.check(lambda b: d(b).update(entryPoint={'relativePath': 'a.mpd', 'contentType': 'application/dash+xml'})), [])
        self.assertEqual(self.check(lambda b: d(b).update(entryPoint={'relativePath': 'a.mpd', 'protocol': 'urn:x'})), [])
        self.assertEqual(self.check(lambda b: d(b).update(entryPoint={'relativePath': 'a.mpd'})), ['distributionConfigurations[0].entryPoint'])
        self.assertEqual(self.check(lambda b: d(b).update(entryPoint={'relativePath': 'a.mpd', 'contentType': 'x', 'protocol': 'urn:x'})),
                         ['distributionConfigurations[0].entryPoint'])

    def test_relative_path_and_status_code_filters(self):
        d = lambda b: b['distributionConfigurations'][0]
        self.assertEqual(self.check(lambda b: d(b).update(entryPoint={'relativePath': 'http://[bad', 'contentType': 'x'})),
                         ['distributionConfigurations[0].entryPoint.relativePath'])
        for code, bad in ((99, True), (100, False), (599, False), (600, True)):
            r = self.check(lambda b: d(b).update(cachingConfigurations=[{'urlPatternFilter': '.*', 'cachingDirectives': {'statusCodeFilters': [code]}}]))
            self.assertEqual(bool(r), bad, code)


if __name__ == '__main__':
    unittest.main()
