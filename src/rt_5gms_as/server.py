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
from .schema_checks import content_hosting_configuration_min_items
from .proxy_factory import WebProxyError, WebProxyRegexTimeout
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
    def __media_type(request):
        # the media type without parameters (TS 29.500 clause 5.2.7.2: content format), "" if there is no Content-Type header
        return request.headers.get('content-type', '').split(';')[0].strip().lower()

    __RESOURCE_ID = regex.compile(r'^[A-Za-z0-9._:-]{1,200}$')

    def __check_schema(self, request, content_hosting_configuration):
        '''Reject a Content Hosting Configuration that breaks a constraint the generated models do not enforce

        TS 29.500 clause 5.2.7.2: a request that does not comply with the schema is rejected with 400 (INVALID_MSG_FORMAT).
        '''
        problems = content_hosting_configuration_min_items(content_hosting_configuration)
        if problems:
            problem = ProblemException(status_code=400, title='Bad Request', detail='The request does not comply with the schema', instance=request.url.path, invalid_params=problems)
            problem.object['cause'] = 'INVALID_MSG_FORMAT'
            raise problem

    def __check_id(self, request, af_resource_id):
        # the identifier is used in a file name (certificates) and in the proxy configuration: no path separators or dot segments
        if self.__RESOURCE_ID.match(af_resource_id) is None or af_resource_id in ('.', '..'):
            raise ProblemException(status_code=400, title='Bad Request', detail='The resource identifier is not valid', instance=request.url.path, invalid_params=[{'param': 'afResourceId', 'reason': 'Only letters, digits and . _ : - are allowed'}])

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
        self.__check_id(request, afResourceId)
        if self.__media_type(request) != 'application/json':
            raise ProblemException(status_code=415, title='Invalid media type', instance=request.url.path)
        self.__check_schema(request, content_hosting_configuration)
        if ctx.haveContentHostingConfiguration(afResourceId):
            raise ProblemException(status_code=409, title='Content Hosting Configuration Already Exists', instance=request.url.path)
        if ctx.wasContentHostingConfigurationDestroyed(afResourceId):
            raise ProblemException(status_code=410, title='Content Hosting Configuration Gone', instance=request.url.path)
        ctx.appLog().info("Adding content hosting configuration %s..."%(afResourceId))
        try:
            ctx.addContentHostingConfiguration(afResourceId, content_hosting_configuration)
        except Context.ValueError as err:
            # a referenced resource is invalid: the resource stays uncreated
            raise ProblemException(status_code=400, title='Invalid Content Hosting Configuration', detail=str(err), instance=request.url.path, invalid_params=[{'param': err.args[1], 'reason': err.args[0]}])
        except Context.ConfigError as err:
            raise ProblemException(status_code=500, title='Error in Content Hosting Configuration', detail=str(err), instance=request.url.path)
        wp = ctx.webProxy()
        # Start or reload the web proxy server; if the proxy cannot use the configuration it is not kept
        # (clause 4.5.4.3 for references that are invalid; for what the proxy cannot realise no clause: 500)
        try:
            if not wp.daemonRunning():
                ctx.appLog().info("Starting proxy daemon...")
                ok = await wp.writeConfiguration() and await wp.startDaemon()
            else:
                ctx.appLog().info("Reloading proxy daemon...")
                ok = await wp.reloadAndWait() is not None
        except Exception as err:
            ctx.appLog().error("The web proxy could not use the Content Hosting Configuration %s: %s", afResourceId, err)
            ok = False
        if not ok:
            ctx.deleteContentHostingConfiguration(afResourceId)
            ctx.forgetDestroyedContentHostingConfiguration(afResourceId)
            try:
                await wp.reloadAndWait()
            except Exception:
                pass
            raise ProblemException(status_code=500, title='Internal Server Error', detail='The web proxy cannot realise this Content Hosting Configuration', instance=request.url.path)
        raise NoProblemException(status_code=201, media_type='application/json', headers={'Location': str(request.url)})

    async def replace_content_hosting_configuration(self, afResourceId, content_hosting_configuration, request=None):
        '''M3 Handler for "PUT .../content-hosting-configurations/{afResourceId}" (clause 4.5.4.5)

        Responds with 200 and an empty body, 204 if unchanged, 400 if a referenced resource is invalid (state kept),
        410 if destroyed before, 404 if it never existed.
        '''
        ctx = self.__ready(request)
        self.__check_id(request, afResourceId)
        self.__check_schema(request, content_hosting_configuration)
        old_chc = ctx.findContentHostingConfigurationByProvisioningSession(afResourceId)
        if old_chc is None:
            self.__gone_or_missing(request, ctx.wasContentHostingConfigurationDestroyed(afResourceId), 'Content Hosting Configuration')
        if old_chc == content_hosting_configuration:
            raise NoProblemException(status_code=204, media_type='application/json')
        ctx.appLog().info("Updating content hosting configuration %s..."%(afResourceId))
        previous = old_chc
        try:
            if ctx.updateContentHostingConfiguration(afResourceId, content_hosting_configuration) is not True:
                # the representation is identical to the current one although the objects differ (clause 4.5.4.5: a "no-op")
                raise NoProblemException(status_code=204, media_type='application/json')
        except Context.ValueError as err:
            raise ProblemException(status_code=400, title='Invalid Content Hosting Configuration', detail=str(err), instance=request.url.path, invalid_params=[{'param': err.args[1], 'reason': err.args[0]}])
        except Context.ConfigError as err:
            raise ProblemException(status_code=500, title='Error in Content Hosting Configuration', detail=str(err), instance=request.url.path)
        try:
            ok = await ctx.webProxy().reloadAndWait() is not None
        except Exception as err:
            ctx.appLog().error("The web proxy could not use the updated Content Hosting Configuration %s: %s", afResourceId, err)
            ok = False
        if not ok:
            # the update is not kept: the state is the one immediately before the operation
            ctx.updateContentHostingConfiguration(afResourceId, previous)
            try:
                await ctx.webProxy().reloadAndWait()
            except Exception:
                pass
            raise ProblemException(status_code=500, title='Internal Server Error', detail='The web proxy cannot realise this Content Hosting Configuration', instance=request.url.path)
        raise NoProblemException(status_code=200, media_type='application/json')

    async def destroy_content_hosting_configuration(self, afResourceId, request=None):
        '''M3 Handler for "DELETE .../content-hosting-configurations/{afResourceId}" (clause 4.5.4.6)

        Responds with 204; 410 if destroyed before, 404 if it never existed.
        '''
        ctx = self.__ready(request)
        self.__check_id(request, afResourceId)
        if not ctx.haveContentHostingConfiguration(afResourceId):
            self.__gone_or_missing(request, ctx.wasContentHostingConfigurationDestroyed(afResourceId), 'Content Hosting Configuration')
        ctx.appLog().info("Deleting content hosting configuration %s..."%(afResourceId))
        wp = ctx.webProxy()
        ctx.deleteContentHostingConfiguration(afResourceId)
        # the resource is destroyed from here on (a later request gets 410); a proxy that cannot follow is reported, and the
        # next reload brings it in line because its configuration is written from the stored state
        try:
            await wp.purgeAll(afResourceId)
            reloaded = await wp.reloadAndWait()
        except Exception as err:
            ctx.appLog().error("The web proxy could not release the Content Hosting Configuration %s: %s", afResourceId, err)
            reloaded = None
        if reloaded is None:
            raise ProblemException(status_code=500, title='Internal Server Error', detail='The Content Hosting Configuration is destroyed but the web proxy could not release it', instance=request.url.path)
        raise NoProblemException(status_code=204, media_type='application/json')

    async def interrogate_content_hosting_configuration_state(self, afResourceId, request=None):
        '''M3 Handler for "GET .../content-hosting-configurations/{afResourceId}/active" (clause 4.5.4.8)

        Responds with 200 and a JSON boolean (true for active); 410 if destroyed before, 404 if it never existed.
        '''
        ctx = self.__ready(request)
        self.__check_id(request, afResourceId)
        if not ctx.haveContentHostingConfiguration(afResourceId):
            self.__gone_or_missing(request, ctx.wasContentHostingConfigurationDestroyed(afResourceId), 'Content Hosting Configuration')
        return ctx.isContentHostingConfigurationActive(afResourceId)

    async def modify_content_hosting_configuration_state(self, afResourceId, body, request=None):
        '''M3 Handler for "POST .../content-hosting-configurations/{afResourceId}/active" (clause 4.5.4.7)

        The body is a single JSON boolean, true for active. The state changes at once (204, also when it is in the
        requested state already), so the 202 of the clause, for a change in progress, is never needed.
        410 if destroyed before, 404 if it never existed, 400 if the body is not a boolean.
        '''
        ctx = self.__ready(request)
        self.__check_id(request, afResourceId)
        if not ctx.haveContentHostingConfiguration(afResourceId):
            self.__gone_or_missing(request, ctx.wasContentHostingConfigurationDestroyed(afResourceId), 'Content Hosting Configuration')
        # the clause asks for a single JSON boolean: the framework would also accept strings such as "yes", so the body is read again
        try:
            desired = await request.json()
        except ValueError:
            desired = None
        if not isinstance(desired, bool):
            raise ProblemException(status_code=400, title='Bad Request', detail='The request body has to be a JSON boolean', instance=request.url.path)
        if ctx.setContentHostingConfigurationActive(afResourceId, desired):
            ctx.appLog().info("Content hosting configuration %s is now %s"%(afResourceId, 'active' if desired else 'inactive'))
            try:
                applied = await ctx.webProxy().reloadAndWait()
            except Exception as err:
                ctx.appLog().error("The web proxy could not be reloaded: %s", err)
                applied = None
            if applied is None:
                # the stored state follows what the proxy serves
                ctx.setContentHostingConfigurationActive(afResourceId, not desired)
                raise ProblemException(status_code=500, title='Internal Server Error', detail='The web proxy could not be reloaded', instance=request.url.path)
            if not applied:
                # clause 4.5.4.7: the activation request is acceptable but not complete yet; the state is polled with the interrogate operation
                raise NoProblemException(status_code=202, media_type='application/json')
        raise NoProblemException(status_code=204, media_type='application/json')

    async def purge_content_hosting_cache(self, afResourceId, pattern, value=None, request=None):
        '''M3 Handler for "POST .../content-hosting-configurations/{afResourceId}/purge" (clause 4.5.4.9)

        Body: application/x-www-form-urlencoded with one key-value pair, pattern=<regular expression>.

        Responds with 200 and the number of cache entries purged, 204 if none, 400 if the body or the expression is
        malformed, 410 if the configuration was destroyed, 404 if it never existed.
        '''
        ctx = self.__ready(request)
        self.__check_id(request, afResourceId)
        if self.__media_type(request) != 'application/x-www-form-urlencoded':
            raise ProblemException(status_code=415, title='Invalid media type', instance=request.url.path)
        if not ctx.haveContentHostingConfiguration(afResourceId):
            self.__gone_or_missing(request, ctx.wasContentHostingConfigurationDestroyed(afResourceId), 'Content Hosting Configuration')
        if pattern is None or len(pattern) == 0:
            raise ProblemException(status_code=400, title='Bad Request', detail='The request body has to carry the key pattern with a regular expression', instance=request.url.path, invalid_params=[{'param': 'pattern', 'reason': 'missing'}])
        try:
            result = await ctx.webProxy().purgeUsingRegex(afResourceId, pattern)
        except regex.error as err:
            raise ProblemException(title='Bad Request', status_code=400, instance=request.url.path, detail='Bad regex: '+str(err), invalid_params=[{'param': 'pattern', 'reason': str(err)}])
        except WebProxyRegexTimeout as err:
            raise ProblemException(title='Bad Request', status_code=400, instance=request.url.path, detail=str(err), invalid_params=[{'param': 'pattern', 'reason': 'too expensive to match'}])
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
        self.__check_id(request, afResourceId)
        if self.__media_type(request) != 'application/x-pem-file':
            raise ProblemException(status_code=415, title='Invalid media type', instance=request.url.path)
        if ctx.haveCertificate(afResourceId):
            raise ProblemException(status_code=409, title='Certificate Already Exists', instance=request.url.path)
        if ctx.wasCertificateDestroyed(afResourceId):
            raise ProblemException(status_code=410, title='Certificate Gone', instance=request.url.path)
        ctx.appLog().info("Adding certificate %s..."%(afResourceId))
        ctx.addCertificate(afResourceId, body)
        raise NoProblemException(status_code=201, media_type='application/json', headers={'Location': str(request.url)})

    async def replace_server_certificate(self, afResourceId, body, request=None):
        '''M3 Handler for "PUT .../certificates/{afResourceId}" (clause 4.5.2.5)

        Responds with 200 and an empty body, 204 if the PEM bundle is identical, 410 if destroyed before, 404 if it never existed.
        '''
        ctx = self.__ready(request)
        self.__check_id(request, afResourceId)
        if not ctx.haveCertificate(afResourceId):
            self.__gone_or_missing(request, ctx.wasCertificateDestroyed(afResourceId), 'Server Certificate')
        ctx.appLog().info("Attempting to update certificate %s..."%(afResourceId))
        if not ctx.updateCertificate(afResourceId, body):
            raise NoProblemException(status_code=204, media_type='application/json')
        # Certificate changed, reload proxy server to pick up new cert
        await ctx.webProxy().reloadAndWait()
        raise NoProblemException(status_code=200, media_type='application/json')

    async def destroy_server_certificate(self, afResourceId, request=None):
        '''M3 Handler for "DELETE .../certificates/{afResourceId}" (clause 4.5.2.6)

        Responds with 204; 409 if still referenced; 410 if destroyed before, 404 if it never existed.
        '''
        ctx = self.__ready(request)
        self.__check_id(request, afResourceId)
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
