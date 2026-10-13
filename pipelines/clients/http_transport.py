"""Bounded provider HTTP requests with raw, redacted transport evidence.

No hidden retries or HTTP response caching. Provider rate limits remain separate
from LLM token budgets. Existing callers retain their response/error interface.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from contextlib import nullcontext
from loguru import logger
from pathlib import Path
from urllib.parse import urlsplit

import requests

from pipelines.rate_limiter import openalex_rate_limiter, s2_rate_limiter, SimpleRateLimiter
from pipelines.runtime import RequestScheduler, atomic_write_json, get_runtime, redact, secret_values

_LOCK = threading.Lock()
_SCHEDULERS = {}
_ARXIV_LIMITER = SimpleRateLimiter(1, 3.0)
_CROSSREF_LIMITER = SimpleRateLimiter(1, 1.0)
_DATACITE_LIMITER = SimpleRateLimiter(1, 1.0)
_DEFAULT_GET = requests.get
_DEFAULT_POST = requests.post
_CONNECTION_LOCK = threading.Lock()
_CONNECTION_LOCAL = threading.local()
_CONNECTION_POOLS = {}


class _ConnectionPool:
    def __init__(self, runtime):
        self.runtime = runtime
        self.sessions = {}
        self.closed = False


def _thread_session(runtime):
    """Reuse connections only inside one runtime and one calling thread."""
    with _CONNECTION_LOCK:
        local = getattr(_CONNECTION_LOCAL, 'sessions', None)
        if local is None:
            local = _CONNECTION_LOCAL.sessions = {}
        # A coordinator may have closed a finished run from another thread.
        for key, (pool, _) in list(local.items()):
            if pool.closed:
                del local[key]
        key = id(runtime)
        pool = _CONNECTION_POOLS.get(key)
        if pool is None:
            pool = _CONNECTION_POOLS[key] = _ConnectionPool(runtime)
        cached = local.get(key)
        if cached is not None and cached[0] is pool:
            return cached[1]
        # Standard Requests defaults preserve environment/proxy/TLS behavior,
        # while the default adapters have zero automatic transport retries.
        session = requests.Session()
        pool.sessions[id(session)] = session
        local[key] = (pool, session)
        return session


def close_thread_connections():
    """Close only this thread's pooled Sessions, across its runtime contexts."""
    with _CONNECTION_LOCK:
        local = getattr(_CONNECTION_LOCAL, 'sessions', {})
        sessions = []
        for pool, session in local.values():
            if not pool.closed and pool.sessions.pop(id(session), None) is not None:
                sessions.append(session)
        local.clear()
    for session in sessions:
        session.close()
    return len(sessions)


def close_http_connections(runtime=None):
    """Close a run's Sessions after its workers finish; leave other runs alone.

    An omitted runtime means the current runtime. Closed pool identities are
    invalidated, so any later request creates a fresh Session in its own thread.
    """
    runtime = runtime if runtime is not None else get_runtime()
    if runtime is None:
        return 0
    with _CONNECTION_LOCK:
        pool = _CONNECTION_POOLS.pop(id(runtime), None)
        if pool is None:
            return 0
        pool.closed = True
        pool.runtime = None
        sessions = list(pool.sessions.values())
        pool.sessions.clear()
    for session in sessions:
        session.close()
    return len(sessions)


def _send(method, url, runtime, kwargs):
    sender = requests.get if method == 'GET' else requests.post
    original = _DEFAULT_GET if method == 'GET' else _DEFAULT_POST
    policy = getattr(runtime, 'retrieval_policy', {})
    if not policy.get('shared_http_connections', False) or sender is not original:
        # Preserve the existing top-level sender injection/monkeypatch contract,
        # including when a pooled runtime is exercised by offline tests.
        return sender(url, **kwargs)
    session = _thread_session(runtime)
    # Requests' top-level helpers start with a fresh cookie jar on every call.
    # Keep that behavior while allowing this call's redirects/cookies as usual.
    session.cookies.clear()
    try:
        return (session.get if method == 'GET' else session.post)(url, **kwargs)
    finally:
        session.cookies.clear()


class ProviderUnavailable(requests.RequestException):
    pass


def openalex_enabled():
    runtime = get_runtime()
    return getattr(runtime, 'retrieval_policy', {}).get('enable_openalex', True)


def retry_after_seconds(value):
    try:
        seconds = float(value)
    except (ValueError, TypeError):
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    import math
    return max(0.0, seconds) if math.isfinite(seconds) else None


def summarize_attempts(run_dir):
    """Only an explicitly declared retry chain can share a logical request ID."""
    latest, count = {}, 0
    for path in sorted((Path(run_dir) / 'http_attempts').glob('*.json')):
        attempt = json.loads(path.read_text(encoding='utf-8'))
        count += 1
        key = attempt.get('logical_request_id', attempt['attempt_id'])
        if key not in latest or attempt.get('attempt_ordinal', 1) > latest[key].get('attempt_ordinal', 1):
            latest[key] = attempt
    failures = [item for item in latest.values() if item.get('status') in ('TRANSPORT_ERROR', 'INVALID_JSON_RESPONSE', 'INVALID_ATOM_RESPONSE', 'RATE_LIMIT_DEFERRED')
                or (item.get('status') == 'HTTP_ERROR' and item.get('http_status') != 404)]
    return {'attempt_count': count, 'logical_request_count': len(latest), 'failed_requests': failures}


def _provider(url):
    host = urlsplit(url).hostname or 'unknown'
    if 'openalex' in host:
        return 'openalex', openalex_rate_limiter
    if 'semanticscholar' in host:
        return 'semantic_scholar', s2_rate_limiter
    if host in ('export.arxiv.org', 'arxiv.org') and '/api/' in url:
        return 'arxiv', _ARXIV_LIMITER
    if host == 'api.crossref.org':
        return 'crossref', _CROSSREF_LIMITER
    if host == 'api.datacite.org':
        return 'datacite', _DATACITE_LIMITER
    return host, None


def provider_scheduler(url):
    provider, _ = _provider(url)
    with _LOCK:
        return _SCHEDULERS.setdefault(provider, RequestScheduler(max_in_flight=1 if provider == 'arxiv' else 4, max_pending=64))


def defer_provider(url, seconds):
    """Publish an observed cooldown to all requests using this provider pool."""
    provider_scheduler(url).defer(seconds)


def bounded_timeout(timeout, remaining):
    """Bound Requests phase timeouts by a remaining admission/attempt budget.

    Requests timeouts are socket inactivity limits, not hard wall-clock deadlines.
    A tuple's connect+read allowances are conservatively bounded by remaining.
    """
    import math
    if not math.isfinite(remaining) or remaining <= 0:
        raise TimeoutError('Read-only request budget exhausted')
    def value(part):
        if part is None:
            return remaining
        if isinstance(part, bool) or not isinstance(part, (float, int)) or not math.isfinite(part) or part <= 0:
            raise ValueError('HTTP timeout must contain finite positive seconds or None')
        return float(part)
    if isinstance(timeout, tuple):
        if len(timeout) != 2:
            raise ValueError('HTTP timeout tuple must be (connect, read)')
        connect, read = map(value, timeout)
        scale = min(1.0, remaining / (connect + read))
        return connect * scale, read * scale
    return min(value(timeout), remaining)


def request(method, url, *, response_format='json', logical_request_id=None, attempt_ordinal=1,
            deadline_monotonic=None, deadline_clock=None, max_wait_seconds=None, **kwargs):
    if method not in ('GET', 'POST') or response_format not in ('json', 'atom'):
        raise ValueError('Unsupported transport method or response format')
    provider, limiter = _provider(url)
    if provider == 'openalex' and not openalex_enabled():
        raise ProviderUnavailable('OpenAlex is disabled by the run policy')
    scheduler = provider_scheduler(url)
    runtime = get_runtime()
    attempt_id = uuid.uuid4().hex
    kwargs.setdefault('timeout', (10, 60))
    secrets = secret_values(kwargs)
    clock = deadline_clock or time.monotonic
    started = clock()
    def remaining():
        left = deadline_monotonic - clock() if deadline_monotonic is not None else None
        if left is not None and left <= 0:
            raise TimeoutError('Read-only request budget exhausted before network attempt')
        return left
    record = {'attempt_id': attempt_id, 'logical_request_id': logical_request_id or attempt_id,
              'attempt_ordinal': attempt_ordinal, 'provider': provider, 'method': method,
              'url': url, 'request': redact(kwargs, secrets), 'status': 'RUNNING', 'network_attempted': False}
    if runtime:
        runtime.ledger.secrets = tuple(set(runtime.ledger.secrets + secrets))
        runtime.ledger.event('HTTP_STARTED', **record)
    try:
        policy = getattr(runtime, 'retrieval_policy', {})
        max_wait = policy.get('http_max_wait_seconds', 120)
        if max_wait_seconds is not None:
            max_wait = min(max_wait, max_wait_seconds)
        left = remaining()
        if left is not None:
            max_wait = min(max_wait, left)
        def waiting(delay):
            logger.info(f'[HTTP] {provider} WAITING_RATE_LIMIT remaining={delay:.1f}s')
            if runtime:
                runtime.ledger.event('HTTP_WAITING', provider=provider, attempt_id=attempt_id, remaining_seconds=delay)
        with scheduler.slot(max_wait_seconds=max_wait, on_wait=waiting,
                            cancel_event=getattr(runtime, 'cancel_event', None)) as admission:
            if limiter:
                left = remaining()
                if left is None:
                    limiter.wait_for_slot()
                else:
                    limiter.wait_for_slot(max_wait_seconds=left)
            from pipelines.clients.host_gate import arxiv_connection
            left = remaining()
            with arxiv_connection(min(max_wait, left) if left is not None else max_wait) if provider == 'arxiv' else nullcontext():
                left = remaining()
                if left is not None:
                    kwargs['timeout'] = bounded_timeout(kwargs.get('timeout'), left)
                    record['request']['timeout'] = kwargs['timeout']
                    record['remaining_before_network_seconds'] = left
                service_started = clock()
                record['network_attempted'] = True
                response = _send(method, url, runtime, kwargs)
            record.update(queue_seconds=service_started - started,
                          service_seconds=clock() - service_started,
                          http_status=response.status_code,
                          status='SUCCEEDED' if response.ok else 'HTTP_ERROR',
                          response_headers=dict(response.headers))
            try:
                if response_format == 'atom':
                    record['body'] = response.text
                    feed = ET.fromstring(response.text)
                    if feed.tag != '{http://www.w3.org/2005/Atom}feed':
                        raise ValueError('Expected Atom feed')
                else:
                    record['body'] = response.json()
                record['json_decode_status'] = 'SUCCEEDED'
                record['response_format'] = response_format
            except (ValueError, TypeError, ET.ParseError):
                record['body'] = response.text
                record['json_decode_status'] = 'FAILED'
                if response.ok:
                    record['status'] = 'INVALID_ATOM_RESPONSE' if response_format == 'atom' else 'INVALID_JSON_RESPONSE'
            retry_after = response.headers.get('Retry-After')
            if retry_after and response.status_code in (429, 503):
                seconds = retry_after_seconds(retry_after)
                if seconds is not None:
                    scheduler.defer(seconds)
                    record['retry_after_seconds'] = seconds
                    if seconds > max_wait:
                        logger.warning(f'[HTTP] {provider} unavailable for {seconds:.0f}s; later calls will fail promptly, not wait hours')
            return response
    except TimeoutError as exc:
        record.update(status='RATE_LIMIT_DEFERRED', error_type=type(exc).__name__, error=str(exc))
        raise ProviderUnavailable(str(exc)) from exc
    except Exception as exc:
        record.update(status='TRANSPORT_ERROR', error_type=type(exc).__name__, error=redact(str(exc), secrets))
        raise
    finally:
        record['elapsed_seconds'] = clock() - started
        if runtime:
            path = runtime.ledger.run_dir / 'http_attempts' / f'{attempt_id}.json'
            atomic_write_json(path, redact(record, runtime.ledger.secrets))
            runtime.ledger.event('HTTP_FINISHED', attempt_id=attempt_id, provider=provider,
                status=record['status'], artifact=str(path.relative_to(runtime.ledger.run_dir)),
                sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def get(url, **kwargs):
    return request('GET', url, **kwargs)


def post(url, **kwargs):
    # Explicit read-only batch operations; no automatic retry of arbitrary POSTs.
    return request('POST', url, **kwargs)
