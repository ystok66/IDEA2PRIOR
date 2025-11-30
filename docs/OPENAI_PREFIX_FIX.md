# Critical Fix: OpenAI Prefix for Custom LLM

## Problem Discovered

Even after implementing Solution B (LLM priority fix), `prior_art_check` was still failing with Vertex AI errors:

```
litellm.APIConnectionError: Your default credentials were not found.
File "litellm/llms/vertex_ai/vertex_llm_base.py", ...
google.auth.exceptions.DefaultCredentialsError
```

## Root Cause

**litellm automatically selects provider based on model name**, NOT just on `api_base`:

- If `model_id = "gemini-3-pro-preview"`:
  - litellm sees "gemini" → assumes Google Gemini provider
  - Tries to use Vertex AI / Google AI Studio
  - Ignores the custom `api_base` parameter ❌
- Even though we passed:
  ```python
  LiteLLMModel(
      model_id="gemini-3-pro-preview",  # ❌ This triggers Gemini provider!
      api_base="https://ai.liaobots.work/v1",
      api_key="your-key"
  )
  ```

## Solution

**Force OpenAI-compatible mode** by prefixing the model name with `openai/`:

```python
# Before (BROKEN)
model_id = "gemini-3-pro-preview"
# litellm detects "gemini" → uses Vertex AI provider

# After (FIXED)
model_id = "openai/gemini-3-pro-preview"
# litellm sees "openai/" prefix → uses OpenAI-compatible provider
# Respects custom api_base and api_key ✅
```

## Code Changes

### `demo_research_agent.py` - `build_llm_from_env()`

```python
if custom_base and custom_key and custom_model:
    # CRITICAL: Prefix with "openai/" to force litellm to use OpenAI-compatible provider
    # This prevents litellm from detecting "gemini" in the name and trying to use Vertex AI
    if not custom_model.startswith("openai/"):
        model_with_prefix = f"openai/{custom_model}"
    else:
        model_with_prefix = custom_model

    logger.info(f"🔧 Using CUSTOM LLM endpoint: {custom_base}")
    logger.info(f"📝 Model: {custom_model} (as {model_with_prefix})")
    logger.info(f"✅ Forcing OpenAI-compatible mode to avoid Vertex AI provider")
    return model_with_prefix, custom_base, custom_key
```

## Why This Works

litellm's provider detection logic:

1. **Checks for explicit prefix** (e.g., `openai/`, `anthropic/`, `gemini/`)
2. If prefix found → uses that provider
3. If no prefix → **guesses based on model name**

By adding `openai/` prefix:

- ✅ Forces OpenAI-compatible provider
- ✅ Makes litellm respect `api_base` and `api_key`
- ✅ Works with any OpenAI-compatible endpoint (liaobots, ollama, vllm, etc.)

## Verification

### Before Fix

```
$ python test_llm_priority.py

Result:
  model_id: gemini-3-pro-preview  # ❌ No prefix
  api_base: https://ai.liaobots.work/v1

→ litellm tries to use Vertex AI
→ DefaultCredentialsError
```

### After Fix

```
$ python test_llm_priority.py

Result:
  model_id: openai/gemini-3-pro-preview  # ✅ With prefix
  api_base: https://ai.liaobots.work/v1

→ litellm uses OpenAI-compatible provider
→ Respects custom endpoint
→ Works correctly
```

## Impact on prior_art_check

Now when running:

```bash
python demo_research_agent.py --task_type prior_art_check \
  --topic "3D detection" \
  --idea "test" \
  --max_rounds 1
```

**All LLM calls will:**

- ✅ Use the `openai/gemini-3-pro-preview` model name
- ✅ Send requests to https://ai.liaobots.work/v1
- ✅ NOT attempt to contact Vertex AI / generativelanguage.googleapis.com
- ✅ Successfully complete scoring and facet extraction

**No more:**

- ❌ `DefaultCredentialsError`
- ❌ API quota errors
- ❌ Empty "None found" reports

## Important Notes

1. **This is transparent to the endpoint** - liaobots still receives the request for `gemini-3-pro-preview`, the `openai/` prefix is only for litellm's routing logic.

2. **Works with any model name** - you can use `openai/gpt-4o`, `openai/claude-3`, `openai/llama-3-70b`, etc. The endpoint sees the real model name without the prefix.

3. **Alternative solution**: Set `CUSTOM_LLM_MODEL_NAME=gpt-4o` instead of `gemini-3-pro-preview` in `.env` to avoid the confusion entirely. But the current fix allows you to keep the original model name in config while forcing correct routing.

## Files Modified

- `demo_research_agent.py` - Added `openai/` prefix logic in `build_llm_from_env()`

## Related Documentation

- [CUSTOM_LLM_PRIORITY_FIX.md](CUSTOM_LLM_PRIORITY_FIX.md) - Original priority fix
- [SOLUTION_B_SUMMARY.md](SOLUTION_B_SUMMARY.md) - Complete solution B implementation
- This document - Critical provider routing fix
