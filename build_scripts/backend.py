#!/usr/bin/python3
#
# 5G-MAG Reference Tools: 5GMS Application Server
# ===============================================
#
# File: backend.py
# License: 5G-MAG Public License (v1.0)
# Author: David Waring
# Copyright: (C) 2022 British Broadcasting Corporation
#
# For full license terms please see the LICENSE file distributed with this
# program. If this file is missing then the license can be retrieved from
# https://drive.google.com/file/d/1cinCiA778IErENZ3JN52VFW-1ffHpx7Z/view
#
# Build backend wrapper for setuptools.build_meta which checks to see if
# the generated python files in src/rt_5gms_as/openapi_5g have been generated
# and creates them if not by running the generate_openapi script.
#
import os.path
import logging
from setuptools import build_meta as _orig
import subprocess

replace = ['build_sdist']
log = logging.getLogger(__name__)

# Copy all exported variables from the original
for name in _orig.__all__:
    if name not in replace:
        v = getattr(_orig, name)
        globals()[name] = v
__all__ = _orig.__all__

def _check_openapi():
    custom_build_dir = os.path.dirname(__file__)
    topdir = os.path.join(custom_build_dir,'..')
    srcdir = os.path.join(topdir,'src')
    rt_5gms_as_dir = os.path.join(srcdir, 'rt_5gms_as')
    openapi_dir = os.path.join(rt_5gms_as_dir, 'openapi_5g')
    if not os.path.isdir(openapi_dir):
        log.info('Generating OpenAPI Python classes...')
        result = subprocess.run([os.path.join(custom_build_dir,'generate_5gms_as_openapi')], stdin=None, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode != 0:
            log.error("Failed: %s", result.stderr.decode('utf-8'))
        else:
            log.info('Done generating OpenAPI Python classes')

def build_sdist(sdist_directory, config_settings=None):
    _check_openapi()
    return _orig.build_sdist(sdist_directory, config_settings)
