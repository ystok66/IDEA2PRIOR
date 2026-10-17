# UI functional verification — 2026-10-17

This is a functional acceptance record, not a new benchmark comparison. Existing
benchmark datasets, selected historical results and manuscript files were not
changed. This acceptance record was finalized locally before publication.

The requested entries are implemented. Acceptance is **partial**: six of ten
one-shot functional runs succeeded, two produced partial results, and two failed.
Offline regression passed **386 tests and 99 subtests in 113.31 seconds**.
A subsequent focused report-contract regression also passed, explicitly covering
full reports without a Top 5 and missing-report failures. A successful button click
or HTTP response is not counted as a successful task.

## Status vocabulary

- **Offline pass**: local fixtures, mocked services, actual local worker processes,
  or a browser fixture; external model calls were not made.
- **Real pass**: the selected local parser or external service produced usable
  output through the actual web entry point, within the stated small test scope.
- **Partial**: useful output exists, but checks, evidence or quality remain incomplete.
- **Failed / blocked**: no usable task output; the failure stage and reason are retained.
- **Not run**: no evidence for that check. Fixing source does not retroactively pass a run.

## Functional checklist

| Entry | Expected behavior | Test and actual result | Fix / evidence |
| --- | --- | --- | --- |
| English / 中文, desktop and narrow screen | English default, readable controls, keyboard focus | Browser pass at desktop and 390 px; no horizontal overflow; Tab moved EN → 中文; no console errors in the clean workspace | Public screenshots in images/; narrow-screen fixture capture remains private |
| API profiles / role selectors | Multiple providers; Heavy, Light, Expert routed independently | Offline pass; browser import restored three profiles and roles; real custom catalog returned 113 model IDs; manual model IDs used in live runs | Same-provider, same-endpoint key reuse; API tests and dry-run plan |
| Model discovery / manual IDs | Catalog requests follow the selected API; failure remains actionable | Real catalog pass on the private compatible endpoint; embedding model access failed separately with HTTP 404 | A catalog listing does not establish generation or embedding access |
| Configuration import / export | Restore settings without importing keys; omit credentials from downloads | Browser pass: three-profile import left key inputs empty; downloaded two-profile export parsed and contained none of the known private keys or credential fields | Export keeps custom endpoint URLs; users must review URLs before sharing |
| Retrieval controls | Free input; EASY / MEDIUM / HARD; two modes; advanced values reach worker | Offline pass for all tiers; browser dry run recorded years 2015–2025, concurrency 3, timeout 90, budgets 4096 / 12000, two rounds, citation expansion off | Production defaults and fixed Top 5 protocol retained; dry run made no model calls |
| Top 5 retrieval | Rank up to five papers; retain diagnostics | **Real failed** before ranking, 27.453 s, local file lock | Bounded atomic-write retry added and regression-tested; **live rerun not run** |
| Full report | Report completion independent of Top 5; preview and download | **Real partial**, 664.156 s; one Markdown report and five ranked papers | Artifact registry and preview/download UI added; full-text and model failures remain visible |
| Code & reproducibility analysis | Uploaded document; flags honored; complete report | **Real pass**, 12.250 s; two code blocks and one pseudocode block | Small illustrative Markdown fixture; model analysis enabled, code execution disabled |
| Technology evolution map | Paper/year limits; rendered graph and source | **Real pass**, 22.859 s; scholarly network and Mermaid output | Year controls added; the actual graph and labels rendered in browser; limits apply to seeds, while citation-neighbor nodes can be older and more numerous |
| Planner–Worker | Bounded tools and Critic; no automatic re-execution | **Real partial**, 20.219 s; tool completed, Critic rejected completeness | Critic diagnostics and report feedback added; 1000-character truncation replaced with a labelled 12000-character window; **live verification of fix not run** |
| Benchmark Landscape | Bounded discovery and metadata report | **Real output obtained**, 6.922 s; year propagation issue found afterwards | Year range now reaches both discovery intent and configuration; regression passed; **live year-filter verification not run** |
| Citation gap analysis | Draft excerpt and known references; scored suggestions | **Real pass**, 12.843 s; three model assessments | Validated grades and explicit failures retained; small draft fixture only |
| Document parsing / native | Extract text, retain source positions, handle empty/corrupt PDFs | **Real local pass**, 11.343 s; two pages, two chunks | Independent entry added; empty/corrupt input tests passed |
| Document parsing / MinerU | Image-only PDF produces OCR text | **Real local pass**, 41.078 s; 931 characters extracted from a scanned fixture | Lazy imports fixed; mineru[pipeline]==2.6.3 and official weights installed in isolated Python 3.12 environment |
| Document vector search | Independent embedding credentials; 1000/200 chunks; top 10 passages with positions | Offline ranking/payload pass; **real blocked**, 8.000 s, one request, HTTP 404 | List splitting and explicit-key handling fixed; selected private route/model was unavailable; no fallback or rerun |
| Progress, time, tokens, errors | Accurate state and actionable diagnostics | Browser observed running, success, partial and failure; structured usage and stage errors retained | Missing usage remains unknown; grouped errors do not count as new requests |
| Stop / timeout / refresh / restart | Stop worker tree; preserve evidence; never resume automatically | Offline process cancellation and deadline tests passed; browser Stop changed synthetic RUNNING → CANCELLED; real completed-job refresh/restart passed | Job identity persisted; blind benchmark association survives restart; interrupted work marked failed |
| Reports / JSON / passages / diagrams | Task-specific presentation and registered file access | Browser preview/download passed; local Markdown and Mermaid rendered; unsafe script, event-handler and javascript-link fixture removed | Only registered in-job artifacts served; traversal and cross-job access rejected; JSON remains downloadable |
| Post-run evaluation | Gold read only after explicit finished benchmark evaluation | Browser pass on completed full-report N001: designated target rank 2, Hit@5 true | Standalone offline action; edited-query and active-job rejection tests passed; this single case is not a new aggregate score |
| Python / CLI / Notebook | Shared configuration and result semantics | Offline API/CLI tests passed; actual Python 3.12 kernel executed independent Notebook copy, including native PDF, corrupt-PDF diagnostics and vector fixture | Public Notebook has empty outputs; no second paid Notebook run; Mermaid source retained in Notebook, diagrams rendered on web |

## One-shot execution ledger

All ten tasks were launched through the real web interface, sequentially, with a
30-minute limit per task and no automatic whole-task rerun. Parser tasks use local
models or extraction. Evolution and landscape tasks use scholarly HTTP requests
without generation calls. Optional extension examples were deliberately small;
the EASY N001 retrieval inputs and Top 5 protocol were not shortened.

| Task | Saved status | Seconds | Model attempts | Reported token subtotal | Missing-usage attempts |
| --- | --- | ---: | ---: | ---: | ---: |
| Native parsing | SUCCEEDED | 11.343 | 0 | — | 0 |
| MinerU OCR | SUCCEEDED | 41.078 | 0 | — | 0 |
| Top 5 / EASY N001 | FAILED | 27.453 | 2 | 1211 | 1 |
| Full report / EASY N001 | PARTIAL | 664.156 | 301 | 316233 | 13 |
| Code Extractor | SUCCEEDED | 12.250 | 2 | 929 | 0 |
| Evolution Mapper | SUCCEEDED | 22.859 | 0 | — | 0 |
| Planner–Worker | PARTIAL | 20.219 | 4 | 3311 | 0 |
| Benchmark Landscape | SUCCEEDED | 6.922 | 0 | — | 0 |
| Citation gap analysis | SUCCEEDED | 12.843 | 3 | 1324 | 0 |
| Document vector search | FAILED | 8.000 | 1 | unknown | 1 |

Total observed model attempts: **313**. Reported token subtotal: **323008**;
**15 attempts lack usage**. Total actual consumption and billable cost are unknown.
These are model-adapter attempts, not a count of all web/catalog/scholarly HTTP
requests or a provider invoice. Setup, model downloads, browser interaction and
rendering checks are outside task durations. Zero model attempts do not assert
that a local OCR model consumed no compute.

The full-report run included 13 model-request failures (HTTP 400) and unavailable
full-text material. It retained available evidence and produced a report; those
failures are not silently treated as successful checks. Planner's saved result
predates the clearer Critic diagnostic; its original JSON remains unchanged.
The OCR run's original scope label said native extraction; source now labels
MinerU/OCR correctly. The parser field and actual OCR output establish which path ran.

## Fixes and evidence locations

The public machine-readable summary is [ui_acceptance_results.json](ui_acceptance_results.json).
It includes query/configuration/result hashes, task times, usage and safe error
codes. Raw configurations and logs remain under ignored .local/ui_acceptance/;
they can contain private endpoints or local paths and are not release assets.

| Evidence | Private relative location |
| --- | --- |
| Final full suite: 386 tests + 99 subtests | .local/ui_acceptance/offline_20261017_121400/pytest.log and junit.xml |
| Report completion contract regression | .local/ui_acceptance/report_contract_regression.log |
| Earlier full suite: 384 tests + 99 subtests | .local/ui_acceptance/offline_20261017_120144/pytest.log |
| Real run inputs, plans, events, results and reports | .local/ui_acceptance/browser_jobs/<job_directory>/; directory IDs are in the public JSON |
| Executed Notebook and status | .local/ui_acceptance/notebook_20261017_113001/ |
| Native/scanned/corrupt PDF fixtures and hashes | .local/ui_acceptance/fixtures/ |
| MinerU official weight download log | .local/ui_acceptance/mineru_model_download.log |
| Browser findings, export checks and synthetic renderer/cancel checks | .local/ui_acceptance/browser_acceptance.json |
| Built wheel: new modules, local renderer/license and all 90 blind prompts present | .local/ui_acceptance/wheel_build.log and wheel_inspection.json |
| Source/evidence SHA-256 manifest | .local/ui_acceptance/evidence_manifest.json |
| Public-file credential scan | .local/ui_acceptance/release_audit.json |

Initial setup checks encountered temporary-directory permission problems, an
optional-dependency issue, a Notebook kernel-manager construction error, and
Windows connection resets. Subsequent checks used an isolated Python 3.12.12
runtime and explicit scratch directories. A focused regression also caught a
Critic dataclass/dict conversion error introduced while adding feedback; it was
fixed before the final 386-test pass. Those earlier failed logs are retained.
The final source fingerprint identifies delivery state, not an exact retrospective
code snapshot for every earlier live run.

## Remaining limitations / not run

- Top 5 live validation after the file-lock fix remains **not run**, as required by
  the no-rerun limit. The separate full-report run does not substitute for that test.
- Live checks of the expanded Critic input window and corrected landscape year
  propagation remain **not run**. Their regression checks passed offline.
- Live vector ranking remains **blocked** for the tested embedding route/model.
  A working embedding provider is required; HTTP 404 is not a vector-search pass.
- Official native Gemini and official OpenAI service credentials were not used
  for additional live calls. Protocol support and error paths are covered offline;
  the private compatible endpoint used in this acceptance is not a validation of
  every provider/model combination or the public preset's benchmark accuracy.
- No paid generation calls were repeated from the Notebook. The actual-kernel
  Notebook run used safe examples, local parsing and mocked embeddings.
- Extracted-code execution was not enabled in the real example. Offline fixtures
  cover that path; arbitrary papers' code cannot be promised to run correctly.
- OCR recognition was tested on one image-only page, not a large multilingual
  corpus. Notebook shows Mermaid source; graph rendering is available on the web.
- Generic database/cloud adapters remain Python-only. The unlocated paper-image
  comparison feature is not advertised as implemented.
- Automated and small-sample functional checks do not establish relevance,
  scientific correctness, production reliability or complete accessibility.

## 中文结论

界面入口、参数、报告预览/下载、OCR、独立向量配置和离线评测均已补齐。
全量离线测试通过；十个实际任务中六个成功、两个部分完成、两个失败。
Top 5 的文件锁问题、Critic 截断与诊断、Landscape 年份传递已修复并离线验证，
但按“不自动整题重跑”的约定没有再次发起真实任务。向量接口返回 404，不能算通过。
所有原始失败和部分结果均保留；README 图片来自实际界面，未包含私有密钥、端点或本机路径。
