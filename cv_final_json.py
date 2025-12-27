from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

# ---- import your modules ----
import cv_text_extractor as tex

# NOTE: adapt this import if your filename differs
import cv_timeline_chunker as tchunk


# =============================================================================
# Configuration: one place to plug your chunker function name
# =============================================================================

# If the chunker exposes a different function name, set it here.
CHUNKER_FUNCTION_CANDIDATES = [
    "extract_timeline_tech_and_totals",
]

# =============================================================================
# Regex helpers (high precision)
# =============================================================================

EMAIL_RE = re.compile(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.IGNORECASE)

# conservative phone detector: catches +49 ..., 0176 ..., with spaces/slashes/dashes
PHONE_RE = re.compile(
    r"""
    (?<!\w)
    (?:\+?\d{1,3}[\s\-\/]?)?
    (?:\(?\d{2,5}\)?[\s\-\/]?)?
    \d{2,5}[\s\-\/]?\d{2,5}[\s\-\/]?\d{0,6}
    (?!\w)
    """,
    re.VERBOSE,
)

URL_RE = re.compile(r"\bhttps?://[^\s)>\]]+\b", re.IGNORECASE)
BARE_DOMAIN_RE = re.compile(
    r"\b(?:www\.)?(?:[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+(?:[a-z]{2,24})(?:/[^\s)>\]]*)?\b",
    re.IGNORECASE,
)

# German city + country patterns are endless; keep it minimal.
GERMANY_RE = re.compile(r"\bGermany\b|\bDeutschland\b", re.IGNORECASE)
CITY_LINE_RE = re.compile(r"^[A-ZÄÖÜ][A-Za-zÄÖÜäöüß.\- ]{2,60}$")

# Section headings for slicing (broad but still anchored)
SECTION_HDR_RE = re.compile(
    r"^\s*(?:##\s*)?(?P<hdr>("
    r"profil|summary|zusammenfassung|kurzprofil|profile|"
    r"work\s+experience|professional\s+experience|experience|employment|berufserfahrung|werdegang|"
    r"skills?|kenntnisse|fähigkeiten|technologien|technologies|tech\s*stack|stack|"
    r"education|ausbildung|studium|bildung|"
    r"certifications?|certificates?|zertifikate|qualifikationen|"
    r"languages?|sprachen"
    r"))\s*:?\s*$",
    re.IGNORECASE,
)

EDU_SIGNAL_RE = re.compile(
    r"\b(bachelor|master|msc|bsc|phd|doktor|diplom|magister|degree|studium)\b"
    r"|\b(university|universität|hochschule|institute|institut)\b"
    r"|\b(19\d{2}|20\d{2})\b",
    re.IGNORECASE,
)

CERT_SIGNAL_RE = re.compile(
    r"\b(certified|certificate|certification|zertifikat|zertifizierung|ihk|itil|scrum|aws|azure|oracle)\b",
    re.IGNORECASE,
)

SKILLY_LINE_RE = re.compile(
    r"\b(java|python|spring|kubernetes|docker|aws|azure|linux|sql|postgres|mysql|react|angular|terraform)\b",
    re.IGNORECASE,
)


# =============================================================================
# Utility
# =============================================================================

def default_asof() -> str:
    today = date.today()
    return f"{today.year:04d}-{today.month:02d}"


def _dedupe_preserve(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for x in items:
        k = x.strip().lower()
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(x.strip())
    return out


def _normalize_phone(s: str) -> Optional[str]:
    s = s.strip()
    s = re.sub(r"[^\d+]", "", s)  # keep digits and leading +
    # too short -> discard
    digits = re.sub(r"\D", "", s)
    if len(digits) < 7:
        return None
    # avoid catching years or "20146" etc (very short)
    if len(digits) <= 6:
        return None
    # normalize "+00" etc not needed; keep as-is
    return s


def _classify_links(urls: List[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"personal": [], "linkedin": None, "github": [], "gitlab": [], "xing": None, "other": []}
    for u in urls:
        lu = u.lower()
        if "linkedin.com" in lu:
            out["linkedin"] = out["linkedin"] or u
        elif "github.com" in lu:
            out["github"].append(u)
        elif "gitlab.com" in lu:
            out["gitlab"].append(u)
        elif "xing.com" in lu:
            out["xing"] = out["xing"] or u
        else:
            # naive: if it looks like a personal site (no big platform), keep in personal
            if any(p in lu for p in ["medium.com", "dev.to", "stackoverflow.com"]):
                out["other"].append(u)
            else:
                out["personal"].append(u)

    out["personal"] = _dedupe_preserve(out["personal"])
    out["github"] = _dedupe_preserve(out["github"])
    out["gitlab"] = _dedupe_preserve(out["gitlab"])
    out["other"] = _dedupe_preserve(out["other"])
    return out


# =============================================================================
# Heuristic extractors (safe, partial)
# =============================================================================
# Don’t treat common tech tokens as domains even if they contain a dot.
NOT_A_DOMAIN = {
    "node.js", "next.js", "nuxt.js", "react.js", "vue.js", "deno.land",  # keep deno.land? (you can remove)
}

# TLDs you actually see in CVs; keeps false positives low.
ALLOWED_TLDS = {
    "de", "com", "net", "org", "io", "dev", "me", "info", "eu", "at", "ch", "uk",
}

def _looks_like_real_domain(s: str) -> bool:
    s = s.strip().lower().strip(".,;:()[]{}")
    if "@" in s:
        return False
    if s in NOT_A_DOMAIN:
        return False
    # split off path
    host = s.split("/", 1)[0]
    if host.count(".") < 1:
        return False
    tld = host.rsplit(".", 1)[-1]
    if tld not in ALLOWED_TLDS:
        return False
    # reject if host contains weird stuff
    if not re.fullmatch(r"[a-z0-9.\-]+", host):
        return False
    # avoid extremely short hosts like "a.de" (often noise)
    if len(host) < 6:
        return False
    return True

def _extract_name_from_header(head_lines: List[str]) -> Tuple[Optional[str], float]:
    """
    Try to get person name from first ~30 lines.
    Safe heuristics:
    - prefer first non-empty line that looks like 'Firstname Lastname'
    - avoid lines that look like headings, links, emails, phone, addresses
    """
    noise_words = {
        "lebenslauf", "curriculum", "vitae", "cv", "resume", "profil", "profile", "summary",
        "freiberufliche", "projektmitarbeit", "berufserfahrung", "experience",
    }

    for ln in head_lines[:30]:
        s = ln.strip()
        if not s or len(s) > 60:
            continue
        low = s.lower()
        if any(w in low for w in noise_words):
            continue
        if "@" in s or "http" in low:
            continue
        if PHONE_RE.search(s):
            continue
        if any(ch.isdigit() for ch in s):
            continue

        # candidate must be 2-4 "name-like" words
        parts = [p for p in re.split(r"\s+", s) if p]
        if not (2 <= len(parts) <= 4):
            continue
        # reject if any token is too short or non-alpha-ish
        if any(len(p) < 2 for p in parts):
            continue
        if any(not re.match(r"^[A-Za-zÄÖÜäöüß\-]+$", p) for p in parts):
            continue

        # looks like a name
        name = " ".join(p[0].upper() + p[1:] if p else p for p in parts)
        return name, 0.75

    return None, 0.0

def _extract_location_from_header(head_lines: List[str]) -> Tuple[Optional[str], float]:
    """
    Location should be a short line like 'Hamburg' or 'Berlin, Germany'.
    Don’t pick random sentences.
    """
    for ln in head_lines[:60]:
        s = ln.strip()
        if not s or len(s) > 40:
            continue
        low = s.lower()
        if "@" in s or "http" in low:
            continue
        if any(k in low for k in ["diplom", "master", "bachelor", "msc", "b.sc", "m.sc"]):
            continue
        if any(ch.isdigit() for ch in s):
            continue

        if GERMANY_RE.search(s):
            return s, 0.75
        if CITY_LINE_RE.match(s):
            return s, 0.6

    return None, 0.0


def extract_core_profile(plain_text: str, *, filename: Optional[str] = None) -> Dict[str, Any]:
    """
    High-precision contacts + safer heuristics for name/location.
    Fixes false domains like 'Node.js' => 'https://Node.js'.
    """
    lines = [ln.strip() for ln in plain_text.splitlines() if ln.strip()]
    head = lines[:140]  # slightly bigger, still “header-y”

    head_text = " ".join(head)

    # Emails
    emails = _dedupe_preserve(EMAIL_RE.findall(head_text))

    # URLs with scheme
    urls = _dedupe_preserve(URL_RE.findall(head_text))

    # Bare domains (strict filtering + scheme normalization)
    bare_urls: List[str] = []
    for m in BARE_DOMAIN_RE.finditer(head_text):
        s = m.group(0).strip()
        if not _looks_like_real_domain(s):
            continue
        if not s.lower().startswith(("http://", "https://")):
            s = "https://" + s.lstrip("/")
        bare_urls.append(s)

    urls = _dedupe_preserve(urls + bare_urls)

    # Phones
    phones_raw = PHONE_RE.findall(head_text)
    phones = _dedupe_preserve([p for p in (_normalize_phone(x) for x in phones_raw) if p])

    links = _classify_links(urls)

    # Name: prefer header text, fallback to filename
    name, name_conf = _extract_name_from_header(head)
    if not name and filename:
        base = os.path.splitext(os.path.basename(filename))[0]
        base = re.sub(r"[\W_]+", " ", base).strip()
        parts = [p for p in base.split() if p and len(p) > 1]
        if len(parts) >= 2:
            name = " ".join(p.capitalize() for p in parts[:4])
            name_conf = 0.55

    # Location: only from header-ish short lines
    location, loc_conf = _extract_location_from_header(head)

    return {
        "name": name,
        "location": location,
        "emails": emails,
        "phones": phones,
        "links": links,
        "confidence": {
            "name": name_conf,
            "location": loc_conf,
            "emails": 0.99 if emails else 0.0,
            "phones": 0.9 if phones else 0.0,
            "links": 0.9 if any([
                links.get("linkedin"),
                links.get("xing"),
                links.get("github"),
                links.get("gitlab"),
                links.get("personal"),
                links.get("other"),
            ]) else 0.0,
        },
    }



def slice_sections(plain_text: str) -> Dict[str, List[str]]:
    """
    Split text into coarse sections by headings.
    Returns mapping: section_key -> lines
    """
    lines = [ln.rstrip() for ln in plain_text.splitlines()]
    cur_key = "unknown"
    out: Dict[str, List[str]] = {"unknown": []}

    def norm_hdr(h: str) -> str:
        h = h.lower().strip()
        # unify a few
        if "skill" in h or "kennt" in h or "fäh" in h:
            return "skills"
        if "educ" in h or "ausbild" in h or "stud" in h or "bildung" in h:
            return "education"
        if "cert" in h or "zert" in h or "qualifik" in h:
            return "certifications"
        if "lang" in h or "sprach" in h:
            return "languages"
        if "experience" in h or "employment" in h or "beruf" in h or "werdegang" in h:
            return "experience"
        if "project" in h or "projekt" in h:
            return "projects"
        if "summary" in h or "profil" in h:
            return "profile"
        return h

    for ln in lines:
        m = SECTION_HDR_RE.match(ln.strip())
        if m:
            cur_key = norm_hdr(m.group("hdr"))
            out.setdefault(cur_key, [])
            continue
        out.setdefault(cur_key, []).append(ln)

    # trim empties
    for k in list(out.keys()):
        out[k] = [x.strip() for x in out[k] if x.strip()]
        if not out[k]:
            out.pop(k, None)
    return out


def extract_skills(sections: Dict[str, List[str]]) -> Dict[str, Any]:
    """
    Minimal version:
    - if there's a skills section, return raw lines + a small keyword-based list
    """
    lines = sections.get("skills", [])
    if not lines:
        return {"items": [], "raw": [], "confidence": 0.0}

    # turn comma-heavy lines into tokens; also scan for known skill words
    raw = lines[:200]  # cap
    text = " ".join(raw)

    # extract tokens from comma-separated lines (low risk)
    tokens = []
    for ln in raw:
        if ln.count(",") >= 2 or ln.count(";") >= 2:
            parts = re.split(r"\s*(?:,|;|\||/)\s*", ln)
            tokens.extend([p.strip() for p in parts if p.strip()])

    # also pick up a few from SKILLY_LINE_RE occurrences
    for m in SKILLY_LINE_RE.finditer(text):
        tokens.append(m.group(0))

    items = _dedupe_preserve([t for t in tokens if 2 <= len(t) <= 60])
    return {"items": items, "raw": raw, "confidence": 0.6 if items else 0.4}


def extract_education(sections: Dict[str, List[str]]) -> Dict[str, Any]:
    """
    Heuristic, high precision:
    keep only lines in education section that contain enough signals.
    """
    lines = sections.get("education", [])
    if not lines:
        return {"entries": [], "raw": [], "confidence": 0.0}

    raw = lines[:250]
    entries: List[Dict[str, Any]] = []

    # group by blank lines is already removed; we do "soft grouping" by year-range markers
    buf: List[str] = []

    def flush_buf():
        nonlocal buf
        if not buf:
            return
        block = " ".join(buf).strip()
        sigs = len(EDU_SIGNAL_RE.findall(block))
        if sigs >= 2:
            entries.append({"text": block})
        buf = []

    for ln in raw:
        if not ln.strip():
            flush_buf()
            continue
        buf.append(ln.strip())
        # if a line contains a year range, flush sooner
        if re.search(r"(19\d{2}|20\d{2})\s*(?:–|-|to)\s*(19\d{2}|20\d{2})", ln):
            flush_buf()

    flush_buf()
    conf = 0.6 if entries else 0.3
    return {"entries": entries, "raw": raw, "confidence": conf}


def extract_certifications(sections: Dict[str, List[str]]) -> Dict[str, Any]:
    lines = sections.get("certifications", [])
    if not lines:
        return {"items": [], "raw": [], "confidence": 0.0}

    raw = lines[:200]
    items = []
    for ln in raw:
        if CERT_SIGNAL_RE.search(ln):
            items.append(ln.strip())

    items = _dedupe_preserve(items)
    return {"items": items, "raw": raw, "confidence": 0.7 if items else 0.3}


# =============================================================================
# Chunker invocation (robust to naming differences)
# =============================================================================

def run_chunker(plain_text: str, *, asof: Optional[str] = None) -> Dict[str, Any]:
    """
    Calls your cv_timeline_chunker in a robust way.
    """
    asof_used = asof or default_asof()

    # Find a callable
    fn = None
    for name in CHUNKER_FUNCTION_CANDIDATES:
        fn = getattr(tchunk, name, None)
        if callable(fn):
            break

    if not callable(fn):
        return {
            "asof_used": asof_used,
            "count": 0,
            "chunks": [],
            "tech_months": {},
            "error": f"No known chunker function found. Tried: {CHUNKER_FUNCTION_CANDIDATES}",
        }

    # Try calling with (text, asof=...)
    try:
        out = fn(plain_text, asof=asof_used)
    except TypeError:
        # maybe signature is different
        out = fn(plain_text)

    # Normalize output shape
    if isinstance(out, list):
        # could be chunk list only
        return {"asof_used": asof_used, "count": len(out), "chunks": out, "tech_months": {}}

    if isinstance(out, dict):
        out.setdefault("asof_used", asof_used)
        out.setdefault("count", len(out.get("chunks", []) or []))
        out.setdefault("chunks", out.get("chunks", []) or [])
        out.setdefault("tech_months", out.get("tech_months", {}) or {})
        return out

    # unknown
    return {"asof_used": asof_used, "count": 0, "chunks": [], "tech_months": {}, "error": "Chunker returned unexpected type"}


# =============================================================================
# Final orchestrator
# =============================================================================

def process_cv(path: str, *, include_raw_text: bool = False, raw_text_max_chars: int = 30000, asof: Optional[str] = None) -> Dict[str, Any]:
    structured, diag = tex.extract_text_structured(path)
    plain = tex.extract_text_plain(structured)

    # Chunker + tech totals
    chunker_out = run_chunker(plain, asof=asof)

    # Derive years from tech_months
    tech_months = chunker_out.get("tech_months", {}) or {}
    tech_years = {k: round(v / 12.0, 2) for k, v in tech_months.items()}

    # Heuristic sections + profile
    sections = slice_sections(plain)
    core_profile = extract_core_profile(plain, filename=path)

    skills = extract_skills(sections)
    education = extract_education(sections)
    certifications = extract_certifications(sections)

    # Quality report
    quality = {
        "extractor_diagnostics": {
            "file_type": diag.file_type,
            "pages": diag.pages,
            "rejected_scanned_pdf": diag.rejected_scanned_pdf,
            "multi_column_pages": diag.multi_column_pages,
            "empty_text_pages": diag.empty_text_pages,
        },
        "pipeline_flags": {
            "has_timeline_chunks": bool(chunker_out.get("chunks")),
            "has_tech_months": bool(tech_months),
            "has_email": bool(core_profile.get("emails")),
            "has_phone": bool(core_profile.get("phones")),
            "has_links": bool(core_profile.get("links")),
            "has_skills_section": "skills" in sections,
            "has_education_section": "education" in sections,
            "has_certs_section": "certifications" in sections,
        },
        "confidence": {
            "core_profile": core_profile.get("confidence", {}),
            "skills": skills.get("confidence", 0.0),
            "education": education.get("confidence", 0.0),
            "certifications": certifications.get("confidence", 0.0),
        },
        "chunker_error": chunker_out.get("error"),
    }

    final: Dict[str, Any] = {
        "source_file": os.path.basename(path),
        "file_type": diag.file_type,
        "asof_used": chunker_out.get("asof_used", asof or default_asof()),
        "core_profile": core_profile,
        "timeline_chunks": chunker_out.get("chunks", []),
        "tech_months": tech_months,
        "tech_years": tech_years,
        "skills": skills,
        "education": education,
        "certifications": certifications,
        "quality_report": quality,
    }

    if include_raw_text:
        raw = plain
        if raw_text_max_chars and len(raw) > raw_text_max_chars:
            raw = raw[:raw_text_max_chars] + "\n... [TRIMMED]\n"
        final["raw_text"] = raw

    return final


# =============================================================================
# CLI quick test
# =============================================================================

if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 2:
        print("Usage: python cv_final_json.py /path/to/cv.pdf|cv.docx")
        raise SystemExit(2)

    p = sys.argv[1]
    out = process_cv(p, include_raw_text=False)
    print(json.dumps(out, ensure_ascii=False, indent=2))
