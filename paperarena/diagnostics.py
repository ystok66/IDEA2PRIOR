"""Public failure explanations without provider payloads or credentials."""
from __future__ import annotations

import re


def _label(value, fallback):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", value) else fallback


def diagnostic_for_type(type_name, *, http_status=None, phase=None, marker=None):
    """Classify known failure metadata; never echo arbitrary error text."""
    name = _label(type_name, "RuntimeError")
    lower = name.lower()
    status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
    if marker == "get_channel_failed":
        code, message = "PROVIDER_ROUTE_UNAVAILABLE", "The selected API could not route this model request. Check model availability at that endpoint."
    elif status == 401 or "authentication" in lower:
        code, message = "AUTHENTICATION_REJECTED", "The API rejected authentication. Check the key and selected provider."
    elif status == 403 or "permission" in lower and status is not None:
        code, message = "PERMISSION_DENIED", "The API denied access. Check this key's permissions and model access."
    elif status == 429 or "ratelimit" in lower:
        code, message = "RATE_LIMITED", "The API rate or quota limit was reached. Check the provider's quota and request limits."
    elif "contextwindow" in lower:
        code, message = "CONTEXT_LIMIT_EXCEEDED", "The selected model's context limit was exceeded. Choose a model with sufficient context."
    elif status in (408, 504) or "timeout" in lower:
        code, message = "PROVIDER_TIMEOUT", "An API request timed out before returning a usable response. No whole-task rerun was started."
    elif status == 404 or ("notfound" in lower and name not in ("ModuleNotFoundError", "FileNotFoundError")):
        code, message = "MODEL_UNAVAILABLE", "The requested model or API route was not found. Check the model ID and endpoint."
    elif status == 400 or "badrequest" in lower:
        code, message = "INVALID_API_REQUEST", "The API rejected the request format or parameters. Check protocol and model compatibility."
    elif status is not None and status >= 500:
        code, message = "PROVIDER_FAILURE", "The API returned a server error. Existing run records were retained."
    elif "connection" in lower or "connecterror" in lower:
        code, message = "NETWORK_CONNECTION_FAILED", "The API connection failed. Check network access and the endpoint."
    elif name in ("ModuleNotFoundError", "ImportError"):
        code, message = "RUNTIME_DEPENDENCY_MISSING", "A required Python dependency is unavailable. Install the project's declared dependencies."
    elif name in ("JSONDecodeError", "ResponseValidationError"):
        code, message = "INVALID_MODEL_RESPONSE", "The model response did not satisfy the required JSON or ranking schema."
    elif name in ("PermissionError", "OSError", "FileNotFoundError"):
        code, message = "LOCAL_FILE_ERROR", "A required local file could not be read or written. Check installation paths and directory permissions."
    elif name in ("ValueError", "TypeError", "KeyError"):
        code, message = "INVALID_CONFIGURATION_OR_RESPONSE", "Configuration or response validation failed. Check API profiles and the recorded stage."
    else:
        code, message = "PIPELINE_ERROR", "The pipeline could not finish this stage. Inspect the local run records; no whole-task rerun was started."
    result = {"type": name, "code": code, "message": message}
    if status is not None: result["http_status"] = status
    if phase: result["phase"] = _label(phase, "unknown_stage")
    return result


def safe_error(error, phase=None):
    """Return safe actionable diagnostics for a local/provider exception."""
    status = getattr(error, "status_code", None)
    if status is None:
        status = getattr(getattr(error, "response", None), "status_code", None)
    # Only an allowlisted marker is inspected internally. No raw exception,
    # request body, response body, credential, or URL enters the public result.
    description = str(error).lower()
    current_phase = phase or getattr(error, "_paperarena_phase", None)
    if isinstance(error, ValueError) and "missing api key" in description:
        return {"type": type(error).__name__, "code": "MISSING_API_KEY",
            "message": "A selected API profile has no key. Enter its key or set the configured environment variable.",
            "phase": _label(current_phase, "configuration")}
    if type(error).__name__ == "JSONDecodeError" and current_phase == "configuration":
        return {"type": "JSONDecodeError", "code": "INVALID_CONFIG_JSON",
            "message": "The configuration file is not valid JSON. Check its syntax before running.", "phase": "configuration"}
    if "prepayment credits are depleted" in description or "prepaid credits are exhausted" in description:
        return {"type": type(error).__name__, "code": "BILLING_CREDITS_EXHAUSTED",
            "message": "The provider reports that prepaid API credits are exhausted. Check this project's billing before another run.",
            "http_status": 402, "adapter_http_status": status,
            "phase": _label(current_phase, "unknown_stage")}
    marker = "get_channel_failed" if "get_channel_failed" in description or "no available channel" in description else None
    return diagnostic_for_type(type(error).__name__, http_status=status,
        phase=current_phase, marker=marker)
