#!/usr/bin/env python3
"""Offline release checks that report locations without printing secret values.

By default, inspect files Git would publish. No network requests, edits, or
history rewriting occur. Use --include-ignored for a working-tree inspection.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Iterable


TEXT_SUFFIXES = {
    ".py", ".md", ".rst", ".txt", ".json", ".jsonl", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".html", ".css", ".js", ".ts", ".sh",
    ".ps1", ".cmd", ".bat", ".ipynb", ".example", ".env", ".gitignore",
}
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules"}
PRIVATE_PATH = re.compile(
    r"(?i)(?:[A-Z]:[\\/](?:Users|PaperArena)[\\/]|"
    + "/" + r"Users/[^/\s]+/|" + "/" + r"home/[^/\s]+/)"
)
TOKEN = re.compile(r"(?<![A-Za-z0-9])(?:sk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{25,})(?![A-Za-z0-9_-])")
KEY_ASSIGNMENT = re.compile(
    r"(?i)\b(?:api[_-]?key|access[_-]?token|secret[_-]?key|password)\b"
    r"[\"']?\s*[:=]\s*[\"']([^\"'\r\n]{12,})[\"']"
)


def _git_command(root: Path, *args: str) -> list[str]:
    # Scope trust to this explicit local root; never change global Git settings.
    return ["git", "-c", f"safe.directory={root.resolve().as_posix()}", *args]


def _placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(word in lowered for word in (
        "example", "placeholder", "your-key", "your_key", "your-api",
        "your_api", "replace-me", "replace_me", "not-a-real", "dummy", "test-key",
    )) or value.startswith("${") or re.fullmatch(r"[A-Z0-9_]+", value) is not None


def _location(path: str, text: str, start: int, code: str) -> dict:
    return {"path": path, "line": text.count("\n", 0, start) + 1, "code": code}


def scan_text(
    text: str,
    path: str,
    *,
    forbidden_terms: Iterable[str] = (),
    secret_values: Iterable[str] = (),
) -> list[dict]:
    """Return redacted finding locations; no matched text is included."""
    findings = []
    lower = text.casefold()
    for term in forbidden_terms:
        offset = lower.find(term.casefold())
        if term and offset >= 0:
            findings.append(_location(path, text, offset, "removed_provider_name"))
    for match in PRIVATE_PATH.finditer(text):
        findings.append(_location(path, text, match.start(), "private_absolute_path"))
    for pattern in (TOKEN, KEY_ASSIGNMENT):
        for match in pattern.finditer(text):
            value = match.group(1) if pattern is KEY_ASSIGNMENT else match.group(0)
            if not _placeholder(value):
                findings.append(_location(path, text, match.start(), "possible_hardcoded_secret"))
    for value in secret_values:
        if value and (offset := text.find(value)) >= 0:
            findings.append(_location(path, text, offset, "known_secret_value"))
    return findings


def scan_notebook(text: str, path: str) -> list[dict]:
    findings = []
    try:
        notebook = json.loads(text)
    except (ValueError, TypeError):
        return [{"path": path, "line": 1, "code": "invalid_notebook_json"}]
    for index, cell in enumerate(notebook.get("cells", []), 1):
        if cell.get("outputs"):
            findings.append({"path": path, "cell": index, "code": "notebook_has_outputs"})
        if cell.get("cell_type") == "code" and cell.get("execution_count") is not None:
            findings.append({"path": path, "cell": index, "code": "notebook_executed_cell"})
    return findings


def candidate_paths(root: Path, include_ignored: bool = False) -> list[Path]:
    if not include_ignored and (root / ".git").exists():
        result = subprocess.run(
            _git_command(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard"),
            cwd=root, capture_output=True, check=True,
        )
        names = set(result.stdout.decode("utf-8", errors="strict").split("\0")) - {""}
        return sorted(root / name for name in names if (root / name).is_file())
    paths = []
    for current, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
        paths.extend(Path(current) / name for name in files)
    return sorted(paths)


def read_secret_values(env_path: Path | None) -> list[str]:
    if env_path is None:
        return []
    secrets = []
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip("\"'")
        if re.search(r"(?i)(key|token|secret|password)", key) and len(value) >= 12:
            if not _placeholder(value):
                secrets.append(value)
    return secrets


def audit_repository(
    root: Path,
    *,
    include_ignored: bool = False,
    forbidden_terms: Iterable[str] | None = None,
    secret_values: Iterable[str] = (),
) -> dict:
    root = root.resolve()
    terms = tuple(forbidden_terms if forbidden_terms is not None else ("".join(("liao", "bot")),))
    findings, scanned, binary = [], 0, 0
    for path in candidate_paths(root, include_ignored):
        relative = path.relative_to(root).as_posix()
        name, suffix = path.name.lower(), path.suffix.lower()
        if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
            findings.append({"path": relative, "line": 1, "code": "private_environment_file"})
            continue
        if suffix in {".key", ".pem", ".p12", ".pfx"}:
            findings.append({"path": relative, "line": 1, "code": "private_key_file"})
            continue
        if suffix in {".pdf", ".zip", ".log"}:
            findings.append({"path": relative, "line": 1, "code": "private_run_artifact"})
            continue
        if suffix not in TEXT_SUFFIXES and name not in {".gitignore", "license", "dockerfile", "makefile"}:
            binary += 1
            continue
        if path.stat().st_size > 10_000_000:
            findings.append({"path": relative, "line": 1, "code": "text_exceeds_audit_limit"})
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeError:
            findings.append({"path": relative, "line": 1, "code": "text_encoding_not_utf8"})
            continue
        scanned += 1
        findings.extend(scan_text(text, relative, forbidden_terms=terms, secret_values=secret_values))
        if suffix == ".ipynb":
            findings.extend(scan_notebook(text, relative))
    unique = {json.dumps(finding, sort_keys=True): finding for finding in findings}
    return {
        "status": "PASS" if not unique else "BLOCKED",
        "scope": "working_tree" if include_ignored else "git_publish_candidates",
        "text_files_scanned": scanned,
        "other_files_skipped": binary,
        "findings": sorted(unique.values(), key=lambda row: (row["path"], row.get("line", 0), row.get("cell", 0), row["code"])),
        "history_checked": False,
    }


def audit_history(root: Path, secret_values: Iterable[str], forbidden_terms: Iterable[str]) -> dict:
    """Read historical text blobs; emit only counts, paths, and finding codes."""
    objects = subprocess.run(
        _git_command(root, "rev-list", "--objects", "--all"), cwd=root, capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    paths = {}
    for line in objects:
        parts = line.split(" ", 1)
        if len(parts) == 2:
            oid, name = parts
            if Path(name).suffix.lower() in TEXT_SUFFIXES or Path(name).name in {".env", ".gitignore", "LICENSE"}:
                paths[oid] = name
    if not paths:
        return {"text_blobs_scanned": 0, "findings": []}
    types = subprocess.run(
        _git_command(root, "cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"),
        cwd=root, input="\n".join(paths) + "\n", capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    ids = [line.split()[0] for line in types if len(line.split()) == 3 and line.split()[1] == "blob" and int(line.split()[2]) <= 10_000_000]
    if not ids:
        return {"text_blobs_scanned": 0, "findings": []}
    data = subprocess.run(
        _git_command(root, "cat-file", "--batch"), cwd=root,
        input=("\n".join(ids) + "\n").encode(), capture_output=True, check=True,
    ).stdout
    offset, findings = 0, []
    for _ in ids:
        end = data.index(b"\n", offset)
        header = data[offset:end].decode().split()
        oid, size = header[0], int(header[2])
        offset = end + 1
        text = data[offset:offset + size].decode("utf-8", errors="replace")
        offset += size + 1
        for finding in scan_text(text, paths[oid], forbidden_terms=forbidden_terms, secret_values=secret_values):
            findings.append({**finding, "blob": oid})
    return {"text_blobs_scanned": len(ids), "findings": findings}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--include-ignored", action="store_true")
    parser.add_argument("--history", action="store_true", help="Also check existing Git history; never rewrite it.")
    parser.add_argument("--secret-env", type=Path, help="Read known keys privately for exact-match detection; values are never printed.")
    parser.add_argument("--json", action="store_true", help="Emit the redacted report as JSON.")
    args = parser.parse_args()
    secret_values = read_secret_values(args.secret_env)
    terms = ("".join(("liao", "bot")),)
    report = audit_repository(args.root, include_ignored=args.include_ignored, secret_values=secret_values, forbidden_terms=terms)
    if args.history:
        report["history"] = audit_history(args.root.resolve(), secret_values, terms)
        report["history_checked"] = True
        if report["history"]["findings"]:
            report["status"] = "BLOCKED"
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"Release audit: {report['status']} ({report['text_files_scanned']} text files)")
        for finding in report["findings"]:
            place = f"cell {finding['cell']}" if "cell" in finding else str(finding["line"])
            print(f"{finding['path']}:{place}: {finding['code']}")
        if args.history:
            print(f"Historical text blobs: {report['history']['text_blobs_scanned']}")
            for finding in report["history"]["findings"]:
                print(f"history {finding['path']}:{finding['line']}: {finding['code']}")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
