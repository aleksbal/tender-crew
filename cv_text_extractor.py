from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import List, Tuple, Dict, Optional

import fitz  # pymupdf
from docx import Document
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph
from docx.table import Table


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

def extract_text_structured(path: str) -> Tuple[Dict, ExtractDiagnostics]:
    """
    Structured API: returns dict with pages->blocks.
    IMPORTANT: For PDFs we do NOT use get_text('blocks') anymore.
               We use WORDS->LINES to preserve reading order and prevent missing anchors.
    """
    ext = os.path.splitext(path.lower())[1]
    if ext == ".pdf":
        return _pdf_to_structured_words(path)
    if ext == ".docx":
        return _docx_to_structured(path)
    raise ValueError(f"Unsupported file type: {ext}")


def extract_readable_text(structured: Dict) -> str:
    """
    Chunker-friendly readable text:
    - preserves page boundaries
    - preserves block boundaries (blank line between blocks)
    - DOES NOT invent [LEFT_COLUMN]/[RIGHT_COLUMN]/[HEADER_AREA]
    - removes obvious footers (Lebenslauf … Seite N)
    """
    pages = structured.get("pages", [])
    if not pages:
        return ""

    parts: List[str] = []
    for page_idx, page in enumerate(pages):
        page_num = page.get("page_number", page_idx + 1)
        parts.append(f"\n--- Page {page_num} ---\n")

        blocks = page.get("blocks", []) or []
        for block in blocks:
            block_text = (block.get("text") or "").strip()
            if not block_text:
                continue

            # remove footer lines inside blocks
            lines: List[str] = []
            for ln in block_text.splitlines():
                s = ln.strip()
                if not s:
                    continue
                if FOOTER_RE.match(s):
                    continue
                lines.append(s)

            if not lines:
                continue

            parts.append("\n".join(lines))
            parts.append("")  # blank line between blocks

    return "\n".join(parts).strip() + "\n"


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

def _pdf_to_structured_words(path: str) -> Tuple[Dict, ExtractDiagnostics]:
    """
    Structured PDF extraction (WORDS->LINES):
    Produces blocks that are essentially reconstructed lines (or small line groups),
    with bboxes. This preserves the anchor dates far better than get_text("blocks").
    """
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

        blocks_out: List[Dict] = []
        page_text_parts: List[str] = []
        abs_pos = 0

        for ln in line_objs:
            text = ln["text"].strip()
            if not text:
                continue

            # optional footer kill right here (helps both structured & readable)
            if FOOTER_RE.match(text):
                continue

            # In structured view, each reconstructed line is a block
            block_text = text
            # char spans inside page_text (simple: whole line)
            char_start = abs_pos
            char_end = char_start + len(block_text)

            blocks_out.append({
                "bbox": ln["bbox"],
                "text": block_text,
                "lines": [{"text": block_text, "char_start": char_start, "char_end": char_end}],
            })

            page_text_parts.append(block_text)
            # account for separator "\n\n" between blocks
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

def _words_to_line_objects(words) -> List[Dict]:
    """
    Like _words_to_lines but returns:
      [{"bbox":[x0,y0,x1,y1], "text":"..."}]
    """
    words_sorted = sorted(words, key=lambda w: (w[1], w[0]))
    Y_TOL = 2.5

    out: List[Dict] = []
    cur: List[tuple] = []
    cur_y: Optional[float] = None

    def flush():
        nonlocal cur
        if not cur:
            return
        cur.sort(key=lambda w: w[0])  # x0

        parts: List[str] = []
        prev_x1 = None

        min_x0 = float("inf")
        min_y0 = float("inf")
        max_x1 = float("-inf")
        max_y1 = float("-inf")

        for (x0, y0, x1, y1, text, *_rest) in cur:
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

        cur = []

    for w in words_sorted:
        x0, y0, x1, y1, text, *_ = w
        if cur_y is None:
            cur_y = y0
            cur.append(w)
            continue
        if abs(y0 - cur_y) <= Y_TOL:
            cur.append(w)
        else:
            flush()
            cur_y = y0
            cur.append(w)

    flush()
    return out

