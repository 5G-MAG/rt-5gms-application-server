#!/usr/bin/python3
#
# 5G-MAG Reference Tools: 5GMS Application Server
# ===============================================
#
# File: utils.py
# License: 5G-MAG Public License (v1.0)
# Author: David Waring
# Copyright: (C) 2022 British Broadcasting Corporation
#
# For full license terms please see the LICENSE file distributed with this
# program. If this file is missing then the license can be retrieved from
# https://drive.google.com/file/d/1cinCiA778IErENZ3JN52VFW-1ffHpx7Z/view
#
# This is the 5G-MAG Reference Tools 5GMS AS common utility functions.
#
# Functions:
# find_executable_on_path(cmd) - Find cmd and return the absolute path
#
'''
General utility functions
'''

import asyncio
import inspect
import os
import os.path
import subprocess
import urllib.parse

from typing import Any, Callable, Awaitable

__all__ = ['find_executable_on_path', 'traverse_directory_tree', 'async_create_task', 'encode_resource_id', 'decode_resource_id', 'MAX_ENCODED_RESOURCE_ID']

def find_executable_on_path(cmd, *, verify=None, extra_paths=None):
    '''Find an executable command on the current $PATH

    Return the str path to the cmd or None if the command doesn't exist.
    '''
    paths = os.environ['PATH'].split(':')
    if extra_paths is not None:
        paths += extra_paths
    for path in paths:
        fpath=os.path.join(path,cmd)
        if os.path.isfile(fpath) and os.access(fpath, os.X_OK, follow_symlinks=True):
            if verify is not None and not verify(fpath):
                continue
            return fpath
    return None

def async_create_task(*args, **kwargs):
    'Wrapper for asyncio.create_task to remove unimplemented kwargs'
    allowed = inspect.signature(asyncio.create_task).parameters
    allowedkwargs = {key: value for key,value in kwargs.items() if key in allowed}
    return asyncio.create_task(*args, **allowedkwargs)

async def traverse_directory_tree(rootpath: str, filtcoro: Callable[[str,bool,Any],Awaitable[Any]], result: Any):
    for (dirpath,dirnames,filenames) in os.walk(rootpath):
        for dirname in dirnames:
            result = await filtcoro(os.path.join(dirpath,dirname), True, result)
        for filename in filenames:
            result = await filtcoro(os.path.join(dirpath,filename), False, result)
    return result

# longest encoded identifier that is still a file name on common file systems (255 bytes)
MAX_ENCODED_RESOURCE_ID = 240

def encode_resource_id(resource_id):
    '''A resource identifier as a string of unreserved characters and percent-encoded octets (RFC 3986 clause 2)

    TS 26.512 V19.3.0 clause 6.4.2: a ResourceId is any string chosen by the 5GMS AF. The encoded form is used where the identifier is a file name
    or is written between quotation marks into the web proxy configuration. It never starts with a dot, so it is neither "." nor "..".
    '''
    name = urllib.parse.quote(resource_id, safe='')
    if name.startswith('.'):
        name = '%2E' + name[1:]
    return name

def decode_resource_id(name):
    '''The resource identifier of an encoded one'''
    return urllib.parse.unquote(name)
