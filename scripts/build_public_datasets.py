#!/usr/bin/env python3
"""Verify packaged benchmarks, or rebuild them from supplied original sources.

Python standard library only. Source packages can live outside the public checkout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path


TIERS = {
    "easy": ("Idea2Paper_Niche30_v0_2/Idea2Paper_Niche30_v0_2", "0.2", "A", "N"),
    "medium": ("Idea2Paper_Fuzzy30_v0_5/Idea2Paper_Fuzzy30_v0_5", "0.5", "A", "C"),
    "hard": ("Idea2Paper_Fuzzy30_v0_5/Idea2Paper_Fuzzy30_v0_5", "0.5", "B", "C"),
}
GOLD_FIELDS = (
    "title", "authors", "venue", "publication_year", "field", "doi", "accepted_dois",
    "arxiv_id", "accepted_arxiv_ids", "arxiv_status", "publication_url", "sources",
    "ambiguity_note", "family_aliases", "arxiv_alias_policy",
    "conceptual_alternatives_are_positive_qrels", "metadata_checked_on", "metadata_check_date",
    "metadata_provenance", "global_uniqueness", "discriminability_status",
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records), encoding="utf-8")


def build_tier(source_root: Path, output_root: Path, name: str) -> dict:
    relative_source, version, variant, prefix = TIERS[name]
    source = source_root / relative_source
    query_source = source / ("queries_A_blind.jsonl" if version == "0.2" else "queries_blind.jsonl")
    source_queries = read_jsonl(query_source)
    selected = [row for row in source_queries if version == "0.2" or row["variant"] == variant]
    expected = {f"{prefix}{i:03}" for i in range(1, 31)}
    ids = [row.get("item_id", row.get("id")) for row in selected]
    if len(ids) != 30 or set(ids) != expected:
        raise ValueError(f"{name}: expected exactly 30 unique {prefix}001–{prefix}030 queries")
    original_gold = {row.get("item_id", row.get("id")): row for row in read_jsonl(source / "gold.jsonl")}
    output = output_root / name
    (output / "prompts").mkdir(parents=True, exist_ok=True)
    blind_rows, gold_rows, item_manifest = [], [], []
    for raw, item_id in zip(selected, ids):
        query = raw["query"]
        if query != original_gold[item_id][f"query_{variant}"]:
            raise ValueError(f"{name}/{item_id}: blind query differs from original reference metadata")
        source_prompt = source / f"prompts_{variant}" / f"{item_id}.txt"
        prompt_bytes = source_prompt.read_bytes()
        prompt_text = prompt_bytes.decode("utf-8-sig")
        if "Research idea:" not in prompt_text or prompt_text.rsplit("Research idea:", 1)[1].strip() != query:
            raise ValueError(f"{name}/{item_id}: original prompt does not contain the exact query")
        destination = output / "prompts" / f"{item_id}.txt"
        shutil.copyfile(source_prompt, destination)
        query_hash = sha256(query.encode("utf-8"))
        prompt_hash = sha256(prompt_bytes)
        blind_rows.append({"item_id": item_id, "query": query, "prompt_file": f"prompts/{item_id}.txt",
                           "query_sha256": query_hash, "prompt_sha256": prompt_hash})
        gold_row = {"item_id": item_id}
        gold_row.update({key: original_gold[item_id][key] for key in GOLD_FIELDS if key in original_gold[item_id]})
        gold_row["group"] = original_gold[item_id].get("primary_group", original_gold[item_id].get("group"))
        gold_row["source_version"] = version
        gold_row["source_variant"] = variant
        gold_rows.append(gold_row)
        item_manifest.append({"item_id": item_id, "query_sha256": query_hash, "prompt_sha256": prompt_hash,
                              "prompt_file": f"prompts/{item_id}.txt"})
    write_jsonl(output / "queries.jsonl", blind_rows)
    write_jsonl(output / "gold.jsonl", gold_rows)
    payload_hashes = {path.relative_to(output).as_posix(): sha256(path.read_bytes())
                      for path in sorted(output.rglob("*")) if path.is_file() and path.name != "manifest.json"}
    manifest = {
        "schema_version": 1, "difficulty": name.upper(), "item_count": 30,
        "source_version": version, "source_variant": variant, "source_package": relative_source,
        "source_query_file": query_source.name, "source_query_file_sha256": sha256(query_source.read_bytes()),
        "source_gold_file_sha256": sha256((source / "gold.jsonl").read_bytes()),
        "query_file_sha256": payload_hashes["queries.jsonl"], "gold_file_sha256": payload_hashes["gold.jsonl"],
        "difficulty_status": "Intended query difficulty; not empirically calibrated across all 30 items.",
        "input_preservation": "Prompt bytes and query strings are copied without rewriting.",
        "gold_policy": "Reference identities are evaluation-only; never include gold.jsonl in retrieval input.",
        "items": item_manifest, "files_sha256": payload_hashes,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"tier": name, "items": len(blind_rows), "queries_sha256": manifest["query_file_sha256"]}


def verify_tier(output_root: Path, name: str) -> dict:
    output = output_root / name
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    rows = read_jsonl(output / "queries.jsonl")
    if len(rows) != manifest["item_count"] or len({row["item_id"] for row in rows}) != len(rows):
        raise ValueError(f"{name}: missing or duplicate query records")
    for relative, expected in manifest["files_sha256"].items():
        path = (output / relative).resolve()
        if not path.is_relative_to(output.resolve()):
            raise ValueError(f"{name}: invalid manifest path")
        if sha256(path.read_bytes()) != expected:
            raise ValueError(f"{name}: file hash mismatch: {relative}")
    for row in rows:
        if sha256(row["query"].encode("utf-8")) != row["query_sha256"]:
            raise ValueError(f"{name}/{row['item_id']}: query hash mismatch")
        if sha256((output / row["prompt_file"]).read_bytes()) != row["prompt_sha256"]:
            raise ValueError(f"{name}/{row['item_id']}: prompt hash mismatch")
    return {"tier": name, "items": len(rows), "status": "verified"}


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", "--source-dir", dest="source_root", type=Path,
                        help="Rebuild from original versioned packages in this supplied directory; otherwise verify packaged data")
    parser.add_argument("--output-root", type=Path, default=root / "datasets")
    args = parser.parse_args()
    for name in TIERS:
        if args.source_root:
            result = build_tier(args.source_root.resolve(), args.output_root.resolve(), name)
        else:
            result = verify_tier(args.output_root.resolve(), name)
        print(json.dumps(result))


if __name__ == "__main__":
    main()
