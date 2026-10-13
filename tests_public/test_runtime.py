"""Offline request/ledger contracts. No SDK, network, or live LLM calls."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pipelines.rate_limiter import SimpleRateLimiter
from pipelines.runtime import (
    AttemptLimitError, LLMRuntime, RequestScheduler, RunLedger, TaskStateError,
    append_jsonl, atomic_write_json, atomic_write_jsonl, get_runtime,
    invoke_model, json_value, redact, use_runtime,
)


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []
    def __call__(self):
        return self.now
    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        local_tmp = Path(__file__).resolve().parent.parent / "audit" / "implementation" / "tmp"
        local_tmp.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=local_tmp)
        self.root = Path(self.temporary.name)
        self.ledger = RunLedger(self.root, config={"api_key": "test-key-example-test-secret-key", "idea": "研究" * 800})
        self.runtime = LLMRuntime(self.ledger)
    def tearDown(self):
        self.temporary.cleanup()
    def task(self, **kwargs):
        return self.ledger.plan_task(stage="test", task_id="paper-1", trial_id="1", purpose="judge", request={"messages": ["hello"]}, **kwargs)

    def test_atomic_json_preserves_old_file_when_replace_fails(self):
        path = self.root / "artifact.json"
        atomic_write_json(path, {"old": 1})
        with patch("pipelines.runtime.os.replace", side_effect=OSError("injected")):
            with self.assertRaises(OSError):
                atomic_write_json(path, {"new": 2})
        self.assertEqual(json.loads(path.read_text()), {"old": 1})
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_jsonl_atomic_concurrent_records_complete(self):
        path = self.root / "events.jsonl"
        atomic_write_jsonl(path, [{"initial": True}])
        with ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(lambda n: append_jsonl(path, {"n": n}), range(30)))
        records = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(len(records), 31)
        self.assertEqual(sorted(row["n"] for row in records[1:]), list(range(30)))

    def test_legal_json_and_secrets(self):
        @dataclass
        class Config:
            count: int = 3
        path = self.root / "legal.json"
        atomic_write_json(path, {"nan": float("nan"), "config": Config(), "bytes": b"abc", "api_key": "secret", "url": "https://u:password@service.invalid/api?api_key=secret&model=a"})
        raw = path.read_text()
        self.assertNotIn(":NaN", raw)
        self.assertNotIn("password", raw)
        self.assertNotIn('"secret"', raw)
        self.assertEqual(json.loads(raw)["config"], {"count": 3})

    def test_response_saved_before_downstream_parse_failure(self):
        original = SimpleNamespace(content="not valid JSON", model="fake", id="response-1", usage=None)
        response = self.runtime.call_model(lambda messages: original, ["x"], stage="s", task_id="p")
        self.assertIs(response, original)
        with self.assertRaises(json.JSONDecodeError):
            json.loads(response.content)
        saved = list(self.ledger.root.glob("*/response_*.json"))
        self.assertEqual(len(saved), 1)
        self.assertEqual(json.loads(saved[0].read_text())["response"]["content"], "not valid JSON")
        self.assertEqual(self.ledger.task_statuses()[0]["status"], "SUCCEEDED")

    def test_failure_is_recorded_without_automatic_retry(self):
        calls = []
        def fail():
            calls.append(1)
            raise RuntimeError("failed test-key-example-test-secret-key")
        with self.assertRaises(RuntimeError):
            self.runtime.call(fail, stage="s", task_id="p", request={"messages": ["x"]})
        self.assertEqual(calls, [1])
        self.assertEqual(self.runtime.scheduler.attempts, 1)
        self.assertEqual(self.ledger.task_statuses()[0]["status"], "FAILED")
        self.assertNotIn("test-key-example-test-secret-key", (self.ledger.root / "events.jsonl").read_text())

    def test_explicit_resume_does_not_call_model_again(self):
        calls = []
        def model(messages):
            calls.append(messages)
            return SimpleNamespace(content="answer", usage=None)
        self.runtime.call_model(model, ["x"], stage="s", task_id="p", validate_response=lambda response: response.content == "answer")
        recovered = LLMRuntime(RunLedger(self.root))
        output = recovered.call_model(model, ["x"], stage="s", task_id="p", resume=True)
        self.assertEqual(output.content, "answer")
        self.assertEqual(len(calls), 1)
        self.assertEqual(recovered.scheduler.attempts, 0)

    def test_stage_trial_purpose_and_repeated_input_are_independent(self):
        for stage, trial, purpose in [("s", "1", "inference"), ("s", "2", "inference"), ("s2", "1", "inference"), ("s", "1", "judge")]:
            self.runtime.call(lambda: "ok", stage=stage, task_id="p", trial_id=trial, purpose=purpose, request={"same": True}, resume=True)
        self.assertEqual(self.runtime.scheduler.attempts, 4)
        self.runtime.call(lambda: "ok", stage="s", request={"same": True})
        self.runtime.call(lambda: "ok", stage="s", request={"same": True})
        self.assertEqual(len(self.ledger.task_statuses()), 6)

    def test_resume_changed_request_rejected(self):
        self.runtime.call(lambda: "ok", stage="s", task_id="p", request={"x": 1})
        with self.assertRaises(TaskStateError):
            self.runtime.call(lambda: "bad", stage="s", task_id="p", request={"x": 2}, resume=True)
        with self.assertRaises(ValueError):
            self.runtime.call(lambda: "bad", stage="s", request={}, resume=True)

    def test_late_attempt_cannot_overwrite_accepted_pointer(self):
        key = self.task()
        old = self.ledger.begin_attempt(key)
        new = self.ledger.begin_attempt(key, supersede=True)
        self.assertTrue(self.ledger.record_response(new, {"content": "new"}))
        self.ledger.validate_attempt(new, True)
        self.assertFalse(self.ledger.record_response(old, {"content": "old"}))
        self.ledger.record_failure(old, RuntimeError("late timeout"))
        self.assertEqual(self.ledger.accepted_response(key)["response"]["content"], "new")
        self.assertEqual(len(list(self.ledger.root.glob("*/response_*.json"))), 2)

    def test_corrupt_response_and_duplicate_attempt_rejected(self):
        key = self.task()
        attempt = self.ledger.begin_attempt(key)
        self.ledger.record_response(attempt, {"x": 1})
        self.ledger.validate_attempt(attempt, True)
        with self.assertRaises(TaskStateError):
            self.ledger.record_response(attempt, {"x": 2})
        with self.assertRaises(TaskStateError):
            self.ledger.begin_attempt(key)
        next(self.ledger.root.glob("*/response_*.json")).write_text("{}", encoding="utf-8")
        with self.assertRaises(TaskStateError):
            self.ledger.accepted_response(key)

    def test_context_propagates_only_when_explicitly_copied(self):
        model = lambda messages: SimpleNamespace(content=messages[0])
        with use_runtime(self.runtime):
            context = copy_context()
            with ThreadPoolExecutor(max_workers=1) as pool:
                response = pool.submit(context.run, invoke_model, model, ["x"], stage="thread", task_id="p").result()
            self.assertEqual(response.content, "x")
            self.assertIs(get_runtime(), self.runtime)
        self.assertIsNone(get_runtime())
        self.assertEqual(len(self.ledger.task_statuses()), 1)
        self.assertEqual(invoke_model(model, ["direct"], stage="no-runtime").content, "direct")

    def test_required_artifact_missing_prevents_completion(self):
        output = self.root / "synthesis.json"
        atomic_write_json(output, {})
        result = self.ledger.finalize([("synthesis", output), ("evidence", self.root / "missing.json")])
        self.assertEqual(result["status"], "INCOMPLETE")
        key = self.task()
        result = self.ledger.finalize([("synthesis", output)])
        self.assertEqual(result["task_statuses"]["PLANNED"], 1)
        self.assertEqual(result["status"], "INCOMPLETE")

    def test_full_config_not_truncated(self):
        config = json.loads((self.root / "run_manifest.json").read_text(encoding="utf-8"))["config"]
        self.assertEqual(len(config["idea"]), 1600)
        self.assertEqual(config["api_key"], "[REDACTED]")

    def test_response_model_key_redacted_everywhere(self):
        class Model:
            api_key = "test-key-example-unique-model-secret"
            def __call__(self, messages):
                return SimpleNamespace(content="echo test-key-example-unique-model-secret")
        self.runtime.call_model(Model(), ["hello test-key-example-unique-model-secret"], stage="s", task_id="secret")
        for file in self.root.rglob("*.json*"):
            self.assertNotIn("test-key-example-unique-model-secret", file.read_text(encoding="utf-8"))

    def test_raw_unvalidated_or_invalid_response_cannot_resume(self):
        model = lambda messages: SimpleNamespace(content="not JSON")
        self.runtime.call_model(model, ["x"], stage="s", task_id="raw")
        with self.assertRaises(TaskStateError):
            self.runtime.call_model(model, ["x"], stage="s", task_id="raw", resume=True)
        with self.assertRaises(json.JSONDecodeError):
            self.runtime.call_model(model, ["x"], stage="s", task_id="bad", validate_response=lambda response: json.loads(response.content))
        invalid = next(task for task in self.ledger.task_statuses() if task["logical_task_id"] == "bad")
        self.assertFalse(invalid["cache_eligible"])
        self.assertEqual(invalid["validation"], "INVALID")
        self.assertEqual(len(list(self.ledger.root.glob("*/response_*.json"))), 2)

    def test_late_validation_does_not_promote_new_response(self):
        key = self.task()
        old = self.ledger.begin_attempt(key)
        new = self.ledger.begin_attempt(key, supersede=True)
        self.ledger.record_response(new, "new")
        self.ledger.record_response(old, "old")
        self.assertFalse(self.ledger.validate_attempt(old, True))
        with self.assertRaises(TaskStateError):
            self.ledger.accepted_response(key)

    def test_run_manager_unique_ids_and_explicit_resume_metadata(self):
        from pipelines.run_manager import RunManager
        first = RunManager("fixture", timestamp="20010101_010101", base_dir=self.root)
        second = RunManager("fixture", timestamp="20010101_010101", base_dir=self.root)
        self.assertNotEqual(first.run_id, second.run_id)
        first.save_config({"idea_description": "x" * 1600, "api_key": "test-key-example-test-secret-key"})
        second.create_run_directory()
        recovered = RunManager("fixture", resume_run_dir=first.run_dir)
        self.assertEqual(recovered.timestamp, first.timestamp)
        self.assertEqual(recovered.base_dir, self.root)
        recovered.save_config({"idea_description": "x" * 1600, "api_key": "test-key-example-test-secret-key"})
        with self.assertRaises(ValueError):
            recovered.save_config({"idea_description": "changed"})
        with self.assertRaises(ValueError):
            RunManager("different topic", resume_run_dir=first.run_dir)


class SchedulerTests(unittest.TestCase):
    def test_rpm_tpm_and_clock_use_are_explicit(self):
        clock = Clock()
        scheduler = RequestScheduler(rpm=2, tpm=10, clock=clock, sleep=clock.sleep)
        with scheduler.slot(estimated_tokens=6):
            pass
        with scheduler.slot(estimated_tokens=6) as result:
            self.assertEqual(result["queue_seconds"], 60)
        self.assertEqual(scheduler.attempts, 2)
        with self.assertRaises(ValueError):
            with scheduler.slot():
                pass
        with self.assertRaises(ValueError):
            with scheduler.slot(estimated_tokens=11):
                pass

    def test_shared_attempt_budget_and_slot_release_on_failure(self):
        scheduler = RequestScheduler(max_in_flight=1, max_pending=0, total_attempts=2)
        with self.assertRaises(RuntimeError):
            with scheduler.slot():
                raise RuntimeError("failed")
        with scheduler.slot():
            pass
        with self.assertRaises(AttemptLimitError):
            with scheduler.slot():
                pass
        self.assertEqual(scheduler.attempts, 2)

    def test_queue_capacity_and_in_flight_bound(self):
        scheduler = RequestScheduler(max_in_flight=1, max_pending=0)
        entered = threading.Event()
        leave = threading.Event()
        def request():
            with scheduler.slot():
                entered.set()
                leave.wait(2)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(request)
            self.assertTrue(entered.wait(2))
            try:
                with self.assertRaises(TaskStateError):
                    with scheduler.slot():
                        self.fail("queue admitted extra task")
            finally:
                leave.set()
            future.result()

    def test_retry_after_without_retry(self):
        clock = Clock()
        scheduler = RequestScheduler(clock=clock, sleep=clock.sleep)
        scheduler.defer(7)
        with scheduler.slot() as timing:
            self.assertEqual(timing["queue_seconds"], 7)
        self.assertEqual(scheduler.attempts, 1)

    def test_legacy_limiter_uses_injected_monotonic_clock(self):
        clock = Clock()
        limiter = SimpleRateLimiter(1, 2.0, clock=clock, sleep=clock.sleep)
        with patch("time.time", side_effect=AssertionError("wall clock must not throttle")):
            limiter.wait_for_slot()
            limiter.wait_for_slot()
        self.assertGreaterEqual(clock.now, 2.0)


if __name__ == "__main__":
    unittest.main()
