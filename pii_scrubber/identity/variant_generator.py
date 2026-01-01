"""
Name variant generation.

This module generates name variants and initials patterns for masking.
"""

from __future__ import annotations

import re
from typing import List, Set

from ..config import AnonymizeConfig
from ..utils import safe_lower, strip_name_token, normalize_text_for_matching
from .extractor import NameCandidateExtractor

class NameVariantGenerator:
    """
    Generates name variants and initials patterns for masking.
    """
    _STOPWORDS = NameCandidateExtractor._STOPWORDS

    def __init__(self, config: AnonymizeConfig):
        self.config = config

    def derive_variants(self, chosen_name: str, full_text: str) -> Set[str]:
        """Generate all variants of a name for masking."""
        chosen_name = normalize_text_for_matching(chosen_name)
        tokens_raw = [t for t in re.split(r"\s+", chosen_name) if t]

        clean: List[str] = []
        for t in tokens_raw:
            t2 = strip_name_token(t)
            if len(t2) < self.config.min_name_token_len:
                continue
            if safe_lower(t2) in self._STOPWORDS:
                continue
            clean.append(t2)

        if not clean:
            return set()

        variants: Set[str] = set()
        full = " ".join(clean)
        variants.add(full)
        variants.add(clean[0])
        if len(clean) >= 2:
            variants.add(clean[-1])
            # Add middle names if present
            if len(clean) > 2:
                for middle in clean[1:-1]:
                    variants.add(middle)

        # Handle hyphenated names
        for token in clean:
            if "-" in token:
                parts = token.split("-")
                for part in parts:
                    if len(part) >= self.config.min_name_token_len:
                        variants.add(part)

        # First name prefixes
        first = clean[0]
        for k in range(4, len(first)):
            pref = first[:k]
            if pref.endswith("-") or pref.endswith("'"):
                continue
            if re.search(rf"\b{re.escape(pref)}\b", full_text, flags=re.IGNORECASE):
                variants.add(pref)

        # Reversed name order (last, first)
        if len(clean) >= 2:
            reversed_name = f"{clean[-1]} {clean[0]}"
            variants.add(reversed_name)

        return {v for v in variants if len(v) >= self.config.min_name_token_len}

    def build_initials_patterns(self, variants: Set[str], full_text: str) -> List[str]:
        """Build regex patterns for matching initials."""
        full = max(variants, key=len, default="")
        toks = [t for t in full.split() if t]
        if len(toks) < 2:
            return []
        first, last = toks[0], toks[-1]
        if len(last) < self.config.min_lastname_len_for_initials:
            return []

        fi = re.escape(first[0].upper())
        li = re.escape(last[0].upper())
        last_esc = re.escape(last)

        patterns: List[str] = []
        patterns.append(rf"\b{fi}\.?\s+{last_esc}\b")                 # M. O'Connell
        patterns.append(rf"\b{fi}\.\s*{li}\.(?=\b|[\s,;:])")         # M.O. (stable with punctuation)

        if re.search(rf"\b{fi}\.\s*{li}\b", full_text):
            patterns.append(rf"\b{fi}\.\s*{li}\b")                   # M.O

        if re.search(rf"\b{fi}\s+{li}\b", full_text):
            patterns.append(rf"\b{fi}\s+{li}\b")                     # M O

        return patterns


# -----------------------------
# Primary Identity Resolver
# -----------------------------