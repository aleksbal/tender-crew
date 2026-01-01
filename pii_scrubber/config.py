"""
Configuration for CV text anonymization.

This module contains the AnonymizeConfig dataclass that defines all
configuration options for the anonymization process.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple, Optional


@dataclass(frozen=True)
class AnonymizeConfig:
    """
    Configuration for CV text anonymization.
    
    Attributes:
        supported_languages: Tuple of language codes to support
        spacy_models: Tuple of (lang_code, model_name) tuples for spaCy models
        target_entities: Tuple of entity types to detect and anonymize
        run_both_lang_passes: Whether to run analysis in both languages
        propagate_primary_name: Whether to propagate primary name variants
        enable_initials: Whether to detect and mask initials
        min_name_token_len: Minimum length for name tokens
        min_lastname_len_for_initials: Minimum lastname length for initials detection
        url_policy: URL anonymization policy ("redact_all" | "keep_domain" | "allowlist_domains_keep_domain")
        url_domain_allowlist: Tuple of allowed domains when using allowlist policy
        debug: Whether to enable debug output
        pii_obfuscation_limit: Limit PII obfuscation to first N characters (0 = no limit)
        operators: Optional custom Presidio operators dict
    """
    supported_languages: Tuple[str, ...] = ("de", "en")
    spacy_models: Tuple[Tuple[str, str], ...] = (
        ("de", "de_core_news_md"),
        ("en", "en_core_web_lg"),
    )

    target_entities: Tuple[str, ...] = (
        "PERSON",
        "ADDRESS",
        "EMAIL_ADDRESS",
        "PHONE_NUMBER",
        "LINKEDIN_PROFILE",
        "URL",
    )

    run_both_lang_passes: bool = True

    # Identity resolution / propagation
    propagate_primary_name: bool = True
    enable_initials: bool = True
    min_name_token_len: int = 3
    min_lastname_len_for_initials: int = 3

    # URL policy:
    # "redact_all" | "keep_domain" | "allowlist_domains_keep_domain"
    url_policy: str = "keep_domain"
    url_domain_allowlist: Tuple[str, ...] = ("linkedin.com", "www.linkedin.com")

    debug: bool = False

    # Limit PII obfuscation to first N characters (0 = no limit, process entire text)
    pii_obfuscation_limit: int = 0

    # Override Presidio anonymizer operators if you want custom tokens
    operators: Optional[dict] = None

