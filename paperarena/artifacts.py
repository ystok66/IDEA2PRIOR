"""Read only registered, bounded job outputs; never arbitrary local paths."""
import hashlib
import mimetypes
from pathlib import Path

MAX_ARTIFACT_BYTES = 8 * 1024 * 1024


def artifact_paths(state):
    root_value = state.get("output_dir")
    if not root_value:
        return {}
    root = Path(root_value).resolve()
    candidates = list(state.get("report_files") or [])
    if state.get("extension_result"):
        candidates.extend(str(root / name) for name in ("document.md", "extension.json"))
    found = {}
    for value in candidates:
        path = Path(value).resolve()
        if not path.is_relative_to(root) or not path.is_file() or path.suffix not in (".md", ".json", ".txt"):
            continue
        if path.stat().st_size > MAX_ARTIFACT_BYTES:
            continue
        key = hashlib.sha256(str(path.relative_to(root)).encode()).hexdigest()[:20]
        found[key] = path
    return found


def artifact_catalog(state):
    return [{"id": key, "name": path.name, "bytes": path.stat().st_size,
             "media_type": mimetypes.guess_type(path.name)[0] or "text/plain"}
            for key, path in artifact_paths(state).items()]


def read_artifact(state, key):
    path = artifact_paths(state)[key]
    payload = path.read_bytes()
    if len(payload) > MAX_ARTIFACT_BYTES:
        raise ValueError("Artifact exceeds the size limit")
    return path.name, payload
