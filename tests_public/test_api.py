"""Offline API contracts. No provider requests, PDF downloads, or gold reads."""
from __future__ import annotations

from contextlib import ExitStack
import io
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

from paperarena import APIProfile, JobManager, RunConfig, run_retrieval
from paperarena._worker import _token_counts, default_pipeline_settings, install_observers
from paperarena.__main__ import dataset_query, main as cli_main
from paperarena.config import worker_environment


def temporary_directory():
    # Keep test files inside the checkout on sandboxed Windows hosts.
    directory = Path(__file__).resolve().parents[1] / "outputs" / "offline_tests"
    directory.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=directory)


class ConfigTests(unittest.TestCase):
    def config(self):
        return RunConfig(profiles={
            "direct": APIProfile("openai", "model-a", api_key="private-a"),
            "google": APIProfile("gemini", "model-b", api_key="private-b"),
            "local": APIProfile("custom", "model-c", api_key="private-c", base_url="http://127.0.0.1:9090/v1"),
        }, roles={"heavy": "direct", "light": "google", "expert": "local"})

    def test_role_routes_and_no_parent_environment_mutation(self):
        before = dict(os.environ)
        environment = worker_environment(self.config())
        self.assertEqual(environment["LLM_HEAVY_API_BASE"], "https://api.openai.com/v1")
        self.assertEqual(environment["LLM_LIGHT_PROVIDER"], "google")
        self.assertEqual(environment["LLM_LIGHT_API_KEY"], "private-b")
        self.assertNotIn("LLM_LIGHT_API_BASE", environment)
        self.assertEqual(environment["LLM_EXPERT_API_BASE"], "http://127.0.0.1:9090/v1")
        self.assertTrue(before == dict(os.environ), "Parent environment was mutated")

    def test_native_gemini_keys_are_separate_per_role(self):
        from pipelines.llm_config import get_litellm_config
        config = RunConfig(profiles={"one": APIProfile("gemini", "model-a", "private-a"), "two": APIProfile("gemini", "model-b", "private-b")}, roles={"heavy": "one", "light": "two", "expert": "two"})
        with patch.dict(os.environ, worker_environment(config), clear=True):
            self.assertEqual(get_litellm_config("heavy"), ("gemini/model-a", None, "private-a"))
            self.assertEqual(get_litellm_config("expert"), ("gemini/model-b", None, "private-b"))

    def test_explicit_env_key_resolution_and_public_redaction(self):
        with patch.dict(os.environ, {"MY_TEST_KEY": "not-publishable", "UNWANTED_KEY": "legacy"}):
            config = RunConfig({"one": APIProfile("openai", "model-a", api_key_env="MY_TEST_KEY")}).resolved()
            self.assertEqual(config.profiles["one"].api_key, "not-publishable")
            self.assertNotIn("MY_TEST_KEY", worker_environment(config))
            self.assertNotIn("not-publishable", json.dumps(config.to_public_dict()))
            self.assertNotIn("not-publishable", repr(config))

    def test_only_routed_profiles_require_credentials(self):
        profiles = {"selected": APIProfile("openai", "model", "selected-key"),
                    "unused": APIProfile("custom", "other-model", base_url="https://example.invalid/v1")}
        config = RunConfig(profiles, {"heavy": "selected", "light": "selected", "expert": "selected"})
        self.assertEqual(config.resolved().profiles["unused"].api_key, "")
        with self.assertRaises(ValueError):
            RunConfig(profiles, {"heavy": "selected", "light": "selected", "expert": "unused"}).resolved()

    def test_invalid_routes_and_credential_url_rejected(self):
        with self.assertRaises(ValueError):
            RunConfig({"one": APIProfile("openai", "model")}, {"heavy": "missing", "light": "one", "expert": "one"})
        for url in ("https://user:password@example.invalid/v1", "https://example.invalid/v1?api_key=hidden"):
            with self.assertRaises(ValueError):
                APIProfile("custom", "model", base_url=url)
        with self.assertRaises(ValueError):
            APIProfile("gemini", "model", base_url="https://example.invalid/v1")
        with self.assertRaises(ValueError):
            RunConfig({"one": APIProfile("openai", "model")}, pipeline={"unrecognized_field": 1})

    def test_legacy_aliases_scrubbed_case_insensitively(self):
        legacy = {"Semantic_Scholar_API_key": "unwanted-legacy", "Semantic_Scholar_URL": "https://wrong.invalid",
                  "semantic_scholar_url": "https://also-wrong.invalid", "s2_api_key": "unwanted-s2",
                  "custom_llm_api_base": "https://wrong-model.invalid", "google_api_key": "unwanted-google",
                  "openalex_base_url": "https://wrong-search.invalid"}
        config = RunConfig({"one": APIProfile("openai", "model", "selected-model")},
                           search={"semantic_scholar_api_key": "selected-search"})
        with patch.dict(os.environ, legacy):
            env = worker_environment(config)
            self.assertFalse(any("unwanted" in value or "wrong.invalid" in value for value in env.values()))
            self.assertEqual(env["SEMANTIC_SCHOLAR_API_KEY"], "selected-search")
            self.assertEqual(env["Semantic_Scholar_API_key"], "selected-search")
            with patch.dict(os.environ, env, clear=True):
                from pipelines.clients.semantic_scholar_client import SemanticScholarClient
                client = SemanticScholarClient()
                self.assertEqual(client.api_key, "selected-search")
                self.assertEqual(client.BASE_URL, "https://api.semanticscholar.org/graph/v1")

    def test_preserved_fulltext_and_shortlist_semantics(self):
        settings = default_pipeline_settings(self.config())
        self.assertEqual(settings["retrieval_eval_fulltext_limit"], 5)
        self.assertEqual(settings["metadata_shortlist_size"] + settings["metadata_boundary_buffer"], 60)
        self.assertEqual(settings["retrieval_eval_pdf_max_snippets"], 6)
        self.assertEqual(settings["retrieval_eval_pdf_snippet_chars"], 1800)
        self.assertFalse(settings["enable_crossref_references"])
        self.assertFalse(settings["enable_openalex"])
        self.assertEqual(settings["citation_neighbor_filter"], "llm")
        with self.assertRaises(ValueError):
            RunConfig(self.config().profiles, pipeline={"retrieval_eval_fulltext_limit": 6})

    def test_config_validation_imports_no_llm_clients_and_real_aliases_are_observed(self):
        code = """
import sys,os,socket,types
os.environ['LITELLM_LOCAL_MODEL_COST_MAP']='True'
def network_forbidden(*args,**kwargs):
    raise AssertionError('No network is allowed in this offline contract test')
socket.socket.connect=network_forbidden
from paperarena import APIProfile,RunConfig
config=RunConfig({'one':APIProfile('openai','model','offline-key')})
assert 'smolagents' not in sys.modules
assert 'pipelines.tasks.prior_art_check' not in sys.modules
from paperarena._worker import install_observers
Model,main,snapshot=install_observers(config,lambda event:None)
import pipelines.tasks.prior_art_scoring as scoring
import pipelines.tasks.prior_art_retrieval as retrieval
import pipelines.tasks.prior_art_fulltext as fulltext
import pipelines.tasks.prior_art_expert_ranking as expert
assert all(module.LiteLLMModel is Model for module in (main,scoring,retrieval,expert))
# Fulltext constructs models with local imports; those see the same patched
# smolagents export once its helper is invoked.
import smolagents
assert smolagents.LiteLLMModel is Model
import litellm
from pipelines.llm_config import get_litellm_config
calls=[]
class FakeResponse:
    choices=[types.SimpleNamespace(message=types.SimpleNamespace(role='assistant',content='{}',tool_calls=None))]
    usage=types.SimpleNamespace(prompt_tokens=3,completion_tokens=2)
    def model_dump(self):
        return {'usage':{'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}}
def fake_completion(**kwargs):
    calls.append(kwargs)
    return FakeResponse()
litellm.completion=fake_completion
for provider,base,key in [('openai',None,'key-one'),('gemini',None,'key-two'),('custom','http://127.0.0.1:9999/v1','key-three')]:
    from paperarena.config import worker_environment
    routed=RunConfig({'one':APIProfile(provider,'test-model',key,base)})
    original_env=dict(os.environ)
    os.environ.clear();os.environ.update(worker_environment(routed))
    model_id,api_base,api_key=get_litellm_config('heavy')
    adapter=Model(model_id=model_id,api_base=api_base,api_key=api_key)
    adapter.generate([{'role':'user','content':'Offline synthetic input'}])
    assert calls[-1]['api_key']==key
    assert calls[-1]['num_retries']==0 and calls[-1]['max_retries']==0
    assert calls[-1]['model']==('gemini/test-model' if provider=='gemini' else 'openai/test-model')
    os.environ.clear();os.environ.update(original_env)
assert snapshot()['tokens']['total_tokens']==15
print('OK')
"""
        result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        # New PyMuPDF releases may emit a deprecation notice for legacy fitz imports.
        self.assertEqual(result.stdout.strip().splitlines()[-1], "OK")


class LocalApiTests(unittest.TestCase):
    def config(self):
        return RunConfig({"one": APIProfile("openai", "model", "not-publishable")})

    def test_dry_run_never_launches_worker_or_network(self):
        with temporary_directory() as temporary, patch("paperarena.api.subprocess.Popen") as launch:
            snapshots = []
            result = run_retrieval("An offline test query", self.config(), temporary, dry_run=True, progress_callback=snapshots.append)
            launch.assert_not_called()
            self.assertEqual(result["status"], "NOT_RUN")
            self.assertEqual(result["network_execution"], "NOT_RUN")
            self.assertNotIn("not-publishable", json.dumps(result))
            self.assertTrue((Path(result["output_dir"]) / "plan.json").is_file())
            self.assertNotIn("not-publishable", "".join(path.read_text(encoding="utf-8") for path in Path(temporary).rglob("*.json")))
            self.assertGreaterEqual(len(snapshots), 2)

    def test_dry_run_allows_env_keys_not_present(self):
        with temporary_directory() as temporary:
            config = RunConfig({"one": APIProfile("gemini", "chosen-model", api_key_env="MISSING_TEST_KEY")})
            result = run_retrieval("Offline", config, temporary, dry_run=True)
            self.assertEqual(result["status"], "NOT_RUN")

    def test_manager_offline_job_and_unknown_id(self):
        with temporary_directory() as temporary:
            manager = JobManager(temporary)
            try:
                job_id = manager.submit("Offline", self.config(), dry_run=True)
                deadline = time.monotonic() + 5
                while manager.get(job_id)["status"] == "QUEUED" and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(manager.get(job_id)["status"], "NOT_RUN")
                self.assertFalse(manager.cancel(job_id))
                with self.assertRaises(KeyError):
                    manager.get("missing")
            finally:
                manager.shutdown()

    def test_already_cancelled_run_never_launches(self):
        event = threading.Event()
        event.set()
        with temporary_directory() as temporary, patch("paperarena.api.subprocess.Popen") as launch:
            result = run_retrieval("Test", self.config(), temporary, cancel_event=event)
            launch.assert_not_called()
            self.assertEqual(result["status"], "CANCELLED")

    def test_run_timeout_and_secrets_stdin_not_command_or_environment(self):
        launched = []
        real_popen = subprocess.Popen
        def launch(*args, **kwargs):
            if args[0][0] == "taskkill":
                return real_popen(*args, **kwargs)
            launched.append((args[0], kwargs["env"]))
            return real_popen([sys.executable, "-c", "import sys,time;sys.stdin.read();time.sleep(10)"], **kwargs)
        with temporary_directory() as temporary, patch("paperarena.api.subprocess.Popen", side_effect=launch):
            result = run_retrieval("Timeout offline helper", self.config(), temporary, timeout_seconds=0.4)
            self.assertEqual(result["status"], "TIMEOUT")
            self.assertLess(result["duration_seconds"], 8)
            self.assertFalse("not-publishable" in json.dumps(launched))
            self.assertNotIn("not-publishable", json.dumps(result))

    def test_duration_heartbeat_without_worker_events(self):
        real_popen = subprocess.Popen
        def launch(*args, **kwargs):
            return real_popen([sys.executable, "-c", "import sys,time,json;sys.stdin.read();time.sleep(2.2);print(json.dumps({'status':'SUCCEEDED','phase':'finished'}))"], **kwargs)
        with temporary_directory() as temporary, patch("paperarena.api.subprocess.Popen", side_effect=launch):
            snapshots = []
            result = run_retrieval("Offline helper timing", self.config(), temporary, timeout_seconds=10, progress_callback=snapshots.append)
            running = [row for row in snapshots if row["status"] == "RUNNING"]
            self.assertEqual(result["status"], "SUCCEEDED")
            self.assertGreaterEqual(len(running), 2)
            self.assertGreaterEqual(max(row["duration_seconds"] for row in running), 1.0)
            self.assertIsNone(result["usage"])

    def test_running_job_cancellation(self):
        real_popen = subprocess.Popen
        launched = threading.Event()
        def launch(*args, **kwargs):
            if args[0][0] == "taskkill":
                return real_popen(*args, **kwargs)
            process = real_popen([sys.executable, "-c", "import sys,time;sys.stdin.read();time.sleep(10)"], **kwargs)
            launched.set()
            return process
        with temporary_directory() as temporary, patch("paperarena.api.subprocess.Popen", side_effect=launch):
            manager = JobManager(temporary)
            try:
                job_id = manager.submit("Cancel helper", self.config())
                self.assertTrue(launched.wait(timeout=3))
                self.assertTrue(manager.cancel(job_id))
                deadline = time.monotonic() + 8
                while manager.get(job_id)["status"] != "CANCELLED" and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertEqual(manager.get(job_id)["status"], "CANCELLED")
            finally:
                manager.shutdown()

    def test_completed_benchmark_survives_server_restart_without_gold_or_keys(self):
        with temporary_directory() as temporary:
            manager = JobManager(temporary)
            job_id = manager.submit("Blind idea", self.config(), dry_run=True)
            manager.register_benchmark(job_id, "easy", "N001")
            manager.shutdown(cancel=False)
            state = manager.get(job_id)
            marker = json.loads((Path(state["output_dir"]) / "web_job.json").read_text())
            self.assertEqual(marker, {"job_id": job_id, "benchmark": ["easy", "N001"]})
            restored = JobManager(temporary)
            try:
                self.assertEqual(restored.benchmark_selections[job_id], ("easy", "N001"))
                self.assertEqual(restored.get(job_id)["status"], "NOT_RUN")
            finally:
                restored.shutdown()

    def test_blind_dataset_reader_does_not_touch_gold(self):
        with temporary_directory() as temporary:
            directory = Path(temporary) / "medium"
            directory.mkdir()
            (directory / "prompts").mkdir()
            prompt = "Research idea:\nBlind query"
            (directory / "prompts" / "C001.txt").write_bytes(prompt.encode("utf-8"))
            row = {"item_id": "C001", "query": "Blind query", "prompt_file": "prompts/C001.txt", "query_sha256": hashlib.sha256(b"Blind query").hexdigest(), "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest()}
            (directory / "queries.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
            (directory / "gold.jsonl").write_text("invalid-json-intentionally", encoding="utf-8")
            self.assertEqual(dataset_query("MDEIUM", "c001", temporary)["query"], "Blind query")
            self.assertEqual(dataset_query("MDEIUM", "c001", temporary)["prompt"], prompt)

    def test_cli_dry_run_roundtrip(self):
        with temporary_directory() as temporary:
            config_path = Path(temporary) / "config.json"
            config_path.write_text(json.dumps({"profiles": {"one": {"provider": "openai", "model": "chosen-model", "api_key_env": "MISSING_TEST_KEY"}}}), encoding="utf-8")
            output = io.StringIO()
            with patch("sys.stdout", output):
                code = cli_main(["run", "--query", "Blind test", "--config", str(config_path), "--output-dir", str(Path(temporary) / "outputs"), "--dry-run"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "NOT_RUN")


class UsageTests(unittest.TestCase):
    def test_reported_usage_and_unknown_are_distinct(self):
        response = types.SimpleNamespace(raw={"usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})
        self.assertEqual(_token_counts(response), {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        self.assertEqual(_token_counts(types.SimpleNamespace()), dict.fromkeys(("prompt_tokens", "completion_tokens", "total_tokens")))

    def test_adapter_retry_disabled_and_usage_observed_with_fake_clients(self):
        captures = []
        class FakeModel:
            def __init__(self, *args, **kwargs):
                self.kwargs = dict(kwargs)
                captures.append(kwargs)
            def generate(self, messages, *args, **kwargs):
                captures.append(kwargs)
                return types.SimpleNamespace(raw={"usage": {"prompt_tokens": 20, "completion_tokens": 4, "total_tokens": 24}})
        fake_smolagents = types.SimpleNamespace(LiteLLMModel=FakeModel)
        fake_litellm = types.SimpleNamespace()
        import pipelines.tasks
        aliases = ("seed_papers_from_llm", "seed_papers_from_serpapi", "validate_llm_seeds_via_openalex", "generate_search_queries_from_idea", "run_exploration_loop", "decompose_idea_into_facets", "extract_idea_features", "extract_high_level_facets_from_idea", "generate_seed_queries_llm", "retrieve_candidates_with_buckets", "refine_queries_llm", "generate_relevance_keywords_from_idea", "generate_facet_patterns_from_idea", "score_papers_with_abstract_only", "expand_corpus_via_citation_graph", "prepare_fixed_fulltext_for_eval", "rerank_shortlist_with_expert")
        fake_main = types.ModuleType("pipelines.tasks.prior_art_check")
        for alias in aliases:
            setattr(fake_main, alias, lambda: None)
        events = []
        config = RunConfig({"one": APIProfile("openai", "model", "secret")})
        with patch.dict(sys.modules, {"smolagents": fake_smolagents, "litellm": fake_litellm, "pipelines.tasks.prior_art_check": fake_main}), patch.object(pipelines.tasks, "prior_art_check", fake_main, create=True):
            Model, main, snapshot = install_observers(config, events.append)
            model = Model(model_id="test")
            model.generate([{"role": "user", "content": "not persisted in events"}])
            model._paperarena_set_output_budget(role="expert", max_tokens=12000)
            model.generate([])
            self.assertFalse(captures[0]["retry"])
            self.assertTrue(all(row["num_retries"] == 0 and row["max_retries"] == 0 for row in captures))
            self.assertEqual(captures[-1]["max_tokens"], 12000)
            self.assertEqual(snapshot()["model_attempts"], 2)
            self.assertEqual(snapshot()["tokens"]["total_tokens"], 48)
            self.assertNotIn("not persisted", json.dumps(events))


if __name__ == "__main__":
    unittest.main()
