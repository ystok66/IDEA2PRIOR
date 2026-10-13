"""Independent seed tasks share runtime and retain deterministic merge order."""
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pipelines.runtime import AttemptLimitError, get_runtime, use_runtime
from pipelines.tasks.prior_art_types import PriorArtConfig
from pipelines.tasks import prior_art_check as main


class Top5SeedOverlapTests(unittest.TestCase):
    def test_parallel_tasks_inherit_runtime_and_merge_in_source_order(self):
        barrier = threading.Barrier(2)
        runtime = object()
        cfg = PriorArtConfig(execution_mode='retrieval_eval', enable_serpapi=True,
                             enable_duckduckgo=False)
        llm_rows = [{'title': 'Duplicate title', 'source': 'llm'}]
        web_rows = [{'title': 'Duplicate title', 'source': 'web'}, {'title': 'Web only'}]

        def llm(*args):
            self.assertIs(get_runtime(), runtime)
            barrier.wait(timeout=3)
            return llm_rows

        def web(**kwargs):
            self.assertIs(get_runtime(), runtime)
            self.assertEqual((kwargs['year_start'], kwargs['year_end']), (2015, 2025))
            barrier.wait(timeout=3)
            return web_rows

        with use_runtime(runtime), patch.object(main, 'seed_papers_from_llm', side_effect=llm) as a, \
                patch.object(main, 'seed_papers_from_serpapi', side_effect=web) as b, \
                patch.object(main, 'merge_seeds', wraps=main.merge_seeds) as merge:
            result = main._discover_seed_papers(object(), 'original research', (2015, 2025), cfg)
            a.assert_called_once()
            b.assert_called_once()
            merge.assert_called_once_with(llm_rows, web_rows)
        # Differing unverified proposals are preserved until identity validation.
        self.assertEqual(result, llm_rows + web_rows)
        self.assertIsNone(get_runtime())

    def test_full_report_and_explicit_reference_remain_serial(self):
        for mode, parallel in [('full_report', True), ('retrieval_eval', False)]:
            sequence = []
            cfg = PriorArtConfig(execution_mode=mode, enable_serpapi=True,
                                 retrieval_eval_parallel_seed_discovery=parallel)
            def llm(*args):
                sequence.append('llm')
                return []
            def web(**kwargs):
                sequence.append('web')
                return []
            with self.subTest(mode=mode), patch.object(main, 'seed_papers_from_llm', side_effect=llm), \
                    patch.object(main, 'seed_papers_from_serpapi', side_effect=web):
                main._discover_seed_papers(object(), 'idea', None, cfg)
            self.assertEqual(sequence, ['llm', 'web'])

    def test_web_disabled_does_not_start_web_task(self):
        cfg = PriorArtConfig(execution_mode='retrieval_eval', enable_serpapi=False, enable_duckduckgo=False)
        with patch.object(main, 'seed_papers_from_llm', return_value=[{'title': 'Seed'}]) as llm, \
                patch.object(main, 'seed_papers_from_serpapi') as web:
            self.assertEqual(main._discover_seed_papers(object(), 'idea', None, cfg), [{'title': 'Seed'}])
            llm.assert_called_once()
            web.assert_not_called()

    def test_source_failure_is_not_retried_or_silently_dropped(self):
        cfg = PriorArtConfig(execution_mode='retrieval_eval', enable_serpapi=True)
        with patch.object(main, 'seed_papers_from_llm', side_effect=RuntimeError('seed failure')) as llm, \
                patch.object(main, 'seed_papers_from_serpapi', return_value=[]) as web, \
                patch.object(main, 'merge_seeds') as merge:
            with self.assertRaisesRegex(RuntimeError, 'seed failure'):
                main._discover_seed_papers(object(), 'idea', None, cfg)
            llm.assert_called_once()
            web.assert_called_once()
            merge.assert_not_called()

    def test_llm_timeout_preserves_completed_web_seeds_and_records_issue(self):
        cfg = PriorArtConfig(execution_mode='retrieval_eval', enable_serpapi=True)
        rows = [{'title': 'Independent web result', 'doi': '10.fixture/web'}]
        events = []
        runtime = SimpleNamespace(ledger=SimpleNamespace(event=lambda event, **kw: events.append((event, kw))))
        with use_runtime(runtime), patch.object(main, 'seed_papers_from_llm', side_effect=TimeoutError('fixture timeout')) as llm, \
                patch.object(main, 'seed_papers_from_serpapi', return_value=rows) as web:
            result = main._discover_seed_papers(object(), 'blind research', (2015, 2025), cfg)
        self.assertEqual(result, rows)
        llm.assert_called_once()
        web.assert_called_once()
        self.assertTrue(any(e[1].get('status') == 'FAILED' and e[1].get('source') == 'llm' for e in events))
        self.assertEqual(runtime.retrieval_issues[0]['error_type'], 'TimeoutError')

    def test_web_failure_preserves_llm_results_without_retry(self):
        cfg = PriorArtConfig(execution_mode='retrieval_eval', enable_serpapi=True)
        rows = [{'title': 'Independent LLM result'}]
        with patch.object(main, 'seed_papers_from_llm', return_value=rows) as llm, \
                patch.object(main, 'seed_papers_from_serpapi', side_effect=TimeoutError('web timeout')) as web:
            result = main._discover_seed_papers(object(), 'blind research', None, cfg)
        self.assertEqual(result, rows)
        llm.assert_called_once()
        web.assert_called_once()

    def test_both_sources_fail_without_fabricating_results(self):
        cfg = PriorArtConfig(execution_mode='retrieval_eval', enable_serpapi=True)
        with patch.object(main, 'seed_papers_from_llm', side_effect=TimeoutError('LLM timeout')) as llm, \
                patch.object(main, 'seed_papers_from_serpapi', side_effect=RuntimeError('web failure')) as web, \
                patch.object(main, 'merge_seeds') as merge:
            with self.assertRaisesRegex(TimeoutError, 'LLM timeout'):
                main._discover_seed_papers(object(), 'blind research', None, cfg)
        llm.assert_called_once()
        web.assert_called_once()
        merge.assert_not_called()

    def test_full_report_failure_still_raises(self):
        cfg = PriorArtConfig(execution_mode='full_report', enable_serpapi=True)
        with patch.object(main, 'seed_papers_from_llm', side_effect=TimeoutError('reference timeout')), \
                patch.object(main, 'seed_papers_from_serpapi') as web:
            with self.assertRaisesRegex(TimeoutError, 'reference timeout'):
                main._discover_seed_papers(object(), 'blind research', None, cfg)
        web.assert_not_called()

    def test_attempt_limit_is_not_treated_as_optional_source_failure(self):
        cfg = PriorArtConfig(execution_mode='retrieval_eval', enable_serpapi=True)
        with patch.object(main, 'seed_papers_from_llm', side_effect=AttemptLimitError('stop budget')), \
                patch.object(main, 'seed_papers_from_serpapi', return_value=[{'title': 'Web'}]), \
                patch.object(main, 'merge_seeds') as merge:
            with self.assertRaisesRegex(AttemptLimitError, 'stop budget'):
                main._discover_seed_papers(object(), 'blind research', None, cfg)
        merge.assert_not_called()


if __name__ == '__main__':
    unittest.main()
