"""
Name candidate extraction from various sources.

This module extracts name candidates from Presidio results, headers,
emails, and LinkedIn profiles.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence, Tuple

from presidio_analyzer import RecognizerResult

from ..utils import norm_space, safe_lower, normalize_text_for_matching, strip_name_token, span_overlaps
from ..patterns import PatternRegistry
from ..filters import AddressFilter, TechnologyFilter

class NameCandidateExtractor:
    """
    Extracts name candidates from various sources (Presidio, headers, emails, LinkedIn).
    """
    _STOPWORDS = {
        # CV/document terms (English and German)
        "curriculum", "vitae", "lebenslauf", "profil", "profile", "summary",
        "kontakt", "contact", "information", "info", "adresse", "address",
        "telefon", "phone", "email", "e-mail", "linkedin", "github", "portfolio",
        "website", "webseite", "blog",
        # Job titles (English and German)
        "senior", "junior", "engineer", "developer", "architect", "consultant",
        "principal", "platform", "cloud", "data", "services",
        "entwickler", "ingenieur", "berater", "architekt",  # German job titles
        "leitender", "führender", "haupt",  # German senior/lead prefixes
        # Company/organization terms
        "gmbh", "ag", "inc", "ltd", "llc", "company", "university", "universität",
        "unternehmen", "firma", "gesellschaft",  # German company terms
        # Titles and honorifics
        "prof", "prof.", "dr", "dr.", "mr", "mrs", "ms",
        "herr", "frau", "doktor", "professor",  # German titles
    }
    _TITLE_CASE_WORD = re.compile(PatternRegistry.TITLE_CASE_WORD)

    def extract_all(
        self,
        text: str,
        presidio_results: Sequence[RecognizerResult],
        extracted_urls: Sequence[str],
        extracted_emails: Sequence[str],
    ) -> List[Dict]:
        """Extract candidates from all sources."""
        text_norm = normalize_text_for_matching(text)
        lines = text_norm.splitlines()
        address_spans = [(r.start, r.end) for r in presidio_results if r.entity_type == "ADDRESS"]

        candidates: List[Dict] = []
        candidates += self.from_person_spans(text_norm, presidio_results, address_spans)
        candidates += self.from_header_lines(lines)
        candidates += self.from_emails(extracted_emails)
        candidates += self.from_linkedin(extracted_urls)
        
        # Apply context-aware filtering (tech stack contexts)
        candidates = self._filter_tech_stack_context(candidates, text_norm)
        
        return candidates
    
    def _filter_tech_stack_context(self, candidates: List[Dict], text: str) -> List[Dict]:
        """
        Filter out candidates that appear in tech stack contexts (comma-separated lists, "used tech:" etc.)
        """
        filtered: List[Dict] = []
        text_lower = text.lower()
        
        for cand in candidates:
            name = cand.get("name", "")
            name_lower = safe_lower(name)
            
            # Check if this appears in a tech stack context
            # Look for patterns like "used tech:", "technologies:", "tech stack:", etc.
            # Include German patterns: "Eingesetzte Technologien:", "Technologien:", etc.
            tech_context_patterns = [
                r"used\s+tech[:\s]",
                r"technologies[:\s]",
                r"tech\s+stack[:\s]",
                r"skills[:\s]",
                r"tools[:\s]",
                r"eingesetzte\s+technologien[:\s]",  # German: "Used technologies"
                r"technologien[:\s]",  # German: "Technologies"
                r"verwendete\s+technologien[:\s]",  # German: "Technologies used"
                r"technologien\s+und\s+werkzeuge[:\s]",  # German: "Technologies and tools"
                r"verwendete\s+technik[:\s]",  # German: "Technology used"
                r"werkzeuge[:\s]",  # German: "Tools"
                r"software[:\s]",  # German: "Software"
                r"frameworks?[:\s]",  # German/English: "Frameworks"
                r"bibliotheken[:\s]",  # German: "Libraries"
                r"programmiersprachen?[:\s]",  # German: "Programming languages"
                r"kenntnisse[:\s]",  # German: "Skills/Knowledge"
                r"fähigkeiten[:\s]",  # German: "Abilities/Skills"
                r"technologies?\s+and\s+tools[:\s]",  # English: "Technologies and tools"
                r"programming\s+languages?[:\s]",  # English: "Programming languages"
            ]
            
            # Find all occurrences of this name in the text
            name_pattern = re.escape(name)
            matches = list(re.finditer(rf"\b{name_pattern}\b", text, flags=re.IGNORECASE))
            
            # Check if any match is in a tech context
            in_tech_context = False
            for match in matches:
                start = match.start()
                # Look backwards for tech context indicators
                context_start = max(0, start - 100)
                context = text[context_start:start + len(name) + 50].lower()
                
                for pattern in tech_context_patterns:
                    if re.search(pattern, context):
                        in_tech_context = True
                        break
                
                # Also check if it's in a comma-separated list (likely tech stack)
                # Look for pattern: word, word, word (at least 2 commas nearby)
                list_context = text[max(0, start - 50):min(len(text), start + len(name) + 50)]
                comma_count = list_context.count(',')
                if comma_count >= 2:
                    # Likely a tech stack list
                    in_tech_context = True
                    break
            
            if not in_tech_context:
                filtered.append(cand)
        
        return filtered

    def from_person_spans(
        self,
        text: str,
        results: Sequence[RecognizerResult],
        address_spans: Sequence[Tuple[int, int]],
    ) -> List[Dict]:
        """Extract candidates from Presidio PERSON entities."""
        out: List[Dict] = []
        for r in results:
            if r.entity_type != "PERSON":
                continue
            if any(span_overlaps(r.start, r.end, a0, a1) for a0, a1 in address_spans):
                continue
            frag = norm_space(text[r.start:r.end])
            if not frag or len(frag) < 3:
                continue
            if AddressFilter.should_exclude(frag):
                continue
            # Filter out technology names (with context for prefix checking)
            context_start = max(0, r.start - 50)
            context_end = min(len(text), r.end + 50)
            context = text[context_start:context_end]
            if TechnologyFilter.should_exclude(frag, context=context):
                continue
            
            # Clean name boundaries - remove common labels/prefixes
            cleaned_frag = self._clean_name_boundaries(frag, text, r.start, r.end)
            if not cleaned_frag or len(cleaned_frag) < 3:
                continue
            
            out.append({
                "name": cleaned_frag,
                "source": "presidio_person",
                "meta": {"start": r.start, "end": r.end, "score": r.score},
            })
        return out

    def _clean_name_boundaries(self, frag: str, full_text: str, start: int, end: int) -> str:
        """
        Clean name boundaries to remove common labels/prefixes like "Office:", "Mobile:", etc.
        """
        # Common labels that shouldn't be part of names (English and German)
        label_patterns = [
            r"^(office|mobile|phone|email|address|contact|tel|fax|büro|mobil|telefon|e-mail|adresse|kontakt)[:\s]*",
            r"[:\s]*(office|mobile|phone|email|address|contact|tel|fax|büro|mobil|telefon|e-mail|adresse|kontakt)$",
        ]
        
        cleaned = frag
        for pattern in label_patterns:
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
        
        # Split into words and filter out label words
        words = cleaned.split()
        filtered_words = []
        label_words = {
            # English
            "office", "mobile", "phone", "email", "address", "contact", "tel", "fax",
            # German
            "büro", "mobil", "telefon", "e-mail", "adresse", "kontakt", "fax",
        }
        
        for word in words:
            word_clean = strip_name_token(word)
            if word_clean and safe_lower(word_clean) not in label_words:
                filtered_words.append(word_clean)
        
        # If we removed words, return the cleaned version
        if len(filtered_words) < len(words):
            return " ".join(filtered_words)
        
        return cleaned.strip()

    def from_header_lines(self, lines: List[str]) -> List[Dict]:
        """Extract candidates from header lines (first 20 lines)."""
        out: List[Dict] = []
        contact_zone_start: Optional[int] = None
        for i, raw in enumerate(lines[:60]):
            if re.search(PatternRegistry.CONTACT_ZONE, raw, flags=re.IGNORECASE):
                contact_zone_start = i
                break

        max_header = 20 if contact_zone_start is None else min(20, contact_zone_start)

        for i, raw in enumerate(lines[:max_header]):
            line = norm_space(raw)
            if not line or len(line) > 70:
                continue
            if AddressFilter.should_exclude(line):
                continue

            words = [w for w in re.split(r"\s+", line) if w]
            name_like: List[str] = []
            for w in words:
                w_clean = strip_name_token(normalize_text_for_matching(w))
                if not w_clean:
                    continue
                if self._TITLE_CASE_WORD.match(w_clean) or re.match(PatternRegistry.TITLE_CASE_COMPOUND, w_clean):
                    if safe_lower(w_clean) not in self._STOPWORDS:
                        name_like.append(w_clean)

            if len(name_like) >= 2:
                cand = " ".join(name_like[:4])
                if AddressFilter.should_exclude(cand):
                    continue
                # Filter out technology names (with context for prefix checking)
                # Use the full line as context since we're extracting from header lines
                if TechnologyFilter.should_exclude(cand, context=line):
                    continue
                out.append({
                    "name": cand,
                    "source": "header_line",
                    "meta": {"line_idx": i, "line": line},
                })
        return out

    def from_emails(self, emails: Sequence[str]) -> List[Dict]:
        """Extract candidates from email local parts."""
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

    def from_linkedin(self, urls: Sequence[str]) -> List[Dict]:
        """Extract candidates from LinkedIn profile URLs."""
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
        m = re.search(PatternRegistry.LINKEDIN_HANDLE_EXTRACT, url, flags=re.IGNORECASE)
        return m.group(1) if m else None


# -----------------------------
# Name Scorer
# -----------------------------