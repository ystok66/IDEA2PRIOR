"""Process-isolated retrieval API shared by Python, notebook, CLI, and web."""
from __future__ import annotations

import copy
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
    os.replace(temporary, path)


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


def run_retrieval(query: str, config: RunConfig | dict, output_dir: str | Path | None = None,
                  timeout_seconds: float = 1800, *, topic: str | None = None,
                  progress_callback: Callable[[dict], None] | None = None,
                  cancel_event: threading.Event | None = None, dry_run: bool = False) -> dict:
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
    config = config.resolved(require_key=not dry_run)
    job_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:12]
    directory = Path(output_dir or "outputs").resolve() / job_id
    directory.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    state = {"job_id": job_id, "status": "NOT_RUN" if dry_run else "STARTING",
             "output_dir": str(directory), "run_dir": None, "duration_seconds": 0.0,
             "phase": "configuration", "top5": [], "usage": None, "error": None,
             "config": config.to_public_dict(), "scientific_validation": "NOT_ADJUDICATED"}
    cancel_event = cancel_event or threading.Event()

    def update(event=None):
        if event:
            state.update(event)
        state["duration_seconds"] = round(time.monotonic() - started, 3)
        _write_json(directory / "result.json", state)
        if progress_callback:
            progress_callback(copy.deepcopy(state))

    update()
    _write_json(directory / "plan.json", {"query": query, "topic": topic or query.split(". ", 1)[0].rstrip("."),
        "config": config.to_public_dict(), "timeout_seconds": timeout_seconds,
        "network_execution": "NOT_RUN" if dry_run else "AUTHORIZED_BY_CALL", "retry_policy": "none",
        "benchmark_gold_access": False})
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
               "config": config.worker_dict(), "output_dir": str(directory)}
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


class JobManager:
    """Small local job queue. Default: one active job, cancellable, no retries."""
    def __init__(self, output_root: str | Path = "outputs", *, max_active_jobs: int = 1):
        self.output_root = Path(output_root).resolve()
        self._executor = ThreadPoolExecutor(max_workers=max_active_jobs, thread_name_prefix="paperarena-job")
        self._jobs: dict[str, dict] = {}
        self._lock = threading.RLock()

    def submit(self, query: str, config: RunConfig | dict, *, timeout_seconds: float = 1800,
               topic: str | None = None, dry_run: bool = False) -> str:
        config = config if isinstance(config, RunConfig) else RunConfig.from_dict(config)
        # Fail missing credentials synchronously, before putting a job in queue.
        config = config.resolved(require_key=not dry_run)
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a nonempty string")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive and finite")
        job_id = uuid.uuid4().hex
        cancellation = threading.Event()
        with self._lock:
            self._jobs[job_id] = {"state": {"job_id": job_id, "status": "QUEUED", "phase": "queued", "duration_seconds": 0.0, "top5": [], "usage": None, "config": config.to_public_dict()}, "cancel": cancellation}

        def progress(state):
            state["job_id"] = job_id
            with self._lock:
                self._jobs[job_id]["state"] = state

        def run():
            try:
                run_retrieval(query, config, self.output_root, timeout_seconds, topic=topic,
                              progress_callback=progress, cancel_event=cancellation, dry_run=dry_run)
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
