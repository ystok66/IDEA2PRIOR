"""Explicit optional research tasks. Importing this module performs no IO."""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
import hashlib
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
from urllib.parse import urlsplit

from .diagnostics import diagnostic_for_type, safe_error, PublicInputError

EXTENSIONS = {
    "code_extractor": {"label": "Code & reproducibility analysis", "label_zh": "代码与可复现性分析", "document": True},
    "evolution_mapper": {"label": "Technology evolution map", "label_zh": "技术演化图", "document": False},
    "planner_worker": {"label": "Planner–Worker research assistant", "label_zh": "Planner–Worker 研究助手", "document": False},
    "benchmark_landscape": {"label": "Benchmark landscape", "label_zh": "基准与评测概览", "document": False},
    "slot_filling": {"label": "Citation gap analysis", "label_zh": "引用缺口分析", "document": False},
}


def validate_extension(name, parameters=None):
    if name not in EXTENSIONS:
        raise PublicInputError("Unknown optional extension")
    if parameters is not None and not isinstance(parameters, dict):
        raise PublicInputError("Extension parameters must be an object")
    params = dict(parameters or {})
    allowed = {"topic", "query", "document_path", "parser", "llm_analysis", "execute_code",
               "max_papers", "start_year", "end_year", "max_steps", "enable_critic",
               "manuscript_excerpt", "known_refs"}
    if set(params) - allowed:
        raise PublicInputError("Unknown extension parameter")
    for key in ("topic", "query", "document_path", "manuscript_excerpt"):
        if key in params and (not isinstance(params[key], str) or len(params[key]) > 20000):
            raise PublicInputError(f"{key} must be text of at most 20,000 characters")
    for key, default in (("llm_analysis", True), ("execute_code", False), ("enable_critic", True)):
        params.setdefault(key, default)
        if type(params[key]) is not bool:
            raise PublicInputError(f"{key} must be a boolean")
    for key, default, maximum in (("max_papers", 10, 60), ("max_steps", 5, 20)):
        params.setdefault(key, default)
        if type(params[key]) is not int or not 1 <= params[key] <= maximum:
            raise PublicInputError(f"{key} must be between 1 and {maximum}")
    for key in ("start_year", "end_year"):
        if params.get(key) is not None and (type(params[key]) is not int or not 1800 <= params[key] <= 2200):
            raise PublicInputError(f"{key} must be a year or null")
    if params.get("start_year") and params.get("end_year") and params["start_year"] > params["end_year"]:
        raise PublicInputError("Start year exceeds end year")
    params.setdefault("parser", "native")
    if params["parser"] not in ("native", "mineru"):
        raise PublicInputError("parser must be native or mineru")
    refs = params.setdefault("known_refs", [])
    if not isinstance(refs, list) or len(refs) > 200 or any(not isinstance(ref, str) or len(ref) > 500 for ref in refs):
        raise PublicInputError("known_refs must contain at most 200 short identifiers")
    if name == "code_extractor" and not params.get("document_path", "").strip():
        raise PublicInputError("Code analysis requires a PDF, Markdown or text document")
    if name != "code_extractor" and not (params.get("topic", "").strip() or params.get("query", "").strip()):
        raise PublicInputError("This extension requires a topic or task")
    if name == "slot_filling" and not params.get("manuscript_excerpt", "").strip():
        raise PublicInputError("Citation gap analysis requires a manuscript excerpt")
    return {"name": name, "parameters": params}


def extension_needs_model(request):
    return bool(extension_model_roles(request))


def extension_model_roles(request):
    name, params = request["name"], request["parameters"]
    if name in ("planner_worker", "slot_filling"):
        return {"heavy", "light"} if name == "planner_worker" and params.get("document_path") and params["llm_analysis"] else {"heavy"}
    return {"light"} if name == "code_extractor" and params["llm_analysis"] else set()


def _jsonable(value):
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return str(value)


def read_document(parameters, directory):
    """Copy supplied material to the isolated job before parsing; no downloads."""
    source = Path(parameters["document_path"]).expanduser().resolve(strict=True)
    if not source.is_file() or source.suffix.lower() not in (".pdf", ".md", ".txt"):
        raise PublicInputError("Provide one PDF, Markdown or text document")
    if source.stat().st_size > 12 * 1024 * 1024:
        raise PublicInputError("The document exceeds the 12 MiB limit")
    inputs = directory / "inputs"
    inputs.mkdir(exist_ok=True)
    target = inputs / ("paper" + source.suffix.lower())
    shutil.copyfile(source, target)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    if target.suffix != ".pdf":
        content = target.read_text(encoding="utf-8-sig")
        parser = "provided_text"
    elif parameters["parser"] == "native":
        import fitz
        with fitz.open(target) as pdf:
            content = "\n\n".join(f"<!-- Page {n + 1} -->\n{page.get_text()}" for n, page in enumerate(pdf))
        parser = "native_pdf_text"
    else:
        from tools.pdf_utils import parse_doc
        parsed = directory / "parsed"
        parse_doc([str(target)], str(parsed), lang="en", backend="pipeline", method="auto")
        markdown = sorted(parsed.rglob("*.md"))
        if not markdown:
            raise FileNotFoundError("MinerU did not produce Markdown")
        content = markdown[0].read_text(encoding="utf-8")
        parser = "mineru"
    if not content.strip():
        raise PublicInputError("The supplied document has no extractable text")
    if len(content) > 2_000_000:
        raise PublicInputError("Extracted document exceeds the two-million-character limit")
    (directory / "document.md").write_text(content, encoding="utf-8")
    return content, {"source_sha256": digest, "parser": parser, "characters": len(content),
                     "source_copy": str(target), "evidence_scope": "supplied document; native extraction omits image-only content"}


def execute_extension(request, config, directory, Model, emit):
    """Capture scholarly transport failures even when legacy tools catch them."""
    import requests
    original = requests.Session.send
    errors = []
    counts = {"attempts": 0, "failures": 0}
    def observed(session, request, **kwargs):
        service = {"api.openalex.org": "OpenAlex", "api.semanticscholar.org": "Semantic Scholar",
                   "export.arxiv.org": "arXiv", "api.crossref.org": "Crossref"}.get(urlsplit(request.url).hostname, "HTTP service")
        counts["attempts"] += 1
        try:
            response = original(session, request, **kwargs)
            if response.status_code >= 400:
                counts["failures"] += 1
                detail = diagnostic_for_type("HTTPError", http_status=response.status_code, phase="extension_discovery")
                detail["service"] = service
                errors.append(detail)
            return response
        except Exception as error:
            counts["failures"] += 1
            detail = safe_error(error, "extension_discovery")
            detail["service"] = service
            errors.append(detail)
            raise
    requests.Session.send = observed
    try:
        result = _execute_extension(request, config, directory, Model, emit)
        result["diagnostics"].extend(errors)
        result["scholarly_http"] = counts
        if errors and result["status"] == "EMPTY":
            result["status"] = "FAILED"
        elif errors and result["status"] == "SUCCEEDED":
            result["status"] = "PARTIAL"
        return result
    finally:
        requests.Session.send = original
        emit({"scholarly_http": counts})


def _execute_extension(request, config, directory, Model, emit):
    """Run the restored implementations with the selected API profiles."""
    request = validate_extension(request["name"], request["parameters"])
    name, params = request["name"], request["parameters"]
    directory = Path(directory)
    topic = params.get("topic") or params.get("query") or "Code analysis"
    diagnostics = []
    evidence = None
    content = None
    if params.get("document_path"):
        emit({"phase": "extension_document"})
        content, evidence = read_document(params, directory)

    def model_for(role):
        profile = config.profiles[config.roles[role]]
        prefix = "gemini/" if profile.provider == "gemini" else "openai/"
        model_id = profile.model if profile.model.startswith(prefix) else prefix + profile.model
        base = None if profile.provider == "gemini" else profile.base_url or "https://api.openai.com/v1"
        return Model(model_id=model_id, api_base=base, api_key=profile.api_key)

    def code_tool():
        from tools import code_extractor as implementation
        CodeExtractorTool = implementation.CodeExtractorTool
        if params["llm_analysis"] and not implementation.LLM_AVAILABLE:
            raise implementation.LLM_IMPORT_ERROR
        if content is None:
            raise PublicInputError("Code analysis requires a supplied document")
        class BoundCodeTool(CodeExtractorTool):
            def _get_markdown_content(self, pdf_name, query=None):
                if pdf_name != "paper":
                    raise PublicInputError("Only the supplied document named paper is available")
                return content
        tool = BoundCodeTool(pdf_base_dir=directory / "inputs", output_base_dir=directory / "parsed",
                             use_mock=False, execute_code=params["execute_code"],
                             llm_enabled=params["llm_analysis"], enable_web_search=False)
        tool.setup()
        return tool

    def evolution_tool():
        from tools.evolution_mapper import EvolutionMapper
        from tools.semantic_scholar_tool import SemanticScholarTool
        class BoundedEvolution(EvolutionMapper):
            def forward(self, topic: str, max_papers: int | None = None,
                        start_year: int | None = None, end_year: int | None = None,
                        format: str | None = None) -> str:
                if max_papers is not None and (type(max_papers) is not int or not 1 <= max_papers <= params["max_papers"]):
                    raise PublicInputError("Evolution map exceeds the selected paper limit")
                return super().forward(topic, max_papers or params["max_papers"], start_year, end_year, "mermaid")
            def setup(self):
                self.semantic_tool.setup()
                self.is_initialized = True
        semantic = SemanticScholarTool(api_key=config.search.get("semantic_scholar_api_key") or None,
                                       output_dir=directory / "citation_cache")
        tool = BoundedEvolution(semantic_tool=semantic, output_dir=directory)
        tool.setup()
        return tool

    emit({"phase": "extension_" + name})
    details = {}
    status = "SUCCEEDED"
    if name == "code_extractor":
        tool = code_tool()
        report = tool.forward("paper", params.get("query") or None)
        status = tool.last_tool_status
        if status == "FAILED":
            raise RuntimeError("Code extraction failed")
        details = {"analysis": getattr(tool, "last_analysis", None),
                   "code_execution_enabled": params["execute_code"], "llm_analysis_enabled": params["llm_analysis"]}
        diagnostics = [diagnostic_for_type(issue["type"], phase=issue["phase"]) for issue in tool.analysis_errors]
    elif name == "evolution_mapper":
        tool = evolution_tool()
        report = tool.forward(topic, params["max_papers"], params.get("start_year"), params.get("end_year"))
        status = tool.last_tool_status
        diagnostics = getattr(tool.semantic_tool, "errors", []) + getattr(tool, "errors", [])
        details = {"network": getattr(tool, "last_network", None), "format": "mermaid"}
        if status == "FAILED":
            report = "# Evolution map unavailable\n\nDiscovery or citation processing failed. See run diagnostics."
    elif name == "planner_worker":
        from planner_worker import PlannerWorkerAgent
        tools = [evolution_tool()]
        if content is not None:
            tools.append(code_tool())
        agent = PlannerWorkerAgent(model_for("heavy"), tools, max_steps=params["max_steps"],
                                   enable_critic=params["enable_critic"], max_reexecutions=0)
        output = agent.run(params.get("query") or topic,
                           context={"available_document": "paper" if content is not None else None,
                                    "max_papers": params["max_papers"], "whole_task_retry": False})
        details = _jsonable(output)
        steps = output.get("execution", {}).get("execution_steps", [])
        status = "SUCCEEDED" if output.get("success") else "PARTIAL" if any(s.get("success") for s in steps) else "FAILED"
        report = "# Planner–Worker result\n\n" + str(output.get("execution", {}).get("final_result") or "No completed tool result.")
        for step in steps:
            if not step.get("success"):
                diagnostics.append({"type": "IncompleteToolStep", "code": "EXTENSION_STEP_INCOMPLETE",
                                    "message": "A planned tool step failed, was blocked, or was not executed.",
                                    "phase": "planner_worker", "step_id": step.get("step_id"),
                                    "step_status": step.get("status")})
        if output.get("errors"):
            diagnostics.append(diagnostic_for_type("ResponseValidationError", phase="planner_worker"))
        for tool in tools:
            diagnostics.extend(getattr(tool, "errors", []))
            diagnostics.extend(getattr(getattr(tool, "semantic_tool", None), "errors", []))
    else:
        from pipelines.state import PipelineState
        from pipelines.exploration_loop import run_exploration_loop
        from pipelines.tasks.benchmark_landscape import build_benchmark_intent, render_benchmark_report
        from pipelines.tasks.prior_art_types import PriorArtConfig
        cfg = PriorArtConfig(**{"enable_openalex": True, "execution_mode": "full_report", "staged_retrieval": False,
                                "recent_year_start": params.get("start_year") or 2015,
                                "recent_year_end": params.get("end_year") or 2025})
        # The original shared discovery loop powers both restored tasks.
        intent = build_benchmark_intent(topic, years=config.years, venues=[])
        if name == "slot_filling":
            intent.task_type = "slot_filling"
        state = run_exploration_loop(None, PipelineState(intent=intent), max_rounds=config.max_rounds,
                                     max_papers=params["max_papers"], cfg=cfg)
        if name == "benchmark_landscape":
            report = render_benchmark_report(state)
            details = {"datasets": state.datasets, "paper_count": len(state.papers),
                       "evidence_scope": "original preliminary metadata and heuristic report"}
        else:
            from pipelines.tasks.slot_filling import score_papers_for_slot_filling, render_slot_filling_report, _is_known_ref
            model = model_for("heavy")
            def answer(prompt):
                return model.generate([{"role": "user", "content": prompt}]).content
            matches = score_papers_for_slot_filling(SimpleNamespace(run=answer), state,
                params["manuscript_excerpt"], params["known_refs"], max_papers=params["max_papers"])
            report = render_slot_filling_report(state, matches)
            details = {"matches": _jsonable(matches), "paper_count": len(state.papers)}
            diagnostics.extend(match.error for match in matches if match.error)
            eligible = sum(not _is_known_ref(paper, params["known_refs"]) for paper in state.papers.values())
            details["expected_assessments"] = min(params["max_papers"], eligible)
            if len(matches) < details["expected_assessments"]:
                diagnostics.append(diagnostic_for_type("ResponseValidationError", phase="slot_filling"))
        status = "SUCCEEDED" if state.papers else "EMPTY"
        if name == "slot_filling" and matches and all(match.assessment_status == "FAILED" for match in matches):
            status = "FAILED"
    if diagnostics and status == "SUCCEEDED":
        status = "PARTIAL"
    # Raw provider errors and credentials never enter saved reports or JSON.
    from pipelines.runtime import redact
    secrets = tuple(p.api_key for p in config.profiles.values() if p.api_key)
    details = redact(_jsonable(details), secrets)
    for secret in secrets:
        report = report.replace(secret, "[REDACTED]")
    report_path = directory / (name + ".md")
    report_path.write_text(report, encoding="utf-8")
    result = {"name": name, "status": status, "details": details, "document_evidence": evidence,
              "report_markdown": report, "diagnostics": diagnostics, "report_path": str(report_path)}
    (directory / "extension.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
