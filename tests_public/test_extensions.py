import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from paperarena import APIProfile, RunConfig, run_extension
from paperarena.extensions import validate_extension, execute_extension
from paperarena.diagnostics import safe_error

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests_public/fixtures/optional_code.md"


class ExtensionTests(unittest.TestCase):
    def config(self):
        return RunConfig({"local": APIProfile("openai", "test-model")})

    def test_isolated_native_code_analysis_needs_no_model_key(self):
        with tempfile.TemporaryDirectory() as folder:
            result = run_extension("code_extractor", self.config(),
                {"document_path": str(FIXTURE), "llm_analysis": False}, output_dir=folder, timeout_seconds=60)
            self.assertEqual(result["status"], "SUCCEEDED", result.get("error"))
            output = result["extension_result"]
            self.assertEqual(output["document_evidence"]["source_sha256"], hashlib.sha256(FIXTURE.read_bytes()).hexdigest())
            self.assertFalse(output["details"]["code_execution_enabled"])
            blocks = output["details"]["analysis"]["code_blocks"]
            self.assertTrue(any("def add_one" in block["code"] for block in blocks))
            self.assertEqual(result["usage"]["model_attempts"], 0)
            self.assertIn("**Execution test**: NOT_RUN", output["report_markdown"])
            self.assertTrue(Path(output["report_path"]).exists())
            self.assertFalse(result["top5"])

    def test_relative_document_path_is_resolved_before_worker(self):
        with tempfile.TemporaryDirectory() as folder:
            result = run_extension("code_extractor", self.config(),
                {"document_path": str(FIXTURE.relative_to(Path.cwd())), "llm_analysis": False},
                output_dir=folder, timeout_seconds=60)
            self.assertEqual(result["status"], "SUCCEEDED", result.get("error"))

    def test_missing_document_has_local_stage_diagnostic(self):
        with tempfile.TemporaryDirectory() as folder:
            result = run_extension("code_extractor", self.config(),
                {"document_path": str(Path(folder) / "missing.md"), "llm_analysis": False},
                output_dir=folder, timeout_seconds=60)
            self.assertEqual(result["status"], "FAILED")
            self.assertEqual(result["error"]["phase"], "extension_document")
            self.assertEqual(result["error"]["code"], "LOCAL_FILE_ERROR")

    def test_dry_run_does_not_import_optional_backends_or_require_key(self):
        with tempfile.TemporaryDirectory() as folder:
            result = run_extension("planner_worker", self.config(), {"topic": "test idea"},
                                   output_dir=folder, dry_run=True)
            self.assertEqual(result["status"], "NOT_RUN")
            plan = json.loads((Path(result["output_dir"]) / "plan.json").read_text())
            self.assertEqual(plan["extension"]["name"], "planner_worker")
            self.assertFalse(plan["benchmark_gold_access"])

    def test_model_analysis_requires_explicit_credentials(self):
        with self.assertRaisesRegex(ValueError, "Missing API key"):
            run_extension("code_extractor", self.config(), {"document_path": str(FIXTURE)})

    def test_limits_and_manuscript_inputs_are_validated(self):
        for name, parameters in [("evolution_mapper", {"topic": "x", "max_papers": 1000}),
                                 ("planner_worker", {"topic": "x", "max_steps": 21}),
                                 ("slot_filling", {"topic": "x"})]:
            with self.assertRaises(ValueError):
                validate_extension(name, parameters)
        detail = safe_error(ModuleNotFoundError("No package", name="mineru"), "extension_document")
        self.assertEqual(detail["missing_dependency"], "mineru")
        self.assertIn(".[mineru]", detail["install_hint"])

    def test_evolution_algorithm_builds_fixture_network(self):
        from tools.semantic_scholar_tool import SemanticScholarTool
        papers = [{"paperId": "p1", "title": "Earlier method", "year": 2020, "citationCount": 3},
                  {"paperId": "p2", "title": "Later method", "year": 2021, "citationCount": 2}]
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(SemanticScholarTool, "search_papers", return_value=papers), \
             patch.object(SemanticScholarTool, "get_paper_details", return_value={"references": papers[:1], "citations": papers[1:]}), \
             patch("tools.evolution_mapper.time.sleep"):
            output = execute_extension(validate_extension("evolution_mapper", {"topic": "test topic", "max_papers": 2}),
                                       self.config(), Path(folder), None, lambda event: None)
            self.assertEqual(output["status"], "SUCCEEDED", output["diagnostics"])
            self.assertIn("mermaid", output["report_markdown"])
            self.assertGreaterEqual(len(output["details"]["network"]["papers"]), 2)

    def test_discovery_exception_is_not_reported_as_empty_success(self):
        import requests
        with tempfile.TemporaryDirectory() as folder, patch("requests.Session.send", side_effect=requests.ConnectionError("private-provider-value")):
            output = execute_extension(validate_extension("evolution_mapper", {"topic": "test topic"}),
                                       self.config(), Path(folder), None, lambda event: None)
            self.assertEqual(output["status"], "FAILED")
            self.assertTrue(output["diagnostics"])
            self.assertNotIn("private-provider-value", json.dumps(output))

    def test_only_used_extension_profiles_need_keys(self):
        from paperarena.api import _resolve_for_operation
        config = RunConfig({"unused": APIProfile("openai", "test-model"),
                            "light": APIProfile("openai", "test-model", "test-key-example")},
                           roles={"heavy": "unused", "light": "light", "expert": "unused"})
        request = validate_extension("code_extractor", {"document_path": str(FIXTURE)})
        resolved = _resolve_for_operation(config, request, False)
        self.assertFalse(resolved.profiles["unused"].api_key)
        self.assertTrue(resolved.profiles["light"].api_key)

    def test_planner_executes_document_tool_and_records_critic_without_replay(self):
        from smolagents.models import Model
        class FixtureModel(Model):
            calls = 0
            def __init__(self, **kwargs):
                super().__init__(model_id="fixture-model")
            def generate(self, messages, **kwargs):
                FixtureModel.calls += 1
                if FixtureModel.calls == 1:
                    value = {"analysis": "Use supplied fixture", "selected_tools": [{
                        "tool_name": "code_extractor", "reason": "Inspect supplied code",
                        "parameters": {"pdf_name": "paper"}, "order": 1}],
                        "steps": ["Inspect document"], "expected_output": "Code report"}
                else:
                    value = {"quality_score": 0.9, "completeness_score": 0.9, "correctness_score": 0.9, "meets_requirements": True,
                             "strengths": [], "weaknesses": [], "improvements": [], "needs_reexecution": True}
                return SimpleNamespace(content=json.dumps(value))
        with tempfile.TemporaryDirectory() as folder:
            output = execute_extension(validate_extension("planner_worker", {
                "topic": "Inspect supplied fixture", "document_path": str(FIXTURE), "llm_analysis": False}),
                self.config(), Path(folder), FixtureModel, lambda event: None)
            self.assertEqual(output["status"], "SUCCEEDED", output["diagnostics"])
            self.assertEqual(output["details"]["reexecution_count"], 0)
            self.assertEqual(output["details"]["rounds"][0]["reexecution_status"], "NOT_RUN_BUDGET")
            self.assertEqual(output["details"]["execution"]["execution_steps"][0]["status"], "SUCCEEDED")
            self.assertEqual(FixtureModel.calls, 2)

    def test_legacy_report_and_citation_scorer_are_connected(self):
        from pipelines.state import Intent, Paper, PipelineState
        state = PipelineState(intent=Intent(task_type="benchmark_landscape", topic="fixture topic"))
        state.papers["p1"] = Paper(title="Synthetic related paper", year=2020, abstract="Fixture abstract")
        class FixtureModel:
            def __init__(self, **kwargs):
                pass
            def generate(self, messages, **kwargs):
                return SimpleNamespace(content=json.dumps({"relevance_level": 3, "should_cite": True,
                    "suggested_slot": "fixture method", "reason": "Synthetic scorer fixture"}))
        with patch("pipelines.exploration_loop.run_exploration_loop", return_value=state):
            for name in ("benchmark_landscape", "slot_filling"):
                with tempfile.TemporaryDirectory() as folder:
                    output = execute_extension(validate_extension(name, {"topic": "fixture topic",
                        "manuscript_excerpt": "Fixture draft", "known_refs": []}), self.config(),
                        Path(folder), FixtureModel, lambda event: None)
                    self.assertEqual(output["status"], "SUCCEEDED", output["diagnostics"])
                    self.assertEqual(output["details"]["paper_count"], 1)
                    if name == "slot_filling":
                        self.assertEqual(len(output["details"]["matches"]), 1)

    def test_invalid_citation_assessment_is_a_failure_not_a_zero_grade(self):
        from pipelines.state import Intent, Paper, PipelineState
        from pipelines.tasks.slot_filling import score_papers_for_slot_filling
        state = PipelineState(intent=Intent(task_type="slot_filling", topic="fixture"))
        state.papers["p1"] = Paper(title="Synthetic paper")
        for response in ("not JSON", json.dumps({"relevance_level": 9, "should_cite": False,
                        "suggested_slot": "", "reason": ""}),
                        json.dumps({"relevance_level": 1, "should_cite": "false",
                        "suggested_slot": "", "reason": ""})):
            matches = score_papers_for_slot_filling(SimpleNamespace(run=lambda prompt: response),
                                                    state, "Fixture draft", [], max_papers=1)
            self.assertEqual(matches[0].assessment_status, "FAILED")
            self.assertEqual(matches[0].error["code"], "INVALID_MODEL_RESPONSE")

    def test_bound_document_cannot_be_replaced_by_planner_arguments(self):
        from smolagents.models import Model
        class FixtureModel(Model):
            def __init__(self, **kwargs):
                super().__init__(model_id="fixture-model")
            def generate(self, messages, **kwargs):
                return SimpleNamespace(content=json.dumps({"selected_tools": [{"tool_name": "code_extractor",
                    "reason": "Fixture path escape", "parameters": {"pdf_name": "../other-document"}}],
                    "steps": [], "analysis": "Synthetic invalid arguments", "expected_output": "test"}))
        request = validate_extension("planner_worker", {"topic": "Fixture path escape",
            "document_path": str(FIXTURE), "llm_analysis": False, "enable_critic": False})
        with tempfile.TemporaryDirectory() as folder:
            output = execute_extension(request, self.config(), Path(folder), FixtureModel, lambda event: None)
            self.assertEqual(output["status"], "FAILED")
            self.assertEqual(output["details"]["execution"]["execution_steps"][0]["status"], "FAILED")


if __name__ == "__main__":
    unittest.main()
