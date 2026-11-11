'''OAuth 2.0 authorisation of the M3 interface (TS 26.512 V19.3.0 clause 6.6.3)

Independently of CAPIF the 5GMS AS plays the role of both authorization server and resource server and the 5GMS AF is the client: the AF
obtains an access token with the client credentials grant (RFC 6749 clause 4.4) and presents it as a bearer token (RFC 6750). The token
endpoint is ``{apiRoot}/oauth2/token`` of the M3 server. The clients are configured in the application server configuration file::

    [5gms_as.oauth]
    token_lifetime = 3600
    [5gms_as.oauth.client.af]
    secret = change-me
    scopes = *

Without a ``[5gms_as.oauth]`` section every request is accepted, as the empty security requirement of the OpenAPI definitions allows.
'''

import base64
import binascii
import re
import secrets
import time
import urllib.parse
from typing import Dict, List, Optional, Set, Tuple

from fastapi.responses import JSONResponse

from .oauth_scopes import OPERATION_SCOPES

_API_PREFIX = '/3gpp-mas-configuration/v1'
_REALM = 'Bearer realm="5GMS AS"'
_MAX_TOKENS = 4096

_CLIENT_SECTION = '5gms_as.oauth.client.'


def _compile(path: str) -> 're.Pattern':
    return re.compile('^' + re.sub(r'\\\{[^}]+\\\}', '[^/]+', re.escape(path)) + '$')


_OPERATIONS: List[Tuple[str, 're.Pattern', Optional[str]]] = [(m, _compile(p), s) for m, p, s in OPERATION_SCOPES]


def _same(a: str, b: str) -> bool:
    '''Constant-time comparison of two strings; secrets.compare_digest raises TypeError for str with non-ASCII characters, which would be a 500 instead of 401'''
    return secrets.compare_digest(a.encode('utf-8', 'surrogatepass'), b.encode('utf-8', 'surrogatepass'))


class Authorizer:
    '''The authorization server and the check of the resource server'''

    def __init__(self, context):
        self.__context = context
        self.__tokens: Dict[str, Tuple[Set[str], bool, float]] = {}

    def __clients(self) -> Dict[str, Tuple[str, Set[str]]]:
        clients = {}
        for section in self.__context.getConfigSections(_CLIENT_SECTION):
            client_id = section[len(_CLIENT_SECTION):]
            secret = self.__context.getConfigVar(section, 'secret')
            scopes = (self.__context.getConfigVar(section, 'scopes', '') or '').split()
            if client_id and secret:
                clients[client_id] = (secret, set(scopes))
        return clients

    def enabled(self) -> bool:
        '''Whether authorisation is configured'''
        return self.__context.hasConfigSection('5gms_as.oauth')

    @staticmethod
    def __problem(status: int, title: str, detail: str, challenge: Optional[str] = None) -> JSONResponse:
        headers = {'WWW-Authenticate': challenge} if challenge else None
        return JSONResponse(status_code=status, content={'title': title, 'status': status, 'detail': detail}, headers=headers,
                            media_type='application/problem+json')

    @staticmethod
    def __token_error(status: int, error: str, description: str, challenge: Optional[str] = None) -> JSONResponse:
        headers = {'Cache-Control': 'no-store', 'Pragma': 'no-cache'}
        if challenge:
            headers['WWW-Authenticate'] = challenge
        return JSONResponse(status_code=status, content={'error': error, 'error_description': description}, headers=headers)

    def __sweep(self) -> None:
        now = time.monotonic()
        for value in [k for k, (_, _, expires) in self.__tokens.items() if expires <= now]:
            del self.__tokens[value]

    def check(self, method: str, path: str, authorization: Optional[str]) -> Optional[JSONResponse]:
        '''Check the access token of a request to the M3 API; returns the answer to send (401 or 403) or `None` if it may go on'''
        if not self.enabled():
            return None
        if authorization is None or authorization[:7].lower() != 'bearer ':
            # RFC 6750 clause 3.1: no error code when the request lacks any authentication information
            return self.__problem(401, 'Unauthorized', 'An OAuth 2.0 access token is required', _REALM)
        self.__sweep()
        token = None
        presented = authorization[7:].strip()
        for value, entry in self.__tokens.items():
            if _same(value, presented):
                token = entry
        if token is None:
            return self.__problem(401, 'Unauthorized', 'The access token is invalid or has expired', _REALM + ', error="invalid_token"')
        scopes, all_scopes, _ = token
        sub = path[len(_API_PREFIX):] if path.startswith(_API_PREFIX) else path
        sub = sub.rstrip('/')
        for op_method, pattern, scope in _OPERATIONS:
            if op_method == method and pattern.match(sub):
                if scope is not None and not all_scopes and scope not in scopes:
                    return self.__problem(403, 'Forbidden', 'The access token does not carry the scope of the operation',
                                          _REALM + f', error="insufficient_scope", scope="{scope}"')
                break
        return None

    def token_endpoint(self, method: str, content_type: Optional[str], body: bytes, authorization: Optional[str]) -> JSONResponse:
        '''The token endpoint (RFC 6749 clause 3.2), client credentials grant only'''
        if method != 'POST':
            # RFC 9110 clause 15.5.6: a 405 carries an Allow header field with the methods of the resource
            response = self.__problem(405, 'Method Not Allowed', 'The token endpoint takes POST')
            response.headers['Allow'] = 'POST'
            return response
        if not self.enabled():
            return self.__problem(404, 'Not Found', 'OAuth 2.0 authorisation is not configured')
        if not content_type or content_type.split(';')[0].strip().lower() != 'application/x-www-form-urlencoded':
            return self.__token_error(400, 'invalid_request', 'The body has to be application/x-www-form-urlencoded')
        form = {k: v[0] for k, v in urllib.parse.parse_qs(body.decode('utf-8', 'replace'), keep_blank_values=True).items()}
        if 'grant_type' not in form:
            return self.__token_error(400, 'invalid_request', 'grant_type is missing')
        if form['grant_type'] != 'client_credentials':
            return self.__token_error(400, 'unsupported_grant_type', 'Only client_credentials is supported')

        client_id = secret = None
        if authorization and authorization[:6].lower() == 'basic ':
            try:
                client_id, _, secret = base64.b64decode(authorization[6:].strip()).decode('utf-8').partition(':')
            except (binascii.Error, UnicodeDecodeError):
                pass
        else:
            client_id, secret = form.get('client_id'), form.get('client_secret')
        clients = self.__clients()
        known = clients.get(client_id or '')
        if known is None or secret is None or not _same(known[0], secret):
            return self.__token_error(401, 'invalid_client', 'Client authentication failed', 'Basic realm="5GMS AS"')

        allowed = known[1]
        if form.get('scope'):
            requested = form['scope'].split()
            if '*' not in allowed and not set(requested) <= allowed:
                return self.__token_error(400, 'invalid_scope', 'A requested scope is not granted to the client')
            granted = set(requested)
        else:
            granted = set(allowed)
        lifetime = int(self.__context.getConfigVar('5gms_as.oauth', 'token_lifetime', '3600'))
        self.__sweep()
        while len(self.__tokens) >= _MAX_TOKENS:
            del self.__tokens[next(iter(self.__tokens))]
        value = secrets.token_hex(32)
        self.__tokens[value] = (granted, '*' in granted, time.monotonic() + lifetime)
        return JSONResponse(content={'access_token': value, 'token_type': 'Bearer', 'expires_in': lifetime,
                                     'scope': ' '.join(sorted(granted))},
                            headers={'Cache-Control': 'no-store', 'Pragma': 'no-cache'})
