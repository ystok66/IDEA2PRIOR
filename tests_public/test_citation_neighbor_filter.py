"""Citation-neighbor semantic gate contracts; no providers or benchmark data."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pipelines.tasks import citation_neighbor_filter as filtering
from pipelines.tasks import prior_art_scoring as scoring
from pipelines.runtime import LLMRuntime, RunLedger, get_runtime, use_runtime


def neighbor(index, *, abstract="Evidence about the proposed research problem.", title=None):
    return {"paperId": f"{index:040x}", "title": title or f"Synthetic paper {index}",
            "abstract": abstract, "year": 2023}


def user_payload(messages):
    message = next(message for message in reversed(messages) if message["role"] == "user")
    content = message["content"]
    if isinstance(content, list):
        content = "".join(block.get("text", "") for block in content)
    return json.loads(content)


class CitationNeighborFilterTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.config_calls = []
        self.handler = lambda payload: {"papers": [
            {"paper_id": paper["paper_id"], "score": 1,
             "reason": "The abstract establishes an adjacent technical connection."}
            for paper in payload["papers"]]}

        for target in ("requests.sessions.Session.request", "socket.create_connection",
                       "socket.socket.connect"):
            guard = patch(target, side_effect=AssertionError("Offline tests forbid live network"))
            guard.start()
            self.addCleanup(guard.stop)

        def config(*args, **kwargs):
            self.config_calls.append((args, kwargs))
            return ("offline-light", "offline://model", "offline-key")

        def invoke(model, messages, **kwargs):
            payload = user_payload(messages)
            self.calls.append({"payload": payload, "kwargs": kwargs})
            response = self.handler(payload)
            if isinstance(response, Exception):
                raise response
            text = response if isinstance(response, str) else json.dumps(response)
            # Exercise the production schema validator even though no model runs.
            validator = kwargs.get("validate_response")
            if validator is not None and validator(text) is not True:
                raise ValueError("Response validation failed")
            return text

        for replacement in (
            patch.object(filtering, "get_litellm_config", side_effect=config),
            patch.object(filtering, "LiteLLMModel", side_effect=lambda **kw: SimpleNamespace(**kw)),
            patch.object(filtering, "_invoke_with_run_reuse", side_effect=invoke),
        ):
            replacement.start()
            self.addCleanup(replacement.stop)

    def run_filter(self, groups, *, cap=15, batch_size=20, idea="Synthetic research idea", facets=(),
                   research_query=None, search_queries=(), relevance_keywords=(), workers=1):
        cfg = SimpleNamespace(citation_neighbor_batch_size=batch_size,
                              topk_neighbors_after_lexical=cap,
                              max_workers_llm_scoring=1,
                              max_workers_llm_prefilter=workers,
                              reuse_llm_across_stages=True)
        return filtering.filter_citation_neighbor_groups(
            groups, cfg, idea, facets=facets, research_query=research_query,
            search_queries=search_queries, relevance_keywords=relevance_keywords)

    def handler_for_scores(self, scores):
        return lambda payload: {"papers": [
            {"paper_id": paper["paper_id"], "score": scores[paper["paper_id"]],
             "reason": "Judgment supported by this synthetic abstract."}
            for paper in payload["papers"]]}

    def assessment(self, paper):
        result = paper["citation_filter_assessment"]
        self.assertIn("score", result)
        self.assertTrue(result.get("status"))
        self.assertTrue(result.get("reason"))
        self.assertTrue(result.get("policy"))
        self.assertTrue(result.get("model_role"))
        return result

    def test_synonymous_evidence_survives_without_literal_keyword_matches(self):
        paper = neighbor(1, title="Estimating flexible local areas from contributed location records",
                         abstract="Uses graded membership and volunteered geographical observations "
                                  "to delineate city districts with uncertain edges.")
        lexical_phrases = ["fuzzy boundaries", "locational uncertainty", "crowdsourced spatial data"]
        combined = (paper["title"] + " " + paper["abstract"]).lower()
        self.assertFalse(any(phrase in combined for phrase in lexical_phrases))
        before = deepcopy(paper)
        self.handler = self.handler_for_scores({paper["paperId"]: 3})
        result = self.run_filter([[paper]],
                                 idea="Infer uncertain neighborhood boundaries from contributed spatial data")
        self.assertEqual(len(result[0]), 1)
        self.assertEqual(self.assessment(result[0][0])["score"], 3)
        self.assertEqual(paper, before, "Filtering must not mutate fetched source evidence")
        self.assertIsNot(result[0][0], paper)
        self.assertEqual(len(self.calls), 1)

    def test_only_valid_zero_is_rejected_and_positive_scores_are_ordered(self):
        papers = [neighbor(index) for index in range(1, 5)]
        self.handler = self.handler_for_scores(dict(zip(
            [paper["paperId"] for paper in papers], [0, 1, 3, 2])))
        result = self.run_filter([papers])
        self.assertEqual([paper["paperId"] for paper in result[0]],
                         [papers[2]["paperId"], papers[3]["paperId"], papers[1]["paperId"]])
        self.assertEqual([self.assessment(paper)["score"] for paper in result[0]], [3, 2, 1])

    def test_duplicate_canonical_id_is_judged_once_but_kept_for_both_seeds(self):
        shared = neighbor(1)
        other = neighbor(2)
        groups = [[shared], [deepcopy(shared), other]]
        original = deepcopy(groups)
        result = self.run_filter(groups)
        judged = [paper["paper_id"] for call in self.calls for paper in call["payload"]["papers"]]
        self.assertCountEqual(judged, [shared["paperId"], other["paperId"]])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual([len(group) for group in result], [1, 2])
        self.assertEqual(result[0][0]["paperId"], result[1][0]["paperId"])
        self.assertIsNot(result[0][0], result[1][0])
        self.assertEqual(groups, original)

    def test_cap_applies_per_seed_to_known_positives_and_unknowns_are_preserved(self):
        papers = [neighbor(index) for index in reversed(range(1, 23))]
        scores = {paper["paperId"]: (None if int(paper["paperId"], 16) >= 21 else
                                    1 + int(paper["paperId"], 16) % 3)
                  for paper in papers}
        self.handler = self.handler_for_scores(scores)
        result = self.run_filter([papers, papers[:3]], cap=15)
        expected = sorted((paper for paper in papers if scores[paper["paperId"]] is not None),
                          key=lambda paper: -scores[paper["paperId"]])[:15]
        known = [paper for paper in result[0] if self.assessment(paper)["score"] is not None]
        unknown = [paper for paper in result[0] if self.assessment(paper)["score"] is None]
        self.assertEqual([paper["paperId"] for paper in known], [paper["paperId"] for paper in expected])
        self.assertCountEqual([paper["paperId"] for paper in unknown], [f"{21:040x}", f"{22:040x}"])
        self.assertEqual(len(result[0]), 17)
        self.assertEqual(len(result[1]), 3)

    def test_model_failure_preserves_all_candidates_even_when_over_positive_cap(self):
        papers = [neighbor(index) for index in range(1, 19)]
        self.handler = lambda payload: RuntimeError("Synthetic model failure")
        result = self.run_filter([papers])
        self.assertEqual([paper["paperId"] for paper in result[0]],
                         [paper["paperId"] for paper in papers])
        self.assertTrue(all(self.assessment(paper)["score"] is None for paper in result[0]))
        self.assertEqual(len(self.calls), 1, "Do not silently reissue a failed batch")

    def test_all_missing_abstracts_are_retained_without_any_model_call(self):
        papers = [neighbor(1, abstract=""), neighbor(2, abstract=None), neighbor(3, abstract="   ")]
        result = self.run_filter([papers])
        self.assertEqual(len(result[0]), 3)
        self.assertTrue(all(self.assessment(paper)["score"] is None for paper in result[0]))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.config_calls, [])

    def test_missing_abstract_cannot_be_converted_to_zero_by_model(self):
        papers = [neighbor(1, abstract=""), neighbor(2, abstract=None), neighbor(3)]
        self.handler = self.handler_for_scores({paper["paperId"]: 0 for paper in papers})
        result = self.run_filter([papers])
        self.assertCountEqual([paper["paperId"] for paper in result[0]],
                              [papers[0]["paperId"], papers[1]["paperId"]])
        self.assertTrue(all(self.assessment(paper)["score"] is None for paper in result[0]))
        judged = [paper["paper_id"] for call in self.calls for paper in call["payload"]["papers"]]
        self.assertEqual(judged, [papers[2]["paperId"]], "Missing evidence is not sent for a zero judgment")

    def test_invalid_batch_schema_fails_open_without_zero_or_lexical_fallback(self):
        papers = [neighbor(1), neighbor(2)]

        def valid_rows():
            return [{"paper_id": paper["paperId"], "score": 0,
                     "reason": "Explicitly unrelated synthetic evidence."} for paper in papers]

        malformed = []
        malformed.append("not JSON")
        malformed.append({"papers": valid_rows()[:1]})
        duplicate = valid_rows()
        duplicate[1]["paper_id"] = duplicate[0]["paper_id"]
        malformed.append({"papers": duplicate})
        unexpected = valid_rows()
        unexpected[1]["paper_id"] = "unknown-input-id"
        malformed.append({"papers": unexpected})
        for invalid_score in (True, False, "0", 0.0, -1, 4):
            rows = valid_rows()
            rows[0]["score"] = invalid_score
            malformed.append({"papers": rows})
        empty_reason = valid_rows()
        empty_reason[0]["reason"] = "   "
        malformed.append({"papers": empty_reason})

        for payload in malformed:
            with self.subTest(response=payload):
                self.handler = lambda input_payload, output=payload: output
                result = self.run_filter([papers])
                self.assertEqual(len(result[0]), len(papers))
                self.assertTrue(all(self.assessment(paper)["score"] is None for paper in result[0]))

    def test_batch_size_membership_and_full_evidence_are_preserved(self):
        tail = " FULL_ABSTRACT_TAIL_MARKER"
        abstract = "a" * 4000 + tail
        papers = [neighbor(index, abstract=abstract) for index in range(1, 46)]
        idea = "Entire synthetic idea " + "b" * 2500 + " IDEA_TAIL_MARKER"
        facets = ["A complete facet", "A second facet"]
        self.run_filter([papers], idea=idea, facets=facets)
        payloads = [call["payload"] for call in self.calls]
        self.assertEqual(sorted(len(payload["papers"]) for payload in payloads), [5, 20, 20])
        judged = [paper["paper_id"] for payload in payloads for paper in payload["papers"]]
        self.assertCountEqual(judged, [paper["paperId"] for paper in papers])
        self.assertEqual(len(set(judged)), 45)
        for payload in payloads:
            self.assertEqual(payload["idea_description"], idea)
            self.assertEqual(payload["facets"], facets)
            self.assertTrue(all(paper["abstract"] == abstract for paper in payload["papers"]))

    def test_no_neighbors_do_not_construct_a_model_or_resolve_provider_credentials(self):
        self.assertEqual(self.run_filter([[], []]), [[], []])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.config_calls, [])

    def test_original_query_and_organized_search_context_reach_same_judgment(self):
        research_query = "Original user's complete research request " + "q" * 3000
        queries = ["First organized search expression", "Second expression with synonyms"]
        keywords = ["Original supplied term", "Abbreviation", "Alternative wording"]
        facets = ["Core task", "Central mechanism"]
        self.run_filter([[neighbor(1)]], idea="Complete research idea", facets=facets,
                        research_query=research_query, search_queries=queries,
                        relevance_keywords=keywords)
        self.assertEqual(len(self.calls), 1)
        payload = self.calls[0]["payload"]
        self.assertEqual(payload["research_query"], research_query)
        self.assertEqual(payload["idea_description"], "Complete research idea")
        self.assertEqual(payload["facets"], facets)
        self.assertEqual(payload["search_queries"], queries)
        self.assertEqual(payload["relevance_keywords"], keywords)
        self.assertEqual(payload["papers"][0]["paper_id"], neighbor(1)["paperId"])

    def test_changed_idea_evidence_model_or_endpoint_gets_a_distinct_logical_task(self):
        paper = neighbor(1)
        self.run_filter([[paper]], idea="Original complete idea")
        self.run_filter([[paper]], idea="Original complete idea")
        self.run_filter([[paper]], idea="Changed complete idea")
        changed_evidence = {**paper, "abstract": paper["abstract"] + " Additional material."}
        self.run_filter([[changed_evidence]], idea="Original complete idea")
        with patch.object(filtering, "get_litellm_config",
                          return_value=("offline-other-model", "offline://model", "offline-key")):
            self.run_filter([[paper]], idea="Original complete idea")
        with patch.object(filtering, "get_litellm_config",
                          return_value=("offline-light", "offline://other-endpoint", "offline-key")):
            self.run_filter([[paper]], idea="Original complete idea")
        task_ids = [call["kwargs"]["task_id"] for call in self.calls]
        self.assertEqual(task_ids[0], task_ids[1], "Identical judgment requests retain their identity")
        self.assertEqual(len(set(task_ids)), 5)
        self.assertEqual([call["payload"]["papers"][0]["paper_id"] for call in self.calls],
                         [paper["paperId"]] * 6,
                         "Unchanged neighbor IDs alone cannot determine a judgment's task identity")

    def test_live_run_ledger_propagates_to_workers_and_reuses_only_identical_requests(self):
        temp_root = Path(__file__).resolve().parents[1] / "audit" / "implementation" / "tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        model_calls = []
        model_defaults = []
        call_lock = threading.Lock()
        coordinator_thread = threading.get_ident()

        class OfflineModel:
            def __init__(self, model_id, api_base, api_key, **defaults):
                self.model_id, self.api_base, self.api_key = model_id, api_base, api_key
                self.kwargs = defaults
                model_defaults.append(defaults)

            def __call__(self, messages, **kwargs):
                payload = user_payload(messages)
                with call_lock:
                    model_calls.append({"query": payload["research_query"],
                                        "thread_id": threading.get_ident(),
                                        "runtime": get_runtime()})
                return json.dumps({"papers": [
                    {"paper_id": paper["paper_id"], "score": 2,
                     "reason": "The synthetic evidence supports a shared mechanism."}
                    for paper in payload["papers"]]})

        papers = [neighbor(index) for index in range(1, 26)]
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            runtime = LLMRuntime(RunLedger(Path(directory)))
            with patch.object(filtering, "LiteLLMModel", OfflineModel), \
                    patch.object(filtering, "_invoke_with_run_reuse",
                                 side_effect=scoring._invoke_with_run_reuse), \
                    patch.object(scoring.llm_rate_limiter, "wait_for_slot"), \
                    use_runtime(runtime):
                first = self.run_filter([papers], research_query="Original query", workers=2)
                repeated = self.run_filter([papers], research_query="Original query", workers=2)
                changed = self.run_filter([papers], research_query="Changed query", workers=2)

            self.assertEqual(first, repeated)
            self.assertEqual(len(first[0]), 15)
            self.assertEqual(len(changed[0]), 15)
            self.assertEqual(len(model_calls), 4, "Two initial batches and two changed-query batches")
            self.assertEqual(runtime.scheduler.attempts, 4)
            self.assertCountEqual([call["query"] for call in model_calls],
                                  ["Original query", "Original query", "Changed query", "Changed query"])
            self.assertTrue(all(call["runtime"] is runtime for call in model_calls))
            self.assertTrue(all(call["thread_id"] != coordinator_thread for call in model_calls))
            self.assertTrue(all(defaults == {"timeout": 120, "num_retries": 0, "max_retries": 0}
                                for defaults in model_defaults))
            tasks = runtime.ledger.task_statuses()
            self.assertEqual(len(tasks), 4)
            self.assertTrue(all(task["stage"] == "citation_neighbor_scoring" and
                                task["status"] == "SUCCEEDED" and task["validation"] == "VALID"
                                for task in tasks))
            events = [json.loads(line) for line in
                      (runtime.ledger.root / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            reuse = [event for event in events if event["event"] == "LLM_RESPONSE_REUSED"]
            self.assertEqual(len(reuse), 2)
            self.assertTrue(all(event["stage"] == "citation_neighbor_scoring" and
                                event["avoided_api_calls"] == 1 for event in reuse))
            self.assertFalse(any(event["event"] == "ATTEMPT_FAILED" for event in events))

    def test_prompt_contains_scoring_examples_without_benchmark_answers(self):
        prompt = filtering.load_prompt()
        self.assertTrue(filtering.PROMPT_VERSION)
        self.assertIsInstance(prompt, str)
        self.assertGreater(len(prompt), 500)
        for score in (0, 1, 2, 3):
            self.assertRegex(prompt, rf'(?m)^{score}:')
        self.assertRegex(prompt, r'(?m)^null:')
        self.assertNotIn("µ-shapes", prompt)
        self.assertNotIn("FT001", prompt)


if __name__ == "__main__":
    unittest.main()
