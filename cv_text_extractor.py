from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import List, Tuple, Dict

import fitz  # pymupdf
from docx import Document
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph
from docx.table import Table

from cv_text_process import normalize_cv_text

from statistics import median


# =============================================================================
# Diagnostics
# =============================================================================

@dataclass
class ExtractDiagnostics:
    file_type: str
    pages: int = 0
    rejected_scanned_pdf: bool = False
    multi_column_pages: int = 0  # kept for compatibility (no longer used by words-extraction)
    empty_text_pages: int = 0


# =============================================================================
# Section-ish headings (optional; used only when normalizing DOCX / legacy blocks)
# =============================================================================

SECTIONY_RE = re.compile(
    r"""
    ^
    \s*
    (?:
        # German
        profil |
        zusammenfassung |
        kurzprofil |
        beruflicher\s+werdegang |
        berufserfahrung |
        projekte |
        projekt(e)? |
        tätigkeiten |
        fähigkeiten |
        skills |
        kenntnisse |
        technologien |
        technik(en)? |
        tech\s*stack |
        ausbildung |
        studium |
        zertifikate |
        qualifikationen |
        sprachen |

        # English
        profile |
        summary |
        professional\s+summary |
        work\s+experience |
        professional\s+experience |
        experience |
        employment |
        projects |
        skills |
        expertise |
        technologies |
        tech\s*stack |
        education |
        certifications |
        qualifications |
        languages
    )
    \s*:?        # optional trailing colon
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)


FOOTER_RE = re.compile(r"^\s*Lebenslauf\b.*\bSeite\s+\d+\s*$", re.IGNORECASE)


# =============================================================================
# Public API
# =============================================================================


def extract_text_structured(path: str, pdf_column_mode: str = "auto") -> Tuple[Dict, ExtractDiagnostics]:
    ext = os.path.splitext(path.lower())[1]
    if ext == ".pdf":
        return _pdf_to_structured_words(path, pdf_column_mode=pdf_column_mode)
    if ext == ".docx":
        return _docx_to_structured(path)
    raise ValueError(f"Unsupported file type: {ext}")

def extract_text_plain(structured: Dict) -> str:
    pages = structured.get("pages", [])
    if not pages:
        return ""

    is_pdf = structured.get("file_type") == "pdf"
    out_parts: List[str] = []

    for page_idx, page in enumerate(pages):
        page_num = page.get("page_number", page_idx + 1)
        header = f"\n--- Page {page_num} ---\n"

        lines_acc: List[str] = []
        for block in (page.get("blocks", []) or []):
            t = (block.get("text") or "").strip()
            if not t:
                continue
            for ln in t.splitlines():
                s = ln.strip()
                if s and not FOOTER_RE.match(s):
                    lines_acc.append(s)

            # preserve paragraph gaps only for docx
            if not is_pdf:
                lines_acc.append("")

        page_text = "\n".join(lines_acc).strip()
        out_parts.append(header + page_text + "\n")

    # ✅ Normalize ONCE at the end (so we can safely join across pages if needed)
    full_text = "\n".join(out_parts).strip() + "\n"
    full_text = normalize_cv_text(full_text)

    return full_text


# =============================================================================
# DOCX
# =============================================================================

def _docx_to_structured(path: str) -> Tuple[Dict, ExtractDiagnostics]:
    doc = Document(path)
    pages_blocks: List[List[Dict]] = [[]]

    def para_has_page_break(par: Paragraph) -> bool:
        for r in par.runs:
            for br in r._r.findall(qn('w:br')):
                if br.get(qn('w:type')) == 'page':
                    return True
        if 'lastRenderedPageBreak' in par._p.xml:
            return True
        return False

    body = doc.element.body
    for child in body:
        if child.tag == qn('w:p'):
            p = Paragraph(child, doc)
            t = (p.text or '').strip()
            if t:
                norm_lines = _normalize_block(t)
                pages_blocks[-1].append({
                    'bbox': None,
                    'text': '\n'.join(norm_lines),
                    'lines': [{'text': ln, 'char_start': None, 'char_end': None} for ln in norm_lines],
                })
            if para_has_page_break(p):
                pages_blocks.append([])
        elif child.tag == qn('w:tbl'):
            tbl = Table(child, doc)
            for row in tbl.rows:
                cells = [c.text.strip() for c in row.cells if c.text and c.text.strip()]
                if cells:
                    t = ' | '.join(cells)
                    norm_lines = _normalize_block(t)
                    pages_blocks[-1].append({
                        'bbox': None,
                        'text': '\n'.join(norm_lines),
                        'lines': [{'text': ln, 'char_start': None, 'char_end': None} for ln in norm_lines],
                    })

    pages_out: List[Dict] = []
    for pi, blocks_out in enumerate(pages_blocks):
        if not blocks_out and pi == len(pages_blocks) - 1:
            continue
        page_text = '\n\n'.join(b['text'] for b in blocks_out)
        pages_out.append({
            'page_number': pi + 1,
            'width': None,
            'height': None,
            'page_text': page_text,
            'blocks': blocks_out,
        })

    diag = ExtractDiagnostics(file_type='docx', pages=len(pages_out))
    return {'file_type': 'docx', 'pages': pages_out}, diag


def _normalize_block(block: str) -> List[str]:
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in block.splitlines()]
    lines = [ln for ln in lines if ln]
    out: List[str] = []
    for ln in lines:
        if SECTIONY_RE.match(ln):
            out.append(f"## {ln}")
        else:
            out.append(ln)
    return out


# =============================================================================
# PDF (FIXED): WORDS -> LINES (for both readable and structured)
# =============================================================================

def _pdf_to_structured_words(path: str, pdf_column_mode: str = "auto") -> Tuple[Dict, ExtractDiagnostics]:
    doc = fitz.open(path)
    diag = ExtractDiagnostics(file_type="pdf", pages=doc.page_count)
    pages_out: List[Dict] = []

    for pno in range(doc.page_count):
        page = doc.load_page(pno)
        words = page.get_text("words")

        if not words:
            diag.rejected_scanned_pdf = True
            diag.empty_text_pages += 1
            raise ValueError(
                f"PDF page {pno+1} has no extractable text (scanned/bitmap?). Rejecting by policy."
            )

        line_objs = _words_to_line_objects(words)

        # ✅ Optional: reorder for 2-column pages
        line_objs = _maybe_reorder_two_columns(line_objs, page.rect.width, mode=pdf_column_mode)

        blocks_out: List[Dict] = []
        page_text_parts: List[str] = []
        abs_pos = 0

        for ln in line_objs:
            text = (ln.get("text") or "").strip()
            if not text:
                continue
            if FOOTER_RE.match(text):
                continue

            block_text = text
            char_start = abs_pos
            char_end = char_start + len(block_text)

            blocks_out.append({
                "bbox": ln.get("bbox"),
                "text": block_text,
                "lines": [{"text": block_text, "char_start": char_start, "char_end": char_end}],
            })

            page_text_parts.append(block_text)
            abs_pos = char_end + 2

        page_text = "\n\n".join(page_text_parts)

        pages_out.append({
            "page_number": pno + 1,
            "width": page.rect.width,
            "height": page.rect.height,
            "page_text": page_text,
            "blocks": blocks_out,
        })

    return {"file_type": "pdf", "pages": pages_out}, diag


def _maybe_reorder_two_columns(line_objs: List[Dict], page_width: float, mode: str = "auto") -> List[Dict]:
    """
    Reorder PDF lines for 2-column layouts using bbox x0 median split.
    mode:
      - "off": never reorder
      - "on": always attempt reorder (even if weak signal)
      - "auto": reorder only if 2-column signal is strong
    """
    if mode == "off":
        return line_objs
    if not line_objs or not page_width:
        return line_objs

    xs = []
    for ln in line_objs:
        bb = ln.get("bbox")
        if bb and len(bb) == 4:
            xs.append(float(bb[0]))
    if len(xs) < 20:
        return line_objs  # not enough lines to decide

    x_med = median(xs)

    left = []
    right = []
    for ln in line_objs:
        bb = ln.get("bbox")
        if not bb:
            continue
        x0, y0, x1, y1 = bb
        if x0 <= x_med:
            left.append(ln)
        else:
            right.append(ln)

    # If one side is tiny, it's not really two columns
    if len(left) < 8 or len(right) < 8:
        return line_objs

    # Compute separation strength
    left_med = median([ln["bbox"][0] for ln in left])
    right_med = median([ln["bbox"][0] for ln in right])
    sep = right_med - left_med

    # "auto" only: require a strong separation
    if mode == "auto":
        # must be a meaningful horizontal gap relative to page width
        if sep < page_width * 0.25:
            return line_objs

        # also require left cluster really on left and right cluster really on right
        if not (left_med < page_width * 0.45 and right_med > page_width * 0.45):
            return line_objs

    # Sort within columns by y0 then x0
    left_sorted = sorted(left, key=lambda ln: (ln["bbox"][1], ln["bbox"][0]))
    right_sorted = sorted(right, key=lambda ln: (ln["bbox"][1], ln["bbox"][0]))

    # Read order: left column top-to-bottom, then right column top-to-bottom
    # (simple + predictable; good enough for most CVs)
    return left_sorted + right_sorted



def _words_to_line_objects(words) -> List[Dict]:
    """
    Production-ish: build lines by (block_no, line_no) from PyMuPDF words.
    words tuple: (x0, y0, x1, y1, "word", block_no, line_no, word_no)
    This avoids y-tolerance heuristics entirely.
    """
    # group words by (block_no, line_no)
    groups: Dict[tuple, List[tuple]] = {}
    for w in words:
        if len(w) < 8:
            # Defensive: unexpected format
            continue
        x0, y0, x1, y1, text, block_no, line_no, word_no = w[:8]
        key = (int(block_no), int(line_no))
        groups.setdefault(key, []).append(w)

    # Sort lines top-to-bottom using min y0 of the group, then min x0
    line_items = []
    for key, ws in groups.items():
        min_y0 = min(w[1] for w in ws)
        min_x0 = min(w[0] for w in ws)
        line_items.append((min_y0, min_x0, key, ws))
    line_items.sort(key=lambda t: (t[0], t[1]))

    out: List[Dict] = []
    for _min_y0, _min_x0, _key, ws in line_items:
        # sort words left-to-right using word_no then x0 (word_no usually stable)
        ws.sort(key=lambda w: (int(w[7]), w[0]))

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


