"""Deterministic IETF RFC 8785-compatible canonical JSON and SHA-256 helpers.

The optional ``rfc8785`` package is used when installed.  The fallback keeps
the important invariants for this engine: booleans remain booleans, negative
zero becomes zero, integer-valued floats serialize without a decimal point,
keys are sorted by UTF-16 code units, and separators are compact.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

try:  # pragma: no cover - optional dependency
    import rfc8785 as _rfc8785
except Exception:  # pragma: no cover
    _rfc8785 = None


def quantize_for_jcs(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): quantize_for_jcs(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [quantize_for_jcs(v) for v in obj]
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise ValueError("JCS cannot serialize NaN or Infinity")
        value = float(obj)
        if value == 0.0:
            return 0
        if value.is_integer():
            return int(value)
        # Keep the shortest round-trip Python/JSON representation rather than
        # quantizing the audit payload.  This matches the RFC 8785 requirement
        # that the canonical form must not change the numeric value.
        return value
    if isinstance(obj, int):
        return int(obj)
    return obj


def _utf16_key(key: str) -> bytes:
    return key.encode("utf-16-be", errors="surrogatepass")


def canonical_dumps(value: Any) -> bytes:
    clean = quantize_for_jcs(value)
    if _rfc8785 is not None:
        return _rfc8785.dumps(clean)
    return canonical_sort_dumps(clean)


def canonical_sort_dumps(value: Any) -> bytes:
    """Fallback canonicalizer with explicit UTF-16 key ordering."""
    clean = quantize_for_jcs(value)

    def encode(node: Any) -> bytes:
        if isinstance(node, dict):
            items = sorted(node.items(), key=lambda item: _utf16_key(item[0]))
            encoded = [
                json.dumps(key, ensure_ascii=False).encode("utf-8")
                + b":"
                + encode(val)
                for key, val in items
            ]
            return b"{" + b",".join(encoded) + b"}"
        if isinstance(node, list):
            return b"[" + b",".join(encode(item) for item in node) + b"]"
        if isinstance(node, bool):
            return b"true" if node else b"false"
        if node is None:
            return b"null"
        if isinstance(node, str):
            return json.dumps(node, ensure_ascii=False).encode("utf-8")
        if isinstance(node, int):
            return str(node).encode("ascii")
        if isinstance(node, float):
            return json.dumps(node, ensure_ascii=False, allow_nan=False).encode(
                "ascii"
            )
        raise TypeError(f"unsupported JCS type: {type(node)!r}")

    return encode(clean)


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_dumps(value)).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


__all__ = [
    "canonical_dumps",
    "canonical_sort_dumps",
    "quantize_for_jcs",
    "sha256_text",
    "sha256_value",
]
