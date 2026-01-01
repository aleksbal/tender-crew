"""
Unit tests for cv_scrubber.config module.
"""

import pytest
from cv_scrubber.config import AnonymizeConfig


def test_anonymize_config_defaults():
    """Test AnonymizeConfig with default values."""
    config = AnonymizeConfig()
    
    assert config.supported_languages == ("de", "en")
    assert config.run_both_lang_passes is True
    assert config.propagate_primary_name is True
    assert config.enable_initials is True
    assert config.url_policy == "keep_domain"
    assert config.debug is False
    assert config.pii_obfuscation_limit == 0


def test_anonymize_config_custom():
    """Test AnonymizeConfig with custom values."""
    config = AnonymizeConfig(
        debug=True,
        url_policy="redact_all",
        pii_obfuscation_limit=1000,
    )
    
    assert config.debug is True
    assert config.url_policy == "redact_all"
    assert config.pii_obfuscation_limit == 1000


def test_anonymize_config_immutable():
    """Test that AnonymizeConfig is immutable (frozen dataclass)."""
    config = AnonymizeConfig()
    
    with pytest.raises(Exception):  # dataclass.FrozenInstanceError
        config.debug = True

