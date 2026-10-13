"""Exact-byte PDF page reuse; all remote responses are offline fixtures."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import fitz

from pipelines.run_manager import RunManager
from pipelines.runtime import fingerprint
from pipelines.sections import load_paper_sections
from pipelines.state import Intent, Paper, PipelineState
from pipelines.tasks import prior_art_scoring as scoring
from tools.pdf_parser import PdfParserTool


class PdfCacheEfficiencyTests(unittest.TestCase):
    def setUp(self):
        task_tmp = Path(__file__).resolve().parents[1] / "audit" / "implementation" / "tmp"
        task_tmp.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=task_tmp)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.shared = self.root / "shared_pages"
        for target in ("requests.sessions.Session.request", "socket.create_connection", "socket.socket.connect"):
            blocked = patch(target, side_effect=AssertionError("Live network is forbidden"))
            blocked.start()
            self.addCleanup(blocked.stop)
        limiter = patch.object(scoring.pdf_rate_limiter, "wait_for_slot")
        limiter.start()
        self.addCleanup(limiter.stop)

    def pdf_bytes(self, label="original"):
        with fitz.open() as document:
            page = document.new_page()
            page.insert_text((72, 72), "1 Introduction\n" + label + " prediction evidence. " * 12)
            page = document.new_page()
            page.insert_text((72, 72), "2 Experiments\nThe prediction mechanism supports all tested cases.")
            return document.tobytes()

    def manager(self, name, shared=True):
        manager = RunManager("fixture", base_dir=self.root / name,
                             shared_pdf_cache_dir=self.shared if shared else None)
        manager.create_run_directory()
        return manager

    def state(self, paper_id="A", title="Fixture", year=2025):
        return PipelineState(intent=Intent(task_type="prior_art_check", topic="prediction"), papers={
            paper_id: Paper(title=title, year=year, doi="10.1234/fixture",
                            pdf_url="https://fixture.invalid/mutable.pdf")
        })

    def response(self, body):
        return SimpleNamespace(content=body, raise_for_status=lambda: None)

    def material(self, state, manager, paper_id="A"):
        paper = state.papers[paper_id]
        return {
            "chunks": [chunk.model_dump() for chunk in paper.chunks],
            "sections": load_paper_sections(manager.run_dir, paper_id).to_dict(),
            "parsed_sections": copy.deepcopy(paper.parsed_sections),
            "pdf_status": paper.pdf_status, "pages_status": paper.pages_status,
            "chunks_status": paper.chunks_status, "sections_status": paper.sections_status,
            "content_hash": paper.content_hash, "parser_version": paper.parser_version,
            "has_fulltext": paper.has_fulltext,
        }

    def test_manager_separates_page_cache_from_download_source_bindings(self):
        manager = self.manager("separate")
        self.assertEqual(manager.pdf_pages_cache_dir, self.shared.resolve())
        self.assertEqual(manager.pdf_cache_dir, self.root / "separate" / "pdf_cache")
        self.assertTrue(manager.pdf_pages_cache_dir.is_dir())
        default = self.manager("default", shared=False)
        self.assertEqual(default.pdf_pages_cache_dir, default.pdf_cache_dir)

    def test_fresh_download_extracts_once_and_preserves_original_algorithms(self):
        body = self.pdf_bytes()
        fixture = self.root / "source.pdf"
        fixture.write_bytes(body)
        parser = PdfParserTool()
        pages, chunks, sections = parser.parse_artifacts(str(fixture), "A", "Fixture", 2025)
        manager, state = self.manager("fresh"), self.state()
        original = PdfParserTool._extract_text_pymupdf
        with patch.object(scoring.requests, "get", return_value=self.response(body)) as request, \
                patch.object(PdfParserTool, "_extract_text_pymupdf", autospec=True, side_effect=original) as extract:
            scoring.enrich_papers_with_fulltext(state, ["A"], max_workers=1, run_mgr=manager)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(extract.call_count, 1)
        self.assertEqual([chunk.model_dump() for chunk in state.papers["A"].chunks], [
            {"text": chunk["text"], "page_start": chunk.get("page_start"), "page_end": chunk.get("page_end")}
            for chunk in chunks
        ])
        self.assertEqual(load_paper_sections(manager.run_dir, "A").to_dict(), sections.to_dict())
        cached = json.loads(next(self.shared.glob("*.pages.json")).read_text(encoding="utf-8"))
        self.assertEqual(cached["pages"], pages)
        self.assertEqual(cached["pdf_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual([page["page"] for page in cached["pages"]], [1, 2])

    def test_cross_run_same_bytes_redownload_but_reuse_pages_and_identical_material(self):
        body = self.pdf_bytes()
        first_manager, second_manager = self.manager("first"), self.manager("second")
        first_state, second_state = self.state(), self.state()
        original = PdfParserTool._extract_text_pymupdf
        with patch.object(scoring.requests, "get", return_value=self.response(body)) as request, \
                patch.object(PdfParserTool, "_extract_text_pymupdf", autospec=True, side_effect=original) as extract:
            scoring.enrich_papers_with_fulltext(first_state, ["A"], max_workers=1, run_mgr=first_manager)
            scoring.enrich_papers_with_fulltext(second_state, ["A"], max_workers=1, run_mgr=second_manager)
        self.assertEqual(request.call_count, 2, "Mutable remote sources must still be checked each run")
        self.assertEqual(extract.call_count, 1)
        self.assertEqual(self.material(first_state, first_manager), self.material(second_state, second_manager))
        self.assertNotEqual(first_state.papers["A"].local_pdf_path, second_state.papers["A"].local_pdf_path)
        self.assertNotEqual(first_state.papers["A"].pdf_source_binding, second_state.papers["A"].pdf_source_binding)
        self.assertEqual(len(list(self.shared.iterdir())), 1)
        self.assertFalse(list(self.shared.glob("*.pdf")))
        self.assertFalse(list(self.shared.glob("*.source.json")))

    def test_cache_reuse_regenerates_paper_specific_section_identity(self):
        body = self.pdf_bytes()
        original = PdfParserTool._extract_text_pymupdf
        with patch.object(scoring.requests, "get", return_value=self.response(body)), \
                patch.object(PdfParserTool, "_extract_text_pymupdf", autospec=True, side_effect=original) as extract:
            for name, paper_id, title, year in [("one", "A", "First title", 2024), ("two", "B", "Second title", 2025)]:
                manager, state = self.manager(name), self.state(paper_id, title, year)
                scoring.enrich_papers_with_fulltext(state, [paper_id], max_workers=1, run_mgr=manager)
                sections = load_paper_sections(manager.run_dir, paper_id)
                self.assertEqual((sections.paper_id, sections.title, sections.year), (paper_id, title, year))
        self.assertEqual(extract.call_count, 1, "Only page text is shared; sections retain current paper metadata")

    def test_changed_bytes_and_parser_version_invalidate_reuse(self):
        first_body, changed_body = self.pdf_bytes("original"), self.pdf_bytes("changed")
        original = PdfParserTool._extract_text_pymupdf
        states = []
        with patch.object(scoring.requests, "get", side_effect=[
                self.response(first_body), self.response(changed_body), self.response(changed_body)]) as request, \
                patch.object(PdfParserTool, "_extract_text_pymupdf", autospec=True, side_effect=original) as extract:
            for name in ("original", "changed"):
                manager, state = self.manager(name), self.state()
                scoring.enrich_papers_with_fulltext(state, ["A"], max_workers=1, run_mgr=manager)
                states.append(state)
            with patch("tools.pdf_parser.PARSER_VERSION", "offline-parser-upgrade"):
                upgraded = self.state()
                scoring.enrich_papers_with_fulltext(upgraded, ["A"], max_workers=1, run_mgr=self.manager("upgraded"))
        self.assertEqual(request.call_count, 3)
        self.assertEqual(extract.call_count, 3)
        self.assertNotEqual(states[0].papers["A"].chunks, states[1].papers["A"].chunks)
        self.assertEqual(states[1].papers["A"].chunks, upgraded.papers["A"].chunks)
        self.assertEqual(upgraded.papers["A"].parser_version, "offline-parser-upgrade")
        self.assertEqual(len(list(self.shared.glob("*.pages.json"))), 3)

    def test_damaged_cache_and_reordered_pages_are_reparsed(self):
        body = self.pdf_bytes()
        with patch.object(scoring.requests, "get", return_value=self.response(body)):
            scoring.enrich_papers_with_fulltext(self.state(), ["A"], max_workers=1, run_mgr=self.manager("seed"))
        cache_path = next(self.shared.glob("*.pages.json"))
        valid = json.loads(cache_path.read_text(encoding="utf-8"))
        variants = []
        altered = copy.deepcopy(valid)
        altered["pages"][0]["text"] = "altered page text with stale digest"
        variants.append(altered)
        reordered = copy.deepcopy(valid)
        reordered["pages"].reverse()
        reordered["pages_sha256"] = fingerprint(reordered["pages"])
        variants.append(reordered)
        wrong_source = copy.deepcopy(valid)
        wrong_source["pdf_sha256"] = "0" * 64
        variants.append(wrong_source)
        variants.append("invalid JSON")
        original = PdfParserTool._extract_text_pymupdf
        with patch.object(scoring.requests, "get", return_value=self.response(body)), \
                patch.object(PdfParserTool, "_extract_text_pymupdf", autospec=True, side_effect=original) as extract:
            for index, variant in enumerate(variants):
                with self.subTest(variant=index):
                    cache_path.write_text(json.dumps(variant) if isinstance(variant, dict) else variant, encoding="utf-8")
                    state = self.state()
                    scoring.enrich_papers_with_fulltext(state, ["A"], max_workers=1, run_mgr=self.manager(f"damaged-{index}"))
                    self.assertEqual(state.papers["A"].pdf_status, "downloaded")
                    self.assertEqual(state.papers["A"].content_hash, valid["pdf_sha256"])
                    self.assertEqual(extract.call_count, index + 1)

    def test_invalid_download_never_becomes_a_shared_cache_entry(self):
        manager, state = self.manager("invalid"), self.state()
        with patch.object(scoring.requests, "get", return_value=self.response(b"<html>not a PDF</html>")):
            scoring.enrich_papers_with_fulltext(state, ["A"], max_workers=1, run_mgr=manager)
        self.assertEqual(state.papers["A"].pdf_status, "error")
        self.assertFalse(state.papers["A"].chunks)
        self.assertEqual(list(self.shared.iterdir()), [])
        self.assertEqual(list(manager.pdf_cache_dir.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
