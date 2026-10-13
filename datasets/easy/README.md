# EASY benchmark

This tier contains the original **EASY** inputs: 30 queries with IDs **N001–N030**. Prompt bytes and query wording are preserved. Original query-file order is preserved; select by ID rather than line position.

`queries.jsonl` and `prompts/` are blind runtime inputs. `gold.jsonl` is separate and must be read only after retrieval for local evaluation. `manifest.json` records original sources and SHA-256 hashes.

EASY is an intended difficulty label, not an empirically calibrated score for all 30 queries. See the [benchmark policy](../README.md), [中文说明](../README.zh-CN.md), and main [usage guide](../../README.md).
