"""Exact-evidence run-local scoring reuse; all model/network calls are mocked."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from pipelines.runtime import LLMRuntime, RunLedger, use_runtime
from pipelines.state import Intent, Paper, PipelineState, Chunk
from pipelines.tasks import prior_art_scoring as scoring
from pipelines.tasks.prior_art_types import PriorArtConfig, PriorArtMatch


def score_payload():
    return {"coverage_level": 3, "covered_facets": ["F1"],
            "task_similarity": 0.9, "method_similarity": 0.8,
            "notes": "Uses the requested method for the requested problem.",
            "high_level_facets": {key: ["prediction"] for key in scoring.HIGH_LEVEL_FACET_TYPES}}


class ScoringReuseTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / "audit" / "implementation" / "tmp"
        root.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.runtime = LLMRuntime(RunLedger(self.root / "first"))
        self.calls = []
        self.call_lock = threading.Lock()
        self.handler = lambda messages: json.dumps(score_payload())
        test = self

        class Model:
            def __init__(self, model_id, api_base, api_key):
                self.model_id, self.api_base, self.api_key = model_id, api_base, api_key
                self.kwargs = {}

            def __call__(self, messages, **kwargs):
                with test.call_lock:
                    test.calls.append(messages)
                return test.handler(messages)

        self.Model = Model
        for target in ("requests.sessions.Session.request", "socket.create_connection", "socket.socket.connect"):
            p = patch(target, side_effect=AssertionError("Live network forbidden"))
            p.start()
            self.addCleanup(p.stop)
        for p in (patch.object(scoring, "LiteLLMModel", Model),
                  patch.object(scoring, "get_litellm_config", return_value=("fake", "offline://model", "fake-key")),
                  patch.object(scoring.llm_rate_limiter, "wait_for_slot")):
            p.start()
            self.addCleanup(p.stop)

    def state(self, count=2):
        return PipelineState(intent=Intent(task_type="prior_art_check", topic="prediction"), papers={
            f"p{n}": Paper(title=f"Paper {n}", abstract=f"Prediction method {n}",
                           high_level_facets={"problem_facets": ["prediction"]})
            for n in range(count)
        })

    def config(self, staged=True, reuse=True):
        cfg = PriorArtConfig(reuse_llm_across_stages=reuse, max_workers_llm_scoring=2,
                             enable_prefilter=False, enable_llm_prefilter=False)
        cfg.staged_retrieval = staged
        return cfg

    def score(self, state, stage, *, cfg=None, idea="prediction idea", facets=None, features=None):
        complete_features = {"datasets": set(), "metrics": set(), "method_families": set()}
        complete_features.update(features or {})
        return scoring.score_papers_with_abstract_only(
            None, state, facets or ["prediction"], complete_features, {},
            idea_description=idea, cfg=cfg or self.config(), stage=stage,
        )

    def events(self):
        return [json.loads(line) for line in (self.runtime.ledger.root / "events.jsonl").read_text(encoding="utf-8").splitlines()]

    def test_unchanged_scores_reuse_with_current_stage_assessment(self):
        state = self.state()
        with use_runtime(self.runtime):
            first = self.score(state, "initial")
            second = self.score(state, "after_citation")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual([asdict(m) for m in first], [asdict(m) for m in second])
        self.assertIsNot(first[0], second[0])
        reuse = [row for row in self.events() if row["event"] == "LLM_RESPONSE_REUSED"]
        self.assertEqual(len(reuse), 2)
        self.assertTrue(all(row["stage"] == "abstract_scoring_after_citation" for row in reuse))
        self.assertTrue(all(row["saved_tokens"] is None for row in reuse))
        assessments = [row for row in self.events() if row["event"] == "ABSTRACT_ASSESSMENT"]
        self.assertEqual([(row["stage"], row["paper_id"]) for row in assessments],
                         [(stage, pid) for stage in ("initial", "after_citation") for pid in ("p0", "p1")])
        self.assertEqual(self.runtime.scheduler.attempts, 2)

    def test_changed_evidence_idea_facets_features_model_and_endpoint_invalidate(self):
        state = self.state(1)
        with use_runtime(self.runtime):
            self.score(state, "initial")
            state.papers["p0"].year = 2018
            self.score(state, "year_changed")
            state.papers["p0"].abstract += " Additional evidence"
            self.score(state, "abstract_changed")
            self.score(state, "idea_changed", idea="another idea")
            self.score(state, "facets_changed", facets=["another facet"])
            self.score(state, "features_changed", features={"datasets": {"new dataset"}})
            with patch.object(scoring, "get_litellm_config", return_value=("fake-v2", "offline://model", "fake-key")):
                self.score(state, "model_changed")
            with patch.object(scoring, "get_litellm_config", return_value=("fake", "offline://other", "fake-key")):
                self.score(state, "endpoint_changed")
            state.papers["p0"].document_version = "v2"
            self.score(state, "version_changed")
            self.score(state, "same_new_version")
        self.assertEqual(len(self.calls), 9)

    def test_staged_popularity_and_provenance_only_changes_reuse(self):
        state = self.state(1)
        with use_runtime(self.runtime):
            self.score(state, "initial")
            state.papers["p0"].citation_count = 10000
            state.papers["p0"].citation_count_source = "s2"
            state.papers["p0"].source_records.append({"source": "crossref", "verified": True})
            state.papers["p0"].publication_status = "verified"
            self.score(state, "after_metadata")
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn("Citation Count:", self.calls[0][0]["content"])

    def test_legacy_citation_prompt_change_still_invalidates(self):
        state = self.state(1)
        with use_runtime(self.runtime):
            self.score(state, "initial", cfg=self.config(staged=False))
            state.papers["p0"].citation_count = 9
            self.score(state, "after_metadata", cfg=self.config(staged=False))
        self.assertEqual(len(self.calls), 2)
        self.assertIn("Citation Count: 9", self.calls[1][0]["content"])

    def test_abstract_tail_changes_invalidate_without_shortening_existing_prompt(self):
        state = self.state(1)
        state.papers["p0"].abstract = "x" * 1100
        with use_runtime(self.runtime):
            self.score(state, "initial")
            state.papers["p0"].abstract += "new tail"
            self.score(state, "changed_tail")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[0], self.calls[1])
        self.assertIn("Abstract: " + "x" * 1000, self.calls[0][0]["content"])

    def test_failed_model_scores_are_not_cached_or_converted_to_zero(self):
        state = self.state(1)
        self.handler = lambda messages: '{"coverage_level":99}'
        with use_runtime(self.runtime):
            first = self.score(state, "initial")
            second = self.score(state, "after_citation")
        self.assertEqual(len(self.calls), 4)  # Merged plus separate fallback, both stages.
        self.assertIsNone(first[0].coverage_level)
        self.assertIsNone(second[0].coverage_level)
        self.assertEqual(second[0].scoring_status, "failed")
        self.assertFalse(any(row["event"] == "LLM_RESPONSE_REUSED" for row in self.events()))

    def test_staged_pool_bypasses_old_caps_and_both_prefilters(self):
        state = self.state(13)
        cfg = self.config()
        cfg.max_abstract_scoring_initial = 1
        cfg.enable_prefilter = cfg.enable_llm_prefilter = True
        cfg.prefilter_min_papers = 0
        with use_runtime(self.runtime), \
                patch.object(scoring, "generate_relevance_keywords_from_idea", side_effect=AssertionError("redundant keywords")), \
                patch.object(scoring, "llm_relevance_prefilter", side_effect=AssertionError("redundant boolean gate")):
            result = self.score(state, "initial", cfg=cfg)
        self.assertEqual(len(result), 13)
        self.assertEqual(len(self.calls), 13)
        self.assertTrue(all(m.covered_facet_labels == ["F1"] and m.task_similarity == .9 and m.method_similarity == .8 for m in result))

    def test_missing_abstract_waits_for_enrichment_without_llm_judgment(self):
        state = self.state(1)
        state.papers["p0"].abstract = None
        with use_runtime(self.runtime):
            first = self.score(state, "initial")
            self.assertEqual(self.calls, [])
            self.assertIsNone(first[0].coverage_level)
            self.assertEqual(first[0].scoring_status, "missing_abstract")
            state.papers["p0"].abstract = "Now there is actual evidence"
            second = self.score(state, "after_metadata")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(second[0].coverage_level, 3)

    def test_conflict_or_newly_known_out_of_scope_year_does_not_trigger_scoring(self):
        state = self.state(2)
        state.intent.years = (2015, 2025)
        state.papers['p0'].identity_status = 'conflict'
        state.papers['p1'].year = 2026
        with use_runtime(self.runtime):
            result = self.score(state, 'after_metadata')
        self.assertEqual(self.calls, [])
        self.assertEqual({m.scoring_status for m in result}, {'identity_conflict', 'out_of_scope'})
        self.assertTrue(all(m.coverage_level is None for m in result))

    def test_reuse_is_opt_in_and_does_not_cross_runs(self):
        state = self.state(1)
        with use_runtime(self.runtime):
            self.score(state, "initial", cfg=self.config(staged=False, reuse=False))
            self.score(state, "after_citation", cfg=self.config(staged=False, reuse=False))
            self.score(state, "opt_in")
        second_runtime = LLMRuntime(RunLedger(self.root / "second"))
        with use_runtime(second_runtime):
            self.score(state, "initial")
        self.assertEqual(len(self.calls), 4)

    def test_no_active_runtime_never_retains_a_process_global_memo(self):
        state = self.state(1)
        with use_runtime(None):
            self.score(state, "initial")
            self.score(state, "after_citation")
        self.assertEqual(len(self.calls), 2)

    def test_boolean_failure_keep_is_not_reused_but_valid_false_is(self):
        state = self.state(1)
        answers = iter(['{"is_relevant":"false"}', '{"is_relevant":false}'])
        self.handler = lambda messages: next(answers)
        with use_runtime(self.runtime):
            first = scoring.llm_relevance_prefilter(list(state.papers.items()), "topic", "idea", stage="initial", reuse=True)
            second = scoring.llm_relevance_prefilter(list(state.papers.items()), "topic", "idea", stage="after_citation", reuse=True)
            third = scoring.llm_relevance_prefilter(list(state.papers.items()), "topic", "idea", stage="after_metadata", reuse=True)
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        self.assertEqual(third, [])
        self.assertEqual(len(self.calls), 2)

    def test_keyword_fallback_is_not_cached_and_word_limits_are_separate(self):
        answers = iter(['{}', '["prediction"]', '["prediction", "system"]'])
        self.handler = lambda messages: next(answers)
        with use_runtime(self.runtime):
            outputs = [scoring.generate_relevance_keywords_from_idea(None, "topic", "idea", ["facet"], reuse=True) for _ in range(3)]
            fulltext = scoring.generate_evidence_keywords_from_idea(None, "topic", "idea", ["facet"], {}, reuse=True)
        self.assertEqual(outputs[0], ["topic", "facet"])
        self.assertEqual(outputs[1:], [["prediction"], ["prediction"]])
        failures = [{k: row.get(k) for k in ('event','error_type','error')}
                    for row in self.events() if row['event'] == 'ATTEMPT_FAILED']
        self.assertEqual(fulltext, ["prediction", "system"], msg=str(failures))
        self.assertEqual(len(self.calls), 3)

    def test_concurrent_identical_keywords_make_one_request(self):
        def handler(messages):
            time.sleep(.02)
            return '["prediction"]'
        self.handler = handler
        with use_runtime(self.runtime), ThreadPoolExecutor(max_workers=4) as pool:
            jobs = [pool.submit(copy_context().run, scoring.generate_relevance_keywords_from_idea,
                                None, "topic", "idea", ["facet"], reuse=True) for _ in range(4)]
            self.assertEqual([job.result() for job in jobs], [["prediction"]] * 4)
        self.assertEqual(len(self.calls), 1)
        events = [row for row in self.events() if row["event"] == "LLM_RESPONSE_REUSED"]
        self.assertEqual(len(events), 3)
        self.assertEqual(len({row["reuse_event_id"] for row in events}), 3)

    def test_fulltext_uses_shortlist_similarity_and_successful_backfill(self):
        state = self.state(5)
        state.papers["p0"].identity_status = "conflict"
        state.papers["p2"].citation_count = 1000000
        matches = [PriorArtMatch(f"p{n}", 3, task_similarity=1 - n / 10, method_similarity=.5) for n in range(5)]
        attempted = []

        def enrich(state, pids, **kwargs):
            attempted.extend(pids)
            for pid in pids:
                if pid != "p1":
                    state.papers[pid].chunks = [Chunk(text="Valid evidence")]
                    state.papers[pid].pdf_status = "downloaded"
        with patch.object(scoring, "enrich_papers_with_fulltext", side_effect=enrich), \
                patch.object(scoring, "generate_evidence_keywords_from_idea", return_value=["prediction"]), \
                patch.object(scoring, "find_evidence_snippets", return_value=[]):
            result = scoring.refine_top_papers_with_fulltext(
                None, state, matches, "idea", ["facet"], {}, top_k=2,
                cfg=self.config(), eligible_paper_ids={"p0", "p1", "p2", "p3"},
            )
        self.assertEqual(attempted, ["p1", "p2", "p3"])
        self.assertEqual(len(result), 5)  # Unread candidates remain in the denominator.
        self.assertEqual(self.calls, [])

    def test_empty_fulltext_pool_skips_keyword_llm(self):
        with patch.object(scoring, "generate_evidence_keywords_from_idea", side_effect=AssertionError("unused request")):
            result = scoring.refine_top_papers_with_fulltext(None, self.state(), [], "idea", [], {}, cfg=self.config())
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
