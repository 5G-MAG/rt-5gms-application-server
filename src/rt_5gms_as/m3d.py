#!/usr/bin/python3
#
# 5G-MAG Reference Tools: 5GMS Application Server
# ===============================================
#
# File: m3d.py
# License: 5G-MAG Public License (v1.0)
#
# For full license terms please see the LICENSE file distributed with this
# program. If this file is missing then the license can be retrieved from
# https://drive.google.com/file/d/1cinCiA778IErENZ3JN52VFW-1ffHpx7Z/view
#
# The 5GMSd AS as a client of the 5GMSd AF at reference point M3d (TS 26.512 V19.3.0 clauses 4.5.6, 4.5.9 and 11.4, TS 26.510 V19.2.0
# clauses 5.3.2, 5.3.5, 7.4.3 and 9.5):
#   - obtains the Service Access Information of each Content Hosting Configuration from the AF, by its external service identifier,
#   - reports the CMCD information that M4d clients send, as the Service Access Information asks, with the JSON metrics report envelope,
#   - authenticates to the AF with the OAuth 2.0 client credentials grant when the AS is configured with client credentials.
#
# The web proxy hands the CMCD information of M4d requests over a Unix domain socket (proxies/cmcd_samples.lua): one JSON object per line.
'''
5gms_as.m3d module
=================

Configuration, in the section ``[5gms_as.m3d]`` of the application configuration file (nothing happens without ``af_url``)::

    af_url = http://127.0.0.26:7777        # the M3d address of the AF (its configuration key msaf.m3)
    oauth_client_id = as1                  # client credentials at the token endpoint of the AF; without them no token is sent
    oauth_client_secret = change-me
    oauth_scope = service-access-information_retrieve metrics-report_submit
    oauth_token_url = http://127.0.0.26:7777/oauth2/token    # default: af_url + /oauth2/token
    session_idle_timeout = 60              # seconds without a request after which a media delivery session is taken as ended
    samples_socket = /run/rt-5gms/m3d-cmcd.sock
    samples_socket_mode = 666              # the web proxy workers may run as another user than the AS
'''

import asyncio
import json
import logging
import os
import random
import re
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx
import regex

API_PATH = '/3gpp-maf-session-handling/v1'
CMCD_SCHEME_PREFIX = 'urn:3gpp:5gms:metrics:common-media-client-data:'
CMCD_TERM_PREFIX = 'urn:3gpp:5gms:event-exposure:common-media-client-data#'
REPORT_MEDIA_TYPE = 'application/3gpp-media-delivery-metrics-report+json;version=Rel19'

# the polling period of the Service Access Information follows Expires and Cache-Control: max-age of the responses (TS 26.510 clause 5.3.2.3);
# these are used when a response has neither, and as the lowest period
DEFAULT_POLL_SECONDS = 60
MIN_POLL_SECONDS = 5
# limits that keep the memory of a reporter bounded
MAX_SESSIONS = 10000
MAX_PENDING_SAMPLES = 500
MAX_RETRY_QUEUE = 100
RETRY_TRIES = 3
RETRY_SECONDS = 30
MAX_SAMPLE_LINE = 64 * 1024


#### CMCD (CTA-5004 version 1) ####

_CMCD_PAIR = re.compile(r'\s*([A-Za-z][A-Za-z0-9-]*)(?:=("(?:[^"\\]|\\.)*"|[^,]*))?\s*(?:,|$)')


def parse_cmcd(text: str) -> Dict[str, Any]:
    '''The key-value pairs of a CMCD payload (CTA-5004 clause 3.1): a value is a string without its quotation marks, a key without a value is True'''
    result: Dict[str, Any] = {}
    pos = 0
    text = text or ''
    while pos < len(text):
        match = _CMCD_PAIR.match(text, pos)
        if match is None or match.end() == pos:
            break
        key, value = match.group(1), match.group(2)
        if value is None:
            result[key] = True
        elif value.startswith('"') and value.endswith('"') and len(value) >= 2:
            result[key] = re.sub(r'\\(.)', r'\1', value[1:-1])
        else:
            result[key] = value.strip()
        pos = match.end()
    return result


def _int(value: Any, minimum: Optional[int] = None) -> Optional[int]:
    if isinstance(value, str) and re.fullmatch(r'-?[0-9]+', value):
        number = int(value)
        return number if minimum is None or number >= minimum else None
    return None


def _number(value: Any) -> Optional[float]:
    if isinstance(value, str) and re.fullmatch(r'-?[0-9]+(\.[0-9]+)?', value):
        number = float(value)
        return int(number) if number == int(number) else number
    return None


def _flag(value: Any) -> Optional[bool]:
    # a key without a value is true (CTA-5004 clause 3.2)
    if value is True or value == 'true':
        return True
    return False if value == 'false' else None


def _text(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value != '' else None


def _enum(*allowed: str) -> Callable[[Any], Optional[str]]:
    return lambda value: value if isinstance(value, str) and value in allowed else None


# RFC 3986 clause 4.2 relative-ref, as far as characters go: unreserved, sub-delims, ":", "@", "/", "?", "#" and percent-encoded octets
_RELATIVE_REF = re.compile(r"(?:[A-Za-z0-9._~!$&'()*+,;=:@/?#-]|%[0-9A-Fa-f]{2})*")


def _relative_ref(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value != '' and _RELATIVE_REF.fullmatch(value) else None


# the keys of each class of CMCD information and their types (TS 26.512 tables 11.4.3.3.2-1 to 11.4.3.3.2-7, TS26512_ClientData.yaml)
CMCD_CLASSES: Dict[str, Dict[str, Callable[[Any], Any]]] = {
    'session': {'v': lambda x: _int(x, 1), 'sid': _text, 'cid': _text, 'st': _enum('v', 'l'), 'sf': _enum('d', 'h', 's', 'o'), 'pr': _number},
    'object': {'ot': _enum('m', 'a', 'v', 'av', 'i', 'c', 'tt', 'k', 'o'), 'd': _int, 'br': _int, 'tb': _int},
    'request': {'su': _flag, 'mtp': _int, 'dl': _int, 'bl': _int, 'nor': _relative_ref, 'nrr': _text},
    'status': {'rtp': _int, 'bs': _flag},
}
_CLASS_PROPERTY = {'session': 'cmcdSessionInfo', 'object': 'cmcdObjectInfo', 'request': 'cmcdRequestInfo', 'status': 'cmcdStatusInfo'}
_CMCD_HEADERS = ('CMCD-Object', 'CMCD-Request', 'CMCD-Status', 'CMCD-Session')


def collect_cmcd(query: Optional[str], headers: Optional[Dict[str, str]]) -> Dict[str, Any]:
    '''The CMCD key-value pairs of a request: the CMCD query argument and the CMCD request headers (CTA-5004 clause 2)'''
    pairs: Dict[str, Any] = {}
    for value in urllib.parse.parse_qs(query or '', keep_blank_values=True).get('CMCD', []):
        pairs.update(parse_cmcd(value))
    lowered = {str(k).lower(): v for k, v in (headers or {}).items()}
    for name in _CMCD_HEADERS:
        if isinstance(lowered.get(name.lower()), str):
            pairs.update(parse_cmcd(lowered[name.lower()]))
    return pairs


def client_data(cmcd_class: str, pairs: Dict[str, Any], keys: Optional[set]) -> Optional[Dict[str, Any]]:
    '''The CMCD information object of a class (TS 26.512 clause 11.4.3.3.2), in alphabetical order of key name; None if there is nothing to report

    keys: the keys that the metrics reporting configuration lists (None: all of the class). A key whose value does not have the type of the
    table is left out. The session class always has the key sid (required by the OpenAPI definition).
    '''
    table = CMCD_CLASSES[cmcd_class]
    result = {}
    for key in sorted(table):
        if keys is not None and key not in keys and not (cmcd_class == 'session' and key == 'sid'):
            continue
        if key in pairs:
            value = table[key](pairs[key])
            if value is not None:
                result[key] = value
    if cmcd_class == 'session' and 'sid' not in result:
        return None
    return result or None


#### Service Access Information ####

class ReportConfig(object):
    '''A client metrics reporting configuration of a CMCD scheme (TS 26.510 table 9.2.3.1-1)'''
    def __init__(self, provisioning_session_id: str, entry: Dict[str, Any]):
        self.provisioning_session_id = provisioning_session_id
        self.id = str(entry['metricsReportingConfigurationId'])
        self.server_addresses = [str(a) for a in entry['serverAddresses']]
        self.cmcd_class = str(entry['scheme'])[len(CMCD_SCHEME_PREFIX):]
        self.sample_percentage = float(entry['samplePercentage'])
        self.interval = self.__seconds(entry.get('reportingInterval'), 0)
        self.start_offset = self.__seconds(entry.get('reportingStartOffset')) or 0
        self.duration = self.__seconds(entry.get('reportingDuration'))
        self.sampling_period = self.__seconds(entry.get('samplingPeriod')) or 0
        self.url_filters = [str(p) for p in entry.get('urlFilters') or []]
        self.keys = self.__keys(self.cmcd_class, entry.get('metrics'))
        if self.cmcd_class not in CMCD_CLASSES or not self.server_addresses or not 0 <= self.sample_percentage <= 100:
            raise ValueError('not a client metrics reporting configuration of a CMCD scheme')

    @staticmethod
    def __seconds(value: Any, minimum: float = -1) -> Optional[float]:
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > minimum:
            return float(value)
        return None

    @staticmethod
    def __keys(cmcd_class: str, metrics: Any) -> Optional[set]:
        # the metrics are terms of the vocabulary of TS 26.512 clause E.2.5: class and key, without a list all keys of the scheme
        if not isinstance(metrics, list) or not metrics:
            return None
        keys = set()
        for term in metrics:
            if isinstance(term, str) and term.startswith(CMCD_TERM_PREFIX):
                term_class, _, key = term[len(CMCD_TERM_PREFIX):].partition('/')
                if term_class == cmcd_class and key in CMCD_CLASSES.get(cmcd_class, {}):
                    keys.add(key)
        return keys

    def url_wanted(self, url: str) -> bool:
        '''urlFilters: regular expressions of the request URLs to report; without them all are reported'''
        if not self.url_filters:
            return True
        for pattern in self.url_filters:
            try:
                if regex.search(pattern, url, timeout=0.5):
                    return True
            except (regex.error, TimeoutError):
                continue
        return False


def parse_service_access_information(body: Any, log: Optional[logging.Logger] = None) -> Tuple[Optional[str], List[ReportConfig]]:
    '''The provisioning session identifier and the CMCD client metrics reporting configurations of a Service Access Information resource

    TS 26.512 clause 4.5.6: the AF puts the configurations of the CMCD schemes of table 7.8.1-1 only into the resource served at M3d; any other is ignored.
    '''
    if not isinstance(body, dict):
        return None, []
    psid = body.get('provisioningSessionId')
    configs = []
    for entry in body.get('clientMetricsReportingConfigurations') or []:
        scheme = entry.get('scheme') if isinstance(entry, dict) else None
        if not isinstance(scheme, str) or not scheme.startswith(CMCD_SCHEME_PREFIX) or not isinstance(psid, str):
            continue
        try:
            configs.append(ReportConfig(psid, entry))
        except (KeyError, TypeError, ValueError) as err:
            if log is not None:
                log.warning('Client metrics reporting configuration ignored: %s', err)
    return (psid if isinstance(psid, str) else None), configs


def _max_age(headers: Any, now: float) -> int:
    '''The polling period in seconds from Cache-Control: max-age or Expires (TS 26.510 clause 5.3.2.3), at least MIN_POLL_SECONDS'''
    match = re.search(r'max-age=([0-9]+)', headers.get('cache-control', '') or '', re.I)
    if match:
        return max(MIN_POLL_SECONDS, int(match.group(1)))
    try:
        from email.utils import parsedate_to_datetime
        return max(MIN_POLL_SECONDS, int(parsedate_to_datetime(headers['expires']).timestamp() - now))
    except Exception:
        return DEFAULT_POLL_SECONDS


#### The AF as seen from the AS ####

class AfError(Exception):
    pass


class AfClient(object):
    '''HTTP client for the Maf_SessionHandling API of the AF at M3d, with the client credentials grant of TS 26.510 clause 7.4.3'''
    def __init__(self, af_url: str, client_id: Optional[str] = None, client_secret: Optional[str] = None, scope: Optional[str] = None,
                 token_url: Optional[str] = None, transport: Optional[httpx.AsyncBaseTransport] = None, timeout: float = 10.0,
                 clock: Callable[[], float] = time.time):
        self.api = af_url.rstrip('/') + API_PATH
        self.__token_url = token_url or af_url.rstrip('/') + '/oauth2/token'
        self.__credentials = (client_id, client_secret or '') if client_id else None
        self.__scope = scope
        self.__token: Optional[str] = None
        self.__token_expires = 0.0
        self.__clock = clock
        self.__http = httpx.AsyncClient(transport=transport, timeout=timeout)

    async def close(self) -> None:
        await self.__http.aclose()

    async def __access_token(self, force: bool) -> Optional[str]:
        if self.__credentials is None:
            return None
        if self.__token is not None and not force and self.__clock() < self.__token_expires:
            return self.__token
        data = {'grant_type': 'client_credentials'}
        if self.__scope:
            data['scope'] = self.__scope
        try:
            response = await self.__http.post(self.__token_url, data=data, auth=self.__credentials)
            answer = response.json()
            if response.status_code != 200 or not isinstance(answer.get('access_token'), str):
                raise AfError('token endpoint answered %d' % response.status_code)
        except (httpx.HTTPError, ValueError, AttributeError) as err:
            raise AfError('no access token: %s' % err)
        self.__token = answer['access_token']
        # RFC 6749 clause 5.1: expires_in is seconds; renew a little early
        self.__token_expires = self.__clock() + max(0, int(answer.get('expires_in', 60)) - 10)
        return self.__token

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        '''A request with the access token as bearer; an answer 401 gets a new token and one more try'''
        base_headers = dict(kwargs.pop('headers', None) or {})
        response = None
        for attempt in (0, 1):
            token = await self.__access_token(force=attempt == 1)
            headers = dict(base_headers, Authorization='Bearer ' + token) if token else base_headers
            try:
                response = await self.__http.request(method, url, headers=headers, **kwargs)
            except httpx.HTTPError as err:
                raise AfError(str(err))
            if response.status_code != 401 or self.__credentials is None:
                break
        return response

    def sai_url(self, external_service_id: str) -> str:
        return '%s/service-access-information/%s' % (self.api, urllib.parse.quote(external_service_id, safe=''))


class _Sai(object):
    def __init__(self):
        self.provisioning_session_id: Optional[str] = None
        self.configs: List[ReportConfig] = []
        self.etag: Optional[str] = None
        self.last_modified: Optional[str] = None
        self.next_poll = 0.0


class _Session(object):
    '''A media delivery session as the AS sees it: the CMCD session identifier of its requests'''
    def __init__(self, now: float):
        self.start = now
        self.last_seen = now
        self.active: Dict[str, bool] = {}
        self.pending: Dict[str, List[Dict[str, Any]]] = {}
        self.last_sample: Dict[str, float] = {}
        self.last_report: Dict[str, float] = {}
        self.content_id: Optional[str] = None


def _timestamp(seconds: float) -> str:
    '''DateTime of TS 29.571 (RFC 3339), UTC'''
    return datetime.fromtimestamp(seconds, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.') + '%03dZ' % int(seconds % 1 * 1000)


class MetricsReporter(object):
    '''The metrics reporting entity of the AS (TS 26.510 clause 5.3.5): CMCD information to the AF at M3d'''
    def __init__(self, context: Any, client: AfClient, clock: Callable[[], float] = time.time,
                 rng: Callable[[], float] = random.random, session_idle_timeout: float = 60.0):
        self.__ctx = context
        self.__client = client
        self.__clock = clock
        self.__random = rng
        self.__idle = session_idle_timeout
        self.__sai: Dict[str, _Sai] = {}
        self.__sessions: Dict[Tuple[str, str], _Session] = {}
        self.__retry: List[Tuple[float, str, Dict[str, Any], int]] = []
        self.__server = None
        self.__tasks: List[asyncio.Future] = []
        self.__log = logging.getLogger(self.__class__.__name__)

    #### Service Access Information (TS 26.510 clause 5.3.2.3) ####
    def __external_service_ids(self) -> Dict[str, str]:
        ids = {}
        for psid in self.__ctx.getProvisioningSessionIds():
            chc = self.__ctx.findContentHostingConfigurationByProvisioningSession(psid)
            if chc is not None and chc.external_service_id:
                ids[psid] = chc.external_service_id
        return ids

    def configs_for(self, af_resource_id: str) -> List[ReportConfig]:
        '''The CMCD client metrics reporting configurations that apply to a Content Hosting Configuration'''
        external = self.__external_service_ids().get(af_resource_id)
        sai = self.__sai.get(external) if external is not None else None
        return list(sai.configs) if sai is not None else []

    async def refresh(self, external_service_id: str) -> None:
        '''Get the Service Access Information of an external service identifier: a conditional GET (TS 26.510 clause 7.1.4.3)'''
        sai = self.__sai.setdefault(external_service_id, _Sai())
        now = self.__clock()
        headers = {}
        if sai.etag:
            headers['If-None-Match'] = sai.etag
        elif sai.last_modified:
            headers['If-Modified-Since'] = sai.last_modified
        sai.next_poll = now + DEFAULT_POLL_SECONDS
        try:
            response = await self.__client.request('GET', self.__client.sai_url(external_service_id), headers=headers)
        except AfError as err:
            self.__log.warning('Service Access Information of %s not retrieved: %s', external_service_id, err)
            return
        if response.status_code == 304:
            sai.next_poll = now + _max_age(response.headers, now)
        elif response.status_code == 200:
            try:
                psid, configs = parse_service_access_information(response.json(), self.__log)
            except ValueError:
                self.__log.warning('Service Access Information of %s is not JSON', external_service_id)
                return
            sai.provisioning_session_id, sai.configs = psid, configs
            sai.etag, sai.last_modified = response.headers.get('etag'), response.headers.get('last-modified')
            sai.next_poll = now + _max_age(response.headers, now)
            self.__log.info('Service Access Information of %s: %d CMCD metrics reporting configuration(s)', external_service_id, len(configs))
        else:
            if response.status_code == 404:
                sai.configs, sai.etag, sai.last_modified = [], None, None
            self.__log.warning('Service Access Information of %s: the AF answered %d', external_service_id, response.status_code)

    async def refresh_due(self) -> None:
        '''Get the Service Access Information of every Content Hosting Configuration whose time has come'''
        wanted = set(self.__external_service_ids().values())
        for gone in [e for e in self.__sai if e not in wanted]:
            del self.__sai[gone]
        now = self.__clock()
        due = [e for e in sorted(wanted) if e not in self.__sai or self.__sai[e].next_poll <= now]
        if due:
            await asyncio.gather(*(self.refresh(e) for e in due), return_exceptions=True)

    #### Samples of CMCD information from the web proxy ####
    def add_sample(self, af_resource_id: str, url: str, query: Optional[str], headers: Optional[Dict[str, str]]) -> None:
        '''One M4d request with CMCD information (the configurations decide what is kept)'''
        configs = self.configs_for(af_resource_id)
        if not configs:
            return
        pairs = collect_cmcd(query, headers)
        sid = _text(pairs.get('sid'))
        if sid is None:
            # the report needs the media delivery session identifier (TS 26.512 clause 10.5.1: the CMCD key sid)
            return
        now = self.__clock()
        key = (af_resource_id, sid)
        session = self.__sessions.get(key)
        if session is None:
            if len(self.__sessions) >= MAX_SESSIONS:
                return
            session = self.__sessions[key] = _Session(now)
        session.last_seen = now
        session.content_id = _text(pairs.get('cid')) or session.content_id
        for config in configs:
            if config.id not in session.active:
                # TS 26.510 clause 5.3.5.1: decided at the start of the session; below 100 percent a random number in 0 to 100 has to be lower
                session.active[config.id] = config.sample_percentage >= 100 or self.__random() * 100 < config.sample_percentage
            if not session.active[config.id] or not config.url_wanted(url):
                continue
            offset = now - session.start
            if offset < config.start_offset or (config.duration is not None and offset > config.start_offset + config.duration):
                continue
            if now - session.last_sample.get(config.id, -1e18) < config.sampling_period:
                continue
            data = client_data(config.cmcd_class, pairs, config.keys)
            if data is None:
                continue
            session.last_sample[config.id] = now
            pending = session.pending.setdefault(config.id, [])
            pending.append({'sampleTimestamp': _timestamp(now),
                            'mediaStreamingClientData': {'requestUrl': url, _CLASS_PROPERTY[config.cmcd_class]: data}})
            del pending[:-MAX_PENDING_SAMPLES]

    #### Reports (TS 26.510 clause 5.3.5.1) ####
    async def flush(self) -> None:
        '''Send the reports that are due: every reporting interval, and at the end of a media delivery session'''
        now = self.__clock()
        sends = []
        for key, session in list(self.__sessions.items()):
            configs = {c.id: c for c in self.configs_for(key[0])}
            ended = now - session.last_seen >= self.__idle
            for config_id, samples in list(session.pending.items()):
                config = configs.get(config_id)
                if config is None:
                    del session.pending[config_id]
                    continue
                if samples and (ended or (config.interval and now - session.last_report.get(config_id, session.start) >= config.interval)):
                    sends.append((config, key[1], session.content_id, list(samples)))
                    session.pending[config_id] = []
                    # the interval timer starts again when a report is produced
                    session.last_report[config_id] = now
            if ended:
                del self.__sessions[key]
        for config, sid, content_id, samples in sends:
            session_object = {'clientId': sid, 'provisioningSessionId': config.provisioning_session_id, 'sessionId': sid, 'samples': samples}
            if content_id:
                session_object['contentId'] = content_id
            await self.__submit(config, {'reportTimestamp': _timestamp(now), 'sessions': [session_object]}, 1)
        due = [r for r in self.__retry if r[0] <= now]
        self.__retry = [r for r in self.__retry if r[0] > now]
        for _, url, body, tries in due:
            await self.__post(url, body, tries)

    def report_url(self, config: ReportConfig) -> str:
        '''One of the server addresses of the configuration, chosen at random, with the path of clause 9.5.2'''
        base = self.__random_choice(config.server_addresses)
        return '%s/provisioning-sessions/%s/metrics-reporting/%s' % (
            base.rstrip('/'), urllib.parse.quote(config.provisioning_session_id, safe=''), urllib.parse.quote(config.id, safe=''))

    def __random_choice(self, items: List[str]) -> str:
        return items[min(int(self.__random() * len(items)), len(items) - 1)]

    async def __submit(self, config: ReportConfig, body: Dict[str, Any], tries: int) -> None:
        await self.__post(self.report_url(config), body, tries)

    async def __post(self, url: str, body: Dict[str, Any], tries: int) -> None:
        '''Submit a metrics report operation (TS 26.510 clause 5.3.5.2)'''
        now = self.__clock()
        try:
            response = await self.__client.request('POST', url, content=json.dumps(body).encode('utf-8'), headers={'Content-Type': REPORT_MEDIA_TYPE})
        except AfError as err:
            self.__log.warning('Metrics report to %s failed: %s', url, err)
            self.__later(now + RETRY_SECONDS, url, body, tries)
            return
        status = response.status_code
        if status in (200, 202):
            return
        if status == 503:
            # the AF cannot take it now: stored and sent again later, as the Retry-After header field says if it does
            after = response.headers.get('retry-after', '')
            self.__later(now + (int(after) if after.isdigit() else RETRY_SECONDS), url, body, tries)
            return
        self.__log.warning('Metrics report to %s refused with %d', url, status)
        if status == 403:
            # reporting is not provisioned any more: look at the Service Access Information again
            for sai in self.__sai.values():
                sai.next_poll = 0.0

    def __later(self, when: float, url: str, body: Dict[str, Any], tries: int) -> None:
        if tries < RETRY_TRIES and len(self.__retry) < MAX_RETRY_QUEUE:
            self.__retry.append((when, url, body, tries + 1))

    #### The samples socket and the loops ####
    async def __on_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                try:
                    line = await reader.readline()
                except (asyncio.LimitOverrunError, ValueError):
                    break
                if not line:
                    break
                try:
                    sample = json.loads(line.decode('utf-8'))
                    self.add_sample(str(sample['ps']), str(sample['url']), sample.get('query'), sample.get('headers'))
                except (ValueError, KeyError, TypeError, AttributeError):
                    self.__log.debug('Sample ignored')
        finally:
            writer.close()

    async def start(self, socket_path: str, socket_mode: int = 0o666) -> None:
        '''Start taking samples from the web proxy and the loops for the Service Access Information and the reports'''
        if os.path.exists(socket_path):
            os.unlink(socket_path)
        os.makedirs(os.path.dirname(socket_path) or '.', exist_ok=True)
        self.__server = await asyncio.start_unix_server(self.__on_connection, path=socket_path, limit=MAX_SAMPLE_LINE)
        os.chmod(socket_path, socket_mode)
        self.__tasks = [asyncio.ensure_future(self.__loop(self.refresh_due)), asyncio.ensure_future(self.__loop(self.flush))]

    async def __loop(self, step: Callable[[], Any]) -> None:
        while True:
            try:
                await step()
            except asyncio.CancelledError:
                raise
            except Exception as err:
                self.__log.error('M3d reporting step failed: %s', err)
            await asyncio.sleep(1)

    async def stop(self) -> None:
        for task in self.__tasks:
            task.cancel()
        if self.__server is not None:
            self.__server.close()
        await self.__client.close()


def from_context(context: Any) -> Optional[MetricsReporter]:
    '''The reporter for the configuration of the AS, or None when the section 5gms_as.m3d has no af_url'''
    section = '5gms_as.m3d'
    af_url = (context.getConfigVar(section, 'af_url', '') or '').strip()
    if not af_url:
        return None
    client = AfClient(af_url, context.getConfigVar(section, 'oauth_client_id') or None, context.getConfigVar(section, 'oauth_client_secret'),
                      context.getConfigVar(section, 'oauth_scope') or None, context.getConfigVar(section, 'oauth_token_url') or None)
    return MetricsReporter(context, client, session_idle_timeout=float(context.getConfigVar(section, 'session_idle_timeout', '60')))


def samples_socket_path(context: Any) -> str:
    '''The Unix socket on which the web proxy hands over CMCD information; empty when M3d is not configured (no af_url)'''
    if not (context.getConfigVar('5gms_as.m3d', 'af_url', '') or '').strip():
        return ''
    default = os.path.join(os.path.dirname(context.getConfigVar('5gms_as', 'pid_path') or '/run/rt-5gms/application-server.pid'), 'm3d-cmcd.sock')
    return context.getConfigVar('5gms_as.m3d', 'samples_socket', default) or default
