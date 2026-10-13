"""Local request accounting and explicit same-task recovery.

No model cache, automatic retry, hidden timeout, or provider quota is inferred.
One coordinator owns a run; threads must share its scheduler and copy_context.
SDK-internal retries/transport requests remain unobserved unless an adapter adds
them. A saved response denotes transport success, not valid scientific output.
"""
from __future__ import annotations

import base64
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
from enum import Enum
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from types import SimpleNamespace
from typing import Any, Callable, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import uuid


_SECRET_KEY = re.compile(r"(?:api[_-]?key|authorization|password|passwd|secret|access[_-]?token|refresh[_-]?token|credential|cookie)", re.I)
_PATH_LOCKS: dict[str, threading.RLock] = {}
_PATH_LOCKS_LOCK = threading.Lock()
_CURRENT: ContextVar[Optional["LLMRuntime"]] = ContextVar("paperarena_runtime", default=None)


def _path_lock(path: Path) -> threading.RLock:
    with _PATH_LOCKS_LOCK:
        return _PATH_LOCKS.setdefault(str(path.resolve()), threading.RLock())


def json_value(value: Any) -> Any:
    """Preserve supported values in legal JSON; never persist arbitrary repr()."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else {"__nonfinite__": str(value)}
    if isinstance(value, Enum):
        return json_value(value.value)
    if isinstance(value, (Path, datetime, date)):
        return str(value)
    if isinstance(value, bytes):
        return {"__bytes_base64__": base64.b64encode(value).decode("ascii")}
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted((json_value(v) for v in value), key=lambda x: json.dumps(x, sort_keys=True))
    if is_dataclass(value):
        return json_value(asdict(value))
    if hasattr(value, "model_dump"):
        return json_value(value.model_dump())
    if isinstance(value, SimpleNamespace):
        return json_value(vars(value))
    # smolagents ChatMessage and similar responses. Do not introspect clients.
    attrs = ("content", "role", "tool_calls", "token_usage", "usage", "id", "model", "response_id", "finish_reason", "raw")
    data = {k: json_value(getattr(value, k)) for k in attrs if hasattr(value, k)}
    if data:
        return data
    raise TypeError(f"Unsupported JSON artifact type: {type(value).__name__}")


def _canonical(value: Any) -> bytes:
    return json.dumps(json_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def redact(value: Any, secrets: tuple[str, ...] = ()) -> Any:
    """Redact secret fields, configured secret values, and URL credentials."""
    value = json_value(value)
    if isinstance(value, dict):
        return {k: "[REDACTED]" if _SECRET_KEY.search(k) else redact(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, secrets) for v in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", value)
        # Endpoints may put credentials in userinfo or query fields.
        if value.startswith(("http://", "https://")):
            parts = urlsplit(value)
            netloc = parts.netloc.rsplit("@", 1)[-1]
            query = urlencode([(k, "[REDACTED]" if _SECRET_KEY.search(k) else v) for k, v in parse_qsl(parts.query, keep_blank_values=True)])
            value = urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))
        return value
    return value


def secret_values(value: Any) -> tuple[str, ...]:
    value = json_value(value)
    found: list[str] = []
    def visit(item):
        if isinstance(item, dict):
            for key, val in item.items():
                if _SECRET_KEY.search(key) and isinstance(val, str) and val:
                    found.append(val)
                else:
                    visit(val)
        elif isinstance(item, list):
            for val in item:
                visit(val)
    visit(value)
    return tuple(sorted(set(found), key=len, reverse=True))


def atomic_write_bytes(path: Path, data: bytes) -> Path:
    """Same-directory temporary file + fsync + replace; preserve old on failure."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _path_lock(path):
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return path


def atomic_write_json(path: Path, value: Any) -> Path:
    return atomic_write_bytes(Path(path), _canonical(redact(value)) + b"\n")


def atomic_write_jsonl(path: Path, records) -> Path:
    return atomic_write_bytes(Path(path), b"".join(_canonical(redact(record)) + b"\n" for record in records))


def append_jsonl(path: Path, record: Any) -> Path:
    """Atomically publish the complete ledger, serialized across local threads.

    This trades an O(n) file copy for recoverable JSONL on small run ledgers.
    Large response bodies live in separate immutable files. Not multi-process.
    """
    path = Path(path)
    with _path_lock(path):
        previous = path.read_bytes() if path.exists() else b""
        if previous and not previous.endswith(b"\n"):
            raise ValueError("Refusing to append to an incomplete JSONL record")
        return atomic_write_bytes(path, previous + _canonical(redact(record)) + b"\n")


class AttemptLimitError(RuntimeError):
    pass


class TaskStateError(RuntimeError):
    pass


class RequestScheduler:
    """Bounded in-process admission with optional explicit rolling RPM/TPM.

    TPM requires caller-supplied conservative input+output reservations. It is
    reservation accounting, not a claim of known billable tokens. Queue wait
    never consumes a model execution timeout. Internal SDK retries are unknown.
    """
    def __init__(self, max_in_flight=4, max_pending=16, rpm=None, tpm=None,
                 total_attempts=None, clock=time.monotonic, sleep=time.sleep):
        for name, value in (("max_in_flight", max_in_flight), ("max_pending", max_pending)):
            if isinstance(value, bool) or not isinstance(value, int) or value < (1 if name == "max_in_flight" else 0):
                raise ValueError(f"{name} must be a valid nonnegative integer")
        for name, value in (("rpm", rpm), ("tpm", tpm), ("total_attempts", total_attempts)):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value <= 0):
                raise ValueError(f"{name} must be a positive integer or None")
        self.max_in_flight, self.max_pending = max_in_flight, max_pending
        self.rpm, self.tpm, self.total_attempts = rpm, tpm, total_attempts
        self.clock, self.sleep = clock, sleep
        self._admission = threading.BoundedSemaphore(max_in_flight + max_pending)
        self._active = threading.BoundedSemaphore(max_in_flight)
        self._lock = threading.Lock()
        self._window = deque()
        self.attempts = 0
        self._not_before = 0.0

    def defer(self, seconds: float) -> None:
        """Apply an observed Retry-After delay; does not retry the failed call."""
        if not math.isfinite(seconds) or seconds < 0:
            return
        with self._lock:
            self._not_before = max(self._not_before, self.clock() + seconds)

    @contextmanager
    def slot(self, estimated_tokens=None, *, max_wait_seconds=None, on_wait=None, cancel_event=None):
        if estimated_tokens is not None and (isinstance(estimated_tokens, bool) or not isinstance(estimated_tokens, int) or estimated_tokens < 0):
            raise ValueError("estimated_tokens must be a nonnegative integer")
        if self.tpm is not None and estimated_tokens is None:
            raise ValueError("TPM requires an explicit input+output token reservation")
        if self.tpm is not None and estimated_tokens > self.tpm:
            raise ValueError("One request reservation exceeds the configured TPM")
        started = self.clock()
        # Reject instead of silently dropping or accumulating unlimited waiters.
        if not self._admission.acquire(blocking=False):
            raise TaskStateError("Shared request queue is full; task remains recorded")
        active = False
        try:
            if max_wait_seconds is None and cancel_event is None:
                self._active.acquire()
            else:
                while not self._active.acquire(timeout=0.1):
                    if cancel_event is not None and cancel_event.is_set():
                        raise TaskStateError("Request admission cancelled")
                    if max_wait_seconds is not None and self.clock() - started >= max_wait_seconds:
                        raise TimeoutError("Request admission wait budget exhausted")
            active = True
            while True:
                with self._lock:
                    now = self.clock()
                    while self._window and self._window[0][0] <= now - 60.0:
                        self._window.popleft()
                    if self.total_attempts is not None and self.attempts >= self.total_attempts:
                        raise AttemptLimitError("Shared total attempt budget exhausted")
                    used_tokens = sum(tokens for _, tokens in self._window)
                    limited = (self.rpm is not None and len(self._window) >= self.rpm) or (self.tpm is not None and used_tokens + estimated_tokens > self.tpm)
                    delay = max(0.0, self._not_before - now)
                    if limited:
                        delay = max(delay, self._window[0][0] + 60.0 - now)
                    if delay <= 0:
                        self.attempts += 1
                        attempt_number = self.attempts
                        self._window.append((now, estimated_tokens or 0))
                        break
                if cancel_event is not None and cancel_event.is_set():
                    raise TaskStateError("Request admission cancelled")
                remaining = None if max_wait_seconds is None else max_wait_seconds - (self.clock() - started)
                if remaining is not None and delay > remaining:
                    raise TimeoutError(f"Provider cooldown ({delay:.1f}s) exceeds remaining wait budget ({remaining:.1f}s)")
                if on_wait is not None:
                    on_wait(delay)
                self.sleep(min(delay, 10.0) if on_wait is not None or cancel_event is not None else delay)
            yield {"queue_seconds": self.clock() - started, "scheduler_attempt": attempt_number,
                   "reserved_tokens": estimated_tokens, "sdk_internal_attempts": None}
        finally:
            if active:
                self._active.release()
            self._admission.release()


class RunLedger:
    """Immutable attempts and a generation-checked accepted response pointer."""
    def __init__(self, run_dir: Path, config=None):
        self.run_dir = Path(run_dir)
        self.root = self.run_dir / "request_ledger"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = _path_lock(self.root)
        self.secrets = secret_values(config or {})
        if config is not None:
            self.write_manifest(config)

    def write_manifest(self, config):
        self.secrets = tuple(set(self.secrets + secret_values(config)))
        return atomic_write_json(self.run_dir / "run_manifest.json", {
            "schema_version": 1, "run_id": self.run_dir.name, "config": redact(config, self.secrets),
            "model_cache": "DISABLED", "sdk_internal_attempts": None,
            "scope": "single coordinator process; explicit same-task recovery only"})

    def event(self, kind, **payload):
        append_jsonl(self.root / "events.jsonl", redact({"event": kind, "utc": datetime.now(timezone.utc).isoformat(), **payload}, self.secrets))

    def plan_task(self, *, stage, task_id, trial_id, purpose, request):
        identity = {"stage": str(stage), "logical_task_id": str(task_id), "trial_id": str(trial_id), "purpose": str(purpose)}
        key = fingerprint(identity)
        directory = self.root / key
        request_hash = fingerprint(request)
        with self._lock:
            directory.mkdir(exist_ok=True)
            plan_path = directory / "task.json"
            if plan_path.exists():
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
                if plan["request_hash"] != request_hash:
                    raise TaskStateError("Existing logical task has different request bytes; use a new task/version")
            else:
                plan = {**identity, "task_key": key, "request_hash": request_hash, "status": "PLANNED", "generation": 0}
                atomic_write_json(directory / "request.json", redact(request, self.secrets))
                atomic_write_json(plan_path, plan)
                self.event("PLANNED", **plan)
            return key

    def begin_attempt(self, key, *, supersede=False):
        with self._lock:
            path = self.root / key / "task.json"
            plan = json.loads(path.read_text(encoding="utf-8"))
            if plan["status"] in ("RUNNING", "SUCCEEDED") and not supersede:
                raise TaskStateError("Task already running/completed; explicit recovery or supersession is required")
            generation = plan["generation"] + 1
            attempt = {"task_key": key, "attempt_id": uuid.uuid4().hex, "generation": generation}
            plan.update(status="RUNNING", generation=generation, current_attempt=attempt["attempt_id"])
            atomic_write_json(path, plan)
            self.event("RUNNING", **attempt)
            return attempt

    def record_response(self, attempt, response, **metadata):
        with self._lock:
            directory = self.root / attempt["task_key"]
            path = directory / f"response_{attempt['attempt_id']}.json"
            if path.exists():
                raise TaskStateError("Immutable attempt response already exists")
            payload = {"response": redact(response, self.secrets), "metadata": redact(metadata, self.secrets), **attempt}
            atomic_write_json(path, payload)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self.event("RESPONSE_SAVED", **attempt, response_path=str(path.relative_to(self.run_dir)), sha256=digest)
            plan_path = directory / "task.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            accepted = plan["generation"] == attempt["generation"] and plan.get("current_attempt") == attempt["attempt_id"]
            if accepted:
                plan.update(status="SUCCEEDED", accepted_response=path.name, response_sha256=digest,
                            validation="UNVALIDATED", cache_eligible=False)
                atomic_write_json(plan_path, plan)
            else:
                self.event("LATE_RESPONSE_NOT_ACCEPTED", **attempt)
            return accepted

    def validate_attempt(self, attempt, valid: bool, error=None):
        """Validate the already durable response; late validation cannot promote it."""
        if not isinstance(valid, bool):
            raise TypeError("Validation result must be bool")
        with self._lock:
            path = self.root / attempt["task_key"] / "task.json"
            plan = json.loads(path.read_text(encoding="utf-8"))
            current = (plan["generation"] == attempt["generation"] and
                       plan.get("current_attempt") == attempt["attempt_id"])
            self.event("RESPONSE_VALIDATION", **attempt, valid=valid, error=error, accepted=current)
            if current:
                if not plan.get("accepted_response"):
                    raise TaskStateError("Cannot validate a response before it is durably saved")
                plan.update(validation="VALID" if valid else "INVALID", cache_eligible=valid,
                            validation_error=error)
                atomic_write_json(path, plan)
            return current

    def record_validation(self, *, stage, task_id, valid, attempt_id, trial_id="default", purpose="inference", error=None):
        key = fingerprint({"stage": str(stage), "logical_task_id": str(task_id), "trial_id": str(trial_id), "purpose": str(purpose)})
        with self._lock:
            plan = json.loads((self.root / key / "task.json").read_text(encoding="utf-8"))
            if plan.get("current_attempt") != attempt_id:
                self.event("LATE_VALIDATION_NOT_ACCEPTED", task_key=key, attempt_id=attempt_id)
                return False
            return self.validate_attempt({"task_key": key, "attempt_id": attempt_id, "generation": plan["generation"]}, valid, error)

    def record_failure(self, attempt, error, **metadata):
        with self._lock:
            self.event("ATTEMPT_FAILED", **attempt, error_type=type(error).__name__, error=redact(str(error), self.secrets), **metadata)
            path = self.root / attempt["task_key"] / "task.json"
            plan = json.loads(path.read_text(encoding="utf-8"))
            if plan["generation"] == attempt["generation"] and plan.get("current_attempt") == attempt["attempt_id"]:
                plan.update(status="FAILED", reason=type(error).__name__)
                atomic_write_json(path, plan)

    def accepted_response(self, key):
        with self._lock:
            directory = self.root / key
            plan = json.loads((directory / "task.json").read_text(encoding="utf-8"))
            if plan["status"] != "SUCCEEDED":
                return None
            if not plan.get("cache_eligible", False):
                raise TaskStateError("Saved raw response is not validated; re-parse it offline before enabling same-task recovery")
            path = directory / plan["accepted_response"]
            if hashlib.sha256(path.read_bytes()).hexdigest() != plan["response_sha256"]:
                raise TaskStateError("Saved response fingerprint mismatch")
            self.event("RESUMED_SAME_TASK", task_key=key, response_path=str(path.relative_to(self.run_dir)))
            return json.loads(path.read_text(encoding="utf-8"))

    def task_statuses(self):
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(self.root.glob("*/task.json"))]

    def artifact(self, name, path, *, required=True):
        path = Path(path)
        status = "SUCCEEDED" if path.is_file() else "MISSING"
        entry = {"name": name, "path": str(path), "required": required, "status": status,
                 "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if status == "SUCCEEDED" else None}
        append_jsonl(self.run_dir / "artifact_manifest.jsonl", entry)
        return entry

    def finalize(self, required_artifacts=()):
        artifacts = [self.artifact(str(name), path) for name, path in required_artifacts]
        tasks = self.task_statuses()
        complete = all(a["status"] == "SUCCEEDED" for a in artifacts) and all(t["status"] == "SUCCEEDED" for t in tasks)
        result = {"status": "SUCCEEDED" if complete else "INCOMPLETE", "required_artifacts": artifacts,
                  "completion_scope": "transport tasks and required files; scientific validation is separate",
                  "validated_response_count": sum(t.get("validation") == "VALID" for t in tasks),
                  "task_count": len(tasks), "task_statuses": {state: sum(t["status"] == state for t in tasks) for state in ("PLANNED", "RUNNING", "SUCCEEDED", "FAILED")}}
        atomic_write_json(self.run_dir / "completion.json", result)
        return result


class SavedResponse(SimpleNamespace):
    """Content-oriented recovery adapter; raw JSON is available as saved_payload."""


class LLMRuntime:
    def __init__(self, ledger: RunLedger, scheduler: Optional[RequestScheduler] = None):
        self.ledger = ledger
        self.scheduler = scheduler or RequestScheduler()
        self._ordinal = 0
        self._lock = threading.Lock()

    def next_task_id(self):
        with self._lock:
            self._ordinal += 1
            # Automatic IDs are fresh tasks; explicit stable IDs enable recovery.
            return f"auto-{self._ordinal:08d}-{uuid.uuid4().hex}"

    def call(self, function: Callable[[], Any], *, stage, task_id=None, trial_id="default", purpose="inference",
             request, resume=False, estimated_tokens=None, decode_response=None, validate_response=None):
        if resume and task_id is None:
            raise ValueError("Same-task recovery requires an explicit stable task_id")
        key = self.ledger.plan_task(stage=stage, task_id=task_id or self.next_task_id(), trial_id=trial_id, purpose=purpose, request=request)
        if resume:
            saved = self.ledger.accepted_response(key)
            if saved is not None:
                return decode_response(saved["response"]) if decode_response else saved["response"]
        attempt = self.ledger.begin_attempt(key)
        started = self.scheduler.clock()
        try:
            with self.scheduler.slot(estimated_tokens=estimated_tokens) as admission:
                execution_started = self.scheduler.clock()
                response = function()
                execution_seconds = self.scheduler.clock() - execution_started
                self.ledger.record_response(attempt, response, **admission, execution_seconds=execution_seconds,
                                            usage=json_value(getattr(response, "usage", getattr(response, "token_usage", None))),
                                            response_model=getattr(response, "model", None), response_id=getattr(response, "id", None))
                if validate_response is not None:
                    try:
                        valid = validate_response(response)
                        if valid is not True:
                            raise ValueError("Response validator must explicitly return True")
                    except BaseException as error:
                        self.ledger.validate_attempt(attempt, False, str(error))
                        raise
                    self.ledger.validate_attempt(attempt, True)
                return response
        except BaseException as error:
            # No outer retry. Subsequent explicitly chosen attempts share budget.
            headers = getattr(getattr(error, "response", None), "headers", {}) or {}
            try:
                delay = float(headers.get("Retry-After", headers.get("retry-after", "nan")))
                self.scheduler.defer(delay)
            except (ValueError, TypeError):
                pass
            self.ledger.record_failure(attempt, error, elapsed_seconds=self.scheduler.clock() - started,
                                       sdk_internal_attempts=None)
            raise

    def call_model(self, model, messages, *, stage, task_id=None, trial_id="default", purpose="inference",
                   resume=False, model_id=None, endpoint=None, request_parameters=None, estimated_tokens=None,
                   validate_response=None):
        parameters = dict(request_parameters or {})
        identity_model = getattr(model, "__self__", model)
        model_secrets = secret_values({key: getattr(identity_model, key, None) for key in ("api_key", "access_token", "authorization")})
        self.ledger.secrets = tuple(set(self.ledger.secrets + model_secrets))
        model_defaults = getattr(identity_model, "kwargs", None)
        if model_defaults is not None and not isinstance(model_defaults, dict):
            model_defaults = None
        request = {"messages": messages, "parameters": parameters,
                   "model_default_parameters": model_defaults,
                   "model": model_id if model_id is not None else getattr(identity_model, "model_id", None),
                   "endpoint": endpoint if endpoint is not None else getattr(identity_model, "api_base", None),
                   "model_backend_revision": None}
        def restore(payload):
            if isinstance(payload, dict) and "content" in payload:
                return SavedResponse(**payload, saved_payload=payload)
            if isinstance(payload, str):
                return payload
            raise TaskStateError("This response shape needs an explicit decoder via runtime.call")
        return self.call(lambda: model(messages, **parameters), stage=stage, task_id=task_id, trial_id=trial_id,
                         purpose=purpose, request=request, resume=resume, estimated_tokens=estimated_tokens,
                         decode_response=restore, validate_response=validate_response)

    def wrap_model(self, model, *, stage, trial_id="default", resume=False):
        runtime = self
        class ModelProxy:
            def __getattr__(self, name):
                return getattr(model, name)
            def __call__(self, messages, **kwargs):
                return runtime.call_model(model, messages, stage=stage, trial_id=trial_id, resume=resume, request_parameters=kwargs)
        return ModelProxy()


@contextmanager
def use_runtime(runtime: Optional[LLMRuntime]):
    token = _CURRENT.set(runtime)
    try:
        yield runtime
    finally:
        _CURRENT.reset(token)


def get_runtime() -> Optional[LLMRuntime]:
    return _CURRENT.get()


def invoke_model(model, messages, *, stage, task_id=None, trial_id="default", purpose="inference",
                 runtime=None, resume=False, model_id=None, endpoint=None, request_parameters=None, estimated_tokens=None,
                 validate_response=None):
    active = runtime if runtime is not None else get_runtime()
    if active is None:
        return model(messages, **(request_parameters or {}))
    return active.call_model(model, messages, stage=stage, task_id=task_id, trial_id=trial_id, purpose=purpose,
                             resume=resume, model_id=model_id, endpoint=endpoint, request_parameters=request_parameters,
                             estimated_tokens=estimated_tokens, validate_response=validate_response)
