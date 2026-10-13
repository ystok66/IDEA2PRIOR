"""Independent synthetic fixtures; no benchmark gold, network, or real LLM calls."""
from contextlib import ExitStack
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pipelines.state import Paper, Intent, PipelineState
from pipelines.identity import upsert_paper
from pipelines.run_manager import RunManager
from pipelines.tasks.prior_art_types import PriorArtConfig, PriorArtMatch
from pipelines.tasks.prior_art_retrieval import apply_citation_recency_quota
from pipelines.tasks.candidate_selection import select_candidates, select_shortlist, refresh_aliases

ROOT = Path(__file__).resolve().parents[1]


def paper(pid, title, *, abstract='evidence', year=2018, citations=None, provider=None, rank=1, entity=None):
    return Paper(paper_id=pid, entity_id=entity or pid, title=title, abstract=abstract,
                 year=year, citation_count=citations,
                 source_records=[{'source': 'retrieval_hit', 'provider': provider, 'rank': rank}] if provider else [])


class CandidateFunnelTests(unittest.TestCase):
    def cfg(self, **kw):
        defaults = dict(max_candidates=4, recent_min_quota_ratio=0, recent_max_quota_ratio=1,
                        missing_abstract_reserve=0, protected_candidate_ratio=.5,
                        retriever_protect_top_k=1)
        defaults.update(kw)
        return PriorArtConfig(**defaults)

    def test_reproduces_legacy_missing_citation_drop_and_fixes_without_gold(self):
        records = {'seed': paper('seed', 'Infer hidden relationships from interacting object motion',
                               abstract='Learn the latent interaction graph and object dynamics', provider='arxiv')}
        records.update({str(i): paper(str(i), 'Database indexing', citations=100+i) for i in range(6)})
        cfg = self.cfg(max_candidates=3)
        self.assertNotIn('seed', [p.paper_id for p in apply_citation_recency_quota(records, cfg)])
        selected, audit = select_candidates(records, cfg, 'Infer hidden relationships and object dynamics')
        self.assertIn('seed', [p.paper_id for p in selected])
        self.assertEqual(len(selected), 3)
        self.assertFalse(audit['decisions']['seed']['citation_used'])
        self.assertIn('protected_retriever_rank', audit['decisions']['seed']['reasons'])

    def test_citation_changes_do_not_change_selection(self):
        records = {str(i): paper(str(i), 'Graph dynamics ' + str(i), citations=i) for i in range(8)}
        first, _ = select_candidates(records, self.cfg(), 'Graph dynamics')
        for i, p in enumerate(records.values()):
            p.citation_count = 100000-i
        second, _ = select_candidates(records, self.cfg(), 'Graph dynamics')
        self.assertEqual([p.paper_id for p in first], [p.paper_id for p in second])

    def test_versions_consume_one_slot_and_keep_audit_members(self):
        records = {'v1': paper('v1', 'Graph model', entity='work'),
                   'v2': paper('v2', 'Graph model', entity='work', abstract='more complete graph evidence'),
                   'other': paper('other', 'Other graph')}
        selected, audit = select_candidates(records, self.cfg(max_candidates=3), 'Graph model')
        self.assertEqual(len(selected), 2)
        self.assertEqual(sum(p.entity_id == 'work' for p in selected), 1)
        self.assertEqual(len(audit['decisions']), 3)
        self.assertTrue(any(len(v) == 2 for v in audit['representative_members'].values()))

    def test_source_protection_is_balanced_and_bounded(self):
        records = {str(i): paper(str(i), 'Noise', provider='s2', rank=i+1) for i in range(10)}
        records['arxiv'] = paper('arxiv', 'Other', provider='arxiv')
        chosen, audit = select_candidates(records, self.cfg(), 'Unrelated')
        self.assertIn('arxiv', [p.paper_id for p in chosen])
        protected = [v for v in audit['decisions'].values() if any(r.startswith('protected_') for r in v['reasons'])]
        self.assertEqual(len(protected), 2)

    def test_recent_minimum_does_not_eagerly_fill_maximum(self):
        records = {str(i): paper(str(i), 'Graph dynamics' if i < 8 else 'Unrelated', year=2018 if i < 8 else 2025)
                   for i in range(16)}
        _, audit = select_candidates(records, self.cfg(max_candidates=10, recent_min_quota_ratio=.3,
                                                       recent_max_quota_ratio=.6), 'Graph dynamics')
        self.assertEqual(audit['recent_selected'], 3)

    def test_missing_abstract_and_conflict_are_not_seed_equivalents(self):
        records = {'uncertain': paper('uncertain', 'Graph', abstract=None, provider='arxiv'),
                   'bad': paper('bad', 'Graph', provider='s2'),
                   'known': paper('known', 'Graph')}
        records['bad'].identity_status = 'conflict'
        _, audit = select_candidates(records, self.cfg(missing_abstract_reserve=1), 'Graph', seed_ids={'bad'})
        self.assertFalse(any(r.startswith('protected_') for r in audit['decisions']['bad']['reasons']))
        self.assertFalse(audit['decisions']['bad']['selected'])
        self.assertIn('known_identity_conflict', audit['decisions']['bad']['reasons'])
        self.assertTrue(audit['decisions']['uncertain']['selected'])

    def test_known_year_filter_and_unknown_year_are_separate(self):
        records = {'outside': paper('outside', 'Graph', year=2026, provider='s2'),
                   'unknown': paper('unknown', 'Graph', year=None, provider='arxiv')}
        chosen, audit = select_candidates(records, self.cfg(), 'Graph', years=(2015, 2025))
        self.assertEqual([p.paper_id for p in chosen], ['unknown'])
        self.assertIn('known_year_out_of_scope', audit['decisions']['outside']['reasons'])

    def test_shortlist_relevance_and_uncertainty_reserve(self):
        records = {str(i): paper(str(i), 'Graph dynamics', citations=100-i) for i in range(5)}
        records['missing'] = paper('missing', 'Graph dynamics', abstract=None)
        matches = [PriorArtMatch(str(i), 3, task_similarity=i/5) for i in range(5)]
        chosen, audit = select_shortlist(records, matches,
            self.cfg(metadata_shortlist_size=2, metadata_boundary_buffer=2), 'Graph dynamics')
        self.assertEqual(audit['primary_ids'], ['4', '3'])
        self.assertIn('missing', chosen)
        self.assertEqual(len(chosen), 4)

    def test_staged_pipeline_orders_scoring_before_shortlist_enrichment(self):
        import pipelines.tasks.prior_art_check as main
        cfg = PriorArtConfig(staged_retrieval=True, reuse_llm_across_stages=True,
            selection_policy_version='relevance_protected_v1', retrieval_backend='s2_arxiv_crossref_v1',
            enable_openalex=False, enable_serpapi=False, enable_duckduckgo=False,
            enable_hierarchical=False, enable_llm_facet_patterns=False, enable_bm25_evidence=False,
            max_candidates=4, missing_abstract_reserve=0, metadata_shortlist_size=2,
            metadata_boundary_buffer=1, top_k_fulltext_refine=1)
        seeds = [Paper(title='Graph dynamics '+str(i), doi='10.test/'+str(i), abstract='Complete graph evidence') for i in range(6)]
        trace = []
        def score(**kw):
            trace.append(('score', kw['stage'], list(kw['state'].papers)))
            return [PriorArtMatch(pid, 2, task_similarity=.8) for pid in kw['state'].papers]
        def enrich(rows, cfg):
            trace.append(('enrich', [p.paper_id for p in rows]))
            # First verification exposes an invalid year. A fourth scored
            # candidate must be verified to refill the three-item shortlist.
            if rows and not any(x[0] == 'year_discovered' for x in trace):
                rows[0].year = 2026
                trace.append(('year_discovered', rows[0].paper_id))
            return rows
        def fulltext(**kw):
            trace.append(('fulltext', kw['eligible_paper_ids'], kw['top_k']))
            return kw['matches']
        fake = {'seed_papers_from_llm': [], 'validate_llm_seeds_via_openalex': seeds,
                'generate_search_queries_from_idea': ['query'], 'decompose_idea_into_facets': ['Technical facet'],
                'extract_idea_features': {'datasets': set(), 'metrics': set(), 'methods': set()},
                'extract_high_level_facets_from_idea': {}, 'generate_seed_queries_llm': ['query'],
                'retrieve_candidates_with_buckets': {}, 'generate_relevance_keywords_from_idea': ['graph'],
                'summarize_intro_rw_for_block3': None, 'build_method_experiment_snippets': None,
                'select_key_papers_and_dump_intro_rw_full': None}
        with tempfile.TemporaryDirectory(dir=ROOT) as temp, ExitStack() as stack:
            stack.enter_context(patch('socket.socket.connect', side_effect=AssertionError('Network forbidden')))
            for name, value in fake.items():
                stack.enter_context(patch.object(main, name, return_value=value))
            stack.enter_context(patch.object(main, 'run_exploration_loop', side_effect=lambda **kw: kw['state']))
            stack.enter_context(patch.object(main, 'score_papers_with_abstract_only', side_effect=score))
            stack.enter_context(patch.object(main, 'refine_top_papers_with_fulltext', side_effect=fulltext))
            stack.enter_context(patch('pipelines.clients.scholarly.enrich_candidates', side_effect=enrich))
            manager = RunManager('fixture', base_dir=Path(temp))
            main.run_prior_art_check(object(), 'Infer graph dynamics', 'fixture', years=(2015,2025),
                                     use_citation_graph=False, cfg=cfg, run_mgr=manager)
            initial_index = next(i for i,x in enumerate(trace) if x[:2] == ('score','initial'))
            enrichment_index = next(i for i,x in enumerate(trace) if x[0] == 'enrich' and x[1])
            self.assertLess(initial_index, enrichment_index)
            self.assertEqual(len(trace[initial_index][2]), 4)
            full = next(x for x in trace if x[0] == 'fulltext')
            self.assertEqual(len(full[1]), 3)
            self.assertEqual(full[2], 1)
            invalid = next(x[1] for x in trace if x[0] == 'year_discovered')
            self.assertNotIn(invalid, full[1])
            shortlist = json.loads((manager.run_dir/'metadata_shortlist.json').read_text(encoding='utf8'))
            self.assertEqual(len(shortlist['verification_rounds']), 2)
            self.assertEqual(len(shortlist['newly_enriched_ids']), 4)
            before = json.loads((manager.run_dir/'checkpoints/before_selection.json').read_text(encoding='utf8'))
            audit = json.loads((manager.run_dir/'candidate_selection.json').read_text(encoding='utf8'))
            self.assertEqual(len(before['papers']), 6)
            self.assertEqual(len(audit['selected_ids']), 4)

    def test_exploration_does_not_enrich_or_read_pdfs_before_selection(self):
        import pipelines.exploration_loop as exploration
        state = PipelineState(intent=Intent(task_type='prior_art_check', topic='Graph'))
        added = [paper(str(i), 'Graph') for i in range(10)]
        with patch.object(exploration, 'discover_papers_round', return_value=added), \
                patch.object(exploration, 'enrich_metadata_and_citations') as enrich, \
                patch.object(exploration, 'maybe_parse_key_papers') as pdf:
            exploration.run_exploration_loop(object(), state, max_rounds=1,
                                             cfg=PriorArtConfig(staged_retrieval=True))
        enrich.assert_not_called()
        pdf.assert_not_called()

    def test_new_shared_ids_reconcile_work_slots_without_merging_versions(self):
        a = paper('a', 'Graph dynamics', entity='work-a')
        b = paper('b', 'Graph dynamics', entity='work-b')
        a.s2_id = b.s2_id = 'a' * 40
        a.arxiv_id, b.arxiv_id = '2401.12345v1', '2401.12345v2'
        a.pdf_url, b.pdf_url = 'https://arxiv.org/pdf/2401.12345v1', 'https://arxiv.org/pdf/2401.12345v2'
        state = PipelineState(intent=Intent(task_type='prior_art_check', topic='Graph'), papers={'a':a,'b':b})
        refresh_aliases(state)
        self.assertEqual(state.papers['a'].entity_id, state.papers['b'].entity_id)
        self.assertEqual(len(state.papers), 2)
        self.assertNotEqual(a.pdf_url, b.pdf_url)
        selected, _ = select_shortlist(state.papers, [PriorArtMatch('a',3),PriorArtMatch('b',3)],
                                      self.cfg(metadata_shortlist_size=2), 'Graph')
        self.assertEqual(len(selected), 1)

    def test_sparse_identity_cannot_bridge_conflicting_work_ids(self):
        records = {pid: paper(pid,'Graph',entity='work-'+pid) for pid in 'abc'}
        for p in records.values():
            p.doi = '10.test/common'
        records['a'].s2_id, records['c'].s2_id = 'a'*40, 'c'*40
        state = PipelineState(intent=Intent(task_type='prior_art_check',topic='Graph'),papers=records)
        refresh_aliases(state)
        self.assertNotEqual(records['a'].entity_id, records['c'].entity_id)
        self.assertTrue(state.identity_conflicts)


if __name__ == '__main__':
    unittest.main()
