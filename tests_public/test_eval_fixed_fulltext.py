"""Fixed evaluation material selection without real network or model calls."""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import fitz

from pipelines.run_manager import RunManager
from pipelines.state import Chunk, Intent, Paper, PipelineState
from pipelines.tasks import prior_art_scoring as scoring
from pipelines.tasks.prior_art_types import PriorArtMatch


class FixedFulltextTests(unittest.TestCase):
    def setUp(self):
        task_tmp = Path(__file__).resolve().parents[1] / "audit" / "implementation" / "tmp"
        task_tmp.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=task_tmp)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.manager = RunManager("fixed evaluation", base_dir=self.root)
        self.manager.create_run_directory()
        self.cfg = SimpleNamespace(retrieval_eval_fulltext_limit=5, max_workers_pdf=3)
        for target in ("requests.sessions.Session.request", "socket.create_connection", "socket.socket.connect"):
            blocked = patch(target, side_effect=AssertionError("Live network is forbidden"))
            blocked.start()
            self.addCleanup(blocked.stop)
        for target in ("invoke_model", "LiteLLMModel", "generate_evidence_keywords_from_idea"):
            blocked = patch.object(scoring, target, side_effect=AssertionError("Fixed material preparation must not invoke an LLM"))
            blocked.start()
            self.addCleanup(blocked.stop)

    def state(self, ids):
        return PipelineState(intent=Intent(task_type="prior_art_check", topic="prediction", years=(2015, 2025)),
                             papers={pid: Paper(title=pid, abstract="prediction mechanism", year=2024) for pid in ids})

    def match(self, pid, level=2, task=0.5, method=0.5, status="succeeded"):
        return PriorArtMatch(pid, level, task_similarity=task, method_similarity=method,
                             scoring_status=status, covered_facet_labels=["F1"], notes="abstract baseline")

    def plan(self):
        return json.loads((self.manager.run_dir / "eval_fulltext_plan.json").read_text(encoding="utf-8"))

    def material_ready(self, state, ids, **kwargs):
        for pid in ids:
            paper = state.papers[pid]
            paper.chunks = [Chunk(text=f"complete material for {pid}")]
            paper.pdf_status = "downloaded"
            paper.pages_status = paper.chunks_status = "parsed"
            paper.has_fulltext = True

    def test_fixed_selection_uses_abstract_match_order_without_modifying_grades(self):
        state = self.state(["F", "E", "D", "C", "B", "A"])
        matches = [self.match("F", 0), self.match("E", 1), self.match("D", 2, 0.1),
                   self.match("C", 2, 0.8, 0.2), self.match("B", 2, 0.8, 0.9), self.match("A", 3)]
        baseline = deepcopy([asdict(match) for match in matches])
        with patch.object(scoring, "enrich_papers_with_fulltext", side_effect=self.material_ready) as enrich:
            attempted = scoring.prepare_fixed_fulltext_for_eval(state, matches, list(state.papers), self.manager, self.cfg)
        self.assertEqual(attempted, ["A", "B", "C", "D", "E"])
        enrich.assert_called_once_with(state, attempted, max_workers=3, run_mgr=self.manager)
        self.assertEqual([asdict(match) for match in matches], baseline)
        self.assertEqual(self.plan()["success_material_count"], 5)
        self.assertEqual(self.plan()["available_ids"], attempted)

    def test_failed_first_candidate_keeps_fixed_five_and_does_not_replace_with_sixth(self):
        state = self.state(["A", "B", "C", "D", "E", "F"])
        matches = [self.match(pid, 3, task=0.9 - index * 0.1) for index, pid in enumerate(state.papers)]
        baseline = deepcopy([asdict(match) for match in matches])
        def acquire(state, ids, **kwargs):
            self.assertEqual(self.plan()["status"], "PLANNED")
            self.assertEqual(self.plan()["attempted_ids"], ["A", "B", "C", "D", "E"])
            state.papers["A"].pdf_status = "error"
            self.material_ready(state, ids[1:])
        with patch.object(scoring, "enrich_papers_with_fulltext", side_effect=acquire) as enrich:
            attempted = scoring.prepare_fixed_fulltext_for_eval(state, matches, list(state.papers), self.manager, self.cfg)
        self.assertEqual(attempted, ["A", "B", "C", "D", "E"])
        self.assertEqual(enrich.call_count, 1)
        self.assertEqual(state.papers["F"].pdf_status, "not_attempted")
        plan = self.plan()
        self.assertEqual(plan["status"], "PARTIAL_MATERIAL")
        self.assertTrue(plan["no_replacement"])
        self.assertEqual(plan["success_material_count"], 4)
        self.assertEqual(plan["missing_material_ids"], ["A"])
        self.assertEqual(plan["materials"][0]["pdf_status"], "error")
        self.assertEqual([asdict(match) for match in matches], baseline)

    def test_empty_candidates_record_zero_without_acquisition_or_llm(self):
        state = self.state([])
        with patch.object(scoring, "enrich_papers_with_fulltext") as enrich:
            self.assertEqual(scoring.prepare_fixed_fulltext_for_eval(state, [], [], self.manager, self.cfg), [])
        enrich.assert_not_called()
        self.assertEqual(self.plan()["status"], "NO_CANDIDATES")
        self.assertEqual(self.plan()["success_material_count"], 0)
        self.assertEqual(self.plan()["attempted_count"], 0)

    def test_entity_dedup_years_conflicts_and_shortlist_boundary(self):
        state = self.state(["bad_year", "conflict", "old_version", "best_version", "unknown_year", "outside_shortlist"])
        state.papers["bad_year"].year = 2014
        state.papers["conflict"].identity_status = "conflict"
        state.papers["old_version"].entity_id = state.papers["best_version"].entity_id = "same_work"
        state.papers["unknown_year"].year = None
        matches = [self.match("old_version", 1), self.match("best_version", 2),
                   self.match("unknown_year", 1), self.match("outside_shortlist", 3),
                   self.match("bad_year", 3), self.match("conflict", 3)]
        shortlist = ["bad_year", "conflict", "old_version", "unknown_year", "best_version", "best_version", "missing"]
        with patch.object(scoring, "enrich_papers_with_fulltext", side_effect=self.material_ready):
            attempted = scoring.prepare_fixed_fulltext_for_eval(state, matches, shortlist, self.manager, self.cfg)
        self.assertEqual(attempted, ["best_version", "unknown_year"])
        self.assertEqual(self.plan()["attempted_count"], 2)
        self.assertEqual({item["reason"] for item in self.plan()["excluded_records"]},
                         {"known_year_out_of_scope", "identity_conflict", "duplicate_entity", "missing_record"})
        self.assertEqual(state.papers["outside_shortlist"].pdf_status, "not_attempted")

    def test_unassessed_records_only_fill_after_valid_matches_and_are_labeled(self):
        state = self.state(["missing_abstract", "failed", "valid", "unscored"])
        state.papers["missing_abstract"].abstract = None
        matches = [self.match("missing_abstract", None, status="missing_abstract"),
                   self.match("failed", None, status="failed"), self.match("valid", 0)]
        with patch.object(scoring, "enrich_papers_with_fulltext", side_effect=self.material_ready):
            attempted = scoring.prepare_fixed_fulltext_for_eval(state, matches, list(state.papers), self.manager, self.cfg)
        self.assertEqual(attempted, ["valid", "missing_abstract", "failed", "unscored"])
        selections = self.plan()["selections"]
        self.assertEqual(selections[0]["selection_basis"], "valid_abstract_match")
        self.assertEqual(selections[0]["accepted_abstract_grade"], 0)
        for selection in selections[1:]:
            self.assertEqual(selection["selection_basis"], "unassessed_candidate_order")
            self.assertIsNone(selection["accepted_abstract_grade"])
        self.assertEqual(selections[-1]["abstract_scoring_status"], "not_scored")

    def test_collection_exception_is_propagated_and_final_stats_survive(self):
        state = self.state(["A"])
        with patch.object(scoring, "enrich_papers_with_fulltext", side_effect=RuntimeError("offline fixture")):
            with self.assertRaises(RuntimeError):
                scoring.prepare_fixed_fulltext_for_eval(state, [self.match("A")], ["A"], self.manager, self.cfg)
        self.assertEqual(self.plan()["status"], "COLLECTION_FAILED")
        self.assertEqual(self.plan()["collection_error_type"], "RuntimeError")
        self.assertEqual(self.plan()["attempted_count"], 1)
        self.assertEqual(self.plan()["success_material_count"], 0)

    def test_other_limits_are_rejected_before_acquisition(self):
        state = self.state(["A"])
        with patch.object(scoring, "enrich_papers_with_fulltext") as enrich:
            for limit in (0, 1, 6, True):
                with self.subTest(limit=limit), self.assertRaises(ValueError):
                    scoring.prepare_fixed_fulltext_for_eval(state, [self.match("A")], ["A"], self.manager,
                                                          SimpleNamespace(retrieval_eval_fulltext_limit=limit, max_workers_pdf=3))
        enrich.assert_not_called()

    def test_real_local_pdf_batch_preserves_full_text_and_does_not_touch_sixth(self):
        state = self.state(["A", "B", "C", "D", "E", "F"])
        for pid, paper in state.papers.items():
            path = self.root / f"{pid}.pdf"
            if pid == "A":
                path.write_bytes(b"not a PDF")
            else:
                with fitz.open() as document:
                    page = document.new_page()
                    page.insert_text((72, 72), f"1 Introduction\nBEGIN_{pid} text from the complete first page.")
                    page = document.new_page()
                    page.insert_text((72, 72), f"2 Experiments\nEND_{pid} text from the complete last page.")
                    path.write_bytes(document.tobytes())
            paper.local_pdf_path = str(path)
        matches = [self.match(pid, 3, task=0.9 - index * 0.1) for index, pid in enumerate(state.papers)]
        baseline = deepcopy([asdict(match) for match in matches])
        attempted = scoring.prepare_fixed_fulltext_for_eval(state, matches, list(state.papers), self.manager, self.cfg)
        self.assertEqual(attempted, ["A", "B", "C", "D", "E"])
        plan = self.plan()
        self.assertEqual(plan["success_material_count"], 4)
        self.assertEqual(state.papers["A"].pdf_status, "error")
        self.assertEqual(state.papers["F"].pdf_status, "not_attempted")
        self.assertFalse(state.papers["F"].chunks)
        for material in plan["materials"][1:]:
            pages = json.loads(Path(material["page_cache_path"]).read_text(encoding="utf-8"))["pages"]
            self.assertEqual([page["page"] for page in pages], [1, 2])
            self.assertIn(f'BEGIN_{material["paper_id"]}', pages[0]["text"])
            self.assertIn(f'END_{material["paper_id"]}', pages[-1]["text"])
        self.assertEqual([asdict(match) for match in matches], baseline)


if __name__ == "__main__":
    unittest.main()
