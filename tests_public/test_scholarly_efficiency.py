"""Opt-in request reuse/grouping contracts; providers and sockets are offline."""
import copy
import json
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

import requests

from pipelines.clients import scholarly as s
from pipelines.state import Paper


def response(body=None, status=200, text=None):
    result = requests.Response()
    result.status_code = status
    result._content = (text if text is not None else json.dumps(body)).encode('utf-8')
    return result


def record(number=1, **extra):
    return {'paperId': f'{number:040x}', 'title': f'Fixture {number}',
            'externalIds': {}, **extra}


def atom(identifier='2401.00001v2'):
    return ('<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
            f'<id>http://arxiv.org/abs/{identifier}</id><title>Fixture 1</title>'
            '<summary>Evidence</summary></entry></feed>')


class ScholarlyEfficiencyTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.runtime = SimpleNamespace(
            ledger=SimpleNamespace(event=lambda event, **details: self.events.append(
                {'event': event, **details})),
            retrieval_policy={'optimize_scholarly_requests': True})
        self.cfg = SimpleNamespace(staged_retrieval=True, s2_batch_size=100,
                                   optimize_scholarly_requests=True)
        for target, kwargs in (
            ('pipelines.clients.scholarly.get_runtime', {'return_value': self.runtime}),
            ('pipelines.clients.scholarly._headers', {'return_value': {}}),
            ('pipelines.clients.scholarly._base', {'return_value': 'https://fixture.invalid/graph/v1'}),
            ('requests.sessions.Session.request', {'side_effect': AssertionError('Offline test forbids HTTP')}),
            ('socket.socket.connect', {'side_effect': AssertionError('Offline test forbids sockets')}),
            ('socket.create_connection', {'side_effect': AssertionError('Offline test forbids sockets')}),
        ):
            guard = patch(target, **kwargs)
            guard.start()
            self.addCleanup(guard.stop)
        # Retry behavior has its own fixed-clock tests. These fixtures count
        # logical provider calls without spending real retry/backoff time.
        retry = patch.object(s, 'call_readonly', side_effect=lambda operation, url, **kwargs:
                             operation(url, **{key: value for key, value in kwargs.items()
                                              if key not in ('method', 'read_only_batch')}))
        retry.start()
        self.addCleanup(retry.stop)

    def parallel(self, function, values):
        with ThreadPoolExecutor(max_workers=8) as executor:
            return list(executor.map(function, values))

    def test_exact_effective_query_singleflight_and_independent_consumers(self):
        def provider(*args, **kwargs):
            time.sleep(.02)
            return response({'data': [record()]})
        queries = ['"latent graph" AND prediction', 'latent graph prediction'] * 4
        with patch.object(s, 'get', side_effect=provider) as get:
            results = self.parallel(lambda query: s.search_s2(query, (2015, 2025), 20), queries)
            results[0][0].title = 'Changed locally'
            results[0][0].source_records[0]['metadata']['title'] = 'Changed source locally'
            repeated = s.search_s2(queries[0], (2015, 2025), 20)
        self.assertEqual(get.call_count, 1)
        self.assertTrue(all(rows[0].title == 'Fixture 1' for rows in results[1:]))
        self.assertEqual(repeated[0].source_records[0]['metadata']['title'], 'Fixture 1')
        events = [event for event in self.events if event.get('stage') == 's2_search']
        self.assertEqual(sorted(event['query'] for event in events[:-1]), sorted(queries))
        self.assertEqual(sum(event['cache_hit'] for event in events), 8)

    def test_search_key_separates_years_limits_fields_endpoint_auth_and_runs(self):
        with patch.object(s, 'get', return_value=response({'data': [record()]})) as get:
            s.search_s2('idea', (2015, 2025), 20)
            s.search_s2('idea', (2015, 2025), 20)
            s.search_s2('idea', (2020, 2025), 20)
            s.search_s2('idea', (2015, 2025), 15)
            with patch.object(s, 'FIELDS', 'paperId,title'):
                s.search_s2('idea', (2015, 2025), 20)
            with patch.object(s, '_base', return_value='https://other.invalid/graph/v1'):
                s.search_s2('idea', (2015, 2025), 20)
            with patch.object(s, '_headers', return_value={'x-api-key': 'test-key-example-fixture-credential'}):
                s.search_s2('idea', (2015, 2025), 20)
            other = SimpleNamespace(ledger=self.runtime.ledger,
                                    retrieval_policy={'optimize_scholarly_requests': True})
            with patch.object(s, 'get_runtime', return_value=other):
                s.search_s2('idea', (2015, 2025), 20)
        self.assertEqual(get.call_count, 7)
        self.assertNotIn('test-key-example-fixture-credential', repr(self.runtime.s2_search_cache))

    def test_disabled_query_cache_keeps_provider_calls(self):
        self.runtime.retrieval_policy['optimize_scholarly_requests'] = False
        with patch.object(s, 'get', return_value=response({'data': [record()]})) as get:
            s.search_s2('idea')
            s.search_s2('idea')
        self.assertEqual(get.call_count, 2)

    def test_valid_zero_search_is_cached_but_errors_and_invalid_rows_are_retryable(self):
        with patch.object(s, 'get', return_value=response({'total': 0})) as get:
            self.assertEqual(s.search_s2('empty'), [])
            self.assertEqual(s.search_s2('empty'), [])
        self.assertEqual(get.call_count, 1)
        for failed in (response({}, 429), response({}, 500), response({'data': None}),
                       response({'data': [None]}), response({'data': [{'title': 'Missing ID'}]}),
                       response({'data': [record(abstract=123)]})):
            other = SimpleNamespace(ledger=self.runtime.ledger,
                                    retrieval_policy={'optimize_scholarly_requests': True})
            with self.subTest(body=failed.text), patch.object(s, 'get_runtime', return_value=other), \
                    patch.object(s, 'get', side_effect=[failed, response({'data': [record()]})]) as get:
                self.assertEqual(s.search_s2('retry'), [])
                self.assertEqual(s.search_s2('retry')[0].title, 'Fixture 1')
                self.assertEqual(get.call_count, 2)

    def test_cached_search_warms_metadata_aliases_and_keeps_discovery_query_provenance(self):
        data = record(externalIds={'DOI': '10.1234/fixture', 'ArXiv': '2401.00001'})
        with patch.object(s, 'get', return_value=response({'data': [data]})) as get, \
                patch.object(s, 'search_arxiv', return_value=[]), \
                patch.object(s, 'post', side_effect=AssertionError('Search already supplied metadata')):
            first = s.discover('"latent graph"', (2015, 2025), 20, self.cfg)
            second = s.discover('latent graph', (2015, 2025), 20, self.cfg)
            detail = s.batch_metadata(['DOI:10.1234/fixture', 'ARXIV:2401.00001'])
        self.assertEqual(get.call_count, 1)
        self.assertTrue(all(row['status'] == 'SUCCEEDED' for row in detail))
        self.assertEqual(next(row['query'] for row in first[0].source_records
                              if row['source'] == 'retrieval_hit'), '"latent graph"')
        self.assertEqual(next(row['query'] for row in second[0].source_records
                              if row['source'] == 'retrieval_hit'), 'latent graph')

    def test_overlapping_metadata_batches_coalesce_and_preserve_slots(self):
        calls = []
        def provider(url, **kwargs):
            ids = kwargs['json']['ids']
            calls.extend(ids)
            time.sleep(.01)
            return response([record(int(identifier, 16)) for identifier in ids])
        a, b, c = [f'{number:040x}' for number in (1, 2, 3)]
        batches = [[b, a, a], [c, b], [a, c]] * 3
        with patch.object(s, 'post', side_effect=provider):
            results = self.parallel(s.batch_metadata, batches)
            results[0][0]['data']['title'] = 'Changed locally'
            cached = s.batch_metadata([b])
        self.assertEqual(sorted(calls), [a, b, c])
        self.assertEqual([len(rows) for rows in results], [len(ids) for ids in batches])
        self.assertTrue(all([row['requested_id'] for row in rows] == ids
                            for rows, ids in zip(results, batches)))
        self.assertEqual(cached[0]['data']['title'], 'Fixture 2')

    def test_metadata_cache_scope_changes_and_failed_batch_remains_retryable(self):
        with patch.object(s, 'post', return_value=response([record()])) as post:
            s.batch_metadata([f'{1:040x}'])
            with patch.object(s, '_base', return_value='https://other.invalid/graph/v1'):
                s.batch_metadata([f'{1:040x}'])
            with patch.object(s, '_headers', return_value={'x-api-key': 'fixture-key'}):
                s.batch_metadata([f'{1:040x}'])
        self.assertEqual(post.call_count, 3)
        with patch.object(s, 'post', side_effect=[response({}, 500), response([record(2)])]) as post:
            self.assertEqual(s.batch_metadata([f'{2:040x}'])[0]['status'], 'FAILED')
            self.assertEqual(s.batch_metadata([f'{2:040x}'])[0]['status'], 'SUCCEEDED')
        self.assertEqual(post.call_count, 2)

    def test_crossref_singleflight_copies_results_and_404_is_provider_scoped(self):
        def provider(*args, **kwargs):
            time.sleep(.01)
            return response({'message': {'DOI': '10.1234/fixture', 'title': ['Fixture 1']}})
        with patch.object(s, 'get', side_effect=provider) as get:
            results = self.parallel(s.crossref_lookup, ['10.1234/fixture'] * 8)
            results[0]['title'][0] = 'Changed locally'
            cached = s.crossref_lookup('DOI:10.1234/FIXTURE')
        self.assertEqual(get.call_count, 1)
        self.assertEqual(cached['title'], ['Fixture 1'])
        with patch.object(s, 'get', return_value=response({}, 404)) as get:
            self.assertEqual(self.parallel(s.crossref_lookup, ['10.1234/missing'] * 8), [None] * 8)
        self.assertEqual(get.call_count, 1)

    def test_crossref_failures_and_identity_conflicts_do_not_poison_cache(self):
        valid = response({'message': {'DOI': '10.1234/fixture', 'title': ['Fixture 1']}})
        for failed in (response({}, 500), response({'message': {'DOI': '10.1234/wrong'}})):
            other = SimpleNamespace(ledger=self.runtime.ledger,
                                    retrieval_policy={'optimize_scholarly_requests': True})
            with patch.object(s, 'get_runtime', return_value=other), \
                    patch.object(s, 'get', side_effect=[failed, valid]) as get:
                self.assertIsNone(s.crossref_lookup('10.1234/fixture'))
                self.assertIsNotNone(s.crossref_lookup('10.1234/fixture'))
            self.assertEqual(get.call_count, 2)

    def test_arxiv_singleflight_keeps_versions_and_returns_copies(self):
        def provider(*args, **kwargs):
            time.sleep(.01)
            return response(text=atom())
        with patch.object(s, 'get', side_effect=provider) as get:
            results = self.parallel(lambda _: s.search_arxiv('', 1, ['2401.00001v2']), range(8))
            results[0][0].title = 'Changed locally'
            repeated = s.search_arxiv('', 1, ['2401.00001v2'])
            s.search_arxiv('', 1, ['2401.00001v1'])
        self.assertEqual(get.call_count, 2)
        self.assertEqual(repeated[0].title, 'Fixture 1')
        self.assertEqual(repeated[0].document_version, '2401.00001v2')

    def test_arxiv_failure_not_cached_and_successful_zero_is_cached(self):
        with patch.object(s, 'get', side_effect=[response({}, 500), response(text=atom())]) as get:
            self.assertEqual(s.search_arxiv('', 1, ['2401.00001v2']), [])
            self.assertEqual(len(s.search_arxiv('', 1, ['2401.00001v2'])), 1)
        self.assertEqual(get.call_count, 2)
        with patch.object(s, 'get', return_value=response(text='<feed xmlns="http://www.w3.org/2005/Atom"/>')) as get:
            self.assertEqual(s.search_arxiv('', 1, ['2401.00009v2']), [])
            self.assertEqual(s.search_arxiv('', 1, ['2401.00009v2']), [])
        self.assertEqual(get.call_count, 1)

    def test_versioned_fallback_batch_preserves_order_duplicates_and_partial_missing(self):
        seeds = [{'title': f'Fixture {number}', 'arxiv_id': f'2401.0000{number}v2'}
                 for number in (2, 1, 3, 1)]
        found = [Paper(title='Fixture 1', arxiv_id='2401.00001v2'),
                 Paper(title='Fixture 2', arxiv_id='2401.00002v2')]
        before = copy.deepcopy(seeds)
        with patch.object(s, 'batch_metadata', return_value=[{'status': 'NOT_FOUND'}] * 4), \
                patch.object(s, 'search_arxiv', return_value=found) as arxiv:
            results = s.validate_seeds(seeds, self.cfg)
        arxiv.assert_called_once_with('', limit=3, id_list=['2401.00002v2', '2401.00001v2', '2401.00003v2'])
        self.assertEqual([paper.title for paper in results], ['Fixture 2', 'Fixture 1', 'Fixture 1'])
        self.assertEqual(seeds, before)
        self.assertIsNot(results[1], results[2])
        self.assertEqual([len([row for row in paper.source_records if row['source'] == 'llm_seed_input'])
                          for paper in results], [1, 1, 1])

    def test_versioned_fallback_rejects_wrong_version_title_and_existing_conflicts(self):
        seeds = [{'title': 'Fixture 1', 'arxiv_id': '2401.00001v2'},
                 {'title': 'Fixture 2', 'arxiv_id': '2401.00002v2'},
                 {'title': 'Fixture 3', 'arxiv_id': '2401.00003v2'}]
        found = [Paper(title='Fixture 1', arxiv_id='2401.00001v1'),
                 Paper(title='Unrelated title', arxiv_id='2401.00002v2')]
        with patch.object(s, 'batch_metadata', return_value=[{'status': 'NOT_FOUND'},
                {'status': 'NOT_FOUND'}, {'status': 'IDENTITY_CONFLICT'}]), \
                patch.object(s, 'search_arxiv', return_value=found) as arxiv:
            self.assertEqual(s.validate_seeds(seeds, self.cfg), [])
        arxiv.assert_called_once_with('', limit=2, id_list=['2401.00001v2', '2401.00002v2'])
        self.assertTrue(any(event.get('stage') == 'seed_version' and
                            event.get('status') == 'IDENTITY_CONFLICT' for event in self.events))

    def test_versioned_fallback_chunks_100_without_losing_ids(self):
        seeds = [{'title': f'Fixture {number}', 'arxiv_id': f'2401.{number:05d}v2'}
                 for number in range(1, 102)]
        def provider(query, limit, id_list):
            self.assertEqual(limit, len(id_list))
            return [Paper(title=f'Fixture {int(identifier[5:10])}', arxiv_id=identifier)
                    for identifier in reversed(id_list)]
        with patch.object(s, 'batch_metadata', return_value=[{'status': 'NOT_FOUND'}] * 101), \
                patch.object(s, 'search_arxiv', side_effect=provider) as arxiv:
            results = s.validate_seeds(seeds, self.cfg)
        self.assertEqual(arxiv.call_count, 2)
        self.assertEqual([len(call.kwargs['id_list']) for call in arxiv.call_args_list], [100, 1])
        self.assertEqual([paper.title for paper in results], [seed['title'] for seed in seeds])

    def test_failed_version_batch_recovers_individual_ids_without_caching_failure(self):
        seeds = [{'title': 'Fixture 1', 'arxiv_id': '2401.00001v2'},
                 {'title': 'Fixture 1', 'arxiv_id': '2401.00002v2'}]
        with patch.object(s, 'batch_metadata', return_value=[{'status': 'NOT_FOUND'}] * 2), \
                patch.object(s, 'get', side_effect=[response({}, 500), response(text=atom()),
                                                   response(text=atom('2401.00002v2'))]) as get:
            results = s.validate_seeds(seeds, self.cfg)
        self.assertEqual(get.call_count, 3)
        self.assertEqual([paper.arxiv_id for paper in results], ['2401.00001v2', '2401.00002v2'])
        self.assertEqual(len(self.runtime.arxiv_cache), 2)

    def test_successful_empty_version_batch_does_not_expand_into_singletons(self):
        seeds = [{'title': 'Fixture 1', 'arxiv_id': '2401.00001v2'},
                 {'title': 'Fixture 2', 'arxiv_id': '2401.00002v2'}]
        with patch.object(s, 'batch_metadata', return_value=[{'status': 'NOT_FOUND'}] * 2), \
                patch.object(s, 'get', return_value=response(
                    text='<feed xmlns="http://www.w3.org/2005/Atom"/>')) as get:
            self.assertEqual(s.validate_seeds(seeds, self.cfg), [])
        self.assertEqual(get.call_count, 1)

    def test_batched_and_legacy_versioned_fallback_have_identical_candidate_records(self):
        seeds = [{'title': f'Fixture {number}', 'arxiv_id': f'2401.0000{number}v2'}
                 for number in (2, 1, 2)]
        def provider(query, limit, id_list):
            return [Paper(title=f'Fixture {int(identifier[5:10])}', arxiv_id=identifier,
                          pdf_url='https://arxiv.org/pdf/' + identifier,
                          preprint_published='2024-01-01', abstract='Fixed source evidence')
                    for identifier in reversed(id_list)]
        records = []
        call_counts = []
        for enabled in (False, True):
            cfg = SimpleNamespace(staged_retrieval=True, s2_batch_size=100,
                                  optimize_scholarly_requests=enabled)
            with patch.object(s, 'batch_metadata', return_value=[{'status': 'NOT_FOUND'}] * 3), \
                    patch.object(s, 'search_arxiv', side_effect=provider) as arxiv:
                records.append([paper.model_dump() for paper in s.validate_seeds(seeds, cfg)])
                call_counts.append(arxiv.call_count)
        self.assertEqual(records[0], records[1])
        self.assertEqual(call_counts, [3, 1])

    def test_disabled_batching_and_unversioned_ids_keep_singleton_path(self):
        seeds = [{'title': 'Fixture 1', 'arxiv_id': '2401.00001v2'},
                 {'title': 'Fixture 1', 'arxiv_id': '2401.00002v2'}]
        disabled = SimpleNamespace(staged_retrieval=True, s2_batch_size=100,
                                   optimize_scholarly_requests=False)
        with patch.object(s, 'batch_metadata', return_value=[{'status': 'NOT_FOUND'}] * 2), \
                patch.object(s, 'search_arxiv', side_effect=lambda query, limit, id_list:
                             [Paper(title='Fixture 1', arxiv_id=id_list[0])]) as arxiv:
            self.assertEqual(len(s.validate_seeds(seeds, disabled)), 2)
        self.assertEqual(arxiv.call_count, 2)
        unversioned = [{'title': 'Fixture 1', 'arxiv_id': '2401.00003'}]
        with patch.object(s, 'batch_metadata', return_value=[{'status': 'NOT_FOUND'}]), \
                patch.object(s, 'search_arxiv', return_value=[Paper(title='Fixture 1', arxiv_id='2401.00003v4')]) as arxiv:
            self.assertEqual(len(s.validate_seeds(unversioned, self.cfg)), 1)
        arxiv.assert_called_once_with('', limit=1, id_list=['2401.00003'])


if __name__ == '__main__':
    unittest.main()
