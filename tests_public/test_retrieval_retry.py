"""Fixed-response scholarly retry contracts; no network, models, or real sleeps."""
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import requests

from pipelines.clients import http_transport as http
from pipelines.clients.retrieval_retry import call_readonly
from pipelines.rate_limiter import SimpleRateLimiter
from pipelines.runtime import LLMRuntime, RequestScheduler, RunLedger, use_runtime


class Clock:
    def __init__(self):
        self.now, self.sleeps = 0.0, []
    def __call__(self):
        return self.now
    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def response(status, headers=None):
    return SimpleNamespace(status_code=status, ok=200 <= status < 400,
        headers=headers or {}, text='{}', json=lambda: {'status': status})


class RetrievalRetryTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / '.local/test_artifacts'
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.clock = Clock()
        self.runtime = LLMRuntime(RunLedger(Path(self.temp.name)))
        self.scheduler = RequestScheduler(clock=self.clock, sleep=self.clock.sleep)
        self.patches = [
            patch.dict(http._SCHEDULERS, {'fixture.invalid': self.scheduler}, clear=True),
            use_runtime(self.runtime),
        ]
        for context in self.patches:
            context.__enter__()
    def tearDown(self):
        for context in reversed(self.patches):
            context.__exit__(None, None, None)
        self.temp.cleanup()
    def call(self, **kwargs):
        return call_readonly(http.get, 'https://fixture.invalid/paper/search',
            clock=self.clock, sleep=self.clock.sleep, jitter=lambda: 0, **kwargs)

    def test_successful_retry_closes_failure_under_same_logical_id(self):
        with patch.object(http.requests, 'get', side_effect=[response(500), response(200)]) as get:
            self.assertEqual(self.call().status_code, 200)
        summary = http.summarize_attempts(self.temp.name)
        self.assertEqual(summary['failed_requests'], [])
        self.assertEqual(summary['attempt_count'], 2)
        self.assertEqual(summary['logical_request_count'], 1)
        entries = [json.loads(path.read_text()) for path in (Path(self.temp.name) / 'http_attempts').glob('*.json')]
        self.assertEqual(sorted(row['attempt_ordinal'] for row in entries), [1, 2])
        self.assertEqual(get.call_count, 2)
        self.assertEqual(self.clock.sleeps, [2.0])

    def test_persistent_500_has_three_total_attempts(self):
        with patch.object(http.requests, 'get', return_value=response(500)) as get:
            self.assertEqual(self.call().status_code, 500)
        self.assertEqual(get.call_count, 3)
        self.assertEqual(self.clock.sleeps, [2.0, 4.0])
        summary = http.summarize_attempts(self.temp.name)
        self.assertEqual(len(summary['failed_requests']), 1)
        self.assertEqual(summary['failed_requests'][0]['attempt_ordinal'], 3)

    def test_400_and_404_do_not_retry(self):
        for status in (400, 404):
            with patch.object(http.requests, 'get', return_value=response(status)) as get:
                self.assertEqual(self.call().status_code, status)
                get.assert_called_once()
        self.assertEqual(self.clock.sleeps, [])

    def test_timeout_and_connection_failures_recover_with_bounded_attempts(self):
        with patch.object(http.requests, 'get', side_effect=[requests.Timeout('fixture'), requests.ConnectionError('fixture'), response(200)]) as get:
            self.assertEqual(self.call().status_code, 200)
        self.assertEqual(get.call_count, 3)
        self.assertEqual(http.summarize_attempts(self.temp.name)['failed_requests'], [])

    def test_persistent_timeout_raises_final_original_error(self):
        errors = [requests.Timeout(str(n)) for n in range(3)]
        with patch.object(http.requests, 'get', side_effect=errors) as get:
            with self.assertRaises(requests.Timeout) as raised:
                self.call()
        self.assertIs(raised.exception, errors[-1])
        self.assertEqual(get.call_count, 3)

    def test_long_retry_after_is_prompt_and_shared_no_early_request(self):
        with patch.object(http.requests, 'get', return_value=response(429, {'Retry-After': '3600'})) as get:
            with self.assertRaises(http.ProviderUnavailable):
                self.call(max_cooldown_seconds=30)
            self.assertEqual(self.clock.now, 0)
            get.assert_called_once()
            # A separate logical call must respect the previously published hold.
            with self.assertRaises(http.ProviderUnavailable):
                self.call(max_cooldown_seconds=30)
            get.assert_called_once()
        self.assertEqual(self.clock.sleeps, [])
        self.assertEqual(self.scheduler._not_before, 3600)

    def test_429_and_503_retry_after_not_shortened(self):
        for status in (429, 503):
            starts = []
            values = iter([response(status, {'Retry-After': '7'}), response(200)])
            def get(*args, **kwargs):
                starts.append(self.clock.now)
                return next(values)
            with patch.object(http.requests, 'get', side_effect=get):
                self.assertEqual(self.call().status_code, 200)
            self.assertGreaterEqual(starts[1] - starts[0], 7)

    def test_expired_retry_budget_never_starts_another_attempt(self):
        def get(*args, **kwargs):
            self.clock.now += 4
            return response(500)
        with patch.object(http.requests, 'get', side_effect=get) as mocked:
            with self.assertRaises(http.ProviderUnavailable):
                self.call(budget_seconds=5)
        mocked.assert_called_once()
        self.assertEqual(self.clock.sleeps, [])

    def test_queue_wait_reduces_real_requests_timeout(self):
        clock = self.clock
        class WaitingScheduler:
            @contextmanager
            def slot(self, **kwargs):
                clock.now += 5
                yield {}
        with patch.dict(http._SCHEDULERS, {'fixture.invalid': WaitingScheduler()}):
            with patch.object(http.requests, 'get', return_value=response(200)) as get:
                self.assertEqual(self.call(budget_seconds=10, timeout=(10, 60)).status_code, 200)
        timeout = get.call_args.kwargs['timeout']
        self.assertLessEqual(sum(timeout), 5.00000001)
        self.assertTrue(all(value > 0 for value in timeout))
        self.assertNotIn('deadline_monotonic', get.call_args.kwargs)

    def test_queue_exhaustion_prevents_the_network_attempt(self):
        clock = self.clock
        class WaitingScheduler:
            @contextmanager
            def slot(self, **kwargs):
                clock.now += 11
                yield {}
        with patch.dict(http._SCHEDULERS, {'fixture.invalid': WaitingScheduler()}):
            with patch.object(http.requests, 'get') as get:
                with self.assertRaises(http.ProviderUnavailable):
                    self.call(budget_seconds=10)
        get.assert_not_called()
        final = http.summarize_attempts(self.temp.name)['failed_requests'][0]
        self.assertFalse(final['network_attempted'])

    def test_provider_limiter_wait_is_bound_by_remaining_budget(self):
        limiter = SimpleRateLimiter(1, 2, clock=self.clock, sleep=self.clock.sleep)
        limiter.wait_for_slot()
        with patch.object(http, '_provider', return_value=('fixture.invalid', limiter)):
            with patch.object(http.requests, 'get') as get:
                with self.assertRaises(http.ProviderUnavailable):
                    self.call(budget_seconds=1)
        get.assert_not_called()
        self.assertEqual(self.clock.now, 0)

    def test_only_declared_readonly_s2_post_can_retry(self):
        with patch.object(http.requests, 'post', side_effect=[response(502), response(200)]) as post:
            good = call_readonly(http.post, 'https://fixture.invalid/graph/v1/paper/batch',
                method='POST', read_only_batch=True, json={'ids': ['paper']},
                clock=self.clock, sleep=self.clock.sleep, jitter=lambda: 0)
        self.assertEqual(good.status_code, 200)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args.kwargs['json'], {'ids': ['paper']})
        with self.assertRaises(ValueError):
            call_readonly(http.post, 'https://fixture.invalid/write', method='POST', read_only_batch=True)
        with self.assertRaises(ValueError):
            call_readonly(http.post, 'https://fixture.invalid/paper/batch', method='POST')
        with self.assertRaises(ValueError):
            call_readonly(http.post, 'https://fixture.invalid/write')

    def test_jitter_added_without_changing_attempt_count(self):
        with patch.object(http.requests, 'get', side_effect=[response(504), response(500), response(200)]):
            call_readonly(http.get, 'https://fixture.invalid/paper/search', clock=self.clock,
                sleep=self.clock.sleep, jitter=lambda: .25)
        self.assertEqual(self.clock.sleeps, [2.25, 4.25])

    def test_datacite_has_separate_one_second_pacing(self):
        provider, limiter = http._provider('https://api.datacite.org/dois/10.1234/example')
        self.assertEqual(provider, 'datacite')
        self.assertEqual((limiter.max_calls, limiter.period), (1, 1.0))


if __name__ == '__main__':
    unittest.main()
