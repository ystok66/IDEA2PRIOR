"""Semantic citation-neighbor gate, with explicit uncertainty and no lexical fallback."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from loguru import logger
from smolagents import LiteLLMModel

from pipelines.llm_config import get_litellm_config
from pipelines.runtime import atomic_write_json, fingerprint, get_runtime
from pipelines.tasks.prior_art_llm_utils import extract_text_from_llm_response
from pipelines.tasks.prior_art_scoring import _invoke_with_run_reuse
from pipelines.tasks.prior_art_utils import ordered_parallel_map

PROMPT_VERSION = "citation_semantic_v1"
PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "citation_neighbor_semantic_v1.txt"


def load_prompt():
    if PROMPT_PATH.is_file():
        return PROMPT_PATH.read_text(encoding="utf-8")
    from importlib.resources import files
    return files("paperarena_prompts").joinpath("citation_neighbor_semantic_v1.txt").read_text(encoding="utf-8")


def _key(paper):
    if paper.get("paperId"):
        return str(paper["paperId"])
    doi = (paper.get("externalIds") or {}).get("DOI")
    if doi:
        return "DOI:" + str(doi).removeprefix("https://doi.org/").casefold()
    data = json.dumps({k: paper.get(k) for k in ("title", "year", "abstract")},
                      sort_keys=True, ensure_ascii=False)
    return "anonymous:" + hashlib.sha256(data.encode("utf-8")).hexdigest()


def _parse_response(response, expected_ids):
    payload = json.loads(extract_text_from_llm_response(response))
    rows = payload.get("papers") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or len(rows) != len(expected_ids):
        raise ValueError("Citation scoring must assess every input exactly once")
    parsed = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Citation assessment must be an object")
        pid, score, reason = row.get("paper_id"), row.get("score"), row.get("reason")
        if not isinstance(pid, str) or pid not in expected_ids or pid in parsed:
            raise ValueError("Unknown, omitted, or duplicate citation paper ID")
        if "score" not in row or (score is not None and (type(score) is not int or score not in range(4))):
            raise ValueError("Citation score must be an integer 0-3 or null")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("Citation assessment requires an evidence-based reason")
        parsed[pid] = {"score": score, "reason": reason.strip()}
    return parsed


def filter_citation_neighbor_groups(groups, cfg, idea_description, facets=(), run_mgr=None,
                                   search_queries=(), relevance_keywords=(), research_query=None):
    """Score unique neighbors once, then apply positive top-k independently per seed.

    Unknown/failed assessments bypass this positive-only top-k, not the downstream
    global citation budget. Neither failures nor missing abstracts become score 0.
    Groups and coordinator ordering are preserved; workers never mutate the input.
    """
    batch_size = getattr(cfg, "citation_neighbor_batch_size", 20)
    if type(batch_size) is not int or not 1 <= batch_size <= 20:
        raise ValueError("Citation scoring batch size must be between 1 and 20")
    cap = cfg.topk_neighbors_after_lexical
    if type(cap) is not int or cap < 0:
        raise ValueError("Citation neighbor budget must be a nonnegative integer")
    if not isinstance(idea_description, str) or not idea_description.strip():
        raise ValueError("Semantic citation scoring requires the complete research idea")
    unique, group_ids = {}, []
    for group in groups:
        ids = []
        for paper in group or []:
            pid = _key(paper)
            if pid not in ids:
                ids.append(pid)
            previous = unique.get(pid)
            if previous is None or len(paper.get("abstract") or "") > len(previous.get("abstract") or ""):
                unique[pid] = dict(paper)
        group_ids.append(ids)
    assessments = {}
    for pid, paper in unique.items():
        if not isinstance(paper.get("abstract"), str) or not paper["abstract"].strip():
            assessments[pid] = {"score": None, "status": "insufficient_evidence",
                               "reason": "Abstract missing; semantic relevance remains unassessed."}
    assessable = [pid for pid in unique if pid not in assessments]
    batches = [assessable[i:i + batch_size] for i in range(0, len(assessable), batch_size)]
    prompt = load_prompt()

    def score_batch(ids):
        try:
            model_id, api_base, api_key = get_litellm_config(role="light")
            model = LiteLLMModel(model_id=model_id, api_base=api_base, api_key=api_key,
                                 timeout=120, num_retries=0, max_retries=0)
            payload = {"research_query": research_query or idea_description,
                       "idea_description": idea_description, "facets": list(facets),
                       "search_queries": list(search_queries),
                       "relevance_keywords": list(relevance_keywords),
                       "papers": [{"paper_id": pid, "title": unique[pid].get("title"),
                                   "abstract": unique[pid]["abstract"]} for pid in ids]}
            messages = [{"role": "system", "content": prompt},
                        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]

            def validate(response):
                _parse_response(response, set(ids))
                return True

            task_id = "citation-" + fingerprint({"messages": messages, "model": model_id,
                       "endpoint": api_base, "credential_scope": api_key,
                       "defaults": getattr(model, "kwargs", None)})[:24]
            response = _invoke_with_run_reuse(
                model, messages, stage="citation_neighbor_scoring", task_id=task_id,
                purpose=PROMPT_VERSION, reuse=cfg.reuse_llm_across_stages,
                dependencies={"prompt_version": PROMPT_VERSION},
                validate_response=validate, rate_limit=True)
            parsed = _parse_response(response, set(ids))
            return {pid: {**row, "status": "succeeded" if row["score"] is not None
                          else "insufficient_evidence"} for pid, row in parsed.items()}
        except Exception as error:
            # No model retry or lexical fallback at this layer. Keep failure visible.
            logger.warning("[CitationSemantic] Batch unassessed: {}", type(error).__name__)
            return {pid: {"score": None, "status": "failed",
                          "reason": "Semantic scoring failed (" + type(error).__name__ + "); retained unassessed."}
                    for pid in ids}

    for result in ordered_parallel_map(batches, score_batch, max_workers=cfg.max_workers_llm_prefilter):
        assessments.update(result)
    for value in assessments.values():
        value.update(policy=PROMPT_VERSION, model_role="light")
    output, seed_audits = [], []
    for ids in group_ids:
        order = {pid: i for i, pid in enumerate(ids)}
        positive = sorted((pid for pid in ids if assessments[pid]["score"] is not None
                           and assessments[pid]["score"] > 0),
                          key=lambda pid: (-assessments[pid]["score"], order[pid]))
        selected_positive = positive[:cap]
        unknown = [pid for pid in ids if assessments[pid]["score"] is None]
        kept_ids = selected_positive + unknown
        output.append([{**unique[pid], "citation_filter_assessment": dict(assessments[pid])}
                       for pid in kept_ids])
        seed_audits.append({"input_ids": ids, "kept_positive_ids": selected_positive,
                            "positive_budget_excluded_ids": positive[cap:],
                            "zero_excluded_ids": [pid for pid in ids if assessments[pid]["score"] == 0],
                            "uncertain_retained_ids": unknown, "kept_ids": kept_ids})
    audit = {"policy": PROMPT_VERSION, "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
             "research_query": research_query or idea_description,
             "idea_description": idea_description, "facets": list(facets),
             "search_queries": list(search_queries), "relevance_keywords": list(relevance_keywords),
             "model_role": "light", "batch_size": batch_size, "positive_cap_per_seed": cap,
             "input_record_count": sum(len(g or []) for g in groups), "unique_count": len(unique),
             "batch_count": len(batches), "assessments": assessments, "seed_decisions": seed_audits,
             "unknown_policy": "retain_unassessed_beyond_positive_cap; downstream_global_budgets_still_apply"}
    if run_mgr is not None:
        atomic_write_json(run_mgr.run_dir / "citation_neighbor_filter.json", audit)
    runtime = get_runtime()
    if runtime is not None:
        runtime.ledger.event("CITATION_SEMANTIC_FILTER", policy=PROMPT_VERSION,
                             unique_count=len(unique), batch_count=len(batches),
                             unassessed_count=sum(a["score"] is None for a in assessments.values()),
                             zero_count=sum(a["score"] == 0 for a in assessments.values()))
    logger.info("[CitationSemantic] {} unique neighbors; {} batches; {} zero; {} unassessed",
                len(unique), len(batches), sum(a["score"] == 0 for a in assessments.values()),
                sum(a["score"] is None for a in assessments.values()))
    return output
