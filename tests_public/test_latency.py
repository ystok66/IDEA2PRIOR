"""Offline scheduling/material regressions. Network and real model calls forbidden."""
import copy
import json
import re
import socket
import tempfile
import threading
import time
import unittest
from contextvars import ContextVar
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import fitz

from pipelines.tasks import prior_art_scoring as scoring
from pipelines.tasks import prior_art_fulltext as fulltext
from pipelines.tasks.prior_art_types import PriorArtConfig, PriorArtMatch, Paragraph
from pipelines.tasks.prior_art_utils import ordered_parallel_map, ensure_pdf_url
from pipelines.state import Intent, PipelineState, Paper, Chunk
from pipelines.sections import load_paper_sections
from pipelines.summaries import IntroRwSummary
from pipelines import intro_rw_summarizer as intro
from tools.pdf_parser import PdfParserTool


def valid_score(level=2):
    return {"coverage_level": level, "covered_facets": ["F1"],
            "task_similarity": 0.5, "method_similarity": 0.6, "notes": "abstract judgment",
            "high_level_facets": {"problem_facets": ["prediction"]}}


class OfflineCase(unittest.TestCase):
    def setUp(self):
        for target in ("requests.sessions.Session.request", "socket.create_connection", "socket.socket.connect"):
            p = patch(target, side_effect=AssertionError("Network is forbidden in offline tests"))
            p.start()
            self.addCleanup(p.stop)

    def model_patches(self, module, handler):
        class FakeModel:
            model_id = "fake"
            api_base = "offline://fake"
            def __init__(self, **kwargs):
                pass
            def __call__(self, messages, **kwargs):
                return handler(messages)
        p = patch.object(module, "get_litellm_config", return_value=("fake", "offline://fake", "not-a-real-key"))
        p.start()
        self.addCleanup(p.stop)
        p = patch("smolagents.LiteLLMModel", FakeModel) if module is fulltext else patch.object(module, "LiteLLMModel", FakeModel)
        p.start()
        self.addCleanup(p.stop)
        return FakeModel

    def state(self):
        return PipelineState(intent=Intent(task_type="prior_art_check", topic="prediction"), papers={
            pid: Paper(title=pid, abstract="prediction method", citation_count=1,
                       high_level_facets={"problem_facets": ["prediction"]},
                       chunks=[Chunk(text=f"evidence for {pid}")])
            for pid in ("B", "A", "C")
        })


class OrderingTests(OfflineCase):
    def test_bounded_order_and_context_survive_reversed_completion(self):
        context = ContextVar("test_context", default="missing")
        context.set("same_run")
        lock = threading.Lock()
        active = peak = 0
        def worker(i):
            nonlocal active, peak
            self.assertEqual(context.get(), "same_run")
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.004 * (4 - i % 4))
            with lock:
                active -= 1
            return None if i == 2 else i
        self.assertEqual(ordered_parallel_map(range(8), worker, 2), [0, 1, None, 3, 4, 5, 6, 7])
        self.assertEqual(peak, 2)

    def test_set_query_order_is_explicit_and_lists_keep_source_order(self):
        self.assertEqual(fulltext._make_bm25_query_for_facet("Facet", {"x": {"z", "a", "m"}}), "facet a m z")
        self.assertEqual(fulltext._make_bm25_query_for_facet("Facet", {"x": ["z", "a"]}), "facet z a")

    def test_prefilter_completion_order_and_boolean_schema(self):
        def handler(messages):
            text = messages[0]["content"]
            pid = re.search(r"Paper Title: (\w+)", text)[1]
            time.sleep(0.01 if pid == "A" else 0.001)
            return json.dumps({"is_relevant": "false" if pid == "B" else pid != "C"})
        self.model_patches(scoring, handler)
        papers = sorted(self.state().papers.items())
        with patch.object(scoring.llm_rate_limiter, "wait_for_slot"):
            serial = scoring.llm_relevance_prefilter(papers, "topic", "idea", 1)
            parallel = scoring.llm_relevance_prefilter(papers, "topic", "idea", 3)
        self.assertEqual([pid for pid, _ in serial], ["A", "B"])
        self.assertEqual([pid for pid, _ in parallel], ["A", "B"])


class ScoringTests(OfflineCase):
    def test_strict_score_rejects_invalid_levels_numbers_and_labels(self):
        self.assertEqual(scoring._validate_score_data(valid_score(0), 1)["coverage_level"], 0)
        for field, value in (("coverage_level", 4), ("coverage_level", True),
                             ("task_similarity", float("nan")), ("method_similarity", "0.5"),
                             ("covered_facets", ["F9"]), ("notes", None)):
            data = valid_score()
            data[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                scoring._validate_score_data(data, 1)
        with self.assertRaises(ValueError):
            scoring._validate_score_data({}, 1)

    def test_actual_scoring_retains_failed_denominator_and_ties(self):
        def handler(messages):
            text = messages[0]["content"]
            if "Title: B\n" in text:
                return json.dumps(valid_score(4))
            time.sleep(0.01 if "Title: A\n" in text else 0.001)
            return json.dumps(valid_score(0))
        self.model_patches(scoring, handler)
        outputs = []
        with patch.object(scoring.llm_rate_limiter, "wait_for_slot"):
            for workers in (1, 3):
                state = self.state()
                config = PriorArtConfig(enable_prefilter=False, enable_llm_prefilter=False,
                                        max_workers_llm_scoring=workers)
                outputs.append(scoring.score_papers_with_abstract_only(
                    None, state, ["prediction"], {"datasets": set(), "metrics": set(), "method_families": set()}, {}, cfg=config))
        self.assertEqual([m.paper_id for m in outputs[0]], ["A", "C", "B"])
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[0][-1].scoring_status, "failed")
        self.assertIsNone(outputs[0][-1].coverage_level)
        self.assertEqual(outputs[0][0].coverage_level, 0)

    def test_refinement_parallel_patches_preserve_abstract_and_failure(self):
        def handler(messages):
            text = messages[0]["content"]
            time.sleep(0.01 if "Title: A\n" in text else 0.001)
            return json.dumps({"coverage_level_refined": 4 if "Title: B\n" in text else 1,
                               "role": "background", "notes": "fulltext judgment"})
        self.model_patches(scoring, handler)
        outputs = []
        for workers in (1, 3):
            state = self.state()
            matches = [PriorArtMatch(pid, 3, notes="abstract judgment") for pid in state.papers]
            with patch.object(scoring, "enrich_papers_with_fulltext"), \
                 patch.object(scoring, "generate_evidence_keywords_from_idea", return_value=["evidence"]), \
                 patch.object(scoring, "find_evidence_snippets", side_effect=lambda paper, *a, **k: [paper.chunks[0].text]):
                outputs.append(scoring.refine_top_papers_with_fulltext(
                    None, state, matches, "idea", ["facet"], {}, top_k=3,
                    cfg=PriorArtConfig(max_workers_fulltext_refine=workers, overlap_pdf_keywords=False)))
        self.assertEqual(outputs[0], outputs[1])
        by_id = {m.paper_id: m for m in outputs[0]}
        self.assertEqual(by_id["A"].coverage_level, 1)
        self.assertEqual(by_id["A"].abstract_coverage_level, 3)
        self.assertEqual(by_id["A"].refinement_snippets, ["evidence for A"])
        self.assertEqual(by_id["B"].coverage_level, 3)
        self.assertEqual(by_id["B"].refinement_status, "failed")

    def test_keywords_begin_while_pdf_stage_is_active(self):
        started = threading.Event()
        def keywords(**kwargs):
            self.assertEqual(kwargs["max_keywords"], 30)
            started.set()
            return ["evidence"]
        def enrich(*args, **kwargs):
            self.assertTrue(started.wait(1), "Keywords were still waiting for PDF completion")
        self.model_patches(scoring, lambda messages: json.dumps({
            "coverage_level_refined": 1, "role": "background", "notes": "ok"}))
        state = self.state()
        with patch.object(scoring, "enrich_papers_with_fulltext", side_effect=enrich), \
             patch.object(scoring, "generate_evidence_keywords_from_idea", side_effect=keywords), \
             patch.object(scoring, "find_evidence_snippets", return_value=["evidence"]):
            scoring.refine_top_papers_with_fulltext(None, state, [PriorArtMatch("A", 2)],
                "idea", ["facet"], {}, top_k=1, cfg=PriorArtConfig(overlap_pdf_keywords=True))


class PdfTests(OfflineCase):
    def make_pdf(self, path, text="the method uses prediction evidence with all source words retained."):
        with fitz.open() as doc:
            page = doc.new_page()
            page.insert_text((72, 72), "1 Method\n" + text)
            page = doc.new_page()
            page.insert_text((72, 72), "2 Experiments\nresults support the proposed prediction mechanism in all tested cases.")
            doc.save(path)

    def test_single_page_extraction_preserves_original_chunk_and_section_algorithms(self):
        with tempfile.TemporaryDirectory() as folder:
            pdf = Path(folder) / "paper.pdf"
            self.make_pdf(pdf)
            parser = PdfParserTool()
            original = PdfParserTool._extract_text_pymupdf
            with patch.object(PdfParserTool, "_extract_text_pymupdf", autospec=True, side_effect=original) as extract:
                pages, chunks, sections = parser.parse_artifacts(str(pdf), "A", "title", 2025)
            self.assertEqual(extract.call_count, 1)
            self.assertEqual(chunks, parser._simple_chunk_from_pages(pages, 1000))
            self.assertEqual(sections, parser.parse_sections(str(pdf), "A", "title", 2025, pages=pages))
            self.assertEqual([p["page"] for p in pages], [1, 2])

    def test_sections_recovery_content_change_and_page_cache_integrity(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pdf = root / "paper.pdf"
            self.make_pdf(pdf)
            manager = SimpleNamespace(run_dir=root / "run", pdf_cache_dir=root / "cache",
                                      link_pdf_to_run=lambda *args: None)
            state = PipelineState(intent=Intent(task_type="prior_art_check", topic="topic"),
                                  papers={"A": Paper(title="A", local_pdf_path=str(pdf),
                                                     chunks=[Chunk(text="stale chunk without provenance")])})
            original = PdfParserTool._extract_text_pymupdf
            with patch.object(PdfParserTool, "_extract_text_pymupdf", autospec=True, side_effect=original) as extract:
                scoring.enrich_papers_with_fulltext(state, ["A"], run_mgr=manager)
                self.assertEqual(extract.call_count, 1)
                self.assertEqual(state.papers["A"].section_status, "parsed")
                self.assertTrue(load_paper_sections(manager.run_dir, "A").sections)
                self.assertTrue(state.papers["A"].parsed_sections)
                first_chunks = copy.deepcopy(state.papers["A"].chunks)
                section_path = manager.run_dir / "pdf_sections" / "A.sections.json"
                section_path.write_text("{broken", encoding="utf-8")
                scoring.enrich_papers_with_fulltext(state, ["A"], run_mgr=manager)
                self.assertEqual(extract.call_count, 1, "Should reuse content-verified pages for section recovery")
                self.assertEqual(state.papers["A"].chunks, first_chunks)
                cached = next(manager.pdf_cache_dir.glob("*.pages.json"))
                data = json.loads(cached.read_text(encoding="utf-8"))
                data["pages"][0]["text"] = "corrupted but valid JSON"
                cached.write_text(json.dumps(data), encoding="utf-8")
                section_path.write_text("{broken", encoding="utf-8")
                scoring.enrich_papers_with_fulltext(state, ["A"], run_mgr=manager)
                self.assertEqual(extract.call_count, 2, "Altered page cache must be rejected")
                replacement = root / "replacement.pdf"
                self.make_pdf(replacement, "a completely changed source document with new experimental details.")
                replacement.replace(pdf)
                scoring.enrich_papers_with_fulltext(state, ["A"], run_mgr=manager)
                self.assertEqual(extract.call_count, 3)
                self.assertNotEqual(state.papers["A"].chunks, first_chunks)

    def test_invalid_pdf_is_not_success(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pdf = root / "bad.pdf"
            pdf.write_text("<html>not a PDF</html>", encoding="utf-8")
            state = PipelineState(intent=Intent(task_type="prior_art_check", topic="topic"),
                                  papers={"A": Paper(title="A", local_pdf_path=str(pdf))})
            manager = SimpleNamespace(run_dir=root / "run", pdf_cache_dir=root / "cache", link_pdf_to_run=lambda *a: None)
            scoring.enrich_papers_with_fulltext(state, ["A"], run_mgr=manager)
            self.assertEqual(state.papers["A"].pdf_status, "error")
            self.assertFalse(state.papers["A"].chunks)

    def test_document_version_survives_parser_upgrade(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pdf = root / 'paper.pdf'
            self.make_pdf(pdf)
            manager = SimpleNamespace(run_dir=root/'run', pdf_cache_dir=root/'cache', link_pdf_to_run=lambda *a: None)
            paper = Paper(title='A', arxiv_id='2401.12345v1', document_version='arxiv:2401.12345v1', local_pdf_path=str(pdf))
            state = PipelineState(intent=Intent(task_type='prior_art_check',topic='test'),papers={'A':paper})
            scoring.enrich_papers_with_fulltext(state,['A'],run_mgr=manager)
            original_version = paper.document_version
            with patch('tools.pdf_parser.PARSER_VERSION','fixture-parser-v2'):
                scoring.enrich_papers_with_fulltext(state,['A'],run_mgr=manager)
            self.assertEqual(paper.document_version, original_version)
            self.assertEqual(paper.parser_version,'fixture-parser-v2')

    def test_two_versions_with_same_doi_download_separate_materials(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            files = [root/'one.pdf',root/'two.pdf']
            self.make_pdf(files[0], 'FIRST_VERSION unique experimental source.')
            self.make_pdf(files[1], 'SECOND_VERSION entirely different experimental source.')
            bodies = {f'https://fixture.invalid/v{i+1}.pdf': path.read_bytes() for i,path in enumerate(files)}
            manager = SimpleNamespace(run_dir=root/'run', pdf_cache_dir=root/'cache', link_pdf_to_run=lambda *a:None)
            papers = {str(i):Paper(title=f'Version {i}',doi='10.1234/shared',document_version=f'v{i}',
                      pdf_url=f'https://fixture.invalid/v{i}.pdf') for i in (1,2)}
            state = PipelineState(intent=Intent(task_type='prior_art_check',topic='test'),papers=papers)
            def response(url, **kwargs):
                return SimpleNamespace(content=bodies[url],raise_for_status=lambda:None)
            with patch.object(scoring.requests,'get',side_effect=response) as request:
                scoring.enrich_papers_with_fulltext(state,['1','2'],run_mgr=manager,max_workers=2)
            self.assertEqual(request.call_count,2)
            self.assertNotEqual(papers['1'].local_pdf_path,papers['2'].local_pdf_path)
            self.assertNotEqual(papers['1'].content_hash,papers['2'].content_hash)
            self.assertIn('FIRST_VERSION',' '.join(c.text for c in papers['1'].chunks))
            self.assertIn('SECOND_VERSION',' '.join(c.text for c in papers['2'].chunks))

    def test_reused_paper_does_not_turn_remote_cache_into_explicit_input(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            first=root/'first.pdf'; second=root/'second.pdf'
            self.make_pdf(first,'FIRST_RUN source text.')
            self.make_pdf(second,'SECOND_RUN updated source text.')
            paper=Paper(title='Remote',pdf_url='https://fixture.invalid/mutable.pdf')
            state=PipelineState(intent=Intent(task_type='prior_art_check',topic='test'),papers={'A':paper})
            def manager(name):
                return SimpleNamespace(run_dir=root/name,pdf_cache_dir=root/'cache',link_pdf_to_run=lambda *a:None)
            def response(body):
                return SimpleNamespace(content=body,raise_for_status=lambda:None)
            with patch.object(scoring.requests,'get',side_effect=[response(first.read_bytes()),response(second.read_bytes())]) as request:
                scoring.enrich_papers_with_fulltext(state,['A'],run_mgr=manager('run1'))
                old_path=paper.local_pdf_path
                scoring.enrich_papers_with_fulltext(state,['A'],run_mgr=manager('run1'))
                self.assertEqual(request.call_count,1)
                scoring.enrich_papers_with_fulltext(state,['A'],run_mgr=manager('run2'))
            self.assertEqual(request.call_count,2)
            self.assertNotEqual(paper.local_pdf_path,old_path)
            self.assertEqual(paper.pdf_origin,'remote_cache')
            self.assertIn('SECOND_RUN',' '.join(c.text for c in paper.chunks))

    def test_resolving_url_does_not_claim_download(self):
        paper = Paper(title="A", pdf_url="https://example.invalid/paper.pdf")
        self.assertEqual(ensure_pdf_url(paper), paper.pdf_url)
        self.assertEqual(paper.pdf_status, "resolved")


class SnippetTests(OfflineCase):
    def test_summary_schema_failure_stays_failed_instead_of_empty_success(self):
        from pipelines.sections import PaperSections, Section
        from pipelines.runtime import RunLedger, LLMRuntime, use_runtime
        self.model_patches(intro, lambda messages: json.dumps({'is_survey':'false',
            'domain_overview_bullets':[], 'contribution_bullets':[], 'positioning_bullets':[], 'bullets_with_snippets':[]}))
        sections=PaperSections('A','title',2025,[Section('intro','Introduction','Our method performs prediction. ' * 10)])
        with tempfile.TemporaryDirectory() as folder:
            runtime=LLMRuntime(RunLedger(Path(folder)))
            with use_runtime(runtime):
                summary=intro.summarize_intro_rw_for_paper('A','title',2025,sections,'topic','idea')
            self.assertEqual(summary.status,'failed')
            self.assertEqual(runtime.ledger.task_statuses()[0]['status'],'FAILED')
            self.assertTrue(summary.error)

    def test_no_fulltext_has_explicit_empty_terminal_artifacts(self):
        with tempfile.TemporaryDirectory() as folder:
            manager=SimpleNamespace(run_dir=Path(folder))
            state=PipelineState(intent=Intent(task_type='prior_art_check',topic='test'))
            fulltext.build_method_experiment_snippets(state,['facet'],{},manager,PriorArtConfig())
            self.assertEqual((Path(folder)/'synthesis/method_exp_snippets.jsonl').read_text(), '')
            self.assertEqual(json.loads((Path(folder)/'synthesis/method_exp_status.json').read_text())['status'], 'NO_MATERIAL')
    def candidates(self, count=31):
        return [(Paragraph(paper_id="A", section_role="method", section_heading="Methods", para_id=i,
                           section_id="section_0", page_start=1, page_end=2,
                           text=f"TOKEN_{i:02d} " + "all original material " * 8, token_count=30), float(i + 1))
                for i in range(count)]

    def test_parallel_original_batches_and_complete_generated_bullets(self):
        prompts = []
        lock = threading.Lock()
        def handler(messages):
            text = messages[0]["content"]
            with lock:
                prompts.append(text)
            numbers = [int(n) for n in re.findall(r"\[Current\]: TOKEN_(\d+)", text)]
            time.sleep(0.01 if numbers[0] == 0 else 0.001)
            return json.dumps([{"para_num": i + 1, "relevant": True, "bullet": f"bullet_{n}_" + "x" * 600,
                                "snippet": f"TOKEN_{n:02d}", "relevance_score": 0.5}
                               for i, n in enumerate(numbers)])
        self.model_patches(fulltext, handler)
        outputs = []
        recorded = []
        for workers in (1, 4):
            prompts.clear()
            statuses = []
            outputs.append(fulltext._llm_filter_and_summarize_paragraphs(
                "facet", self.candidates(), PriorArtConfig(max_workers_snippet_batches=workers),
                task_prefix="facet_0", audit_records=statuses))
            recorded.append(sorted(prompts))
            self.assertEqual(len(prompts), 3)
            self.assertEqual([r["expected_paragraphs"] for r in statuses], [10, 10, 10])
            self.assertEqual([r["status"] for r in statuses], ["succeeded"] * 3)
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(recorded[0], recorded[1])
        self.assertEqual(len(outputs[0]), 30)
        self.assertTrue(all(len(s.bullet) > 600 for s in outputs[0]))
        self.assertEqual(len({s.loc for s in outputs[0]}), 30)
        self.assertEqual([s.loc for s in fulltext._dedupe_snippets(outputs[0], 5)],
                         [f"section_0_method_para_{i}" for i in range(5)])

    def test_missing_duplicate_and_nonboolean_paragraphs_stay_visible(self):
        self.model_patches(fulltext, lambda messages: json.dumps([
            {"para_num": 1, "relevant": True, "bullet": "ok", "snippet": "original", "relevance_score": 1},
            {"para_num": 1, "relevant": True, "bullet": "duplicate", "snippet": "x", "relevance_score": 1},
            {"para_num": 2, "relevant": "false", "bullet": "bad", "snippet": "x", "relevance_score": 1},
        ]))
        statuses = []
        result = fulltext._llm_filter_and_summarize_paragraphs(
            "facet", self.candidates(3), PriorArtConfig(), audit_records=statuses)
        self.assertEqual(len(result), 1)
        self.assertEqual(statuses[0]["status"], "partial")
        self.assertEqual(statuses[0]["missing_para_nums"], [2, 3])

    def test_section_and_paragraph_identity_do_not_collide(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "pdf_sections"
            path.mkdir()
            (path / "A.sections.json").write_text(json.dumps({"paper_id": "A", "sections": [
                {"role": "method", "heading": "Methods", "text": "retained paragraph source text " * 4, "start_page": i, "end_page": i}
                for i in (1, 2)]}), encoding="utf-8")
            paragraphs = fulltext._load_method_and_experiment_paragraphs(Path(folder), ["A"])
            self.assertEqual([p.section_id for p in paragraphs], ["section_0", "section_1"])
            self.assertEqual([p.para_id for p in paragraphs], [0, 0])
            self.assertEqual([p.page_start for p in paragraphs], [1, 2])

    def test_intro_parallel_dispatch_keeps_original_order(self):
        def summarize(info, task_root, topic, idea, index, total):
            time.sleep(0.01 if index == 0 else 0.001)
            summary = IntroRwSummary.empty(info["paper_id"], "title", 2025)
            summary.contribution_bullets = ["complete contribution"]
            return info["paper_id"], summary, "success"
        with tempfile.TemporaryDirectory() as folder, patch.object(intro, "_summarize_single_paper", side_effect=summarize), \
             patch.dict("os.environ", {"INTRO_RW_MAX_WORKERS": "3"}):
            result = intro.summarize_intro_rw_for_block3(folder, [{"paper_id": "B"}, {"paper_id": "A"}], "topic", "idea")
            self.assertEqual([s.paper_id for s in result], ["B", "A"])
            self.assertEqual([json.loads(line)["paper_id"] for line in
                              (Path(folder) / "synthesis" / "intro_rw_summary_status.jsonl").read_text().splitlines()], ["B", "A"])


if __name__ == "__main__":
    unittest.main()
