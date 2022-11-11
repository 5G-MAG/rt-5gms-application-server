#!/usr/bin/python3
#
# 5G-MAG Reference Tools: M3 client testing api
# =============================================
#
# File: test_m3_client/client.py
# License: 5G-MAG Public License (v1.0)
# Author: David Waring
# Copyright: (C) 2022 British Broadcasting Corporation
#
# For full license terms please see the LICENSE file distributed with this
# program. If this file is missing then the license can be retrieved from
# https://drive.google.com/file/d/1cinCiA778IErENZ3JN52VFW-1ffHpx7Z/view
#
# The file provides the M3Client class.
#
'''
5G-MAG Reference Tools: M3 client testing api
=============================================

This class provides an interface to communicate with a 5GMS Application Server.
'''

import aiofiles
import json
import logging
import os.path
import os
import httpx
import sys
from typing import Optional, Union, Tuple, List

class M3Exception(Exception):
    '''Exception raised when there was a problem during M3 operations.
    '''
    def __init__(self, reason: str, status_code: Optional[int] = None):
        super().__init__(reason, status_code)

    def __str__(self) -> str:
        if self.args[1] is not None:
            return '[%i] %s'%(self.args[1], self.args[0])
        return self.args[0]

    def __repr__(self) -> str:
        return self.__class__.__name__+'(reason=%r, status_code=%r)'%self.args

class M3ClientException(M3Exception):
    '''Exception raised when there was a problem with the client request.
    '''
    pass

class M3ServerException(M3Exception):
    '''Exception raised when there was a problem with the Application Server.
    '''
    pass

class M3Client(object):
    '''M3 API Client Class
    '''

    def __init__(self, host_address: Tuple[str,int]):
        self.__host_address = host_address
        self.__session = None
        self.__log = logging.getLogger(__name__)

    async def __do_request(self, method: str, url_suffix: str, body: Union[str,bytes], content_type: str, headers: Optional[dict] = None) -> dict:
        if isinstance(body, str):
            body = bytes(body)
        req_headers = {'Content-Type': content_type}
        if headers is not None:
            req_headers.update(headers)
        url = 'http://'+self.__host_address[0]+':'+str(self.__host_address[1])+'/3gpp-m3/v1'+url_suffix
        if self.__session is None:
            self.__session = httpx.AsyncClient(http1=False, http2=True, headers={'User-Agent': '5GMS-AF/testing'})
        req = self.__session.build_request(method, url, headers=req_headers, data=body)
        resp = await self.__session.send(req)
        return {'status_code': resp.status_code, 'body': resp.text, 'headers': resp.headers}

    async def addCertificateFromPemFile(self, provisioning_session_id: str, certificate_id: str, pem_filename: str) -> bool:
        async with aiofiles.open(pem_filename, mode='rb') as pem_in:
            pem = await pem_in.read()
        return await self.addCertificateFromPemData(provisioning_session_id, certificate_id, pem)

    async def addCertificateFromPemData(self, provisioning_session_id: str, certificate_id: str, pem: str) -> bool:
        result = await self.__do_request('POST', '/certificates/'+provisioning_session_id+':'+certificate_id, pem, 'application/x-pem-file')
        if result['status_code'] == 201:
            self.__log.info('Certificate added successfully as %s', result['headers']['Location'])
            return True
        elif result['status_code'] == 405:
            raise M3ClientException('Certificate already exists!', status_code=result['status_code'])
        elif result['status_code'] == 413:
            raise M3ClientException('Certificate data too big for server!', status_code=result['status_code'])
        elif result['status_code'] == 414:
            raise M3ClientException('URI too long for server, try shorter provisioning session and/or certficate ids.', status_code=result['status_code'])
        elif result['status_code'] == 415:
            raise M3ClientException('Unsupported media-type', status_code=result['status_code'])
        elif result['status_code'] == 500:
            raise M3ServerException('Internal Server Error', status_code=result['status_code'])
        elif result['status_code'] == 503:
            raise M3ServerException('Service Unavailable', status_code=result['status_code'])
        else:
            msg = 'Unknown status code, %i, from server'%result['status_code']
            if result['status_code'] < 500:
                raise M3ClientException(msg, status_code=result['status_code'])
            else:
                raise M3ServerException(msg, status_code=result['status_code'])

    async def updateCertificateFromPemFile(self, provisioning_session_id: str, certificate_id: str, pem_filename: str) -> bool:
        async with aiofiles.open(pem_file, mode='rb') as pem_in:
            pem = await pem_in.read()
        return await self.updateCertificateFromPemData(provisioning_session_id, certificate_id, pem)

    async def updateCertificateFromPemData(self, provisioning_session_id: str, certificate_id: str, pem: str) -> bool:
        result = await self.__do_request('PUT', '/certificates/'+provisioning_session_id+':'+certificate_id, pem, 'application/x-pem-file')
        if result['status_code'] == 200:
            self.__log.debug('Certificate %s:%s updated successfully', provisioning_session_id, certificate_id)
            return True
        elif result['status_code'] == 204:
            self.__log.debug('Certificate %s:%s not changed', provisioning_session_id, certificate_id)
            return False
        elif result['status_code'] == 404:
            raise M3ClientException('Certificate not found!', status_code=result['status_code'])
        elif result['status_code'] == 413:
            raise M3ClientException('Certificate data too big for server!', status_code=result['status_code'])
        elif result['status_code'] == 414:
            raise M3ClientException('URI too long for server, try shorter provisioning session and/or certficate ids.', status_code=result['status_code'])
        elif result['status_code'] == 415:
            raise M3ClientException('Unsupported media-type', status_code=result['status_code'])
        elif result['status_code'] == 500:
            raise M3ServerException('Internal Server Error', status_code=result['status_code'])
        elif result['status_code'] == 503:
            raise M3ServerException('Service Unavailable', status_code=result['status_code'])
        else:
            msg = 'Unknown status code, %i, from server'%result['status_code']
            if result['status_code'] < 500:
                raise M3ClientException(msg, status_code=result['status_code'])
            else:
                raise M3ServerException(msg, status_code=result['status_code'])

    async def deleteCertificate(self, provisioning_session_id: str, certificate_id: str) -> bool:
        result = await self.__do_request('DELETE', '/certificates/'+provisioning_session_id+':'+certificate_id, None, 'application/json')
        if result['status_code'] == 204:
            self.__log.debug('Certificate delete successfully')
            return True
        elif result['status_code'] == 404:
            raise M3ClientException('Certificate not found!', status_code=result['status_code'])
        elif result['status_code'] == 409:
            raise M3ClientException('Certificate is in use by a ContentHostingConfiguration!', status_code=result['status_code'])
        elif result['status_code'] == 413:
            raise M3ClientException('Certificate data too big for server!', status_code=result['status_code'])
        elif result['status_code'] == 414:
            raise M3ClientException('URI too long for server, try shorter provisioning session and/or certficate ids.', status_code=result['status_code'])
        elif result['status_code'] == 500:
            raise M3ServerException('Internal Server Error', status_code=result['status_code'])
        elif result['status_code'] == 503:
            raise M3ServerException('Service Unavailable', status_code=result['status_code'])
        else:
            msg = 'Unknown status code, %i, from server'%result['status_code']
            if result['status_code'] < 500:
                raise M3ClientException(msg, status_code=result['status_code'])
            else:
                raise M3ServerException(msg, status_code=result['status_code'])

    async def listCertificates(self) -> List[str]:
        result = await self.__do_request('GET', '/certificates', None, 'application/json')
        if result['status_code'] == 200:
            return [str(s) for s in json.loads(result['body'])]
        elif result['status_code'] == 500:
            raise M3ServerException('Internal Server Error', status_code=result['status_code'])
        elif result['status_code'] == 503:
            raise M3ServerException('Service Unavailable', status_code=result['status_code'])
        else:
            msg = 'Unknown status code, %i, from server'%result['status_code']
            if result['status_code'] < 500:
                raise M3ClientException(msg, status_code=result['status_code'])
            else:
                raise M3ServerException(msg, status_code=result['status_code'])

    async def addContentHostingConfigurationFromJsonFile(self, provisioning_session_id: str, chc_filename: str) -> bool:
        async with aiofiles.open(chc_filename, mode='rb') as chc_in:
            chc = await chc_in.read()
        return await self.addContentHostingConfigurationFromJsonString(provisioning_session_id, chc)

    async def addContentHostingConfigurationFromObject(self, provisioning_session_id: str, chc: dict) -> bool:
        chcstr = json.dumps(chc)
        return await self.addContentHostingConfigurationFromJsonString(provisioning_session_id, chcstr)

    async def addContentHostingConfigurationFromJsonString(self, provisioning_session_id: str, chc: str) -> bool:
        result = await self.__do_request('POST', '/content-hosting-configurations/'+provisioning_session_id, chc, 'application/json')
        if result['status_code'] == 201:
            self.__log.debug('ContentHostingConfiguration added successfully as %s'%result['headers']['Location'])
            return True
        elif result['status_code'] == 405:
            raise M3ClientException('ContentHostingConfiguration already exists!', status_code=result['status_code'])
        elif result['status_code'] == 413:
            raise M3ClientException('ContentHostingConfiguration data too big for server!', status_code=result['status_code'])
        elif result['status_code'] == 414:
            raise M3ClientException('URI too long for server, try shorter provisioning session id.', status_code=result['status_code'])
        elif result['status_code'] == 415:
            raise M3ClientException('Unsupported media-type', status_code=result['status_code'])
        elif result['status_code'] == 500:
            raise M3ServerException('Internal Server Error', status_code=result['status_code'])
        elif result['status_code'] == 503:
            raise M3ServerException('Service Unavailable', status_code=result['status_code'])
        else:
            msg = 'Unknown status code, %i, from server'%result['status_code']
            if result['status_code'] < 500:
                raise M3ClientException(msg, status_code=result['status_code'])
            else:
                raise M3ServerException(msg, status_code=result['status_code'])

    async def updateContentHostingConfigurationFromJsonFile(self, provisioning_session_id: str, chc_file: str) -> bool:
        async with aiofiles.open(chc_file, mode='rb') as chc_in:
            chc = await chc_in.read()
        return await self.updateContentHostingConfigurationFromJsonString(self, provisioning_session_id, chc)

    async def updateContentHostingConfigurationFromObject(self, provisioning_session_id: str, chc: dict) -> bool:
        chcstr = json.dumps(chc)
        return await self.updateContentHostingConfigurationFromJsonString(self, provisioning_session_id, chcstr)

    async def updateContentHostingConfigurationFromJsonString(self, provisioning_session_id: str, chc: str) -> bool:
        result = await self.__do_request('PUT', '/content-hosting-configurations/'+provisioning_session_id, chc, 'application/json')
        if result['status_code'] == 200:
            self.__log.debug('ContentHostingConfiguration updated')
            return True
        elif result['status_code'] == 204:
            self.__log.debug('No change to ContentHostingConfiguration')
            return False
        elif result['status_code'] == 404:
            raise M3ClientException('ContentHostingConfiguration not found!', status_code=result['status_code'])
        elif result['status_code'] == 413:
            raise M3ClientException('ContentHostingConfiguration data too big for server!', status_code=result['status_code'])
        elif result['status_code'] == 414:
            raise M3ClientException('URI too long for server, try shorter provisioning session id.', status_code=result['status_code'])
        elif result['status_code'] == 415:
            raise M3ClientException('Unsupported media-type', status_code=result['status_code'])
        elif result['status_code'] == 500:
            raise M3ServerException('Internal Server Error', status_code=result['status_code'])
        elif result['status_code'] == 503:
            raise M3ServerException('Service Unavailable', status_code=result['status_code'])
        else:
            msg = 'Unknown status code, %i, from server'%result['status_code']
            if result['status_code'] < 500:
                raise M3ClientException(msg, status_code=result['status_code'])
            else:
                raise M3ServerException(msg, status_code=result['status_code'])

    async def deleteContentHostingConfiguration(self, provisioning_session_id: str) -> bool:
        result = await self.__do_request('DELETE', '/content-hosting-configurations/'+provisioning_session_id, None, 'application/json')
        if result['status_code'] == 204:
            self.__log.debug('ContentHostingConfiguration for %s deleted', provisioning_session_id)
            return True
        elif result['status_code'] == 404:
            raise M3ClientException('ContentHostingConfiguration not found!', status_code=result['status_code'])
        elif result['status_code'] == 414:
            raise M3ClientException('URI too long for server, try shorter provisioning session id.', status_code=result['status_code'])
        elif result['status_code'] == 415:
            raise M3ClientException('Unsupported media-type', status_code=result['status_code'])
        elif result['status_code'] == 500:
            raise M3ServerException('Internal Server Error', status_code=result['status_code'])
        elif result['status_code'] == 503:
            raise M3ServerException('Service Unavailable', status_code=result['status_code'])
        else:
            msg = 'Unknown status code, %i, from server'%result['status_code']
            if result['status_code'] < 500:
                raise M3ClientException(msg, status_code=result['status_code'])
            else:
                raise M3ServerException(msg, status_code=result['status_code'])

    async def listContentHostingConfigurations(self) -> List[str]:
        result = await self.__do_request('GET', '/content-hosting-configurations', None, 'application/json')
        if result['status_code'] == 200:
            return [str(s) for s in json.loads(result['body'])]
        elif result['status_code'] == 500:
            raise M3ServerException('Internal Server Error', status_code=result['status_code'])
        elif result['status_code'] == 503:
            raise M3ServerException('Service Unavailable', status_code=result['status_code'])
        else:
            msg = 'Unknown status code, %i, from server'%result['status_code']
            if result['status_code'] < 500:
                raise M3ClientException(msg, status_code=result['status_code'])
            else:
                raise M3ServerException(msg, status_code=result['status_code'])
