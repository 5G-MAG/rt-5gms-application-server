#!/usr/bin/python3
#
# 5G-MAG Reference Tools: M3 client testing app
# =============================================
#
# File: test_m3_client/__init__.py
# License: 5G-MAG Public License (v1.0)
# Author: David Waring
# Copyright: (C) 2022 British Broadcasting Corporation
#
# For full license terms please see the LICENSE file distributed with this
# program. If this file is missing then the license can be retrieved from
# https://drive.google.com/file/d/1cinCiA778IErENZ3JN52VFW-1ffHpx7Z/view
#
# This module provides the an M3 API Client interface.
#
'''Test M3 API Client module

This module provides an M3 API Client interface which can be used to
communicate with a 5GMS Application Server.

The module provides the M3Client class.
'''

from .client import M3Client, M3Exception, M3ClientException, M3ServerException

__all__ = [
        'M3Client',
        'M3Exception',
        'M3ClientException',
        'M3ServerException',
        ]
