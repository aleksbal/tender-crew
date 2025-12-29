# cv_text_process.py
from __future__ import annotations

import copy
import re
from typing import Any, Dict, List


# =============================================================================
# Helpers: generic list utils
# =============================================================================

def _dedupe_preserve_order(items: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for x in items:
        k = (x or "").strip()
        if not k:
            continue
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


# =============================================================================
# Normalization: PDF/DOCX extracted raw text -> cleaner text (anchor-safe)
# =============================================================================

_PAGE_MARKER_RE = re.compile(r"^\s*---\s*Page\s+\d+\s*---\s*$", re.IGNORECASE)

# matches a line that is just "1" .. "999" (typical footer page counters)
_STANDALONE_PAGE_NUM_RE = re.compile(r"^\s*\d{1,3}\s*$")

# date-ish line starters you likely want to preserve as anchors
_DATE_ANCHOR_RE = re.compile(
    r"^\s*(\d{1,2}/\d{4}|\d{4}|\d{2}\.\d{2}\.\d{4}|\d{2}/\d{2}/\d{4})\b"
)

# lone bullet dash lines produced by PDF extraction
_LONE_DASH_RE = re.compile(r"^\s*[-–—−]\s*$")  # includes unicode dashes

# glue-to-email boundary: "...Engineeremail@" -> "...Engineer email@"
# \w is unicode-aware in Python's re
_GLUED_TO_EMAIL_RE = re.compile(r"(\w)([A-Za-z0-9._%+-]{1,32}@)")

# Collapse excessive blank lines (structural cleanup)
_MULTI_BLANK_LINES_RE = re.compile(r"\n{3,}")


def _is_page_marker(line: str) -> bool:
    return bool(_PAGE_MARKER_RE.match(line or ""))


def _looks_like_date_anchor(line: str) -> bool:
    return bool(_DATE_ANCHOR_RE.match(line or ""))


def _looks_like_heading(line: str) -> bool:
    """
    Conservative heading detector.
    - trailing ":" => heading
    - ALLCAPS-ish (unicode aware), if enough letters and most are uppercase
    """
    s = (line or "").strip()
    if not s:
        return False
    if s.endswith(":"):
        return True

    letters = [ch for ch in s if ch.isalpha()]
    if len(letters) >= 4:
        upp = sum(1 for ch in letters if ch.isupper())
        return (upp / len(letters)) >= 0.8
    return False


def _is_bullet_line(line: str) -> bool:
    return (line or "").lstrip().startswith("- ")


def _fix_glued_email(line: str) -> str:
    return _GLUED_TO_EMAIL_RE.sub(r"\1 \2", line or "")


def _fix_glued_case_boundaries(line: str) -> str:
    """
    Insert a space between a lower-case letter followed by an upper-case letter,
    using Unicode-aware checks (no hardcoded alphabets).
    Example: "GmbHDesigning" -> "GmbH Designing"
    """
    if not line:
        return line

    out_chars: List[str] = []
    prev = ""
    for ch in line:
        if prev and prev.isalpha() and ch.isalpha():
            if prev.islower() and ch.isupper():
                out_chars.append(" ")
        out_chars.append(ch)
        prev = ch
    return "".join(out_chars)


def _join_bullet_continuations(lines: List[str]) -> List[str]:
    """
    Join wrapped bullet continuation lines into the previous bullet line.

    Example:
      - Entwicklung ... für Frontend-,
        Backend-, Komponenten- und Integrationstests
    becomes:
      - Entwicklung ... für Frontend-, Backend-, Komponenten- und Integrationstests

    Conservative:
    - only if previous is a bullet and current is NOT a bullet
    - never across page markers
    - don’t join headings/date anchors
    - join only if strong continuation signal:
        * current starts with lowercase letter, OR
        * previous ends with comma or dash-like char
    """
    out: List[str] = []
    for cur in lines:
        cur_r = (cur or "").rstrip()

        if not out:
            out.append(cur_r)
            continue

        prev = out[-1]

        if not prev.strip() or not cur_r.strip():
            out.append(cur_r)
            continue

        if _is_page_marker(prev) or _is_page_marker(cur_r):
            out.append(cur_r)
            continue

        if _is_bullet_line(prev) and not _is_bullet_line(cur_r):
            if _looks_like_heading(cur_r) or _looks_like_date_anchor(cur_r):
                out.append(cur_r)
                continue

            cur_stripped = cur_r.lstrip()
            prev_end = prev.rstrip()[-1] if prev.rstrip() else ""

            starts_lower = cur_stripped[0].isalpha() and cur_stripped[0].islower()
            prev_ends_comma_or_dash = prev_end in {",", "-", "–", "—", "−"}

            if starts_lower or prev_ends_comma_or_dash:
                out[-1] = prev.rstrip() + " " + cur_stripped
                continue

        out.append(cur_r)

    return out


def normalize_cv_text(text: str) -> str:
    """
    Deterministic cleanup for PDF/DOCX extracted CV text.
    Goal: remove extraction artifacts WITHOUT changing meaning or destroying anchors.
    Unicode-safe (no ASCII/umlaut hardcoding).
    """
    if not text:
        return text

    t = text.replace("\r\n", "\n").replace("\r", "\n")

    # Normalize common bullet glyphs into stable bullet lines (conservative)
    t = (
        t.replace("●", "\n- ")
         .replace("•", "\n- ")
         .replace("▪", "\n- ")
         .replace("‣", "\n- ")
    )

    # Join hyphenated line breaks (safe). \w is Unicode-aware.
    t = re.sub(r"(\w)-\n(\w)", r"\1-\2", t)
    t = re.sub(
        r"(\w)-\n(?:---\s*Page\s+\d+\s*---\n)+(\w)",
        r"\1-\2",
        t,
        flags=re.IGNORECASE,
    )

    # Work line-by-line for anchor-safe ops
    lines = [ln.rstrip() for ln in t.split("\n")]

    # 0) Drop standalone page-number lines (1..999), but never drop page markers
    filtered: List[str] = []
    for ln in lines:
        if _is_page_marker(ln):
            filtered.append(ln)
            continue
        if _STANDALONE_PAGE_NUM_RE.match(ln or ""):
            filtered.append("")  # keep a blank to avoid gluing unrelated lines
            continue
        filtered.append(ln)
    lines = filtered

    # 1) Fix "lone dash" bullet lines by merging with next non-empty line
    out: List[str] = []
    i = 0
    while i < len(lines):
        cur = (lines[i] or "").rstrip()

        if _LONE_DASH_RE.match(cur):
            j = i + 1
            while j < len(lines) and (not (lines[j] or "").strip() or _is_page_marker(lines[j])):
                if _is_page_marker(lines[j]):
                    out.append(lines[j])
                j += 1
            if j < len(lines) and (lines[j] or "").strip():
                out.append("- " + (lines[j] or "").strip())
                i = j + 1
                continue
            i += 1
            continue

        out.append(cur)
        i += 1
    lines = out

    # 2) Conservative broken-word join (NO hyphen), Unicode-safe using isalpha checks
    def should_join_broken_word(a: str, b: str) -> bool:
        a = (a or "").rstrip()
        b = (b or "").lstrip()

        if not a or not b:
            return False
        if _is_page_marker(a) or _is_page_marker(b):
            return False
        if b.startswith("- "):  # don't glue into bullets
            return False
        if _looks_like_heading(a) or _looks_like_heading(b):
            return False
        if _looks_like_date_anchor(a) or _looks_like_date_anchor(b):
            return False
        if re.search(r"[.:;,)\]]\s*$", a):  # end-of-sentence/field
            return False

        # word fragments: last char of a alpha, first char of b alpha
        if not a[-1].isalpha():
            return False
        if not b[0].isalpha():
            return False

        # strong signals only
        if b[0].islower():
            return True

        last_token = (a.split()[-1] if a.split() else "")
        if len(last_token) <= 2:  # "g\neoinformations" style
            return True

        return False

    out = []
    i = 0
    while i < len(lines):
        cur = lines[i]
        if i + 1 < len(lines) and should_join_broken_word(cur, lines[i + 1]):
            lines[i + 1] = (cur or "").rstrip() + (lines[i + 1] or "").lstrip()
            i += 1
            continue
        out.append(cur)
        i += 1
    lines = out

    # 3) Join bullet continuation lines (fixes scattered bullets / wrapped bullets)
    lines = _join_bullet_continuations(lines)

    # 4) Conservative glued-token spacing (do NOT try to fix general compounding)
    spaced: List[str] = []
    for ln in lines:
        if not ln or _is_page_marker(ln):
            spaced.append(ln)
            continue
        s = ln
        s = _fix_glued_email(s)
        s = _fix_glued_case_boundaries(s)
        spaced.append(s)
    lines = spaced

    # 5) Collapse excessive blank lines and trailing whitespace
    t2 = "\n".join((l or "").rstrip() for l in lines)
    t2 = _MULTI_BLANK_LINES_RE.sub("\n\n", t2).strip() + "\n"
    return t2


# =============================================================================
# Postprocess LLM JSON output (generic hygiene; no CV-specific hardcoding)
# =============================================================================

_BULLET_SEPS = ["●", "•", "‣"]
_BULLET_GLYPHS_RE = re.compile(r"[•●▪‣◦∙·]")
_SPLIT_SEPS_REGEX = re.compile(r"(?:\s*[;•●]\s+)+")

# join word-broken line wraps INSIDE a block (very conservative: alpha + newline + alpha)
_INTRAWORD_BREAK_RE = re.compile(r"([^\W\d_])\n\s*([^\W\d_])", flags=re.UNICODE)

# collapse excessive blank lines inside blocks
_MULTI_BLANK_LINES_BLOCK_RE = re.compile(r"\n{3,}")

# split obvious stuck tokens like "PostgerSQL Ant" only when right side is a known single tool
_TRAILING_KNOWN_TOOL_RE = re.compile(
    r"^(?P<a>.+?)\s+(?P<b>Ant|Maven|Gradle|Jira|Jenkins|GitHub)$", re.IGNORECASE
)


def _normalize_text_block(s: str) -> str:
    if not s:
        return s
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    # join intra-word breaks inside blocks: "g\neoinfo" -> "geoinfo"
    s = _INTRAWORD_BREAK_RE.sub(r"\1\2", s)
    s = _MULTI_BLANK_LINES_BLOCK_RE.sub("\n\n", s)
    s = "\n".join(line.rstrip() for line in s.split("\n"))
    return s.strip()


def _split_flattened_bullets(text: str) -> str:
    """
    If PDF flattening turned bullets into a single line using separators like ';' or '●',
    split into proper '- ' bullet lines.
    """
    if not text:
        return text

    # If already has bullet lines, keep as-is
    if "\n-" in text or text.lstrip().startswith("- "):
        return text

    if any(sep in text for sep in _BULLET_SEPS) or ";" in text:
        parts = _SPLIT_SEPS_REGEX.split(text)
        parts = [p.strip() for p in parts if p and p.strip()]
        if len(parts) >= 2:
            if parts[0].endswith(":"):
                intro = parts[0]
                bullets = parts[1:]
                return intro + "\n" + "\n".join(f"- {b}" for b in bullets)
            return "\n".join(f"- {p}" for p in parts)

    return text


def _enforce_bullet_consistency(text: str) -> str:
    """
    If a block contains bullets, ensure list-like lines are bullets too,
    and avoid mixed plain lines in the middle of bullet lists.
    """
    if not text:
        return text

    lines = text.split("\n")
    has_bullets = any(l.lstrip().startswith("-") for l in lines)
    if not has_bullets:
        return text

    out: List[str] = []
    in_list_mode = False

    for raw in lines:
        line = raw.strip()
        if not line:
            out.append("")
            continue

        is_bullet = line.startswith("-")
        if is_bullet:
            in_list_mode = True
            bullet_body = line[1:].lstrip()
            out.append(f"- {bullet_body}" if bullet_body else "-")
            continue

        if line.endswith(":"):
            in_list_mode = False
            out.append(line)
            continue

        if in_list_mode:
            out.append(f"- {line}")
        else:
            out.append(line)

    normalized = "\n".join(out)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized).strip()
    return normalized


def _fix_description(desc: str, role: str, evidence: str, header_fallback: str) -> str:
    desc = _normalize_text_block(desc)
    desc = _split_flattened_bullets(desc)
    desc = _enforce_bullet_consistency(desc)

    if desc:
        return desc

    # fallback chain (generic)
    if role:
        return role
    if evidence:
        return evidence
    return header_fallback or ""


def _fix_technologies(techs: List[str]) -> List[str]:
    """
    Fix common technology list issues:
    - trim whitespace
    - split obvious stuck tokens like 'PostgerSQL Ant' when right part is known
    - dedupe preserving order
    """
    if not techs:
        return []

    out: List[str] = []
    for t in techs:
        t = (t or "").strip()
        if not t:
            continue

        m = _TRAILING_KNOWN_TOOL_RE.match(t)
        if m:
            left = (m.group("a") or "").strip()
            right = (m.group("b") or "").strip()
            if left and "," not in left:
                out.append(left)
                out.append(right)
                continue

        out.append(t)

    return _dedupe_preserve_order(out)


def postprocess_cv_json(json_obj: Dict[str, Any]) -> Dict[str, Any]:
    """
    Postprocess a CV JSON produced by an LLM to fix common quality issues:
      - Description formatting: ensure non-empty, preserve/repair bullets, fix intra-word wraps.
      - Technologies arrays: split obvious stuck tokens + dedupe + whitespace cleanup.
      - Text blocks: collapse excessive blank lines and join intra-word line breaks.

    Returns a deep-copied, cleaned JSON object (does not modify input).
    """
    cv = copy.deepcopy(json_obj)

    cv["summary"] = _normalize_text_block(cv.get("summary", ""))

    exp_list = cv.get("experience", [])
    if isinstance(exp_list, list):
        for exp in exp_list:
            if not isinstance(exp, dict):
                continue

            role = (exp.get("role") or "").strip()
            company = (exp.get("company") or "").strip()
            evidence = (exp.get("evidence") or "").strip()
            header_fallback = " | ".join([x for x in [role, company] if x]) or evidence

            exp["description"] = _fix_description(
                exp.get("description", ""),
                role=role,
                evidence=evidence,
                header_fallback=header_fallback,
            )

            techs = exp.get("technologies", [])
            if isinstance(techs, list):
                exp["technologies"] = _fix_technologies([str(x) for x in techs])
            else:
                exp["technologies"] = []

    skills = cv.get("skills")
    if isinstance(skills, dict):
        for k in ["programming_languages", "technologies", "soft_skills"]:
            arr = skills.get(k)
            if isinstance(arr, list):
                skills[k] = _dedupe_preserve_order([str(x) for x in arr])
            elif arr is None:
                skills[k] = []
        cv["skills"] = skills

    edu_list = cv.get("education", [])
    if isinstance(edu_list, list):
        for edu in edu_list:
            if not isinstance(edu, dict):
                continue
            for k in ["degree", "institution", "location"]:
                edu[k] = _normalize_text_block(edu.get(k, ""))
            for k in ["start_date", "end_date"]:
                edu[k] = (edu.get(k) or "").strip()

    certs = cv.get("certifications", [])
    if isinstance(certs, list):
        for c in certs:
            if not isinstance(c, dict):
                continue
            c["title"] = _normalize_text_block(c.get("title", ""))
            if "evidence" in c:
                c["evidence"] = _normalize_text_block(c.get("evidence", ""))

    langs = cv.get("languages", [])
    if isinstance(langs, list):
        for l in langs:
            if not isinstance(l, dict):
                continue
            l["language"] = _normalize_text_block(l.get("language", ""))
            l["proficiency"] = _normalize_text_block(l.get("proficiency", ""))

    return cv

