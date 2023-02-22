#!/usr/bin/python3
#
# 5G-MAG Reference Tools: 5GMS Application Server
# ===============================================
#
# File: exceptions.py
# License: 5G-MAG Public License (v1.0)
# Author: David Waring
# Copyright: (C) 2022 British Broadcasting Corporation
#
# For full license terms please see the LICENSE file distributed with this
# program. If this file is missing then the license can be retrieved from
# https://drive.google.com/file/d/1cinCiA778IErENZ3JN52VFW-1ffHpx7Z/view
#
# This is the 5G-MAG Reference Tools 5GMS AS exceptions module.
# This file contains definistions for app specific Exceptions.
#
'''
Reference Tools: 5GMS Application Server Exceptions
===================================================
'''
from typing import Optional, List, TypedDict

class InvalidParamMandatory(TypedDict):
    param: str

class InvalidParam(InvalidParamMandatory, total=False):
    reason: str

class ProblemException(Exception):
    def __init__(self, status_code=500, title=None, detail=None, problem_type=None, instance=None, headers: Optional[dict] = None, invalid_params: Optional[List[InvalidParam]] = None):
        # defaults
        if title is None:
            title = 'Internal Server Error'
        if detail is None:
            detail = title
        if problem_type is None:
            problem_type = '/3gpp-m3/v1'
        if instance[:len(problem_type)] == problem_type:
            instance = instance[len(problem_type):]
        # Store values
        self.status_code = status_code
        self.title = title
        self.detail = detail
        self.problem_type = problem_type
        self.instance = instance
        self.headers = headers
        self.invalid_params = invalid_params
        # Create Problem object
        self.object = {'title': self.title, 'detail': self.detail, 'type': self.problem_type, 'status': self.status_code}
        if self.instance is not None:
            self.object['instance'] = self.instance
        if self.invalid_params is not None and len(self.invalid_params) > 0:
            self.object['invalidParams'] = self.invalid_params

    def __str__(self):
        return '[%i] %s'%(self.status_code,self.detail)

class NoProblemException(Exception):
    def __init__(self, body: Optional[str] = None, status_code: int = 200, media_type: Optional[str] = 'application/octet-stream', headers: Optional[dict] = None):
        if body is None:
            body = ''
        self.body = body
        self.status_code = status_code
        self.media_type = media_type
        self.headers = headers

    def __str__(self):
        return '[%i] %s'%(self.status_code,self.body)

