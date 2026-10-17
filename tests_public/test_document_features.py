import importlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from paperarena import APIProfile, RunConfig, run_extension
from paperarena.artifacts import artifact_catalog, read_artifact
from paperarena.documents import document_chunks, EmbeddingClient, search_document
from paperarena.extensions import validate_extension, read_document


class DocumentTests(unittest.TestCase):
    def test_full_report_does_not_require_top5_but_requires_a_report(self):
        from paperarena._worker import result_status
        self.assertEqual(result_status("full_report", {"status":"SUCCEEDED"}, {}, ["report.md"]), "SUCCEEDED")
        self.assertEqual(result_status("full_report", {"status":"PARTIAL"}, {}, ["report.md"]), "PARTIAL")
        self.assertEqual(result_status("full_report", {"status":"SUCCEEDED"}, {"candidates":[{}]*5}, []), "FAILED")
        self.assertEqual(result_status("retrieval_eval", {}, {"status":"NOT_RUN_EMPTY_POOL"}, []), "EMPTY")
        self.assertEqual(result_status("retrieval_eval", {}, {"status":"FAILED"}, ["report.md"]), "FAILED")

    def test_critic_preserves_small_reports_and_labels_long_excerpts(self):
        from planner_worker import Critic
        critic=object.__new__(Critic)
        report="Detailed result\n"+"x"*3000+"\nEvidence scope and conclusion"
        summary=critic._format_execution_summary({"success":True,"execution_steps":[{"success":True,"result":report}]})
        self.assertIn(report,summary)
        large="a"*15000+"END EVIDENCE"
        summary=critic._format_execution_summary({"success":True,"execution_steps":[{"success":True,"result":large}]})
        self.assertIn("saved tool output is complete",summary)
        self.assertIn("END EVIDENCE",summary)

    def test_atomic_state_write_retries_temporary_windows_lock(self):
        from paperarena.api import _write_json
        replace=os.replace
        attempts=[]
        def locked_once(source,target):
            attempts.append(1)
            if len(attempts)==1:
                raise PermissionError("simulated destination lock")
            return replace(source,target)
        with tempfile.TemporaryDirectory() as folder, patch("paperarena.api.os.replace",side_effect=locked_once), patch("paperarena.api.time.sleep"):
            target=Path(folder)/"result.json"
            _write_json(target,{"status":"RUNNING"})
            self.assertEqual(json.loads(target.read_text())["status"],"RUNNING")
            self.assertEqual(len(attempts),2)

    def test_page_offsets_and_overlap(self):
        text = "<!-- Page 1 -->\n" + "a"*220 + "\n<!-- Page 2 -->\n" + "b"*130
        chunks = document_chunks(text, 100, 20)
        self.assertEqual({r["page"] for r in chunks}, {1, 2})
        for row in chunks:
            self.assertEqual(text[row["start"]:row["end"]], row["text"])
            self.assertLessEqual(len(row["text"]), 100)
        self.assertEqual(chunks[1]["start"]-chunks[0]["start"], 80)
        with self.assertRaises(ValueError):
            document_chunks(text, 100, 100)

    def test_mineru_module_import_is_lazy(self):
        module = importlib.import_module("tools.pdf_utils")
        self.assertTrue(callable(module.parse_doc))

    def test_legacy_splitter_accepts_list_and_explicit_embedding_key(self):
        from rag.retrievers.simple_vector_retriever import SimpleVectorRetriever
        from rag.embeddings.openai_embedding import OpenAIEmbedding
        splitter = SimpleVectorRetriever(embedding_model=object(), chunk_size=100, chunk_overlap=20)
        self.assertEqual(splitter._split_text(["first", "second"]), ["first", "second"])
        self.assertEqual(splitter._split_text([]), [])
        with self.assertRaises(TypeError):
            splitter._split_text([1])
        with patch.dict(os.environ, {"OPENAI_API_KEY": "wrong-key"}), patch("rag.embeddings.openai_embedding.OpenAI") as client:
            OpenAIEmbedding(api_key="explicit-test-key", base_url="https://embedding.example/v1")
            self.assertEqual(client.call_args.kwargs["api_key"], "explicit-test-key")
            self.assertEqual(client.call_args.kwargs["base_url"], "https://embedding.example/v1")
            self.assertEqual(client.call_args.kwargs["max_retries"], 0)

    def test_embedding_config_secrets_and_used_roles(self):
        from paperarena.api import _resolve_for_operation
        config = RunConfig({"unused": APIProfile("openai", "unused")},
                           embedding=APIProfile("custom", "embed", "private-test-key", "https://embedding.example/v1"))
        self.assertNotIn("private-test-key", json.dumps(config.to_public_dict()))
        request = validate_extension("document_search", {"query": "test", "document_path": "test.md"})
        resolved = _resolve_for_operation(config, request, False)
        self.assertEqual(resolved.embedding.api_key, "private-test-key")
        self.assertFalse(resolved.profiles["unused"].api_key)
        with self.assertRaises(ValueError):
            _resolve_for_operation(RunConfig(config.profiles), request, False)

    def test_embedding_payload_order_cosine_and_usage(self):
        config = RunConfig({"unused": APIProfile("openai", "unused")},
                           embedding=APIProfile("custom", "embed", "explicit-test-key", "https://embedding.example/v1"))
        def response(**kwargs):
            texts = kwargs["json"]["input"]
            vectors = [[1, 0] if "relevant" in t else [0, 1] for t in texts]
            result = MagicMock(status_code=200)
            result.json.return_value = {"data": [{"index": i, "embedding": v} for i, v in reversed(list(enumerate(vectors)))],
                                        "usage": {"total_tokens": 8}}
            return result
        with patch("requests.Session") as factory:
            session = factory.return_value.__enter__.return_value
            session.post.side_effect = lambda url, **kwargs: response(**kwargs)
            result = search_document("<!-- Page 1 -->other material\n<!-- Page 2 -->relevant material",
                {"query": "relevant query", "chunk_size": 1000, "chunk_overlap": 200, "top_k": 10}, config, lambda e: None)
            self.assertEqual(result["matches"][0]["page"], 2)
            self.assertEqual(result["embedding_usage"]["model_attempts"], 2)
            self.assertEqual(result["embedding_usage"]["tokens"]["total_tokens"], 16)
            self.assertFalse(session.post.call_args.kwargs["allow_redirects"])
            self.assertEqual(session.post.call_args.kwargs["headers"]["Authorization"], "Bearer explicit-test-key")

    def test_embedding_http_failures_are_not_retried_and_usage_unknown(self):
        import requests
        for code in (401, 429, 500):
            with self.subTest(code=code), patch("requests.Session") as factory:
                response = MagicMock(status_code=code)
                response.raise_for_status.side_effect = requests.HTTPError(response=response)
                factory.return_value.__enter__.return_value.post.return_value = response
                client = EmbeddingClient(APIProfile("openai", "embed", "test-key"), 1, lambda e: None)
                with self.assertRaises(requests.HTTPError):
                    client.embed(["test"])
                self.assertEqual(client.attempts, 1)
                self.assertEqual(client.failed, 1)
                self.assertIsNone(client.usage()["tokens"]["total_tokens"])
                self.assertEqual(factory.return_value.__enter__.return_value.post.call_count, 1)

    def test_embedding_invalid_response(self):
        with patch("requests.Session") as factory:
            response = MagicMock(status_code=200)
            response.json.return_value = {"data": [{"index": 0, "embedding": [float("nan")]}]}
            factory.return_value.__enter__.return_value.post.return_value = response
            client = EmbeddingClient(APIProfile("openai", "embed", "test-key"), 1, lambda e: None)
            with self.assertRaises(ValueError):
                client.embed(["test"])
            self.assertEqual(client.failed, 1)

    def test_empty_and_corrupt_pdf_fail(self):
        import fitz
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            doc = fitz.open();doc.new_page();doc.save(root/"empty.pdf");doc.close()
            (root/"bad.pdf").write_bytes(b"not a PDF")
            for filename in ("empty.pdf", "bad.pdf"):
                job=root/filename.split(".")[0];job.mkdir()
                with self.assertRaises(Exception):
                    read_document({"document_path": str(root/filename), "parser": "native"}, job)

    def test_native_document_worker_and_registered_artifacts(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/"source.md";source.write_text("Native document evidence.", encoding="utf-8")
            result=run_extension("document_parse", RunConfig({"unused": APIProfile("openai", "unused")}),
                                 {"document_path": str(source)}, output_dir=root/"runs", timeout_seconds=90)
            self.assertEqual(result["status"], "SUCCEEDED", result.get("error"))
            self.assertEqual(result["extension_result"]["details"]["chunk_count"], 1)
            files=artifact_catalog(result)
            self.assertTrue(any(f["name"]=="document.md" for f in files))
            name, content=read_artifact(result, files[0]["id"])
            self.assertTrue(content)
            with self.assertRaises(KeyError):
                read_artifact(result, "../../source.md")
            result["report_files"].append(str(source))
            self.assertFalse(any(f["name"]=="source.md" for f in artifact_catalog(result)))


if __name__=="__main__":
    unittest.main()
