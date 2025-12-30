"""
cv_pii_scrubber.py

Production-grade CV text anonymizer (DE/EN) using a hybrid approach.

Pipeline (deterministic, auditable):
  1) Normalize text for stable matching (apostrophes, hyphens, NBSP)
  2) Presidio Analyzer (spaCy NER) + custom regex recognizers for high-precision PII
  3) Validate/filter risky PHONE_NUMBER detections (avoid eating dates/years)
  4) First anonymization pass with Presidio spans
  5) PrimaryIdentityResolver selects candidate's primary name (candidate extraction + scoring)
  6) Propagate masking for chosen name variants + initials
  7) URL policy pass (avoid leaking personal websites / repeated footer URLs)
  8) Postprocess pass (collapse repeated tags, punctuation spacing, blank lines)
  9) Optional debug output: chosen identity, scoring breakdown, masked variants/patterns

Why phone filtering exists:
  CVs contain many date ranges (e.g. "2/1997 - 12/1998", "2003-2004") that sloppy phone patterns
  can misclassify as PHONE_NUMBER. We therefore drop PHONE_NUMBER results unless:
    - the matched span has >= min_phone_digits digits (configurable), AND
    - the matched span is not "date-like" by a set of heuristics.

Dependencies:
  pip install presidio-analyzer presidio-anonymizer spacy
  python -m spacy download de_core_news_md
  python -m spacy download en_core_web_lg
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple, Set
from urllib.parse import urlparse

from presidio_analyzer import AnalyzerEngine, PatternRecognizer, Pattern, RecognizerResult
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig


# -----------------------------
# Config
# -----------------------------

@dataclass(frozen=True)
class AnonymizeConfig:
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
        "LOCATION",
    )

    run_both_lang_passes: bool = True

    # Identity resolution / propagation
    propagate_primary_name: bool = True
    enable_initials: bool = True
    min_name_token_len: int = 3
    min_lastname_len_for_initials: int = 3

    # Phone validation (prevents dates/years being masked as phones)
    min_phone_digits: int = 8
    drop_phone_if_date_like: bool = True

    # URL policy:
    # "redact_all" | "keep_domain" | "allowlist_domains_keep_domain"
    url_policy: str = "keep_domain"
    url_domain_allowlist: Tuple[str, ...] = ("linkedin.com", "www.linkedin.com")

    debug: bool = False

    # Override Presidio anonymizer operators if you want custom tokens
    operators: Optional[dict] = None


# -----------------------------
# Normalization + helpers
# -----------------------------

def _norm_space(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _safe_lower(s: str) -> str:
    return s.casefold()


def _span_overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and b_start < a_end


def normalize_text_for_matching(text: str) -> str:
    """
    Normalize punctuation/whitespace so regex propagation is stable.
    """
    t = text
    t = t.replace("’", "'").replace("‘", "'")
    t = t.replace("‐", "-").replace("–", "-").replace("—", "-")
    t = re.sub(r"[\u00A0\u2007\u202F]", " ", t)  # NBSP variants
    return t


def _strip_name_token(token: str) -> str:
    """
    Keep letters/digits/underscore + German letters + hyphen + apostrophe.
    """
    return re.sub(r"[^\wÄÖÜäöüß\-']", "", token)


def _contains_address_markers(s: str) -> bool:
    return bool(re.search(r"(straße|strasse|str\.)\b", s, flags=re.IGNORECASE)) or bool(re.search(r"\b\d{5}\b", s))


# -----------------------------
# PHONE filtering (avoid date false positives)
# -----------------------------

_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_MONTH_YEAR_RE = re.compile(r"\b\d{1,2}[\/.](?:19|20)\d{2}\b")          # 2/1997 or 12.1998
_YEAR_MONTH_RE = re.compile(r"\b(?:19|20)\d{2}[\/.]\d{1,2}\b")          # 1997/12 or 1998.2
_MONTHYEAR_RANGE_RE = re.compile(
    r"\b\d{1,2}[\/.](?:19|20)\d{2}\s*[-–]\s*\d{1,2}[\/.](?:19|20)\d{2}\b"
)  # 2/1997 - 12/1998


def _digits_only(s: str) -> str:
    return re.sub(r"\D", "", s)


def _is_date_like_phone_span(span_text: str) -> bool:
    """
    Heuristics: reject spans that look like dates / date ranges.

    Note:
      This is intentionally conservative: it prefers *not masking* over masking a year range.
      It is applied ONLY to PHONE_NUMBER spans.
    """
    s = span_text.strip()

    # Classic CV patterns
    if _MONTHYEAR_RANGE_RE.search(s):
        return True

    # Explicit month/year or year/month tokens
    if _MONTH_YEAR_RE.search(s) or _YEAR_MONTH_RE.search(s):
        return True

    # If the span includes a year token at all, assume "date-ish" in CV context.
    # This prevents things like "2/1997" being partially masked.
    if _YEAR_RE.search(s):
        return True

    return False


# -----------------------------
# Primary Identity Resolver
# -----------------------------

class PrimaryIdentityResolver:
    _STOPWORDS = {
        "curriculum", "vitae", "lebenslauf", "profil", "profile", "summary",
        "kontakt", "contact", "information", "info", "adresse", "address",
        "telefon", "phone", "email", "e-mail", "linkedin", "github", "portfolio",
        "website", "webseite", "blog",
        "senior", "junior", "engineer", "developer", "architect", "consultant",
        "principal", "platform", "cloud", "data", "services",
        "gmbh", "ag", "inc", "ltd", "llc", "company", "university", "universität",
        "prof", "prof.", "dr", "dr.", "mr", "mrs", "ms",
    }

    _TITLE_CASE_WORD = re.compile(r"^[A-ZÄÖÜ][a-zäöüß]+(?:[-'][A-ZÄÖÜa-zäöüß]+)?$")

    def __init__(self, config: AnonymizeConfig):
        self.config = config

    def resolve(
        self,
        text: str,
        presidio_results: Sequence[RecognizerResult],
        extracted_urls: Sequence[str],
        extracted_emails: Sequence[str],
    ) -> Dict:
        text_norm = normalize_text_for_matching(text)
        lines = text_norm.splitlines()

        address_spans = [(r.start, r.end) for r in presidio_results if r.entity_type == "ADDRESS"]

        candidates: List[Dict] = []
        candidates += self._candidates_from_person_spans(text_norm, presidio_results, address_spans)
        candidates += self._candidates_from_header_lines(lines)
        candidates += self._candidates_from_emails(extracted_emails)
        candidates += self._candidates_from_linkedin(extracted_urls)

        scored = self._score_candidates(candidates, text_norm)
        chosen = max(scored, key=lambda c: c["score_total"], default=None)

        variants: Set[str] = set()
        initials_patterns: List[str] = []
        chosen_name = ""

        if chosen and chosen.get("name"):
            chosen_name = chosen["name"]
            variants = self._derive_variants(chosen_name, text_norm)
            if self.config.enable_initials:
                initials_patterns = self._build_initials_patterns(variants, text_norm)

        debug_info = {
            "chosen_name": chosen_name,
            "chosen_source": chosen.get("source") if chosen else None,
            "chosen_score_total": chosen.get("score_total") if chosen else None,
            "chosen_score_breakdown": chosen.get("score_breakdown") if chosen else None,
            "candidates_top": sorted(scored, key=lambda c: c["score_total"], reverse=True)[:10],
            "masked_variants": sorted(variants, key=len, reverse=True),
            "masked_initials_patterns": initials_patterns,
        }

        return {
            "chosen_name": chosen_name,
            "variants": variants,
            "initials_patterns": initials_patterns,
            "debug": debug_info,
        }

    def _candidates_from_person_spans(
        self,
        text: str,
        results: Sequence[RecognizerResult],
        address_spans: Sequence[Tuple[int, int]],
    ) -> List[Dict]:
        out: List[Dict] = []
        for r in results:
            if r.entity_type != "PERSON":
                continue
            if any(_span_overlaps(r.start, r.end, a0, a1) for a0, a1 in address_spans):
                continue
            frag = _norm_space(text[r.start:r.end])
            if not frag or len(frag) < 3:
                continue
            if _contains_address_markers(frag):
                continue
            out.append({
                "name": frag,
                "source": "presidio_person",
                "meta": {"start": r.start, "end": r.end, "score": r.score},
            })
        return out

    def _candidates_from_header_lines(self, lines: List[str]) -> List[Dict]:
        out: List[Dict] = []

        contact_zone_start: Optional[int] = None
        for i, raw in enumerate(lines[:60]):
            if re.search(r"\b(contact|kontakt|address|adresse|email|e-mail|phone|telefon)\b", raw, flags=re.IGNORECASE):
                contact_zone_start = i
                break

        max_header = 20 if contact_zone_start is None else min(20, contact_zone_start)

        for i, raw in enumerate(lines[:max_header]):
            line = _norm_space(raw)
            if not line or len(line) > 70:
                continue
            if _contains_address_markers(line):
                continue

            words = [w for w in re.split(r"\s+", line) if w]
            name_like: List[str] = []
            for w in words:
                w_clean = _strip_name_token(normalize_text_for_matching(w))
                if not w_clean:
                    continue
                if self._TITLE_CASE_WORD.match(w_clean) or re.match(r"^[A-ZÄÖÜ][a-zäöüß]+(?:[-'][A-ZÄÖÜa-zäöüß]+)+$", w_clean):
                    if _safe_lower(w_clean) not in self._STOPWORDS:
                        name_like.append(w_clean)

            if len(name_like) >= 2:
                cand = " ".join(name_like[:4])
                if _contains_address_markers(cand):
                    continue
                out.append({
                    "name": cand,
                    "source": "header_line",
                    "meta": {"line_idx": i, "line": line},
                })

        return out

    def _candidates_from_emails(self, emails: Sequence[str]) -> List[Dict]:
        out: List[Dict] = []
        for e in emails:
            local = e.split("@", 1)[0]
            parts = re.split(r"[._\-+]+", local)
            parts = [p for p in parts if p and len(p) >= 2 and not p.isdigit()]

            if any(p.lower() in {"info", "kontakt", "contact", "hr", "jobs", "career", "bewerbung"} for p in parts):
                continue

            if len(parts) >= 2:
                cand = f"{parts[0].capitalize()} {parts[1].capitalize()}"
                out.append({"name": cand, "source": "email_localpart", "meta": {"email": e, "local": local}})
            elif len(parts) == 1:
                out.append({"name": parts[0].capitalize(), "source": "email_localpart_weak", "meta": {"email": e, "local": local}})
        return out

    def _candidates_from_linkedin(self, urls: Sequence[str]) -> List[Dict]:
        out: List[Dict] = []
        for u in urls:
            handle = self._extract_linkedin_handle(u)
            if not handle:
                continue
            handle = re.sub(r"\d+$", "", handle)
            parts = [p for p in re.split(r"[-_]+", handle) if p and len(p) >= 2]
            if len(parts) >= 2:
                cand = f"{parts[0].capitalize()} {parts[1].capitalize()}"
            else:
                cand = parts[0].capitalize() if parts else ""
            if cand:
                out.append({"name": cand, "source": "linkedin_handle", "meta": {"url": u, "handle": handle}})
        return out

    @staticmethod
    def _extract_linkedin_handle(url: str) -> Optional[str]:
        m = re.search(r"linkedin\.com/(?:in|pub)/([^/?#\s]+)", url, flags=re.IGNORECASE)
        return m.group(1) if m else None

    def _score_candidates(self, candidates: List[Dict], text: str) -> List[Dict]:
        scored: List[Dict] = []

        for c in candidates:
            name = normalize_text_for_matching(_norm_space(c["name"]))
            if _contains_address_markers(name):
                continue

            tokens = [_strip_name_token(t) for t in re.split(r"\s+", name) if t]
            tokens = [t for t in tokens if t]

            breakdown: Dict[str, float] = {}
            score = 0.0

            src = c.get("source", "")
            src_weight = {
                "presidio_person": 60,
                "header_line": 40,
                "linkedin_handle": 18,
                "email_localpart": 14,
                "email_localpart_weak": 6,
            }.get(src, 5)
            score += src_weight
            breakdown["source_weight"] = src_weight

            if 2 <= len(tokens) <= 4:
                score += 25
                breakdown["token_count_bonus"] = 25
            elif len(tokens) == 1:
                score += 5
                breakdown["token_count_bonus"] = 5
            else:
                score -= 10
                breakdown["token_count_bonus"] = -10

            stop_pen = 0.0
            for t in tokens:
                if _safe_lower(t) in self._STOPWORDS:
                    stop_pen += 25.0
            if stop_pen:
                score -= stop_pen
                breakdown["stopword_penalty"] = -stop_pen

            if src == "header_line":
                li = int(c.get("meta", {}).get("line_idx", 999))
                if li <= 6:
                    pos_bonus = max(0, 25 - (li * 3))
                    score += pos_bonus
                    breakdown["position_bonus"] = pos_bonus
                else:
                    score -= 15
                    breakdown["late_header_penalty"] = -15

            if src == "presidio_person":
                ps = float(c.get("meta", {}).get("score", 0.0))
                ps_bonus = min(20.0, ps * 20.0)
                score += ps_bonus
                breakdown["presidio_score_bonus"] = ps_bonus

            freq_bonus = 0.0
            for t in tokens:
                if len(t) < self.config.min_name_token_len:
                    continue
                cnt = len(re.findall(rf"\b{re.escape(t)}\b", text, flags=re.IGNORECASE))
                if cnt >= 2:
                    freq_bonus += 5.0
            if freq_bonus:
                score += min(20.0, freq_bonus)
                breakdown["frequency_bonus"] = min(20.0, freq_bonus)

            if re.search(r"\d", name):
                score -= 30.0
                breakdown["digits_penalty"] = -30.0

            scored.append({
                **c,
                "name": name,
                "tokens": tokens,
                "score_total": score,
                "score_breakdown": breakdown,
            })

        best_by_name: Dict[str, Dict] = {}
        for c in scored:
            key = _safe_lower(c["name"])
            if key not in best_by_name or c["score_total"] > best_by_name[key]["score_total"]:
                best_by_name[key] = c

        return list(best_by_name.values())

    def _derive_variants(self, chosen_name: str, full_text: str) -> Set[str]:
        chosen_name = normalize_text_for_matching(chosen_name)
        tokens_raw = [t for t in re.split(r"\s+", chosen_name) if t]

        clean: List[str] = []
        for t in tokens_raw:
            t2 = _strip_name_token(t)
            if len(t2) < self.config.min_name_token_len:
                continue
            if _safe_lower(t2) in self._STOPWORDS:
                continue
            clean.append(t2)

        if not clean:
            return set()

        variants: Set[str] = set()
        full = " ".join(clean)
        variants.add(full)
        variants.add(clean[0])
        if len(clean) >= 2:
            variants.add(clean[-1])

        first = clean[0]
        for k in range(4, len(first)):
            pref = first[:k]
            if pref.endswith("-") or pref.endswith("'"):
                continue
            if re.search(rf"\b{re.escape(pref)}\b", full_text, flags=re.IGNORECASE):
                variants.add(pref)

        return {v for v in variants if len(v) >= self.config.min_name_token_len}

    def _build_initials_patterns(self, variants: Set[str], full_text: str) -> List[str]:
        full = max(variants, key=len, default="")
        toks = [t for t in full.split() if t]
        if len(toks) < 2:
            return []
        first, last = toks[0], toks[-1]
        if len(last) < self.config.min_lastname_len_for_initials:
            return []

        fi = re.escape(first[0].upper())
        li = re.escape(last[0].upper())
        last_esc = re.escape(last)

        patterns: List[str] = []
        patterns.append(rf"\b{fi}\.?\s+{last_esc}\b")                 # M. O'Connell
        patterns.append(rf"\b{fi}\.\s*{li}\.(?=\b|[\s,;:])")         # M.O. (stable with punctuation)

        if re.search(rf"\b{fi}\.\s*{li}\b", full_text):
            patterns.append(rf"\b{fi}\.\s*{li}\b")                   # M.O

        if re.search(rf"\b{fi}\s+{li}\b", full_text):
            patterns.append(rf"\b{fi}\s+{li}\b")                     # M O

        return patterns


# -----------------------------
# CvAnonymizer
# -----------------------------

class CvAnonymizer:
    _ENTITY_PRIORITY = {
        "ADDRESS": 100,
        "LINKEDIN_PROFILE": 95,
        "EMAIL_ADDRESS": 90,
        "PHONE_NUMBER": 80,
        "URL": 75,
        "PERSON": 70,
        "LOCATION": 10,
    }

    _URL_FIND_RE = re.compile(
        r"\bhttps?://[^\s<>()\[\]\"']{6,}\b|\b(?:www\.)?[a-zA-Z0-9-]+\.[a-zA-Z]{2,}(?:/[^\s<>()\[\]\"']*)?\b"
    )

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

    def anonymize(self, text: str, preferred_language: str = "de") -> str:
        if not text:
            return text

        text_norm = normalize_text_for_matching(text)

        # 1) Analyze (multi-lang)
        results = self._analyze_multi_pass(text_norm, preferred_language=preferred_language)

        # 2) Drop risky phone detections BEFORE overlap collapsing and BEFORE anonymization
        results = self._filter_phone_results(results, text_norm)

        # 3) Collapse overlaps (priority-based)
        collapsed = self._collapse_overlaps(results, text_norm)

        extracted_urls = self._extract_entity_texts(text_norm, results, {"URL", "LINKEDIN_PROFILE"})
        extracted_emails = self._extract_entity_texts(text_norm, results, {"EMAIL_ADDRESS"})

        # 4) First anonymization pass
        anon = self.anonymizer.anonymize(text=text_norm, analyzer_results=collapsed, operators=self.operators)
        out = anon.text

        # 5) Resolve primary identity + propagate variants / initials
        resolved = self.identity_resolver.resolve(
            text=text_norm,
            presidio_results=results,
            extracted_urls=extracted_urls,
            extracted_emails=extracted_emails,
        )

        if self.config.propagate_primary_name and resolved["variants"]:
            out = self._mask_variants(out, resolved["variants"], label="<PERSON>")

        if self.config.enable_initials and resolved["initials_patterns"]:
            out = self._mask_initials(out, resolved["initials_patterns"], label="<PERSON>")

        # 6) Final URL anti-leak pass
        out = self._apply_url_policy(out)

        # 7) Debug
        if self.config.debug:
            self._print_debug(resolved)

        # 8) Final cleanup pass
        out = self.postprocess(out)
        return out

    # -----------------------
    # NEW: Phone filtering (avoid dates/years being masked)
    # -----------------------

    def _filter_phone_results(self, results: Sequence[RecognizerResult], text: str) -> List[RecognizerResult]:
        """
        Drop PHONE_NUMBER spans unless they look like real phones in CV context.

        Rules:
          - Must have at least config.min_phone_digits digits in the span
          - If config.drop_phone_if_date_like is True, reject spans that look like dates
            (month/year, year/month, month/year ranges, or any year token)
        """
        if not results:
            return []

        out: List[RecognizerResult] = []
        for r in results:
            if r.entity_type != "PHONE_NUMBER":
                out.append(r)
                continue

            span = text[r.start:r.end]
            digits = _digits_only(span)

            if len(digits) < self.config.min_phone_digits:
                continue

            if self.config.drop_phone_if_date_like and _is_date_like_phone_span(span):
                continue

            out.append(r)

        return out

    # -----------------------
    # Postprocess (member function)
    # -----------------------

    def postprocess(self, text: str) -> str:
        """
        Final readability cleanup after multi-pass anonymization.

        - Collapses repeated tags: "<PERSON> <PERSON>" -> "<PERSON>"
        - Fixes spaces before punctuation: "<PERSON> ," -> "<PERSON>,"
        - Limits blank lines
        """
        if not text:
            return text

        out = text
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

        # Collapse repeats for each token (individually)
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
        analyzer.registry.add_recognizer(
            PatternRecognizer(
                supported_entity="EMAIL_ADDRESS",
                supported_language="en",
                patterns=[
                    Pattern("email", r"\b[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+\b", 0.95),
                    Pattern(
                        "email_obf",
                        r"\b[a-zA-Z0-9_.+-]+\s*(?:\(|\[)?\s*(?:at|@)\s*(?:\)|\])?\s*[a-zA-Z0-9-]+\s*(?:\(|\[)?\s*(?:dot|\.)\s*(?:\)|\])?\s*[a-zA-Z0-9-.]+\b",
                        0.75,
                    ),
                ],
            )
        )

        # NOTE:
        # We keep the phone recognizer permissive, and rely on _filter_phone_results to prevent
        # dates/years from being masked as <PHONE>.
        analyzer.registry.add_recognizer(
            PatternRecognizer(
                supported_entity="PHONE_NUMBER",
                supported_language="en",
                patterns=[
                    Pattern(
                        "phone_candidate",
                        r"\b(?:\+?\d{1,3}[\s().-]?)?(?:\(?\d{2,5}\)?[\s().-]?)?\d[\d\s().-]{6,}\d\b",
                        0.80,
                    )
                ],
            )
        )

        street_types = r"(?:straße|strasse|str\.|weg|allee|gasse|platz|ring|damm|ufer)"
        analyzer.registry.add_recognizer(
            PatternRecognizer(
                supported_entity="ADDRESS",
                supported_language="de",
                patterns=[
                    Pattern(
                        "de_address_full",
                        rf"\b[A-ZÄÖÜ][\wÄÖÜäöüß\.\- ]{{2,}}?{street_types}\s+\d{{1,4}}[a-zA-Z]?\s*,?\s*\d{{5}}\s+[A-ZÄÖÜ][\wÄÖÜäöüß\.\- ]{{1,}}\b",
                        0.85,
                    ),
                    Pattern("de_zip_city", r"\b\d{5}\s+[A-ZÄÖÜ][\wÄÖÜäöüß\- ]{2,}\b", 0.50),
                ],
            )
        )

        analyzer.registry.add_recognizer(
            PatternRecognizer(
                supported_entity="LINKEDIN_PROFILE",
                supported_language="en",
                patterns=[
                    Pattern(
                        "linkedin_profile_any",
                        r"\b(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/(?:in|pub)/[A-Za-z0-9\-_%/]+/?\b",
                        0.95,
                    )
                ],
            )
        )

        analyzer.registry.add_recognizer(
            PatternRecognizer(
                supported_entity="URL",
                supported_language="en",
                patterns=[
                    Pattern("url_scheme", r"\bhttps?://[^\s<>()\[\]\"']{6,}\b", 0.85),
                    Pattern("url_bare", r"\b(?:www\.)?[a-zA-Z0-9-]+\.[a-zA-Z]{2,}(?:/[^\s<>()\[\]\"']*)?\b", 0.70),
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

    def _mask_initials(self, text: str, patterns: Sequence[str], label: str) -> str:
        out = text
        for pat in patterns:
            out = re.sub(pat, label, out)
        return out

    # -----------------------
    # URL policy
    # -----------------------

    def _apply_url_policy(self, text: str) -> str:
        policy = self.config.url_policy

        def repl(m: re.Match) -> str:
            raw = m.group(0)
            if raw.startswith("<") and raw.endswith(">"):
                return raw

            domain, has_path = self._parse_domain_and_path(raw)
            if not domain:
                return "<URL>"

            if policy == "redact_all":
                return "<URL>"

            if policy == "keep_domain":
                return f"{domain}/<PATH>" if has_path else domain

            if policy == "allowlist_domains_keep_domain":
                if domain in self.config.url_domain_allowlist:
                    return f"{domain}/<PATH>" if has_path else domain
                return "<URL>"

            return "<URL>"

        return self._URL_FIND_RE.sub(repl, text)

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
                frag = _norm_space(text[r.start:r.end])
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
2/1997 - 12/1998
My first Java project
"""
    anon = CvAnonymizer(AnonymizeConfig(debug=True, url_policy="keep_domain"))
    print(anon.anonymize(sample, preferred_language="de"))
