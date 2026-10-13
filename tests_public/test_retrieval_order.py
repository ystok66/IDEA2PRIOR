import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pipelines.state import PipelineState, Intent, Paper
from pipelines.tasks.prior_art_types import PriorArtConfig, PriorArtMatch
from pipelines.tasks import prior_art_retrieval as retrieval
from pipelines.clients import web_search_client as web


class RetrievalOrderingTests(unittest.TestCase):
    def test_seed_metadata_is_not_hybridized_by_same_title(self):
        first={'title':'Same title','doi':'10.1234/a','url':'source-a'}
        second={'title':'Same title','doi':'10.1234/b','url':'source-b'}
        self.assertEqual(retrieval.merge_seeds([first], [first,second]), [first,second])

    def test_web_sources_keep_submission_order(self):
        cfg = PriorArtConfig()
        def slow(*args):
            time.sleep(.02)
            return [{'source': 'serpapi'}]
        with patch.object(web, 'run_serpapi_for_idea', side_effect=slow), patch.object(web, 'run_duckduckgo_for_idea', return_value=[{'source': 'ddg'}]):
            result = web.discover_web_candidates('idea', 2020, 2025, cfg)
        self.assertEqual([r['source'] for r in result], ['serpapi', 'ddg'])

    def test_thirty_result_batches_preserve_membership_and_order(self):
        rows = [{'title': str(i), 'link': str(i), 'snippet': 'same material', 'source_mode': 'fixture'} for i in range(65)]
        calls = []
        def parse(idea, payload, max_papers, task_id=None):
            ids = [int(r['title']) for r in payload['web_results']]
            calls.append((task_id, ids, max_papers))
            time.sleep(.015 if ids[0] == 0 else 0)
            return [{'title': str(i), 'url': str(i)} for i in ids]
        with patch.object(web, 'discover_web_candidates', return_value=rows), patch.object(web, 'normalize_all_web_results', side_effect=lambda x:x), patch.object(retrieval, 'parse_web_results_with_llm', side_effect=parse):
            output = retrieval.seed_papers_from_web_search('idea', 2020, 2025, PriorArtConfig(max_workers_web_batches=3))
        self.assertEqual([x['title'] for x in output], [str(i) for i in range(65)])
        self.assertEqual(sorted((ids for _, ids, _ in calls), key=lambda x:x[0]), [list(range(30)), list(range(30,60)), list(range(60,65))])
        self.assertTrue(all(limit == 15 for _, _, limit in calls))

    def test_citation_workers_do_not_mutate_in_completion_order(self):
        state = PipelineState(intent=Intent(task_type='prior_art_check', topic='test'), papers={
            'a': Paper(title='A', s2_id='a', paper_id='a', citation_count=20),
            'b': Paper(title='B', s2_id='b', paper_id='b', citation_count=10)})
        def references(pid, **kwargs):
            time.sleep(.02 if pid == 'a' else 0)
            return [{'paperId': pid+'-child', 'title': pid+' child', 'abstract': 'match'}]
        client = SimpleNamespace(get_references=references, get_citations=lambda *a, **kw: [])
        with patch.object(retrieval, 'SemanticScholarClient', return_value=client), patch.object(retrieval, 'lexical_filter_neighbors', side_effect=lambda **kw:kw['neighbors']):
            retrieval.expand_corpus_via_citation_graph(state, [PriorArtMatch('a', 2), PriorArtMatch('b', 2)], PriorArtConfig(citation_neighbor_filter='lexical'), ['match'])
        self.assertEqual([p.title for p in state.papers.values()], ['A', 'B', 'a child', 'b child'])

    def test_staged_citation_workers_resolve_ids_and_add_neighbors(self):
        state = PipelineState(intent=Intent(task_type='prior_art_check', topic='test'), papers={
            's2': Paper(title='S2 seed', s2_id='a' * 40, paper_id='s2'),
            'doi': Paper(title='DOI seed', doi='10.1234/paper.pdf', paper_id='doi'),
            'arxiv': Paper(title='arXiv seed', arxiv_id='2101.09486v2', paper_id='arxiv',
                           document_version='2101.09486v2',
                           pdf_url='https://arxiv.org/pdf/2101.09486v2'),
        })
        ids = ['a' * 40, 'DOI:10.1234/paper.pdf', 'ARXIV:2101.09486']

        def references(pid, **kwargs):
            index = ids.index(pid) + 1
            return [{'paperId': f'{index:040x}', 'title': f'Neighbor {index}',
                     'abstract': 'Relevant matching material', 'year': 2023}]

        client = SimpleNamespace(get_references=unittest.mock.Mock(side_effect=references),
                                 get_citations=unittest.mock.Mock(return_value=[]))
        with patch('socket.socket.connect', side_effect=AssertionError('Offline tests only')), \
             patch.object(retrieval, 'SemanticScholarClient', return_value=client), \
             patch.object(retrieval, 'lexical_filter_neighbors', side_effect=lambda **kw: kw['neighbors']):
            retrieval.expand_corpus_via_citation_graph(
                state, [PriorArtMatch(pid, 2) for pid in state.papers],
                PriorArtConfig(staged_retrieval=True, max_neighbors_per_seed=8, citation_neighbor_filter='lexical'),
                ['matching'], max_seed_papers=3, max_workers=3)
        for method in (client.get_references, client.get_citations):
            self.assertCountEqual([call.args[0] for call in method.call_args_list], ids)
            self.assertTrue(all(call.kwargs['limit'] == 4 for call in method.call_args_list))
        self.assertEqual([p.title for p in state.papers.values()],
                         ['S2 seed', 'DOI seed', 'arXiv seed', 'Neighbor 3', 'Neighbor 2', 'Neighbor 1'])
        self.assertEqual(state.papers['arxiv'].document_version, '2101.09486v2')
        self.assertEqual(state.papers['arxiv'].pdf_url, 'https://arxiv.org/pdf/2101.09486v2')

    def test_openalex_cross_bucket_metadata_is_merged(self):
        state = PipelineState(intent=Intent(task_type='prior_art_check', topic='test'))
        outputs = [json.dumps([{'title':'A','doi':'https://doi.org/10.x/a','openalex_id':'W1'}]),
                   json.dumps([{'title':'A','doi':'10.x/a','openalex_id':'W1','abstract':'Original abstract','authors':['Author']}])]
        with patch.object(retrieval.OpenAlexWorksSearchTool, 'forward', autospec=True, side_effect=outputs):
            result = retrieval.retrieve_candidates_with_buckets(['query'], state, PriorArtConfig(), 2018, 2025)
        self.assertEqual(len(result), 1)
        self.assertEqual(next(iter(result.values())).abstract, 'Original abstract')
        self.assertEqual(next(iter(result.values())).authors, ['Author'])

    def test_default_citation_gate_uses_semantics_and_passes_complete_context(self):
        seed = Paper(title='Synthetic seed', paper_id='seed', s2_id='a' * 40)
        state = PipelineState(intent=Intent(task_type='prior_art_check', topic='Original query'),
                              papers={'seed': seed})
        neighbor = {'paperId': 'b' * 40, 'title': 'Synthetic synonym paper',
                    'abstract': 'A meaning match without the generated exact phrase.', 'year': 2020,
                    'externalIds': None, 'authors': None}
        client = SimpleNamespace(get_references=lambda *a, **k: [neighbor],
                                 get_citations=lambda *a, **k: [])

        def semantic(groups, cfg, description, **kwargs):
            self.assertEqual(groups, [[neighbor]])
            self.assertEqual(description, 'Complete original idea')
            self.assertEqual(kwargs['research_query'], 'Original query')
            self.assertEqual(kwargs['search_queries'], ['Search query 1', 'Search query 2'])
            self.assertEqual(kwargs['relevance_keywords'], ['absent exact phrase'])
            self.assertEqual(kwargs['facets'], ['Facet 1'])
            return [[{**neighbor, 'citation_filter_assessment': {
                'score': 3, 'status': 'succeeded', 'reason': 'Same core task and mechanism.',
                'policy': 'citation_semantic_v1', 'model_role': 'light'}}]]

        with patch('socket.socket.connect', side_effect=AssertionError('Offline only')), \
             patch.object(retrieval, 'SemanticScholarClient', return_value=client), \
             patch.object(retrieval, 'lexical_filter_neighbors', side_effect=AssertionError('No lexical gate')), \
             patch('pipelines.tasks.citation_neighbor_filter.filter_citation_neighbor_groups', side_effect=semantic) as scoring:
            retrieval.expand_corpus_via_citation_graph(
                state, [PriorArtMatch('seed', 2)], PriorArtConfig(staged_retrieval=True),
                ['absent exact phrase'], idea_description='Complete original idea', facets=['Facet 1'],
                research_query='Original query', search_queries=['Search query 1', 'Search query 2'])
        self.assertEqual(scoring.call_count, 1)
        added = next(p for p in state.papers.values() if p.s2_id == 'b' * 40)
        self.assertTrue(any(r.get('source') == 'citation_neighbor_assessment' and r.get('score') == 3
                            for r in added.source_records))


if __name__ == '__main__':
    unittest.main()
