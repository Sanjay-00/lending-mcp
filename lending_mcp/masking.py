"""
Masking of identifiers, applied when a response or a sheet is built, never in the cache.

Tool results go to the LLM provider, so PAN, date of birth, phone, bank account number and
GSTIN are masked unless LENDING_MCP_UNMASKED=1. `scrub` is the safety net for text: an
engine's finding or narration can quote an identifier in full inside a sentence.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional


def mask_tail(value, keep: int = 4) -> Optional[str]:
    if value in (None, ""):
        return None
    s = str(value)
    return "*" * max(len(s) - keep, 0) + s[-keep:] if len(s) > keep else "*" * len(s)


def mask_dob(value) -> Optional[str]:
    if value in (None, ""):
        return None
    year = re.search(r"(19|20)\d{2}", str(value))
    return f"**-**-{year.group(0)}" if year else "masked"


def mask_gstin(value) -> Optional[str]:
    """The state code and the last three characters stay: 29ABCDE1234F1Z5 -> 29**********1Z5."""
    if value in (None, ""):
        return None
    s = str(value)
    return s[:2] + "*" * max(len(s) - 5, 0) + s[-3:] if len(s) > 5 else "*" * len(s)


def scrub(obj: Any, replacements: Dict[str, str]) -> Any:
    """A deep copy of obj with every exact occurrence of each replacements key replaced by its
    value, in dict keys, string values and nested strings. Longest keys first, so an
    identifier that contains another (a GSTIN contains its PAN) is replaced whole."""
    pairs = sorted(((k, v) for k, v in replacements.items() if k), key=lambda kv: -len(kv[0]))
    if not pairs:
        return obj

    def text(s: str) -> str:
        for old, new in pairs:
            if old in s:
                s = s.replace(old, new)
        return s

    def walk(o):
        if isinstance(o, str):
            return text(o)
        if isinstance(o, dict):
            return {(text(k) if isinstance(k, str) else k): walk(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [walk(v) for v in o]
        return o

    return walk(obj)
