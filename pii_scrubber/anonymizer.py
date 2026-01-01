"""
Main text anonymizer class.

This module contains the TextAnonymizer class that orchestrates the
entire anonymization pipeline.
"""

from __future__ import annotations

import re
import json
from dataclasses import asdict
from typing import Dict, List, Optional, Sequence, Set, Tuple, Any
from urllib.parse import urlparse

from presidio_analyzer import AnalyzerEngine, PatternRecognizer, Pattern, RecognizerResult
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

from .config import AnonymizeConfig
from .utils import norm_space, normalize_text_for_matching, span_overlaps
from .patterns import PatternRegistry
from .filters import AddressFilter, CityNameFilter, TechnologyFilter
from .tracker import ObfuscationTracker
from .identity import PrimaryIdentityResolver

class TextAnonymizer:
    _ENTITY_PRIORITY = {
        "ADDRESS": 100,
        "LINKEDIN_PROFILE": 95,
        "EMAIL_ADDRESS": 90,
        "PHONE_NUMBER": 80,
        "URL": 75,
        "PERSON": 70,
        "LOCATION": 10,
    }

    _URL_FIND_RE = re.compile(PatternRegistry.URL_FIND)

    def __init__(self, config: Optional[AnonymizeConfig] = None):
        self.config = config or AnonymizeConfig()
        self.analyzer = self._build_analyzer()
        self.anonymizer = AnonymizerEngine()
        self.identity_resolver = PrimaryIdentityResolver(self.config)

        if self.config.operators is None:
            self.operators = {
                "DEFAULT": OperatorConfig("replace", {"new_value": "<REDACTED>"}),
                "PERSON": OperatorConfig("replace", {"new_value": "<PERSON>"}),
                "ADDRESS": OperatorConfig("replace", {"new_value": "<ADDRESS>"}),
                "EMAIL_ADDRESS": OperatorConfig("replace", {"new_value": "<EMAIL>"}),
                "PHONE_NUMBER": OperatorConfig("replace", {"new_value": "<PHONE>"}),
                "LINKEDIN_PROFILE": OperatorConfig("replace", {"new_value": "<LINKEDIN>"}),
                "URL": OperatorConfig("replace", {"new_value": "<URL>"}),
                "LOCATION": OperatorConfig("replace", {"new_value": "<LOCATION>"}),
            }
        else:
            self.operators = self.config.operators
    
    def _config_to_dict(self) -> Dict[str, Any]:
        """Convert config to a dictionary for JSON serialization."""
        config_dict = asdict(self.config)
        # Convert operators if they exist
        if config_dict.get("operators") is not None:
            # Operators are OperatorConfig objects, convert to dict representation
            ops_dict = {}
            for key, op_config in config_dict["operators"].items():
                if isinstance(op_config, OperatorConfig):
                    ops_dict[key] = {
                        "type": op_config.operator_name,
                        "params": op_config.params
                    }
                else:
                    ops_dict[key] = op_config
            config_dict["operators"] = ops_dict
        return config_dict
    
    def _replace_entities_with_tracking(
        self,
        text: str,
        results: Sequence[RecognizerResult],
        tracker: ObfuscationTracker
    ) -> str:
        """
        Replace entities in text with numbered tokens and track each replacement.
        Processes entities in reverse order of position to avoid offset issues.
        """
        # Sort results by position (reverse order for safe replacement)
        sorted_results = sorted(results, key=lambda r: (r.start, r.end), reverse=True)
        
        out = text
        for r in sorted_results:
            entity_type = r.entity_type
            original_value = norm_space(text[r.start:r.end])
            
            # Skip if empty
            if not original_value:
                continue
            
            # Get next token for this entity type
            token = tracker.get_next_token(entity_type)
            
            # Replace in text (using original positions from original text)
            # Since we're processing in reverse, positions are still valid
            out = out[:r.start] + token + out[r.end:]
            
            # Record the obfuscation
            tracker.record_obfuscation(token, original_value)
        
        return out

    def anonymize(self, text: str, preferred_language: str = "de") -> Dict[str, Any]:
        """
        Anonymize text and return a JSON structure with obfuscation details.
        
        Returns:
            dict with keys:
                - original_text: str - the original input text
                - obfuscated_text: str - the text with PII obfuscated
                - config: dict - the configuration used for masking
                - obfuscations: list - list of dicts with 'key' and 'value' for each obfuscation
        """
        if not text:
            return {
                "original_text": text,
                "obfuscated_text": text,
                "config": self._config_to_dict(),
                "obfuscations": []
            }

        # Create tracker for this anonymization session
        tracker = ObfuscationTracker()

        # Apply PII obfuscation limit if configured
        if self.config.pii_obfuscation_limit > 0 and len(text) > self.config.pii_obfuscation_limit:
            # Only anonymize the first N characters
            text_to_anonymize = text[:self.config.pii_obfuscation_limit]
            text_remainder = text[self.config.pii_obfuscation_limit:]
            
            # Anonymize only the first part
            result = self._anonymize_text(text_to_anonymize, preferred_language, tracker)
            
            # Combine anonymized part + original remainder
            obfuscated_text = result["obfuscated_text"] + text_remainder
            
            return {
                "original_text": text,
                "obfuscated_text": obfuscated_text,
                "config": self._config_to_dict(),
                "obfuscations": tracker.get_mappings()
            }
        else:
            # Process entire text (no limit)
            result = self._anonymize_text(text, preferred_language, tracker)
            return {
                "original_text": text,
                "obfuscated_text": result["obfuscated_text"],
                "config": self._config_to_dict(),
                "obfuscations": tracker.get_mappings()
            }
    
    def _anonymize_text(self, text: str, preferred_language: str, tracker: ObfuscationTracker) -> Dict[str, Any]:
        """
        Internal method that performs the actual anonymization.
        Separated to allow limiting obfuscation to a portion of text.
        Returns dict with obfuscated_text.
        """
        if not text:
            return {"obfuscated_text": text}

        text_norm = normalize_text_for_matching(text)

        # 1) Analyze (multi-lang)
        results = self._analyze_multi_pass(text_norm, preferred_language=preferred_language)

        # 2) Filter out phone numbers that are actually dates
        results = self._filter_date_like_phones(results, text_norm)
        
        # 2b) Filter out PERSON entities that are technology names
        results = self._filter_technology_persons(results, text_norm)

        # 3) Collapse overlaps (priority-based)
        collapsed = self._collapse_overlaps(results, text_norm)

        extracted_urls = self._extract_entity_texts(text_norm, results, {"URL", "LINKEDIN_PROFILE"})
        extracted_emails = self._extract_entity_texts(text_norm, results, {"EMAIL_ADDRESS"})

        # 4) Replace entities with numbered tokens and track obfuscations
        out = self._replace_entities_with_tracking(text_norm, collapsed, tracker)

        # 6) Resolve primary identity + propagate variants / initials
        resolved = self.identity_resolver.resolve(
            text=text_norm,
            presidio_results=results,
            extracted_urls=extracted_urls,
            extracted_emails=extracted_emails,
        )

        if self.config.propagate_primary_name and resolved["variants"]:
            out = self._mask_variants_with_tracking(out, text_norm, resolved["variants"], tracker)

        if self.config.enable_initials and resolved["initials_patterns"]:
            out = self._mask_initials_with_tracking(out, text_norm, resolved["initials_patterns"], tracker)

        # 7) Combined URL policy + postprocessing pass
        out = self._apply_url_policy_and_postprocess(out, tracker)

        # 8) Debug
        if self.config.debug:
            self._print_debug(resolved)

        return {"obfuscated_text": out}

    # -----------------------
    # Phone date filtering
    # -----------------------

    def _filter_date_like_phones(self, results: Sequence[RecognizerResult], text: str) -> List[RecognizerResult]:
        """
        Filter out PHONE_NUMBER entities that are actually dates.
        This is a post-filter to catch date patterns that the regex didn't exclude.
        """
        if not results:
            return []

        out: List[RecognizerResult] = []
        for r in results:
            if r.entity_type != "PHONE_NUMBER":
                out.append(r)
                continue

            span_text = text[r.start:r.end]
            
            # Check if this looks like a date using the date patterns
            if (PatternRegistry.DATE_MONTH_YEAR.search(span_text) or
                PatternRegistry.DATE_YEAR_MONTH.search(span_text) or
                PatternRegistry.DATE_YEAR_RANGE.search(span_text) or
                PatternRegistry.DATE_MONTHYEAR_RANGE.search(span_text)):
                # This is a date, not a phone number
                continue

            # Also check if the span contains a year pattern (19xx or 20xx)
            if re.search(r"\b(?:19|20)\d{2}\b", span_text):
                # Contains a year, likely a date
                continue
            
            out.append(r)
        
        return out
    
    def _filter_technology_persons(self, results: Sequence[RecognizerResult], text: str) -> List[RecognizerResult]:
        """
        Filter out PERSON and LOCATION entities that are actually technology/product names or city names.
        This prevents tech names and cities from being incorrectly masked.
        """
        if not results:
            return []
        
        out: List[RecognizerResult] = []
        for r in results:
            # Filter PERSON entities that are cities or tech names
            if r.entity_type == "PERSON":
                span_text = norm_space(text[r.start:r.end])
                
                # Check if this is a city name (misclassified as PERSON)
                if CityNameFilter.should_exclude(span_text):
                    # This is a city, not a person - but keep it as LOCATION would be correct
                    # Actually, we should exclude it from PERSON masking
                    continue
                
                # Check if this is a technology name
                if TechnologyFilter.should_exclude(span_text):
                    # This is a tech name, not a person
                    continue
                
                # Also check context - if it's in a tech stack context, exclude it
                context_start = max(0, r.start - 50)
                context_end = min(len(text), r.end + 50)
                context = text[context_start:context_end].lower()
                
                tech_context_patterns = [
                    r"used\s+tech[:\s]",
                    r"technologies[:\s]",
                    r"tech\s+stack[:\s]",
                    r"skills[:\s]",
                    r"tools[:\s]",
                    r"eingesetzte\s+technologien[:\s]",  # German: "Used technologies"
                    r"technologien[:\s]",  # German: "Technologies"
                ]
                
                in_tech_context = any(re.search(pattern, context) for pattern in tech_context_patterns)
                if in_tech_context:
                    # In tech context, likely not a person name
                    continue
            
            # Filter LOCATION entities that are technology names
            elif r.entity_type == "LOCATION":
                span_text = norm_space(text[r.start:r.end])
                
                # Check if this is a technology name (misclassified as LOCATION)
                if TechnologyFilter.should_exclude(span_text):
                    # This is a tech name, not a location
                    continue

            out.append(r)

        return out

    # -----------------------
    # Combined URL policy + postprocessing
    # -----------------------

    def _apply_url_policy_and_postprocess(self, text: str, tracker: Optional[ObfuscationTracker] = None) -> str:
        """
        Combined pass: applies URL policy and postprocessing in a single traversal.
        More efficient than separate passes.
        """
        if not text:
            return text

        # First apply URL policy
        policy = self.config.url_policy

        def url_repl(m: re.Match) -> str:
            raw = m.group(0)
            if raw.startswith("<") and raw.endswith(">"):
                return raw

            domain, has_path = self._parse_domain_and_path(raw)
            if not domain:
                token = "<URL>"
                if tracker:
                    token = tracker.get_next_token("URL")
                    tracker.record_obfuscation(token, raw)
                return token

            if policy == "redact_all":
                token = "<URL>"
                if tracker:
                    token = tracker.get_next_token("URL")
                    tracker.record_obfuscation(token, raw)
                return token

            if policy == "keep_domain":
                # Keep domain, but track path obfuscation if present
                if has_path and tracker:
                    path_token = tracker.get_next_token("URL")
                    tracker.record_obfuscation(path_token, raw)
                    return f"{domain}/{path_token}"
                return f"{domain}/<PATH>" if has_path else domain

            if policy == "allowlist_domains_keep_domain":
                if domain in self.config.url_domain_allowlist:
                    # Keep domain, but track path obfuscation if present
                    if has_path and tracker:
                        path_token = tracker.get_next_token("URL")
                        tracker.record_obfuscation(path_token, raw)
                        return f"{domain}/{path_token}"
                    return f"{domain}/<PATH>" if has_path else domain
                token = "<URL>"
                if tracker:
                    token = tracker.get_next_token("URL")
                    tracker.record_obfuscation(token, raw)
                return token

            token = "<URL>"
            if tracker:
                token = tracker.get_next_token("URL")
                tracker.record_obfuscation(token, raw)
            return token

        out = self._URL_FIND_RE.sub(url_repl, text)

        # Then apply postprocessing cleanup
        # Note: We need to handle numbered tokens too (PERSON1, PERSON2, etc.)
        # Pattern to match any numbered token: <ENTITY_TYPE followed by optional digits>
        numbered_token_pattern = r"<([A-Z_]+)\d+>"
        
        # Collapse repeats for numbered tokens
        out = re.sub(rf"(?:<[A-Z_]+\d+>[\s]*){{2,}}", lambda m: m.group(0).split()[0] + " ", out)
        
        # Also handle legacy unnumbered tokens
        tags = {
            "<PERSON>",
            "<ADDRESS>",
            "<EMAIL>",
            "<PHONE>",
            "<LINKEDIN>",
            "<URL>",
            "<LOCATION>",
            "<REDACTED>",
        }

        # Collapse repeats for each legacy token (individually)
        for token in tags:
            out = re.sub(rf"(?:{re.escape(token)}[\s]*){{2,}}", token + " ", out)

        # Remove trailing spaces before newlines
        out = re.sub(r"[ \t]+\n", "\n", out)

        # Spacing before punctuation
        out = re.sub(r"\s+([,.;:!?])", r"\1", out)
        out = re.sub(r"([,.;:!?])([A-Za-z0-9<])", r"\1 \2", out)

        # "( <TAG> )" -> "(<TAG>)"
        out = re.sub(r"\(\s+(<[^>]+>)\s+\)", r"(\1)", out)

        # Collapse large empty blocks
        out = re.sub(r"\n{3,}", "\n\n", out).strip() + "\n"

        return out

    def postprocess(self, text: str) -> str:
        """
        Legacy method for backward compatibility.
        Delegates to combined method (URL policy with default settings).
        """
        return self._apply_url_policy_and_postprocess(text, tracker=None)

    # -----------------------
    # Analyzer + recognizers
    # -----------------------

    def _build_analyzer(self) -> AnalyzerEngine:
        configuration = {
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": lang, "model_name": model} for lang, model in self.config.spacy_models],
        }
        provider = NlpEngineProvider(nlp_configuration=configuration)
        nlp_engine = provider.create_engine()

        analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=list(self.config.supported_languages))
        self._add_regex_recognizers(analyzer)
        return analyzer

    def _add_regex_recognizers(self, analyzer: AnalyzerEngine) -> None:
        """Add all regex-based recognizers using patterns from PatternRegistry."""
        
        # Email addresses
        analyzer.registry.add_recognizer(
            PatternRecognizer(
                supported_entity="EMAIL_ADDRESS",
                supported_language="en",
                patterns=[
                    Pattern("email", PatternRegistry.EMAIL_STANDARD, 0.95),
                    Pattern("email_obf", PatternRegistry.EMAIL_OBFUSCATED, 0.75),
                ],
            )
        )

        # Phone numbers (stricter pattern to avoid dates)
        analyzer.registry.add_recognizer(
            PatternRecognizer(
                supported_entity="PHONE_NUMBER",
                supported_language="en",
                patterns=[
                    Pattern("phone_strict", PatternRegistry.PHONE_STRICT, 0.85)
                ],
            )
        )

        # German addresses
        analyzer.registry.add_recognizer(
            PatternRecognizer(
                supported_entity="ADDRESS",
                supported_language="de",
                patterns=[
                    Pattern("de_address_full", PatternRegistry.ADDRESS_FULL_DE, 0.85),
                    Pattern("de_zip_city", PatternRegistry.ADDRESS_ZIP_CITY_DE, 0.50),
                ],
            )
        )

        # LinkedIn profiles
        analyzer.registry.add_recognizer(
            PatternRecognizer(
                supported_entity="LINKEDIN_PROFILE",
                supported_language="en",
                patterns=[
                    Pattern("linkedin_profile_any", PatternRegistry.LINKEDIN_PROFILE, 0.95)
                ],
            )
        )

        # URLs
        analyzer.registry.add_recognizer(
            PatternRecognizer(
                supported_entity="URL",
                supported_language="en",
                patterns=[
                    Pattern("url_scheme", PatternRegistry.URL_SCHEME, 0.85),
                    Pattern("url_bare", PatternRegistry.URL_BARE, 0.70),
                ],
            )
        )

    # -----------------------
    # Analyze passes
    # -----------------------

    def _analyze_multi_pass(self, text: str, preferred_language: str) -> List[RecognizerResult]:
        langs = list(self.config.supported_languages)
        if preferred_language not in langs:
            preferred_language = langs[0]

        ordered = [preferred_language]
        if self.config.run_both_lang_passes:
            ordered += [l for l in langs if l != preferred_language]

        merged: List[RecognizerResult] = []
        for lang in ordered:
            merged.extend(self.analyzer.analyze(text=text, entities=list(self.config.target_entities), language=lang))
        return merged

    # -----------------------
    # Overlap collapse
    # -----------------------

    def _collapse_overlaps(self, results: Sequence[RecognizerResult], original_text: str) -> List[RecognizerResult]:
        if not results:
            return []

        sorted_results = sorted(results, key=lambda r: (r.start, -r.end, -(r.end - r.start), -r.score))
        out: List[RecognizerResult] = []

        for r in sorted_results:
            span_len = r.end - r.start
            if span_len > 160:
                continue
            if "\n" in original_text[r.start:r.end]:
                continue

            if not out:
                out.append(self._clone(r))
                continue

            last = out[-1]
            if r.start <= last.end:
                if self._better(r, last):
                    out[-1] = self._clone(r)
            else:
                out.append(self._clone(r))

        return out

    def _better(self, cand: RecognizerResult, curr: RecognizerResult) -> bool:
        cp = self._ENTITY_PRIORITY.get(cand.entity_type, 0)
        rp = self._ENTITY_PRIORITY.get(curr.entity_type, 0)
        if cp != rp:
            return cp > rp
        if cand.score != curr.score:
            return cand.score > curr.score
        return (cand.end - cand.start) > (curr.end - curr.start)

    @staticmethod
    def _clone(r: RecognizerResult) -> RecognizerResult:
        return RecognizerResult(entity_type=r.entity_type, start=r.start, end=r.end, score=r.score)

    # -----------------------
    # Propagation helpers
    # -----------------------

    def _mask_variants(self, text: str, variants: Set[str], label: str) -> str:
        out = text
        for v in sorted(variants, key=len, reverse=True):
            out = re.sub(rf"\b{re.escape(v)}\b", label, out, flags=re.IGNORECASE)
        return out

    def _mask_variants_with_tracking(
        self, 
        text: str, 
        original_text: str, 
        variants: Set[str], 
        tracker: ObfuscationTracker
    ) -> str:
        """Mask variants and track each replacement."""
        out = text
        for v in sorted(variants, key=len, reverse=True):
            # Find all matches in original text
            pattern = rf"\b{re.escape(v)}\b"
            matches = list(re.finditer(pattern, original_text, flags=re.IGNORECASE))
            
            if matches:
                # Get next PERSON token
                token = tracker.get_next_token("PERSON")
                
                # Replace in output text
                out = re.sub(pattern, token, out, flags=re.IGNORECASE)
                
                # Record obfuscation (use first match's value as representative)
                original_value = original_text[matches[0].start():matches[0].end()]
                tracker.record_obfuscation(token, original_value)
        
        return out

    def _mask_initials(self, text: str, patterns: Sequence[str], label: str) -> str:
        out = text
        for pat in patterns:
            out = re.sub(pat, label, out)
        return out

    def _mask_initials_with_tracking(
        self, 
        text: str, 
        original_text: str, 
        patterns: Sequence[str], 
        tracker: ObfuscationTracker
    ) -> str:
        """Mask initials patterns and track each replacement."""
        out = text
        for pat in patterns:
            # Find all matches in original text
            matches = list(re.finditer(pat, original_text))
            
            if matches:
                # Get next PERSON token
                token = tracker.get_next_token("PERSON")
                
                # Replace in output text
                out = re.sub(pat, token, out)
                
                # Record obfuscation (use first match's value as representative)
                original_value = original_text[matches[0].start():matches[0].end()]
                tracker.record_obfuscation(token, original_value)
        
        return out


    @staticmethod
    def _parse_domain_and_path(raw: str) -> Tuple[Optional[str], bool]:
        s = raw.strip()
        if not re.match(r"^https?://", s, flags=re.IGNORECASE):
            s_for_parse = "http://" + s
        else:
            s_for_parse = s

        try:
            p = urlparse(s_for_parse)
            netloc = (p.netloc or "").lower()
            if not netloc:
                return None, False
            domain = netloc[4:] if netloc.startswith("www.") else netloc
            has_path = bool((p.path and p.path != "/") or p.query)
            return domain, has_path
        except Exception:
            return None, False

    # -----------------------
    # Entity text extraction
    # -----------------------

    @staticmethod
    def _extract_entity_texts(text: str, results: Sequence[RecognizerResult], types: Set[str]) -> List[str]:
        out: List[str] = []
        for r in results:
            if r.entity_type in types:
                frag = norm_space(text[r.start:r.end])
                if frag:
                    out.append(frag)

        seen = set()
        uniq: List[str] = []
        for x in out:
            k = x.lower()
            if k not in seen:
                seen.add(k)
                uniq.append(x)
        return uniq

    # -----------------------
    # Debug
    # -----------------------

    def _print_debug(self, resolved: Dict) -> None:
        dbg = resolved.get("debug", {})
        print("\n=== CV ANONYMIZER DEBUG ===")
        print(f"Chosen candidate name: {dbg.get('chosen_name')!r}")
        print(f"Chosen source: {dbg.get('chosen_source')}")
        print(f"Chosen total score: {dbg.get('chosen_score_total')}")
        print("Why chosen (score breakdown):")
        bd = dbg.get("chosen_score_breakdown") or {}
        for k, v in bd.items():
            print(f"  - {k}: {v}")

        print("\nTop candidates (score):")
        for c in dbg.get("candidates_top", []):
            print(
                f"  * {c['name']!r}  score={c['score_total']:.1f}  "
                f"source={c.get('source')}  breakdown={c.get('score_breakdown')}"
            )

        print("\nMasked tokens/variants:")
        for v in dbg.get("masked_variants", []):
            print(f"  - {v}")

        print("\nMasked initials patterns:")
        for p in dbg.get("masked_initials_patterns", []):
            print(f"  - {p}")
        print("===========================\n")


# -----------------------------
# Demo
# -----------------------------
# -----------------------------
# Demo
# -----------------------------
if __name__ == "__main__":
    sample = """
Lebenslauf

Aleksandar Herman Balaban     
Office: 06221 / 123456
Mobile: +49 171 2345678

Online Producer/Webdeveloper
selbstständig freiberuflich

Projects:

2/2025 - 12/2025
My last Java project    
used tech: Java, Python, JavaScript, Oracle, MySQL, Kafka, Anthropic    
2/1997 - 12/1998
My first Java project für eine 'Duale Hochschule

Used tech: Python, Jenkins, Crew AI, JavaScript, Oracle, MySQL
2/1997 - 12/1998
My first Java project
Verwendung von Prometheus für tracking
Used tech: Python, Jenkins, Crew AI, JavaScript, Oracle, MySQL, Gradle    
"""
    anon = TextAnonymizer(AnonymizeConfig(debug=True, url_policy="keep_domain"))
    result = anon.anonymize(sample, preferred_language="de")
    print("=== OBFUSCATED TEXT ===")
    print(result["obfuscated_text"])
    print("\n=== OBFUSCATIONS ===")
    for obf in result["obfuscations"]:
        print(f"{obf['key']}: {obf['value']}")
    print(f"\n=== CONFIG ===")
    print(json.dumps(result["config"], indent=2, default=str))