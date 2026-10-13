# Public release validation

This record distinguishes source changes and offline checks from real retrieval experiments. Historical run ledgers are preserved privately; selected comparison outputs are included in the datasets. They do not validate this newly packaged release or its migrated Gemini model preset.

## Completed work

- Added a shared Python API, command line, local web interface, and Notebook entry point.
- Added explicit OpenAI, native Gemini, and custom OpenAI-compatible profiles, with separate `heavy`, `light`, and `expert` role routing.
- Added English-first documentation and an English/Chinese interface. Model IDs can be selected from the chosen provider's catalog or entered manually. Model discovery uses catalog requests only.
- Preserved the original blind prompts and queries for EASY, MEDIUM, and HARD, with SHA-256 manifests. Gold remains separate from runtime inputs.
- Retained retrieval evaluation's fixed five full-text attempts, no PDF replacements, local passage extraction, complete shortlist abstracts, and Expert ranking without report generation.
- Moved historical experiments, credentials, unrelated code, and the previous Git history to a private sibling archive. The previous history contained a credential and must not be published. Fresh public Git history is separate; no GitHub upload has been performed.

## Checks and limitations

Public dataset names and source-copy tools now consistently use `easy`, `medium`, and `hard`. Private backups retain the historical labels. Only dataset-label metadata was renamed in reference records; paper identities and all 90 original prompts remain unchanged. The pricing references use official Deep Research API models and agents. OpenAI illustrations reprice the known mean token workload; Google's typical-task estimates are identified separately, with the equivalent-workload Gemini bill undetermined. Browser benchmark results remain identified as web evaluations. An initial documentation draft used general model prices; it was corrected to Deep Research pricing before the updated release export.

The canonical source-copy path was checked for all three tiers: every copied manifest file matched its source bytes, self-copy performed verification only, and nested source/output directories were rejected. Seven existing dataset tests passed. The API estimate calculations and all displayed coverage fractions were checked against aggregate JSON; local documentation links and table column counts were also verified. No paid API requests were made.


The README now includes the preserved October 2026 three-system benchmark comparison. [BENCHMARK_RESULTS.md](BENCHMARK_RESULTS.md) records the agreed EASY Hit@5 reporting convention, uniform 50-slot requested-scope coverage, selected-run timing, incomplete token usage, and the separate nine-query cost sample. EASY coverage was checked against the original ten-query source comparison: its shared-paper counts are 14, 15, and 17, giving 28%, 30%, and 34%. All coverage percentages and fractions are consistent across the English/Chinese README and aggregate JSON. The prompt-construction attribution to GPT-6 Astra in ChatGPT is maintainer-provided provenance. These edits made no paid API calls or new retrieval runs and did not change source outputs or blind prompt bytes. Historical results are separate from the interface trials below and do not validate the migrated public preset.

The public project is now named **Idea2Prior**. Its web labels, README files, Notebook, distribution name, and new `idea2prior` Python/CLI entry points agree. The existing `paperarena` module and CLI remain compatible. All four dataset CLI dry runs passed with networking blocked; the nine existing web tests passed, and the running local server served the new English/Chinese branding. The rebuilt wheel includes both entry points, all 90 blind prompts, and the required citation/web resources; it imported and completed an offline dry run outside the source checkout. The public Notebook's code cells parsed successfully and its outputs remain empty. These checks made no model requests. An initial local wheel-check helper imported nonexistent resource functions; the helper was corrected to use the existing resource API, without changing retrieval code.

Each tier now includes supplied ChatGPT/Gemini response TXT files, selected pipeline Top 5 exports, structured results, and original Expert JSON. Six external reports and 30 Expert result files were copied with source SHA-256 checks and a credential/private-path scan. Completion states and incomplete API usage retain their original meanings. An export helper initially encountered protected historical temporary files and a legacy summary schema without a separate Expert-status field; access was resolved and the status was read from the original Expert result. No model request, new benchmark run, or ranking change occurred during this export. GitHub publication and local commits remain deferred at the user's request.

The exported outputs exposed an offline identity-matcher bug: an explicitly accepted arXiv record's DOI was treated as a conflicting publication DOI. The [arXiv record](https://arxiv.org/abs/2309.11271) lists its DataCite DOI, while the [ACL publication](https://aclanthology.org/2023.sigdial-1.4/) has the publication DOI already recorded in the dataset. The evaluator now recognizes the dedicated arXiv DOI namespace only when the record is explicitly accepted; unrelated publication DOIs and unaccepted records still reject a match. The seven dataset tests passed, including positive and conflicting-identifier cases. Offline rescoring of the unchanged selected outputs returned 9/10, 5/10, and 2/10 for EASY, MEDIUM, and HARD. The initial verification is preserved privately. No candidate, ranking, blind prompt, or gold identity was edited to obtain these scores.

The release checks use mocked providers, local fixtures, and localhost HTTP requests. External sockets are blocked during the offline test suite. Dataset verification compares all 90 prompts and query hashes with their manifests.

The recorded suite passed **358 tests and 96 subtests** in **52.86 seconds**, with zero real model API calls in that suite. Both execution modes and worker model observers imported successfully with external sockets blocked. The public Notebook's safe cells executed with both network guards disabled, returning `NOT_RUN`, with zero external connection attempts and zero gold-file reads. No Notebook outputs or execution counts are included in the published file.

EASY, MEDIUM, HARD, and the `MDEIUM` alias passed CLI dry runs. The installed wheel passed checks from outside the source checkout: all 90 blind prompts, web assets, and the citation-scoring rubric were readable, and retrieval dry-run returned `NOT_RUN`. Browser checks confirmed English/Chinese switching, model selection, a missing-key catalog message, extra API profiles, and a custom Expert configuration check without external requests.

Initial tests encountered local temporary-directory restrictions and a missing optional test dependency. Validation uses an explicit workspace temporary directory and private local test dependencies to avoid changing global Python packages. These were environment failures, rather than benchmark misses.

Earlier cleanup encountered Windows directory handles and a residual reporting `STATUS_DELETE_PENDING`. The old dataset directory shell, audit residue, build directory, two package-metadata directories, and two root caches have now been moved to the private sibling archive. All 231 accessible files in those seven directories matched their pre-move hashes. Historical payloads retain their earlier verified archives. An inaccessible residual is not asserted to be empty. Private manifests retain the exact paths and archive details; these obsolete directories are absent from the public root and release package.

The original `gemini-3.1-flash-lite-preview` model is [retired by Google](https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-lite-preview). The public native-Gemini preset uses `gemini-3.1-flash-lite`, with `gemini-3.8-flash` for Expert. Original model names are retained in a separate historical example. This is a visible configuration change; retrieval accuracy, timing, and token consumption with the migrated preset have **not** been measured.

## Real interface trials

Two intended trials on 2026-10-13 used a user-supplied OpenAI-compatible endpoint with the historical Gemini model IDs. Its credentials and URL are private test inputs, never public defaults. The web trial was submitted through the actual **Start retrieval** button. The Notebook trial executed the `.ipynb` cells in a real Jupyter kernel. The public Notebook remains unexecuted; executed copies and request ledgers are private.

| Interface | Input | Status | Retrieval time | Model attempts | Reported token subtotal | Designated target |
| --- | --- | --- | --- | ---: | ---: | --- |
| Web | EASY N001 | PARTIAL, Expert succeeded | 492.462 s (8m12s) | 254 | 303,689 | Rank 1, normalized title match |
| Notebook | MEDIUM C002 | PARTIAL, Expert succeeded | 549.452 s (9m09s) | 295 | 385,752 | Rank 1, exact DOI match |

The mean retrieval duration of these two cases is **520.957 seconds (8m41s)**. These are functional interface checks; two positive cases do not establish general benchmark accuracy. Complete token consumption and billing remain unknown: 3 web model attempts and 14 Notebook model attempts failed without reported usage. The two reported subtotals sum to 689,441 tokens.

Web Top 5:

1. [µ-shapes: Delineating urban neighborhoods using volunteered geographic information](https://josis.org/index.php/josis/article/view/67)
2. Representations of an Urban Ethnic Neighbourhood: Residents' Cognitive Boundaries of Koreatown, Los Angeles
3. Redefining Neighborhood Boundaries Using Activity Spaces: Bringing Together Soft Spaces and Spatial Analysis to Further the Discussion on Planning in Functional Spaces
4. Methods for neighbourhood Mapping, boundary agreement
5. Where is Huaqiangbei? Identifying the evolutional vague region of urban business district using toponym co-occurrence on web pages with multiple time series

Notebook Top 5:

1. [The Bounded Laplace Mechanism in Differential Privacy](https://doi.org/10.29012/jpc.715)
2. [Differential Privacy via a Truncated and Normalized Laplace Mechanism](https://doi.org/10.1007/s11390-020-0193-z)
3. [Truncated Laplace and Gaussian mechanisms of RDP](https://doi.org/10.48550/arxiv.2309.12647)
4. [Bounded and Unbiased Composite Differential Privacy](https://doi.org/10.1109/sp54263.2024.00108)
5. [Meeting Utility Constraints in Differential Privacy: A Privacy-Boosting Approach](https://doi.org/10.1109/sp61157.2025.00064)

The endpoint's live catalog returned 113 model options. Some generation requests nevertheless reported no available model channel (HTTP 400); a listed model is not guaranteed to have a usable channel on every request. Additional partial-completion issues concerned identity verification and full-text retrieval. The Notebook attempted five fixed PDFs, obtained three, and added no replacements. Original records and all incomplete checks are retained.

An initial Notebook attempt selected native Gemini instead of the intended compatible endpoint and failed before ranking: the provider reported depleted prepaid credits (402), wrapped by the adapter as 500. It is kept separately and excluded from the two intended trials. Diagnostics now identify the original billing cause. A local test configuration also omitted an `os` import and was stopped before any model request; it was corrected before the compatible Notebook trial. An initial Windows summary export decoded a UTF-8 title incorrectly, producing a false evaluator miss; the export was corrected using explicit UTF-8 without changing the raw ranking or dataset. Earlier failed artifacts remain private.

## Optional-extension restoration: 2026-10-13

The earlier release cleanup moved functional modules as well as experiment residue; that scope was too broad. The original archive was retained and its hashes were verified. Seventy-seven original Python source files, including the RAG support package, were copied back before adapting their public entry points. The private originals remain unchanged. Code Extractor, Evolution Mapper, Planner–Worker, Benchmark Landscape and Citation Gap Analysis now have independent Python API/CLI, web and Notebook entry points. Default retrieval does not call them.

Fixed machine-specific document paths, eager optional imports and implicit `.env` loading were removed from the public path. Models use explicit profiles; unused extension roles do not require keys. Code execution is opt-in. Failed legacy scholarly requests, failed citation assessments and incomplete planner steps produce explicit diagnostics. Citation scores validate grades and JSON booleans; parse failures are not counted as validated zero relevance. Code reports show NOT_RUN/NOT_APPLICABLE accurately and use the actual UTC evaluation time rather than the source file's modification timestamp.

The complete offline regression passed **370 tests and 96 subtests** in 61.53 seconds, with external sockets blocked. Subsequent citation-validation, failure-reporting and document-boundary changes were checked in the targeted extension/API/web/diagnostics suite. Tests use synthetic local documents and mocked models/scholarly responses; no new paid model calls or retrieval benchmark were run. These checks establish interface behavior, not scientific extraction accuracy.

The public Notebook was executed in a real Jupyter kernel with external network access blocked and real-request guards off. A separate kernel trial ran local code analysis with `llm_analysis=False`, succeeded, and recorded zero model calls. Executed copies remain private; the public Notebook has empty outputs and execution counts. Browser checks exercised the actual extension start control, native document analysis, missing-file failure and English/Chinese switching. User-facing reports can be previewed and downloaded separately from Top 5 results.

Final targeted checks passed **48 extension/API/web/diagnostics tests**; a further 13-test extension run checked the revised document-boundary fixture. The rebuilt wheel contains all five extensions, their required source modules, the RAG license and all 90 blind prompts. It was imported outside the source checkout, checked byte-for-byte against the current key modules, and completed a local code-analysis trial with zero model calls. The CLI extension command also completed that local trial. A synthetic PDF was selected through the actual browser file chooser, uploaded through the form and parsed successfully with the native parser; the UI showed Completed and No model calls. The browser check found and corrected a variable shadowing the DOM document before final export. Package-structure validation initially overlooked a declared package-directory alias; the helper was corrected, with no package code change.

Initial checks found a relative document path being resolved against the worker's isolated directory; paths are now resolved in the caller before dispatch. An initial test invocation used the protected Windows system temporary directory; subsequent checks used the workspace scratch directory. Two mocked fixtures initially used the wrong search-return shape and an incomplete Critic JSON schema; they were corrected to match the implementations. Failed local attempts and logs remain private. No failed scientific benchmark was replaced by these checks.

README now acknowledges the PaperArena paper/repository, places Deep Research cost references after timing/token accounting, and explains the shared blind-prompt structure and the idea-to-paper-only evaluation scope. Broader webpage/forum search remains limited and unbenchmarked. Bundled third-party sources retain their notices and license terms.

## Private archive and storage

The archive preserves 71,246 distinct original files, including earlier Git history and all historical experiment records. Exactly 715 files with explicit 2025-12 experiment provenance were compressed privately: 54,784,556 logical bytes became a 23,453,402-byte ZIP. Every entry was decompressed and matched against its original SHA-256 before duplicate source files were removed. Current-version and ambiguous records were kept uncompressed. On the local drive's 1 MiB allocation units, the listed files' allocated-space reduction is estimated at 687 MiB; this estimate excludes retained empty directories and unrelated new files.

Token totals cover only usage reported by model adapters. Unknown usage remains unknown; neither complete cost nor a billing total is inferred.

## NOT_RUN

- Paid-model trials for the restored extensions, live scholarly-network validation, MinerU structured parsing/model downloads, and the optional legacy vector-PDF path. Local/native code analysis and mocked extension flows were verified.
- The manuscript's figure-comparison implementation was not located in either the current or archived workspace Python sources; it remains unavailable and is not advertised as restored.
- Successful generation with the recommended native-Gemini preset. Its initial native-provider attempt was rejected for depleted credits.
- A fresh benchmark comparing migrated Gemini defaults with historical models.
- Real end-to-end full-report generation and manual Jupyter-browser interaction. The `.ipynb` itself was executed in a real Jupyter kernel.
- GitHub publication and a public hosted service. The web server is designed for local use.

The public license is PolyForm Noncommercial 1.0.0. Commercial use requires separate authorization from the authors. Third-party dependencies and papers retain their own rights.
