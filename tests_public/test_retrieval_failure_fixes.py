"""Real failure inputs with offline provider fixtures; no scientific pilot."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import requests
from pipelines.clients import scholarly as s
from pipelines.identity import upsert_paper
from pipelines.runtime import LLMRuntime, RunLedger, use_runtime
from pipelines.state import Paper, PipelineState, Intent

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((Path(__file__).parent / 'fixtures/retrieval_failure_inputs.json').read_text(encoding='utf-8'))


def response(body, status=200):
    value = requests.Response()
    value.status_code = status
    value._content = json.dumps(body).encode()
    value.headers['Content-Type'] = 'application/json'
    return value


class FailureRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.runtime = LLMRuntime(RunLedger(Path(self.temp.name)))
        self.runtime.retrieval_policy = {'retrieval_backend': 's2_arxiv_crossref_v1', 'enable_openalex': False}
        self.guard = patch('socket.socket.connect', side_effect=AssertionError('Offline tests only'))
        self.guard.start(); self.addCleanup(self.guard.stop)

    def test_recorded_versioned_ids_use_real_successful_work_response(self):
        ids = ['ARXIV:2101.09486v1', 'ARXIV:2205.11894v1']
        def api(url, **kw):
            self.assertEqual(kw['json']['ids'], ['ARXIV:2101.09486', 'ARXIV:2205.11894'])
            return response(FIXTURE['s2_real_response'])
        with use_runtime(self.runtime), patch.object(s, 'post', side_effect=api) as post:
            rows = s.batch_metadata(ids)
        self.assertEqual(post.call_count, 1)
        self.assertTrue(all(row['status'] == 'SUCCEEDED' for row in rows))
        self.assertEqual([row['requested_id'] for row in rows], ids)

    def test_48_slots_preserved_with_40_unique_work_queries(self):
        inputs = FIXTURE['bad_batch_slots']
        def api(url, **kw):
            ids = kw['json']['ids']
            self.assertEqual(len(ids), 40)
            self.assertTrue(all('v' not in i[6:] for i in ids))
            return response([None] * len(ids))
        with use_runtime(self.runtime), patch.object(s, 'post', side_effect=api) as post:
            rows = s.batch_metadata(inputs)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(len(rows), 48)
        self.assertEqual([r['requested_id'] for r in rows], inputs)

    def test_s2_lookup_does_not_strip_doi_suffix_or_source_version(self):
        self.assertEqual(s.s2_lookup_id('10.1234/foo.v1'), 'DOI:10.1234/foo.v1')
        self.assertEqual(s.s2_lookup_id('10.1234/paper.pdf'), 'DOI:10.1234/paper.pdf')
        self.assertEqual(s.s2_id('https://arxiv.org/pdf/2101.09486v2.pdf'), 'ARXIV:2101.09486v2')
        paper = Paper(title='X', arxiv_id='2101.09486v2', document_version='2101.09486v2', pdf_url='https://arxiv.org/pdf/2101.09486v2')
        before = paper.model_dump()
        self.assertEqual(s.paper_lookup_id(paper), 'ARXIV:2101.09486')
        self.assertEqual(paper.model_dump(), before)

    def test_null_cached_per_run_400_and_500_not_not_found(self):
        with use_runtime(self.runtime), patch.object(s, 'post', return_value=response([None])) as post:
            s.batch_metadata(['ARXIV:2101.09486v1'])
            s.batch_metadata(['ARXIV:2101.09486v2'])
            self.assertEqual(post.call_count, 1)
        other = LLMRuntime(RunLedger(Path(self.temp.name) / 'other'))
        with use_runtime(other), patch.object(s, 'post', side_effect=[response({'error': 'No valid paper ids given'}, 400), response(FIXTURE['s2_real_response'][:1])]) as post:
            first = s.batch_metadata(['ARXIV:2101.09486'])
            second = s.batch_metadata(['ARXIV:2101.09486'])
        self.assertEqual(first[0]['status'], 'FAILED')
        self.assertEqual(second[0]['status'], 'SUCCEEDED')
        self.assertEqual(post.call_count, 2)

    def test_33_real_arxiv_dois_never_query_crossref(self):
        with use_runtime(self.runtime), patch.object(s, 'get', side_effect=AssertionError('Wrong DOI agency')), patch.object(s, 'datacite_lookup', side_effect=AssertionError('Reuse Atom metadata')):
            for doi in FIXTURE['arxiv_dois']:
                self.assertIsNone(s.crossref_lookup(doi=doi))
                paper = Paper(title='Fixture', doi=doi, arxiv_id=s.arxiv_doi_id(doi), preprint_published='2020-01-01', retrieval_source='arxiv_search')
                s.verify_publication(paper)
                self.assertEqual(paper.publication_status, 'preprint')
                self.assertIsNone(paper.publication_doi)

    def test_crossref_negative_cache_and_transient_failure_recovery(self):
        with use_runtime(self.runtime), patch.object(s, 'get', return_value=response({}, 404)) as get:
            s.crossref_lookup(doi='10.1234/test')
            s.crossref_lookup(doi='https://doi.org/10.1234/TEST')
            self.assertEqual(get.call_count, 1)
        data = {'message': {'DOI': '10.1234/other', 'title': ['Test']}}
        with use_runtime(self.runtime), patch.object(s, 'get', side_effect=[response({}, 500), response(data)]) as get:
            self.assertIsNone(s.crossref_lookup(doi='10.1234/other'))
            self.assertIsNotNone(s.crossref_lookup(doi='10.1234/other'))
            self.assertEqual(get.call_count, 2)

    def test_datacite_dates_do_not_become_publication_year(self):
        attrs = {'titles': [{'title': 'Fixture'}], 'types': {'resourceTypeGeneral': 'Preprint'}, 'publicationYear': 2017, 'registered': '2022-01-01', 'created': '2022-01-01'}
        paper = Paper(title='Fixture', doi='10.48550/arxiv.2101.09486', arxiv_id='2101.09486v2', document_version='2101.09486v2', preprint_published='2021-01-01')
        with patch.object(s, 'datacite_lookup', return_value=attrs), patch.object(s, 'crossref_lookup', side_effect=AssertionError('Wrong agency')):
            s.verify_publication(paper)
        self.assertIsNone(paper.year)
        self.assertEqual(paper.document_version, '2101.09486v2')
        self.assertEqual(paper.preprint_metadata_dates['datacite']['registered'], '2022-01-01')
        self.assertEqual(paper.publication_status, 'preprint')

    def test_crossref_posted_content_and_unknown_types_not_formal_publication(self):
        for kind in ['posted-content', None]:
            data = {'title': ['Fixture'], 'type': kind, 'subtype': 'preprint', 'published': {'date-parts': [[2022]]}}
            paper = Paper(title='Fixture', doi='10.1234/a', year=2017, publication_year_source='semantic_scholar')
            with patch.object(s, 'crossref_lookup', return_value=data):
                s.verify_publication(paper)
            self.assertNotEqual(paper.publication_status, 'published_metadata_verified')
            self.assertIsNone(paper.publication_doi)
            self.assertEqual(paper.year, 2017)

    def test_both_dois_survive_and_only_typed_publication_sets_year(self):
        paper = Paper(title='Fixture', doi='10.48550/arxiv.2101.09486', year=2021)
        s.assign_doi(paper, paper.doi)
        s.assign_doi(paper, '10.1234/publication')
        data = {'title': ['Fixture'], 'type': 'journal-article', 'published': {'date-parts': [[2023]]}}
        with patch.object(s, 'crossref_lookup', return_value=data):
            s.verify_publication(paper)
        self.assertEqual(paper.preprint_doi, '10.48550/arxiv.2101.09486')
        self.assertEqual(paper.publication_doi, '10.1234/publication')
        self.assertEqual(paper.year, 2023)
        self.assertEqual(paper.field_conflicts['year'], [2021, 2023])

    def test_crossref_404_routes_to_other_agency(self):
        paper = Paper(title='Fixture', doi='10.5281/zenodo.123')
        attrs = {'titles': [{'title': 'Fixture'}], 'types': {'resourceTypeGeneral': 'Preprint'}}
        with use_runtime(self.runtime), patch.object(s, 'get', return_value=response({}, 404)), patch.object(s, 'doi_agency', return_value='datacite') as agency, patch.object(s, 'datacite_lookup', return_value=attrs):
            s.verify_publication(paper)
        agency.assert_called_once_with('10.5281/zenodo.123')
        self.assertEqual(paper.metadata_agency, 'datacite')
        self.assertEqual(paper.publication_status, 'preprint')

    def test_title_cannot_override_explicit_seed_identity(self):
        seeds = [{'title': 'Fixture', 'arxiv_id': '2101.09486'}]
        bad = {'paperId': 'a'*40, 'title': 'Other paper', 'externalIds': {'ArXiv': '2101.09486'}}
        with use_runtime(self.runtime), patch.object(s, 'post', return_value=response([bad])), patch.object(s, 'search_s2', side_effect=AssertionError('Must not recover conflict by title')):
            self.assertEqual(s.validate_seeds(seeds), [])
        self.assertFalse(s.seed_matches(seeds[0], Paper(title='Fixture', arxiv_id='2205.11894')))

    def test_seed_version_url_survives_unversioned_arxiv_field(self):
        data = {'paperId': 'a'*40, 'title': 'Fixture', 'externalIds': {'ArXiv': '2101.09486'}}
        seed = {'title': 'Fixture', 'arxiv_id': '2101.09486', 'url': 'https://arxiv.org/abs/2101.09486v2'}
        with use_runtime(self.runtime), patch.object(s, 'post', return_value=response([data])):
            paper = s.validate_seeds([seed])[0]
        self.assertEqual(paper.document_version, '2101.09486v2')
        self.assertEqual(paper.pdf_url, 'https://arxiv.org/pdf/2101.09486v2')
        seed['arxiv_id'] = '2101.09486v1'
        with use_runtime(self.runtime):
            self.assertEqual(s.validate_seeds([seed]), [])

    def test_same_version_merges_across_entry_points_different_version_does_not(self):
        state = PipelineState(intent=Intent(task_type='retrieval', topic='Fixture'))
        key, _ = upsert_paper(state, Paper(title='Fixture', arxiv_id='2101.09486v1', document_version='2101.09486v1'))
        other, new = upsert_paper(state, Paper(title='Fixture', arxiv_id='2101.09486v1'))
        self.assertFalse(new)
        self.assertEqual(key, other)
        other, new = upsert_paper(state, Paper(title='Fixture', arxiv_id='2101.09486v2'))
        self.assertTrue(new)
        self.assertNotEqual(key, other)

    def test_preprint_and_publication_link_only_with_same_arxiv_identity(self):
        state = PipelineState(intent=Intent(task_type='retrieval', topic='Fixture'))
        a = Paper(title='Fixture', doi='10.48550/arxiv.2101.09486', arxiv_id='2101.09486v1')
        b = Paper(title='Fixture', doi='10.1234/published', arxiv_id='2101.09486v1')
        s.assign_doi(a, a.doi); s.assign_doi(b, b.doi)
        key, _ = upsert_paper(state, a)
        same, new = upsert_paper(state, b)
        self.assertFalse(new); self.assertEqual(key, same)
        self.assertEqual(state.papers[key].external_doi, '10.1234/published')
        other = Paper(title='Fixture', doi='10.1234/different', arxiv_id='2101.09486v1')
        s.assign_doi(other, other.doi)
        _, new = upsert_paper(state, other)
        self.assertTrue(new)

    def test_unknown_publisher_pdf_cannot_acquire_arxiv_version_label(self):
        for reverse in [False, True]:
            state = PipelineState(intent=Intent(task_type='retrieval', topic='Fixture'))
            publisher = Paper(title='Fixture', doi='10.1234/a', arxiv_id='2101.09486', pdf_url='https://publisher/formal.pdf')
            arxiv = Paper(title='Fixture', doi='10.1234/a', arxiv_id='2101.09486v1', document_version='2101.09486v1', pdf_url='https://arxiv.org/pdf/2101.09486v1')
            for paper in ([arxiv, publisher] if reverse else [publisher, arxiv]):
                upsert_paper(state, paper)
            self.assertEqual(len(state.papers), 2)
            for paper in state.papers.values():
                if paper.pdf_url == 'https://publisher/formal.pdf':
                    self.assertIsNone(paper.document_version)
                else:
                    self.assertEqual(paper.document_version, 'arxiv:2101.09486v1')


if __name__ == '__main__':
    unittest.main()
