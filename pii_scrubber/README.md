# PII Scrubber Package

A comprehensive Python package for anonymizing PII (Personally Identifiable Information) in text.

## Features

- **Hybrid Approach**: Combines Presidio Analyzer (spaCy NER) with custom regex patterns
- **Multi-language Support**: German and English
- **Identity Resolution**: Intelligent primary name detection and variant propagation
- **Numbered Tokens**: Unique numbered tokens for each obfuscation (PERSON1, PERSON2, etc.)
- **Comprehensive Tracking**: Detailed obfuscation mappings
- **Configurable**: Extensive configuration options

## Installation

```bash
pip install presidio-analyzer presidio-anonymizer spacy
python -m spacy download de_core_news_md
python -m spacy download en_core_web_lg
```

## Quick Start

```python
from pii_scrubber import TextAnonymizer, AnonymizeConfig

# Create anonymizer with default config
anonymizer = TextAnonymizer()

# Anonymize text
result = anonymizer.anonymize(
    "Max Mustermann\nEmail: max@example.com\nPhone: +49 171 2345678",
    preferred_language="de"
)

# Access results
print(result["obfuscated_text"])
print(result["obfuscations"])  # List of {key, value} mappings
print(result["config"])  # Configuration used
```

## Package Structure

```
cv_scrubber/
├── __init__.py              # Public API
├── config.py                # Configuration
├── utils.py                 # Utility functions
├── patterns.py              # Regex patterns
├── filters.py               # False positive filters
├── tracker.py               # Obfuscation tracking
├── anonymizer.py            # Main anonymizer class
└── identity/                # Identity resolution
    ├── extractor.py          # Name candidate extraction
    ├── scorer.py             # Name candidate scoring
    ├── variant_generator.py  # Name variant generation
    └── resolver.py           # Identity resolution orchestration
```

## Configuration

```python
from pii_scrubber import AnonymizeConfig, TextAnonymizer

config = AnonymizeConfig(
    debug=True,
    url_policy="keep_domain",
    propagate_primary_name=True,
    enable_initials=True,
    pii_obfuscation_limit=0,  # 0 = no limit
)

anonymizer = TextAnonymizer(config)
```

## Return Structure

The `anonymize()` method returns a dictionary with:

- `original_text`: Original input text
- `obfuscated_text`: Text with PII obfuscated
- `config`: Configuration used (as dict)
- `obfuscations`: List of mappings with `key` (token) and `value` (original value)

## Testing

```bash
pytest tests/test_cv_scrubber/
```

## License

See main project LICENSE file.

