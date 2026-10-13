#!/usr/bin/env python3
"""Score completed public benchmark outputs locally without model or network calls.

Input is a JSON array or JSONL with item_id, status and top5. Candidate entries
can contain title, doi, arxiv_id, url and an explicit versions list. This is an
identity matcher, not a semantic judge or a substitute for version adjudication.
"""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path
from urllib.parse import unquote


def normalize_title(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    for name, char in {"alpha": "α", "beta": "β", "gamma": "γ", "mu": "μ"}.items():
        text = text.replace("\\" + name, char)
    return "".join(char for char in text if char.isalnum())


def normalize_doi(value: str) -> str:
    text = unquote(str(value or "")).strip().casefold()
    found = re.search(r"10\.\d{4,9}/[^\s<>\"']+", text)
    if not found:
        return ""
    result = found.group(0).split("#", 1)[0].split("?", 1)[0].rstrip(".,;")
    while result.endswith(")") and result.count(")") > result.count("("):
        result = result[:-1]
    return result


def normalize_arxiv(value: str) -> str:
    text = str(value or "").strip().casefold()
    if text.startswith(("http://", "https://")):
        url_record = re.match(r"https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/([^?#]+)", text)
        if not url_record:
            return ""
        text = url_record.group(1).removesuffix(".pdf")
    text = re.sub(r"^arxiv:\s*", "", text)
    modern = re.fullmatch(r"(\d{4}\.\d{4,5})(?:v\d+)?", text)
    legacy = re.fullmatch(r"([a-z-]+(?:\.[a-z]{2})?/\d{7})(?:v\d+)?", text)
    return (modern or legacy).group(1) if modern or legacy else ""


def paper_identifiers(paper: dict) -> tuple[set[str], set[str]]:
    urls = [paper.get(key, "") for key in ("url", "paper_url", "pdf_url", "publication_url")]
    dois = {normalize_doi(value) for value in [paper.get("doi", ""), *urls]}
    arxiv = {normalize_arxiv(value) for value in [paper.get("arxiv_id", ""), paper.get("arxiv", ""), *urls]}
    # arXiv's DataCite DOI identifies an arXiv record rather than a separate
    # journal publication. Only recognize the dedicated, valid namespace.
    arxiv.update(arxiv_record_doi(doi) for doi in dois)
    return dois - {""}, arxiv - {""}


def arxiv_record_doi(doi: str) -> str:
    prefix = "10.48550/arxiv."
    return normalize_arxiv(doi[len(prefix):]) if doi.startswith(prefix) else ""


def primary_match(candidate: dict, gold: dict) -> str | None:
    candidate_dois, candidate_arxiv = paper_identifiers(candidate)
    gold_dois = {normalize_doi(value) for value in [gold.get("doi", ""), *(gold.get("accepted_dois") or [])]} - {""}
    gold_arxiv = {normalize_arxiv(value) for value in [gold.get("arxiv_id", ""), *(gold.get("accepted_arxiv_ids") or [])]} - {""}
    if candidate_dois and gold_dois and not candidate_dois & gold_dois:
        # An explicitly accepted arXiv record can have its own DOI alongside
        # the target publication DOI. Other contradictory DOIs still reject
        # the candidate, even when the title or an arXiv ID agrees.
        record_dois = {doi for doi in candidate_dois if arxiv_record_doi(doi)}
        if candidate_dois - record_dois or not candidate_arxiv & gold_arxiv:
            return None
    if candidate_arxiv and gold_arxiv and not candidate_arxiv & gold_arxiv:
        return None
    if candidate_dois & gold_dois:
        return "doi"
    if candidate_arxiv & gold_arxiv:
        return "accepted_arxiv_record"
    target_title = normalize_title(gold.get("title", ""))
    if target_title and normalize_title(candidate.get("title", "")) == target_title:
        return "normalized_title"
    return None


def match_candidate(candidate: dict, gold: dict, track: str = "designated") -> str | None:
    versions = candidate.get("versions", [])
    if not isinstance(versions, list) or any(not isinstance(row, dict) for row in versions):
        raise ValueError("Candidate versions must be a list of paper objects")
    for paper in [candidate, *versions]:
        match = primary_match(paper, gold)
        if match:
            return match
        if track == "family":
            for alias in gold.get("family_aliases", []):
                alias_gold = {"title": alias.get("title", ""), "doi": normalize_doi(alias.get("url", ""))}
                match = primary_match(paper, alias_gold)
                if match:
                    return "family_alias_" + match
    return None


def read_rows(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8-sig").strip()
    if not text:
        raise ValueError("Results file is empty")
    if text.startswith("["):
        result = json.loads(text)
    elif text.startswith("{"):
        try:
            single = json.loads(text)
        except json.JSONDecodeError:
            result = [json.loads(line) for line in text.splitlines() if line.strip()]
        else:
            result = single.get("results", [single])
    else:
        raise ValueError("Expected a JSON object, JSON array or JSONL")
    if not isinstance(result, list) or any(not isinstance(row, dict) for row in result):
        raise ValueError("Results must be a list of objects")
    return result


def score(rows: list[dict], gold: dict[str, dict], item_ids: list[str], track: str = "designated") -> dict:
    if len(item_ids) != len(set(item_ids)) or any(item_id not in gold for item_id in item_ids):
        raise ValueError("Requested item IDs must be unique and present in the selected benchmark")
    indexed = {}
    for row in rows:
        item_id = row.get("item_id")
        if item_id not in gold or item_id in indexed:
            raise ValueError(f"Unknown or duplicate result item_id: {item_id}")
        indexed[item_id] = row
    outcomes = []
    for item_id in item_ids:
        row = indexed.get(item_id, {"status": "not_run"})
        source_status = row.get("status", "completed")
        top5 = row.get("top5", [])
        if not isinstance(top5, list) or len(top5) > 5 or any(not isinstance(paper, dict) for paper in top5):
            raise ValueError(f"{item_id}: top5 must contain at most five paper objects")
        status = {"SUCCEEDED": "completed", "PARTIAL": "completed" if len(top5) == 5 else "error",
                  "FAILED": "error", "TIMEOUT": "error", "CANCELLED": "cancelled", "NOT_RUN": "not_run",
                  "EMPTY": "error"}.get(source_status, source_status)
        if status not in {"completed", "error", "not_run", "cancelled"}:
            raise ValueError(f"{item_id}: unsupported status {status!r}")
        if status != "completed" and top5 and source_status not in {"PARTIAL", "FAILED", "TIMEOUT", "CANCELLED"}:
            raise ValueError(f"{item_id}: incomplete runs cannot supply a completed Top 5")
        rank, basis = None, None
        if status == "completed":
            rank = 0
            for position, candidate in enumerate(top5, 1):
                basis = match_candidate(candidate, gold[item_id], track)
                if basis:
                    rank = position
                    break
        outcomes.append({"item_id": item_id, "status": status, "source_status": source_status,
                         "candidate_count": len(top5), "partial_candidates_not_scored": status != "completed" and bool(top5),
                         "target_rank": rank, "match_basis": basis})
    completed = [row for row in outcomes if row["status"] == "completed"]
    attempted = [row for row in outcomes if row["status"] != "not_run"]
    hits = sum(bool(row["target_rank"]) for row in completed)
    def metrics(selected: list[dict]) -> dict | None:
        if not selected:
            return None
        return {"hit_at_1": sum(row["target_rank"] == 1 for row in selected) / len(selected),
                "hit_at_5": hits / len(selected),
                "mrr_at_5": sum(1 / row["target_rank"] if row["target_rank"] else 0 for row in selected) / len(selected)}
    return {
        "track": track, "planned": len(item_ids), "completed": len(completed),
        "not_run": sum(row["status"] == "not_run" for row in outcomes),
        "operational_errors": sum(row["status"] == "error" for row in outcomes),
        "cancelled": sum(row["status"] == "cancelled" for row in outcomes), "hits": hits,
        "complete": len(completed) == len(item_ids),
        "completed_metrics": metrics(completed), "end_to_end_attempted_metrics": metrics(attempted),
        "headline_metrics": metrics(attempted) if len(attempted) == len(item_ids) else None,
        "identity_policy": "Exact DOI, accepted arXiv record or normalized exact title; no fuzzy semantic matching.",
        "limitations": ["Title-only matches and arXiv historical versions still require identity adjudication.",
                        "The designated source is a known positive, not necessarily the only relevant paper.",
                        "Missing runs are NOT_RUN; errors and cancellations are operational outcomes, not semantic misses.",
                        "Backend PARTIAL outputs are scored only when all five validated rank slots exist.",
                        "No ranks or successes are imputed for absent external results."],
        "outcomes": outcomes,
    }


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", choices=["easy", "medium", "mdeium", "hard"], required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--items", nargs="*", help="Only evaluate these IDs; default is all 30")
    parser.add_argument("--track", choices=["designated", "family"], default="designated")
    parser.add_argument("--datasets-root", type=Path, default=root / "datasets")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    tier = "medium" if args.tier == "mdeium" else args.tier
    gold_rows = read_rows(args.datasets_root / tier / "gold.jsonl")
    gold = {row["item_id"]: row for row in gold_rows}
    try:
        report = score(read_rows(args.results), gold, args.items or sorted(gold), args.track)
    except (ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"Error: {exc}\n")
    report["tier"] = tier
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8")
        print(f"Saved {args.out}")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
