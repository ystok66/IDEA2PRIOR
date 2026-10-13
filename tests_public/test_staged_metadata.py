"""Staged discovery and shortlist metadata contracts; HTTP/model access mocked."""
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import requests

from pipelines.clients import scholarly as scholarly
from pipelines.state import Paper


def response(body):
    result = requests.Response()
    result.status_code = 200
    result._content = json.dumps(body).encode('utf-8')
    return result


def s2_record(title='Fixture', sid='a' * 40, **extra):
    return {'paperId': sid, 'title': title, 'externalIds': {}, **extra}


class StagedMetadataTests(unittest.TestCase):
    def setUp(self):
        self.cfg = SimpleNamespace(staged_retrieval=True, s2_batch_size=20)
        self.events = []
        runtime = SimpleNamespace(ledger=SimpleNamespace(
            event=lambda event, **kwargs: self.events.append({'event': event, **kwargs})), retrieval_policy={})
        runtime_patch = patch.object(scholarly, 'get_runtime', return_value=runtime)
        runtime_patch.start()
        self.addCleanup(runtime_patch.stop)
        for target in ('requests.sessions.Session.request', 'socket.socket.connect', 'socket.create_connection'):
            guard = patch(target, side_effect=AssertionError('Staged metadata tests forbid network'))
            guard.start()
            self.addCleanup(guard.stop)

    def test_discovery_defers_all_costly_enrichment_and_retains_provenance(self):
        original = Paper(title='Fixture', doi='10.1234/shared', year=2020, retrieval_source='s2_search')
        duplicate = Paper(title='Fixture', doi='10.1234/shared', retrieval_source='arxiv_search')
        old = Paper(title='Old', s2_id='b' * 40, year=2010)
        unknown = Paper(title='Unindexed evidence', arxiv_id='2401.12345v2', document_version='2401.12345v2',
                        pdf_url='https://arxiv.org/pdf/2401.12345v2', abstract='Full unindexed abstract')
        before = original.model_dump()
        with patch.object(scholarly, 'search_s2', return_value=[original, old]), \
             patch.object(scholarly, 'search_arxiv', return_value=[duplicate, unknown]), \
             patch.object(scholarly, 'batch_metadata', side_effect=AssertionError('No discovery batch')), \
             patch.object(scholarly, 'verify_publication', side_effect=AssertionError('No discovery publication lookup')):
            rows = scholarly.discover('graph AND prediction', (2019, 2025), 15, self.cfg)
        self.assertEqual(len(rows), 2)
        merged = next(paper for paper in rows if paper.doi == '10.1234/shared')
        hits = [record for record in merged.source_records if record['source'] == 'retrieval_hit']
        self.assertEqual([(hit['provider'], hit['rank']) for hit in hits], [('semantic_scholar', 1), ('arxiv', 1)])
        self.assertTrue(all(hit['query'] == 'graph AND prediction' for hit in hits))
        self.assertEqual(next(p for p in rows if p.title == 'Unindexed evidence').abstract, 'Full unindexed abstract')
        self.assertTrue(any(e.get('status') == 'OUT_OF_SCOPE' and e['title'] == 'Old' for e in self.events))
        self.assertEqual(original.model_dump(), before)

    def test_malformed_batch_record_is_retained_without_aborting_other_candidates(self):
        bad = Paper(title='Fixture', s2_id='a'*40)
        good = Paper(title='Other', s2_id='b'*40)
        payloads = [{'status':'SUCCEEDED', 'data':s2_record(abstract=123)},
                    {'status':'SUCCEEDED', 'data':s2_record(title='Other',sid='b'*40,abstract='Valid abstract')}]
        with patch.object(scholarly, 'batch_metadata', return_value=payloads), \
                patch.object(scholarly, 'verify_publication', side_effect=lambda p:p):
            result = scholarly.enrich_candidates([bad,good], self.cfg)
        self.assertEqual(result, [bad,good])
        self.assertIsNone(bad.abstract)
        self.assertEqual(good.abstract, 'Valid abstract')
        self.assertTrue(any(r['source']=='shortlist_s2_invalid' and r['metadata']['abstract']==123 for r in bad.source_records))
        self.assertTrue(any(e.get('status')=='INVALID_RESPONSE' for e in self.events))

    def test_explicit_versions_stay_separate_during_cheap_merge(self):
        papers = [Paper(title='Fixture', arxiv_id=f'2401.12345v{version}',
                        document_version=f'2401.12345v{version}', pdf_url=f'https://arxiv.org/pdf/2401.12345v{version}')
                  for version in (1, 2)]
        with patch.object(scholarly, 'search_s2', return_value=[]), \
             patch.object(scholarly, 'search_arxiv', return_value=papers):
            result = scholarly.discover('query', None, cfg=self.cfg)
        self.assertEqual(len(result), 2)
        self.assertEqual({p.document_version for p in result}, {'arxiv:2401.12345v1', 'arxiv:2401.12345v2'})
        self.assertEqual({p.pdf_url for p in result}, {'https://arxiv.org/pdf/2401.12345v1', 'https://arxiv.org/pdf/2401.12345v2'})

    def test_legacy_discovery_keeps_eager_path(self):
        s2_paper = Paper(title='A', s2_id='a' * 40)
        arxiv_paper = Paper(title='B', arxiv_id='2401.12345')
        with patch.object(scholarly, 'search_s2', return_value=[s2_paper]), \
             patch.object(scholarly, 'search_arxiv', return_value=[arxiv_paper]), \
             patch.object(scholarly, 'batch_metadata', return_value=[{'status': 'NOT_FOUND'}]) as batch, \
             patch.object(scholarly, 'verify_publication', side_effect=lambda p: p) as verify:
            result = scholarly.discover('query', None, cfg=SimpleNamespace(staged_retrieval=False, s2_batch_size=20))
        self.assertEqual(len(result), 2)
        batch.assert_called_once_with(['ARXIV:2401.12345'], 20)
        self.assertEqual(verify.call_count, 2)

    def test_shortlist_only_batches_missing_metadata_without_rekey_or_material_changes(self):
        rich = scholarly.paper_from_s2(s2_record(title='Rich', sid='c' * 40, abstract=None))
        rich.paper_id = 'p_rich'
        poor = Paper(title='Fixture', paper_id='p_locked', entity_id='entity_locked',
                     arxiv_id='2401.12345v2', document_version='arxiv:2401.12345v2',
                     pdf_url='https://arxiv.org/pdf/2401.12345v2', abstract='Version two source abstract',
                     doi='10.48550/arxiv.2401.12345')
        failed = Paper(title='Not indexed', paper_id='p_failed', doi='10.1234/missing')
        data = s2_record(externalIds={'ArXiv': '2401.12345', 'DOI': '10.1234/publication'},
                         year=2023, abstract='S2 work-level abstract', citationCount=0,
                         openAccessPdf={'url': 'https://arxiv.org/pdf/2401.12345v9'})
        with patch.object(scholarly, 'batch_metadata', return_value=[
            {'status': 'SUCCEEDED', 'data': data}, {'status': 'FAILED', 'data': None}
        ]) as batch, patch.object(scholarly, 'verify_publication', side_effect=lambda p: p) as verify:
            result = scholarly.enrich_candidates([rich, poor, failed], self.cfg)
        batch.assert_called_once_with(['ARXIV:2401.12345', 'DOI:10.1234/missing'], 20)
        self.assertEqual([id(p) for p in result], [id(rich), id(poor), id(failed)])
        self.assertEqual([p.paper_id for p in result], ['p_rich', 'p_locked', 'p_failed'])
        self.assertEqual(verify.call_count, 3)
        self.assertEqual(poor.entity_id, 'entity_locked')
        self.assertEqual(poor.document_version, 'arxiv:2401.12345v2')
        self.assertEqual(poor.pdf_url, 'https://arxiv.org/pdf/2401.12345v2')
        self.assertEqual(poor.abstract, 'Version two source abstract')
        self.assertEqual(poor.citation_count, 0)
        self.assertEqual(poor.preprint_doi, '10.48550/arxiv.2401.12345')
        self.assertEqual(poor.external_doi, '10.1234/publication')
        self.assertIn('S2 work-level abstract', poor.field_conflicts['abstract'])

    def test_shortlist_strong_id_conflict_blocks_publication_and_pdf(self):
        paper = Paper(title='Fixture', paper_id='p_stable', s2_id='a' * 40,
                      arxiv_id='2401.12345v2', pdf_url='https://arxiv.org/pdf/2401.12345v2')
        wrong = s2_record(externalIds={'ArXiv': '2402.11111'})
        with patch.object(scholarly, 'batch_metadata', return_value=[{'status': 'SUCCEEDED', 'data': wrong}]), \
             patch.object(scholarly, 'verify_publication', side_effect=AssertionError('Conflict must not reach publication lookup')):
            result = scholarly.enrich_candidates([paper], self.cfg)
        self.assertIs(result[0], paper)
        self.assertEqual(paper.identity_status, 'conflict')
        self.assertEqual(paper.arxiv_id, '2401.12345v2')
        self.assertIsNone(paper.pdf_url)
        self.assertEqual(paper.paper_id, 'p_stable')

    def test_shortlist_enrichment_does_not_drop_newly_out_of_window_year(self):
        paper = Paper(title='Fixture', doi='10.1234/article', paper_id='stable')
        def publication(p):
            p.year = 2010
            return p
        with patch.object(scholarly, 'batch_metadata', return_value=[{'status': 'NOT_FOUND'}]), \
             patch.object(scholarly, 'verify_publication', side_effect=publication):
            rows = scholarly.enrich_candidates([paper], self.cfg)
        self.assertEqual(rows, [paper])
        self.assertEqual(rows[0].year, 2010)

    def test_zero_total_without_data_is_valid_but_other_missing_arrays_fail(self):
        with patch.object(scholarly, 'get', return_value=response({'total': 0, 'offset': 0})):
            self.assertEqual(scholarly.search_s2('empty query'), [])
        self.assertEqual(self.events[-1]['status'], 'SUCCEEDED')
        for body in ({'total': 1}, {}, {'total': False}, {'total': 0, 'data': None}):
            with self.subTest(body=body), patch.object(scholarly, 'get', return_value=response(body)):
                self.assertEqual(scholarly.search_s2('broken response'), [])
                self.assertEqual(self.events[-1]['status'], 'FAILED')

    def test_markup_comparison_preserves_text_and_does_not_add_fuzzy_identity(self):
        for left, right in (
            ('A <i>Graph</i> Model', 'A Graph Model'),
            ('A &amp; B', 'A & B'),
            ('Bayes<i>ian</i> X<sup>2</sup>', 'Bayesian X2'),
            ('A <jats:italic>latent</jats:italic> graph', 'A latent graph'),
        ):
            with self.subTest(left=left):
                self.assertTrue(scholarly.title_matches(left, right))
        self.assertFalse(scholarly.title_matches('Graph prediction', 'Graph classification'))
        self.assertFalse(scholarly.title_matches('x<y>z', 'xz'))

    def test_staged_seed_s2_null_recovers_exact_arxiv_version_without_publication_lookup(self):
        seed = {'title': 'Fixture', 'arxiv_id': '2401.12345v2'}
        source = Paper(title='Fixture', arxiv_id='2401.12345v2', document_version='2401.12345v2',
                       pdf_url='https://arxiv.org/pdf/2401.12345v2', preprint_published='2024-01-01')
        with patch.object(scholarly, 'batch_metadata', return_value=[{'status': 'NOT_FOUND'}]) as batch, \
             patch.object(scholarly, 'search_arxiv', return_value=[source]) as arxiv, \
             patch.object(scholarly, 'search_s2', side_effect=AssertionError('Do not broaden an exact ID')), \
             patch.object(scholarly, 'verify_publication', side_effect=AssertionError('Publication lookup deferred')):
            result = scholarly.validate_seeds([seed], self.cfg)
        self.assertEqual(len(result), 1)
        arxiv.assert_called_once_with('', limit=1, id_list=['2401.12345v2'])
        self.assertEqual(batch.call_count, 1)
        self.assertEqual(result[0].document_version, '2401.12345v2')
        self.assertIsNone(result[0].year)
        self.assertEqual(result[0].publication_status, 'unverified')

    def test_staged_seed_explicit_conflict_cannot_be_repaired_by_title(self):
        with patch.object(scholarly, 'batch_metadata', return_value=[{'status': 'IDENTITY_CONFLICT'}]), \
             patch.object(scholarly, 'search_arxiv', side_effect=AssertionError('Do not recover conflicts')), \
             patch.object(scholarly, 'search_s2', side_effect=AssertionError('Do not recover conflicts')):
            self.assertEqual(scholarly.validate_seeds([{'title': 'Fixture', 'arxiv_id': '2401.12345'}], self.cfg), [])

    def test_staged_seed_available_arxiv_does_not_assert_missing_s2_id(self):
        seed = {'title': 'Fixture', 'arxiv_id': '2401.12345v2', 's2_id': 'a' * 40}
        source = Paper(title='Fixture', arxiv_id='2401.12345v2')
        with patch.object(scholarly, 'batch_metadata', return_value=[{'status': 'FAILED'}]), \
             patch.object(scholarly, 'search_arxiv', return_value=[source]), \
             patch.object(scholarly, 'verify_publication', side_effect=AssertionError('Deferred')):
            result = scholarly.validate_seeds([seed], self.cfg)
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0].s2_id)
        self.assertTrue(any(event.get('status') == 'UNVERIFIED_IDENTITY_FIELDS' for event in self.events))
        self.assertEqual(result[0].source_records[-1]['metadata'], seed)

    def test_staged_seed_wrong_returned_version_is_rejected(self):
        source = Paper(title='Fixture', arxiv_id='2401.12345v1', document_version='2401.12345v1')
        with patch.object(scholarly, 'batch_metadata', return_value=[{'status': 'NOT_FOUND'}]), \
             patch.object(scholarly, 'search_arxiv', return_value=[source]):
            result = scholarly.validate_seeds([{'title': 'Fixture', 'arxiv_id': '2401.12345v2'}], self.cfg)
        self.assertEqual(result, [])
        self.assertTrue(any(event.get('stage') == 'seed_version' and event.get('status') == 'IDENTITY_CONFLICT' for event in self.events))

    def test_staged_seed_s2_success_defers_publication_and_preserves_requested_pdf(self):
        data = s2_record(externalIds={'ArXiv': '2401.12345'}, openAccessPdf={'url': 'https://arxiv.org/pdf/2401.12345v9'})
        with patch.object(scholarly, 'batch_metadata', return_value=[{'status': 'SUCCEEDED', 'data': data}]), \
             patch.object(scholarly, 'verify_publication', side_effect=AssertionError('Deferred')):
            result = scholarly.validate_seeds([{'title': 'Fixture', 'arxiv_id': '2401.12345v2'}], self.cfg)
        self.assertEqual(result[0].pdf_url, 'https://arxiv.org/pdf/2401.12345v2')
        self.assertEqual(result[0].document_version, '2401.12345v2')


if __name__ == '__main__':
    unittest.main()
