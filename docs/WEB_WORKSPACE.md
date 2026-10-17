# Research workspace

Run the local interface with:

    python -m pip install -e ".[extensions,notebook,pdf-rag]"
    python -m idea2prior web

Open http://127.0.0.1:7860. English is the default; EN / 中文 switches labels.
The same RunConfig and isolated worker serve the web, Python, CLI and Notebook.
The server binds only to loopback. Leave it running while jobs execute.

## Configure and run

1. Write an idea or select an EASY, MEDIUM or HARD benchmark item.
2. Select a task. Paper retrieval supports Top 5 and full-report modes under
   Retrieval settings & output. Other tasks show their own document, year,
   paper-limit and planning controls.
3. Enter API profiles and route Heavy, Light and Expert independently.
   Entering a key and URL triggers model discovery. A manual model ID remains
   available; a listed model does not guarantee generation access.
4. Advanced settings expose years, concurrency, request timeout, output budgets,
   citation expansion and additional pipeline JSON. The default Top 5 protocol
   retains its 60-paper shortlist and five fixed full-text attempts.
5. Check configuration performs a dry run. Start calls only the APIs needed by
   the selected task. Stop cancels the worker process tree; deadlines do the same.

Configuration export omits credentials; import also discards supplied keys.
Custom base URLs remain in configuration files, so review before sharing.
Keys stay in memory and are passed to workers through stdin. No browser storage
contains them. Models can share a key only when their provider and endpoint match.

## PDF / OCR and vector search

Document parsing and document vector search are independent tasks. Upload a PDF,
UTF-8 Markdown or text file up to 12 MiB, or enter its path on the server computer.
Native PDF extraction reads page text. For scanned documents select MinerU:

    # Use Python 3.12 for this optional pipeline.
    python -m pip install -e ".[mineru]"
    mineru-models-download -s huggingface -m pipeline

This extra pins mineru[pipeline]==2.6.3. The model download can be large.
On Windows, an NTFS environment/cache avoids exFAT small-file overhead.
HF_HOME can select the model cache; MINERU_TOOLS_CONFIG_JSON can select the
MinerU configuration file. Parsing can download missing models on first use.
OCR output needs review for recognition and layout errors.

Vector search requires its own explicit embedding profile: OpenAI, native Gemini
or a compatible custom API. It does not reuse a generation model implicitly.
Default chunks are 1,000 characters with 200-character overlap; up to 10 passages
are ranked by cosine similarity. Results retain source page markers when
available, character offsets, scores and complete selected passage text.
There is no persistent vector database or generated answer in this task.
Embedding usage is recorded only when the provider returns it.

Evolution-map year and paper limits apply to seed discovery. Citation neighbors
can add more nodes, including older work outside the seed year range.

## Results and diagnostics

Results adapt to the task: paper cards, sanitized Markdown, Mermaid diagrams,
document passages or structured JSON. Registered reports and attachments have
preview and download controls. The attachment endpoint rejects arbitrary paths.
All rendering libraries are local; model HTML/scripts are never executed.

The page shows status, phase, elapsed time, reported tokens and failure reasons.
PARTIAL means some stages did not complete; it is not an unqualified success.
Missing usage and monetary cost stay unknown. A full report is judged by report
generation, independently of whether a Top 5 ranking exists.

The job URL restores progress on refresh. Completed jobs remain inspectable
after a server restart. Interrupted jobs are marked failed, never auto-resumed.
Each job saves plan.json, result.json and events.jsonl; extension.json and
Markdown reports contain task outputs. Logs may include local paths and API
endpoint configuration, so keep run folders private unless reviewed.

An unchanged benchmark run offers a separate offline evaluation button after
execution ends. Gold records are read only by that evaluation action. Editing
the query disables this association.

## 中文使用要点

网页默认英文，可点击中文切换。检索、完整报告和七种扩展共用 Python / CLI /
Notebook 的配置和运行约定。高级设置提供年份、并发、超时、输出预算和引文开关。
配置导入及导出均不保留密钥；导出仍含自定义端点，分享前请检查。

文档解析支持原生 PDF 文本和 MinerU/OCR。向量检索使用独立 embedding 配置，
默认每块 1,000 字符、重叠 200 字符、最多返回 10 段，并注明可获得的来源位置。
结果可预览或下载；失败、部分完成、取消和超时均保留明确状态与诊断。
运行后单独点击离线评测，检索时不读取标准答案。

See [functional acceptance evidence](UI_FUNCTIONAL_CHECKLIST.md) for measured
outcomes and remaining limitations. Generic database/cloud adapters remain Python
capabilities; the unlocated cross-paper image comparison is not advertised.
