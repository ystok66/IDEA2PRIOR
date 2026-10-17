import json
import threading
import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from paperarena.web import create_server
from paperarena.model_catalog import ModelCatalogError


class FakeManager:
    def __init__(self):
        self.jobs = {}

    def submit(self, query, config, **kwargs):
        self.jobs["job1"] = {"job_id": "job1", "status": "NOT_RUN", "top5": [], "config": config.to_public_dict(), "query": query}
        return "job1"

    def get(self, job_id):
        return self.jobs[job_id]

    def cancel(self, job_id):
        self.jobs[job_id]["status"] = "CANCELLED"
        return True

    def shutdown(self):
        pass


class LocalWebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manager = FakeManager()
        cls.server = create_server(port=0, manager=cls.manager)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join(timeout=3)

    def request(self, path, data=None, headers=None):
        request = Request(self.base + path, data=json.dumps(data).encode() if data is not None else None,
                          headers=headers or ({"Content-Type": "application/json"} if data is not None else {}))
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, response.read(), response.headers
        except HTTPError as response:
            return response.code, response.read(), response.headers

    def test_assets_and_headers(self):
        for path in ["/", "/styles.css", "/app.js", "/extensions.js", "/workspace.js", "/renderer.js"]:
            status, body, headers = self.request(path)
            self.assertEqual(status, 200); self.assertTrue(body)
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

    def test_registered_artifact_download_and_job_isolation(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);report=root/"report.md";report.write_text("# Safe report",encoding="utf-8")
            self.manager.jobs["attachment-job"]={"job_id":"attachment-job","status":"SUCCEEDED","output_dir":str(root),"report_files":[str(report)]}
            status,body,_=self.request("/api/jobs/attachment-job")
            self.assertEqual(status,200)
            artifact=json.loads(body)["artifacts"][0]
            status,body,headers=self.request("/api/jobs/attachment-job/artifacts/"+artifact["id"])
            self.assertEqual(status,200)
            self.assertEqual(body,b"# Safe report")
            self.assertIn("attachment",headers["Content-Disposition"])
            self.assertEqual(self.request("/api/jobs/attachment-job/artifacts/../../.env")[0],404)
            self.manager.jobs["empty-job"]={"status":"SUCCEEDED","output_dir":str(root),"report_files":[]}
            self.assertEqual(self.request("/api/jobs/empty-job/artifacts/"+artifact["id"])[0],404)
        self.manager.jobs.pop("attachment-job",None);self.manager.jobs.pop("empty-job",None)

    def test_posthoc_evaluation_requires_unchanged_finished_benchmark(self):
        from paperarena.__main__ import dataset_query
        item=dataset_query("easy","N001")
        data={"query":item["query"],"dry_run":True,"benchmark":{"difficulty":"easy","item_id":"N001"},
              "config":{"profiles":{"a":{"provider":"openai","model":"offline"}}}}
        self.assertEqual(self.request("/api/jobs",data)[0],202)
        self.assertEqual(self.request("/api/evaluate",{"job_id":"job1"})[0],400)
        self.manager.jobs["job1"]["status"]="FAILED"
        status,body,_=self.request("/api/evaluate",{"job_id":"job1"})
        self.assertEqual(status,200,body)
        self.assertEqual(json.loads(body)["operational_errors"],1)
        self.manager.benchmark_selections.pop("job1",None)
        data["query"]="Edited idea"
        self.assertEqual(self.request("/api/jobs",data)[0],202)
        self.manager.jobs["job1"]["status"]="SUCCEEDED"
        self.assertEqual(self.request("/api/evaluate",{"job_id":"job1"})[0],400)

    def test_catalog_has_blind_queries_only(self):
        status, body, _ = self.request("/api/catalog")
        self.assertEqual(status, 200)
        catalog = json.loads(body)["datasets"]
        self.assertEqual([row["difficulty"] for row in catalog], ["easy", "medium", "hard"])
        self.assertEqual([len(row["items"]) for row in catalog], [30] * 3)
        for group in catalog:
            for item in group["items"]:
                self.assertEqual(set(item), {"item_id", "query"})

    def test_unknown_and_private_files_are_not_served(self):
        for path in ["/.env", "/../.env", "/datasets/easy/gold.jsonl", "/api/gold"]:
            self.assertEqual(self.request(path)[0], 404)

    def test_reject_cross_origin_and_foreign_host(self):
        self.assertEqual(self.request("/api/catalog", headers={"Origin": "https://example.org"})[0], 403)
        self.assertEqual(self.request("/", headers={"Host": "example.org"})[0], 403)

    def test_multi_api_submission_and_redaction(self):
        data = {"query": "A research idea", "dry_run": True, "config": {
            "profiles": {"a": {"provider": "openai", "model": "test-model", "api_key": "test-key-example-one"},
                         "b": {"provider": "gemini", "model": "test-model", "api_key": "test-key-example-two"}},
            "roles": {"heavy": "a", "light": "a", "expert": "b"}}}
        status, body, _ = self.request("/api/jobs", data); self.assertEqual(status, 202)
        job_id = json.loads(body)["job_id"]
        status, body, _ = self.request("/api/jobs/" + job_id); self.assertEqual(status, 200)
        self.assertNotIn(b"test-key-example-one", body); self.assertNotIn(b"test-key-example-two", body)
        self.assertEqual(json.loads(body)["config"]["roles"]["expert"], "b")
        status, body, _ = self.request("/api/jobs/" + job_id + "/cancel", {})
        self.assertEqual(status, 200); self.assertTrue(json.loads(body)["cancelled"])

    def test_malformed_config_never_echoes_key(self):
        key = "test-key-example-private"
        status, body, _ = self.request("/api/jobs", {"query": "idea", "config": {"unknown": key}})
        self.assertEqual(status, 400); self.assertNotIn(key.encode(), body)

    def test_disallow_nonlocal_binding(self):
        with self.assertRaises(ValueError): create_server("0.0.0.0", manager=self.manager)

    def test_model_catalog_uses_chosen_profile_without_submitting_job(self):
        before = dict(self.manager.jobs)
        with patch("paperarena.web.fetch_models", return_value=[{"id": "test-model", "label": "Test model"}]) as lookup:
            status, body, _ = self.request("/api/models", {"profile": {
                "provider": "gemini", "model": "catalog-placeholder", "api_key": "test-key-example-models"}})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["models"][0]["id"], "test-model")
        self.assertEqual(lookup.call_args.args[0].provider, "gemini")
        self.assertEqual(self.manager.jobs, before)
        self.assertNotIn(b"test-key-example-models", body)

    def test_model_catalog_errors_are_safe_and_local_origin_is_required(self):
        data = {"profile": {"provider": "openai", "model": "test-model", "api_key": "test-key-example-models"}}
        with patch("paperarena.web.fetch_models", side_effect=ModelCatalogError("CATALOG_TIMEOUT", "The model catalog request timed out.")):
            status, body, _ = self.request("/api/models", data)
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["code"], "CATALOG_TIMEOUT")
        self.assertNotIn(b"test-key-example-models", body)
        self.assertEqual(self.request("/api/models", data, {"Content-Type": "application/json", "Origin": "https://example.org"})[0], 403)


if __name__ == "__main__": unittest.main()
