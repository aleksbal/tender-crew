"""
cv_timeline_chunker.py

Single-file, production-oriented timeline chunker + tech list extractor for “flattened table” CV text like:

[LEFT_COLUMN] 07/2024 bis
...
[LEFT_COLUMN] 02/2025
...
Eingesetzte Technologien: Java 21, Python 3.10, ...

Includes:
- Robust date parsing:
  - 04/2019, 04.2019, 2019-04
  - April 2019, Apr 2019, März 2020 (DE+EN month names)
  - ranges like "April 2019 - Juni 2022"
  - open-ended like "seit April 2019", "bis heute/present"
- Timeline chunking for flattened multi-column dumps:
  - Works with tagged lines ([LEFT_COLUMN], [HEADER_AREA], [RIGHT_COLUMN])
  - Also works with tagless lines (plain text)
  - End date often appears in the next date-ish line
  - Keeps [HEADER_AREA] payload as BODY content (important for spillover bullets/tech across pages)
  - Ignores [RIGHT_COLUMN] entirely (sidebars/footers)
- Tech extraction:
  - Explicit blocks: "Eingesetzte Technologien: ..."
  - Wrapped blocks (prefix alone + following lines)
  - Prefix-less comma-heavy stacks
  - Inline fallback from free text
- Output:
  - Each chunk includes `months` duration
  - Aggregated "tech -> total months"

No external dependencies (stdlib only).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from datetime import date
from typing import Dict, Iterable, List, Optional, Tuple


# ----------------------------
# Text normalization
# ----------------------------

_BULLETS = {"•", "‣", "∙", "◦", "·", "●", "▪", "–", "—", "−", "•"}


def normalize_text(text: str) -> str:
    """
    Normalize extracted CV text (PDF/DOCX -> text):
    - normalize newlines
    - remove private use area glyphs (common for icon fonts)
    - de-hyphenate line breaks: "micro-\\nservices" -> "microservices"
    - fix within-line broken hyphenations: "Com- pose" -> "Compose"
    - normalize bullets to "- "
    - collapse whitespace while preserving newlines
    - cap huge blank runs
    """
    if not text:
        return ""

    t = text.replace("\r\n", "\n").replace("\r", "\n")

    # Remove private use area glyphs (icons)
    t = re.sub(r"[\uf000-\uf8ff]", "", t)

    # De-hyphenate across line breaks: "micro-\nservices" -> "microservices"
    t = re.sub(r"(\w)-\n(\w)", r"\1\2", t)

    # Fix within-line broken hyphenations: "Com- pose" -> "Compose"
    t = re.sub(r"(\w)-\s+(\w)", r"\1\2", t)

    lines: List[str] = []
    for line in t.split("\n"):
        s = line.strip()

        # Normalize leading bullets
        if s and s[0] in _BULLETS:
            s = "- " + s[1:].lstrip()

        # Collapse internal whitespace
        s = re.sub(r"[ \t]+", " ", s).strip()
        lines.append(s)

    out = "\n".join(lines)
    out = re.sub(r"\n{4,}", "\n\n\n", out)
    return out.strip()


def _strip_noise(line: str) -> str:
    s = line.strip()
    s = re.sub(r"[\uf000-\uf8ff]", "", s)
    return s.strip()


# ----------------------------
# Date parsing (numeric + month names)
# ----------------------------

MONTHS: Dict[str, int] = {
    # German
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
    # English full names
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

NUM_MMYYYY_RE = re.compile(r"^(0?[1-9]|1[0-2])[./](19\d{2}|20\d{2})$")  # 04/2019 or 04.2019
NUM_YYYYMM_RE = re.compile(r"^(19\d{2}|20\d{2})-(0?[1-9]|1[0-2])$")    # 2019-04
YEAR_ONLY_RE = re.compile(r"^(19\d{2}|20\d{2})$")                      # 2019
MONTHNAME_RE = re.compile(r"^(?P<m>[A-Za-zÄÖÜäöüß]+)\s+(?P<y>19\d{2}|20\d{2})$")

RANGE_GLUE_RE = re.compile(r"\s*(?:–|—|-|to|bis|until)\s*", re.IGNORECASE)
SINCE_RE = re.compile(r"^(since|seit|ab)\s+", re.IGNORECASE)
PRESENT_RE = re.compile(r"\b(heute|aktuell|present|current|bis\s+heute)\b", re.IGNORECASE)


def parse_date_token(token: str) -> Optional[str]:
    """
    Convert a date token to YYYY-MM.
    Supports:
      - 04/2019, 04.2019
      - 2019-04
      - April 2019, Apr 2019, März 2020, Mrz 2020
      - 2019 (falls back to 2019-01)
    """
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
    """
    Parse a date range from a string:
      - "April 2019 - Juni 2022"
      - "04/2019 bis 06/2022"
      - "2019-04 – 2022-06"
      - "seit April 2019"
      - "... bis heute/present"
    Returns (start_ym, end_ym) as YYYY-MM.
    """
    s = text.strip()
    if not s:
        return None

    # since/seit/ab -> open-ended
    if SINCE_RE.match(s):
        s2 = SINCE_RE.sub("", s).strip()
        start = parse_date_token(s2)
        if start:
            return start, (asof or start)

    # "X ... bis heute/present"
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
    """
    Inclusive month duration between YYYY-MM and YYYY-MM.
    2024-07..2025-02 => 8 months.
    """
    sy, sm = start_ym.split("-")
    ey, em = end_ym.split("-")
    s = int(sy) * 12 + int(sm)
    e = int(ey) * 12 + int(em)
    return max(0, (e - s) + 1)


def default_asof() -> str:
    """
    Resolve 'bis heute/present' as current month at extraction time.
    """
    today = date.today()
    return f"{today.year:04d}-{today.month:02d}"


# ----------------------------
# Tech extraction + normalization
# ----------------------------

TECH_PREFIX_RE = re.compile(
    r"""
    ^
    (?:
      eingesetzte\s+technologien|technologien|tech(?:nologies)?\s+stack|tech\s*stack|stack|tools
    )
    \s*:\s*
    """,
    re.IGNORECASE | re.VERBOSE,
)

TECH_BLOCK_START_RE = re.compile(
    r"^(eingesetzte\s+technologien|technologien|tech(?:nologies)?\s+stack|tech\s*stack|stack|tools)\s*:?\s*$",
    re.IGNORECASE,
)

TECH_BLOCK_STOP_RE = re.compile(
    r"""
    ^
    (?:
      -\s+|                    # bullet starts -> likely description resumes
      aufgabe|verantwort|tätig|entwicklung|implement|migration|
      role|responsibil|project|kunde|customer|
      \[left_column\]|\[right_column\]|\[header_area\]|---\s*page
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

TECH_SPLIT_RE = re.compile(r"\s*(?:,|;|\||/)\s*")
TECH_TRIM_RE = re.compile(r"^\s*(?:und|and)\s+|\s+$", re.IGNORECASE)
TECH_TOKEN_OK_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+.#()\-\s]{1,80}$")

FUZZY_TECH_MAPPING: Dict[str, str] = {
    "js": "JavaScript",
    "javascript": "JavaScript",
    "ts": "TypeScript",
    "typescript": "TypeScript",
    "py": "Python",
    "python": "Python",
    "java": "Java",
    "k8s": "Kubernetes",
    "kubernetes": "Kubernetes",
    "postgres": "PostgreSQL",
    "postgresql": "PostgreSQL",
    "pg": "PostgreSQL",
    "tf": "Terraform",
    "terraform": "Terraform",
    "springboot": "Spring Boot",
    "springboot3": "Spring Boot",
    "springboot34": "Spring Boot",
    "springboot32": "Spring Boot",
    "springboot31": "Spring Boot",
    "springboot30": "Spring Boot",
    "spring boot": "Spring Boot",
    "sb": "Spring Boot",
    "node": "Node.js",
    "nodejs": "Node.js",
    "node.js": "Node.js",
    "otel": "OpenTelemetry",
}

INLINE_TECH_PATTERNS = [
    r"\bjava\b",
    r"\bspring(?:\s+boot|\s+cloud|\s+batch)?\b",
    r"\bkafka\b",
    r"\baws\b|\bamazon web services\b",
    r"\bazure\b|\bazure devops\b",
    r"\bkubernetes\b|\bk8s\b",
    r"\bdocker\b",
    r"\bterraform\b",
    r"\bpython\b",
    r"\btypescript\b|\bts\b",
    r"\bjavascript\b|\bjs\b",
    r"\bpostgres(?:ql)?\b|\bpostgre\s*sql\b",
    r"\bgrafana\b",
    r"\bprometheus\b",
    r"\bkeycloak\b",
    r"\bjunit\b",
    r"\btestcontainers\b",
    r"\bmaven\b",
    r"\bgradle\b",
    r"\bhelm\b",
    r"\bargocd\b",
    r"\bopentelemetry\b|\botel\b",
    r"\bwiremock\b",
    r"\bliquibase\b",
    r"\bflyway\b",
    r"\bdatadog\b",
    r"\bsplunk\b",
    r"\bvault\b",
    r"\bnginx\b",
    r"\breact\b",
    r"\bangular\b",
    r"\bnode(?:\.js)?\b",
]
INLINE_TECH_RE = re.compile("|".join(f"(?:{p})" for p in INLINE_TECH_PATTERNS), re.IGNORECASE)

LOCATION_LINE_RE = re.compile(r"^[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]{2,40}$")

TECHY_KEYWORDS_RE = re.compile(
    r"\b(java|spring|aws|azure|kubernetes|k8s|docker|terraform|python|typescript|javascript|postgres|sql|kafka|grafana|prometheus|keycloak|junit|maven|gradle|git|helm|argocd|wiremock|testcontainers|liquibase|flyway|datadog|splunk|vault|nginx|react|angular|node)\b",
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
    bt = re.sub(r"(\w)-\s+(\w)", r"\1\2", bt)
    bt = re.sub(r"\s{2,}", " ", bt)
    raw_tokens = TECH_SPLIT_RE.split(bt)
    tokens = [_clean_tech_token(t) for t in raw_tokens]
    tokens = [t for t in tokens if t]
    tokens = [normalize_tech_token(t) for t in tokens if t]
    return tokens


def extract_inline_tech_mentions(lines: List[str]) -> List[str]:
    found: List[str] = []
    text = " ".join(lines)
    for m in INLINE_TECH_RE.finditer(text):
        found.append(normalize_tech_token(m.group(0)))
    found = [_clean_tech_token(x) for x in found if x]
    return _dedupe_preserve_order([x for x in found if x])


def _looks_like_comma_heavy_stack(line: str) -> bool:
    s = line.strip()
    if not s or s.startswith("-"):
        return False
    if LOCATION_LINE_RE.match(s):
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

        # 1) "Eingesetzte Technologien: <stuff>"
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
                if LOCATION_LINE_RE.match(nxt):
                    break
                block_parts.append(nxt)
                j += 1
            collected.extend(_parse_tech_block(" ".join(block_parts)))
            i = j
            continue

        # 2) "Eingesetzte Technologien:" alone + wrapped
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
                if LOCATION_LINE_RE.match(nxt):
                    break
                block_parts.append(nxt)
                j += 1
            collected.extend(_parse_tech_block(" ".join(block_parts)))
            i = j
            continue

        # 3) Prefix-less comma-heavy stacks (possibly multiple consecutive lines)
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
                if LOCATION_LINE_RE.match(nxt):
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
        if ct.lower() in {"technologien", "eingesetzte technologien", "tools", "stack"}:
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
    r"^(skills|kenntnisse|education|ausbildung|certifications|zertifikate|languages|sprachen)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TimelineChunk:
    start_date: str
    end_date: str
    months: int
    header_lines: List[str]
    body_lines: List[str]
    technologies: List[str]
    source_pages: List[int]


def chunk_timeline_from_extracted_text(text: str, *, asof: Optional[str] = None) -> List[TimelineChunk]:
    """
    Chunker that works with:
      - tagged lines: [LEFT_COLUMN] 07/2024 bis ...
      - tagless lines: 07/2024 bis ...
    Also:
      - keeps [HEADER_AREA] payload as body (NOT skipped)
      - ignores RIGHT_COLUMN entirely
    """
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

    def parse_anchor(candidate: str) -> Optional[Tuple[str, str, bool]]:
        """
        Returns (start_ym, end_ym, open_ended)
        """
        sp = parse_start_from_payload(candidate)
        if sp:
            start_ym, open_ended = sp
            end_ym = asof or start_ym
            return start_ym, end_ym, open_ended

        rng = parse_date_range(candidate, asof=asof)
        if rng:
            start_ym, end_ym = rng
            return start_ym, end_ym, False

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

        anchor = parse_anchor(candidate)
        if not anchor:
            i += 1
            continue

        start_ym, end_ym, open_ended = anchor

        # If this was "07/2024 bis" (no inline end), try to pick up end date in the next date-ish line
        j = i + 1
        if not open_ended and parse_date_range(candidate, asof=asof) is None:
            while j < len(lines):
                raw_j = lines[j].strip()

                if PAGE_RE.match(raw_j):
                    j += 1
                    continue
                if is_right_column_line(raw_j):
                    j += 1
                    continue

                cand_j = strip_any_tag(raw_j)

                # stop if next anchor starts
                if parse_start_from_payload(cand_j):
                    break

                end_tok = parse_date_token(cand_j)
                if not end_tok:
                    # try compacting spaces (e.g., "Jun 2022")
                    end_tok = parse_date_token(re.sub(r"\s{2,}", " ", cand_j))

                if end_tok:
                    end_ym = end_tok
                    j += 1
                    break

                if PRESENT_RE.search(cand_j):
                    end_ym = asof or start_ym
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

            if SECTION_BREAK_RE.match(raw_k):
                break

            cand_k = strip_any_tag(raw_k)
            if parse_anchor(cand_k):
                break

            if is_right_column_line(raw_k):
                k += 1
                continue

            if cand_k:
                collected_lines.append(cand_k)

            k += 1

        # Split header/body
        header_lines: List[str] = []
        body_lines: List[str] = []

        for ln in collected_lines:
            if not ln:
                continue
            if ln.startswith("-") or ln.startswith("•"):
                body_lines.append("- " + ln.lstrip("•").strip() if ln.startswith("•") else ln)
                continue
            if TECH_HEADER_STOP_RE.match(ln):
                body_lines.append(ln)
                continue
            if not body_lines and len(header_lines) < 4:
                header_lines.append(ln)
            else:
                body_lines.append(ln)

        techs = extract_technologies_from_lines(header_lines + body_lines)

        content_len = sum(len(x) for x in header_lines) + sum(len(x) for x in body_lines)
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
                    source_pages=pages,
                )
            )

        i = k

    return chunks


# ----------------------------
# Aggregation: tech -> total months
# ----------------------------

def aggregate_tech_months(chunks: List[TimelineChunk]) -> Dict[str, int]:
    """
    Simple, pragmatic rule:
    If a technology appears in a chunk, count the full chunk duration for that technology.
    """
    totals: Dict[str, int] = {}
    for ch in chunks:
        for tech in ch.technologies:
            totals[tech] = totals.get(tech, 0) + ch.months
    return dict(sorted(totals.items(), key=lambda kv: (-kv[1], kv[0].lower())))


# ----------------------------
# High-level helper (final output)
# ----------------------------

def extract_timeline_tech_and_totals(text: str, *, asof: Optional[str] = None) -> Dict[str, object]:
    """
    Final, pragmatic output:
      - chunks[] each includes months + extracted tech list
      - tech_months: aggregated totals across chunks
      - asof_used: actual YYYY-MM used for open-ended ranges
    """
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

[HEADER_AREA] [LEFT_COLUMN] 
1986 (39) · Mainz

[LEFT_COLUMN] 07/2024 bis

Senior Fullstack Software Developer

[LEFT_COLUMN] 02/2025

50Hertz Transmission GmbH

[LEFT_COLUMN] Hamburg

•
Integration von Microservices auf Basis von Java

Eingesetzte Technologien: Java 21, Python 3.10, TypeScript, JavaScript, Spring Boot 3.4, Kubernetes, Docker

--- Page 2 ---

[LEFT_COLUMN] 04/2024 bis
Site Reliability Engineer
[LEFT_COLUMN] 06/2024
Adobe Systems Engineering GmbH
[HEADER_AREA] • Implementierung von Cloud-Infrastruktur mit Amazon Web Services (AWS)
[HEADER_AREA] • Realisierung von Infrastructure-as-Code (IaC) mit Terraform
Eingesetzte Technologien: AWS, Terraform, Prometheus, Grafana
"""

    result = extract_timeline_tech_and_totals(SAMPLE, asof="2025-12")

    for idx, ch in enumerate(result["chunks"], 1):
        print(f"\n== Chunk {idx} ==")
        print(ch["start_date"], "->", ch["end_date"], f"({ch['months']} months)")
        print("HEADER:", ch["header_lines"])
        print("TECH:", ch["technologies"])
        print("BODY (first lines):", ch["body_lines"][:8])

    print("\n== Aggregated tech months (top 25) ==")
    for tech, m in list(result["tech_months"].items())[:25]:
        print(f"{tech}: {m}")
