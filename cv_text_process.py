import copy
import json
import re
from typing import Any, Dict, List


_BULLET_SEPS = ["●", "•", "‣"]
_BULLET_GLYPHS = r"[•●▪‣◦∙·]"
_SPLIT_SEPS_REGEX = re.compile(r"(?:\s*[;•●]\s+)+")
_INTRAWORD_BREAK = re.compile(r"([A-Za-zÄÖÜäöüß])\n\s*([A-Za-zÄÖÜäöüß])")  # join word-broken line wraps
_MULTI_BLANK_LINES = re.compile(r"\n{3,}")
_TRAILING_KNOWN_TOOL = re.compile(
    r"^(?P<a>.+?)\s+(?P<b>Ant|Maven|Gradle|Jira|Jenkins|GitHub)$", re.IGNORECASE
)


def _dedupe_preserve_order(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for x in items:
        k = x.strip()
        if not k:
            continue
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _normalize_text_block(s: str) -> str:
    if not s:
        return s
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    # join words broken by hard line wrap: "g\neoinfo" -> "geoinfo"
    s = _INTRAWORD_BREAK.sub(r"\1\2", s)
    # collapse excessive blank lines (e.g. summary extracted with empty lines)
    s = _MULTI_BLANK_LINES.sub("\n\n", s)
    # trim trailing whitespace per line
    s = "\n".join(line.rstrip() for line in s.split("\n"))
    return s.strip()


def _split_flattened_bullets(text: str) -> str:
    """
    If PDF flattening turned bullets into a single line using separators like ';' or '●',
    split into proper '- ' bullet lines.
    """
    if not text:
        return text

    # If already has bullet lines, keep as-is (we'll still fix mixed bullet/plain later).
    if "\n-" in text or text.lstrip().startswith("- "):
        return text

    if any(sep in text for sep in _BULLET_SEPS) or ";" in text:
        parts = _SPLIT_SEPS_REGEX.split(text)
        parts = [p.strip() for p in parts if p and p.strip()]
        if len(parts) >= 2:
            # Keep first part as intro if it looks like a heading/sentence ending with ':'
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

    out = []
    in_list_mode = False
    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            out.append("")
            continue

        is_bullet = line.startswith("-")
        if is_bullet:
            in_list_mode = True
            # normalize bullet prefix to "- "
            bullet_body = line[1:].lstrip()
            out.append(f"- {bullet_body}" if bullet_body else "-")
            continue

        # Heading / intro line: keep as-is; switch list mode off only if clearly a heading
        if line.endswith(":"):
            in_list_mode = False
            out.append(line)
            continue

        # If we are in list mode and we see a plain line, treat it as bullet
        # (typical PDF extraction breaks bullets or drops the symbol)
        if in_list_mode:
            out.append(f"- {line}")
        else:
            out.append(line)

    # remove duplicate blank lines inside description blocks
    normalized = "\n".join(out)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized).strip()
    return normalized


def _fix_description(desc: str, role: str, evidence: str, header_fallback: str) -> str:
    desc = _normalize_text_block(desc)
    desc = _split_flattened_bullets(desc)
    desc = _enforce_bullet_consistency(desc)

    if desc:
        return desc

    # Hard fallback chain
    if role:
        return role
    if evidence:
        return evidence
    return header_fallback or ""


def _fix_technologies(techs: List[str]) -> List[str]:
    """
    Fix the 3 common technology issues:
    1) strip/normalize whitespace
    2) split obvious combined tokens like 'PostgerSQL Ant' -> ['PostgerSQL', 'Ant']
    3) dedupe preserving order (e.g., duplicate 'Cloud Foundry')
    """
    if not techs:
        return []

    out: List[str] = []
    for t in techs:
        t = (t or "").strip()
        if not t:
            continue

        # If token is obviously two items stuck together, split conservatively when
        # the right part is a well-known single tool word and left is non-empty.
        m = _TRAILING_KNOWN_TOOL.match(t)
        if m:
            left = m.group("a").strip()
            right = m.group("b").strip()
            # Only split if left contains NO commas already (avoid breaking legit phrases)
            if left and "," not in left:
                out.append(left)
                out.append(right)
                continue

        out.append(t)

    return _dedupe_preserve_order(out)


def postprocess_cv_json(json_obj: Dict[str, Any]) -> Dict[str, Any]:
    """
    Postprocess a CV JSON produced by an LLM to fix the common quality issues:
      - Description formatting: ensure non-empty, preserve/repair bullets, fix PDF line wraps.
      - Technologies arrays: split obvious stuck tokens + dedupe + whitespace cleanup.
      - Text blocks: collapse excessive blank lines and join intra-word line breaks.

    Returns a deep-copied, cleaned JSON object (does not modify input).
    """
    cv = copy.deepcopy(json_obj)

    # summary: remove excessive blank lines / intra-word breaks
    cv["summary"] = _normalize_text_block(cv.get("summary", ""))

    # experience
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

    # skills: dedupe technologies (common duplication like Cloud Foundry)
    skills = cv.get("skills")
    if isinstance(skills, dict):
        for k in ["programming_languages", "technologies", "soft_skills"]:
            arr = skills.get(k)
            if isinstance(arr, list):
                skills[k] = _dedupe_preserve_order([str(x) for x in arr])
            elif arr is None:
                skills[k] = []
        cv["skills"] = skills

    # education degree: keep as-is (your system prompt now enforces “literal phrase only”)
    # But still normalize weird whitespace in institution/degree strings.
    edu_list = cv.get("education", [])
    if isinstance(edu_list, list):
        for edu in edu_list:
            if not isinstance(edu, dict):
                continue
            for k in ["degree", "institution", "location"]:
                edu[k] = _normalize_text_block(edu.get(k, ""))
            for k in ["start_date", "end_date"]:
                edu[k] = (edu.get(k) or "").strip()

    # certifications / languages: just normalize whitespace; do NOT translate or invent
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


_PAGE_MARKER_RE = re.compile(r"^\s*---\s*Page\s+\d+\s*---\s*$", re.IGNORECASE)

# matches a line that is just "1" .. "999" (typical footer page counters)
_STANDALONE_PAGE_NUM_RE = re.compile(r"^\s*\d{1,3}\s*$")

# date-ish line starters you likely want to preserve as anchors (very lightweight)
_DATE_ANCHOR_RE = re.compile(
    r"^\s*(\d{1,2}/\d{4}|\d{4}|\d{2}/\d{2}/\d{4}|\d{2}\.\d{2}\.\d{4})\b"
)

# lone bullet dash lines produced by PDF extraction
_LONE_DASH_RE = re.compile(r"^\s*[-–]\s*$")

# safe-ish glued token boundaries (conservative)
# 1) lower -> Upper (e.g., "GmbHDesigning")
_GLUED_LOWER_UPPER_RE = re.compile(r"([a-zäöüß])([A-ZÄÖÜ])")

# 2) letter -> email-ish boundary (e.g., "Engineeremail@" or "Engineerinfo@")
# triggers only if next part looks like a plausible email local-part start
_GLUED_TO_EMAIL_RE = re.compile(r"([A-Za-zÄÖÜäöüß])([A-Za-z0-9._%+-]{1,32}@)")

def normalize_cv_text(text: str) -> str:
    """
    Deterministic cleanup for PDF/DOCX extracted CV text.
    Goal: remove extraction artifacts WITHOUT changing meaning or destroying line anchors.
    """
    if not text:
        return text

    t = text.replace("\r\n", "\n").replace("\r", "\n")

    # Normalize common bullet glyphs into stable bullet lines (do NOT reflow)
    t = t.replace("●", "\n- ").replace("•", "\n- ").replace("▪", "\n- ")

    # Join hyphenated line breaks (safe, incl. across page markers)
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
    #    (This kills the "1 / 2 / 3 / 4 / ..." artifacts you showed.)
    filtered: List[str] = []
    for ln in lines:
        if _PAGE_MARKER_RE.match(ln):
            filtered.append(ln)
            continue
        if _STANDALONE_PAGE_NUM_RE.match(ln):
            # NOTE: This drops "1" but keeps "2017" (4 digits)
            filtered.append("")  # keep a blank to not glue unrelated lines
            continue
        filtered.append(ln)
    lines = filtered

    # helper: headings/labels
    def looks_like_heading(line: str) -> bool:
        s = line.strip()
        if not s:
            return False
        if s.endswith(":"):
            return True
        # ALLCAPS-ish heading
        letters = re.sub(r"[^A-Za-zÄÖÜäöüß]", "", s)
        return bool(letters) and letters.isupper() and len(letters) >= 4

    # 1) Fix "lone dash" bullet lines by merging with next non-empty line
    out: List[str] = []
    i = 0
    while i < len(lines):
        cur = lines[i].rstrip()
        if _LONE_DASH_RE.match(cur):
            # find next non-empty non-page-marker line
            j = i + 1
            while j < len(lines) and (not lines[j].strip() or _PAGE_MARKER_RE.match(lines[j])):
                # preserve page markers in-place
                if _PAGE_MARKER_RE.match(lines[j]):
                    out.append(lines[j])
                j += 1
            if j < len(lines) and lines[j].strip():
                out.append("- " + lines[j].strip())
                i = j + 1
                continue
            # if nothing to merge with, drop it
            i += 1
            continue

        out.append(cur)
        i += 1
    lines = out

    # 2) Conservative broken-word join (NO hyphen)
    def should_join_broken_word(prev_line: str, next_line: str) -> bool:
        a = prev_line.rstrip()
        b = next_line.lstrip()

        if not a or not b:
            return False
        if _PAGE_MARKER_RE.match(a) or _PAGE_MARKER_RE.match(b):
            return False
        if b.startswith("- "):  # don't glue into bullets
            return False
        if looks_like_heading(a) or looks_like_heading(b):
            return False
        if _DATE_ANCHOR_RE.match(a) or _DATE_ANCHOR_RE.match(b):
            return False
        if re.search(r"[.:;,)\]]\s*$", a):  # end-of-sentence/field
            return False

        # both sides must look like word fragments
        if not re.search(r"[A-Za-zÄÖÜäöüß]$", a):
            return False
        if not re.match(r"^[A-Za-zÄÖÜäöüß]", b):
            return False

        # strong signals only
        if re.match(r"^[a-zäöüß]", b):  # continuation starts lowercase
            return True

        last_token = re.split(r"\s+", a)[-1]
        if len(last_token) <= 2:  # "g\neoinformations" style
            return True

        return False

    out = []
    i = 0
    while i < len(lines):
        cur = lines[i]
        if i + 1 < len(lines) and should_join_broken_word(cur, lines[i + 1]):
            # join without adding space (word continuation)
            lines[i + 1] = cur.rstrip() + lines[i + 1].lstrip()
            i += 1
            continue
        out.append(cur)
        i += 1
    lines = out

    # 3) Conservative glued-token spacing (do NOT try to fix general German compounding)
    # Apply per-line so we don't merge lines / destroy anchors.
    spaced = []
    for ln in lines:
        if not ln or _PAGE_MARKER_RE.match(ln):
            spaced.append(ln)
            continue
        s = ln
        s = _GLUED_TO_EMAIL_RE.sub(r"\1 \2", s)
        s = _GLUED_LOWER_UPPER_RE.sub(r"\1 \2", s)
        spaced.append(s)
    lines = spaced

    # 4) (optional) split flattened semicolon lists into bullets (your original, but slightly safer)
    # Only if line contains >=2 semicolons and doesn't already contain bullets.
    final_lines: List[str] = []
    for ln in lines:
        s = ln.strip()
        if s.count(";") >= 2 and "- " not in s and not _PAGE_MARKER_RE.match(s):
            parts = [p.strip() for p in s.split(";") if p.strip()]
            if len(parts) >= 3:
                final_lines.append("")
                final_lines.extend([f"- {p}" for p in parts])
                continue
        final_lines.append(ln)

    # Collapse excessive blank lines (keep structure)
    t = "\n".join(final_lines)
    t = re.sub(r"\n{3,}", "\n\n", t).strip() + "\n"
    return t


# --- HOW TO USE ---
if __name__ == "__main__":
    # 1) Load JSON from a file (or from an LLM response string)
    with open("cv.json", "r", encoding="utf-8") as f:
        cv = json.load(f)

    # 2) Postprocess
    cleaned = postprocess_cv_json(cv)

    # 3) Save
    with open("cv.cleaned.json", "w", encoding="utf-8") as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=2)

    print("Wrote cv.cleaned.json")
