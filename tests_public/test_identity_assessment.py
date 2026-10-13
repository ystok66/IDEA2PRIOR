import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import ExitStack

from pipelines.state import Intent, PipelineState, Paper, Chunk
from pipelines.identity import upsert_paper, paper_from_openalex
from pipelines.assessment import build_assessment_snapshot
from pipelines.paper_record import paper_to_paper_record
from pipelines.tasks.prior_art_types import PriorArtConfig, PriorArtMatch
from pipelines.tasks.prior_art_synthesis import build_synthesis_input
from pipelines.tasks.prior_art_report import render_prior_art_report
from pipelines.run_manager import RunManager

ROOT = Path(__file__).resolve().parents[1]
TMP = ROOT / 'audit' / 'implementation' / 'tmp'
TMP.mkdir(parents=True, exist_ok=True)


def state():
    return PipelineState(intent=Intent(task_type='prior_art_check', topic='arbitrary domain',
        extra_constraints={'idea_description': 'Complete idea ' * 90, 'use_citation_graph': 'false'}))


class IdentityAssessmentTests(unittest.TestCase):
    def test_unsupported_optimization_flags_fail_before_any_work(self):
        import pipelines.tasks.prior_art_check as main
        for config in [PriorArtConfig(llm_tokens_per_minute=1000), PriorArtConfig(overlap_refine_method=True),
                       PriorArtConfig(resume_same_task=True), PriorArtConfig(staged_retrieval=True)]:
            with self.subTest(config=config), patch.object(main, '_run_prior_art_check_impl') as impl:
                with self.assertRaises(ValueError):
                    main.run_prior_art_check(object(), 'idea', 'topic', cfg=config)
                impl.assert_not_called()

    def test_invalid_scoring_labels_do_not_inflate_facet_statistics(self):
        from pipelines.tasks.prior_art_synthesis import _build_facet_coverage_stats
        matches={'good':PriorArtMatch('good',2,covered_facet_labels=['F1']),
                 'invalid':PriorArtMatch('invalid',2,covered_facet_labels=['F1']),
                 'fuzzy':PriorArtMatch('fuzzy',2,covered_facet_labels=['technical matching text'])}
        result=_build_facet_coverage_stats([{'id':'F1','label':'technical matching'}],matches,
                                         {'good':2,'invalid':None,'fuzzy':2})
        self.assertEqual(result[0]['total_papers'],1)
        self.assertEqual(result[0]['strong_papers'],1)

    def test_pipeline_exception_after_snapshot_cannot_report_success(self):
        import pipelines.tasks.prior_art_check as main
        from pipelines.runtime import atomic_write_json
        with tempfile.TemporaryDirectory(dir=TMP) as temp:
            manager = RunManager('failure', base_dir=Path(temp))
            def fail_after_snapshot(*args, **kwargs):
                atomic_write_json(manager.run_dir / 'assessment_snapshot.json', {'saved': True})
                raise ValueError('Renderer failed after snapshot')
            with patch.object(main, '_run_prior_art_check_impl', side_effect=fail_after_snapshot):
                with self.assertRaises(ValueError):
                    main.run_prior_art_check(object(), 'idea', 'failure', run_mgr=manager)
            outcome = json.loads((manager.run_dir/'completion.json').read_text(encoding='utf-8'))
            self.assertEqual(outcome['status'], 'FAILED')
            self.assertEqual(outcome['error_type'], 'ValueError')

    def test_alias_merge_preserves_abstract_authors_and_conflicts(self):
        s = state()
        pid, _ = upsert_paper(s, Paper(title='A', doi='https://doi.org/10.X/A', openalex_id='https://openalex.org/W1'))
        pid2, created = upsert_paper(s, Paper(title='A alternate', doi='10.x/a', s2_id='ABC', abstract='Full abstract', authors=['Alice']))
        self.assertEqual(pid, pid2)
        self.assertFalse(created)
        self.assertEqual(s.papers[pid].abstract, 'Full abstract')
        self.assertEqual(s.papers[pid].authors, ['Alice'])
        self.assertEqual(s.papers[pid].field_conflicts['title'], ['A', 'A alternate'])
        self.assertEqual(s.paper_aliases['s2:abc'], [pid])

    def test_versions_conflicts_and_title_only_are_not_merged(self):
        s = state()
        a, _ = upsert_paper(s, Paper(title='Same', arxiv_id='2401.12345v1', content_hash='a', chunks=[Chunk(text='version one')]))
        b, _ = upsert_paper(s, Paper(title='Same', arxiv_id='2401.12345v2', content_hash='b'))
        self.assertNotEqual(a, b)
        self.assertEqual(s.papers[a].entity_id, s.papers[b].entity_id)
        self.assertFalse(s.papers[b].chunks)
        c, _ = upsert_paper(s, Paper(title='Same', arxiv_id='2401.12345'))
        self.assertNotEqual(a, c)
        d, _ = upsert_paper(s, Paper(title='Same'))
        e, _ = upsert_paper(s, Paper(title='Same'))
        self.assertNotEqual(d, e)
        x, _ = upsert_paper(s, Paper(title='X', doi='10.a/x', s2_id='shared'))
        y, _ = upsert_paper(s, Paper(title='Y', doi='10.b/y', s2_id='shared'))
        self.assertNotEqual(x, y)
        self.assertTrue(s.identity_conflicts)

    def test_openalex_adapter_and_section_identity(self):
        paper = paper_from_openalex({'id': 'https://openalex.org/W123', 'display_name': 'Title',
            'abstract_inverted_index': {'two': [1], 'one': [0]},
            'primary_location': {'source': {'display_name': 'Venue'}},
            'authorships': [{'author': {'display_name': 'Author'}}]})
        self.assertEqual((paper.abstract, paper.venue, paper.authors), ('one two', 'Venue', ['Author']))
        paper.paper_id = 'stable'
        paper.parsed_sections = [{'heading': '3 Experiments', 'role': 'experiment', 'text': 'Exact text',
                                  'section_id': 's3', 'page_start': 5, 'page_end': 6}]
        record = paper_to_paper_record(paper)
        self.assertEqual(record.paper_id, 'stable')
        self.assertEqual(record.sections[0].role, 'experiments')
        self.assertEqual(record.sections[0].section_id, 's3')
        self.assertEqual(record.sections[0].page_start, 5)

    def test_snapshot_rejects_invalid_output_and_preserves_zero(self):
        s = state()
        for pid in ['zero', 'failed', 'boolean', 'nan', 'badfacet', 'missing', 'refined']:
            s.papers[pid] = Paper(title=pid, paper_id=pid)
        matches = [PriorArtMatch('zero', 0), PriorArtMatch('failed', None, scoring_status='failed'),
            PriorArtMatch('boolean', True), PriorArtMatch('nan', 2, task_similarity=float('nan')),
            PriorArtMatch('badfacet', 2, covered_facet_labels=['F999']),
            PriorArtMatch('refined', 1, abstract_coverage_level=3, refined_coverage_level=1,
                          refinement_status='succeeded', refinement_snippets=['Specific evidence'])]
        snap = build_assessment_snapshot(s, matches, ['Facet'])
        self.assertEqual(snap['buckets']['ignored'], ['zero'])
        self.assertEqual(len(snap['buckets']['unassessed']), 5)
        self.assertEqual(snap['entries']['refined']['abstract_assessment']['coverage_level'], 3)
        self.assertEqual(snap['entries']['refined']['final']['source'], 'fulltext_snippets')
        self.assertIsNone(snap['entries']['nan']['final']['source'])

    def test_json_markdown_share_final_snapshot_and_all_snippets(self):
        s = state()
        s.papers = {'a': Paper(title='Paper A', paper_id='a'), 'b': Paper(title='Paper B', paper_id='b')}
        matches = [PriorArtMatch('a', 1, abstract_coverage_level=3, refined_coverage_level=1,
                                refinement_status='succeeded'), PriorArtMatch('b', None, scoring_status='failed')]
        snap = build_assessment_snapshot(s, matches, ['Facet'])
        cfg = PriorArtConfig()
        with tempfile.TemporaryDirectory(dir=TMP) as temp:
            folder = Path(temp); (folder / 'synthesis').mkdir()
            items = [{'paper_id': 'a', 'text': f'paragraph {i}', 'facet_id': 'F1',
                      'section_id': 's1', 'paragraph_id': i, 'page_start': 3} for i in range(22)]
            (folder / 'synthesis/method_exp_snippets.jsonl').write_text('\n'.join(json.dumps(x) for x in items), encoding='utf-8')
            result = build_synthesis_input(s, cfg, folder, facets=[{'id': 'F1', 'label': 'Facet'}], scored_matches=matches, snapshot=snap)
            report = render_prior_art_report('Idea', ['Facet'], matches, s, {}, {}, config=cfg, snapshot=snap)
        self.assertEqual(result['buckets'], snap['buckets'])
        self.assertEqual(len(result['papers']), 2)
        self.assertEqual(len(next(p for p in result['papers'] if p['paper_id']=='a')['method_exp_snippets']), 22)
        self.assertIn(snap['snapshot_id'], report)
        self.assertIn('Paper B', report)
        self.assertFalse(result['metadata']['run_info']['config']['enable_citation_graph'])
        self.assertGreater(len(result['metadata']['idea_description']), 500)

    def test_main_pipeline_offline_final_artifacts(self):
        import pipelines.tasks.prior_art_check as main
        cfg = PriorArtConfig(enable_serpapi=False, enable_duckduckgo=False,
            enable_hierarchical=False, enable_llm_facet_patterns=False, enable_bm25_evidence=False)
        fake = {
            'seed_papers_from_llm': [], 'validate_llm_seeds_via_openalex': [Paper(title='Known', doi='10.test/a', abstract='Original abstract')],
            'generate_search_queries_from_idea': ['query'], 'decompose_idea_into_facets': ['Technical facet'],
            'extract_idea_features': {'datasets': set(), 'metrics': set(), 'methods': set()},
            'extract_high_level_facets_from_idea': {}, 'generate_seed_queries_llm': ['query'],
            'retrieve_candidates_with_buckets': {}, 'generate_relevance_keywords_from_idea': ['technical'],
            'summarize_intro_rw_for_block3': None, 'build_method_experiment_snippets': None,
            'select_key_papers_and_dump_intro_rw_full': None,
        }
        with tempfile.TemporaryDirectory(dir=TMP) as temp, ExitStack() as stack:
            stack.enter_context(patch('socket.socket.connect', side_effect=AssertionError('Network forbidden')))
            for name, value in fake.items():
                stack.enter_context(patch.object(main, name, return_value=value))
            stack.enter_context(patch.object(main, 'run_exploration_loop', side_effect=lambda **kw: kw['state']))
            stack.enter_context(patch.object(main, 'apply_citation_recency_quota', side_effect=lambda papers, cfg: list(papers.values())))
            stack.enter_context(patch.object(main, 'score_papers_with_abstract_only', side_effect=lambda **kw: [PriorArtMatch(pid, 0) for pid in kw['state'].papers]))
            stack.enter_context(patch.object(main, 'refine_top_papers_with_fulltext', side_effect=lambda **kw: kw['matches']))
            manager = RunManager('fixture', base_dir=Path(temp))
            main.run_prior_art_check(object(), 'Complete idea ' * 90, 'fixture', use_citation_graph=False, cfg=cfg, run_mgr=manager)
            snapshot = json.loads((manager.run_dir / 'assessment_snapshot.json').read_text(encoding='utf-8'))
            synthesis = json.loads((manager.run_dir / 'synthesis/synthesis_input.json').read_text(encoding='utf-8'))
            self.assertEqual(snapshot['buckets'], synthesis['buckets'])
            self.assertEqual(len(snapshot['candidate_ids']), 1)
            self.assertTrue(list(manager.md_reports_dir.glob('*.md')))
            self.assertFalse(json.loads((manager.run_dir / 'config.json').read_text(encoding='utf-8'))['use_citation_graph'])


if __name__ == '__main__':
    unittest.main()
