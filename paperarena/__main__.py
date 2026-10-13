"""Run ``python -m paperarena --help`` for CLI usage."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from . import RunConfig, run_retrieval, run_extension


def dataset_query(difficulty: str, item: str, dataset_root: str | Path | None = None) -> dict:
    difficulty = difficulty.lower()
    if difficulty == "mdeium":
        difficulty = "medium"
    if difficulty not in ("easy", "medium", "hard"):
        raise ValueError("dataset must be EASY, MEDIUM, or HARD")
    if dataset_root is None:
        from .resources import benchmark_directory
        directory = Path(benchmark_directory(difficulty)).resolve()
    else:
        directory = (Path(dataset_root) / difficulty).resolve()
    rows = [json.loads(line) for line in (directory / "queries.jsonl").read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    matching = [row for row in rows if row["item_id"].casefold() == item.casefold()]
    if len(matching) != 1:
        raise ValueError("Requested item ID is not present in the blind dataset")
    # Load only the selected blind prompt, preserving the historical benchmark
    # input bytes. Gold and other targets never enter runtime memory.
    row = dict(matching[0])
    query_hash = hashlib.sha256(row["query"].encode("utf-8")).hexdigest()
    if row.get("query_sha256") != query_hash:
        raise ValueError("Blind query hash does not match its dataset manifest")
    prompt_path = (directory / row["prompt_file"]).resolve()
    if not prompt_path.is_relative_to(directory):
        raise ValueError("Prompt path escapes the selected blind dataset")
    prompt_bytes = prompt_path.read_bytes()
    if hashlib.sha256(prompt_bytes).hexdigest() != row.get("prompt_sha256"):
        raise ValueError("Blind prompt hash does not match its dataset manifest")
    row["prompt"] = prompt_bytes.decode("utf-8")
    return row


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(description="Idea2Prior scholarly retrieval and Top 5 ranking")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Run one query or one blind dataset item")
    source = run.add_mutually_exclusive_group(required=True)
    source.add_argument("--query")
    source.add_argument("--dataset", help="EASY, MEDIUM, or HARD (MDEIUM is accepted)")
    run.add_argument("--item", help="N001–N030 for EASY; C001–C030 for MEDIUM/HARD")
    run.add_argument("--dataset-root", type=Path)
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--output-dir", type=Path, default=Path("outputs"))
    run.add_argument("--timeout-seconds", type=float, default=1800)
    run.add_argument("--mode", choices=("retrieval_eval", "full_report"))
    run.add_argument("--dry-run", action="store_true", help="Validate and save a plan without network calls")
    extension = sub.add_parser("extension", help="Run an independent optional research task")
    from .extensions import EXTENSIONS
    extension.add_argument("--name", choices=tuple(EXTENSIONS), required=True)
    extension.add_argument("--parameters", type=Path, required=True, help="JSON parameter object")
    extension.add_argument("--config", type=Path, required=True)
    extension.add_argument("--output-dir", type=Path, default=Path("outputs/extensions"))
    extension.add_argument("--timeout-seconds", type=float, default=1800)
    extension.add_argument("--dry-run", action="store_true")
    web = sub.add_parser("web", help="Start the local browser interface")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=7860)
    web.add_argument("--output-dir", type=Path, default=Path("outputs"))
    args = parser.parse_args(argv)
    if args.command == "web":
        from .web import serve
        serve(host=args.host, port=args.port, output_root=args.output_dir)
        return 0
    if args.command == "extension":
        try:
            config = RunConfig.from_dict(json.loads(args.config.read_text(encoding="utf-8-sig")))
            parameters = json.loads(args.parameters.read_text(encoding="utf-8-sig"))
            result = run_extension(args.name, config, parameters, args.output_dir,
                                   args.timeout_seconds, dry_run=args.dry_run)
        except Exception as error:
            from .diagnostics import safe_error
            result = {"status": "FAILED", "error": safe_error(error, "extension_configuration")}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] in ("SUCCEEDED", "PARTIAL", "EMPTY", "NOT_RUN") else 1
    try:
        config_dict = json.loads(args.config.read_text(encoding="utf-8-sig"))
        if args.mode:
            config_dict["mode"] = args.mode
        config = RunConfig.from_dict(config_dict)
        item = None
        if args.dataset:
            if not args.item:
                parser.error("--dataset requires --item")
            item = dataset_query(args.dataset, args.item, args.dataset_root)
        elif args.item:
            parser.error("--item requires --dataset")
        result = run_retrieval(item["prompt"] if item else args.query, config, args.output_dir,
                               args.timeout_seconds, dry_run=args.dry_run,
                               topic=item["query"].split(". ", 1)[0].rstrip(".") if item else None)
        if item:
            result["item_id"] = item["item_id"]
            result["difficulty"] = "medium" if args.dataset.lower() == "mdeium" else args.dataset.lower()
            result["benchmark_input"] = {"item_id": item["item_id"], "prompt_file": item["prompt_file"],
                "query_sha256": item["query_sha256"], "prompt_sha256": item["prompt_sha256"],
                "input_representation": "original_filled_blind_prompt", "gold_access": False}
            path = Path(result["output_dir"]) / "result.json"
            path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] in ("SUCCEEDED", "PARTIAL", "EMPTY", "NOT_RUN") else 1
    except (ValueError, OSError, KeyError) as exc:
        # Exception text from a malformed JSON document can echo user content;
        # print only an actionable category, never a config or provider payload.
        print("Configuration/input error (" + type(exc).__name__ + "). Check your config fields, model IDs, environment keys, and dataset item.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
