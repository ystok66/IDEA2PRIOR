"""Internal stdin-driven worker. Console emits only public progress events."""
from __future__ import annotations

from dataclasses import fields
import functools
import json
import os
from pathlib import Path
import sys
import threading
import time

from .config import RunConfig, worker_environment
from .diagnostics import safe_error, diagnostic_for_type


def default_pipeline_settings(config: RunConfig) -> dict:
    """Preserve the published Top-5 profile's retrieval and ranking semantics."""
    settings = {
        "execution_mode": config.mode, "citation_neighbor_filter": "llm",
        "citation_neighbor_batch_size": 20, "enable_crossref_references": False,
        "retrieval_backend": "s2_arxiv_crossref_v1", "enable_openalex": False,
        "s2_batch_size": 100, "http_max_wait_seconds": 120,
        "staged_retrieval": True, "selection_policy_version": "relevance_protected_v1",
        "max_candidates": 200, "retriever_protect_top_k": 5, "protected_candidate_ratio": 0.25,
        "missing_abstract_reserve": 10, "metadata_shortlist_size": 50,
        "metadata_boundary_buffer": 10, "citation_new_candidate_limit": 50,
        "debug_mode": False, "recent_year_start": 2024, "recent_year_end": 2025,
        "max_workers_llm_scoring": 20, "max_workers_llm_prefilter": 4,
        "max_workers_openalex": 4, "max_workers_s2": 4, "max_workers_pdf": 5,
        "max_workers_web_batches": 4, "max_workers_fulltext_refine": 4,
        "max_workers_snippet_batches": 4, "max_in_flight_requests": 4,
        "overlap_pdf_keywords": True, "resume_same_task": False,
        "reuse_llm_across_stages": True, "enable_arxiv_search": False,
        "expert_retrieval_timeout_seconds": config.request_timeout_seconds,
        "optimize_scholarly_requests": True, "shared_http_connections": True,
        "shared_pdf_cache_dir": None, "retrieval_eval_fulltext_limit": 5,
        "retrieval_eval_pdf_text_mode": "snippets", "retrieval_eval_pdf_max_snippets": 6,
        "retrieval_eval_pdf_snippet_chars": 1800, "retrieval_eval_skip_report_evidence": True,
        "retrieval_eval_parallel_seed_discovery": True, "retrieval_eval_expert_compact_input": True,
        "retrieval_eval_expert_max_output_tokens": config.expert_max_output_tokens,
        "enable_serpapi": bool(config.search.get("serpapi_api_key")), "enable_duckduckgo": True,
    }
    settings.update(config.pipeline)
    for key in ("enable_serpapi", "enable_duckduckgo"):
        if key in config.search:
            settings[key] = config.search[key]
    if settings["enable_serpapi"] and not config.search.get("serpapi_api_key"):
        raise ValueError("SerpAPI is enabled but no explicit SerpAPI key was supplied")
    if config.years:
        settings["recent_year_start"] = max(config.years[0], min(settings["recent_year_start"], config.years[1]))
        settings["recent_year_end"] = max(settings["recent_year_start"], min(settings["recent_year_end"], config.years[1]))
    return settings


def _token_counts(response) -> dict:
    raw = getattr(response, "raw", None)
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump()
    raw = raw if isinstance(raw, dict) else {}
    usage = raw.get("usage")
    if not isinstance(usage, dict):
        usage = getattr(response, "token_usage", None)
        if hasattr(usage, "dict"):
            usage = usage.dict()
        elif hasattr(usage, "__dict__"):
            usage = vars(usage)
    usage = usage if isinstance(usage, dict) else {}
    values = {}
    for field, aliases in (("prompt_tokens", ("prompt_tokens", "input_tokens")), ("completion_tokens", ("completion_tokens", "output_tokens")), ("total_tokens", ("total_tokens",))):
        value = next((usage[name] for name in aliases if name in usage), None)
        values[field] = value if type(value) is int and value >= 0 else None
    return values


def install_observers(config: RunConfig, emit):
    """Observe dispatches and reported usage without changing model prompts."""
    import smolagents
    import litellm
    original = smolagents.LiteLLMModel
    litellm.num_retries = 0
    litellm.model_fallbacks = None
    litellm.drop_params = False
    litellm.suppress_debug_info = True
    lock = threading.Lock()
    attempts = 0
    responses = 0
    failures = 0
    last_errors = []
    phase_state = {"value": "retrieval"}
    subtotals = dict.fromkeys(("prompt_tokens", "completion_tokens", "total_tokens"), 0)
    observed = dict.fromkeys(subtotals, 0)

    def usage_snapshot():
        missing = {field: attempts - observed[field] for field in subtotals}
        return {"model_attempts": attempts, "responses_observed": responses,
            "failed_model_attempts": failures, "recent_request_errors": list(last_errors),
            "reported_token_subtotal": {field: value if observed[field] else None for field, value in subtotals.items()},
            "missing_token_attempt_counts": missing,
            "tokens": {field: subtotals[field] if observed[field] and not missing[field] else None for field in subtotals},
            "token_usage_complete": bool(attempts) and all(n == 0 for n in missing.values()),
            "cost": None, "billable_calls": None,
            "scope": "Adapter calls and reported usage only; unknown usage is not zero. No automatic adapter or SDK retries are requested."}

    class ObservedModel(original):
        def __init__(self, *args, **kwargs):
            self._output_limit = config.max_output_tokens
            kwargs.update(retry=False, num_retries=0, max_retries=0,
                          timeout=config.request_timeout_seconds, max_tokens=self._output_limit,
                          drop_params=False, context_window_fallback_dict={})
            super().__init__(*args, **kwargs)

        def _paperarena_set_output_budget(self, *, role, max_tokens):
            if role != "expert":
                raise ValueError("Only Expert uses a distinct output budget")
            self._output_limit = max_tokens
            self.kwargs["max_tokens"] = max_tokens

        def generate(self, messages, *args, **kwargs):
            nonlocal attempts, responses, failures
            kwargs.update(num_retries=0, max_retries=0, timeout=config.request_timeout_seconds,
                          max_tokens=self._output_limit, drop_params=False,
                          context_window_fallback_dict={})
            with lock:
                attempts += 1
                emit({"usage": usage_snapshot()})
            try:
                response = super().generate(messages, *args, **kwargs)
                counts = _token_counts(response)
                with lock:
                    responses += 1
                    for field, value in counts.items():
                        if value is not None:
                            subtotals[field] += value
                            observed[field] += 1
                return response
            except Exception as error:
                detail = safe_error(error, phase_state["value"])
                with lock:
                    failures += 1
                    last_errors.append(detail)
                    del last_errors[:-10]
                emit({"last_request_error": detail})
                raise
            finally:
                with lock:
                    emit({"usage": usage_snapshot()})

    smolagents.LiteLLMModel = ObservedModel
    # Import the orchestrator after replacing the adapter, including its module
    # aliases. All later model constructors therefore use the same observer.
    import pipelines.tasks.prior_art_check as main
    aliases = {
        "seed_papers_from_llm": "seed_recall", "seed_papers_from_serpapi": "web_search",
        "validate_llm_seeds_via_openalex": "identity_verification",
        "generate_search_queries_from_idea": "search_queries", "run_exploration_loop": "exploration",
        "decompose_idea_into_facets": "idea_analysis", "extract_idea_features": "idea_features",
        "extract_high_level_facets_from_idea": "idea_facets", "generate_seed_queries_llm": "search_queries",
        "retrieve_candidates_with_buckets": "candidate_retrieval", "refine_queries_llm": "query_refinement",
        "generate_relevance_keywords_from_idea": "keywords", "generate_facet_patterns_from_idea": "evidence_patterns",
        "score_papers_with_abstract_only": "abstract_scoring", "expand_corpus_via_citation_graph": "citation_expansion",
        "prepare_fixed_fulltext_for_eval": "fixed_five_pdf_review", "rerank_shortlist_with_expert": "expert_ranking",
    }
    def wrap(function, phase):
        @functools.wraps(function)
        def call(*args, **kwargs):
            phase_state["value"] = phase
            emit({"phase": phase})
            try:
                return function(*args, **kwargs)
            except Exception as error:
                error._paperarena_phase = phase
                raise
        return call
    for name, phase in aliases.items():
        setattr(main, name, wrap(getattr(main, name), phase))
    return ObservedModel, main, usage_snapshot


def execute(payload, emit) -> int:
    config = RunConfig.from_dict(payload["config"])
    env = worker_environment(config)
    os.environ.clear()
    os.environ.update(env)
    from loguru import logger
    logger.remove()  # Raw provider messages and prompts never become console logs.
    directory = Path(payload["output_dir"]).resolve()
    # The arXiv pacing gate remains shared between runs under one output root,
    # while installed package directories remain read-only runtime inputs.
    os.environ.setdefault("PAPERARENA_CACHE_DIR", str(directory.parent / ".runtime_cache"))
    temp_dir = directory / "tmp"
    temp_dir.mkdir(exist_ok=True)
    os.environ["TEMP"] = str(temp_dir)
    os.environ["TMP"] = str(temp_dir)
    import tempfile
    tempfile.tempdir = str(temp_dir)
    settings = default_pipeline_settings(config)
    from pipelines.tasks.prior_art_types import PriorArtConfig
    allowed = {row.name for row in fields(PriorArtConfig)}
    if set(settings) - allowed:
        raise ValueError("Unknown pipeline configuration field")
    cfg = PriorArtConfig(**settings)
    Model, main, snapshot = install_observers(config, emit)
    from pipelines.llm_config import get_litellm_config
    from pipelines.run_manager import RunManager
    manager = RunManager(topic="retrieval", base_dir=directory)
    manager.create_run_directory()
    # Register every explicitly supplied credential before any provider error
    # reaches the structured request ledger's redaction layer.
    secrets = tuple(p.api_key for p in config.profiles.values() if p.api_key) + tuple(str(config.search[key]) for key in ("serpapi_api_key", "semantic_scholar_api_key") if config.search.get(key))
    manager._secrets = secrets
    manager.runtime.ledger.secrets = secrets
    emit({"run_dir": str(manager.run_dir), "phase": "retrieval"})
    model_id, base_url, api_key = get_litellm_config("heavy")
    agent = Model(model_id=model_id, api_base=base_url, api_key=api_key)
    main.run_prior_art_check(agent, payload["query"], payload["topic"], years=config.years,
        venues=[], max_rounds=config.max_rounds, use_citation_graph=config.use_citation_graph,
        cfg=cfg, run_mgr=manager)
    ranked_path = manager.run_dir / "ranked_candidates.json"
    completion_path = manager.run_dir / "completion.json"
    ranked = json.loads(ranked_path.read_text(encoding="utf-8")) if ranked_path.exists() else {}
    completion = json.loads(completion_path.read_text(encoding="utf-8")) if completion_path.exists() else {}
    top5 = ranked.get("candidates", [])[:5]
    status = "SUCCEEDED" if completion.get("status") == "SUCCEEDED" else "PARTIAL" if top5 else "FAILED"
    if config.mode == "retrieval_eval" and ranked.get("status") == "NOT_RUN_EMPTY_POOL":
        status = "EMPTY"
    if config.mode == "retrieval_eval" and ranked.get("status") == "FAILED":
        status = "FAILED"
    usage = snapshot()
    diagnostics = []
    outcome = completion.get("business_outcome", {})
    for issue in outcome.get("issues", [])[:10]:
        phase = issue.get("stage")
        if phase in {"fulltext_material", "expert_fulltext_material_validation", "fulltext_refinement"}:
            detail = {"type": "MaterialUnavailable", "code": "FULLTEXT_REVIEW_INCOMPLETE",
                "message": "Some full-text material was unavailable or could not be validated. Available abstract evidence was retained.", "phase": phase}
        elif phase == "abstract_scoring":
            detail = {"type": "ResponseValidationError", "code": "ABSTRACT_SCORING_INCOMPLETE",
                "message": "Some abstract assessments failed or returned invalid output. Unvalidated grades are not treated as successful scores.", "phase": phase}
        else:
            detail = diagnostic_for_type(issue.get("error_type", "RuntimeError"),
                http_status=issue.get("http_status"), phase=phase)
        diagnostics.append(detail)
    error = None
    if status == "FAILED":
        error = usage["recent_request_errors"][-1] if usage["recent_request_errors"] else diagnostic_for_type(ranked.get("error_type", "NoValidatedRanking"), phase="expert_ranking")
    elif status == "PARTIAL":
        error = {"type": "IncompleteChecks", "code": "PARTIAL_COMPLETION",
            "message": "A ranking was produced, but some retrieval, scoring, or full-text checks did not complete. Review the diagnostic details.", "phase": "finished"}
    elif status == "EMPTY":
        error = {"type": "EmptyCandidatePool", "code": "NO_CANDIDATES",
            "message": "Retrieval produced no eligible candidates for Expert ranking.", "phase": "candidate_retrieval"}
    emit({"status": status, "phase": "finished", "top5": top5, "usage": snapshot(),
          "diagnostics": diagnostics, "issue_count": len(outcome.get("issues", [])),
          "report_files": [str(path) for path in manager.md_reports_dir.glob("*.md")],
          "ranking_rule": ranked.get("ranking_rule"), "error": error})
    return 0 if status in ("SUCCEEDED", "PARTIAL", "EMPTY") else 1


def main():
    stream = sys.stdout
    event_lock = threading.Lock()
    current_phase = {"value": "configuration"}
    def emit(value):
        with event_lock:
            if value.get("phase"):
                current_phase["value"] = value["phase"]
            stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()
    try:
        payload = json.load(sys.stdin)
        # Only deliberate structured events use the saved original stream.
        with open(os.devnull, "w", encoding="utf-8") as sink:
            sys.stdout = sink
            sys.stderr = sink
            return execute(payload, emit)
    except BaseException as exc:
        emit({"status": "FAILED", "phase": "failed", "error": safe_error(exc,
            getattr(exc, "_paperarena_phase", current_phase["value"]))})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
