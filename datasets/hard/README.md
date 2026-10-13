# HARD benchmark

This tier contains the original **Idea2Paper Fuzzy30 v0.5 B** inputs: 30 queries with IDs **C001–C030**. Prompt bytes and query wording are preserved. HARD and MEDIUM share targets; HARD supplies shorter, less specific queries.

`queries.jsonl` and `prompts/` are blind runtime inputs. `gold.jsonl` is separate and must be read only after retrieval for local evaluation. `manifest.json` records original sources and SHA-256 hashes.

HARD is an intended difficulty label, not an empirically calibrated score for all 30 queries. See the [benchmark policy](../README.md), [中文说明](../README.zh-CN.md), and main [usage guide](../../README.md).
