"""
Unit tests for cv_scrubber.utils module.
"""

import pytest
from cv_scrubber.utils import (
    norm_space,
    safe_lower,
    span_overlaps,
    normalize_text_for_matching,
    strip_name_token,
)


def test_norm_space():
    """Test whitespace normalization."""
    assert norm_space("  hello   world  ") == "hello world"
    assert norm_space("a\n\tb") == "a b"
    assert norm_space("") == ""


def test_safe_lower():
    """Test case-insensitive comparison."""
    assert safe_lower("Hello") == "hello"
    assert safe_lower("WELCOME") == "welcome"
    assert safe_lower("") == ""


def test_span_overlaps():
    """Test span overlap detection."""
    # Overlapping spans
    assert span_overlaps(0, 10, 5, 15) is True
    assert span_overlaps(5, 15, 0, 10) is True
    
    # Non-overlapping spans
    assert span_overlaps(0, 5, 10, 15) is False
    assert span_overlaps(10, 15, 0, 5) is False
    
    # Adjacent spans (not overlapping)
    assert span_overlaps(0, 5, 5, 10) is False


def test_normalize_text_for_matching():
    """Test text normalization for matching."""
    # Apostrophes
    assert "'" in normalize_text_for_matching("it's")
    assert "'" in normalize_text_for_matching("it's")
    
    # Hyphens
    assert "-" in normalize_text_for_matching("co‐operation")
    assert "-" in normalize_text_for_matching("co–operation")
    
    # Non-breaking spaces
    text = "hello\u00A0world"
    assert "\u00A0" not in normalize_text_for_matching(text)


def test_strip_name_token():
    """Test name token stripping."""
    assert strip_name_token("Max-Mustermann") == "Max-Mustermann"
    assert strip_name_token("O'Brien") == "O'Brien"
    assert strip_name_token("Max, Mustermann") == "MaxMustermann"
    assert strip_name_token("123abc") == "123abc"

