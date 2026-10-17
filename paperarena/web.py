"""Local browser interface. API credentials stay in memory and worker stdin."""
from __future__ import annotations

import argparse
import base64
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
import uuid

from paperarena import APIProfile, JobManager, RunConfig
from paperarena.model_catalog import fetch_models, ModelCatalogError
from paperarena.diagnostics import safe_error
from paperarena.resources import benchmark_directory
from paperarena.__main__ import dataset_query
from paperarena.extensions import EXTENSIONS, validate_extension
from paperarena.artifacts import artifact_catalog, read_artifact

ROOT = Path(__file__).resolve().parents[1]
ASSETS = Path(__file__).parent / "web_assets"
MAX_BODY = 128 * 1024
ASSET_TYPES = {"/": ("index.html", "text/html; charset=utf-8"),
               "/app.js": ("app.js", "text/javascript; charset=utf-8"),
               "/styles.css": ("styles.css", "text/css; charset=utf-8")}
ASSET_TYPES["/extensions.js"] = ("extensions.js", "text/javascript; charset=utf-8")
ASSET_TYPES["/workspace.js"] = ("workspace.js", "text/javascript; charset=utf-8")
ASSET_TYPES["/renderer.js"] = ("renderer.js", "text/javascript; charset=utf-8")


def catalog():
    """Read blind inputs only. Gold files are never served or loaded here."""
    result = []
    for difficulty in ("easy", "medium", "hard"):
        directory = benchmark_directory() / difficulty
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        rows = [json.loads(line) for line in (directory / "queries.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        result.append({"difficulty": difficulty, "label": difficulty.upper(),
                       "source_version": manifest.get("source_version"),
                       "items": [{"item_id": row["item_id"], "query": row["query"]}
                                 for row in sorted(rows, key=lambda r: r["item_id"])]})
    return result


def create_server(host="127.0.0.1", port=7860, *, manager=None, output_root="outputs"):
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("This local interface binds to 127.0.0.1 or localhost.")
    manager = manager or JobManager(output_root=output_root, max_active_jobs=1)

    class Handler(BaseHTTPRequestHandler):
        server_version = "Idea2Prior"

        def log_message(self, fmt, *args):
            # Request bodies, query strings and credentials are never logged.
            return

        def headers_for(self, content_type):
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")

        def json_response(self, status, body):
            payload = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.headers_for("application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def local_request(self):
            authority = self.headers.get("Host", "")
            valid = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            if authority not in valid:
                self.json_response(403, {"error": "Invalid local Host header."})
                return False
            origin = self.headers.get("Origin")
            if origin and origin not in {"http://" + h for h in valid}:
                self.json_response(403, {"error": "Cross-origin requests are not accepted."})
                return False
            return True

        def do_GET(self):
            if not self.local_request():
                return
            path = urlsplit(self.path).path
            try:
                if path in ASSET_TYPES:
                    filename, content_type = ASSET_TYPES[path]
                    payload = (ASSETS / filename).read_bytes()
                    self.send_response(200)
                    self.headers_for(content_type)
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                elif path == "/api/catalog":
                    self.json_response(200, {"datasets": catalog(), "extensions": EXTENSIONS})
                elif path.startswith("/api/jobs/") and "/artifacts/" in path:
                    parts = path.strip("/").split("/")
                    if len(parts) != 5 or parts[3] != "artifacts":
                        raise KeyError("Invalid artifact")
                    name, payload = read_artifact(manager.get(parts[2]), parts[4])
                    self.send_response(200)
                    self.headers_for("text/plain; charset=utf-8")
                    self.send_header("Content-Disposition", 'attachment; filename="' + name.replace('"', '') + '"')
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                elif path.startswith("/api/jobs/") and path.count("/") == 3:
                    job = manager.get(path.rsplit("/", 1)[-1])
                    job["artifacts"] = artifact_catalog(job)
                    job["benchmark_evaluation_available"] = job.get("job_id", path.rsplit("/", 1)[-1]) in getattr(manager, "benchmark_selections", {})
                    self.json_response(200, job)
                else:
                    self.json_response(404, {"error": "Not found."})
            except (KeyError, FileNotFoundError):
                self.json_response(404, {"error": "Item not found."})
            except Exception:
                self.json_response(500, {"error": "Unable to read local state."})

        def do_POST(self):
            path = urlsplit(self.path).path
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self.json_response(400, {"error": "Invalid request size."})
                return
            if length < 0 or length > (17 * 1024 * 1024 if path == "/api/documents" else MAX_BODY):
                self.json_response(413, {"error": "Invalid request size."})
                return
            # Drain even rejected requests so Windows does not reset the response
            # while closing a connection with unread POST bytes.
            body = self.rfile.read(length) if length else b""
            if not self.local_request():
                return
            try:
                if path.startswith("/api/jobs/") and path.endswith("/cancel") and path.count("/") == 4:
                    job_id = path.split("/")[3]
                    self.json_response(200, {"cancelled": manager.cancel(job_id)})
                    return
                if path not in {"/api/jobs", "/api/models", "/api/documents", "/api/evaluate"}:
                    self.json_response(404, {"error": "Not found."})
                    return
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    self.json_response(415, {"error": "Use application/json."})
                    return
                if length <= 0 or length > (17 * 1024 * 1024 if path == "/api/documents" else MAX_BODY):
                    self.json_response(413, {"error": "Invalid request size."})
                    return
                payload = json.loads(body)
                if not isinstance(payload, dict):
                    raise ValueError("Request must be an object.")
                if path == "/api/evaluate":
                    from .evaluation import read_rows, score
                    job = manager.get(payload.get("job_id"))
                    if job["status"] not in ("SUCCEEDED", "PARTIAL", "FAILED", "EMPTY", "TIMEOUT", "CANCELLED"):
                        raise ValueError("Evaluate only a finished retrieval")
                    selection = getattr(manager, "benchmark_selections", {}).get(payload.get("job_id"))
                    if not selection or job.get("operation") == "extension":
                        raise ValueError("This job is not an unchanged benchmark retrieval")
                    tier, item = selection
                    gold = {r["item_id"]: r for r in read_rows(benchmark_directory() / tier / "gold.jsonl")}
                    report = score([{**job, "item_id": item}], gold, [item])
                    self.json_response(200, report)
                    return
                if path == "/api/documents":
                    suffix = Path(payload.get("filename", "")).suffix.lower()
                    if suffix not in (".pdf", ".md", ".txt") or not isinstance(payload.get("base64"), str):
                        raise ValueError("Upload a PDF, Markdown or text document.")
                    try:
                        content = base64.b64decode(payload["base64"], validate=True)
                    except Exception:
                        raise ValueError("Invalid document encoding") from None
                    if not content or len(content) > 12 * 1024 * 1024:
                        raise ValueError("Document must be between 1 byte and 12 MiB.")
                    uploads = Path(getattr(manager, "output_root", output_root)).resolve() / ".uploads"
                    uploads.mkdir(parents=True, exist_ok=True)
                    document = uploads / (uuid.uuid4().hex + suffix)
                    document.write_bytes(content)
                    self.json_response(201, {"document_path": str(document), "bytes": len(content)})
                    return
                if path == "/api/models":
                    profile = APIProfile.from_dict(payload.get("profile", {}))
                    model_options = {"purpose": payload["purpose"]} if "purpose" in payload else {}
                    models = fetch_models(profile, timeout_seconds=15.0, max_pages=5, **model_options)
                    self.json_response(200, {"models": models})
                    return
                query = payload.get("query", "").strip()
                if not query or len(query) > 20_000:
                    raise ValueError("Enter a research idea of at most 20,000 characters.")
                topic = None
                extension = payload.get("extension")
                if extension is not None:
                    if not isinstance(extension, dict) or set(extension) - {"name", "parameters"}:
                        raise ValueError("Invalid extension selection")
                    extension = validate_extension(extension.get("name"), extension.get("parameters"))
                benchmark = payload.get("benchmark")
                if benchmark is not None and extension is not None:
                    raise ValueError("Benchmark retrieval and extension execution are separate tasks")
                if benchmark is not None:
                    if not isinstance(benchmark, dict) or set(benchmark) != {"difficulty", "item_id"}:
                        raise ValueError("Invalid benchmark selection.")
                    row = dataset_query(benchmark["difficulty"], benchmark["item_id"])
                    if query == row["query"].strip():
                        topic = row["query"].split(". ", 1)[0].rstrip(".")
                        query = row["prompt"]
                    else:
                        benchmark = None
                config = RunConfig.from_dict(payload.get("config", {}))
                timeout = payload.get("timeout_seconds", 1800)
                if type(timeout) is not int or not 30 <= timeout <= 7200:
                    raise ValueError("Time limit must be between 30 and 7,200 seconds.")
                if type(payload.get("dry_run", False)) is not bool:
                    raise ValueError("dry_run must be a boolean.")
                options = {"extension": extension} if extension else {}
                job_id = manager.submit(query, config, timeout_seconds=timeout,
                                        topic=topic, dry_run=payload.get("dry_run", False), **options)
                if benchmark is not None:
                    if not hasattr(manager, "benchmark_selections"):
                        manager.benchmark_selections = {}
                    tier = benchmark["difficulty"].lower()
                    selection = ("medium" if tier == "mdeium" else tier, row["item_id"])
                    if hasattr(manager, "register_benchmark"):
                        manager.register_benchmark(job_id, *selection)
                    else:
                        manager.benchmark_selections[job_id] = selection
                self.json_response(202, {"job_id": job_id})
            except ModelCatalogError as exc:
                self.json_response(400, {"error": str(exc), "code": exc.code})
            except KeyError:
                self.json_response(404, {"error": "Job not found."})
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                # Never echo a credentials-bearing configuration in an error.
                detail = safe_error(exc, "configuration")
                self.json_response(400, {"error": detail["message"], **{k: v for k, v in detail.items() if k != "message"}})
            except Exception:
                self.json_response(409, {"error": "Unable to start or cancel this job. Check the current task."})

    server = ThreadingHTTPServer((host, port), Handler)
    server.manager = manager
    return server


def serve(host="127.0.0.1", port=7860, output_root="outputs"):
    server = create_server(host, port, output_root=output_root)
    print(f"Idea2Prior: http://{host}:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.manager.shutdown()
        server.server_close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Start the local Idea2Prior browser interface.")
    parser.add_argument("--host", choices=("127.0.0.1", "localhost"), default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument("--output-dir", default="outputs")
    args = parser.parse_args(argv)
    serve(args.host, args.port, args.output_dir)


if __name__ == "__main__":
    main()
