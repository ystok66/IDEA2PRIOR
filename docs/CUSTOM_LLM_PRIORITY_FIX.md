# CUSTOM_LLM Priority Fix - Documentation

## Problem

Previously, `demo_research_agent.py` always prioritized `GOOGLE_API_KEY` over custom LLM configurations. This caused all LLM calls to go to Google's Gemini API even when `CUSTOM_LLM_*` environment variables were set.

**Symptoms:**

- User sets `CUSTOM_LLM_API_BASE`, `CUSTOM_LLM_API_KEY`, and `CUSTOM_LLM_MODEL_NAME` for a working endpoint (e.g., liaobots)
- But `demo_research_agent.py` still logs: `🤖 Using Google Gemini model (detected GOOGLE_API_KEY)`
- All LLM calls hit `generativelanguage.googleapis.com` and fail with quota errors

## Solution

### Changes Made

**File:** `demo_research_agent.py`

**New Function:** `build_llm_from_env()`

This function now implements the correct priority order:

```
1. CUSTOM_LLM_* (HIGHEST PRIORITY)
   - CUSTOM_LLM_API_BASE
   - CUSTOM_LLM_API_KEY
   - CUSTOM_LLM_MODEL_NAME

2. GOOGLE_API_KEY (MEDIUM PRIORITY)

3. OPENAI_API_KEY (LOWEST PRIORITY)
```

### Code Changes

**Before:**

```python
if os.getenv("GOOGLE_API_KEY") and not os.getenv("OPENAI_API_KEY"):
    default_model = "gemini/gemini-3-pro-preview"
else:
    default_model = "gpt-4o"
```

**After:**

```python
def build_llm_from_env() -> tuple[str, Optional[str], Optional[str]]:
    # Check custom LLM first (highest priority)
    custom_base = os.getenv("CUSTOM_LLM_API_BASE")
    custom_key = os.getenv("CUSTOM_LLM_API_KEY")
    custom_model = os.getenv("CUSTOM_LLM_MODEL_NAME")

    if custom_base and custom_key and custom_model:
        logger.info(f"🔧 Using CUSTOM LLM endpoint: {custom_base}")
        return custom_model, custom_base, custom_key

    # Then check Google Gemini
    if os.getenv("GOOGLE_API_KEY"):
        logger.info("🤖 Using Google Gemini model")
        return "gemini/gemini-3-pro-preview", None, None

    # Finally fallback to OpenAI
    if os.getenv("OPENAI_API_KEY"):
        logger.info("🤖 Using OpenAI GPT-4 model")
        return "gpt-4o", None, None
```

## How to Use

### Option 1: Use Custom LLM (liaobots, etc.)

Set these in your `.env`:

```env
# Custom LLM (e.g., liaobots) - HIGHEST PRIORITY
CUSTOM_LLM_API_BASE=https://ai.liaobots.work/v1
CUSTOM_LLM_API_KEY=your-liaobots-key-here
CUSTOM_LLM_MODEL_NAME=gemini-3-pro-preview

# Optional: Keep GOOGLE_API_KEY for Gemini File API tools
GOOGLE_API_KEY=your-google-key-here
```

**Result:**

```
🔧 Using CUSTOM LLM endpoint: https://ai.liaobots.work/v1
📝 Model: gemini-3-pro-preview
```

All agent LLM calls will go to liaobots, NOT to Google.

### Option 2: Use Official Google Gemini

Set only:

```env
GOOGLE_API_KEY=your-google-key-here
```

**Result:**

```
🤖 Using Google Gemini model (detected GOOGLE_API_KEY)
```

### Option 3: Use OpenAI

Set only:

```env
OPENAI_API_KEY=your-openai-key-here
```

**Result:**

```
🤖 Using OpenAI GPT-4 model (detected OPENAI_API_KEY)
```

## Verification

### Test 1: Verify liaobots connection

```bash
python test_liaobots_api.py
```

Expected output:

```
HTTP status: 200
--- model reply ---
{
  "ok": true,
  "msg": "hello from liaobots"
}
```

### Test 2: Run a pipeline task

```bash
python demo_research_agent.py --task_type prior_art_check --topic "3D detection" --idea "test" --max_rounds 1
```

Check the logs at the start:

- If using liaobots: `🔧 Using CUSTOM LLM endpoint: https://ai.liaobots.work/v1`
- If using Google: `🤖 Using Google Gemini model (detected GOOGLE_API_KEY)`

## Benefits

✅ **Flexibility**: Choose any OpenAI-compatible endpoint (liaobots, local ollama, etc.)
✅ **Cost Control**: Use cheaper third-party APIs for general chat while keeping Google for special tools
✅ **No More Quota Issues**: Bypass Google's free tier limits by using custom endpoints
✅ **Clear Logging**: Know exactly which LLM is being used

## Notes

- The `GOOGLE_API_KEY` can still coexist with `CUSTOM_LLM_*`
- In future, we could selectively use Google only for `GoogleFileSearchTool` while using custom LLM for agent chat
- For now, whichever is configured with higher priority is used for ALL LLM calls

## Files Modified

1. `demo_research_agent.py` - Main changes to LLM selection logic
2. `test_liaobots_api.py` - Added dotenv loading
3. `docs/CUSTOM_LLM_PRIORITY_FIX.md` - This documentation
