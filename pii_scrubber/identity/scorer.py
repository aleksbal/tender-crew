"""
Name candidate scoring.

This module scores name candidates using configurable weights.
"""

from __future__ import annotations

import re
from typing import Dict, List

from ..config import AnonymizeConfig
from ..utils import norm_space, safe_lower, strip_name_token, normalize_text_for_matching
from ..filters import AddressFilter
from .extractor import NameCandidateExtractor

class NameScorer:
    """
    Scores name candidates using configurable weights.
    Replaces magic numbers with named constants.
    """
    # Source weights
    SOURCE_WEIGHTS = {
        "presidio_person": 60.0,
        "header_line": 40.0,
        "linkedin_handle": 18.0,
        "email_localpart": 14.0,
        "email_localpart_weak": 6.0,
        "default": 5.0,
    }
    
    # Token count bonuses/penalties
    TOKEN_COUNT_OPTIMAL_MIN = 2
    TOKEN_COUNT_OPTIMAL_MAX = 4
    TOKEN_COUNT_OPTIMAL_BONUS = 25.0
    TOKEN_COUNT_SINGLE_BONUS = 5.0
    TOKEN_COUNT_TOO_MANY_PENALTY = -10.0
    
    # Stopword penalty
    STOPWORD_PENALTY_PER_TOKEN = 25.0
    
    # Header position bonuses/penalties
    HEADER_POSITION_BONUS_BASE = 25.0
    HEADER_POSITION_BONUS_DECAY = 3.0
    HEADER_POSITION_LATE_PENALTY = -15.0
    HEADER_POSITION_LATE_THRESHOLD = 6
    
    # Presidio score bonus
    PRESIDIO_SCORE_MULTIPLIER = 20.0
    PRESIDIO_SCORE_MAX_BONUS = 20.0
    
    # Frequency bonus
    FREQUENCY_MIN_OCCURRENCES = 2
    FREQUENCY_BONUS_PER_TOKEN = 5.0
    FREQUENCY_MAX_BONUS = 20.0
    
    # Digits penalty
    DIGITS_PENALTY = -30.0
    
    # Common word penalty (for words like Office, Mobile, etc.)
    COMMON_WORD_PENALTY = -40.0
    COMMON_WORDS = {
        # English
        "office", "mobile", "phone", "email", "address", "contact", "tel", "fax",
        # German
        "büro", "mobil", "telefon", "e-mail", "adresse", "kontakt", "fax",
    }
    
    # Proper name bonus (title case, 2-4 tokens, no digits, no common words)
    PROPER_NAME_BONUS = 15.0

    def __init__(self, config: AnonymizeConfig):
        self.config = config
        self._stopwords = NameCandidateExtractor._STOPWORDS

    def score_all(self, candidates: List[Dict], text: str) -> List[Dict]:
        """Score all candidates and return sorted by score."""
        scored: List[Dict] = []
        for c in candidates:
            name = normalize_text_for_matching(norm_space(c["name"]))
            if AddressFilter.should_exclude(name):
                continue

            tokens = [strip_name_token(t) for t in re.split(r"\s+", name) if t]
            tokens = [t for t in tokens if t]

            breakdown: Dict[str, float] = {}
            score = 0.0

            # Source weight
            src = c.get("source", "")
            src_weight = self.SOURCE_WEIGHTS.get(src, self.SOURCE_WEIGHTS["default"])
            score += src_weight
            breakdown["source_weight"] = src_weight

            # Token count bonus/penalty
            if self.TOKEN_COUNT_OPTIMAL_MIN <= len(tokens) <= self.TOKEN_COUNT_OPTIMAL_MAX:
                score += self.TOKEN_COUNT_OPTIMAL_BONUS
                breakdown["token_count_bonus"] = self.TOKEN_COUNT_OPTIMAL_BONUS
            elif len(tokens) == 1:
                score += self.TOKEN_COUNT_SINGLE_BONUS
                breakdown["token_count_bonus"] = self.TOKEN_COUNT_SINGLE_BONUS
            else:
                score += self.TOKEN_COUNT_TOO_MANY_PENALTY
                breakdown["token_count_bonus"] = self.TOKEN_COUNT_TOO_MANY_PENALTY

            # Stopword penalty
            stop_pen = 0.0
            for t in tokens:
                if safe_lower(t) in self._stopwords:
                    stop_pen += self.STOPWORD_PENALTY_PER_TOKEN
            if stop_pen:
                score -= stop_pen
                breakdown["stopword_penalty"] = -stop_pen

            # Header position bonus
            if src == "header_line":
                li = int(c.get("meta", {}).get("line_idx", 999))
                if li <= self.HEADER_POSITION_LATE_THRESHOLD:
                    pos_bonus = max(0, self.HEADER_POSITION_BONUS_BASE - (li * self.HEADER_POSITION_BONUS_DECAY))
                    score += pos_bonus
                    breakdown["position_bonus"] = pos_bonus
                else:
                    score += self.HEADER_POSITION_LATE_PENALTY
                    breakdown["late_header_penalty"] = self.HEADER_POSITION_LATE_PENALTY

            # Presidio score bonus
            if src == "presidio_person":
                ps = float(c.get("meta", {}).get("score", 0.0))
                ps_bonus = min(self.PRESIDIO_SCORE_MAX_BONUS, ps * self.PRESIDIO_SCORE_MULTIPLIER)
                score += ps_bonus
                breakdown["presidio_score_bonus"] = ps_bonus

            # Frequency bonus
            freq_bonus = 0.0
            for t in tokens:
                if len(t) < self.config.min_name_token_len:
                    continue
                cnt = len(re.findall(rf"\b{re.escape(t)}\b", text, flags=re.IGNORECASE))
                if cnt >= self.FREQUENCY_MIN_OCCURRENCES:
                    freq_bonus += self.FREQUENCY_BONUS_PER_TOKEN
            if freq_bonus:
                score += min(self.FREQUENCY_MAX_BONUS, freq_bonus)
                breakdown["frequency_bonus"] = min(self.FREQUENCY_MAX_BONUS, freq_bonus)

            # Digits penalty
            if re.search(r"\d", name):
                score += self.DIGITS_PENALTY
                breakdown["digits_penalty"] = self.DIGITS_PENALTY

            # Common word penalty (Office, Mobile, etc.)
            name_lower = safe_lower(name)
            has_common_word = any(word in self.COMMON_WORDS for word in name_lower.split())
            if has_common_word:
                score += self.COMMON_WORD_PENALTY
                breakdown["common_word_penalty"] = self.COMMON_WORD_PENALTY

            # Proper name bonus: title case, 2-4 tokens, no digits, no common words
            if (2 <= len(tokens) <= 4 and
                not re.search(r"\d", name) and
                not has_common_word and
                all(t[0].isupper() if t else False for t in tokens if t)):
                score += self.PROPER_NAME_BONUS
                breakdown["proper_name_bonus"] = self.PROPER_NAME_BONUS

            scored.append({
                **c,
                "name": name,
                "tokens": tokens,
                "score_total": score,
                "score_breakdown": breakdown,
            })

        # Deduplicate by name (keep highest scoring)
        best_by_name: Dict[str, Dict] = {}
        for c in scored:
            key = safe_lower(c["name"])
            if key not in best_by_name or c["score_total"] > best_by_name[key]["score_total"]:
                best_by_name[key] = c

        return list(best_by_name.values())


# -----------------------------
# Name Variant Generator
# -----------------------------