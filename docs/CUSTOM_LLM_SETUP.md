# Custom LLM Endpoint Setup Guide

This guide shows you how to configure PaperArena to use a custom OpenAI-compatible LLM endpoint.

## Quick Start

### 1. Configure Your Environment

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Then edit `.env` and fill in your actual API keys:

```bash
# Your custom endpoint configuration
CUSTOM_LLM_API_BASE=https://ai.liaobots.work/v1
CUSTOM_LLM_API_KEY=
CUSTOM_LLM_MODEL_NAME=gpt-4o-mini

# Google Gemini (for File API)
GOOGLE_API_KEY=your-actual-google-key-here
GEMINI_MODEL_NAME=gemini-1.5-flash

# OpenAlex (free, just needs email)
OPENALEX_MAILTO=your-email@example.com
```

⚠️ **IMPORTANT**:

- The API key shown above (`pkMC39rcEIEqi`) was publicly exposed and should be regenerated immediately!
- Never commit `.env` files to version control (it's already in `.gitignore`)

### 2. Test Your Configuration

Run the test script:

```bash
python test_custom_llm_endpoint.py
```

This will:

1. List all available models from your custom endpoint
2. Test a simple chat completion
3. Show you recommended models for different use cases

### 3. Choose Your Model

After running the test, you can update `CUSTOM_LLM_MODEL_NAME` in `.env` with one of:

**Cost-effective options:**

- `gpt-4o-mini` - Fast and cheap, good for most tasks
- `claude-3-5-haiku-20241022` - Claude's fast model

**Balanced options:**

- `gpt-4o` - GPT-4 Omni, good balance
- `claude-3-5-sonnet-20241022` - Claude's best model

**Reasoning tasks:**

- `deepseek-reasoner` - Good for complex reasoning
- `o1-mini` - OpenAI's reasoning model

## Architecture

PaperArena now uses **two separate LLM configurations**:

### 1. Custom Endpoint (PRIMARY)

- **Used for**: All agent interactions, code generation, research queries
- **Configuration**: `CUSTOM_LLM_API_BASE`, `CUSTOM_LLM_API_KEY`, `CUSTOM_LLM_MODEL_NAME`
- **Why**: Cost-effective, supports multiple providers

### 2. Google Gemini (SPECIALIZED)

- **Used for**: Gemini File API, PDF RAG, multimodal features
- **Configuration**: `GOOGLE_API_KEY`, `GEMINI_MODEL_NAME`
- **Why**: Official Google API for File API features

These run **completely independently** - no conflicts!

## How It Works

### Original Flow (OpenAI only):

```
User Request → OpenAI API → GPT-4 → Response
```

### New Flow (Custom Endpoint):

```
User Request → Custom Endpoint → Multiple LLM Options → Response
                                   ├─ GPT-4o-mini (cheap)
                                   ├─ Claude-3.5-Sonnet (quality)
                                   ├─ DeepSeek (reasoning)
                                   └─ ... many more
```

### Gemini File API (Unchanged):

```
PDF Upload → Google File API → Gemini 1.5 Flash → RAG Results
```

## Troubleshooting

### Test fails with "invalid API key"

1. Make sure you copied your key correctly to `.env`
2. Check if the key has been regenerated (if it was exposed)
3. Verify the endpoint URL is exactly: `https://ai.liaobots.work/v1`

### Model not available

1. Run `python test_custom_llm_endpoint.py` to see available models
2. Update `CUSTOM_LLM_MODEL_NAME` to a model from the list
3. Some models may require specific account tiers

### Gemini File API not working

Make sure you have:

- `GOOGLE_API_KEY` set to your official Google AI Studio key
- `GEMINI_MODEL_NAME=gemini-1.5-flash` (or another Gemini model)

## Security Best Practices

1. **Never commit `.env`** - It's in `.gitignore` by default
2. **Regenerate exposed keys** - If you accidentally share a key, regenerate it immediately
3. **Use environment-specific keys** - Different keys for dev/staging/prod
4. **Rotate keys regularly** - Good practice for long-running projects

## Next Steps

After configuration:

1. Test with: `python test_custom_llm_endpoint.py`
2. Run a research task: `python demo_research_agent.py --task prior_art --idea "your research idea"`
3. Monitor your usage on the provider's dashboard

## Cost Comparison

Approximate costs (as of 2025):

| Model             | Custom Endpoint  | Official API    |
| ----------------- | ---------------- | --------------- |
| GPT-4o-mini       | ~$0.10/1M tokens | $0.15/1M tokens |
| GPT-4o            | ~$2.50/1M tokens | $5.00/1M tokens |
| Claude 3.5 Sonnet | ~$2.00/1M tokens | $3.00/1M tokens |

_Actual prices vary by provider and usage tier_
