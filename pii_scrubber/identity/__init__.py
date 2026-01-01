"""
Identity resolution subpackage.

This subpackage contains components for identifying and resolving
the primary identity (name) from CV text.
"""

from .extractor import NameCandidateExtractor
from .scorer import NameScorer
from .variant_generator import NameVariantGenerator
from .resolver import PrimaryIdentityResolver

__all__ = [
    "NameCandidateExtractor",
    "NameScorer",
    "NameVariantGenerator",
    "PrimaryIdentityResolver",
]

