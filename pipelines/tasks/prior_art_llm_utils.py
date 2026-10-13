"""
Prior Art Check - LLM Utilities

This module contains helper functions for LLM interactions:
- JSON repair (fix common LLM output issues)
- Text extraction from LLM responses

These utilities are used by both scoring and retrieval modules.
"""

from __future__ import annotations

import re
import json
import ast
from typing import Optional, Dict, Any, List

from loguru import logger


def extract_text_from_llm_response(resp) -> str:
    """
    Normalize an LLM response to a plain text string.

    This function handles:
    - smolagents / LiteLLM ChatMessage objects (using `.content`)
    - Arbitrary objects by falling back to `str(resp)`
    - Markdown fenced code blocks like ```json ... ```
    - Optional language hints (e.g. "json" on the first line inside the fence)

    The goal is to obtain a clean JSON string that can be passed to
    json.loads() or ast.literal_eval().
    """
    # 1. Extract raw text content
    if hasattr(resp, "content") and isinstance(getattr(resp, "content"), str):
        text = getattr(resp, "content")
    else:
        text = str(resp)

    text = text.strip()

    # 2. Strip HTML-like <code> ... </code> wrapper if present (defensive)
    if "<code>" in text and "</code>" in text:
        start = text.find("<code>") + len("<code>")
        end = text.rfind("</code>")
        if end > start:
            text = text[start:end].strip()

    # 3. Strip Markdown fenced code blocks ```...```
    if "```" in text:
        first = text.find("```")
        last = text.rfind("```")
        if last > first:
            inner = text[first + 3 : last].strip()
            # Handle optional language marker on the first line (e.g., "json")
            first_newline = inner.find("\n")
            if first_newline != -1:
                first_line = inner[:first_newline].strip().lower()
                # If the first line looks like a language hint, drop it
                if first_line in {"json", "javascript", "js", "python", "py"}:
                    inner = inner[first_newline + 1 :].lstrip()
            text = inner.strip()

    return text


def repair_json_output(raw: str) -> str:
    """
    Attempt to repair common LLM JSON output issues.
    
    Handles:
    - Text before/after the JSON object (extracts { ... })
    - Trailing commas before } or ]
    - Single-line // comments
    - Control characters that break JSON parsing
    
    Args:
        raw: Raw LLM output string
        
    Returns:
        Cleaned JSON string, or original if no { } found
        
    Example:
        >>> raw = 'Here is the answer: {"key": "value",} done.'
        >>> repair_json_output(raw)
        '{"key": "value"}'
    """
    # Strip everything before first { and after last }
    start = raw.find('{')
    end = raw.rfind('}')
    if start == -1 or end == -1:
        return raw
    trimmed = raw[start:end+1]
    
    # Remove single-line // comments (but not inside strings - rough heuristic)
    trimmed = re.sub(r'//[^\n"]*(?=\n|$)', '', trimmed)
    
    # Remove trailing commas before } or ]
    trimmed = re.sub(r',\s*([}\]])', r'\1', trimmed)
    
    # Remove control characters that break JSON
    trimmed = re.sub(r'[\x00-\x1f\x7f]', ' ', trimmed)
    
    return trimmed


# Regex to match ```json ... ``` blocks
JSON_BLOCK_RE = re.compile(
    r'```json\s*(.*?)\s*```',
    flags=re.DOTALL | re.IGNORECASE,
)


def extract_json_candidate(raw: str) -> str:
    """
    Extract the most likely JSON fragment from LLM output.
    
    Priority:
    1. Content inside ```json ... ``` blocks
    2. First { ... } or [ ... ] block found
    
    Args:
        raw: Raw LLM output text
        
    Returns:
        Extracted JSON candidate string
    """
    # 1) Try markdown ```json``` wrapped content
    m = JSON_BLOCK_RE.search(raw)
    if m:
        return m.group(1).strip()
    
    # 2) Find first { or [
    start = None
    for ch in ["{", "["]:
        idx = raw.find(ch)
        if idx != -1:
            start = idx if start is None else min(start, idx)
    
    if start is None:
        return raw.strip()  # No JSON structure found
    
    # Find matching end
    end = max(raw.rfind("}"), raw.rfind("]"))
    if end == -1 or end < start:
        end = len(raw) - 1
    
    return raw[start : end + 1]


def parse_json_with_repair(raw: str, fallback_empty: bool = True) -> Optional[Dict[str, Any]]:
    """
    Parse JSON dict from LLM output with automatic repair.
    
    Uses 4-layer parsing strategy:
    1. Extract JSON candidate from text
    2. Direct json.loads()
    3. json.loads() after repair
    4. ast.literal_eval() fallback
    
    Args:
        raw: Raw LLM output string
        fallback_empty: If True, return {} on failure; else return None
        
    Returns:
        Parsed dict, or fallback value on failure
    """
    # First extract the JSON candidate
    candidate = extract_json_candidate(raw)
    data = None
    
    # Layer 1: Direct json.loads
    try:
        data = json.loads(candidate)
    except Exception:
        pass
    
    # Layer 2: Try with JSON repair
    if data is None:
        try:
            repaired = repair_json_output(candidate)
            data = json.loads(repaired)
            logger.debug("[LLMUtils] JSON repair succeeded")
        except Exception:
            pass
    
    # Layer 3: Fallback to ast.literal_eval
    if data is None:
        try:
            data = ast.literal_eval(candidate)
        except Exception:
            pass
    
    # Return result - expect dict
    if data is not None and isinstance(data, dict):
        return data
    
    if fallback_empty:
        logger.warning("[LLMUtils] Failed to parse JSON as dict, returning empty dict")
        return {}
    return None


def parse_json_list_with_repair(raw: str, fallback_empty: bool = True) -> Optional[List[Any]]:
    """
    Parse JSON list from LLM output with automatic repair.
    
    Similar to parse_json_with_repair but expects list output.
    Also handles common case where LLM wraps list in a dict.
    
    Args:
        raw: Raw LLM output string
        fallback_empty: If True, return [] on failure; else return None
        
    Returns:
        Parsed list, or fallback value on failure
    """
    # First extract the JSON candidate
    candidate = extract_json_candidate(raw)
    data = None
    
    # Layer 1: Direct json.loads
    try:
        data = json.loads(candidate)
    except Exception:
        pass
    
    # Layer 2: Try with JSON repair
    if data is None:
        try:
            repaired = repair_json_output(candidate)
            data = json.loads(repaired)
            logger.debug("[LLMUtils] JSON repair (list) succeeded")
        except Exception:
            pass
    
    # Layer 3: Fallback to ast.literal_eval
    if data is None:
        try:
            data = ast.literal_eval(candidate)
        except Exception:
            pass
    
    # If already a list, return it
    if isinstance(data, list):
        return data
    
    # Auto-unwrap: if dict with exactly one list-value field, extract it
    if isinstance(data, dict):
        list_keys = [k for k, v in data.items() if isinstance(v, list)]
        if len(list_keys) == 1:
            logger.debug(f"[LLMUtils] Unwrapping list from dict key '{list_keys[0]}'")
            return data[list_keys[0]]
    
    if fallback_empty:
        logger.warning("[LLMUtils] Failed to parse JSON as list, returning []")
        return []
    return None


def extract_json_list_from_response(raw: str) -> List[str]:
    """
    Extract a JSON list from LLM response text.
    
    Handles cases where the model adds extra text around the list.
    
    Args:
        raw: Raw LLM response text
        
    Returns:
        List of strings, or empty list on failure
    """
    # Try direct parsing first
    try:
        result = json.loads(raw.strip())
        if isinstance(result, list):
            return [str(item).strip() for item in result if item]
    except json.JSONDecodeError:
        pass
    
    # Try to find array brackets
    start = raw.find('[')
    end = raw.rfind(']')
    
    if start != -1 and end > start:
        try:
            result = json.loads(raw[start:end+1])
            if isinstance(result, list):
                return [str(item).strip() for item in result if item]
        except json.JSONDecodeError:
            pass
    
    return []


def clean_llm_output(text: str) -> str:
    """
    Clean LLM output text by removing common wrapper patterns.
    
    Handles:
    - Markdown code fences (```json ... ```)
    - Leading/trailing whitespace
    - <code></code> tags
    
    Args:
        text: Raw LLM output text
        
    Returns:
        Cleaned text
    """
    text = text.strip()
    
    # Remove markdown code fences
    if text.startswith("```"):
        lines = text.split('\n')
        # Skip first and last line if they are code fences
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = '\n'.join(lines)
    
    # Remove <code> tags
    text = re.sub(r'</?code>', '', text)
    
    return text.strip()
