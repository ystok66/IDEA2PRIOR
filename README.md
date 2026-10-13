# Idea2Prior

*Find and Rank Prior Research from an Idea.*

**Find papers from a research idea and rank a Top 5 using abstracts and available full-text evidence.**

English is the primary documentation language. [中文说明](README.zh-CN.md) is also available.

Idea2Prior provides a local web interface, Python API, command line, and Jupyter Notebook. Configure multiple OpenAI, Gemini, or OpenAI-compatible APIs and choose separate models for retrieval and scoring, lightweight tasks, and final Expert ranking.

## Benchmark results: October 2026

Historical comparison on the **first 10 queries of each tier**. ChatGPT and Gemini were tested through their **October 2026 web versions of Deep Research**; Idea2Prior used its API pipeline. Hit@5 is the fraction of queries whose designated reference paper appears in the returned Top 5.

| Query tier | Historical inputs |     Idea2Prior | ChatGPT Deep Research (web) | Gemini Deep Research (web) |
| ---------- | ----------------- | -------------: | --------------------------: | -------------------------: |
| EASY       | v0.2 A, N001–N010 | **90% (9/10)** |                 70% (7/10) |                 70% (7/10) |
| MEDIUM     | v0.5 A, C001–C010 | **50% (5/10)** |                  30% (3/10) |                 30% (3/10) |
| HARD       | v0.5 B, C001–C010 |     20% (2/10) |              **30% (3/10)** |                 10% (1/10) |

EASY follows the agreed reporting convention documented in [benchmark accounting](docs/BENCHMARK_RESULTS.md). Idea2Prior's EASY score uses the final results after rerunning operational failures. These historical scores do not measure the migrated Gemini preset in this public release; each tier contains 30 available queries, of which only the first 10 are compared here.

**Mutual Top 5 coverage** counts shared paper identities for the same query, divided by the fixed number of Top 5 slots. Rank order is ignored.

| Pair                 |          EASY |      MEDIUM |       HARD |
| -------------------- | ------------: | ----------: | ---------: |
| Idea2Prior ↔ ChatGPT | 31.1% (14/45) | 22% (11/50) |  6% (3/50) |
| Idea2Prior ↔ Gemini  | 28.9% (13/45) |  12% (6/50) | 16% (8/50) |
| ChatGPT ↔ Gemini     | 37.8% (17/45) | 20% (10/50) | 14% (7/50) |

**Mean time and API-reported tokens per query:**

| Idea2Prior scope | Queries | Mean runtime | Mean input tokens | Mean output tokens | Mean reported total tokens |
| ---------------- | ------: | -----------: | ----------------: | -----------------: | -------------------------: |
| EASY             |      10 |        8m54s |         288,821.8 |           88,153.6 |                  376,975.4 |
| MEDIUM           |      10 |        9m49s |         304,122.9 |           91,695.7 |                  395,818.6 |
| HARD             |      10 |        7m57s |         302,838.2 |           88,621.0 |                  391,459.2 |
| All three tiers  |      30 |    **8m53s** |     **298,594.3** |       **89,490.1** |              **388,084.4** |

The user-reported web averages are **5.2 minutes/query for ChatGPT** and **4.7 minutes/query for Gemini**; independent per-query timing logs were unavailable. Idea2Prior means use the final selected runs, excluding superseded whole-query attempts. Tokens sum all model stages; **1,491 requests lack usage**, so the reported subtotal is incomplete and complete consumption is unknown.

**Cost reference (USD):** Idea2Prior's user-reported nine-query sample averages **$0.0945 per query**. For the web products, the reference subscription prices checked on **2026-10-13** are [ChatGPT Plus: $20/month](https://learn.chatgpt.com/docs/pricing) and [Google AI Pro: $19.99/month](https://gemini.google/subscriptions/?currencyCode=USD). These are subscription prices; the cited official pages do not establish a fixed price per Deep Research report, and the exact subscriptions used in the experiments were not recorded. API fees, web subscriptions, and the nine-query cost sample have different accounting bases.

See [benchmark accounting and provenance](docs/BENCHMARK_RESULTS.md) and [aggregate data](docs/benchmark_results.json) for the preserved assumptions, denominators, and source fingerprints.

The datasets include the original ChatGPT and Gemini response TXT files and our selected Top 5, structured results, and original Expert outputs. [Browse the three-system result files](datasets/README.md#historical-comparison-outputs).

## Quick start

Requires Python 3.10 or later. In the project directory:

```bash
python -m venv .venv
```

Activate on Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Activate on macOS or Linux:

```bash
source .venv/bin/activate
```

Install and start the web interface:

```bash
python -m pip install -e .
python -m idea2prior web --port 7860
```

Open **http://127.0.0.1:7860**, enter a research idea, and provide your API key and model settings. The default web preset uses native Gemini: `gemini-3.1-flash-lite` for retrieval/scoring and lightweight tasks, and `gemini-3.8-flash` for Expert ranking. Switch between English and Chinese in the page header. Choose a model from the dropdown or enter a custom ID. After you enter a key and a valid API URL, the interface automatically loads that provider's model catalog; native Gemini uses its official URL. **Load models** can refresh the list manually. Discovery sends catalog requests, without generating model responses. A catalog listing does not prove generation access, quota, or routing availability.

The previous retrieval model, `gemini-3.1-flash-lite-preview`, was [shut down by Google on May 25, 2026](https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-lite-preview). The recommended preset uses its official replacement; the original IDs are preserved in [`configs/gemini_previous_workflow.example.json`](configs/gemini_previous_workflow.example.json). Confirm access to the selected models before a real run. No model is substituted silently.

Add multiple API profiles and route each of the three roles independently. **Check configuration** validates the format offline; it does not verify credentials or model availability. **Start retrieval** sends requests to the selected APIs.

The web server listens locally. It shows progress, elapsed time, reported tokens, a stop control, Top 5 results, and JSON downloads. Failure details include the stage, a safe explanation, an error code, and an HTTP status when known. Partial completion preserves the ranking and explains incomplete checks. The URL's opaque job ID restores the view after a page refresh while the same server retains the job; it does not restart retrieval. Run artifacts are saved under `outputs/`, which is excluded from Git.

The Python module and CLI `paperarena` remain available for compatibility; new examples use `idea2prior`.

## API configuration

| Provider | Protocol                           | Settings                                                     |
| -------- | ---------------------------------- | ------------------------------------------------------------ |
| `gemini` | Native Gemini API through LiteLLM  | `model`, plus `api_key` or `api_key_env`; omit `base_url`    |
| `openai` | OpenAI Chat Completions            | `model`, plus `api_key` or `api_key_env`; official endpoint by default |
| `custom` | OpenAI-compatible Chat Completions | `model`, `base_url`, plus `api_key` or `api_key_env`         |

Use model IDs available to your account that support the selected protocol. Custom endpoints must support Chat Completions. Gemini's OpenAI-compatible endpoint can also use `custom`; see [Google's documentation](https://ai.google.dev/gemini-api/docs/openai). See [LiteLLM's documentation](https://docs.litellm.ai/docs/providers/gemini) for the native Gemini adapter.

Example configurations:

- [`configs/gemini_recommended.example.json`](configs/gemini_recommended.example.json): recommended Gemini preset with separate retrieval and Expert models.
- [`configs/gemini.example.json`](configs/gemini.example.json): one Gemini API with a model you select.
- [`configs/openai.example.json`](configs/openai.example.json): one OpenAI API.
- [`configs/multiple.example.json`](configs/multiple.example.json): multiple APIs and explicit role routing.

`roles.heavy` handles retrieval and abstract scoring; `roles.light` handles tasks such as keywords; `roles.expert` handles final ranking. Roles can share one profile or use different providers, models, endpoints, and keys. Each job runs in an isolated child process so concurrent configurations do not overwrite one another. Providers are not switched automatically.

The default publication-year range is **2015–2025**, matching the historical benchmark profile. Set `years` in a configuration file to change this range, or `null` to remove the year restriction.

Copy the recommended preset to `configs/run.local.json`. Prefer environment variables to keys stored in JSON:

```powershell
Copy-Item configs/gemini_recommended.example.json configs/run.local.json
$env:GEMINI_API_KEY = "YOUR_API_KEY"
# For profiles using OpenAI:
$env:OPENAI_API_KEY = "YOUR_API_KEY"
```

```bash
cp configs/gemini_recommended.example.json configs/run.local.json
export GEMINI_API_KEY="YOUR_API_KEY"
# For profiles using OpenAI:
export OPENAI_API_KEY="YOUR_API_KEY"
```

Replace any `YOUR_*_MODEL` placeholders in generic examples before real use. `.env.example` documents variable names; the application does not automatically load a local `.env`. Keys entered in the web interface are used for the current local job and are not written to browser persistent storage, saved run configurations, or logs. Worker configuration is passed through standard input, and exported configurations are redacted.

Retrieval also uses Semantic Scholar, arXiv, Crossref, and optional web search. Set `search.semantic_scholar_api_key_env` to reference a scholarly search key. For SerpAPI, set `search.serpapi_api_key_env` and `enable_serpapi: true`. Otherwise leave `enable_serpapi: false`; supplemental public web search remains available. Source availability can affect recall; identical outputs across environments are not guaranteed.

## Command line

Check the recommended configuration and blind input first:

```bash
python -m idea2prior run --dataset EASY --item N001 --config configs/gemini_recommended.example.json --dry-run
```

A dry run sends no model or search requests. For real retrieval, supply your local configuration and omit `--dry-run`:

```bash
python -m idea2prior run --dataset EASY --item N001 --config configs/run.local.json --timeout-seconds 1800
python -m idea2prior run --dataset MEDIUM --item C001 --config configs/run.local.json
python -m idea2prior run --dataset HARD --item C001 --config configs/run.local.json
python -m idea2prior run --query "Describe your research idea here." --config configs/run.local.json
```

Each invocation creates a new run directory. A job stops after 30 minutes by default, without automatically rerunning the whole item. Use `--output-dir` to change the artifact location or `--mode full_report` for the complete report workflow. After installation, `idea2prior run ...` is also available.

## Python and Notebook

```python
import json
from idea2prior import RunConfig, run_retrieval

with open("configs/run.local.json", encoding="utf-8") as handle:
    config = RunConfig.from_dict(json.load(handle))

result = run_retrieval(
    "Describe your research idea here.", config,
    output_dir="outputs/python", timeout_seconds=1800,
)
for paper in result["top5"]:
    print(paper["rank"], paper["title"], paper.get("url"))
print(result["duration_seconds"], result["usage"])
```

Result statuses distinguish success, partial completion, failure, an empty candidate pool, cancellation, timeout, and a job not run. Producing a ranking does not establish that the designated benchmark paper was found. `usage.reported_token_subtotal` sums only API-reported usage. Complete token totals remain `null` when usage is missing; character counts are not used to estimate cost.

Open [`notebooks/idea2prior_quickstart.ipynb`](notebooks/idea2prior_quickstart.ipynb):

```bash
python -m pip install -e ".[notebook]"
jupyter lab notebooks/idea2prior_quickstart.ipynb
```

The Notebook loads the recommended Gemini preset and defaults to `RUN_REAL_REQUESTS = False`. Its optional model-catalog lookup also defaults to `RUN_MODEL_CATALOG = False`. It calls APIs only after you enable the corresponding operation. Supply keys through environment variables or `getpass`. Failed or partial runs print the stage, safe failure reason, HTTP status when known, and the artifact directory. Clear all execution outputs before sharing the Notebook.

## Retrieval workflow and modes

1. Generate search cues from the research idea and combine model seeds, scholarly retrieval, and available web sources.
2. Deduplicate papers, verify identities, fill in metadata and abstracts, and assign relevance grades: `3 substantial overlap / 2 partial overlap / 1 adjacent research / 0 no overlap`.
3. Expand citation neighbors from relevant seeds. An LLM evaluates new candidates using the research input, search queries, and prepared keywords.
4. Build a shortlist of up to 60 papers, prepare full-text evidence, and produce the final ranking.

`retrieval_eval` is the default. It makes five fixed full-text attempts without adding replacements when PDFs are unavailable. Local extraction selects up to six relevant passages per paper, each limited to 1,800 characters. Expert receives complete shortlist abstracts, abstract grades, available passages, and full-text availability states, then returns the Top 5. Later report generation is skipped.

`full_report` retains the complete report workflow, including more extensive evidence organization and synthesis. Its full-text and final-ranking rules differ from retrieval evaluation; state those differences when comparing metrics.

The calling layer retains batch metadata retrieval, HTTP connection reuse, reuse of valid responses within a job, and bounded concurrency. The public interface adds unified configuration, role routing, and isolated workers that can be stopped. It does not enable automatic whole-item reruns or claim unchanged accuracy by reducing the candidate pool. The default is four simultaneous model requests; adjust `pipeline.max_in_flight_requests` to change it. Expert has a separate output budget; a larger budget can increase cost.

## Public benchmark

| Tier   | Original input | IDs       | Queries |
| ------ | -------------- | --------- | ------: |
| EASY   | v0.2 A         | N001–N030 |      30 |
| MEDIUM | v0.5 A         | C001–C030 |      30 |
| HARD   | v0.5 B         | C001–C030 |      30 |

The CLI accepts `MDEIUM` as a spelling alias; the canonical name is `MEDIUM`. These labels express intended query difficulty, rather than difficulty calibrated by a large evaluation.

Each tier contains `queries.jsonl`, original complete `prompts/`, a SHA-256 manifest, and separate `gold.jsonl`. Dataset runs check query and prompt hashes and send the original complete blind prompt. Gold is read only by the offline evaluator; the web interface exposes no answer endpoint. Free-form and benchmark runs use different input representations. Create a new version when changing benchmark text, rather than overwrite original queries.

Verify the packaged data:

```bash
python scripts/build_public_datasets.py
```

After real CLI retrieval, pass its `output_dir/result.json` to the evaluator:

```bash
python scripts/evaluate_public_results.py --tier easy --results outputs/SELECTED_RUN/result.json --items N001 --out outputs/scores.json
```

It also accepts a multi-item JSON array or JSONL with `item_id`, `status`, and `top5`. For multiple attempts at one item, select the adopted attempt explicitly before evaluation. Metrics distinguish valid ranking hits, operational failures, missing runs, and work-family matches. Missing results are never imputed as successes. A designated target is a known positive; other relevant papers can also have value. See the [benchmark policy](datasets/README.md).

## Validation and repository layout

```bash
python -m pip install -e ".[test]"
python -m pytest tests_public -q
python scripts/audit_public_release.py --json
```

Tests use mocked APIs and local PDF fixtures. They do not run a paid benchmark. Current validation evidence and limitations are recorded in [`docs/VALIDATION.md`](docs/VALIDATION.md).

```text
idea2prior/    Public Python API and CLI entry point
paperarena/    Core runtime, compatibility API, workers, and web interface
pipelines/      Retrieval, identity checks, scoring, and evidence workflow
tools/          Required retrieval and PDF utilities
prompts/        Citation-neighbor scoring rules
datasets/       EASY, MEDIUM, HARD blind inputs and separate gold records
notebooks/      Executable usage example
configs/        Configuration examples without keys
scripts/        Dataset verification, offline evaluation, release checks
tests_public/   Offline API, web, dataset, and core workflow tests
docs/           Usage and validation records
```

Earlier attempts, raw request logs, caches, old dataset directories, and old Git history are preserved outside the public release directory. The selected comparison responses and outputs are included under `datasets/*/results/`. Local working files under `.local/` and generated runs under `outputs/` are excluded from public Git and the release ZIP. Do not upload the private archive or those working directories. Cleanup inventories are retained privately.

## License and commercial use

The code uses [PolyForm Noncommercial 1.0.0](LICENSE). Noncommercial use is governed by its formal terms. **For commercial use, contact the author through this repository's GitHub Issues and obtain a separate commercial license first.** This is a source-available noncommercial license, rather than a permissive license such as MIT or Apache-2.0. The formal English terms control; see the [official PolyForm text](https://github.com/polyformproject/polyform-licenses/blob/1.0.0/PolyForm-Noncommercial-1.0.0.md).

Dependencies retain their own licenses. Paper titles, DOIs, bibliographic records, and links identify literature; this project does not relicense third-party papers or distribute their PDFs or complete text.
