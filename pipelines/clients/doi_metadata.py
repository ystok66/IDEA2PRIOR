"""Registration-agency routing and DataCite metadata, scoped to one run.

An agency's 404 only means that its endpoint did not return a public record in
this material snapshot. It never means that the paper or DOI does not exist.
This module does not assign publication dates or mutate paper/PDF versions.
"""
from __future__ import annotations

import copy
import os
import re
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import quote, unquote

from pipelines.clients.http_transport import get
from pipelines.runtime import get_runtime


_INITIALIZATION_LOCK = threading.Lock()
_DOI_PATTERN = re.compile(r"10\.\d{4,9}/\S+", re.I)
_ARXIV_DOI_PATTERN = re.compile(
    r"10\.48550/arxiv\.((?:\d{4}\.\d{4,5}|[a-z.-]+/\d{7}))(?:v[1-9]\d*)?", re.I
)


def normalize_doi(value):
    """Normalize a DOI name/URL without stripping or inventing version suffixes."""
    if not isinstance(value, str):
        return None
    normalized = unquote(value).strip()
    normalized = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", normalized, flags=re.I)
    normalized = re.sub(r"^doi:\s*", "", normalized, flags=re.I)
    if not _DOI_PATTERN.fullmatch(normalized):
        return None
    return normalized.lower()


def arxiv_doi_id(value):
    """Return the unversioned arXiv entity ID encoded in an arXiv DOI.

    An input's optional vN suffix is ignored only in this returned routing ID.
    It does not rewrite the input DOI, requested arXiv version, or PDF identity.
    arXiv itself assigns one unversioned DOI that resolves to the latest version.
    """
    normalized = normalize_doi(value)
    match = _ARXIV_DOI_PATTERN.fullmatch(normalized or "")
    return match.group(1) if match else None


def _outcome(stage, status, **details):
    runtime = get_runtime()
    if runtime is None:
        return
    runtime.ledger.event("RETRIEVAL_RESULT", stage=stage, status=status, **details)
    if status in ("FAILED", "INVALID_RESPONSE", "IDENTITY_CONFLICT"):
        with _INITIALIZATION_LOCK:
            if not hasattr(runtime, "retrieval_issues"):
                runtime.retrieval_issues = []
            runtime.retrieval_issues.append({"stage": stage, "status": status, **details})


@contextmanager
def _run_cache(key):
    """Coalesce deterministic metadata reads only within the active runtime."""
    runtime = get_runtime()
    if runtime is None:
        yield {}
        return
    with _INITIALIZATION_LOCK:
        if not hasattr(runtime, "doi_metadata_cache"):
            runtime.doi_metadata_cache = {}
            runtime.doi_metadata_locks = {}
        lock = runtime.doi_metadata_locks.setdefault(key, threading.RLock())
    with lock:
        yield runtime.doi_metadata_cache


def _cached_value(cache, key, stage, doi):
    entry = cache[key]
    _outcome(stage, entry["status"], input_id=doi, provider_scope=stage,
             cache_hit=True, cached_from_lookup_id=entry["lookup_id"],
             observed_at=entry["observed_at"], scope="current_run")
    return copy.deepcopy(entry["value"])


def _save(cache, key, stage, doi, status, value, lookup_id):
    observed_at = datetime.now(timezone.utc).isoformat()
    cache[key] = {"status": status, "value": copy.deepcopy(value),
                  "lookup_id": lookup_id, "observed_at": observed_at}
    _outcome(stage, status, input_id=doi, provider_scope=stage, cache_hit=False,
             lookup_id=lookup_id, observed_at=observed_at, scope="current_run")
    return copy.deepcopy(value)


def datacite_lookup(doi):
    """Return complete DataCite attributes, or a visible provider-scoped failure.

    Only successful identity-checked records and explicit HTTP 404s are cached.
    Transport errors, rate limits, server failures, and malformed/conflicting
    records remain retryable on a later caller's bounded attempt.
    """
    normalized = normalize_doi(doi)
    if normalized is None:
        _outcome("datacite", "INVALID_ID", input_id=doi, provider_scope="datacite")
        return None
    key = ("datacite", "doi", normalized)
    with _run_cache(key) as cache:
        if key in cache:
            return _cached_value(cache, key, "datacite", normalized)
        lookup_id = uuid.uuid4().hex
        try:
            response = get("https://api.datacite.org/dois/" + quote(normalized, safe=""),
                           logical_request_id=lookup_id)
            if response.status_code == 404:
                return _save(cache, key, "datacite", normalized, "NOT_FOUND", None, lookup_id)
            response.raise_for_status()
            body = response.json()
            data = body.get("data") if isinstance(body, dict) else None
            attributes = data.get("attributes") if isinstance(data, dict) else None
            if (not isinstance(attributes, dict) or data.get("type") != "dois"
                    or not normalize_doi(data.get("id")) or not normalize_doi(attributes.get("doi"))):
                _outcome("datacite", "INVALID_RESPONSE", input_id=normalized, lookup_id=lookup_id)
                return None
            if normalize_doi(data["id"]) != normalized or normalize_doi(attributes["doi"]) != normalized:
                _outcome("datacite", "IDENTITY_CONFLICT", input_id=normalized,
                         returned_id=data.get("id"), returned_doi=attributes.get("doi"), lookup_id=lookup_id)
                return None
            return _save(cache, key, "datacite", normalized, "SUCCEEDED", attributes, lookup_id)
        except Exception as exc:
            _outcome("datacite", "FAILED", input_id=normalized, lookup_id=lookup_id,
                     error_type=type(exc).__name__)
            return None


def doi_agency(doi):
    """Return a registration agency ID, not a publication-type judgment.

    arXiv's documented 10.48550 prefix routes directly to DataCite. Other DOI
    names use the Crossref agency endpoint, which also covers non-Crossref DOIs.
    """
    normalized = normalize_doi(doi)
    if normalized is None:
        _outcome("doi_agency", "INVALID_ID", input_id=doi, provider_scope="doi_agency")
        return None
    if normalized.startswith("10.48550/arxiv."):
        _outcome("doi_agency", "ROUTED", input_id=normalized, agency="datacite",
                 source="documented_arxiv_prefix", network_request=False)
        return "datacite"
    key = ("crossref", "agency", normalized)
    with _run_cache(key) as cache:
        if key in cache:
            return _cached_value(cache, key, "doi_agency", normalized)
        lookup_id = uuid.uuid4().hex
        try:
            params = {"mailto": os.getenv("CROSSREF_MAILTO") or os.getenv("OPENALEX_MAILTO", "")}
            response = get("https://api.crossref.org/works/" + quote(normalized, safe="") + "/agency",
                           params=params, logical_request_id=lookup_id)
            if response.status_code == 404:
                return _save(cache, key, "doi_agency", normalized, "NOT_FOUND", None, lookup_id)
            response.raise_for_status()
            body = response.json()
            message = body.get("message") if isinstance(body, dict) else None
            agency = message.get("agency") if isinstance(message, dict) else None
            name = agency.get("id") if isinstance(agency, dict) else None
            if not isinstance(name, str) or not name.strip() or not normalize_doi(message.get("DOI")):
                _outcome("doi_agency", "INVALID_RESPONSE", input_id=normalized, lookup_id=lookup_id)
                return None
            if normalize_doi(message["DOI"]) != normalized:
                _outcome("doi_agency", "IDENTITY_CONFLICT", input_id=normalized,
                         returned_doi=message.get("DOI"), lookup_id=lookup_id)
                return None
            return _save(cache, key, "doi_agency", normalized, "SUCCEEDED", name.strip().lower(), lookup_id)
        except Exception as exc:
            _outcome("doi_agency", "FAILED", input_id=normalized, lookup_id=lookup_id,
                     error_type=type(exc).__name__)
            return None
