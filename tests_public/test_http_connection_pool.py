"""Offline connection lifetime, isolation, and legacy sender contracts."""
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import requests

from pipelines.clients import http_transport as http
from pipelines.runtime import LLMRuntime, RequestScheduler, RunLedger, use_runtime


def response(status=200, headers=None):
    return SimpleNamespace(status_code=status, ok=200 <= status < 400,
        headers=headers or {}, text='{"data": ["complete body"]}',
        json=lambda: {'data': ['complete body']})


class SessionFixture:
    def __init__(self):
        self.cookies = requests.cookies.RequestsCookieJar()
        self.calls = []
        self.closed = 0
        self.result = response()

    def get(self, url, **kwargs):
        return self._send('GET', url, kwargs)

    def post(self, url, **kwargs):
        return self._send('POST', url, kwargs)

    def _send(self, method, url, kwargs):
        self.calls.append((method, url, kwargs, dict(self.cookies), threading.get_ident()))
        self.cookies.set('response_cookie', 'must_not_reach_the_next_call')
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def close(self):
        self.closed += 1


class HTTPConnectionPoolTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / '.local/test_artifacts'
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.runtimes = []
        self.sessions = []
        self.fixture_lock = threading.Lock()
        self.runtime = self.new_runtime()
        self.factory = patch.object(http.requests, 'Session', side_effect=self.new_session)
        self.factory.start()
        # A regression cannot accidentally turn an offline fixture into network.
        self.no_network = patch('requests.adapters.HTTPAdapter.send',
            side_effect=AssertionError('Offline test attempted a real transport'))
        self.no_network.start()

    def tearDown(self):
        for runtime in self.runtimes:
            http.close_http_connections(runtime)
        http.close_thread_connections()
        self.no_network.stop()
        self.factory.stop()
        self.temp.cleanup()

    def new_runtime(self):
        runtime = LLMRuntime(RunLedger(Path(self.temp.name) / str(len(self.runtimes))))
        runtime.retrieval_policy = {'shared_http_connections': True}
        self.runtimes.append(runtime)
        return runtime

    def new_session(self):
        session = SessionFixture()
        with self.fixture_lock:
            self.sessions.append(session)
        return session

    def records(self, runtime=None):
        root = (runtime or self.runtime).ledger.run_dir / 'http_attempts'
        return [json.loads(path.read_text(encoding='utf-8')) for path in root.glob('*.json')]

    def test_get_and_post_reuse_one_session_preserve_options_and_stateless_cookies(self):
        options = {'params': {'q': 'whole idea'}, 'headers': {'Authorization': 'Bearer fixture-secret'},
            'auth': ('fixture-user', 'fixture-password'), 'proxies': {'https': 'http://fixture.invalid:123'},
            'verify': 'fixture-ca.pem', 'cert': ('fixture-cert.pem', 'fixture-key.pem'),
            'timeout': (4, 7), 'cookies': {'explicit': 'this request only'},
            'allow_redirects': False, 'stream': True}
        with use_runtime(self.runtime):
            http.get('https://fixture.invalid/search', **options)
            http.post('https://fixture.invalid/batch', json={'ids': ['paper']})
        self.assertEqual(len(self.sessions), 1)
        session = self.sessions[0]
        self.assertEqual([call[0] for call in session.calls], ['GET', 'POST'])
        self.assertEqual(session.calls[0][2], options)
        self.assertEqual(session.calls[1][2], {'json': {'ids': ['paper']}, 'timeout': (10, 60)})
        self.assertEqual([call[3] for call in session.calls], [{}, {}])
        self.assertEqual(dict(session.cookies), {})
        records = self.records()
        self.assertEqual(len(records), 2)
        self.assertTrue(all(row['body'] == {'data': ['complete body']} for row in records))
        self.assertNotIn('fixture-secret', json.dumps(records))
        self.assertTrue(all(row['network_attempted'] for row in records))

    def test_opt_in_is_required_and_replaced_top_level_senders_still_work(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                self.runtime.retrieval_policy = {'shared_http_connections': enabled}
                with use_runtime(self.runtime), patch.object(http.requests, 'get', return_value=response()) as get, \
                        patch.object(http.requests, 'post', return_value=response()) as post:
                    http.get('https://fixture.invalid/search', params={'q': 'idea'})
                    http.post('https://fixture.invalid/batch', json={'ids': ['paper']})
                get.assert_called_once()
                post.assert_called_once()
        self.assertEqual(self.sessions, [])

    def test_unset_policy_uses_original_top_level_helpers_without_a_pool(self):
        self.runtime.retrieval_policy = {}
        with use_runtime(self.runtime), patch('requests.api.request', return_value=response()) as sender:
            http.get('https://fixture.invalid/search', params={'q': 'idea'})
            http.post('https://fixture.invalid/batch', json={'ids': ['paper']})
        self.assertEqual(sender.call_count, 2)
        self.assertEqual([call.args[0] for call in sender.call_args_list], ['get', 'post'])
        self.assertEqual(self.sessions, [])

    def test_worker_threads_do_not_share_sessions(self):
        barrier = threading.Barrier(2)
        failures = []
        def worker():
            try:
                with use_runtime(self.runtime):
                    http.get('https://fixture.invalid/search')
                    barrier.wait(timeout=5)
                    http.get('https://fixture.invalid/search')
            except BaseException as exc:
                failures.append(exc)
        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(failures, [])
        self.assertEqual(len(self.sessions), 2)
        self.assertEqual([len(session.calls) for session in self.sessions], [2, 2])
        owners = [{call[4] for call in session.calls} for session in self.sessions]
        self.assertTrue(all(len(owner) == 1 for owner in owners))
        self.assertTrue(owners[0].isdisjoint(owners[1]))
        self.assertEqual(http.close_http_connections(self.runtime), 2)
        self.assertEqual([session.closed for session in self.sessions], [1, 1])

    def test_runtime_cleanup_leaves_other_runs_open_and_rebuilds_closed_local_session(self):
        other = self.new_runtime()
        with use_runtime(self.runtime):
            http.get('https://fixture.invalid/search')
        with use_runtime(other):
            http.get('https://fixture.invalid/search')
            self.assertEqual(http.close_http_connections(), 1)
        self.assertEqual([session.closed for session in self.sessions], [0, 1])
        with use_runtime(self.runtime):
            http.get('https://fixture.invalid/search')
        self.assertEqual(len(self.sessions), 2)
        self.assertEqual(len(self.sessions[0].calls), 2)
        with use_runtime(other):
            http.get('https://fixture.invalid/search')
        self.assertEqual(len(self.sessions), 3)
        self.assertEqual(http.close_thread_connections(), 2)
        self.assertEqual([session.closed for session in self.sessions], [1, 1, 1])
        with use_runtime(self.runtime):
            http.get('https://fixture.invalid/search')
        self.assertEqual(len(self.sessions), 4)

    def test_pooled_errors_do_not_retry_and_cookies_are_cleared(self):
        with use_runtime(self.runtime):
            session = http._thread_session(self.runtime)
            failure = requests.Timeout('fixture timeout')
            session.result = failure
            with self.assertRaises(requests.Timeout) as raised:
                http.get('https://fixture.invalid/search')
        self.assertIs(raised.exception, failure)
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(dict(session.cookies), {})
        record = self.records()[0]
        self.assertEqual(record['status'], 'TRANSPORT_ERROR')
        self.assertEqual(record['error_type'], 'Timeout')

    def test_pooled_retry_after_keeps_admission_and_no_extra_attempt(self):
        scheduler = RequestScheduler()
        with patch.dict(http._SCHEDULERS, {'fixture.invalid': scheduler}), use_runtime(self.runtime):
            session = http._thread_session(self.runtime)
            session.result = response(429, {'Retry-After': '3600'})
            self.assertEqual(http.get('https://fixture.invalid/search').status_code, 429)
            with self.assertRaises(http.ProviderUnavailable):
                http.get('https://fixture.invalid/search', max_wait_seconds=0)
        self.assertEqual(len(session.calls), 1)
        records = self.records()
        self.assertEqual({row['status'] for row in records}, {'HTTP_ERROR', 'RATE_LIMIT_DEFERRED'})
        deferred = next(row for row in records if row['status'] == 'RATE_LIMIT_DEFERRED')
        self.assertFalse(deferred['network_attempted'])

    def test_pooled_deadline_still_bounds_phase_timeouts(self):
        with use_runtime(self.runtime):
            http.get('https://fixture.invalid/search', timeout=(10, 60),
                deadline_monotonic=5, deadline_clock=lambda: 0)
        timeout = self.sessions[0].calls[0][2]['timeout']
        self.assertLessEqual(sum(timeout), 5.00000001)
        self.assertTrue(all(value > 0 for value in timeout))
        self.assertNotIn('deadline_monotonic', self.sessions[0].calls[0][2])

    def test_real_session_defaults_keep_environment_and_zero_hidden_retries(self):
        # No request is sent: this checks the actual factory's adapter defaults.
        with patch.object(http.requests, 'Session', requests.sessions.Session):
            with use_runtime(self.runtime):
                session = http._thread_session(self.runtime)
                self.assertTrue(session.trust_env)
                self.assertTrue(session.verify)
                self.assertIsNone(session.auth)
                self.assertEqual(session.proxies, {})
                self.assertTrue(all(adapter.max_retries.total == 0 for adapter in session.adapters.values()))
                self.assertEqual(http.close_http_connections(), 1)


if __name__ == '__main__':
    unittest.main()
