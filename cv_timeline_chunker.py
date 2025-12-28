"""
cv_timeline_chunker.py

Timeline chunker + tech list extractor for flattened CV text.

Applied fixes (GENERAL, not CV-specific):
✅ Anchor detection now works when dates/ranges appear *anywhere in a line* (e.g. "Project (2019 - 2024)")
✅ Supports YEAR-only anchors like "(2025)" and year ranges "2019 - 2024"
✅ Supports single-date anchors like "4/2006" by closing the chunk at (month before next anchor) instead of defaulting to asof
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from datetime import date
from typing import Dict, Iterable, List, Optional, Tuple


# ----------------------------
# Text normalization
# ----------------------------

_BULLETS = {"•", "‣", "∙", "◦", "·", "●", "▪", "–", "—", "−"}

_GARBAGE_EXACT = {"", "-", "•", "·", "▪", "–", "—", "−"}
_GARBAGE_ONLY_RE = re.compile(r"^\s*(?:[-•·▪–—−]+)\s*$")


def normalize_text(text: str) -> str:
    if not text:
        return ""

    t = text.replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"[\uf000-\uf8ff]", "", t)
    t = re.sub(r"(\w)-\n(\w)", r"\1\2", t)
    t = re.sub(r"(\w)-\s+(\w)", r"\1\2", t)

    lines: List[str] = []
    for line in t.split("\n"):
        s = line.strip()

        if s and s[0] in _BULLETS:
            s = "- " + s[1:].lstrip()

        s = re.sub(r"[ \t]+", " ", s).strip()
        lines.append(s)

    out = "\n".join(lines)
    out = re.sub(r"\n{4,}", "\n\n\n", out)
    return out.strip()


def _strip_noise(line: str) -> str:
    s = line.strip()
    s = re.sub(r"[\uf000-\uf8ff]", "", s)
    return s.strip()


def _is_garbage_line(s: str) -> bool:
    if s is None:
        return True
    t = s.strip()
    if t in _GARBAGE_EXACT:
        return True
    if _GARBAGE_ONLY_RE.match(t):
        return True
    if re.fullmatch(r"[-•·▪–—−\s]{1,10}", t):
        return True
    return False


# ----------------------------
# Date parsing
# ----------------------------

MONTHS: Dict[str, int] = {
    "januar": 1, "jan": 1,
    "februar": 2, "feb": 2,
    "märz": 3, "maerz": 3, "mrz": 3, "mar": 3,
    "april": 4, "apr": 4,
    "mai": 5, "may": 5,
    "juni": 6, "jun": 6,
    "juli": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "oktober": 10, "okt": 10, "oct": 10,
    "november": 11, "nov": 11,
    "dezember": 12, "dez": 12, "dec": 12,
    "january": 1,
    "february": 2,
    "march": 3,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

NUM_MMYYYY_RE = re.compile(r"^(0?[1-9]|1[0-2])[./](19\d{2}|20\d{2})$")
NUM_YYYYMM_RE = re.compile(r"^(19\d{2}|20\d{2})-(0?[1-9]|1[0-2])$")
YEAR_ONLY_RE = re.compile(r"^(19\d{2}|20\d{2})$")
MONTHNAME_RE = re.compile(r"^(?P<m>[A-Za-zÄÖÜäöüß]+)\s+(?P<y>19\d{2}|20\d{2})$")

RANGE_GLUE_RE = re.compile(r"\s*(?:–|—|-|to|bis|until)\s*", re.IGNORECASE)
SINCE_RE = re.compile(r"^(since|seit|ab)\s+", re.IGNORECASE)
PRESENT_RE = re.compile(r"\b(heute|aktuell|present|current|bis\s+heute)\b", re.IGNORECASE)


def parse_date_token(token: str) -> Optional[str]:
    t = token.strip().lower()
    t = t.replace(",", " ")
    t = re.sub(r"\s{2,}", " ", t).strip()
    if not t:
        return None

    m = NUM_MMYYYY_RE.match(t)
    if m:
        mm, yy = int(m.group(1)), int(m.group(2))
        return f"{yy:04d}-{mm:02d}"

    m = NUM_YYYYMM_RE.match(t)
    if m:
        yy, mm = int(m.group(1)), int(m.group(2))
        return f"{yy:04d}-{mm:02d}"

    m = MONTHNAME_RE.match(t)
    if m:
        name = m.group("m").lower()
        name = name.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
        mm = MONTHS.get(name)
        if mm:
            yy = int(m.group("y"))
            return f"{yy:04d}-{mm:02d}"

    m = YEAR_ONLY_RE.match(t)
    if m:
        yy = int(m.group(1))
        return f"{yy:04d}-01"

    return None


def parse_date_range(text: str, *, asof: Optional[str] = None) -> Optional[Tuple[str, str]]:
    s = text.strip()
    if not s:
        return None

    if SINCE_RE.match(s):
        s2 = SINCE_RE.sub("", s).strip()
        start = parse_date_token(s2)
        if start:
            return start, (asof or start)

    if PRESENT_RE.search(s):
        parts = RANGE_GLUE_RE.split(s, maxsplit=1)
        start = parse_date_token(parts[0].strip()) if parts else None
        if start:
            return start, (asof or start)

    parts = RANGE_GLUE_RE.split(s)
    if len(parts) >= 2:
        start = parse_date_token(parts[0].strip())
        end = parse_date_token(parts[1].strip())
        if start and end:
            return start, end

    return None


def parse_start_from_payload(payload: str) -> Optional[Tuple[str, bool]]:
    """
    Parse start date from a LEFT_COLUMN payload.
    Returns (start_ym, is_open_ended) if it looks like a start anchor, else None.
    Handles:
      - "07/2024 bis"
      - "April 2019 bis"
      - "2019-04 bis"
      - "seit 03/2020"
      - "... bis heute"
    """
    p = payload.strip()
    if not p:
        return None

    if SINCE_RE.match(p):
        maybe = SINCE_RE.sub("", p).strip()
        start = parse_date_token(maybe)
        if start:
            return start, True
        return None

    m = re.match(r"^(?P<left>.+?)\s+(?P<glue>bis|to|until|–|-|—)\s*(?P<tail>.*)$", p, re.IGNORECASE)
    if not m:
        return None

    left = m.group("left").strip()
    tail = (m.group("tail") or "").strip()
    start = parse_date_token(left)
    if not start:
        return None

    open_ended = bool(PRESENT_RE.search(tail))
    return start, open_ended


def looks_like_range_start_payload(payload: str) -> bool:
    p = payload.strip()
    if not p:
        return False
    if SINCE_RE.match(p):
        maybe = SINCE_RE.sub("", p).strip()
        return parse_date_token(maybe) is not None
    m = re.match(r"^(?P<left>.+?)\s+(bis|to|until|–|-|—)\b", p, re.IGNORECASE)
    if m:
        return parse_date_token(m.group("left").strip()) is not None
    return False


def month_diff_inclusive(start_ym: str, end_ym: str) -> int:
    sy, sm = start_ym.split("-")
    ey, em = end_ym.split("-")
    s = int(sy) * 12 + int(sm)
    e = int(ey) * 12 + int(em)
    return max(0, (e - s) + 1)


def default_asof() -> str:
    today = date.today()
    return f"{today.year:04d}-{today.month:02d}"


# ----------------------------
# NEW: anchor detection anywhere in line (projects like "... (2019 - 2024)")
# ----------------------------

INLINE_YEAR_RANGE_RE = re.compile(
    r"\b(19\d{2}|20\d{2})\s*(?:–|—|−|-|bis|to|until)\s*(19\d{2}|20\d{2})\b",
    re.IGNORECASE,
)
INLINE_MMYYYY_RANGE_RE = re.compile(
    r"\b(0?[1-9]|1[0-2])[./](19\d{2}|20\d{2})\s*(?:–|—|−|-|bis|to|until)\s*(0?[1-9]|1[0-2])[./](19\d{2}|20\d{2})\b",
    re.IGNORECASE,
)
PAREN_SINGLE_YEAR_RE = re.compile(r"\((19\d{2}|20\d{2})\)")
INLINE_SINGLE_MMYYYY_RE = re.compile(r"\b(0?[1-9]|1[0-2])[./](19\d{2}|20\d{2})\b")
INLINE_SINGLE_YYYY_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")


def find_date_anchor_anywhere(line: str, *, asof: Optional[str]) -> Optional[Tuple[str, str, bool, bool]]:
    """
    Returns (start_ym, end_ym, open_ended, end_missing)
    - end_missing=True means we only found a start anchor and must close using next anchor (or keep single-month).
    """
    s = line.strip()
    if not s:
        return None

    # 1) mm/yyyy range anywhere (incl within parentheses)
    m = INLINE_MMYYYY_RANGE_RE.search(s)
    if m:
        sm, sy, em, ey = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        return f"{sy:04d}-{sm:02d}", f"{ey:04d}-{em:02d}", False, False

    # 2) year range anywhere
    m = INLINE_YEAR_RANGE_RE.search(s)
    if m:
        sy, ey = int(m.group(1)), int(m.group(2))
        return f"{sy:04d}-01", f"{ey:04d}-12", False, False

    # 3) "(2025)" year-only
    m = PAREN_SINGLE_YEAR_RE.search(s)
    if m:
        y = int(m.group(1))
        return f"{y:04d}-01", f"{y:04d}-12", False, False

    # 4) single mm/yyyy token -> start-only
    m = INLINE_SINGLE_MMYYYY_RE.fullmatch(s) or INLINE_SINGLE_MMYYYY_RE.search(s)
    if m:
        mm, yy = int(m.group(1)), int(m.group(2))
        start = f"{yy:04d}-{mm:02d}"
        return start, start, False, True

    # 5) single yyyy token alone -> start-only
    if INLINE_SINGLE_YYYY_RE.fullmatch(s):
        yy = int(s)
        start = f"{yy:04d}-01"
        return start, start, False, True

    return None


# ----------------------------
# Tech extraction (unchanged)
# ----------------------------

TECH_PREFIX_RE = re.compile(
    r"^(?:eingesetzte\s+technologien|technologien|tech(?:nologies)?\s+stack|tech\s*stack|stack|tools)\s*:\s*",
    re.IGNORECASE,
)
TECH_BLOCK_START_RE = re.compile(
    r"^(eingesetzte\s+technologien|technologien|tech(?:nologies)?\s+stack|tech\s*stack|stack|tools)\s*:?\s*$",
    re.IGNORECASE,
)
TECH_BLOCK_STOP_RE = re.compile(
    r"^(?:-\s+|aufgabe|verantwort|tätig|entwicklung|implement|migration|role|responsibil|project|kunde|customer|\[left_column\]|\[right_column\]|\[header_area\]|---\s*page)",
    re.IGNORECASE,
)

TECH_SPLIT_RE = re.compile(r"\s*(?:,|;|\||/)\s*")
TECH_TRIM_RE = re.compile(r"^\s*(?:und|and)\s+|\s+$", re.IGNORECASE)
TECH_TOKEN_OK_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+.#()\-\s]{1,80}$")

FUZZY_TECH_MAPPING = {
    "js": "JavaScript",
    "ts": "TypeScript",
    "py": "Python",
    "k8s": "Kubernetes",
    "otel": "OpenTelemetry",
    "postgres": "PostgreSQL",
    "postgresql": "PostgreSQL",
    "mysql": "MySQL",
    "mariadb": "MariaDB",
}

INLINE_TECH_PATTERNS = [
    r"\bjava\b",
    r"\bpython\b",
    r"\bjavascript\b|\bjs\b",
    r"\btypescript\b|\bts\b",
    r"\bspring(?:\s+boot|\s+cloud|\s+batch)?\b",
    r"\bdocker\b",
    r"\bkubernetes\b|\bk8s\b",
    r"\baws\b|\bamazon web services\b",
    r"\bazure\b",
    r"\bterraform\b",
    r"\bpostgres(?:ql)?\b",
    r"\bsql\b",
    r"\bkafka\b",
    r"\brabbitmq\b",
]
INLINE_TECH_RE = re.compile("|".join(f"(?:{p})" for p in INLINE_TECH_PATTERNS), re.IGNORECASE)

TECHY_KEYWORDS_RE = re.compile(
    r"\b(java|spring|aws|azure|kubernetes|k8s|docker|terraform|python|typescript|javascript|postgres|sql|kafka|grafana|prometheus|keycloak|junit|maven|gradle|git|helm|wiremock|testcontainers|liquibase|flyway|datadog|splunk|vault|nginx|react|angular|node)\b",
    re.IGNORECASE,
)

TECH_TOKEN_REJECT_SUBSTRINGS = {
    "beruflicher", "werdegang", "bildung", "ausbildung", "studium",
    "kenntnisse", "fähigkeiten", "sprachen", "zertifikate", "profil",
    "experience", "education", "skills", "languages", "certifications", "profile", "summary",
}

TECH_HEADING_STRIP_RE = re.compile(
    r"\b(beruflicher\s+werdegang|bildung|ausbildung|studium|kenntnisse|fähigkeiten|"
    r"education|skills|languages|certifications|profile|summary|zusammenfassung|"
    r"weitere\s+kenntnisse\s+und\s+fähigkeiten)\b",
    re.IGNORECASE,
)


def _clean_tech_token(tok: str) -> str:
    t = tok.strip()
    t = TECH_TRIM_RE.sub("", t).strip()
    t = re.sub(r"\s{2,}", " ", t).strip()
    t = t.rstrip(".,:")
    return t


def normalize_tech_token(tok: str) -> str:
    t = _clean_tech_token(tok)
    if not t:
        return t
    key = re.sub(r"[^a-z0-9+.#-]", "", t.lower())
    mapped = FUZZY_TECH_MAPPING.get(key)
    return mapped if mapped else t


def _dedupe_preserve_order(items: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for x in items:
        key = x.lower()
        if key not in seen:
            seen.add(key)
            out.append(x)
    return out


def _parse_tech_block(block_text: str) -> List[str]:
    bt = block_text.strip()
    if not bt:
        return []

    bt = TECH_HEADING_STRIP_RE.sub(" ", bt)
    bt = re.sub(r"\s{2,}", " ", bt).strip()

    bt = re.sub(r"(\w)-\s+(\w)", r"\1\2", bt)
    bt = re.sub(r"\s{2,}", " ", bt)
    raw_tokens = TECH_SPLIT_RE.split(bt)

    tokens = []
    for raw in raw_tokens:
        ct = _clean_tech_token(raw)
        if not ct:
            continue
        ct = normalize_tech_token(ct)
        if not ct:
            continue
        low = ct.lower()
        if any(bad in low for bad in TECH_TOKEN_REJECT_SUBSTRINGS):
            continue
        tokens.append(ct)

    return tokens


def extract_inline_tech_mentions(lines: List[str]) -> List[str]:
    found: List[str] = []
    text = " ".join(lines)
    for m in INLINE_TECH_RE.finditer(text):
        found.append(normalize_tech_token(m.group(0)))
    found = [_clean_tech_token(x) for x in found if x]

    out: List[str] = []
    for x in found:
        low = x.lower()
        if any(bad in low for bad in TECH_TOKEN_REJECT_SUBSTRINGS):
            continue
        out.append(x)

    return _dedupe_preserve_order([x for x in out if x])


def _looks_like_comma_heavy_stack(line: str) -> bool:
    s = line.strip()
    if not s or s.startswith("-"):
        return False
    comma_count = s.count(",")
    if comma_count < 3:
        return False
    if not TECHY_KEYWORDS_RE.search(s):
        return False
    return True


def extract_technologies_from_lines(lines: List[str]) -> List[str]:
    collected: List[str] = []
    i = 0

    while i < len(lines):
        ln = lines[i].strip()
        if not ln:
            i += 1
            continue

        m = TECH_PREFIX_RE.match(ln)
        if m:
            remainder = ln[m.end():].strip()
            block_parts = [remainder] if remainder else []
            j = i + 1
            while j < len(lines):
                nxt = lines[j].strip()
                if not nxt:
                    break
                if TECH_BLOCK_STOP_RE.match(nxt):
                    break
                if looks_like_range_start_payload(nxt):
                    break
                block_parts.append(nxt)
                j += 1
            collected.extend(_parse_tech_block(" ".join(block_parts)))
            i = j
            continue

        if TECH_BLOCK_START_RE.match(ln):
            block_parts: List[str] = []
            j = i + 1
            while j < len(lines):
                nxt = lines[j].strip()
                if not nxt:
                    break
                if TECH_BLOCK_STOP_RE.match(nxt):
                    break
                if looks_like_range_start_payload(nxt):
                    break
                block_parts.append(nxt)
                j += 1
            collected.extend(_parse_tech_block(" ".join(block_parts)))
            i = j
            continue

        if _looks_like_comma_heavy_stack(ln):
            block_parts = [ln]
            j = i + 1
            while j < len(lines):
                nxt = lines[j].strip()
                if not nxt:
                    break
                if TECH_BLOCK_STOP_RE.match(nxt):
                    break
                if looks_like_range_start_payload(nxt):
                    break
                if _looks_like_comma_heavy_stack(nxt) or TECHY_KEYWORDS_RE.search(nxt):
                    block_parts.append(nxt)
                    j += 1
                    continue
                break
            collected.extend(_parse_tech_block(" ".join(block_parts)))
            i = j
            continue

        i += 1

    cleaned: List[str] = []
    for t in collected:
        ct = _clean_tech_token(t)
        if not ct:
            continue
        if not TECH_TOKEN_OK_RE.match(ct):
            continue
        low = ct.lower()
        if low in {"technologien", "eingesetzte technologien", "tools", "stack"}:
            continue
        if any(bad in low for bad in TECH_TOKEN_REJECT_SUBSTRINGS):
            continue
        cleaned.append(ct)

    cleaned = _dedupe_preserve_order(cleaned)

    if not cleaned:
        cleaned = extract_inline_tech_mentions(lines)

    return cleaned


# ----------------------------
# Timeline chunking
# ----------------------------

LEFT_COL_RE = re.compile(r"^\[LEFT_COLUMN\]\s*(.*)$")
RIGHT_COL_RE = re.compile(r"^\[RIGHT_COLUMN\]\s*(.*)$")
HEADER_RE = re.compile(r"^\[HEADER_AREA\]\s*(.*)$")
PAGE_RE = re.compile(r"^---\s*Page\s+\d+\s*---\s*$", re.IGNORECASE)

SECTION_BREAK_RE = re.compile(
    r"""
    ^
    (?:
        beruflicher\s+werdegang|
        bildung|ausbildung|studium|
        kenntnisse|fähigkeiten|skills?|
        zertifikate|certifications?|
        sprachen|languages?|
        profile|profil|summary|zusammenfassung|
        publications?|publikationen|
        interests?|interessen|
        awards?|auszeichnungen|
        weitere\s+kenntnisse
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

LOCATION_LIKE_RE = re.compile(r"^[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]{2,40}$")

DESC_HINT_RE = re.compile(
    r"\b(beratung|entwicklung|implement|migration|aufbau|konzeption|analyse|"
    r"development|implementation|migration|design|architecture|monitoring|testing|"
    r"automatisierung|einführung|weiterentwicklung|optimierung)\b",
    re.IGNORECASE,
)


def _looks_like_description(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if len(s) >= 60:
        return True
    if s.count(" ") >= 7:
        return True
    if DESC_HINT_RE.search(s):
        return True
    if re.match(r"^(Integration|Entwicklung|Implementierung|Migration|Aufbau|Konzeption|Optimierung|Einführung|Weiterentwicklung)\b", s):
        return True
    return False


def _looks_like_company(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if LOCATION_LIKE_RE.match(s):
        return False
    if re.search(r"\b(GmbH|AG|SE|KG|UG|Inc\.?|Ltd\.?|LLC|S\.?A\.?|S\.?r\.?l\.?)\b", s):
        return True
    if len(s) <= 50 and s.count(" ") <= 6 and not _looks_like_description(s):
        return True
    return False


@dataclass(frozen=True)
class TimelineChunk:
    start_date: str
    end_date: str
    months: int
    header_lines: List[str]
    body_lines: List[str]
    technologies: List[str]
    canonical_text: str
    source_pages: List[int]


def chunk_timeline_from_extracted_text(text: str, *, asof: Optional[str] = None) -> List[TimelineChunk]:
    norm = normalize_text(text)
    lines = [ln.rstrip("\n") for ln in norm.splitlines()]

    chunks: List[TimelineChunk] = []
    i = 0
    current_page = 0

    TECH_HEADER_STOP_RE = re.compile(r"^(eingesetzte\s+technologien|technologien|tech\s*stack|stack|tools)\b", re.IGNORECASE)

    def strip_any_tag(s: str) -> str:
        s2 = s.strip()
        s2 = LEFT_COL_RE.sub(r"\1", s2)
        s2 = HEADER_RE.sub(r"\1", s2)
        s2 = RIGHT_COL_RE.sub(r"\1", s2)
        return _strip_noise(s2).strip()

    def is_right_column_line(raw_line: str) -> bool:
        return bool(RIGHT_COL_RE.match(raw_line.strip()))

    def parse_anchor(candidate: str) -> Optional[Tuple[str, str, bool, bool]]:
        """
        Returns (start_ym, end_ym, open_ended, end_missing)
        """
        # NEW: anchors anywhere in line (e.g. "Project (2019 - 2024)")
        anywhere = find_date_anchor_anywhere(candidate, asof=asof)
        if anywhere:
            return anywhere

        # strict range lines
        rng = parse_date_range(candidate, asof=asof)
        if rng:
            start_ym, end_ym = rng
            open_ended = bool(PRESENT_RE.search(candidate)) or bool(SINCE_RE.match(candidate))
            return start_ym, end_ym, open_ended, False

        # start-payload "07/2024 bis ..." (end may be on following line)
        sp = parse_start_from_payload(candidate)
        if sp:
            start_ym, open_ended = sp
            if open_ended:
                return start_ym, (asof or start_ym), True, False
            # end missing -> try to read end from next date-ish line; else close via next anchor
            return start_ym, start_ym, False, True

        return None

    while i < len(lines):
        raw = lines[i].strip()

        if PAGE_RE.match(raw):
            current_page += 1
            i += 1
            continue

        if is_right_column_line(raw):
            i += 1
            continue

        candidate = strip_any_tag(raw)

        if SECTION_BREAK_RE.match(candidate):
            break

        anchor = parse_anchor(candidate)
        if not anchor:
            i += 1
            continue

        start_ym, end_ym, open_ended, end_missing = anchor

        # If end is missing (e.g. "07/2024 bis" or "4/2006"), try to read an explicit end on following line(s)
        j = i + 1
        if end_missing and not open_ended:
            while j < len(lines):
                raw_j = lines[j].strip()

                if PAGE_RE.match(raw_j):
                    j += 1
                    continue
                if is_right_column_line(raw_j):
                    j += 1
                    continue

                cand_j = strip_any_tag(raw_j)

                if SECTION_BREAK_RE.match(cand_j):
                    break

                # stop if next anchor starts (don’t consume it)
                if parse_anchor(cand_j):
                    break

                end_tok = parse_date_token(cand_j)
                if not end_tok:
                    end_tok = parse_date_token(re.sub(r"\s{2,}", " ", cand_j))

                if end_tok:
                    end_ym = end_tok
                    end_missing = False
                    j += 1
                    break

                if PRESENT_RE.search(cand_j):
                    end_ym = asof or start_ym
                    end_missing = False
                    j += 1
                    break

                j += 1

        # Collect content until next anchor / section break
        collected_lines: List[str] = []
        pages: List[int] = [current_page] if current_page else []

        k = j
        while k < len(lines):
            raw_k = lines[k].strip()

            if PAGE_RE.match(raw_k):
                current_page += 1
                if current_page not in pages:
                    pages.append(current_page)
                k += 1
                continue

            cand_k = strip_any_tag(raw_k)

            if SECTION_BREAK_RE.match(cand_k):
                break
            if parse_anchor(cand_k):
                break
            if is_right_column_line(raw_k):
                k += 1
                continue

            if not cand_k or _is_garbage_line(cand_k):
                k += 1
                continue

            if LOCATION_LIKE_RE.match(cand_k):
                k += 1
                continue

            collected_lines.append(cand_k)
            k += 1

        # NEW: if end is still missing, close chunk to month before next anchor start (if any)
        if end_missing and k < len(lines):
            next_cand = strip_any_tag(lines[k].strip())
            nxt = parse_anchor(next_cand)
            if nxt:
                next_start_ym = nxt[0]
                y, m = map(int, next_start_ym.split("-"))
                if m == 1:
                    end_ym = f"{y-1:04d}-12"
                else:
                    end_ym = f"{y:04d}-{m-1:02d}"
                end_missing = False

        # Still missing? Keep single-month (safer than exploding to asof)
        if end_missing:
            end_ym = start_ym
            end_missing = False

        # Header/body split
        header_lines: List[str] = []
        body_lines: List[str] = []

        for ln in collected_lines:
            if not ln or _is_garbage_line(ln):
                continue
            if LOCATION_LIKE_RE.match(ln):
                continue

            if ln.startswith("-") or ln.startswith("•"):
                cleaned = ln.lstrip("•").strip()
                if _is_garbage_line(cleaned):
                    continue
                body_lines.append(cleaned if cleaned.startswith("-") else "- " + cleaned)
                continue

            if TECH_HEADER_STOP_RE.match(ln):
                body_lines.append(ln)
                continue

            if not header_lines and _looks_like_company(ln) and not _looks_like_description(ln):
                header_lines.append(ln)
                continue

            if len(header_lines) == 1 and not body_lines:
                if not _looks_like_description(ln) and len(ln) <= 60 and ln.count(" ") <= 8:
                    header_lines.append(ln)
                    continue

            body_lines.append(ln)

        canonical_parts: List[str] = []
        canonical_parts.extend([x for x in header_lines if x and not _is_garbage_line(x)])
        canonical_parts.extend([x for x in body_lines if x and not _is_garbage_line(x)])
        canonical_text = "\n".join(canonical_parts).strip()

        techs = extract_technologies_from_lines(header_lines + body_lines)

        content_len = sum(len(x) for x in canonical_parts)
        if content_len >= 20:
            months = month_diff_inclusive(start_ym, end_ym)
            chunks.append(
                TimelineChunk(
                    start_date=start_ym,
                    end_date=end_ym,
                    months=months,
                    header_lines=header_lines,
                    body_lines=body_lines,
                    technologies=techs,
                    canonical_text=canonical_text,
                    source_pages=pages,
                )
            )

        i = k

        if i < len(lines):
            nxt = strip_any_tag(lines[i].strip())
            if SECTION_BREAK_RE.match(nxt):
                break

    return chunks


# ----------------------------
# Aggregation
# ----------------------------

def aggregate_tech_months(chunks: List[TimelineChunk]) -> Dict[str, int]:
    totals: Dict[str, int] = {}
    for ch in chunks:
        for tech in ch.technologies:
            totals[tech] = totals.get(tech, 0) + ch.months
    return dict(sorted(totals.items(), key=lambda kv: (-kv[1], kv[0].lower())))


# ----------------------------
# High-level helper (final output)
# ----------------------------

def extract_timeline_tech_and_totals(text: str, *, asof: Optional[str] = None) -> Dict[str, object]:
    asof_used = asof or default_asof()
    chunks = chunk_timeline_from_extracted_text(text, asof=asof_used)
    tech_months = aggregate_tech_months(chunks)

    return {
        "asof_used": asof_used,
        "count": len(chunks),
        "chunks": [asdict(c) for c in chunks],
        "tech_months": tech_months,
    }


# ----------------------------
# Manual quick test
# ----------------------------
if __name__ == "__main__":
    SAMPLE = r"""
--- Page 1 ---

SCOBEES - FULLSTACK ENTWICKLUNG (2025)
Scobees GmbH, Köln
Aufgabenbereich:
FullStack Software Entwicklung - Schwerpunkt Backend (NestJS)
Eingesetzte Technologien:
NodeJS 20, Typescript, NestJS 10, TypeORM / Postgres, Angular 20

SAMSON D41 - FULLSTACK ENTWICKLUNG (2024 - 2025)
Peak One GmbH, Hamburg
Team Mitglied in der Webentwicklung und Verantwortlichkeit für eine der zentralen API’s (Microservice).
Eingesetzte Technologien:
NodeJS 20, Typescript, Angular, AWS Cloud (ECS / Kubernetes), MongoDB / DocumentDB

4/2006
Technik Vorstand (CTO)
Netempire AG, Köln
Aufgabenbereich:
Beratung, technisches Projektmanagement und komplexe Software Entwicklung.

Bildung
Master of Science ...
"""
    result = extract_timeline_tech_and_totals(SAMPLE, asof="2025-12")
    for idx, ch in enumerate(result["chunks"], 1):
        print(f"\n== Chunk {idx} ==")
        print(ch["start_date"], "->", ch["end_date"], f"({ch['months']} months)")
        print("HEADER:", ch["header_lines"])
        print("BODY:", ch["body_lines"])
        print("TECH:", ch["technologies"])

    print("\n== Aggregated tech months ==")
    for tech, m in list(result["tech_months"].items())[:25]:
        print(f"{tech}: {m}")
