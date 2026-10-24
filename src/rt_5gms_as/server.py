#!/usr/bin/python3
#
# 5G-MAG Reference Tools: 5GMS Application Server
# ===============================================
#
# File: server.py
# License: 5G-MAG Public License (v1.0)
# Author: David Waring
# Copyright: (C) 2022 British Broadcasting Corporation
#
# For full license terms please see the LICENSE file distributed with this
# program. If this file is missing then the license can be retrieved from
# https://drive.google.com/file/d/1cinCiA778IErENZ3JN52VFW-1ffHpx7Z/view
#
# This provides the server functions for the M3 API (Mas_Configuration, TS 26.512 clause 9)

from fastapi import Request
from .exceptions import ProblemException, NoProblemException
from .context import Context
from .proxy_factory import WebProxyError
from typing import Tuple

import regex

class M3Server:
    '''
    M3 API handling methods

    This class contains methods that will be called by the OpenAPI generated code. The method names are the
    operationIds of the Mas_Configuration API (TS 26.512 V19.3.0 clause 9 and C.3A).

    This file needs to be kept synchronised with changes to the M3 API.
    '''
    def __init__(self):
        '''
        Constructor
        '''
        self.__context = None

    def setContext(self, context):
        '''
        Register an application context

        An application context can be registered which will then be accessible to the API callbacks
        '''
        self.__context = context

    #### helpers ####
    def __ready(self, request):
        if self.__context is None:
            raise ProblemException(status_code=500, title='Server not finished initialisation', instance=request.url.path)
        return self.__context

    @staticmethod
    def __not_implemented(request, what):
        # TS 29.500 clause 5.2.7.2: a request that the NF does not implement is answered with an error status and a ProblemDetails
        raise ProblemException(status_code=501, title='Not Implemented', detail='%s is not implemented by this 5GMS AS'%what, instance=request.url.path)

    @staticmethod
    def __gone_or_missing(request, destroyed, what):
        # TS 26.512 clause 4.5.4.5 and others: 410 if the resource previously existed and was destroyed, otherwise 404
        if destroyed:
            raise ProblemException(status_code=410, title='%s Gone'%what, instance=request.url.path)
        raise ProblemException(status_code=404, title='%s Not Found'%what, instance=request.url.path)

    #### Content Hosting Configurations (TS 26.512 clause 4.5.4) ####
    async def enumerate_content_hosting_configurations(self, request=None):
        '''M3 Handler for "GET .../content-hosting-configurations"

        Responds with a JSON array of Content Hosting Configuration resource identifiers (clause 4.5.4.2).
        '''
        ctx = self.__ready(request)
        ctx.appLog().info("Getting list of content hosting configurations...")
        return list(ctx.getProvisioningSessionIds())

    async def create_content_hosting_configuration(self, afResourceId, content_hosting_configuration, request=None):
        '''M3 Handler for "POST .../content-hosting-configurations/{afResourceId}" (clause 4.5.4.3)

        Content-Type: application/json
        Expects an ApplicationServerContentHostingConfiguration JSON object as the body.

        Responds with an empty body and 201, 409 if it exists, 400 if a referenced resource is invalid, 410 if it was
        destroyed before.
        '''
        ctx = self.__ready(request)
        if request.headers['content-type'] != 'application/json':
            raise ProblemException(status_code=415, title='Invalid media type', instance=request.url.path)
        if ctx.haveContentHostingConfiguration(afResourceId):
            raise ProblemException(status_code=409, title='Content Hosting Configuration Already Exists', instance=request.url.path)
        if ctx.wasContentHostingConfigurationDestroyed(afResourceId):
            raise ProblemException(status_code=410, title='Content Hosting Configuration Gone', instance=request.url.path)
        ctx.appLog().info("Adding content hosting configuration %s..."%(afResourceId))
        ctx.appLog().debug("afResourceId = %s, chc = %r", afResourceId, content_hosting_configuration)
        try:
            ctx.addContentHostingConfiguration(afResourceId, content_hosting_configuration)
        except Context.ValueError as err:
            # a referenced resource is invalid: the resource stays uncreated
            raise ProblemException(status_code=400, title='Invalid Content Hosting Configuration', detail=str(err), instance=request.url.path, invalid_params=[{'param': err.args[1], 'reason': err.args[0]}])
        except Context.ConfigError as err:
            raise ProblemException(status_code=500, title='Error in Content Hosting Configuration', detail=str(err), instance=request.url.path)
        wp = ctx.webProxy()
        # Start or reload the web proxy server
        if not wp.daemonRunning():
            ctx.appLog().info("Starting proxy daemon...")
            await wp.writeConfiguration()
            await wp.startDaemon()
        else:
            ctx.appLog().info("Reloading proxy daemon...")
            await wp.reload()
        raise NoProblemException(status_code=201, media_type='application/json', headers={'Location': request.url.path})

    async def replace_content_hosting_configuration(self, afResourceId, content_hosting_configuration, request=None):
        '''M3 Handler for "PUT .../content-hosting-configurations/{afResourceId}" (clause 4.5.4.5)

        Responds with 200 and an empty body, 204 if unchanged, 400 if a referenced resource is invalid (state kept),
        410 if destroyed before, 404 if it never existed.
        '''
        ctx = self.__ready(request)
        old_chc = ctx.findContentHostingConfigurationByProvisioningSession(afResourceId)
        if old_chc is None:
            self.__gone_or_missing(request, ctx.wasContentHostingConfigurationDestroyed(afResourceId), 'Content Hosting Configuration')
        if old_chc == content_hosting_configuration:
            raise NoProblemException(status_code=204, media_type='application/json')
        ctx.appLog().info("Updating content hosting configuration %s..."%(afResourceId))
        try:
            if ctx.updateContentHostingConfiguration(afResourceId, content_hosting_configuration) is not True:
                ctx.appLog().error("Assertion: content hosting configuration updated failed!")
                raise ProblemException(status_code=500, title='Internal Server Error', detail='Assertion Error: ContentHostingConfiguration not updated when an update expected', instance=request.url.path)
        except Context.ValueError as err:
            raise ProblemException(status_code=400, title='Invalid Content Hosting Configuration', detail=str(err), instance=request.url.path, invalid_params=[{'param': err.args[1], 'reason': err.args[0]}])
        except Context.ConfigError as err:
            raise ProblemException(status_code=500, title='Error in Content Hosting Configuration', detail=str(err), instance=request.url.path)
        await ctx.webProxy().reload()
        raise NoProblemException(status_code=200, media_type='application/json')

    async def destroy_content_hosting_configuration(self, afResourceId, request=None):
        '''M3 Handler for "DELETE .../content-hosting-configurations/{afResourceId}" (clause 4.5.4.6)

        Responds with 204; 410 if destroyed before, 404 if it never existed.
        '''
        ctx = self.__ready(request)
        if not ctx.haveContentHostingConfiguration(afResourceId):
            self.__gone_or_missing(request, ctx.wasContentHostingConfigurationDestroyed(afResourceId), 'Content Hosting Configuration')
        ctx.appLog().info("Deleting content hosting configuration %s..."%(afResourceId))
        wp = ctx.webProxy()
        ctx.deleteContentHostingConfiguration(afResourceId)
        await wp.purgeAll(afResourceId)
        await wp.reload()
        raise NoProblemException(status_code=204, media_type='application/json')

    async def interrogate_content_hosting_configuration_state(self, afResourceId, request=None):
        '''GET .../content-hosting-configurations/{afResourceId}/active (clause 4.5.4.8)'''
        self.__ready(request)
        self.__not_implemented(request, 'Interrogating the state of a Content Hosting Configuration')

    async def modify_content_hosting_configuration_state(self, afResourceId, body, request=None):
        '''POST .../content-hosting-configurations/{afResourceId}/active (clause 4.5.4.7)'''
        self.__ready(request)
        self.__not_implemented(request, '(De)activating a Content Hosting Configuration')

    async def purge_content_hosting_cache(self, afResourceId, pattern, value=None, request=None):
        '''M3 Handler for "POST .../content-hosting-configurations/{afResourceId}/purge" (clause 4.5.4.9)

        Body: application/x-www-form-urlencoded with one key-value pair, pattern=<regular expression>.

        Responds with 200 and the number of cache entries purged, 204 if none, 400 if the body or the expression is
        malformed, 410 if the configuration was destroyed, 404 if it never existed.
        '''
        ctx = self.__ready(request)
        if request.headers['content-type'] != 'application/x-www-form-urlencoded':
            raise ProblemException(status_code=415, title='Invalid media type', instance=request.url.path)
        if not ctx.haveContentHostingConfiguration(afResourceId):
            self.__gone_or_missing(request, ctx.wasContentHostingConfigurationDestroyed(afResourceId), 'Content Hosting Configuration')
        if pattern is None or len(pattern) == 0:
            raise ProblemException(status_code=400, title='Bad Request', detail='The request body has to carry the key pattern with a regular expression', instance=request.url.path, invalid_params=[{'param': 'pattern', 'reason': 'missing'}])
        try:
            result = await ctx.webProxy().purgeUsingRegex(afResourceId, pattern)
        except regex.error as err:
            raise ProblemException(title='Bad Request', status_code=400, instance=request.url.path, detail='Bad regex: '+str(err), invalid_params=[{'param': 'pattern', 'reason': str(err)}])
        except WebProxyError as err:
            raise ProblemException(title='Internal Error', status_code=500, instance=request.url.path, detail='Regex failed to compile: '+str(err))
        except Exception as err:
            raise ProblemException(title='Internal Error', status_code=500, instance=request.url.path, detail='Exception while purging content: '+str(err))
        if result == 0:
            raise NoProblemException(status_code=204)
        return result

    #### Server Certificates (TS 26.512 clause 4.5.2) ####
    async def enumerate_server_certificates(self, request=None):
        '''M3 Handler for "GET .../certificates": a JSON array of Server Certificate resource identifiers (clause 4.5.2.2)'''
        ctx = self.__ready(request)
        ctx.appLog().info("Getting list of certificates...")
        return list(ctx.getCertificateIds())

    async def create_server_certificate(self, afResourceId, body, request=None):
        '''M3 Handler for "POST .../certificates/{afResourceId}" (clause 4.5.2.3)

        Content-Type: application/x-pem-file
        Body contains PEM file contents (public certificate, private key and intermediate certificates)

        Responds with 201 and an empty body, 409 if it exists, 410 if destroyed before.
        '''
        ctx = self.__ready(request)
        if request.headers['content-type'] != 'application/x-pem-file':
            raise ProblemException(status_code=415, title='Invalid media type', instance=request.url.path)
        if ctx.haveCertificate(afResourceId):
            raise ProblemException(status_code=409, title='Certificate Already Exists', instance=request.url.path)
        if ctx.wasCertificateDestroyed(afResourceId):
            raise ProblemException(status_code=410, title='Certificate Gone', instance=request.url.path)
        ctx.appLog().info("Adding certificate %s..."%(afResourceId))
        ctx.addCertificate(afResourceId, body)
        raise NoProblemException(status_code=201, media_type='application/json', headers={'Location': request.url.path})

    async def replace_server_certificate(self, afResourceId, body, request=None):
        '''M3 Handler for "PUT .../certificates/{afResourceId}" (clause 4.5.2.5)

        Responds with 200 and an empty body, 204 if the PEM bundle is identical, 410 if destroyed before, 404 if it never existed.
        '''
        ctx = self.__ready(request)
        if not ctx.haveCertificate(afResourceId):
            self.__gone_or_missing(request, ctx.wasCertificateDestroyed(afResourceId), 'Server Certificate')
        ctx.appLog().info("Attempting to update certificate %s..."%(afResourceId))
        if not ctx.updateCertificate(afResourceId, body):
            raise NoProblemException(status_code=204, media_type='application/json')
        # Certificate changed, reload proxy server to pick up new cert
        await ctx.webProxy().reload()
        raise NoProblemException(status_code=200, media_type='application/json')

    async def destroy_server_certificate(self, afResourceId, request=None):
        '''M3 Handler for "DELETE .../certificates/{afResourceId}" (clause 4.5.2.6)

        Responds with 204; 409 if still referenced; 410 if destroyed before, 404 if it never existed.
        '''
        ctx = self.__ready(request)
        if not ctx.haveCertificate(afResourceId):
            self.__gone_or_missing(request, ctx.wasCertificateDestroyed(afResourceId), 'Server Certificate')
        ctx.appLog().info("Deleting certificate %s..."%afResourceId)
        try:
            ctx.deleteCertificate(afResourceId)
        except Context.ConfigError as err:
            # Only complains if certificate is still in use
            raise ProblemException(status_code=409, title='Certificate still in use', detail=str(err), instance=request.url.path)
        raise NoProblemException(status_code=204, media_type='application/json')

    #### Content Preparation Templates (clause 4.5.3): not implemented ####
    async def enumerate_content_preparation_templates(self, request=None):
        '''GET .../content-preparation-templates: this AS holds none (clause 4.5.3.2: an empty array)'''
        self.__ready(request)
        return []

    async def create_content_preparation_template(self, afResourceId, body, request=None):
        self.__ready(request)
        self.__not_implemented(request, 'Content Preparation Templates')

    async def replace_content_preparation_template(self, afResourceId, body, request=None):
        self.__ready(request)
        self.__not_implemented(request, 'Content Preparation Templates')

    async def destroy_content_preparation_template(self, afResourceId, request=None):
        self.__ready(request)
        self.__not_implemented(request, 'Content Preparation Templates')

    #### Content Publishing Configurations (clause 4.5.5, uplink): not implemented ####
    async def enumerate_content_publishing_configurations(self, request=None):
        '''GET .../content-publishing-configurations: this AS holds none (clause 4.5.5.2: an empty array)'''
        self.__ready(request)
        return []

    async def create_content_publishing_configuration(self, afResourceId, content_publishing_configuration, request=None):
        self.__ready(request)
        self.__not_implemented(request, 'Content Publishing')

    async def replace_content_publishing_configuration(self, afResourceId, content_publishing_configuration, request=None):
        self.__ready(request)
        self.__not_implemented(request, 'Content Publishing')

    async def destroy_content_publishing_configuration(self, afResourceId, request=None):
        self.__ready(request)
        self.__not_implemented(request, 'Content Publishing')

    async def interrogate_content_publishing_configuration_state(self, afResourceId, request=None):
        self.__ready(request)
        self.__not_implemented(request, 'Content Publishing')

    async def modify_content_publishing_configuration_state(self, afResourceId, body, request=None):
        self.__ready(request)
        self.__not_implemented(request, 'Content Publishing')

    async def purge_content_publishing_cache(self, afResourceId, pattern, value=None, request=None):
        self.__ready(request)
        self.__not_implemented(request, 'Content Publishing')

server = M3Server()
