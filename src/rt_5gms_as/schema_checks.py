#!/usr/bin/env python3
#==============================================================================
# 5G-MAG Reference Tools: 5GMS Application Server M3 request checks
#==============================================================================
# License: 5G-MAG Public License (v1.0)
#
# For full license terms please see the LICENSE file distributed with this
# program. If this file is missing then the license can be retrieved from
# https://drive.google.com/file/d/1cinCiA778IErENZ3JN52VFW-1ffHpx7Z/view
#
'''
Checks of the constraints that the generated models do not enforce

The python-fastapi generator does not turn ``minItems`` of an array into a constraint of the model, so a Content Hosting
Configuration with an empty list where the OpenAPI definition asks for at least one item would pass the model. The list below
is taken from the schemas that the M3 interface uses (TS26510_Maf_Provisioning_ContentHosting.yaml of TS 26.510 V19.2.0,
TS26510_CommonData.yaml and TS26512_Mas_Configuration_ContentHosting.yaml of TS 26.512 V19.3.0).
'''

from typing import Any, List, Dict


def content_hosting_configuration_min_items(chc: Any) -> List[Dict[str, str]]:
    '''The arrays of a Content Hosting Configuration that are empty and must have at least one item

    :param chc: an ApplicationServerContentHostingConfiguration model object.
    :return: a list of ``{'param': <JSON path>, 'reason': <text>}`` entries, empty if the configuration is fine.
    '''
    problems: List[Dict[str, str]] = []

    def need(value: Any, path: str) -> None:
        if value is not None and len(value) < 1:
            problems.append({'param': path, 'reason': 'the array needs at least one item (minItems 1)'})

    need(chc.distribution_configurations, 'distributionConfigurations')
    for idx, dist in enumerate(chc.distribution_configurations or []):
        base = f'distributionConfigurations[{idx}]'
        need(dist.path_rewrite_rules, f'{base}.pathRewriteRules')
        need(dist.caching_configurations, f'{base}.cachingConfigurations')
        if dist.geo_fencing is not None:
            need(dist.geo_fencing.locators, f'{base}.geoFencing.locators')
        if dist.entry_point is not None:
            need(dist.entry_point.profiles, f'{base}.entryPoint.profiles')
        for cidx, cache in enumerate(dist.caching_configurations or []):
            need(cache.caching_directives.status_code_filters, f'{base}.cachingConfigurations[{cidx}].cachingDirectives.statusCodeFilters')
    return problems
