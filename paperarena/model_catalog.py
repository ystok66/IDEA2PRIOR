"""Explicit, bounded model-catalog lookup; never sends generation requests.

Gemini model fields/pagination follow https://ai.google.dev/api/models.
OpenAI-compatible catalogs use GET /models and optional same-endpoint cursors.
"""
from __future__ import annotations

import math
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter

from .config import APIProfile


GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_MODELS = 5000


class ModelCatalogError(ValueError):
    """Public error whose text contains no keys, URLs, or provider payloads."""
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _model_rows(payload: dict[str, Any], profile: APIProfile, purpose="generation") -> list[dict[str, str]]:
    source = payload.get("models" if profile.provider == "gemini" else "data")
    if not isinstance(source, list) or len(source) > _MAX_MODELS:
        raise ModelCatalogError("INVALID_CATALOG", "The provider did not return a supported model catalog.")
    result = []
    for row in source:
        if not isinstance(row, dict):
            continue
        if profile.provider == "gemini":
            methods = row.get("supportedGenerationMethods")
            required_method = "embedContent" if purpose == "embedding" else "generateContent"
            if not isinstance(methods, list) or required_method not in methods:
                continue
            model_id = row.get("name")
            if isinstance(model_id, str):
                model_id = model_id.removeprefix("models/")
            label = row.get("displayName")
        else:
            model_id = row.get("id")
            label = row.get("name")
        if not isinstance(model_id, str) or not model_id.strip() or len(model_id) > 512:
            continue
        model_id = model_id.strip()
        if any(ord(char) < 32 for char in model_id) or (profile.api_key and profile.api_key in model_id):
            continue
        label = label if isinstance(label, str) and label.strip() else model_id
        if profile.api_key:
            label = label.replace(profile.api_key, "[REDACTED]")
        label = " ".join(label.split())[:200]
        result.append({"id": model_id, "label": label})
    return result


def fetch_models(profile: APIProfile, *, timeout_seconds: float = 15.0,
                 max_pages: int = 5, purpose="generation") -> list[dict[str, str]]:
    """Return model options from the explicitly chosen provider.

    There are at most max_pages GETs, zero automatic retries, zero redirects,
    zero fallback providers, and zero generation requests. The timeout is also
    checked between pages; missing keys fail before opening an HTTP session.
    Manual model IDs remain a frontend option when a catalog is unavailable.
    """
    if purpose not in ("generation", "embedding"):
        raise ModelCatalogError("INVALID_PURPOSE", "Unsupported model capability.")
    if not isinstance(profile, APIProfile):
        raise ModelCatalogError("INVALID_PROFILE", "Select a valid API profile before fetching models.")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 60:
        raise ModelCatalogError("INVALID_TIMEOUT", "Catalog timeout must be between zero and 60 seconds.")
    if type(max_pages) is not int or not 1 <= max_pages <= 10:
        raise ModelCatalogError("INVALID_PAGE_LIMIT", "Catalog page limit must be between one and ten.")
    try:
        profile = profile.resolved(require_key=True)
    except ValueError:
        raise ModelCatalogError("MISSING_API_KEY", "Enter an API key before fetching the model catalog.") from None

    native_gemini = profile.provider == "gemini"
    url = GEMINI_MODELS_URL if native_gemini else (profile.base_url or "https://api.openai.com/v1").rstrip("/") + "/models"
    headers = {"Accept": "application/json"}
    headers["x-goog-api-key" if native_gemini else "Authorization"] = profile.api_key if native_gemini else "Bearer " + profile.api_key
    params: dict[str, Any] = {"pageSize": 1000} if native_gemini else {}
    deadline = time.monotonic() + timeout_seconds
    seen_cursors = set()
    found: dict[str, dict[str, str]] = {}
    session = requests.Session()
    session.trust_env = False  # No inherited proxy or .netrc auth can reroute keys.
    session.mount("http://", HTTPAdapter(max_retries=0))
    session.mount("https://", HTTPAdapter(max_retries=0))
    try:
        for page in range(max_pages):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ModelCatalogError("CATALOG_TIMEOUT", "The model catalog request timed out.")
            try:
                response = session.get(url, headers=headers, params=dict(params),
                    timeout=(min(5.0, remaining), remaining), allow_redirects=False)
                if response.status_code != 200:
                    raise ModelCatalogError("CATALOG_HTTP_ERROR", "The model catalog could not be loaded. Check the API key and selected endpoint.")
                if len(response.content) > _MAX_RESPONSE_BYTES:
                    raise ModelCatalogError("CATALOG_TOO_LARGE", "The model catalog response exceeded the local size limit.")
                payload = response.json()
            except ModelCatalogError:
                raise
            except requests.Timeout:
                raise ModelCatalogError("CATALOG_TIMEOUT", "The model catalog request timed out.") from None
            except (requests.RequestException, ValueError, TypeError):
                raise ModelCatalogError("CATALOG_REQUEST_FAILED", "The model catalog request failed. Check connectivity and endpoint compatibility.") from None
            if not isinstance(payload, dict):
                raise ModelCatalogError("INVALID_CATALOG", "The provider did not return a supported model catalog.")
            for row in _model_rows(payload, profile, purpose):
                found.setdefault(row["id"], row)
            if len(found) > _MAX_MODELS:
                raise ModelCatalogError("CATALOG_TOO_LARGE", "The model catalog exceeded the local item limit.")

            if native_gemini:
                cursor = payload.get("nextPageToken")
                cursor_field = "pageToken"
                has_more = bool(cursor)
            else:
                # Standard OpenAI lists are unpaginated. Compatible endpoints
                # may expose a has_more/last_id cursor; never follow remote URLs.
                has_more = payload.get("has_more") is True
                cursor = payload.get("last_id")
                cursor_field = "after"
            if not has_more:
                return sorted(found.values(), key=lambda row: row["id"].casefold())
            if not isinstance(cursor, str) or not cursor or len(cursor) > 4096 or cursor in seen_cursors:
                raise ModelCatalogError("INVALID_PAGINATION", "The provider returned unsupported catalog pagination.")
            if page + 1 >= max_pages:
                raise ModelCatalogError("CATALOG_PAGE_LIMIT", "The catalog page limit was reached. Enter the desired model ID manually.")
            seen_cursors.add(cursor)
            params[cursor_field] = cursor
        raise ModelCatalogError("CATALOG_PAGE_LIMIT", "The catalog page limit was reached.")
    finally:
        session.close()


__all__ = ["fetch_models", "ModelCatalogError"]
