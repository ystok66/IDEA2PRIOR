"""Local browser interface. API credentials stay in memory and worker stdin."""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from paperarena import APIProfile, JobManager, RunConfig
from paperarena.model_catalog import fetch_models, ModelCatalogError
from paperarena.diagnostics import safe_error
from paperarena.resources import benchmark_directory
from paperarena.__main__ import dataset_query

ROOT = Path(__file__).resolve().parents[1]
ASSETS = Path(__file__).parent / "web_assets"
MAX_BODY = 128 * 1024
ASSET_TYPES = {"/": ("index.html", "text/html; charset=utf-8"),
               "/app.js": ("app.js", "text/javascript; charset=utf-8"),
               "/styles.css": ("styles.css", "text/css; charset=utf-8")}


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
                    self.json_response(200, {"datasets": catalog()})
                elif path.startswith("/api/jobs/") and path.count("/") == 3:
                    job = manager.get(path.rsplit("/", 1)[-1])
                    self.json_response(200, job)
                else:
                    self.json_response(404, {"error": "Not found."})
            except (KeyError, FileNotFoundError):
                self.json_response(404, {"error": "Item not found."})
            except Exception:
                self.json_response(500, {"error": "Unable to read local state."})

        def do_POST(self):
            if not self.local_request():
                return
            path = urlsplit(self.path).path
            try:
                if path.startswith("/api/jobs/") and path.endswith("/cancel") and path.count("/") == 4:
                    job_id = path.split("/")[3]
                    self.json_response(200, {"cancelled": manager.cancel(job_id)})
                    return
                if path not in {"/api/jobs", "/api/models"}:
                    self.json_response(404, {"error": "Not found."})
                    return
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    self.json_response(415, {"error": "Use application/json."})
                    return
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > MAX_BODY:
                    self.json_response(413, {"error": "Invalid request size."})
                    return
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("Request must be an object.")
                if path == "/api/models":
                    profile = APIProfile.from_dict(payload.get("profile", {}))
                    models = fetch_models(profile, timeout_seconds=15.0, max_pages=5)
                    self.json_response(200, {"models": models})
                    return
                query = payload.get("query", "").strip()
                if not query or len(query) > 20_000:
                    raise ValueError("Enter a research idea of at most 20,000 characters.")
                topic = None
                benchmark = payload.get("benchmark")
                if benchmark is not None:
                    if not isinstance(benchmark, dict) or set(benchmark) != {"difficulty", "item_id"}:
                        raise ValueError("Invalid benchmark selection.")
                    row = dataset_query(benchmark["difficulty"], benchmark["item_id"])
                    if query == row["query"].strip():
                        topic = row["query"].split(". ", 1)[0].rstrip(".")
                        query = row["prompt"]
                config = RunConfig.from_dict(payload.get("config", {}))
                timeout = payload.get("timeout_seconds", 1800)
                if type(timeout) is not int or not 30 <= timeout <= 7200:
                    raise ValueError("Time limit must be between 30 and 7,200 seconds.")
                if type(payload.get("dry_run", False)) is not bool:
                    raise ValueError("dry_run must be a boolean.")
                job_id = manager.submit(query, config, timeout_seconds=timeout,
                                        topic=topic, dry_run=payload.get("dry_run", False))
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
