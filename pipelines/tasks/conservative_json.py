"""Local format recovery without inventing fields or completing truncation."""
from __future__ import annotations

import ast
import json
import io
import math
import re
import tokenize
from typing import Any


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"Duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_constant(value):
    raise ValueError(f"Nonfinite JSON constant: {value}")


def _check_json_types(value):
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _check_json_types(item)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _check_json_types(item)
        return
    raise ValueError("Value is not a finite JSON data structure")


def parse_json_locally(text: str) -> tuple[Any, str]:
    """Parse one complete value, returning (data, recovery_mode).

    Only a whole Markdown fence, raw control characters inside JSON strings,
    and existing Python-literal syntax are recoverable. No field names/values,
    closing delimiters, comments, or surrounding prose are guessed or removed.
    Callers must still validate their domain schema before accepting the value.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Expected a nonempty JSON response")
    candidate = text.strip()
    fence = re.fullmatch(r"```(?:json|python)?\s*\n(.*?)\n?```", candidate, re.S | re.I)
    suffix = "_fence" if fence else ""
    if fence:
        candidate = fence.group(1).strip()
    for strict, mode in ((True, "json"), (False, "json_control_characters")):
        try:
            data = json.loads(candidate, strict=strict, object_pairs_hook=_unique_object,
                              parse_constant=_reject_constant)
            _check_json_types(data)
            return data, mode + suffix
        except (ValueError, TypeError):
            pass
    try:
        if any(token.type == tokenize.COMMENT for token in tokenize.generate_tokens(io.StringIO(candidate).readline)):
            raise ValueError("Comments are not valid JSON data")
        tree = ast.parse(candidate, mode="eval")
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                keys = [ast.literal_eval(key) for key in node.keys]
                if any(type(key) is not str for key in keys) or len(set(keys)) != len(keys):
                    raise ValueError("Python object contains invalid or duplicate keys")
        data = ast.literal_eval(tree)
        _check_json_types(data)
        return data, "python_literal" + suffix
    except (ValueError, TypeError, SyntaxError, tokenize.TokenError) as exc:
        raise ValueError("Response is not one complete unambiguous JSON value") from exc
