#!/usr/bin/env python3
"""Verify EASY/MEDIUM/HARD, or copy them from a canonical dataset source.

Python standard library only. A source root contains easy/, medium/, and hard/.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path


TIERS = {"easy": "N", "medium": "C", "hard": "C"}
CONSTRUCTION_PROVENANCE = {
    "authoring_tool": "ChatGPT",
    "authoring_model": "GPT-6 Astra",
    "role": "Extract and generate shared literature-search prompts",
    "source": "maintainer-provided construction record",
    "shared_across_methods": ["Idea2Prior", "ChatGPT Deep Research", "Gemini Deep Research"],
    "input_preservation": "Original prompt bytes and query strings are retained",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def verify_tier(output_root: Path, name: str) -> dict:
    output = (output_root / name).resolve()
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    rows = read_jsonl(output / "queries.jsonl")
    expected = {f"{TIERS[name]}{i:03}" for i in range(1, 31)}
    if len(rows) != 30 or {row["item_id"] for row in rows} != expected:
        raise ValueError(f"{name}: expected 30 unique canonical query IDs")
    if manifest["item_count"] != 30 or manifest["difficulty"] != name.upper():
        raise ValueError(f"{name}: inconsistent tier manifest")
    for relative, expected_hash in manifest["files_sha256"].items():
        path = (output / relative).resolve()
        if not path.is_relative_to(output):
            raise ValueError(f"{name}: invalid manifest path")
        if sha256(path.read_bytes()) != expected_hash:
            raise ValueError(f"{name}: file hash mismatch: {relative}")
    for row in rows:
        prompt = (output / row["prompt_file"]).resolve()
        if not prompt.is_relative_to(output):
            raise ValueError(f"{name}: prompt path escapes the tier")
        if sha256(row["query"].encode("utf-8")) != row["query_sha256"]:
            raise ValueError(f"{name}/{row['item_id']}: query hash mismatch")
        prompt_bytes = prompt.read_bytes()
        if sha256(prompt_bytes) != row["prompt_sha256"]:
            raise ValueError(f"{name}/{row['item_id']}: prompt hash mismatch")
        if prompt_bytes.decode("utf-8-sig").rsplit("Research idea:", 1)[1].strip() != row["query"]:
            raise ValueError(f"{name}/{row['item_id']}: prompt and query differ")
    return {"tier": name, "items": len(rows), "status": "verified"}


def build_tier(source_root: Path, output_root: Path, name: str) -> dict:
    source = (source_root / name).resolve()
    output = (output_root / name).resolve()
    verify_tier(source_root, name)
    if source == output:
        return verify_tier(output_root, name)
    if source.is_relative_to(output) or output.is_relative_to(source):
        raise ValueError("Source and output tier directories must not overlap")
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    for relative in [*manifest["files_sha256"], "manifest.json"]:
        original = (source / relative).resolve()
        destination = (output / relative).resolve()
        if not original.is_relative_to(source) or not destination.is_relative_to(output):
            raise ValueError("Dataset copy path escapes its tier")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, destination)
    return verify_tier(output_root, name)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", "--source-dir", dest="source_root", type=Path,
                        help="Source containing easy/, medium/, hard/; omit to verify existing data")
    parser.add_argument("--output-root", type=Path, default=root / "datasets")
    args = parser.parse_args()
    for name in TIERS:
        result = build_tier(args.source_root.resolve(), args.output_root.resolve(), name) if args.source_root else verify_tier(args.output_root.resolve(), name)
        print(json.dumps(result))


if __name__ == "__main__":
    main()
