from dataclasses import dataclass
from typing import Literal, Optional, Tuple
import os
from loguru import logger

Provider = Literal["custom", "google"]

@dataclass
class LLMConfig:
    role: str                     # "heavy" or "light"
    provider: Provider
    model: str
    api_base: Optional[str]       # Only for custom
    api_key: str                  # Custom key or Google key

def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    """Helper to get stripped env var, returning None for empty strings."""
    v = os.getenv(name, default)
    if v is None:
        return None
    v_strip = v.strip()
    if v_strip.lower() in ("", "none", "null"):
        return None
    return v_strip

def load_llm_config(role: str) -> LLMConfig:
    assert role in ("heavy", "light", "expert")
    prefix = f"LLM_{role.upper()}"

    # 1. Determine Provider and Model
    provider = _env(f"{prefix}_PROVIDER", "custom")
    model = _env(f"{prefix}_MODEL", "gemini-2.5-flash-thinking")

    # 2. Resolve Configuration based on Provider
    if provider == "custom":
        # Try specific -> global custom
        api_base = _env(f"{prefix}_API_BASE") or _env("CUSTOM_LLM_API_BASE")
        api_key = _env(f"{prefix}_API_KEY") or _env("CUSTOM_LLM_API_KEY")

        # Fallback Logic: If custom is invalid, try Google
        if not api_base or not api_key:
            google_key = _env("GOOGLE_API_KEY")
            if google_key:
                logger.warning(f"[LLM-{role}] Custom config missing. Falling back to Google provider.")
                return LLMConfig(role=role, provider="google", model=model, api_base=None, api_key=google_key)
            else:
                raise RuntimeError(f"[LLM-{role}] Missing Custom config AND Google API Key.")
        
        return LLMConfig(role=role, provider="custom", model=model, api_base=api_base, api_key=api_key)

    elif provider == "google":
        # Public profiles may route separate native Gemini keys per role.
        google_key = _env(f"{prefix}_API_KEY") or _env("GOOGLE_API_KEY")
        if not google_key:
            raise RuntimeError(f"[LLM-{role}] Provider is google but GOOGLE_API_KEY is missing.")
        return LLMConfig(role=role, provider="google", model=model, api_base=None, api_key=google_key)
    
    else:
        raise ValueError(f"Unknown provider: {provider}")

def get_litellm_config(role: str) -> Tuple[str, Optional[str], Optional[str]]:
    """
    Returns (model_id, api_base, api_key) suitable for LiteLLMModel.
    """
    cfg = load_llm_config(role)
    
    if cfg.provider == "custom":
        # Force OpenAI-compatible mode for LiteLLM if needed
        model_id = cfg.model
        if not model_id.startswith("openai/"):
            model_id = f"openai/{cfg.model}"
            
        logger.info(f"[LLM-{role}] Using CUSTOM endpoint: {cfg.api_base} (model={model_id})")
        return model_id, cfg.api_base, cfg.api_key
        
    elif cfg.provider == "google":
        # LiteLLM usually takes gemini/model-name
        model_id = cfg.model
        if not model_id.startswith("gemini/"):
            model_id = f"gemini/{cfg.model}"
            
        logger.info(f"[LLM-{role}] Using GOOGLE endpoint (model={model_id})")
        return model_id, None, cfg.api_key
        
    return "gpt-4o", None, None
