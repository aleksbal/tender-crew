# Refactoring Completion Guide

This guide helps complete the refactoring of `cv_text_scrubber.py` into the new package structure.

## Current Status

✅ **Completed:**
- Package structure created
- `config.py` - Configuration dataclass
- `utils.py` - Utility functions
- `patterns.py` - Pattern registry
- `filters.py` - Filter classes
- `tracker.py` - Obfuscation tracker
- `__init__.py` - Public API
- `README.md` - Package documentation

⏳ **Remaining:**
- `identity/extractor.py` - NameCandidateExtractor
- `identity/scorer.py` - NameScorer
- `identity/variant_generator.py` - NameVariantGenerator
- `identity/resolver.py` - PrimaryIdentityResolver
- `anonymizer.py` - CvAnonymizer (main class)
- Unit tests
- Update `cv_llm_converter.py` imports

## Steps to Complete

### 1. Extract Identity Components

For each identity component, extract from `cv_text_scrubber.py`:

#### extractor.py (lines ~422-667)
- Extract `NameCandidateExtractor` class
- Update imports:
  - `from ..utils import norm_space, safe_lower, normalize_text_for_matching, strip_name_token`
  - `from ..patterns import PatternRegistry`
  - `from ..filters import AddressFilter, TechnologyFilter`
- Replace `_norm_space` → `norm_space`, `_safe_lower` → `safe_lower`, etc.

#### scorer.py (lines ~674-833)
- Extract `NameScorer` class
- Update imports:
  - `from ..config import AnonymizeConfig`
  - `from ..utils import norm_space, safe_lower, strip_name_token`
  - `from ..filters import AddressFilter`
- Replace internal function calls

#### variant_generator.py (lines ~840-925)
- Extract `NameVariantGenerator` class
- Update imports:
  - `from ..config import AnonymizeConfig`
  - `from ..utils import safe_lower, strip_name_token, normalize_text_for_matching`

#### resolver.py (lines ~932-983)
- Extract `PrimaryIdentityResolver` class
- Update imports:
  - `from ..config import AnonymizeConfig`
  - `from .extractor import NameCandidateExtractor`
  - `from .scorer import NameScorer`
  - `from .variant_generator import NameVariantGenerator`
  - `from ..utils import normalize_text_for_matching`

### 2. Extract Main Anonymizer

#### anonymizer.py (lines ~1024-end)
- Extract `CvAnonymizer` class
- Update all imports:
  ```python
  from .config import AnonymizeConfig
  from .utils import norm_space, normalize_text_for_matching
  from .patterns import PatternRegistry
  from .filters import AddressFilter, CityNameFilter, TechnologyFilter
  from .tracker import ObfuscationTracker
  from .identity import PrimaryIdentityResolver
  ```
- Replace all internal function calls with module imports
- Update `_config_to_dict()` to use `asdict` from dataclasses

### 3. Create Unit Tests

Create test files in `tests/test_cv_scrubber/`:

- `test_config.py` - Test AnonymizeConfig
- `test_utils.py` - Test utility functions
- `test_patterns.py` - Test PatternRegistry
- `test_filters.py` - Test filter classes
- `test_tracker.py` - Test ObfuscationTracker
- `test_anonymizer.py` - Test CvAnonymizer
- `identity/test_extractor.py` - Test NameCandidateExtractor
- `identity/test_scorer.py` - Test NameScorer
- `identity/test_variant_generator.py` - Test NameVariantGenerator
- `identity/test_resolver.py` - Test PrimaryIdentityResolver

### 4. Update Dependent Code

Update `cv_llm_converter.py`:
```python
# Old:
from cv_text_scrubber import CvAnonymizer, AnonymizeConfig

# New:
from cv_scrubber import CvAnonymizer, AnonymizeConfig
```

### 5. Testing

1. Run unit tests: `pytest tests/test_cv_scrubber/`
2. Test integration: Run `cv_llm_converter.py` with sample CVs
3. Verify backward compatibility (if keeping old file)

### 6. Cleanup

- Optionally rename `cv_text_scrubber.py` to `cv_text_scrubber.py.bak`
- Update any other files that import from `cv_text_scrubber`

## Import Mapping Reference

| Old (internal) | New (module) |
|----------------|--------------|
| `_norm_space` | `utils.norm_space` |
| `_safe_lower` | `utils.safe_lower` |
| `_strip_name_token` | `utils.strip_name_token` |
| `normalize_text_for_matching` | `utils.normalize_text_for_matching` |
| `PatternRegistry` | `patterns.PatternRegistry` |
| `AddressFilter` | `filters.AddressFilter` |
| `CityNameFilter` | `filters.CityNameFilter` |
| `TechnologyFilter` | `filters.TechnologyFilter` |
| `AnonymizeConfig` | `config.AnonymizeConfig` |
| `ObfuscationTracker` | `tracker.ObfuscationTracker` |
| `NameCandidateExtractor` | `identity.extractor.NameCandidateExtractor` |
| `NameScorer` | `identity.scorer.NameScorer` |
| `NameVariantGenerator` | `identity.variant_generator.NameVariantGenerator` |
| `PrimaryIdentityResolver` | `identity.resolver.PrimaryIdentityResolver` |
| `CvAnonymizer` | `anonymizer.CvAnonymizer` |

## Verification Checklist

- [ ] All modules created with correct imports
- [ ] All internal function calls updated
- [ ] Unit tests created and passing
- [ ] `cv_llm_converter.py` updated and working
- [ ] Integration tests passing
- [ ] Documentation updated
- [ ] README.md complete

