"""
Pattern registry for regex patterns used in PII detection.

This module centralizes all regex patterns used throughout the anonymization
process, making them easier to maintain, test, and tune.
"""

import re


class PatternRegistry:
    """
    Centralized registry for all regex patterns used in PII detection.
    Makes patterns easier to maintain, test, and tune.
    """
    
    # Email patterns
    # Standard email: local@domain.tld
    # Local part: alphanumeric, dots, underscores, plus, hyphens (but not consecutive dots or leading/trailing dots)
    # Domain: alphanumeric, hyphens (but not leading/trailing hyphens)
    # TLD: alphanumeric, hyphens, dots (for multi-part TLDs like co.uk)
    EMAIL_STANDARD = r"\b[a-zA-Z0-9](?:[a-zA-Z0-9_.+-]*[a-zA-Z0-9])?@[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?\.[a-zA-Z0-9](?:[a-zA-Z0-9-.]*[a-zA-Z0-9])?\b"
    # Obfuscated emails: supports both English (at, dot) and German (at, punkt) obfuscation
    # Pattern: local [brackets?] at/@ [brackets?] domain [brackets?] dot/punkt/. [brackets?] tld
    # IMPORTANT: The "at" or "@" is REQUIRED and must be a separate word/token
    # This prevents false positives like "Cross-Platform-Basis. Entwicklung"
    # The pattern requires word boundaries around "at" to ensure it's not part of another word
    # Allows optional brackets around "at" and "dot" with flexible spacing
    EMAIL_OBFUSCATED = r"\b[a-zA-Z0-9](?:[a-zA-Z0-9_.+-]*[a-zA-Z0-9])?\s*(?:\(|\[)?\s*\b(?:at|@)\b\s*(?:\)|\])?\s+[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?\s*(?:\(|\[)?\s*\b(?:dot|punkt|\.)\b\s*(?:\)|\])?\s+[a-zA-Z0-9](?:[a-zA-Z0-9-.]*[a-zA-Z0-9])?\b"
    
    # Phone pattern: stricter to avoid matching dates
    # Note: We use a simpler pattern and validate in post-processing to exclude dates
    PHONE_STRICT = (
        r"\b(?:\+?\d{1,4}[\s().-]?)?(?:\(?\d{2,5}\)?[\s().-]?)?\d{2,4}[\s().-]?\d{2,4}[\s().-]?\d{2,6}\b"
    )
    
    # Date patterns to exclude from phone detection
    DATE_MONTH_YEAR = re.compile(r"\b\d{1,2}[/.]\s*(?:19|20)\d{2}\b")  # 2/2025, 12.1998
    DATE_YEAR_MONTH = re.compile(r"\b(?:19|20)\d{2}[/.]\s*\d{1,2}\b")  # 2025/2, 1998.12
    DATE_YEAR_RANGE = re.compile(r"\b(?:19|20)\d{2}\s*[-–]\s*(?:19|20)\d{2}\b")  # 2025-2026
    DATE_MONTHYEAR_RANGE = re.compile(r"\b\d{1,2}[/.](?:19|20)\d{2}\s*[-–]\s*\d{1,2}[/.](?:19|20)\d{2}\b")  # 2/2025 - 12/2025
    
    # German address patterns
    STREET_TYPES = r"(?:straße|strasse|str\.|weg|allee|gasse|platz|ring|damm|ufer)"
    ADDRESS_FULL_DE = rf"\b[A-ZÄÖÜ][\wÄÖÜäöüß\.\- ]{{2,}}?{STREET_TYPES}\s+\d{{1,4}}[a-zA-Z]?\s*,?\s*\d{{5}}\s+[A-ZÄÖÜ][\wÄÖÜäöüß\.\- ]{{1,}}\b"
    ADDRESS_ZIP_CITY_DE = r"\b\d{5}\s+[A-ZÄÖÜ][\wÄÖÜäöüß\- ]{2,}\b"
    
    # LinkedIn patterns
    LINKEDIN_PROFILE = r"\b(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/(?:in|pub)/[A-Za-z0-9\-_%/]+/?\b"
    LINKEDIN_HANDLE_EXTRACT = r"linkedin\.com/(?:in|pub)/([^/?#\s]+)"
    
    # URL patterns
    URL_SCHEME = r"\bhttps?://[^\s<>()\[\]\"']{6,}\b"
    URL_BARE = r"\b(?:www\.)?[a-zA-Z0-9-]+\.[a-zA-Z]{2,}(?:/[^\s<>()\[\]\"']*)?\b"
    URL_FIND = rf"{URL_SCHEME}|{URL_BARE}"
    
    # Name patterns
    TITLE_CASE_WORD = r"^[A-ZÄÖÜ][a-zäöüß]+(?:[-'][A-ZÄÖÜa-zäöüß]+)?$"
    TITLE_CASE_COMPOUND = r"^[A-ZÄÖÜ][a-zäöüß]+(?:[-'][A-ZÄÖÜa-zäöüß]+)+$"
    
    # Contact zone detection
    CONTACT_ZONE = r"\b(contact|kontakt|address|adresse|email|e-mail|phone|telefon)\b"

