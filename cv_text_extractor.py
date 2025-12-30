from __future__ import annotations

from pathlib import Path
from collections import Counter
import re
from typing import List, Tuple

import pymupdf


_FORM_FEED = "\f"

_ws_re = re.compile(r"[ \t]+")
_digits_re = re.compile(r"\d")
_page_x_of_y_re = re.compile(r"(?i)\bpage\s*\d+\s*(?:/|of)\s*\d+\b")


def _norm(s: str) -> str:
    s = s.replace("\u00a0", " ").strip()
    if not s:
        return ""
    s = _page_x_of_y_re.sub("page # of #", s)
    s = _ws_re.sub(" ", s)
    s = _digits_re.sub("#", s)
    return s.lower().strip()


def _page_block_signatures(page) -> List[Tuple[str, float, float]]:
    """
    Returns list of (signature, y0, y1) for text blocks.
    Using blocks ONLY for header/footer detection.
    """
    out = []
    for b in page.get_text("blocks") or []:
        if len(b) < 5:
            continue
        x0, y0, x1, y1, text = b[0], b[1], b[2], b[3], b[4]
        if not isinstance(text, str):
            continue
        sig = _norm(text)
        if sig:
            out.append((sig, float(y0), float(y1)))
    return out


def _learn_repeating_hf_signatures(doc: pymupdf.Document,
                                  header_zone: float = 0.18,
                                  footer_zone: float = 0.82,
                                  min_pages_frac: float = 0.60) -> Tuple[set[str], set[str]]:
    """
    Find signatures that repeat on many pages, separately for header and footer zones.
    """
    n = doc.page_count
    if n <= 1:
        return set(), set()

    header_counts = Counter()
    footer_counts = Counter()

    for i in range(n):
        page = doc.load_page(i)
        rect = page.rect
        h_cut = rect.height * header_zone
        f_cut = rect.height * footer_zone

        for sig, y0, y1 in _page_block_signatures(page):
            if y1 <= h_cut:
                header_counts[sig] += 1
            if y0 >= f_cut:
                footer_counts[sig] += 1

    thr = max(2, int(round(n * min_pages_frac)))
    headers = {s for s, c in header_counts.items() if c >= thr}
    footers = {s for s, c in footer_counts.items() if c >= thr}
    return headers, footers


def _compute_body_clip(page,
                       header_sigs: set[str],
                       footer_sigs: set[str],
                       header_zone: float = 0.18,
                       footer_zone: float = 0.82,
                       pad: float = 2.0) -> pymupdf.Rect:
    """
    Compute a per-page body clip rectangle based on where repeating header/footer blocks
    actually are on that page. Falls back to full page if nothing detected.
    """
    rect = page.rect
    h_cut = rect.height * header_zone
    f_cut = rect.height * footer_zone

    body_top = 0.0
    body_bottom = rect.height

    # Find the lowest repeating header block and highest repeating footer block on THIS page
    for sig, y0, y1 in _page_block_signatures(page):
        if header_sigs and sig in header_sigs and y1 <= h_cut:
            body_top = max(body_top, y1 + pad)
        if footer_sigs and sig in footer_sigs and y0 >= f_cut:
            body_bottom = min(body_bottom, y0 - pad)

    # Ensure sane rectangle
    if body_bottom <= body_top + 5:
        body_top = 0.0
        body_bottom = rect.height

    return pymupdf.Rect(0, body_top, rect.width, body_bottom)


def extract_plain_text(path: Path) -> str:
    doc = pymupdf.open(path)
    try:
        header_sigs, footer_sigs = _learn_repeating_hf_signatures(doc)

        result_text: List[str] = []
        for i in range(doc.page_count):
            page = doc.load_page(i)

            clip = _compute_body_clip(page, header_sigs, footer_sigs)

            # IMPORTANT: keep the SAME extraction mode that flattens your tables well.
            # (You can toggle sort=True if you see ordering glitches on some PDFs.)
            text = page.get_text(clip=clip, sort=True)

            result_text.append(text)
            result_text.append(_FORM_FEED)

        return postprocess("".join(result_text))
    finally:
        doc.close()

import re

def postprocess(text: str) -> str:
    text = re.sub(r'(\w)-\n(\w)', r'\1\2', text)                         # join hyphen-wrapped words
    text = re.sub(r'[ \t]+\n', '\n', text)                               # trim line-end spaces
    text = re.sub(r'\n{3,}', '\n\n', text)                               # collapse huge gaps
    text = re.sub(r'(?m)^[^\w\n]{1,6}$\n?', '', text)                    # drop icon-only short lines
    text = re.sub(r'(?m)^[\uE000-\uF8FF]+\s*$\n?', '', text)             # drop Private-Use glyph lines
    text = re.sub(r'(?m)^\s*•\s*', '• ', text)                           # normalize bullets
    text = re.sub(r'(?m)^\s+([•\w(])', r'\1', text)                      # de-indent common lines
    text = re.sub(r'(?m)^\s*$\n', '\n', text)                            # remove empty lines with spaces
    text = re.sub(r'[ \t]{2,}', ' ', text)                               # collapse runs of spaces
    return text.strip() + "\n"


if __name__ == "__main__":
    print(extract_plain_text(Path("cv/cvd.pdf")))
