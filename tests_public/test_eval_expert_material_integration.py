"""Actual fixed PDF preparation feeding audited snippets to one offline Expert."""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fitz

from pipelines.run_manager import RunManager
from pipelines.runtime import use_runtime
from pipelines.state import Intent, Paper, PipelineState
from pipelines.tasks import prior_art_expert_ranking as expert
from pipelines.tasks import prior_art_scoring as scoring
from pipelines.tasks.prior_art_types import PriorArtConfig, PriorArtMatch
from tools.pdf_parser import PdfParserTool


class ExpertMaterialIntegrationTests(unittest.TestCase):
    def assert_located_snippets(self, material, source_pages):
        self.assertEqual(material["representation"], "selected_snippets")
        self.assertFalse(material["complete"])
        self.assertTrue(material["snippets"])
        self.assertLessEqual(len(material["snippets"]), 6)
        previous_end = None
        sent_texts = []
        for snippet in material["snippets"]:
            self.assertEqual(snippet["sent_start"], 0 if previous_end is None else previous_end + 2)
            text = material["text"][snippet["sent_start"]:snippet["sent_end"]]
            original = source_pages[snippet["page"]]
            self.assertEqual(text, original[snippet["source_start"]:snippet["source_end"]])
            self.assertGreaterEqual(snippet["source_start"], 0)
            self.assertLessEqual(snippet["source_end"], len(original))
            self.assertEqual(len(text), snippet["length"])
            self.assertLessEqual(len(text), 1800)
            self.assertEqual(hashlib.sha256(text.encode("utf-8")).hexdigest(), snippet["text_sha256"])
            self.assertNotIn("text", snippet, "Snippet metadata must not duplicate the sent evidence")
            previous_end = snippet["sent_end"]
            sent_texts.append(text)
        self.assertEqual(material["text"], "\n\n".join(sent_texts))
        self.assertEqual(previous_end, len(material["text"]))
        self.assertEqual({page["page"] for page in material["source_pages"]}, set(source_pages))
        for page in material["source_pages"]:
            text = source_pages[page["page"]]
            self.assertEqual(page["length"], len(text))
            self.assertEqual(page["text_sha256"], hashlib.sha256(text.encode("utf-8")).hexdigest())

    def test_fixed_five_located_pdf_snippets_feed_one_expert_with_the_whole_pool(self):
        task_tmp = Path(__file__).resolve().parents[1] / "audit" / "implementation" / "tmp"
        task_tmp.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=task_tmp) as temporary:
            root = Path(temporary)
            manager = RunManager("fixed five to Expert", base_dir=root / "artifacts",
                                 shared_pdf_cache_dir=root / "shared_pages")
            manager.create_run_directory()
            ids = ["A", "B", "C", "D", "E", "F"]
            abstracts = {pid: f"Full original abstract {pid}. The idea concerns latent interactions and physical motion."
                         for pid in ids}
            state = PipelineState(intent=Intent(task_type="prior_art_check", topic="latent interaction", years=(2015, 2025)),
                                  papers={pid: Paper(title=f"Paper {pid}", abstract=abstracts[pid], year=2024,
                                                     entity_id=f"work-{pid}") for pid in ids})
            expected_source_pages = {}
            for pid, paper in state.papers.items():
                path = root / f"{pid}.pdf"
                paper.local_pdf_path = str(path)
                if pid == "A":
                    path.write_bytes(b"not a PDF; first fixed candidate has unavailable full text")
                    continue
                first = f"1 Introduction\nBEGIN_{pid} complete first-page experiment context."
                last = f"2 Experiments\nEND_{pid} complete last-page result and limitations."
                with fitz.open() as document:
                    document.new_page().insert_text((72, 72), first)
                    document.new_page().insert_text((72, 72), last)
                    path.write_bytes(document.tobytes())
                # This independent text oracle reflects the fixture's visible
                # lines and the native extractor's final newline on each page.
                expected_source_pages[pid] = {1: first + "\n", 2: last + "\n"}
            matches = [PriorArtMatch(pid, grade, task_similarity=0.9 - index * 0.1,
                                     method_similarity=0.5, covered_facet_labels=["F1"],
                                     notes=f"Original abstract grade for {pid}")
                       for index, (pid, grade) in enumerate(zip(ids, [3, 3, 2, 2, 1, 0]))]
            baseline = deepcopy([asdict(match) for match in matches])
            cfg = PriorArtConfig(execution_mode="retrieval_eval", max_workers_pdf=2)
            messages = []

            class FixtureModel:
                model_id = "fixture-expert"
                api_base = "offline://fixture-expert"
                api_key = "test-key-example-fixture-not-a-credential"
                kwargs = {}

                def __call__(self, call_messages, **kwargs):
                    messages.append(deepcopy(call_messages))
                    return json.dumps({"candidates": [
                        {"rank": rank, "paper_id": pid,
                         "expert_reason": f"Independent fixture judgment for {pid}",
                         "evidence_source": "abstract" if pid == "F" else "abstract_and_fulltext"}
                        for rank, pid in enumerate(["F", "E", "D", "C", "B"], 1)
                    ]})

            fixture_model = FixtureModel()
            blocked_targets = ("requests.sessions.Session.request", "socket.create_connection", "socket.socket.connect")
            from contextlib import ExitStack
            with ExitStack() as stack:
                blocked = [stack.enter_context(patch(target, side_effect=AssertionError("Live network is forbidden")))
                           for target in blocked_targets]
                configuration = stack.enter_context(patch.object(expert, "get_litellm_config", return_value=(
                    fixture_model.model_id, fixture_model.api_base, fixture_model.api_key)))
                constructor = stack.enter_context(patch.object(expert, "LiteLLMModel", return_value=fixture_model))
                original_extract = PdfParserTool._extract_text_pymupdf
                extraction = stack.enter_context(patch.object(PdfParserTool, "_extract_text_pymupdf", autospec=True,
                                                              side_effect=original_extract))
                with use_runtime(manager.runtime):
                    attempted = scoring.prepare_fixed_fulltext_for_eval(
                        state, matches, ids, manager, cfg, years=(2015, 2025))
                    self.assertEqual(attempted, ["A", "B", "C", "D", "E"])
                    self.assertEqual(manager.runtime.ledger.task_statuses(), [], "Material collection makes no model requests")
                    self.assertEqual(extraction.call_count, 5, "Only the five fixed local PDFs were attempted")
                    result = expert.rerank_shortlist_with_expert(
                        state, matches, ids, attempted,
                        "Recover latent interactions and physical motion from observed trajectories.",
                        ["latent interactions", "physical motion"], manager, cfg, years=(2015, 2025))
                self.assertEqual(extraction.call_count, 5, "Expert uses the verified shared pages rather than re-extracting")
                for forbidden in blocked:
                    forbidden.assert_not_called()
                configuration.assert_called_once_with(role="expert")
                constructor.assert_called_once()

            plan = json.loads((manager.run_dir / "eval_fulltext_plan.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["attempted_ids"], ["A", "B", "C", "D", "E"])
            self.assertEqual(plan["success_material_count"], 4)
            self.assertEqual(plan["available_ids"], ["B", "C", "D", "E"])
            self.assertEqual(plan["missing_material_ids"], ["A"])
            self.assertTrue(plan["no_replacement"])
            self.assertEqual(state.papers["F"].pdf_status, "not_attempted")
            self.assertIsNone(state.papers["F"].content_hash)
            self.assertFalse(state.papers["F"].chunks)
            self.assertEqual(len(list(manager.pdf_pages_cache_dir.glob("*.pages.json"))), 4)

            self.assertEqual(len(messages), 1)
            sent = json.loads(messages[0][1]["content"])
            saved = json.loads((manager.run_dir / "expert_ranking_input.json").read_text(encoding="utf-8"))
            self.assertEqual(sent, expert._compact_model_input(saved))
            self.assertEqual([row["paper_id"] for row in sent["candidates"]], ids)
            self.assertEqual(saved["candidate_pool_ids"], ids)
            self.assertEqual(sent["candidate_count"], 6)
            self.assertLessEqual(sent["candidate_count"], 60)
            input_by_id = {row["paper_id"]: row for row in sent["candidates"]}
            self.assertEqual({pid: row["abstract"] for pid, row in input_by_id.items()}, abstracts)
            self.assertEqual(input_by_id["A"]["fulltext"]["text"], "")
            self.assertEqual(input_by_id["F"]["fulltext"]["text"], "")
            self.assertFalse(input_by_id["F"]["fulltext"]["attempted"])
            for pid in ["B", "C", "D", "E"]:
                material = next(row["fulltext"] for row in saved["candidates"] if row["paper_id"] == pid)
                self.assert_located_snippets(material, expected_source_pages[pid])
                self.assertEqual(input_by_id[pid]["fulltext"]["text"], material["text"])
                self.assertNotIn("verification", input_by_id[pid]["fulltext"])
                self.assertEqual(material["source"], "verified_pages_cache")
                self.assertEqual(Path(material["verification"]["pages_cache_path"]).parent,
                                 manager.pdf_pages_cache_dir)

            self.assertEqual(result["status"], "SUCCEEDED")
            self.assertEqual([candidate["paper_id"] for candidate in result["candidates"]], ["F", "E", "D", "C", "B"])
            self.assertEqual(result["candidates"][0]["coverage_level"], 0,
                             "A lower abstract grade with no attempted PDF can rank first under the unified Expert decision")
            self.assertEqual([asdict(match) for match in matches], baseline)
            tasks = manager.runtime.ledger.task_statuses()
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0]["stage"], "expert_ranking")
            self.assertEqual(tasks[0]["validation"], "VALID")

    def test_late_page_relevant_snippets_are_sent_without_all_middle_distractors(self):
        task_tmp = Path(__file__).resolve().parents[1] / "audit" / "implementation" / "tmp"
        task_tmp.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=task_tmp) as temporary:
            root = Path(temporary)
            manager = RunManager("late page snippets", base_dir=root / "artifacts",
                                 shared_pdf_cache_dir=root / "shared_pages")
            manager.create_run_directory()
            abstract = "Full original abstract: latent interactions and physical motion are inferred from trajectories."
            paper = Paper(title="Late-page interaction mechanism", abstract=abstract, year=2024)
            state = PipelineState(intent=Intent(task_type="prior_art_check", topic="latent interaction", years=(2015, 2025)),
                                  papers={"LATE": paper})
            source_pages = {1: "1 Introduction\nAn administrative catalog of static decorative textures.\n"}
            middle_markers = []
            for page in (2, 3, 4):
                lines = []
                for index in range(50):
                    marker = f"MIDDLE_{page}_LINE_{index}"
                    middle_markers.append(marker)
                    lines.append(marker + " decorative inventory and unrelated static texture statistics.")
                source_pages[page] = f"{page} Auxiliary Text\n" + "\n".join(lines) + "\n"
            source_pages[5] = "5 Experiments\n" + "\n".join([
                f"LATE_SIGNAL_{index} latent interactions predict physical motion from observed trajectories."
                for index in range(35)
            ]) + "\n"
            path = root / "late.pdf"
            with fitz.open() as document:
                for text in source_pages.values():
                    document.new_page().insert_text((72, 72), text.rstrip("\n"), fontsize=9)
                path.write_bytes(document.tobytes())
            paper.local_pdf_path = str(path)
            matches = [PriorArtMatch("LATE", 2, task_similarity=0.5, method_similarity=0.5,
                                     covered_facet_labels=["F1"], notes="Original abstract baseline")]
            baseline = deepcopy(asdict(matches[0]))
            cfg = PriorArtConfig(execution_mode="retrieval_eval", max_workers_pdf=2)
            messages = []

            class FixtureModel:
                model_id = "fixture-expert"
                api_base = "offline://fixture-expert"
                api_key = "test-key-example-fixture-not-a-credential"
                kwargs = {}

                def __call__(self, call_messages, **kwargs):
                    messages.append(deepcopy(call_messages))
                    return json.dumps({"candidates": [{"rank": 1, "paper_id": "LATE",
                        "expert_reason": "The late-page evidence supports the idea.",
                        "evidence_source": "abstract_and_fulltext"}]})

            fixture_model = FixtureModel()
            from contextlib import ExitStack
            with ExitStack() as stack:
                forbidden = [stack.enter_context(patch(target, side_effect=AssertionError("Live network is forbidden")))
                             for target in ("requests.sessions.Session.request", "socket.create_connection", "socket.socket.connect")]
                stack.enter_context(patch.object(expert, "get_litellm_config", return_value=(
                    fixture_model.model_id, fixture_model.api_base, fixture_model.api_key)))
                stack.enter_context(patch.object(expert, "LiteLLMModel", return_value=fixture_model))
                original_extract = PdfParserTool._extract_text_pymupdf
                extraction = stack.enter_context(patch.object(PdfParserTool, "_extract_text_pymupdf", autospec=True,
                                                              side_effect=original_extract))
                with use_runtime(manager.runtime):
                    attempted = scoring.prepare_fixed_fulltext_for_eval(state, matches, ["LATE"], manager, cfg)
                    result = expert.rerank_shortlist_with_expert(
                        state, matches, ["LATE"], attempted,
                        "Recover latent interactions and physical motion from observed trajectories.",
                        ["latent interactions", "physical motion"], manager, cfg, years=(2015, 2025))
                self.assertEqual(extraction.call_count, 1)
                for blocker in forbidden:
                    blocker.assert_not_called()
            self.assertEqual(result["status"], "SUCCEEDED")
            self.assertEqual(len(messages), 1)
            row = json.loads(messages[0][1]["content"])["candidates"][0]
            self.assertEqual(row["abstract"], abstract)
            material = row["fulltext"]
            saved = json.loads((manager.run_dir / "expert_ranking_input.json").read_text(encoding="utf-8"))
            self.assert_located_snippets(saved["candidates"][0]["fulltext"], source_pages)
            self.assertEqual(material["text"], saved["candidates"][0]["fulltext"]["text"])
            self.assertTrue(any(snippet["page"] == 5 for snippet in material["snippets"]))
            self.assertIn("LATE_SIGNAL", material["text"])
            self.assertLess(sum(marker in material["text"] for marker in middle_markers), len(middle_markers))
            self.assertLess(len(material["text"]), sum(len(text) for text in source_pages.values()))
            self.assertEqual(asdict(matches[0]), baseline)
            self.assertEqual(len(manager.runtime.ledger.task_statuses()), 1)


if __name__ == "__main__":
    unittest.main()
