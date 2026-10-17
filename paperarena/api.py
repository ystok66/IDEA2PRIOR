"""Process-isolated retrieval API shared by Python, notebook, CLI, and web."""
from __future__ import annotations

import copy
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import threading
import time
from typing import Callable
import uuid

from .config import APIProfile, RunConfig, worker_environment
from .diagnostics import safe_error


def _write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    # Windows readers / antivirus can briefly hold a destination handle.
    # Retry only this local atomic write, never the research task or API call.
    for attempt in range(10):
        try:
            os.replace(temporary, path)
            break
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(min(0.02 * 2**attempt, 0.2))


def _stop_process(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
                creationflags=subprocess.CREATE_NO_WINDOW, timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        process.wait(timeout=5)


def _resolve_for_operation(config, extension, dry_run):
    if extension is None:
        return config.resolved(require_key=not dry_run)
    from .extensions import extension_model_roles
    resolved = config.resolved(require_key=False)
    if not dry_run:
        if extension["name"] == "document_search":
            if resolved.embedding is None:
                raise ValueError("Document search requires an explicit embedding profile")
            resolved.embedding.resolved(require_key=True)
        for role in extension_model_roles(extension):
            resolved.profiles[resolved.roles[role]].resolved(require_key=True)
    return resolved


def run_retrieval(query: str, config: RunConfig | dict, output_dir: str | Path | None = None,
                  timeout_seconds: float = 1800, *, topic: str | None = None,
                  progress_callback: Callable[[dict], None] | None = None,
                  cancel_event: threading.Event | None = None, dry_run: bool = False,
                  _extension: dict | None = None) -> dict:
    """Run once. Credentials travel through stdin; returned snapshots never contain them.

    Every invocation creates a fresh child directory under output_dir. A dry run
    validates configuration and emits a plan without importing the pipeline or
    making any network requests. No item retries or provider fallbacks occur.
    """
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a nonempty string")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive and finite")
    config = config if isinstance(config, RunConfig) else RunConfig.from_dict(config)
    if _extension is not None:
        _extension = copy.deepcopy(_extension)
        path = _extension["parameters"].get("document_path")
        if path:
            # Resolve in the caller's directory before the isolated worker starts.
            _extension["parameters"]["document_path"] = str(Path(path).expanduser().resolve())
    config = _resolve_for_operation(config, _extension, dry_run)
    job_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:12]
    directory = Path(output_dir or "outputs").resolve() / job_id
    directory.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    state = {"job_id": job_id, "status": "NOT_RUN" if dry_run else "STARTING",
             "output_dir": str(directory), "run_dir": None, "duration_seconds": 0.0,
             "phase": "configuration", "top5": [], "usage": None, "error": None,
             "config": config.to_public_dict(), "scientific_validation": "NOT_ADJUDICATED",
             "operation": "extension" if _extension else "retrieval", "extension_result": None}
    state["extension_name"] = _extension["name"] if _extension else None
    cancel_event = cancel_event or threading.Event()

    def update(event=None):
        if event:
            state.update(event)
        state["duration_seconds"] = round(time.monotonic() - started, 3)
        if event:
            record = {key: value for key, value in event.items()
                      if key in ("status", "phase", "usage", "error", "diagnostics", "report_files")}
            record["elapsed_seconds"] = state["duration_seconds"]
            with (directory / "events.jsonl").open("a", encoding="utf-8") as log:
                log.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
        _write_json(directory / "result.json", state)
        if progress_callback:
            progress_callback(copy.deepcopy(state))

    update()
    _write_json(directory / "plan.json", {"query": query, "topic": topic or query.split(". ", 1)[0].rstrip("."),
        "config": config.to_public_dict(), "timeout_seconds": timeout_seconds,
        "query_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
        "config_sha256": hashlib.sha256(json.dumps(config.to_public_dict(), sort_keys=True).encode("utf-8")).hexdigest(),
        "network_execution": "NOT_RUN" if dry_run else "AUTHORIZED_BY_CALL", "retry_policy": "none",
        "benchmark_gold_access": False, "extension": _extension})
    if cancel_event.is_set():
        update({"status": "CANCELLED", "phase": "cancelled"})
        return copy.deepcopy(state)
    if dry_run:
        update({"phase": "validated", "network_execution": "NOT_RUN"})
        return copy.deepcopy(state)

    # The child starts with scrubbed credentials. Its stdin payload restores
    # only this run's explicitly routed profiles, avoiding process env races.
    env = worker_environment(config)
    for key in list(env):
        if key.upper().endswith("_API_KEY"):
            env.pop(key)
    source_root = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = source_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    payload = {"query": query, "topic": topic or query.split(". ", 1)[0].rstrip("."),
               "config": config.worker_dict(), "output_dir": str(directory), "extension": _extension}
    command = [sys.executable, "-X", "utf8", "-m", "paperarena._worker"]
    messages: queue.Queue = queue.Queue()
    process = None
    last_progress = started
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", cwd=directory, env=env,
            creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW) if os.name == "nt" else 0,
            start_new_session=os.name != "nt")
        process.stdin.write(json.dumps(payload, ensure_ascii=False))
        process.stdin.close()
        payload = None

        def read_events():
            for line in process.stdout:
                try:
                    value = json.loads(line)
                    if isinstance(value, dict):
                        messages.put(value)
                except (ValueError, TypeError):
                    pass  # Foreign console output is never persisted.

        reader = threading.Thread(target=read_events, daemon=True)
        reader.start()
        update({"status": "RUNNING", "phase": "starting_pipeline"})
        while True:
            if cancel_event.is_set():
                _stop_process(process)
                update({"status": "CANCELLED", "phase": "cancelled"})
                break
            if time.monotonic() - started >= timeout_seconds:
                _stop_process(process)
                update({"status": "TIMEOUT", "phase": "deadline", "error": {"type": "DeadlineExceeded", "message": "The configured run deadline was reached; no retry was started."}})
                break
            try:
                event = messages.get(timeout=0.2)
                update(event)
                last_progress = time.monotonic()
            except queue.Empty:
                if time.monotonic() - last_progress >= 1:
                    update()
                    last_progress = time.monotonic()
            if process.poll() is not None:
                reader.join(timeout=2)
                while not messages.empty():
                    update(messages.get_nowait())
                if state["status"] not in ("SUCCEEDED", "PARTIAL", "FAILED", "EMPTY"):
                    update({"status": "FAILED", "phase": "worker_exit", "error": {"type": "WorkerExited", "message": "Worker exited before producing a final result."}})
                break
        process.wait(timeout=5)
    except BaseException as error:
        if process is not None:
            _stop_process(process)
        update({"status": "FAILED", "phase": "runner_error", "error": safe_error(error, state.get("phase"))})
        error._paperarena_output_dir = str(directory)
        error._paperarena_phase = state["error"].get("phase", "runner_error")
        raise
    finally:
        if process is not None and process.stdout is not None:
            process.stdout.close()
    update()
    return copy.deepcopy(state)


def run_extension(name: str, config: RunConfig | dict, parameters: dict | None = None,
                  output_dir: str | Path | None = None, timeout_seconds: float = 1800, **kwargs) -> dict:
    """Start one optional task, using the same isolated worker and diagnostics."""
    from .extensions import validate_extension
    request = validate_extension(name, parameters)
    query = request["parameters"].get("query") or request["parameters"].get("topic") or name
    return run_retrieval(query, config, output_dir, timeout_seconds, _extension=request, **kwargs)


class JobManager:
    """Small local job queue. Default: one active job, cancellable, no retries."""
    def __init__(self, output_root: str | Path = "outputs", *, max_active_jobs: int = 1):
        self.output_root = Path(output_root).resolve()
        self._executor = ThreadPoolExecutor(max_workers=max_active_jobs, thread_name_prefix="paperarena-job")
        self._jobs: dict[str, dict] = {}
        self._lock = threading.RLock()
        self.benchmark_selections: dict[str, tuple[str, str]] = {}
        # Restore saved jobs for inspection only; interrupted work is never resumed.
        for marker in self.output_root.glob("*/web_job.json"):
            try:
                metadata = json.loads(marker.read_text(encoding="utf-8"))
                job_id = metadata["job_id"]
                if len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
                    continue
                state = json.loads((marker.parent / "result.json").read_text(encoding="utf-8"))
                state["job_id"] = job_id
                if state["status"] in ("QUEUED", "STARTING", "RUNNING"):
                    state.update(status="FAILED", phase="server_restart",
                        error={"type": "InterruptedRun", "code": "INTERRUPTED_RUN",
                               "message": "The previous local process stopped. Saved evidence is retained; no run was resumed."})
                self._jobs[job_id] = {"state": state, "cancel": threading.Event()}
                selection = metadata.get("benchmark")
                if (isinstance(selection, list) and len(selection) == 2
                        and selection[0] in ("easy", "medium", "hard")
                        and isinstance(selection[1], str)):
                    self.benchmark_selections[job_id] = tuple(selection)
            except (OSError, ValueError, KeyError, TypeError):
                continue

    def submit(self, query: str, config: RunConfig | dict, *, timeout_seconds: float = 1800,
               topic: str | None = None, dry_run: bool = False, extension: dict | None = None) -> str:
        from .extensions import validate_extension
        if extension is not None:
            extension = validate_extension(extension["name"], extension.get("parameters"))
        config = config if isinstance(config, RunConfig) else RunConfig.from_dict(config)
        # Fail missing credentials synchronously, before putting a job in queue.
        config = _resolve_for_operation(config, extension, dry_run)
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a nonempty string")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive and finite")
        job_id = uuid.uuid4().hex
        cancellation = threading.Event()
        with self._lock:
            self._jobs[job_id] = {"state": {"job_id": job_id, "status": "QUEUED", "phase": "queued", "duration_seconds": 0.0, "top5": [], "usage": None, "config": config.to_public_dict(), "operation": "extension" if extension else "retrieval", "extension_name": extension["name"] if extension else None}, "cancel": cancellation}

        def progress(state):
            state["job_id"] = job_id
            with self._lock:
                self._jobs[job_id]["state"] = state
            if state.get("output_dir"):
                marker = Path(state["output_dir"]) / "web_job.json"
                if not marker.exists():
                    with self._lock:
                        _write_json(marker, {"job_id": job_id,
                            "benchmark": self.benchmark_selections.get(job_id)})

        def run():
            try:
                options = {"_extension": extension} if extension else {}
                run_retrieval(query, config, self.output_root, timeout_seconds, topic=topic,
                              progress_callback=progress, cancel_event=cancellation, dry_run=dry_run, **options)
            except Exception as error:
                with self._lock:
                    self._jobs[job_id]["state"].update(status="FAILED", error=safe_error(error,
                        self._jobs[job_id]["state"].get("phase")))

        self._executor.submit(run)
        return job_id

    def get(self, job_id: str) -> dict:
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError("Unknown job ID")
            state = copy.deepcopy(self._jobs[job_id]["state"])
        return state

    def register_benchmark(self, job_id: str, tier: str, item: str):
        """Persist blind item identity for post-run evaluation, never its gold data."""
        if tier not in ("easy", "medium", "hard") or not isinstance(item, str):
            raise ValueError("Invalid benchmark identity")
        with self._lock:
            state = self._jobs[job_id]["state"]
            self.benchmark_selections[job_id] = (tier, item)
            if state.get("output_dir"):
                _write_json(Path(state["output_dir"]) / "web_job.json",
                            {"job_id": job_id, "benchmark": [tier, item]})

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError("Unknown job ID")
            job = self._jobs[job_id]
            if job["state"]["status"] in ("SUCCEEDED", "PARTIAL", "FAILED", "EMPTY", "CANCELLED", "TIMEOUT", "NOT_RUN"):
                return False
            job["cancel"].set()
            if job["state"]["status"] == "QUEUED":
                job["state"].update(status="CANCELLED", phase="cancelled_before_start")
            return True

    def shutdown(self, *, cancel: bool = True):
        if cancel:
            with self._lock:
                for job in self._jobs.values():
                    job["cancel"].set()
        self._executor.shutdown(wait=True)
