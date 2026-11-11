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

import re
from typing import Any, List, Dict, Optional
from urllib.parse import unquote_plus


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


def content_hosting_configuration_value_problems(chc: Any) -> List[Dict[str, str]]:
    '''Other constraints of the schemas that the generated models do not enforce

    The ``oneOf`` of RelativeMediaEntryPoint (contentType or protocol, exactly one), the format of relativePath
    (uri-reference) and the range 100..599 of the statusCodeFilters of CachingConfiguration.

    :param chc: an ApplicationServerContentHostingConfiguration model object.
    :return: a list of ``{'param': <JSON path>, 'reason': <text>}`` entries, empty if the configuration is fine.
    '''
    from urllib.parse import urlsplit
    problems: List[Dict[str, str]] = []
    for idx, dist in enumerate(chc.distribution_configurations or []):
        base = f'distributionConfigurations[{idx}]'
        ep = dist.entry_point
        if ep is not None:
            if (ep.content_type is None) == (ep.protocol is None):
                problems.append({'param': f'{base}.entryPoint', 'reason': 'exactly one of contentType and protocol is needed'})
            try:
                urlsplit(ep.relative_path)
            except ValueError:
                problems.append({'param': f'{base}.entryPoint.relativePath', 'reason': 'not a valid URI reference'})
        for cidx, cache in enumerate(dist.caching_configurations or []):
            for code in (cache.caching_directives.status_code_filters or []):
                if not 100 <= code <= 599:
                    problems.append({'param': f'{base}.cachingConfigurations[{cidx}].cachingDirectives.statusCodeFilters',
                                     'reason': 'a status code is between 100 and 599'})
                    break
    return problems


_PERCENT = re.compile(rb'%(?![0-9A-Fa-f]{2})')


def purge_form_problem(body: bytes) -> Optional[str]:
    '''Why the body of a purge request is not a single pattern key-value pair of application/x-www-form-urlencoded, None if it is

    TS 26.512 V19.3.0 clause 4.5.4.9: the body is a single key-value pair, the key is the string "pattern".
    '''
    if _PERCENT.search(body):
        return 'A percent sign is not followed by two hexadecimal digits'
    try:
        text = body.decode('ascii')
        pairs = text.split('&')
        if len(pairs) != 1:
            return 'The body has to be a single key-value pair'
        if '=' not in pairs[0]:
            return 'The body has to be a key-value pair: key=value'
        key, value = pairs[0].split('=', 1)
        unquote_plus(key, errors='strict')
        unquote_plus(value, errors='strict')
    except UnicodeError:
        return 'The body is not valid UTF-8 text with percent-encoding'
    if unquote_plus(key) != 'pattern':
        return 'The key has to be the string pattern'
    return None
