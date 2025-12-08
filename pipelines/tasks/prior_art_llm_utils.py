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


def parse_json_with_repair(raw: str, fallback_empty: bool = True) -> Optional[Dict[str, Any]]:
    """
    Parse JSON from LLM output with automatic repair.
    
    Uses 3-layer parsing strategy:
    1. Direct json.loads()
    2. json.loads() after repair
    3. ast.literal_eval() fallback
    
    Args:
        raw: Raw LLM output string
        fallback_empty: If True, return {} on failure; else return None
        
    Returns:
        Parsed dict, or fallback value on failure
    """
    data = None
    
    # Layer 1: Direct json.loads
    try:
        data = json.loads(raw)
    except Exception:
        pass
    
    # Layer 2: Try with JSON repair
    if data is None:
        try:
            repaired = repair_json_output(raw)
            data = json.loads(repaired)
            logger.debug("[LLMUtils] JSON repair succeeded")
        except Exception:
            pass
    
    # Layer 3: Fallback to ast.literal_eval
    if data is None:
        try:
            data = ast.literal_eval(raw)
        except Exception:
            pass
    
    # Return result
    if data is not None and isinstance(data, dict):
        return data
    
    if fallback_empty:
        logger.warning("[LLMUtils] Failed to parse JSON, returning empty dict")
        return {}
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
