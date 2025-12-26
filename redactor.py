import re
from dataclasses import dataclass, asdict
from typing import List, Tuple, Dict, Any


EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# Fairly permissive phone regex (EU/DE-ish). You can tighten later.
PHONE_RE = re.compile(
    r"""
    (?<!\w)
    (?=(?:.*\d){7,})                        # require at least 7 digits total
    (?:\+?\d{1,3}[\s\-\.]?)?             # country code
    (?:\(?0?\d{2,5}\)?[\s\-\.]?)        # area code
    (?:\d[\d\s\-\.]{4,}\d)              # number body (min length)
    (?!\w)
    """,
    re.VERBOSE,
)

# German-ish address hints (street suffixes + house number)
STREET_RE = re.compile(
    r"\b([A-ZÄÖÜ][\wÄÖÜäöüß.\- ]{2,})(straße|str\.|weg|platz|allee|gasse|ring|ufer|damm)\s+\d+\w?\b",
    re.IGNORECASE,
)

POSTCODE_CITY_RE = re.compile(r"\b\d{5}\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\- ]+\b")

ADDRESS_LABEL_RE = re.compile(r"^\s*(adresse|anschrift|address)\s*[:\-]", re.IGNORECASE)

SECTION_HEADER_RE = re.compile(
    r"^\s*(experience|work history|employment|berufserfahrung|projects|projekte|skills|fähigkeiten|education|ausbildung|profil|summary|zusammenfassung)\s*$",
    re.IGNORECASE,
)

NAME_LIKE_RE = re.compile(r"^[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß\-']+(?:\s+[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß\-']+){1,3}$")


@dataclass
class Redaction:
    kind: str
    original: str
    replacement: str
    line_index: int


def _looks_like_name(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if "@" in s or any(ch.isdigit() for ch in s):
        return False
    lower = s.lower()
    if any(k in lower for k in ["lebenslauf", "cv", "resume", "curriculum vitae"]):
        return False
    # Avoid lines that are likely titles
    if any(k in lower for k in ["engineer", "entwickler", "developer", "architect", "berater", "consultant"]):
        return False
    return bool(NAME_LIKE_RE.match(s))


def redact_cv_text(text: str, max_header_lines: int = 20) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Redacts name (header heuristic), address (header heuristics), phone+email (global).
    Preserves line breaks for better downstream JSON extraction.
    """
    lines = text.splitlines()

    # Determine header region: up to first section header or max_header_lines (counting non-empty)
    header_end = 0
    non_empty = 0
    for i, line in enumerate(lines):
        if SECTION_HEADER_RE.match(line.strip()):
            header_end = i
            break
        if line.strip():
            non_empty += 1
        if non_empty >= max_header_lines:
            header_end = i + 1
            break
    if header_end == 0:
        header_end = min(len(lines), max_header_lines)

    redactions: List[Redaction] = []

    def replace_in_line(i: int, pattern: re.Pattern, repl: str, kind: str, only_first: bool = False):
        original = lines[i]
        if not pattern.search(original):
            return
        if only_first:
            m = pattern.search(original)
            if m:
                before = original
                lines[i] = original[:m.start()] + repl + original[m.end():]
                redactions.append(Redaction(kind, before[m.start():m.end()], repl, i))
        else:
            matches = list(pattern.finditer(original))
            if not matches:
                return
            before = original
            lines[i] = pattern.sub(repl, original)
            for m in matches:
                redactions.append(Redaction(kind, m.group(0), repl, i))

    # 1) Global email + phone
    for i in range(len(lines)):
        replace_in_line(i, EMAIL_RE, "[REDACTED_EMAIL]", "email")
        replace_in_line(i, PHONE_RE, "[REDACTED_PHONE]", "phone")

    # 2) Header: name heuristic (first non-empty line, optionally second)
    header_indices = [i for i in range(header_end) if lines[i].strip()]
    if header_indices:
        i0 = header_indices[0]
        if _looks_like_name(lines[i0]):
            orig = lines[i0]
            lines[i0] = "[REDACTED_NAME]"
            redactions.append(Redaction("name", orig, "[REDACTED_NAME]", i0))

        if len(header_indices) > 1:
            i1 = header_indices[1]
            # redact second line if also looks like name (some people write Firstname + Lastname on two lines or include middle name)
            if _looks_like_name(lines[i1]):
                orig = lines[i1]
                lines[i1] = "[REDACTED_NAME]"
                redactions.append(Redaction("name", orig, "[REDACTED_NAME]", i1))

    # 3) Header: address heuristics
    for i in range(header_end):
        line = lines[i].strip()
        if not line:
            continue

        if ADDRESS_LABEL_RE.match(line):
            orig = lines[i]
            lines[i] = "[REDACTED_ADDRESS]"
            redactions.append(Redaction("address", orig, "[REDACTED_ADDRESS]", i))
            continue

        # Street + number
        if STREET_RE.search(line):
            orig = lines[i]
            lines[i] = STREET_RE.sub("[REDACTED_ADDRESS]", lines[i])
            redactions.append(Redaction("address", orig, lines[i], i))
            continue

        # Postcode + city
        if POSTCODE_CITY_RE.search(line):
            orig = lines[i]
            lines[i] = POSTCODE_CITY_RE.sub("[REDACTED_ADDRESS]", lines[i])
            redactions.append(Redaction("address", orig, lines[i], i))
            continue

    redacted_text = "\n".join(lines)
    return redacted_text, [asdict(r) for r in redactions]


def redact_structured(structured: Dict, max_header_lines: int = 20) -> Tuple[Dict, List[Dict[str, Any]]]:
    """
    Redact structured output produced by `extract_text_structured`.

    Modifies and returns the structured dict (pages->blocks->lines) with
    line texts replaced in-place and returns a list of redaction records.
    Each redaction record includes page/block/line indices and original spans
    when available.
    """
    redactions: List[Dict[str, Any]] = []

    pages = structured.get("pages", [])

    # 0) Compute original absolute char spans for every line (before redaction)
    for pi, page in enumerate(pages):
        page_text_parts: List[str] = []
        for bi, block in enumerate(page.get("blocks", [])):
            line_texts = [ln.get("text", "") for ln in block.get("lines", [])]
            block_text = "\n".join(line_texts)
            block["text"] = block_text
            page_text_parts.append(block_text)

        page_text = "\n\n".join(page_text_parts)
        page["page_text"] = page_text

        # set absolute char spans for lines (original positions)
        offset = 0
        for bi, block in enumerate(page.get("blocks", [])):
            for li, ln in enumerate(block.get("lines", [])):
                txt = ln.get("text", "")
                ln_start = offset
                ln_end = ln_start + len(txt)
                ln["char_start"] = ln_start
                ln["char_end"] = ln_end
                offset = ln_end + 1  # newline within block
            offset += 1  # blank line between blocks

    # 1) Apply regex redactions line-by-line, recording original absolute spans
    def _apply_line_redactions(pi: int, bi: int, li: int, ln: Dict[str, Any]):
        original = ln.get("text", "")
        if not original:
            return

        # helper to apply pattern and record matches against original
        def apply_pattern(pattern: re.Pattern, repl: str, kind: str):
            nonlocal original
            matches = list(pattern.finditer(original))
            if not matches:
                return

            # If matching phones, filter out common year-range patterns like '2020-2024'
            if kind == "phone":
                matches = [m for m in matches if not re.search(r"\d{4}-\d{4}", m.group(0))]
                if not matches:
                    return

            for m in matches:
                abs_start = None
                abs_end = None
                if ln.get("char_start") is not None:
                    abs_start = ln["char_start"] + m.start()
                    abs_end = ln["char_start"] + m.end()
                redactions.append({
                    "kind": kind,
                    "original": m.group(0),
                    "replacement": repl,
                    "page": pi,
                    "block": bi,
                    "line": li,
                    "char_start": abs_start,
                    "char_end": abs_end,
                })

        apply_pattern(EMAIL_RE, "[REDACTED_EMAIL]", "email")
        apply_pattern(PHONE_RE, "[REDACTED_PHONE]", "phone")
        apply_pattern(STREET_RE, "[REDACTED_ADDRESS]", "address")
        apply_pattern(POSTCODE_CITY_RE, "[REDACTED_ADDRESS]", "address")

        # Address label: redact the label substring if present
        m = ADDRESS_LABEL_RE.search(original)
        if m:
            abs_start = None
            abs_end = None
            if ln.get("char_start") is not None:
                abs_start = ln["char_start"] + m.start()
                abs_end = ln["char_start"] + m.end()
            redactions.append({
                "kind": "address",
                "original": original[m.start(): m.end()],
                "replacement": "[REDACTED_ADDRESS]",
                "page": pi,
                "block": bi,
                "line": li,
                "char_start": abs_start,
                "char_end": abs_end,
            })

        # Now produce the updated line text by applying substitutions on the original
        updated = EMAIL_RE.sub("[REDACTED_EMAIL]", original)

        # Replace phones only when they are not year ranges like '2020-2024'
        def _phone_repl(m: re.Match) -> str:
            s = m.group(0)
            if re.search(r"\d{4}-\d{4}", s):
                return s
            return "[REDACTED_PHONE]"

        updated = PHONE_RE.sub(_phone_repl, updated)
        updated = STREET_RE.sub("[REDACTED_ADDRESS]", updated)
        updated = POSTCODE_CITY_RE.sub("[REDACTED_ADDRESS]", updated)
        updated = ADDRESS_LABEL_RE.sub("[REDACTED_ADDRESS]", updated)

        ln["text"] = updated

    for pi, page in enumerate(pages):
        for bi, block in enumerate(page.get("blocks", [])):
            for li, ln in enumerate(block.get("lines", [])):
                _apply_line_redactions(pi, bi, li, ln)

    # 2) Name redaction heuristic using the original spans (first non-empty line(s))
    if pages:
        first_page = pages[0]
        found = False
        for bi, block in enumerate(first_page.get("blocks", [])):
            for li, ln in enumerate(block.get("lines", [])):
                txt = ln.get("text", "").strip()
                if not txt:
                    continue
                if _looks_like_name(txt):
                    orig = txt
                    abs_start = ln.get("char_start")
                    abs_end = ln.get("char_end")
                    redactions.append({
                        "kind": "name",
                        "original": orig,
                        "replacement": "[REDACTED_NAME]",
                        "page": 0,
                        "block": bi,
                        "line": li,
                        "char_start": abs_start,
                        "char_end": abs_end,
                    })
                    ln["text"] = "[REDACTED_NAME]"
                    found = True
                    break
            if found:
                break

    # 3) Recompute block.text and page.page_text and updated per-line absolute spans
    for pi, page in enumerate(pages):
        page_text_parts = []
        for bi, block in enumerate(page.get("blocks", [])):
            line_texts = [ln.get("text", "") for ln in block.get("lines", [])]
            block_text = "\n".join(line_texts)
            block["text"] = block_text
            page_text_parts.append(block_text)

        page_text = "\n\n".join(page_text_parts)
        page["page_text"] = page_text

        # recompute absolute char spans for updated text
        offset = 0
        for bi, block in enumerate(page.get("blocks", [])):
            for li, ln in enumerate(block.get("lines", [])):
                txt = ln.get("text", "")
                ln_start = offset
                ln_end = ln_start + len(txt)
                ln["char_start"] = ln_start
                ln["char_end"] = ln_end
                offset = ln_end + 1
            offset += 1

    return structured, redactions
