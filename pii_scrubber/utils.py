"""
Utility functions for text normalization and helper operations.

This module provides helper functions used throughout the anonymization
process for text normalization, string manipulation, and span operations.
"""

from __future__ import annotations

import re
from typing import Tuple


def norm_space(s: str) -> str:
    """Normalize whitespace in a string."""
    return re.sub(r"\s+", " ", s).strip()


def safe_lower(s: str) -> str:
    """Casefold a string safely."""
    return s.casefold()


def span_overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    """Check if two spans overlap."""
    return a_start < b_end and b_start < a_end


def normalize_text_for_matching(text: str) -> str:
    """
    Normalize punctuation/whitespace so regex propagation is stable.
    
    Normalizes:
    - Apostrophes (various Unicode variants)
    - Hyphens (various Unicode variants)
    - Non-breaking spaces
    """
    t = text
    t = t.replace("'", "'").replace("'", "'")
    t = t.replace("‐", "-").replace("–", "-").replace("—", "-")
    t = re.sub(r"[\u00A0\u2007\u202F]", " ", t)  # NBSP variants
    return t


def strip_name_token(token: str) -> str:
    """
    Keep letters/digits/underscore + German letters + hyphen + apostrophe.
    
    Removes all other characters from a name token.
    """
    return re.sub(r"[^\wÄÖÜäöüß\-']", "", token)

