# Public benchmark tiers

English is the primary documentation language. [中文说明](README.zh-CN.md) is also available.

| Tier | IDs | Queries |
| --- | --- | ---: |
| EASY | N001–N030 | 30 |
| MEDIUM | C001–C030 | 30 |
| HARD | C001–C030 | 30 |

EASY, MEDIUM, and HARD describe intended difficulty based on how much information the prompt supplies. They do not mean the difficulty of all 30 queries has been empirically calibrated. The tiers are not independent target-paper pools: MEDIUM and HARD share targets with different queries. Original query order is preserved. Select by ID; do not assume the first EASY record is N001.

## Dataset construction

GPT-6 Astra in ChatGPT was used to extract and generate the shared literature-search prompts, according to the maintainer's construction record. The same filled prompt for an item is used across Idea2Prior, ChatGPT Deep Research, and Gemini Deep Research. EASY, MEDIUM, and HARD preserve the supplied query versions with different amounts of information; original prompt bytes are retained. Each tier's manifest records this construction provenance separately from the source and file hashes.

## Files and input integrity

Each tier contains `queries.jsonl`, `prompts/<ID>.txt`, `gold.jsonl`, `manifest.json`, and a separate `results/` directory. Every query record contains `item_id`, the original `query`, a relative `prompt_file`, and two SHA-256 hashes. Prompt files preserve original bytes, and query strings preserve original wording. The manifest records hashes of original source files and packaged public files so versions and inputs can be checked.

## Historical comparison outputs

| Tier | ChatGPT web response | Gemini web response | Idea2Prior Top 5 | Structured pipeline outputs |
| --- | --- | --- | --- | --- |
| EASY | [chatgpt.txt](easy/results/chatgpt.txt) | [gemini.txt](easy/results/gemini.txt) | [idea2prior_top5.txt](easy/results/idea2prior_top5.txt) | [JSON](easy/results/idea2prior_results.json) |
| MEDIUM | [chatgpt.txt](medium/results/chatgpt.txt) | [gemini.txt](medium/results/gemini.txt) | [idea2prior_top5.txt](medium/results/idea2prior_top5.txt) | [JSON](medium/results/idea2prior_results.json) |
| HARD | [chatgpt.txt](hard/results/chatgpt.txt) | [gemini.txt](hard/results/gemini.txt) | [idea2prior_top5.txt](hard/results/idea2prior_top5.txt) | [JSON](hard/results/idea2prior_results.json) |

The browser TXT files preserve the supplied October 2026 Deep Research responses byte for byte. The HARD ChatGPT file is the adopted NEW report. Each tier includes the selected ten historical Idea2Prior outputs, their timing and reported usage, and original Expert result JSON under `results/idea2prior_expert/`. Source and export hashes are in `results/manifest.json`. These are historical evaluation materials; runtime retrieval does not read the result directories. Keep them out of new retrieval sessions.

Runtime retrieval reads only `queries.jsonl` and `prompts/`. **`gold.jsonl` is for local evaluation after retrieval has finished.** It contains the designated paper's title, DOI, accepted arXiv records, and identity-ambiguity notes. Keep gold, standard answers, other systems' answers, and previous conversations about the item out of retrieval sessions. Because gold is public, this is not a hidden test set.

## Evaluation policy

The designated paper is a known positive. Broad queries may also match other research. Hit@5 measures whether the designated paper was retrieved; it does not establish that other candidates are invalid. Semantic relevance and relationships between versions still require manual review. An arXiv ID is a record-level association; verify the relationship of historical versions to the journal paper. The original `family_aliases` remain in the evaluation file; strict designated-target hits and work-family hits are scored separately.

The packages include original queries and prompts, reference bibliographic metadata, short curator notes, supplied browser responses, and selected pipeline output files. They do not contain downloaded paper PDFs, extracted full-text caches, credentials, or raw request logs. Third-party papers and provider content retain their own rights.

Verify the packaged files and hashes offline:

```bash
python scripts/build_public_datasets.py
```

For development, supply `--source-root <directory-containing-easy-medium-hard>` to copy and verify canonical tiers into `--output-root`. Normal use does not require a private archive. The tool does not rewrite queries or source files.

After retrieval, calculate designated-target Hit@5 offline:

```bash
python scripts/evaluate_public_results.py --tier easy --results outputs/results.jsonl --items N001 N002 N003 --out outputs/scores.json
```

Each result row contains `item_id`, `status`, and a `top5` array. Candidates can contain `title`, `doi`, `arxiv_id`, or `url`. The default `--track designated` scores the designated target; `--track family` also accepts explicitly recorded work-family aliases. Matching is deterministic and makes no model calls. Title-only matches and arXiv historical versions still need identity review. Missing items remain NOT_RUN and are never imputed as successful.

The main [usage guide](../README.md) covers the Gemini default preset, other APIs, the local web interface, and the Notebook. See [LICENSE](../LICENSE) for project licensing; third-party papers are not relicensed.
