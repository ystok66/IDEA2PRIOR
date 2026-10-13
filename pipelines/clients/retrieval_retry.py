"""Explicit, bounded retries for scholarly read-only metadata operations.

Only callers opting into this helper retry. Current integration is S2 GET and
its explicitly read-only /paper/batch POST. The shared HTTP transport remains
single-attempt. No arbitrary POST is retried and no successful body is cached.

The budget covers backoff and admission and is checked immediately before each
network attempt by http_transport. Requests socket timeouts cannot impose an
absolute wall-clock deadline (DNS, streaming, and OS scheduling can overrun).
"""
from __future__ import annotations

import math
import random
import time
from typing import Callable
from urllib.parse import urlsplit
import uuid

import requests

from pipelines.clients.http_transport import (
    ProviderUnavailable, bounded_timeout, defer_provider, retry_after_seconds,
)
from pipelines.clients import http_transport
from pipelines.runtime import get_runtime


RETRYABLE_STATUS = frozenset((429, 500, 502, 503, 504))


def _jitter():
    return random.uniform(0.0, 0.25)


def call_readonly(operation: Callable, url: str, *, method='GET', read_only_batch=False,
                  max_attempts=3, budget_seconds=120.0, max_cooldown_seconds=30.0,
                  timeout=(10, 60), logical_request_id=None,
                  clock=time.monotonic, sleep=time.sleep, jitter=_jitter, **kwargs):
    """Perform at most 3 attempts under one explicit logical request ID.

    ``operation`` follows http_transport.get/post's keyword interface. Failed
    final HTTP responses are returned unchanged for the caller's raise_for_status;
    final connection/timeouts propagate unchanged. Long server cooldowns raise
    ProviderUnavailable immediately and stay enforced for other provider calls.
    """
    method = method.upper()
    if (operation is http_transport.post and method != 'POST') or (operation is http_transport.get and method != 'GET'):
        raise ValueError('Declared retry method must match the supplied HTTP operation')
    if method != 'GET' and not (method == 'POST' and read_only_batch is True
                               and urlsplit(url).path.rstrip('/').endswith('/paper/batch')):
        raise ValueError('Retries require GET or an explicitly read-only S2 paper/batch POST')
    if type(max_attempts) is not int or not 1 <= max_attempts <= 3:
        raise ValueError('Read-only retrieval permits between 1 and 3 total attempts')
    for name, value in (('budget_seconds', budget_seconds), ('max_cooldown_seconds', max_cooldown_seconds)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f'{name} must be finite positive seconds')
    if any(name in kwargs for name in ('attempt_ordinal', 'deadline_monotonic', 'deadline_clock', 'max_wait_seconds')):
        raise ValueError('Retry coordination fields are owned by call_readonly')
    logical_id = logical_request_id or uuid.uuid4().hex
    deadline = clock() + budget_seconds
    runtime = get_runtime()

    def event(status, **details):
        if runtime:
            runtime.ledger.event('READONLY_RETRY', logical_request_id=logical_id,
                                 status=status, method=method, url=url, **details)

    for ordinal in range(1, max_attempts + 1):
        left = deadline - clock()
        if left <= 0:
            event('BUDGET_EXHAUSTED', attempts_started=ordinal - 1)
            raise ProviderUnavailable('Read-only request budget exhausted before another attempt')
        effective_timeout = bounded_timeout(timeout, left)
        response = None
        failure = None
        try:
            response = operation(url, logical_request_id=logical_id, attempt_ordinal=ordinal,
                deadline_monotonic=deadline, deadline_clock=clock,
                max_wait_seconds=min(max_cooldown_seconds, left), timeout=effective_timeout, **kwargs)
        except ProviderUnavailable:
            event('PROVIDER_UNAVAILABLE', attempt_ordinal=ordinal)
            raise
        except (requests.Timeout, requests.ConnectionError) as error:
            failure = error
        if response is not None and response.status_code not in RETRYABLE_STATUS:
            return response

        retry_after = None
        if response is not None and response.status_code in (429, 503):
            retry_after = retry_after_seconds(response.headers.get('Retry-After'))
            if retry_after is not None:
                # Also protects other logical requests if operation is a test
                # adapter or another compatible single-attempt transport.
                defer_provider(url, retry_after)
        left = deadline - clock()
        if retry_after is not None and (retry_after > max_cooldown_seconds or retry_after >= left):
            event('PROVIDER_UNAVAILABLE', attempt_ordinal=ordinal, retry_after_seconds=retry_after,
                  remaining_seconds=max(0.0, left))
            raise ProviderUnavailable(f'Provider Retry-After ({retry_after:.1f}s) exceeds the permitted wait budget')
        if ordinal == max_attempts:
            event('ATTEMPTS_EXHAUSTED', attempt_ordinal=ordinal)
            if failure is not None:
                raise failure
            return response
        extra = jitter()
        if isinstance(extra, bool) or not isinstance(extra, (int, float)) or not math.isfinite(extra) or extra < 0:
            raise ValueError('Retry jitter must be finite and nonnegative')
        delay = max((2.0 ** ordinal) + extra, retry_after or 0.0)
        if delay >= left:
            event('BUDGET_EXHAUSTED', attempt_ordinal=ordinal, remaining_seconds=max(0.0, left),
                  retry_delay_seconds=delay)
            if failure is not None:
                raise failure
            raise ProviderUnavailable('Read-only request budget cannot accommodate another retry')
        event('WAITING_RETRY', attempt_ordinal=ordinal, retry_delay_seconds=delay)
        sleep(delay)
    raise AssertionError('Unreachable retry state')
