#!/usr/bin/python3
#
# 5G-MAG Reference Tools: 5GMS Application Server
# ===============================================
#
# File: fix_openapi_yaml.py
# License: 5G-MAG Public License (v1.0)
# Copyright: (C) 2026 5G-MAG
#
# For full license terms please see the LICENSE file distributed with this
# program. If this file is missing then the license can be retrieved from
# https://drive.google.com/file/d/1cinCiA778IErENZ3JN52VFW-1ffHpx7Z/view
#
# Called by generate_openapi (option -y) with the directory of the downloaded 5G_APIs YAML files.
#
# TS26512_Mas_Configuration.yaml writes the keys of its paths with percent-encoded braces
# (/certificates/%7bafResourceId%7d), which a generator takes as literal paths. The keys are rewritten to the path
# template form used by the sub-API files (/certificates/{afResourceId}); the $ref fragments are left as they are.
#
# The 3GPP "extensible enumeration" pattern, anyOf of a string with an enum and a plain string, is turned by the Python
# generator into an empty model class that rejects every value (ContentTransferMode: "mode": "PULL" is refused). Such a
# schema is replaced by a plain string whose description lists the values of the enumeration.
#
# ProblemDetails of TS29571_CommonData.yaml has the property noProfileMatchInfo (NRF discovery). Its generated model
# QueryParamCombination has a field named _query_params, which pydantic refuses at import time. The M3 API never
# returns this property, so it is dropped from ProblemDetails.
#
# The purge operations of the sub-API files list 204 but no 200 (specification defect SD-003 of the uplift register),
# while TS 26.512 clause 4.5.4.9 requires 200 with the number of purged cache entries. Without a 200 response the
# generator emits a handler that does not return the value, so a 200 response with an integer body is added.
#
# The python-fastapi generator emits security hooks for the OAuth2 client credentials flow that refer to an undefined
# name (oauth2_), so the module cannot be imported. This AS does not check access tokens (scope limit, see the register
# of the uplift), so the security requirements are removed from the generator input.
import os
import re
import sys

yaml_dir = sys.argv[1]
fname = os.path.join(yaml_dir, 'TS26512_Mas_Configuration.yaml')
if os.path.isfile(fname):
    with open(fname, encoding='utf-8') as f:
        lines = f.read().split('\n')
    out = []
    for line in lines:
        # a path key: two spaces, a slash path ending in a colon (the $ref lines carry a quoted fragment and are not keys)
        if re.match(r'^  /[^ ]*:\s*$', line):
            line = line.replace('%7b', '{').replace('%7B', '{').replace('%7d', '}').replace('%7D', '}')
        out.append(line)
    with open(fname, 'w', encoding='utf-8') as f:
        f.write('\n'.join(out))


def _is_extensible_enum(schema):
    if not isinstance(schema, dict) or set(schema.keys()) - {'anyOf', 'description', 'nullable', 'title'}:
        return False
    options = schema.get('anyOf')
    if not isinstance(options, list) or len(options) != 2:
        return False
    first, second = options
    return (isinstance(first, dict) and first.get('type') == 'string' and isinstance(first.get('enum'), list)
            and isinstance(second, dict) and second.get('type') == 'string' and 'enum' not in second)


def _fix(node):
    changed = False
    if isinstance(node, dict):
        for key, value in list(node.items()):
            if key == 'security' and isinstance(value, list):
                del node[key]
                changed = True
            elif _is_extensible_enum(value):
                values = ', '.join(str(v) for v in value['anyOf'][0]['enum'])
                text = value.get('description') or ''
                node[key] = {'type': 'string', 'description': (text + ' ' if text else '') + 'Values defined in this release: ' + values}
                changed = True
            elif _fix(value):
                changed = True
    elif isinstance(node, list):
        for item in node:
            if _fix(item):
                changed = True
    return changed


try:
    import yaml
except ImportError:
    yaml = None
if yaml is not None:
    for name in sorted(os.listdir(yaml_dir)):
        if not name.endswith('.yaml'):
            continue
        path = os.path.join(yaml_dir, name)
        with open(path, encoding='utf-8') as f:
            try:
                doc = yaml.safe_load(f)
            except yaml.YAMLError:
                continue
        changed = _fix(doc)
        try:
            for api_path, item in doc['paths'].items():
                op = item.get('post') if isinstance(item, dict) else None
                if api_path.endswith('/purge') and isinstance(op, dict) and '200' not in op['responses']:
                    responses = {'200': {'description': 'Content purged: the number of cache entries purged',
                                         'content': {'application/json': {'schema': {'type': 'integer', 'minimum': 1}}}}}
                    responses.update(op['responses'])
                    op['responses'] = responses
                    changed = True
        except (KeyError, TypeError, AttributeError):
            pass
        try:
            if 'securitySchemes' in doc['components']:
                del doc['components']['securitySchemes']
                changed = True
        except (KeyError, TypeError):
            pass
        try:
            props = doc['components']['schemas']['ProblemDetails']['properties']
            if 'noProfileMatchInfo' in props:
                del props['noProfileMatchInfo']
                changed = True
        except (KeyError, TypeError):
            pass
        if changed:
            with open(path, 'w', encoding='utf-8') as f:
                yaml.safe_dump(doc, f, sort_keys=False, width=1000)
