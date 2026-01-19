# CV Scrubber Refactoring Plan

## Overview
Refactoring `cv_text_scrubber.py` (1735 lines) into a well-structured Python package following industry best practices.

## Proposed Structure

```
cv_scrubber/
├── __init__.py              # Public API exports
├── config.py                # AnonymizeConfig
├── utils.py                 # Helper functions (normalize, etc.)
├── patterns.py              # PatternRegistry
├── filters.py               # AddressFilter, CityNameFilter, TechnologyFilter
├── tracker.py               # ObfuscationTracker
├── anonymizer.py            # CvAnonymizer (main class)
└── identity/                # Name resolution subpackage
    ├── __init__.py
    ├── extractor.py          # NameCandidateExtractor
    ├── scorer.py             # NameScorer
    ├── variant_generator.py  # NameVariantGenerator
    └── resolver.py           # PrimaryIdentityResolver

tests/
└── test_cv_scrubber/
    ├── __init__.py
    ├── test_config.py
    ├── test_utils.py
    ├── test_patterns.py
    ├── test_filters.py
    ├── test_tracker.py
    ├── test_anonymizer.py
    └── identity/
        ├── __init__.py
        ├── test_extractor.py
        ├── test_scorer.py
        ├── test_variant_generator.py
        └── test_resolver.py
```

## Module Responsibilities

### Core Modules
- **config.py**: Configuration dataclass
- **utils.py**: Text normalization and helper functions
- **patterns.py**: Centralized regex patterns
- **filters.py**: False positive filters
- **tracker.py**: Obfuscation tracking
- **anonymizer.py**: Main anonymizer class

### Identity Subpackage
- **extractor.py**: Extract name candidates from various sources
- **scorer.py**: Score name candidates
- **variant_generator.py**: Generate name variants and initials patterns
- **resolver.py**: Orchestrate identity resolution

## Migration Strategy

1. Create package structure
2. Extract modules one by one
3. Update imports
4. Create unit tests
5. Update dependent code (cv_llm_converter.py)
6. Keep old file as backup initially

## Benefits

- **Maintainability**: Smaller, focused modules
- **Testability**: Isolated components with unit tests
- **Reusability**: Components can be imported independently
- **Clarity**: Clear separation of concerns
- **Industry Standard**: Follows Python packaging best practices

