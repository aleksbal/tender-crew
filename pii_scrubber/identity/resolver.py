"""
Primary identity resolution.

This module orchestrates identity resolution using extractor, scorer,
and variant generator.
"""

from __future__ import annotations

from typing import Dict, List, Sequence, Set

from presidio_analyzer import RecognizerResult

from ..config import AnonymizeConfig
from ..utils import normalize_text_for_matching
from .extractor import NameCandidateExtractor
from .scorer import NameScorer
from .variant_generator import NameVariantGenerator

class PrimaryIdentityResolver:
    def __init__(self, config: AnonymizeConfig):
        self.config = config
        self.extractor = NameCandidateExtractor()
        self.scorer = NameScorer(config)
        self.variant_generator = NameVariantGenerator(config)

    def resolve(
        self,
        text: str,
        presidio_results: Sequence[RecognizerResult],
        extracted_urls: Sequence[str],
        extracted_emails: Sequence[str],
    ) -> Dict:
        """Resolve primary identity using extractor, scorer, and variant generator."""
        text_norm = normalize_text_for_matching(text)

        # Extract candidates from all sources
        candidates = self.extractor.extract_all(
            text_norm, presidio_results, extracted_urls, extracted_emails
        )

        # Score candidates
        scored = self.scorer.score_all(candidates, text_norm)
        chosen = max(scored, key=lambda c: c["score_total"], default=None)

        # Generate variants and initials patterns
        variants: Set[str] = set()
        initials_patterns: List[str] = []
        chosen_name = ""

        if chosen and chosen.get("name"):
            chosen_name = chosen["name"]
            variants = self.variant_generator.derive_variants(chosen_name, text_norm)
            if self.config.enable_initials:
                initials_patterns = self.variant_generator.build_initials_patterns(variants, text_norm)

        debug_info = {
            "chosen_name": chosen_name,
            "chosen_source": chosen.get("source") if chosen else None,
            "chosen_score_total": chosen.get("score_total") if chosen else None,
            "chosen_score_breakdown": chosen.get("score_breakdown") if chosen else None,
            "candidates_top": sorted(scored, key=lambda c: c["score_total"], reverse=True)[:10],
            "masked_variants": sorted(variants, key=len, reverse=True),
            "masked_initials_patterns": initials_patterns,
        }

        return {
            "chosen_name": chosen_name,
            "variants": variants,
            "initials_patterns": initials_patterns,
            "debug": debug_info,
        }



# -----------------------------
# Obfuscation Tracker
# -----------------------------