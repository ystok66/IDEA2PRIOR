import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import requests
from pipelines.clients import http_transport as http
from pipelines.runtime import LLMRuntime, RunLedger, use_runtime

TMP = Path(__file__).resolve().parents[1] / '.local/test_artifacts'
TMP.mkdir(parents=True, exist_ok=True)


class HTTPTransportTests(unittest.TestCase):
    def test_metadata_identifier_normalizes_version_but_source_keeps_version(self):
        from pipelines.clients.s2_ids import paper_id_path
        from pipelines.tasks.prior_art_retrieval import extract_arxiv_id_from_url
        self.assertEqual(paper_id_path('https://doi.org/10.1234/a?b#c'), 'DOI:10.1234/a%3Fb%23c')
        self.assertEqual(paper_id_path('2401.12345v2'), 'ARXIV:2401.12345')
        self.assertEqual(extract_arxiv_id_from_url('https://arxiv.org/pdf/2401.12345v2.pdf'), '2401.12345v2')

    def test_full_body_recorded_and_credentials_redacted(self):
        response = SimpleNamespace(status_code=200, ok=True, headers={}, json=lambda:{'data':['original']})
        with tempfile.TemporaryDirectory(dir=TMP) as temp:
            runtime = LLMRuntime(RunLedger(Path(temp)))
            with use_runtime(runtime), patch.object(http.requests, 'get', return_value=response) as get:
                self.assertIs(http.get('https://fixture.invalid/works', params={'api_key':'test-key-example-test-secret-value', 'q':'complete idea'}), response)
            get.assert_called_once()
            self.assertEqual(get.call_args.kwargs['timeout'], (10,60))
            text = next((Path(temp)/'http_attempts').glob('*.json')).read_text(encoding='utf-8')
            self.assertNotIn('test-key-example-test-secret-value', text)
            record = json.loads(text)
            self.assertEqual(record['body']['data'], ['original'])
            self.assertEqual(record['status'], 'SUCCEEDED')

    def test_http_failure_is_preserved_without_hidden_retry(self):
        response = SimpleNamespace(status_code=429, ok=False, headers={'Retry-After':'0'}, json=lambda:{'error':'rate limited'})
        with tempfile.TemporaryDirectory(dir=TMP) as temp:
            with use_runtime(LLMRuntime(RunLedger(Path(temp)))), patch.object(http.requests, 'get', return_value=response) as get:
                self.assertIs(http.get('https://fixture.invalid/works'), response)
            get.assert_called_once()
            record=json.loads(next((Path(temp)/'http_attempts').glob('*.json')).read_text(encoding='utf-8'))
            self.assertEqual(record['status'], 'HTTP_ERROR')
            self.assertEqual(record['http_status'], 429)

    def test_transport_failure_has_explicit_status(self):
        with tempfile.TemporaryDirectory(dir=TMP) as temp:
            with use_runtime(LLMRuntime(RunLedger(Path(temp)))), patch.object(http.requests,'get',side_effect=requests.Timeout('fixture')) as get:
                with self.assertRaises(requests.Timeout):
                    http.get('https://fixture.invalid/works')
            get.assert_called_once()
            record=json.loads(next((Path(temp)/'http_attempts').glob('*.json')).read_text(encoding='utf-8'))
            self.assertEqual(record['status'], 'TRANSPORT_ERROR')

    def test_only_explicit_retry_chain_can_close_an_http_failure(self):
        bad = SimpleNamespace(status_code=429, ok=False, headers={}, json=lambda:{'error':'rate limited'})
        good = SimpleNamespace(status_code=200, ok=True, headers={}, json=lambda:{'data':[]})
        with tempfile.TemporaryDirectory(dir=TMP) as temp:
            with use_runtime(LLMRuntime(RunLedger(Path(temp)))), patch.object(http.requests,'get',side_effect=[bad,good,bad,good]):
                http.get('https://fixture.invalid/works', logical_request_id='declared', attempt_ordinal=1)
                http.get('https://fixture.invalid/works', logical_request_id='declared', attempt_ordinal=2)
                http.get('https://fixture.invalid/works')
                http.get('https://fixture.invalid/works')
            summary = http.summarize_attempts(temp)
            self.assertEqual(summary['attempt_count'], 4)
            self.assertEqual(summary['logical_request_count'], 3)
            self.assertEqual(len(summary['failed_requests']), 1)

    def test_success_status_with_invalid_json_is_not_successful_search(self):
        def invalid():
            raise ValueError('invalid JSON')
        response = SimpleNamespace(status_code=200, ok=True, headers={}, text='<html>error</html>', json=invalid)
        with tempfile.TemporaryDirectory(dir=TMP) as temp:
            with use_runtime(LLMRuntime(RunLedger(Path(temp)))), patch.object(http.requests,'get',return_value=response):
                http.get('https://fixture.invalid/works')
            self.assertEqual(http.summarize_attempts(temp)['failed_requests'][0]['status'], 'INVALID_JSON_RESPONSE')


if __name__ == '__main__':
    unittest.main()
