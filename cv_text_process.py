# cv_text_process.py
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union


# =============================================================================
# Unicode / regex building blocks
# =============================================================================

# Unicode "letter" without third-party 'regex': [^\W\d_] = any unicode letter
_LETTER = r"[^\W\d_]"
_LETTER_TOKEN_RE = re.compile(rf"^{_LETTER}+$", re.UNICODE)

# PDF icon lines (FontAwesome etc.) often show as private-use glyphs
_PUA_ONLY_LINE_RE = re.compile(r"^[\s\uE000-\uF8FF]+$", re.UNICODE)

_PAGE_MARKER_RE = re.compile(r"^\s*---\s*Page\s+\d+\s*---\s*$", re.IGNORECASE)
_STANDALONE_PAGE_NUM_RE = re.compile(r"^\s*\d{1,3}\s*$")

# conservative date-ish anchors (keep stable, do not overfit)
_DATE_ANCHOR_RE = re.compile(
    r"^\s*(\d{1,2}/\d{4}|\d{4}|\d{2}/\d{2}/\d{4}|\d{2}\.\d{2}\.\d{4})\b"
)

# dash / bullet
_LONE_DASH_RE = re.compile(r"^\s*[-–]\s*$")
_BULLET_LINE_RE = re.compile(r"^\s*[-–]\s+\S")
_BULLET_PREFIX_RE = re.compile(r"^\s*[-–]\s*")

# hyphen wrap join (safe)
_HYPHEN_WRAP_RE = re.compile(rf"({_LETTER}|\d)-\n\s*({_LETTER}|\d)", re.UNICODE)
_HYPHEN_WRAP_ACROSS_PAGES_RE = re.compile(
    rf"({_LETTER}|\d)-\n(?:---\s*Page\s+\d+\s*---\n)+\s*({_LETTER}|\d)",
    re.IGNORECASE | re.UNICODE,
)

# email local-part split repair: "m ail@x.de" -> "mail@x.de"
_GLUED_TO_EMAIL_RE = re.compile(
    rf"({_LETTER}|[0-9])\s+([A-Za-z0-9._%+\-]{{1,32}}@)", re.UNICODE
)

# common bullet glyphs in PDFs -> normalize to "- "
_BULLET_GLYPHS = ["●", "•", "▪", "‣", "◦", "∙", "·"]

# very conservative flattened list separators -> bullets (post-LLM helper)
_FLAT_LIST_SEP_RE = re.compile(r"(?:\s*[;•●]\s+)+", re.UNICODE)

# trailing known tool split (post-LLM helper)
_TRAILING_KNOWN_TOOL = re.compile(
    r"^(?P<a>.+?)\s+(?P<b>Ant|Maven|Gradle|Jira|Jenkins|GitHub)$", re.IGNORECASE
)

# whitespace normalization: include NBSP, zero-width, etc.
# - \u00A0 NBSP, \u200B ZWSP, \u200C ZWNJ, \u200D ZWJ, \uFEFF BOM, \u00AD soft hyphen
_WEIRD_WS_RE = re.compile(r"[\u00A0\u200B\u200C\u200D\uFEFF]+", re.UNICODE)
_SOFT_HYPHEN_RE = re.compile(r"\u00AD", re.UNICODE)
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")

# legal-form fixes (high-signal PDF spacing artifacts)
# run after whitespace normalization and after wrap-joins
_LEGAL_FORM_FIXES: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"\bG\s*m\s*b\s*H\b", re.IGNORECASE), "GmbH"),  # catches "G m b H" too
    (re.compile(r"\bGmb\s*H\b", re.IGNORECASE), "GmbH"),
    (re.compile(r"\bA\s*G\b", re.IGNORECASE), "AG"),
    (re.compile(r"\bK\s*G\b", re.IGNORECASE), "KG"),
    (re.compile(r"\bG\s*b\s*R\b", re.IGNORECASE), "GbR"),
    (re.compile(r"\bS\s*E\b", re.IGNORECASE), "SE"),
]


# =============================================================================
# Debug support
# =============================================================================

@dataclass
class DebugConfig:
    enabled: bool = False
    keep_snapshots: bool = False  # store intermediate text snapshots
    max_events: int = 10_000      # safety

@dataclass
class DebugState:
    events: List[str]
    snapshots: Dict[str, str]

def _dbg_add(dbg: Optional[DebugState], msg: str) -> None:
    if not dbg:
        return
    if len(dbg.events) < 10_000:
        dbg.events.append(msg)

def _dbg_snap(dbg: Optional[DebugState], key: str, text: str) -> None:
    if not dbg:
        return
    if dbg.snapshots is not None:
        dbg.snapshots[key] = text


# =============================================================================
# Utilities
# =============================================================================

def _dedupe_preserve_order(items: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for x in items:
        s = (x or "").strip()
        if not s:
            continue
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out

def _normalize_text_block(s: str) -> str:
    """Conservative whitespace cleanup inside JSON fields (not the PDF text stream)."""
    if not s:
        return s
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = "\n".join(line.rstrip() for line in s.split("\n"))
    return s.strip()

def _is_headingish(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if s.endswith(":"):
        return True
    # ALLCAPS-ish headings
    letters_only = re.sub(rf"[^{_LETTER}]+", "", s, flags=re.UNICODE)
    return bool(letters_only) and letters_only.isupper() and len(letters_only) >= 4

def _starts_lowercase_letter(s: str) -> bool:
    s = s.lstrip()
    if not s:
        return False
    ch = s[0]
    return ch.isalpha() and ch.islower()

def _ends_with_strong_stop(s: str) -> bool:
    # treat ":" as strong stop too (labels)
    return bool(re.search(r"[.!?]\s*$", s)) or s.rstrip().endswith(":")

def _looks_like_label_line(s: str) -> bool:
    # Keep conservative: common label patterns in German CVs
    t = s.strip()
    if not t:
        return False
    if t.endswith(":"):
        return True
    if re.match(r"^\s*Eingesetzte Technologien\s*:\s*", t, re.IGNORECASE):
        return True
    return False

def _normalize_weird_whitespace(s: str) -> str:
    # remove soft hyphen, normalize weird ws to normal space, collapse multiple spaces
    s2 = _SOFT_HYPHEN_RE.sub("", s)
    s2 = _WEIRD_WS_RE.sub(" ", s2)
    # normalize tabs to spaces, collapse runs
    s2 = s2.replace("\t", " ")
    s2 = _MULTI_SPACE_RE.sub(" ", s2)
    return s2


# =============================================================================
# Normalization for extracted plain text (PDF/DOCX)
# =============================================================================

def _drop_pua_icon_lines(lines: List[str], dbg: Optional[DebugState]) -> List[str]:
    out: List[str] = []
    for ln in lines:
        if _PAGE_MARKER_RE.match(ln):
            out.append(ln)
            continue
        if _PUA_ONLY_LINE_RE.match(ln):
            _dbg_add(dbg, f"drop_pua: {ln!r}")
            continue
        out.append(ln)
    return out

def _drop_standalone_page_numbers(lines: List[str], dbg: Optional[DebugState]) -> List[str]:
    out: List[str] = []
    for ln in lines:
        if _PAGE_MARKER_RE.match(ln):
            out.append(ln)
            continue
        if _STANDALONE_PAGE_NUM_RE.match(ln):
            _dbg_add(dbg, f"drop_page_num: {ln!r}")
            out.append("")  # preserve separation
            continue
        out.append(ln)
    return out

def _fix_lone_dash_lines(lines: List[str], dbg: Optional[DebugState]) -> List[str]:
    """
    Turns:
      "-"
      "Text..."
    into:
      "- Text..."
    Preserves page markers.
    """
    out: List[str] = []
    i = 0
    while i < len(lines):
        cur = lines[i].rstrip()
        if _LONE_DASH_RE.match(cur):
            j = i + 1
            # skip empties and page markers
            while j < len(lines) and (not lines[j].strip() or _PAGE_MARKER_RE.match(lines[j])):
                if _PAGE_MARKER_RE.match(lines[j]):
                    out.append(lines[j])
                j += 1
            if j < len(lines) and lines[j].strip():
                merged = "- " + lines[j].strip()
                _dbg_add(dbg, f"lone_dash_merge: {merged!r}")
                out.append(merged)
                i = j + 1
                continue
            _dbg_add(dbg, "lone_dash_drop: '-' (no next content)")
            i += 1
            continue

        out.append(cur)
        i += 1
    return out

def _fix_email_and_legal_forms_per_line(lines: List[str], dbg: Optional[DebugState]) -> List[str]:
    out: List[str] = []
    for ln in lines:
        if not ln or _PAGE_MARKER_RE.match(ln):
            out.append(ln)
            continue

        s0 = ln
        s = _normalize_weird_whitespace(s0)

        # email glue: "m ail@" -> "mail@"
        s2 = _GLUED_TO_EMAIL_RE.sub(r"\1\2", s)
        if s2 != s:
            _dbg_add(dbg, f"email_glue: {s!r} -> {s2!r}")
        s = s2

        # legal forms
        for pat, repl in _LEGAL_FORM_FIXES:
            s3 = pat.sub(repl, s)
            if s3 != s:
                _dbg_add(dbg, f"legal_form: {s!r} -> {s3!r}")
            s = s3

        out.append(s)
    return out

def _join_soft_wraps(lines: List[str], dbg: Optional[DebugState]) -> List[str]:
    """
    GENERAL soft-wrap join to preserve meaning/content (prose-first).

    It joins lines when the newline is likely a layout wrap, not a semantic boundary.
    Works for bullets AND prose. Does NOT require bullets.

    Hard stops (never join across):
      - page markers
      - date anchors
      - heading-ish lines / label lines (e.g., 'Eingesetzte Technologien:')
      - empty line (paragraph break)

    Hyphen wrap is handled earlier globally, but this also joins e.g.:
      'Ent-' + 'scheidungsprozessen'  (if hyphen survived in plain text)
    """
    out: List[str] = []
    i = 0

    def is_hard_stop_line(s: str) -> bool:
        t = s.strip()
        if not t:
            return True
        if _PAGE_MARKER_RE.match(t):
            return True
        if _DATE_ANCHOR_RE.match(t):
            return True
        if _is_headingish(t):
            return True
        if _looks_like_label_line(t):
            return True
        return False

    while i < len(lines):
        cur = lines[i].rstrip()
        if not cur.strip():
            out.append("")
            i += 1
            continue

        if _PAGE_MARKER_RE.match(cur.strip()):
            out.append(cur.strip())
            i += 1
            continue

        merged = _normalize_weird_whitespace(cur).strip()
        j = i + 1

        while j < len(lines):
            nxt_raw = lines[j]
            nxt = _normalize_weird_whitespace(nxt_raw).strip()

            if is_hard_stop_line(nxt_raw) or is_hard_stop_line(lines[j]):
                break

            # If current is a date anchor line, do not join
            if _DATE_ANCHOR_RE.match(merged):
                break

            # If next line starts a bullet and current is not a bullet, do not glue into it
            if _BULLET_LINE_RE.match(nxt) and not _BULLET_LINE_RE.match(merged):
                break

            # If current ends with label colon, do not join
            if merged.endswith(":"):
                break

            # Join decision:
            # 1) hyphen wrap inside line (Ent- + scheidung...)
            if merged.endswith("-") and _starts_lowercase_letter(nxt):
                _dbg_add(dbg, f"softwrap_hyphen: {merged!r} + {nxt!r}")
                merged = merged[:-1] + nxt
                j += 1
                continue

            # 2) Strong stop punctuation => boundary
            if _ends_with_strong_stop(merged):
                break

            # 3) Bullet handling:
            #    If merged is a bullet and next line is not a bullet but looks like continuation,
            #    we join (this fixes your '- in GitHub Entwicklung ...' cases).
            merged_is_bullet = _BULLET_LINE_RE.match(merged)
            nxt_is_bullet = _BULLET_LINE_RE.match(nxt)

            if merged_is_bullet and not nxt_is_bullet:
                # Do not join if next looks like a new section label
                if _looks_like_label_line(nxt):
                    break
                _dbg_add(dbg, f"softwrap_bullet_cont: + {nxt!r}")
                merged = f"{merged} {nxt}".strip()
                j += 1
                continue

            # 4) Prose continuation:
            #    If next starts lowercase, or current ends with comma/slash/open-paren, join.
            if _starts_lowercase_letter(nxt) or re.search(r"[,/(\u2013-]\s*$", merged):
                _dbg_add(dbg, f"softwrap_join: {merged!r} + {nxt!r}")
                merged = f"{merged} {nxt}".strip()
                j += 1
                continue

            # Otherwise: do not join (too risky)
            break

        out.append(merged)
        i = j

    return out

def _normalize_bullet_glyphs(text: str, dbg: Optional[DebugState]) -> str:
    t = text
    for g in _BULLET_GLYPHS:
        if g in t:
            t = t.replace(g, "\n- ")
            _dbg_add(dbg, f"bullet_glyph_replace: {g!r}")
    return t

def normalize_cv_text(
    text: str,
    debug: bool = False,
    keep_snapshots: bool = False,
) -> Union[str, Tuple[str, List[str], Dict[str, str]]]:
    """
    Deterministic cleanup for extracted CV text (PDF/DOCX).
    Goal: remove extraction artifacts WITHOUT losing meaning/content or destroying anchors.

    Returns:
      - debug=False: normalized_text
      - debug=True: (normalized_text, debug_events, snapshots)
    """
    if not text:
        if debug:
            return ("", [], {})
        return ""

    dbg_state: Optional[DebugState] = None
    if debug:
        dbg_state = DebugState(events=[], snapshots={} if keep_snapshots else {})

    t = text.replace("\r\n", "\n").replace("\r", "\n")
    _dbg_snap(dbg_state, "0_raw", t)

    # normalize bullet glyphs
    t = _normalize_bullet_glyphs(t, dbg_state)
    _dbg_snap(dbg_state, "1_bullets", t)

    # safe hyphen-wrap joins (incl across pages)
    t2 = _HYPHEN_WRAP_RE.sub(r"\1-\2", t)
    if t2 != t:
        _dbg_add(dbg_state, "hyphen_wrap_join: within page")
    t = t2

    t2 = _HYPHEN_WRAP_ACROSS_PAGES_RE.sub(r"\1-\2", t)
    if t2 != t:
        _dbg_add(dbg_state, "hyphen_wrap_join: across pages")
    t = t2
    _dbg_snap(dbg_state, "2_hyphenwrap", t)

    # split into lines and trim
    lines = [ln.rstrip() for ln in t.split("\n")]

    # drop icon-only lines & page numbers
    lines = _drop_pua_icon_lines(lines, dbg_state)
    lines = _drop_standalone_page_numbers(lines, dbg_state)
    _dbg_snap(dbg_state, "3_drop_icons_pagenums", "\n".join(lines))

    # fix lone dash lines
    lines = _fix_lone_dash_lines(lines, dbg_state)
    _dbg_snap(dbg_state, "4_lone_dash", "\n".join(lines))

    # per-line whitespace normalize + email + legal forms BEFORE join (helps GmbH)
    lines = _fix_email_and_legal_forms_per_line(lines, dbg_state)
    _dbg_snap(dbg_state, "5_email_legal", "\n".join(lines))

    # join soft wraps (main content-preservation step)
    lines = _join_soft_wraps(lines, dbg_state)
    _dbg_snap(dbg_state, "6_softwrap", "\n".join(lines))

    # run email/legal again AFTER joins (catches Gmb\nH cases created by joins)
    lines = _fix_email_and_legal_forms_per_line(lines, dbg_state)
    _dbg_snap(dbg_state, "7_email_legal_again", "\n".join(lines))

    # collapse excessive blank lines
    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out).strip() + "\n"
    _dbg_snap(dbg_state, "8_final", out)

    if debug:
        return out, (dbg_state.events if dbg_state else []), (dbg_state.snapshots if dbg_state else {})
    return out


# =============================================================================
# Optional: build text from PyMuPDF words, with optional column mode
# =============================================================================

def words_to_line_objects(words: Iterable[tuple]) -> List[Dict[str, Any]]:
    """
    Build line objects by (block_no, line_no) from PyMuPDF words.

    Each words tuple: (x0, y0, x1, y1, "word", block_no, line_no, word_no)
    """
    groups: Dict[Tuple[int, int], List[tuple]] = {}
    for w in words:
        if len(w) < 8:
            continue
        x0, y0, x1, y1, text, block_no, line_no, word_no = w[:8]
        key = (int(block_no), int(line_no))
        groups.setdefault(key, []).append(w)

    line_items: List[Tuple[float, float, Tuple[int, int], List[tuple]]] = []
    for key, ws in groups.items():
        min_y0 = min(w[1] for w in ws)
        min_x0 = min(w[0] for w in ws)
        line_items.append((min_y0, min_x0, key, ws))
    line_items.sort(key=lambda t: (t[0], t[1]))

    out: List[Dict[str, Any]] = []
    for _min_y0, _min_x0, _key, ws in line_items:
        ws.sort(key=lambda w: (int(w[7]), w[0]))  # word_no then x0

        parts: List[str] = []
        prev_x1 = None

        min_x0 = float("inf")
        min_y0 = float("inf")
        max_x1 = float("-inf")
        max_y1 = float("-inf")

        for (x0, y0, x1, y1, text, *_rest) in ws:
            min_x0 = min(min_x0, x0)
            min_y0 = min(min_y0, y0)
            max_x1 = max(max_x1, x1)
            max_y1 = max(max_y1, y1)

            t = re.sub(r"\s+", " ", (text or "").strip())
            t = _normalize_weird_whitespace(t)
            if not t:
                continue

            if prev_x1 is None:
                parts.append(t)
            else:
                gap = x0 - prev_x1
                parts.append((" " if gap > 1.5 else "") + t)
            prev_x1 = x1

        line_text = "".join(parts).strip()
        if line_text:
            out.append({"bbox": [min_x0, min_y0, max_x1, max_y1], "text": line_text})

    return out


def words_to_text(
    words: Iterable[tuple],
    column_mode: str = "auto",  # "off" | "auto" | "on"
    debug: bool = False,
) -> Union[str, Tuple[str, List[str]]]:
    """
    Turn PyMuPDF words into page text, optionally using a simple 2-column split by x-median.

    column_mode:
      - "off": never split columns
      - "on": always split
      - "auto": split only if it looks like 2 columns
    """
    dbg: List[str] = []
    ws = [w for w in words if len(w) >= 8]
    if not ws:
        return ("", dbg) if debug else ""

    # estimate x split (median of word centers)
    centers = sorted(((w[0] + w[2]) / 2.0) for w in ws)
    x_med = centers[len(centers) // 2]

    # cheap 2-col detection
    left = [w for w in ws if ((w[0] + w[2]) / 2.0) < x_med]
    right = [w for w in ws if ((w[0] + w[2]) / 2.0) >= x_med]

    def col_score(col: List[tuple]) -> float:
        # how much content? (words count) + spread of x
        if not col:
            return 0.0
        xs = [((w[0] + w[2]) / 2.0) for w in col]
        return float(len(col)) * (max(xs) - min(xs) + 1.0)

    use_cols = False
    if column_mode == "on":
        use_cols = True
    elif column_mode == "off":
        use_cols = False
    else:
        # "auto": require both sides non-trivial and separated
        if len(left) > 30 and len(right) > 30:
            # separation: the 90th percentile of left < 10th percentile of right
            left_xs = sorted(((w[0] + w[2]) / 2.0) for w in left)
            right_xs = sorted(((w[0] + w[2]) / 2.0) for w in right)
            left_p90 = left_xs[int(0.9 * (len(left_xs) - 1))]
            right_p10 = right_xs[int(0.1 * (len(right_xs) - 1))]
            if left_p90 + 5.0 < right_p10:
                use_cols = True

    if debug:
        dbg.append(f"column_mode={column_mode}, use_cols={use_cols}, x_med={x_med:.2f}, left_words={len(left)}, right_words={len(right)}")

    if not use_cols:
        lines = words_to_line_objects(ws)
        text = "\n".join(l["text"] for l in lines if l.get("text"))
        return (text, dbg) if debug else text

    # build lines per column
    left_lines = words_to_line_objects(left)
    right_lines = words_to_line_objects(right)

    # merge by y-bands: at same y-band, emit left then right
    # (helps "dates column" appear before content)
    band = 6.0  # points-ish; simple
    def band_key(line: Dict[str, Any]) -> int:
        y0 = float(line["bbox"][1])
        return int(y0 // band)

    buckets: Dict[int, Dict[str, List[Dict[str, Any]]]] = {}
    for ln in left_lines:
        buckets.setdefault(band_key(ln), {"L": [], "R": []})["L"].append(ln)
    for ln in right_lines:
        buckets.setdefault(band_key(ln), {"L": [], "R": []})["R"].append(ln)

    merged_lines: List[str] = []
    for k in sorted(buckets.keys()):
        L = buckets[k]["L"]
        R = buckets[k]["R"]
        # keep local ordering by x/y already in line_objects; just emit L then R
        for ln in L:
            merged_lines.append(ln["text"])
        for ln in R:
            merged_lines.append(ln["text"])

    text = "\n".join(merged_lines)
    return (text, dbg) if debug else text


# =============================================================================
# Post-LLM JSON cleanup (tender-extraction oriented)
# =============================================================================

def _split_flattened_bullets(text: str) -> str:
    """
    Post-LLM helper. If a description was flattened into one line with separators like ';' or '●',
    split into separate lines (NOT necessarily bullets; but we keep '- ' if present).
    """
    if not text:
        return text

    if "\n-" in text or text.lstrip().startswith("- "):
        return text

    if "●" in text or "•" in text or ";" in text:
        parts = _FLAT_LIST_SEP_RE.split(text)
        parts = [p.strip() for p in parts if p and p.strip()]
        if len(parts) >= 2:
            # keep intro sentence if it ends with ':'
            if parts[0].endswith(":"):
                intro = parts[0]
                rest = parts[1:]
                return intro + "\n" + "\n".join(rest)
            return "\n".join(parts)

    return text

def _fix_technologies(techs: List[str]) -> List[str]:
    if not techs:
        return []
    out: List[str] = []
    for t in techs:
        t = _normalize_weird_whitespace((t or "").strip())
        if not t:
            continue

        m = _TRAILING_KNOWN_TOOL.match(t)
        if m:
            left = m.group("a").strip()
            right = m.group("b").strip()
            if left and "," not in left:
                out.append(left)
                out.append(right)
                continue

        out.append(t)
    return _dedupe_preserve_order(out)

def _fix_description(desc: str, role: str, evidence: str, header_fallback: str) -> str:
    # IMPORTANT: tender-extraction => do NOT force bullets
    desc = _normalize_text_block(desc)
    desc = _split_flattened_bullets(desc)

    if desc:
        return desc
    if role:
        return role
    if evidence:
        return evidence
    return header_fallback or ""

def postprocess_cv_json(json_obj: Dict[str, Any]) -> Dict[str, Any]:
    """
    Postprocess a CV JSON produced by an LLM to fix common quality issues
    WITHOUT enforcing any visual formatting (bullets are optional).

    - Description: ensure non-empty, normalize whitespace, split flattened separators
    - Technologies: normalize whitespace, split a few obvious stuck tokens, dedupe
    """
    cv = copy.deepcopy(json_obj)

    cv["summary"] = _normalize_text_block(cv.get("summary", ""))

    exp_list = cv.get("experience", [])
    if isinstance(exp_list, list):
        for exp in exp_list:
            if not isinstance(exp, dict):
                continue

            role = _normalize_weird_whitespace((exp.get("role") or "").strip())
            company = _normalize_weird_whitespace((exp.get("company") or "").strip())
            evidence = _normalize_text_block(exp.get("evidence", ""))

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
                skills[k] = _dedupe_preserve_order([_normalize_weird_whitespace(str(x)) for x in arr])
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
                edu[k] = _normalize_weird_whitespace((edu.get(k) or "").strip())

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


# =============================================================================
# CLI quick test
# =============================================================================

if __name__ == "__main__":
    import argparse
    import pathlib
    import sys

    ap = argparse.ArgumentParser()
    ap.add_argument("raw_text_file", help="Path to extracted raw text file")
    ap.add_argument("--debug", action="store_true", help="Print debug events")
    ap.add_argument("--snapshots", action="store_true", help="Include debug snapshots")
    args = ap.parse_args()

    p = pathlib.Path(args.raw_text_file)
    raw = p.read_text(encoding="utf-8", errors="replace")

    if args.debug:
        norm, events, snaps = normalize_cv_text(raw, debug=True, keep_snapshots=args.snapshots)  # type: ignore
        print(norm)
        print("\n--- DEBUG EVENTS ---")
        for e in events:
            print(e)
        if args.snapshots:
            print("\n--- SNAPSHOTS ---")
            for k, v in snaps.items():
                print(f"\n### {k}\n{v[:2000]}\n...")
    else:
        print(normalize_cv_text(raw))
