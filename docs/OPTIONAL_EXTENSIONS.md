# Optional research extensions

Idea2Prior builds on [PaperArena](https://arxiv.org/abs/2510.10909) and its [official implementation](https://github.com/ustc-ai4science/PaperArena). This release restores the project's earlier implementation-analysis, evolution-map, Planner–Worker, benchmark-landscape and citation-gap modules as independent optional tasks. Original archived sources remain preserved. These modules are not automatically called by `retrieval_eval` or `full_report`.

## Entry points and configuration

In the local web interface, change **Task** from its default paper retrieval to an extension. Use the existing API profiles and roles. A document can be uploaded or selected through its local path. The Notebook's section 6 provides a task selector, examples for every extension, an offline configuration check, and a separate `RUN_EXTENSIONS=False` execution guard.

```python
from idea2prior import run_extension
result = run_extension("evolution_mapper", config,
    {"topic": "YOUR_TOPIC", "max_papers": 10},
    output_dir="outputs/extensions", timeout_seconds=1800)
print(result["status"], result.get("error"))
print((result.get("extension_result") or {}).get("report_path"))
```

Every call creates a separate job directory. The same process isolation, deadline, cancellation, safe failure messages and reported-token accounting apply to retrieval and extensions. There is no automatic whole-task rerun. Only API profiles used by the selected extension require credentials.

| Extension | Model use | Other inputs/services |
| --- | --- | --- |
| Code Extractor | Light when `llm_analysis=True` | Supplied document; optional MinerU parser |
| Evolution Mapper | No generation-model call | Semantic Scholar search and citation graph |
| Planner–Worker | Heavy for Planner/Critic; Light if document analysis is enabled | Restored evolution/code tools, bounded steps |
| Benchmark Landscape | No generation-model call | Original OpenAlex/Semantic Scholar exploration and metadata heuristics |
| Citation Gap Analysis | Heavy for citation scoring | Original exploration, draft excerpt and known-reference identifiers |

## Code Extractor

Provide `document_path`, optional `query`, `llm_analysis` (default true), `execute_code` (default false), and `parser` (`native` or `mineru`). Supported documents are PDF, UTF-8 Markdown and UTF-8 text, up to 12 MiB. The source document is copied into the job and fingerprinted with SHA-256 before parsing. PDFs use native page-text extraction by default; image-only content is outside that evidence scope. Selecting MinerU explicitly uses the restored structured parser and may download model assets.

The original code/pseudocode detection, completeness, framework/dependency, classification and snippet-validation algorithms are retained. Local algorithms inspect the supplied text; individual legacy LLM prompts may use bounded portions rather than the entire document. Full supplied text replaces the old machine-specific PDF directory lookup in the public entry point. Standalone document vector search is also available in the web interface and Notebook; the original vector-retrieval classes remain available for advanced Python use.

With `llm_analysis=False`, evaluation scores are labelled heuristic fallbacks. With `execute_code=False`, Python syntax can be checked but snippets are not executed; execution/import/load/assertion states remain explicit. Enabling execution uses the restored `LocalPythonExecutor`. A completed snippet check does not establish paper-level reproducibility or algorithm correctness. Model-assisted extraction is distinguished from verbatim source evidence by the original provenance fields.

## Evolution Mapper and Planner–Worker

Evolution Mapper retains citation-network construction, directed edges, timeline grouping, path analysis and Mermaid reporting. Set `max_papers` from 1 to 60 and optional `start_year`/`end_year`. This caps seed discovery; citation neighbours can add graph nodes. The web interface renders Mermaid and preserves its source; the old `graphviz` argument did not have a distinct renderer and is not offered as an implemented alternative.

Planner–Worker retains planning, argument validation, dependency-aware execution, step statuses and Critic review. It exposes the restored evolution tool and, when supplied, the bound document named `paper` for code analysis. `max_steps` is 1–20 (default 5); `enable_critic` defaults true. Public runs set `max_reexecutions=0`: a Critic request to repeat work is retained in results, not automatically executed. Tools cannot use a document name to access files beyond the supplied document. Each planned step remains in the execution record, including failures, blocked dependencies and unexecuted steps.

## Benchmark Landscape and Citation Gap Analysis

These modules retain their original exploration workflow, rather than replacing the newer Top 5 retrieval path. Benchmark Landscape is a preliminary metadata/heuristic report of detected datasets, metrics, method families and cited papers. It is not a verified systematic benchmark survey. Citation Gap Analysis accepts `manuscript_excerpt` and `known_refs`; it excludes recognized existing references and uses the selected Heavy model to recommend citations and their placement.

The paper limit controls map/assessment sizes; the older exploration loop can discover additional candidates before its stopping checks. These results are separate from the published idea-to-paper Hit@5 evaluation.

## Dependencies and output

```bash
python -m pip install -e ".[extensions,notebook]"
python -m pip install -e ".[mineru]"   # only for structured PDF parsing
python -m pip install -e ".[pdf-rag]"  # optional original vector retrieval
```

MinerU and third-party legacy RAG backends are loaded lazily. An advanced vector PDF extractor uses a separately supplied OpenAI embedding key; parsing a new PDF through that older class also needs MinerU. Other retained RAG backends may require their own SDKs. Their original copyright headers and the bundled [Apache-2.0 license](../rag/LICENSE) are retained.

`result.json` records status, stage, duration, usage and safe errors. `extension.json` contains structured analysis, evidence scope, diagnostic records and the Markdown report; reports can also be downloaded from the web interface. Missing packages include a dependency name and installation hint. Scholarly HTTP failures are recorded even when an older tool catches its exception; failed discovery is not labelled a successful empty result. Failed or partial model calls retain their cause and HTTP status when known. Missing token usage remains unknown.

Current offline and real API acceptance evidence is recorded in [UI_FUNCTIONAL_CHECKLIST.md](UI_FUNCTIONAL_CHECKLIST.md). These small functional checks are not a new scientific extension benchmark. The manuscript's separate cross-paper figure-comparison implementation has not been located in current or archived workspace Python sources and is not advertised as an available feature.


## Document parsing and vector search

Two standalone tasks, document_parse and document_search, use the shared worker and diagnostics. See [WEB_WORKSPACE.md](WEB_WORKSPACE.md) for their parameters, independent embedding profiles, source offsets, MinerU 2.6.3 installation and report previews. The web interface renders Markdown and Mermaid using bundled local libraries. The Notebook previews sanitized reports and retains Mermaid source.
