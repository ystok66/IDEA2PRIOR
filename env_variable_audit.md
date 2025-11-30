# Environment Variable Audit Report

## ✅ LLM Configuration - Working Correctly

**Verification Result:**

```
[LLM-heavy] Using CUSTOM endpoint: https://ai.liaobots.work/v1 (model=openai/gemini-2.5-flash-thinking)
[LLM-light] Using CUSTOM endpoint: https://ai.liaobots.work/v1 (model=openai/gemini-2.5-flash-thinking)
```

Both heavy and light roles correctly load from your `.env` configuration.

---

## ⚠️ Issues Found

### 1. `CUSTOM_LLM_MODEL_NAME` - Deprecated (Low Priority)

**Files still using old variable:**

- `test_custom_llm_endpoint.py` (Line 112)
- `test_liaobots_api.py` (Line 11)
- `test_llm_priority.py` (Lines 26, 61, 73)

**Impact:** Low - These are test scripts only  
**Recommendation:** These test files are outdated and can be **ignored or deleted**. The new system uses `LLM_HEAVY_MODEL` and `LLM_LIGHT_MODEL` instead.

---

### 2. `Semantic_Scholar_*` - Inconsistent Naming (Medium Priority)

**Current usage in code:**

- `Semantic_Scholar_URL` (mixed case, with underscore)
- `Semantic_Scholar_API_key` (mixed case, with underscore)

**Your `.env` has:**

```bash
Semantic_Scholar_URL=...
Semantic_Scholar_API_key=...
```

**Files using these:**

- `pipelines/clients/semantic_scholar_client.py`
- `tools/semantic_scholar_search.py`
- `tools/paper_resolver.py`

**Recommendation:** Your `.env` and code **match**, so this is fine. However, Python convention prefers `UPPERCASE_WITH_UNDERSCORES` for env vars. Consider standardizing to:

```bash
SEMANTIC_SCHOLAR_URL=...
SEMANTIC_SCHOLAR_API_KEY=...
```

Then update the code to use the uppercase versions. **NOT urgent** - current setup works.

---

### 3. `SERPAPI_API_KEY` vs `SERP_API` - Already Correct ✅

**Status:** Good! Code already uses `SERPAPI_API_KEY`, which matches your `.env`.

---

## 📋 Summary

| Variable                      | Status        | Action Needed                       |
| ----------------------------- | ------------- | ----------------------------------- |
| `LLM_HEAVY_*` / `LLM_LIGHT_*` | ✅ Working    | None                                |
| `CUSTOM_LLM_API_BASE/KEY`     | ✅ Working    | None                                |
| `CUSTOM_LLM_MODEL_NAME`       | ⚠️ Deprecated | Delete old test files (optional)    |
| `Semantic_Scholar_*`          | ⚠️ Mixed case | Standardize to uppercase (optional) |
| `SERPAPI_API_KEY`             | ✅ Correct    | None                                |

**Priority Actions:**

1. ✅ **Your current `.env` is correct and working**
2. 🔧 (Optional) Delete outdated test files: `test_llm_priority.py`, `test_custom_llm_endpoint.py`
3. 🔧 (Optional) Standardize `Semantic_Scholar_*` to uppercase in future refactoring
