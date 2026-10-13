"""Offline contracts for shared search scopes and same-paper PDF recovery."""
import unittest
from unittest.mock import patch

from pipelines.clients import scholarly
from pipelines.state import Paper, PipelineState, Intent
from pipelines.tasks import prior_art_retrieval as retrieval
from pipelines.tasks.prior_art_types import PriorArtConfig
from pipelines.tasks.prior_art_utils import verified_pdf_alternatives
from tools.paper_resolver import PaperResolverTool


class RetrievalPdfOptimizationTests(unittest.TestCase):
    def setUp(self):
        for target in ('requests.sessions.Session.request', 'socket.socket.connect',
                       'socket.create_connection'):
            guard = patch(target, side_effect=AssertionError('Offline fixture forbids network'))
            guard.start()
            self.addCleanup(guard.stop)

    def test_same_scope_keeps_both_provider_rank_boundaries_and_provenance(self):
        calls, raw = [], []
        def discover(query, years, limit, cfg):
            calls.append((query, years, limit))
            for provider in ('semantic_scholar', 'arxiv'):
                for rank in range(1, limit + 1):
                    raw.append(Paper(title=f'{provider} work {rank}',
                        doi=f'10.1234/{provider}-{rank}', retrieval_source=provider,
                        source_records=[{'source': 'retrieval_hit', 'provider': provider,
                            'query': query, 'rank': rank, 'requested_years': list(years),
                            'retrieval_limit': limit}]))
            return raw
        cfg = PriorArtConfig(retrieval_backend='s2_arxiv_crossref_v1', optimize_scholarly_requests=True,
                             recent_year_start=2024, recent_year_end=2025)
        state = PipelineState(intent=Intent(task_type='prior_art_check', topic='fixture'))
        with patch.object(scholarly, 'discover', side_effect=discover):
            found = retrieval.retrieve_candidates_with_buckets(['query'], state, cfg, 2024, 2025)
        self.assertEqual(calls, [('query', (2024, 2025), 15)])
        self.assertEqual(len(found), 30)
        for paper in found.values():
            rank = int(paper.title.rsplit(' ', 1)[1])
            sources = {record['source'] for record in paper.source_records}
            self.assertTrue(any(source.startswith('main:') for source in sources))
            self.assertEqual(any(source.startswith('recent:') for source in sources), rank <= 10)
            recent_hits = [s for s in paper.source_records if s.get('retrieval_limit') == 10]
            self.assertEqual(len(recent_hits), int(rank <= 10))
        self.assertTrue(all(len(p.source_records) == 1 for p in raw), 'Do not mutate shared provider rows')

    def test_distinct_year_scopes_still_request_both(self):
        cfg = PriorArtConfig(retrieval_backend='s2_arxiv_crossref_v1', optimize_scholarly_requests=True,
                             recent_year_start=2024, recent_year_end=2025)
        state = PipelineState(intent=Intent(task_type='prior_art_check', topic='fixture'))
        with patch.object(scholarly, 'discover', return_value=[]) as fetch:
            retrieval.retrieve_candidates_with_buckets(['query'], state, cfg, 2015, 2025)
        self.assertEqual([(c.args[1], c.args[2]) for c in fetch.call_args_list],
                         [((2015, 2025), 15), ((2024, 2025), 10)])

    def test_repeated_identical_zero_query_does_not_refetch(self):
        cfg = PriorArtConfig(retrieval_backend='s2_arxiv_crossref_v1', optimize_scholarly_requests=True,
                             recent_year_start=2024, recent_year_end=2025)
        state = PipelineState(intent=Intent(task_type='prior_art_check', topic='fixture'))
        with patch.object(scholarly, 'discover', return_value=[]) as fetch:
            self.assertEqual(retrieval.retrieve_candidates_with_buckets(
                ['same', 'same'], state, cfg, 2024, 2025), {})
        self.assertEqual(fetch.call_count, 1)

    def test_unoptimized_reference_keeps_independent_bucket_requests(self):
        cfg = PriorArtConfig(retrieval_backend='s2_arxiv_crossref_v1', optimize_scholarly_requests=False,
                             recent_year_start=2024, recent_year_end=2025)
        state = PipelineState(intent=Intent(task_type='prior_art_check', topic='fixture'))
        with patch.object(scholarly, 'discover', return_value=[]) as fetch:
            retrieval.retrieve_candidates_with_buckets(['query'], state, cfg, 2024, 2025)
        self.assertEqual([(c.args[1], c.args[2]) for c in fetch.call_args_list],
                         [((2024, 2025), 15), ((2024, 2025), 10)])

    def test_empty_s2_oa_url_reaches_existing_unpaywall_source(self):
        resolver = PaperResolverTool()
        for empty_url in ('', None, '  '):
            with self.subTest(empty_url=empty_url), \
                 patch.object(resolver, '_lookup_semantic_scholar', return_value={
                     'openAccessPdf': {'url': empty_url}, 'externalIds': {}}), \
                 patch('pipelines.clients.http_transport.openalex_enabled', return_value=False), \
                 patch.object(resolver, '_lookup_unpaywall', return_value={'fixture': True}) as lookup, \
                 patch.object(resolver, '_unpaywall_pdf_url', return_value='https://example.test/file.pdf'), \
                 patch.object(resolver, '_extract_meta_from_unpaywall', return_value={}):
                result = resolver.resolve_pdf_and_metadata(doi='10.1234/paper')
            self.assertEqual(result['pdf_url'], 'https://example.test/file.pdf')
            self.assertEqual(lookup.call_count, 1)

    @staticmethod
    def original(**changes):
        fields = dict(title='Reliable procedure assistance', authors=['Alice Example'],
                      paper_id='original', doi='10.1234/paper', arxiv_id='2401.00001',
                      identity_status='verified_doi_title')
        fields.update(changes)
        return Paper(**fields)

    @staticmethod
    def metadata(**changes):
        fields = dict(title='Reliable procedure assistance', authors=['Alice Example'],
                      arxiv_id='2401.00001v2', document_version='2401.00001v2')
        fields.update(changes)
        return Paper(**fields)

    def test_alternate_requires_independent_metadata_and_keeps_paper_id(self):
        paper = self.original()
        with patch.object(scholarly, 'search_arxiv', return_value=[self.metadata()]) as lookup:
            choices = verified_pdf_alternatives(paper, 'https://publisher.test/blocked')
        self.assertEqual(lookup.call_args.kwargs, {'limit': 1, 'id_list': ['2401.00001']})
        self.assertEqual(choices[0]['url'], 'https://arxiv.org/pdf/2401.00001v2')
        self.assertEqual(paper.paper_id, 'original')
        self.assertEqual(paper.arxiv_id, '2401.00001', 'Helper resolves but caller binds accepted material')
        self.assertEqual(paper.source_records[-1]['status'], 'verified')
        self.assertEqual(paper.source_records[-1]['failed_url'], 'https://publisher.test/blocked')

    def test_wrong_title_doi_id_or_author_never_authorizes_pdf(self):
        for changes in ({'title': 'Different work'}, {'doi': '10.1234/other'},
                        {'arxiv_id': '2401.00002v2'}, {'authors': ['Other Person']}):
            with self.subTest(changes=changes):
                paper = self.original()
                with patch.object(scholarly, 'search_arxiv', return_value=[self.metadata(**changes)]):
                    self.assertEqual(verified_pdf_alternatives(paper, 'https://publisher.test/x'), [])
                self.assertEqual(paper.source_records[-1]['status'], 'unavailable')

    def test_matching_publication_doi_can_corroborate_without_author_names(self):
        paper = self.original(authors=[])
        with patch.object(scholarly, 'search_arxiv', return_value=[self.metadata(
                authors=[], doi='https://doi.org/10.1234/paper')]):
            choices = verified_pdf_alternatives(paper, 'https://publisher.test/x')
        self.assertEqual(choices[0]['evidence']['shared_publication_doi'], ['10.1234/paper'])

    def test_explicit_version_is_not_silently_replaced_with_latest(self):
        paper = self.original(arxiv_id='2401.00001v1')
        with patch.object(scholarly, 'search_arxiv', return_value=[self.metadata()]):
            self.assertEqual(verified_pdf_alternatives(paper, 'https://publisher.test/x'), [])
        self.assertEqual(paper.source_records[-1]['reason'], 'arxiv_identifier_or_version_conflict')

    def test_unversioned_metadata_does_not_authorize_unpinned_material(self):
        paper = self.original()
        with patch.object(scholarly, 'search_arxiv', return_value=[self.metadata(arxiv_id='2401.00001')]):
            self.assertEqual(verified_pdf_alternatives(paper, 'https://publisher.test/x'), [])
        self.assertEqual(paper.source_records[-1]['reason'], 'arxiv_version_unverified')

    def test_no_id_conflicted_identity_or_arxiv_failure_adds_no_lookup(self):
        with patch.object(scholarly, 'search_arxiv') as lookup:
            self.assertEqual(verified_pdf_alternatives(self.original(arxiv_id=None), 'https://publisher.test/x'), [])
            self.assertEqual(verified_pdf_alternatives(self.original(identity_status='conflict'), 'https://publisher.test/x'), [])
            self.assertEqual(verified_pdf_alternatives(self.original(), 'https://arxiv.org/pdf/2401.00001'), [])
        lookup.assert_not_called()

    def test_metadata_unavailable_stays_unavailable(self):
        paper = self.original()
        with patch.object(scholarly, 'search_arxiv', return_value=[]):
            self.assertEqual(verified_pdf_alternatives(paper, 'https://publisher.test/x'), [])
        self.assertEqual(paper.source_records[-1]['reason'], 'arxiv_metadata_unavailable_or_ambiguous')

    def test_query_prompts_use_configured_dates_not_literal_year_hints(self):
        cfg = PriorArtConfig(recent_year_start=2030, recent_year_end=2031)
        captured = []
        def invoke(model, messages, **kwargs):
            captured.append(messages[0]['content'])
            return '[]'
        with patch.object(retrieval, 'get_litellm_config', return_value=('model', 'url', 'key')), \
             patch.object(retrieval, 'LiteLLMModel'), \
             patch.object(retrieval, 'invoke_expert_retrieval', side_effect=invoke), \
             patch.object(retrieval, 'invoke_model', side_effect=invoke), \
             patch.object(retrieval, 'extract_text_from_llm_response', side_effect=lambda value:value):
            retrieval.generate_seed_queries_llm(None, 'topic', 'idea', ['facet'], cfg)
            retrieval.refine_queries_llm(None, 'topic', 'idea', [], ['facet'], cfg)
        self.assertEqual(len(captured), 2)
        for prompt in captured:
            self.assertIn('2030-2031', prompt)
            self.assertNotIn('2023', prompt)
            self.assertNotIn('2024', prompt)


if __name__ == '__main__':
    unittest.main()
