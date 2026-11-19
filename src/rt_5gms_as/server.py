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
from .schema_checks import content_hosting_configuration_min_items, content_hosting_configuration_value_problems, purge_form_problem
from .proxy_factory import WebProxyError, WebProxyRegexTimeout
from .utils import encode_resource_id, MAX_ENCODED_RESOURCE_ID
from typing import Tuple

import base64
import binascii
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

    @staticmethod
    async def __no_body(request):
        # the YAML (413) and clauses 4.5.x.2/4.5.4.8: no request body is allowed for this operation
        if len(await request.body()) > 0:
            raise ProblemException(status_code=413, title='Content Too Large', detail='No request body allowed', instance=request.url.path)

    __PEM_BLOCK = regex.compile(r'-----BEGIN ([A-Z0-9 ]+)-----\s*([A-Za-z0-9+/=\s]*?)\s*-----END \1-----')

    def __check_pem(self, request, body):
        '''Reject a body that is not a PEM bundle with at least one certificate and a private key (clauses 4.5.2.3 and 4.5.2.5)'''
        labels = []
        for match in self.__PEM_BLOCK.finditer(body or ''):
            try:
                der = base64.b64decode(''.join(match.group(2).split()), validate=True)
            except binascii.Error:
                der = b''
            if len(der) > 0 and der[0] == 0x30:
                labels.append(match.group(1))
        problem = None
        if 'CERTIFICATE' not in labels:
            problem = 'The PEM bundle has to contain an X.509 certificate'
        elif not any(label.endswith('PRIVATE KEY') for label in labels):
            problem = 'The PEM bundle has to contain the private key'
        if problem is not None:
            raise ProblemException(status_code=400, title='Bad Request', detail=problem, instance=request.url.path, invalid_params=[{'param': 'body', 'reason': problem}])

    @staticmethod
    async def __reload(ctx, what):
        '''Reload the web proxy; True only when the new configuration is in use (clauses 4.5.2.5, 4.5.4.3, 4.5.4.5 and 4.5.4.7: success is returned only for a configuration that is applied)

        A reload that fails, that raises an exception or that is not finished in time counts as not applied.
        '''
        try:
            return await ctx.webProxy().reloadAndWait() is True
        except Exception as err:
            ctx.appLog().error("The web proxy could not be reloaded for %s: %s", what, err)
            return False

    @staticmethod
    def __valid_id(resource_id):
        # TS 26.512 V19.3.0 clause 6.4.2: any string chosen by the AF; only what the file system of the AS cannot name is refused
        try:
            return 0 < len(encode_resource_id(resource_id)) <= MAX_ENCODED_RESOURCE_ID
        except UnicodeError:
            return False

    def __check_schema(self, request, content_hosting_configuration):
        '''Reject a Content Hosting Configuration that breaks a constraint the generated models do not enforce

        TS 29.500 clause 5.2.7.2: a request that does not comply with the schema is rejected with 400 (INVALID_MSG_FORMAT).
        '''
        if content_hosting_configuration is None:
            raise ProblemException(status_code=400, title='Bad Request', detail='The request body is missing', instance=request.url.path)
        problems = content_hosting_configuration_min_items(content_hosting_configuration) + \
            content_hosting_configuration_value_problems(content_hosting_configuration)
        if problems:
            problem = ProblemException(status_code=400, title='Bad Request', detail='The request does not comply with the schema', instance=request.url.path, invalid_params=problems)
            problem.object['cause'] = 'INVALID_MSG_FORMAT'
            raise problem

    def __check_id(self, request, af_resource_id):
        # the identifier is used in a file name (certificates, templates): the encoded form has to fit in one
        if not self.__valid_id(af_resource_id):
            raise ProblemException(status_code=400, title='Bad Request', detail='The resource identifier is not valid', instance=request.url.path, invalid_params=[{'param': 'afResourceId', 'reason': 'The percent-encoded identifier is longer than %d characters' % MAX_ENCODED_RESOURCE_ID}])

    @staticmethod
    def __cannot_provision(request, what):
        # a Content Publishing Configuration (uplink) is one that this 5GMSd AS cannot realise: the OpenAPI response table lists 500
        raise ProblemException(status_code=500, title='Internal Server Error', detail='%s cannot be provisioned by this 5GMS AS'%what, instance=request.url.path)

    @staticmethod
    def __gone_or_missing(request, destroyed, what):
        # TS 26.512 clause 4.5.4.5 and others: 410 if the resource previously existed and was destroyed, otherwise 404
        if destroyed:
            raise ProblemException(status_code=410, title='%s Gone'%what, instance=request.url.path)
        raise ProblemException(status_code=404, title='%s Not Found'%what, instance=request.url.path)

    def missing_resource_problem(self, request):
        '''The 410 or 404 problem for a PUT or POST to a Content Hosting Configuration or certificate that does not exist, otherwise None

        The clauses 4.5.2.5, 4.5.4.5, 4.5.4.7 and 4.5.4.9 name 410 and 404 for a resource that is not there; this is answered before the
        body is judged (media type, schema), like the PUT of a Content Preparation Template. The framework calls this when it rejects a body.
        '''
        ctx = self.__context
        parts = request.url.path.rstrip('/').split('/')
        if ctx is None or len(parts) < 4:
            return None
        if request.method == 'PUT' and parts[-2] == 'certificates':
            rid, have, destroyed, what = parts[-1], ctx.haveCertificate, ctx.wasCertificateDestroyed, 'Server Certificate'
        elif request.method == 'PUT' and parts[-2] == 'content-hosting-configurations':
            rid, have, destroyed, what = parts[-1], ctx.haveContentHostingConfiguration, ctx.wasContentHostingConfigurationDestroyed, 'Content Hosting Configuration'
        elif request.method == 'POST' and parts[-3] == 'content-hosting-configurations' and parts[-1] in ('active', 'purge'):
            rid, have, destroyed, what = parts[-2], ctx.haveContentHostingConfiguration, ctx.wasContentHostingConfigurationDestroyed, 'Content Hosting Configuration'
        else:
            return None
        if not self.__valid_id(rid) or have(rid):
            return None
        try:
            self.__gone_or_missing(request, destroyed(rid), what)
        except ProblemException as problem:
            return problem

    #### Content Hosting Configurations (TS 26.512 clause 4.5.4) ####
    async def enumerate_content_hosting_configurations(self, request=None):
        '''M3 Handler for "GET .../content-hosting-configurations"

        Responds with a JSON array of Content Hosting Configuration resource identifiers (clause 4.5.4.2).
        '''
        ctx = self.__ready(request)
        await self.__no_body(request)
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
                ok = await wp.reloadAndWait() is True
        except Exception as err:
            ctx.appLog().error("The web proxy could not use the Content Hosting Configuration %s: %s", afResourceId, err)
            ok = False
        if not ok:
            ctx.deleteContentHostingConfiguration(afResourceId)
            ctx.forgetDestroyedContentHostingConfiguration(afResourceId)
            await self.__reload(ctx, afResourceId)
            raise ProblemException(status_code=500, title='Internal Server Error', detail='The web proxy cannot realise this Content Hosting Configuration', instance=request.url.path)
        raise NoProblemException(status_code=201, media_type='application/json', headers={'Location': str(request.url)})

    async def replace_content_hosting_configuration(self, afResourceId, content_hosting_configuration, request=None):
        '''M3 Handler for "PUT .../content-hosting-configurations/{afResourceId}" (clause 4.5.4.5)

        Responds with 200 and an empty body, 204 if unchanged, 400 if a referenced resource is invalid (state kept),
        410 if destroyed before, 404 if it never existed.
        '''
        ctx = self.__ready(request)
        self.__check_id(request, afResourceId)
        # clause 4.5.4.5: 410 or 404 for a resource that is not there, whatever the body is
        old_chc = ctx.findContentHostingConfigurationByProvisioningSession(afResourceId)
        if old_chc is None:
            self.__gone_or_missing(request, ctx.wasContentHostingConfigurationDestroyed(afResourceId), 'Content Hosting Configuration')
        self.__check_schema(request, content_hosting_configuration)
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
        if not await self.__reload(ctx, afResourceId):
            # the update is not kept: the state is the one immediately before the operation
            ctx.updateContentHostingConfiguration(afResourceId, previous)
            await self.__reload(ctx, afResourceId)
            raise ProblemException(status_code=500, title='Internal Server Error', detail='The web proxy cannot realise this Content Hosting Configuration', instance=request.url.path)
        raise NoProblemException(status_code=200, media_type='application/json')

    async def destroy_content_hosting_configuration(self, afResourceId, request=None):
        '''M3 Handler for "DELETE .../content-hosting-configurations/{afResourceId}" (clause 4.5.4.6)

        Responds with 204; 410 if destroyed before, 404 if it never existed.
        '''
        ctx = self.__ready(request)
        await self.__no_body(request)
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
            reloaded = await wp.reloadAndWait() is True
        except Exception as err:
            ctx.appLog().error("The web proxy could not release the Content Hosting Configuration %s: %s", afResourceId, err)
            reloaded = False
        if not reloaded:
            raise ProblemException(status_code=500, title='Internal Server Error', detail='The Content Hosting Configuration is destroyed but the web proxy could not release it', instance=request.url.path)
        raise NoProblemException(status_code=204, media_type='application/json')

    async def interrogate_content_hosting_configuration_state(self, afResourceId, request=None):
        '''M3 Handler for "GET .../content-hosting-configurations/{afResourceId}/active" (clause 4.5.4.8)

        Responds with 200 and a JSON boolean (true for active); 410 if destroyed before, 404 if it never existed.
        '''
        ctx = self.__ready(request)
        self.__check_id(request, afResourceId)
        await self.__no_body(request)
        if not ctx.haveContentHostingConfiguration(afResourceId):
            self.__gone_or_missing(request, ctx.wasContentHostingConfigurationDestroyed(afResourceId), 'Content Hosting Configuration')
        return ctx.isContentHostingConfigurationActive(afResourceId)

    async def modify_content_hosting_configuration_state(self, afResourceId, body, request=None):
        '''M3 Handler for "POST .../content-hosting-configurations/{afResourceId}/active" (clause 4.5.4.7)

        The body is a single JSON boolean, true for active. The state changes at once (204, also when it is in the
        requested state already). Clause 4.5.4.7 allows 202 for a change in progress, but the OpenAPI response table lists 204 only and
        takes precedence (TS 26.512 annex C.1), so 202 is not used.
        410 if destroyed before, 404 if it never existed, 400 if the body is not a boolean, 415 if it is not application/json.
        '''
        ctx = self.__ready(request)
        self.__check_id(request, afResourceId)
        if not ctx.haveContentHostingConfiguration(afResourceId):
            self.__gone_or_missing(request, ctx.wasContentHostingConfigurationDestroyed(afResourceId), 'Content Hosting Configuration')
        if self.__media_type(request) != 'application/json':
            raise ProblemException(status_code=415, title='Invalid media type', instance=request.url.path)
        # the clause asks for a single JSON boolean: the framework would also accept strings such as "yes", so the body is read again
        try:
            desired = await request.json()
        except ValueError:
            desired = None
        if not isinstance(desired, bool):
            raise ProblemException(status_code=400, title='Bad Request', detail='The request body has to be a JSON boolean', instance=request.url.path)
        if ctx.setContentHostingConfigurationActive(afResourceId, desired):
            ctx.appLog().info("Content hosting configuration %s is now %s"%(afResourceId, 'active' if desired else 'inactive'))
            if not await self.__reload(ctx, afResourceId):
                # the stored state follows what the proxy serves
                ctx.setContentHostingConfigurationActive(afResourceId, not desired)
                await self.__reload(ctx, afResourceId)
                raise ProblemException(status_code=500, title='Internal Server Error', detail='The web proxy could not be reloaded', instance=request.url.path)
        raise NoProblemException(status_code=204, media_type='application/json')

    async def purge_content_hosting_cache(self, afResourceId, pattern, value=None, request=None):
        '''M3 Handler for "POST .../content-hosting-configurations/{afResourceId}/purge" (clause 4.5.4.9)

        Body: application/x-www-form-urlencoded with one key-value pair, pattern=<regular expression>.

        Responds with 200 and the number of cache entries purged, 204 if none, 400 if the body or the expression is
        malformed, 410 if the configuration was destroyed, 404 if it never existed.
        '''
        ctx = self.__ready(request)
        self.__check_id(request, afResourceId)
        if not ctx.haveContentHostingConfiguration(afResourceId):
            self.__gone_or_missing(request, ctx.wasContentHostingConfigurationDestroyed(afResourceId), 'Content Hosting Configuration')
        if self.__media_type(request) != 'application/x-www-form-urlencoded':
            raise ProblemException(status_code=415, title='Invalid media type', instance=request.url.path)
        body = getattr(request.state, 'raw_body', None)
        problem = purge_form_problem(body) if body is not None else None
        if problem is not None:
            raise ProblemException(status_code=400, title='Bad Request', detail=problem, instance=request.url.path, invalid_params=[{'param': 'body', 'reason': problem}])
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
        await self.__no_body(request)
        ctx.appLog().info("Getting list of certificates...")
        return list(ctx.getCertificateIds())

    async def create_server_certificate(self, afResourceId, body, request=None):
        '''M3 Handler for "POST .../certificates/{afResourceId}" (clause 4.5.2.3)

        Content-Type: application/x-pem-file
        Body contains PEM file contents (public certificate, private key and intermediate certificates)

        Responds with 201 and an empty body, 400 if the body is not a PEM bundle, 409 if it exists, 410 if destroyed before.
        '''
        ctx = self.__ready(request)
        self.__check_id(request, afResourceId)
        if self.__media_type(request) != 'application/x-pem-file':
            raise ProblemException(status_code=415, title='Invalid media type', instance=request.url.path)
        self.__check_pem(request, body)
        if ctx.haveCertificate(afResourceId):
            raise ProblemException(status_code=409, title='Certificate Already Exists', instance=request.url.path)
        if ctx.wasCertificateDestroyed(afResourceId):
            raise ProblemException(status_code=410, title='Certificate Gone', instance=request.url.path)
        ctx.appLog().info("Adding certificate %s..."%(afResourceId))
        ctx.addCertificate(afResourceId, body)
        raise NoProblemException(status_code=201, media_type='application/json', headers={'Location': str(request.url)})

    async def replace_server_certificate(self, afResourceId, body, request=None):
        '''M3 Handler for "PUT .../certificates/{afResourceId}" (clause 4.5.2.5)

        Responds with 200 and an empty body, 204 if the PEM bundle is identical, 400 if the body is not a PEM bundle,
        410 if destroyed before, 404 if it never existed.
        '''
        ctx = self.__ready(request)
        self.__check_id(request, afResourceId)
        if not ctx.haveCertificate(afResourceId):
            self.__gone_or_missing(request, ctx.wasCertificateDestroyed(afResourceId), 'Server Certificate')
        if self.__media_type(request) != 'application/x-pem-file':
            raise ProblemException(status_code=415, title='Invalid media type', instance=request.url.path)
        self.__check_pem(request, body)
        ctx.appLog().info("Attempting to update certificate %s..."%(afResourceId))
        previous = ctx.certificateContents(afResourceId)
        if not ctx.updateCertificate(afResourceId, body):
            raise NoProblemException(status_code=204, media_type='application/json')
        # Certificate changed, reload proxy server to pick up new cert
        if not await self.__reload(ctx, afResourceId):
            # the new bundle is not kept: the state is the one immediately before the operation
            ctx.updateCertificate(afResourceId, previous)
            await self.__reload(ctx, afResourceId)
            raise ProblemException(status_code=500, title='Internal Server Error', detail='The web proxy cannot use this certificate', instance=request.url.path)
        raise NoProblemException(status_code=200, media_type='application/json')

    async def destroy_server_certificate(self, afResourceId, request=None):
        '''M3 Handler for "DELETE .../certificates/{afResourceId}" (clause 4.5.2.6)

        Responds with 204; 409 if still referenced; 410 if destroyed before, 404 if it never existed.
        '''
        ctx = self.__ready(request)
        await self.__no_body(request)
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

    #### Content Preparation Templates (TS 26.512 clause 4.5.3) ####
    async def __template_payload(self, request):
        # a template is of any media type the AS is given (binary too, "*/*" in the OpenAPI file): the raw body is stored with its Content-Type
        # (RFC 9110 clause 8.3: a body without a Content-Type is taken as application/octet-stream)
        media_type = request.headers.get('content-type', '').strip() or 'application/octet-stream'
        body = await request.body()
        if len(body) == 0:
            raise ProblemException(status_code=400, title='Bad Request', detail='The request body is missing', instance=request.url.path, invalid_params=[{'param': 'body', 'reason': 'empty'}])
        return body, media_type

    async def enumerate_content_preparation_templates(self, request=None):
        '''M3 Handler for "GET .../content-preparation-templates": a JSON array of resource identifiers (clause 4.5.3.2)'''
        ctx = self.__ready(request)
        await self.__no_body(request)
        return ctx.contentPreparationTemplates().ids()

    async def create_content_preparation_template(self, afResourceId, body, request=None):
        '''M3 Handler for "POST .../content-preparation-templates/{afResourceId}" (clause 4.5.3.3)

        Responds with 201 and an empty body, 409 if it exists, 410 if destroyed before.
        '''
        ctx = self.__ready(request)
        self.__check_id(request, afResourceId)
        store = ctx.contentPreparationTemplates()
        payload, media_type = await self.__template_payload(request)
        if store.have(afResourceId):
            raise ProblemException(status_code=409, title='Content Preparation Template Already Exists', instance=request.url.path)
        if store.wasDestroyed(afResourceId):
            raise ProblemException(status_code=410, title='Content Preparation Template Gone', instance=request.url.path)
        ctx.appLog().info("Adding content preparation template %s..."%afResourceId)
        store.put(afResourceId, payload, media_type)
        raise NoProblemException(status_code=201, media_type='application/json', headers={'Location': str(request.url)})

    async def replace_content_preparation_template(self, afResourceId, body, request=None):
        '''M3 Handler for "PUT .../content-preparation-templates/{afResourceId}" (clause 4.5.3.5)

        Responds with 200, 204 if unchanged, 410 if destroyed before, 404 if it never existed.
        '''
        ctx = self.__ready(request)
        self.__check_id(request, afResourceId)
        store = ctx.contentPreparationTemplates()
        # clause 4.5.3.5: 410 or 404 for a resource that is not there, whatever the body is
        if not store.have(afResourceId):
            self.__gone_or_missing(request, store.wasDestroyed(afResourceId), 'Content Preparation Template')
        payload, media_type = await self.__template_payload(request)
        ctx.appLog().info("Updating content preparation template %s..."%afResourceId)
        if not store.put(afResourceId, payload, media_type):
            raise NoProblemException(status_code=204, media_type='application/json')
        raise NoProblemException(status_code=200, media_type='application/json')

    async def destroy_content_preparation_template(self, afResourceId, request=None):
        '''M3 Handler for "DELETE .../content-preparation-templates/{afResourceId}" (clause 4.5.3.6)

        Responds with 204; 409 if a Content Hosting Configuration references it; 410 if destroyed before, 404 if never existed.
        '''
        ctx = self.__ready(request)
        self.__check_id(request, afResourceId)
        await self.__no_body(request)
        store = ctx.contentPreparationTemplates()
        if not store.have(afResourceId):
            self.__gone_or_missing(request, store.wasDestroyed(afResourceId), 'Content Preparation Template')
        if ctx.isContentPreparationTemplateInUse(afResourceId):
            raise ProblemException(status_code=409, title='Content Preparation Template still in use', detail='A Content Hosting Configuration refers to this template', instance=request.url.path)
        ctx.appLog().info("Deleting content preparation template %s..."%afResourceId)
        store.delete(afResourceId)
        raise NoProblemException(status_code=204, media_type='application/json')

    #### Content Publishing Configurations (clause 4.5.5, uplink): this AS holds none ####
    async def enumerate_content_publishing_configurations(self, request=None):
        '''GET .../content-publishing-configurations: this AS holds none (clause 4.5.5.2: an empty array)'''
        self.__ready(request)
        return []

    async def create_content_publishing_configuration(self, afResourceId, content_publishing_configuration, request=None):
        self.__ready(request)
        self.__cannot_provision(request, 'A Content Publishing Configuration')

    # no resource ever existed at the other URLs: 404 (clauses 4.5.5.5 to 4.5.5.9)
    async def replace_content_publishing_configuration(self, afResourceId, content_publishing_configuration, request=None):
        self.__ready(request)
        self.__gone_or_missing(request, False, 'Content Publishing Configuration')

    async def destroy_content_publishing_configuration(self, afResourceId, request=None):
        self.__ready(request)
        self.__gone_or_missing(request, False, 'Content Publishing Configuration')

    async def interrogate_content_publishing_configuration_state(self, afResourceId, request=None):
        self.__ready(request)
        self.__gone_or_missing(request, False, 'Content Publishing Configuration')

    async def modify_content_publishing_configuration_state(self, afResourceId, body, request=None):
        self.__ready(request)
        self.__gone_or_missing(request, False, 'Content Publishing Configuration')

    async def purge_content_publishing_cache(self, afResourceId, pattern, value=None, request=None):
        self.__ready(request)
        self.__gone_or_missing(request, False, 'Content Publishing Configuration')

server = M3Server()
