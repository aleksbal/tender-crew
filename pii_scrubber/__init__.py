"""
PII Scrubber - Text Anonymization Package

A comprehensive package for anonymizing PII (Personally Identifiable Information)
in text using a hybrid approach combining Presidio Analyzer with
custom regex patterns and identity resolution.

Main Components:
    - TextAnonymizer: Main anonymizer class
    - AnonymizeConfig: Configuration dataclass
    - ObfuscationTracker: Tracks obfuscations with numbered tokens

Example:
    >>> from pii_scrubber import TextAnonymizer, AnonymizeConfig
    >>> 
    >>> config = AnonymizeConfig(debug=True)
    >>> anonymizer = TextAnonymizer(config)
    >>> result = anonymizer.anonymize("Max Mustermann, email@example.com")
    >>> print(result["obfuscated_text"])
    >>> print(result["obfuscations"])
"""

from __future__ import annotations

from .anonymizer import TextAnonymizer
from .config import AnonymizeConfig
from .tracker import ObfuscationTracker

# Re-export for convenience
from . import patterns
from . import filters
from . import utils
from .identity import (
    NameCandidateExtractor,
    NameScorer,
    NameVariantGenerator,
    PrimaryIdentityResolver,
)

__version__ = "1.0.0"

__all__ = [
    # Main classes
    "TextAnonymizer",
    "AnonymizeConfig",
    "ObfuscationTracker",
    # Identity resolution
    "NameCandidateExtractor",
    "NameScorer",
    "NameVariantGenerator",
    "PrimaryIdentityResolver",
    # Submodules
    "patterns",
    "filters",
    "utils",
]

