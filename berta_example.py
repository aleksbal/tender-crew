from __future__ import annotations
import re
from typing import List
from dataclasses import dataclass
from transformers import AutoTokenizer, AutoModelForTokenClassification, pipeline

# ==========================================================
# MODEL
# ==========================================================

MODEL = "Davlan/xlm-roberta-base-ner-hrl"
# Multilingual PER / ORG / LOC NER (German-safe)

# ==========================================================
# REGEX DEFINITIONS
# ==========================================================

# ---- EMAIL ------------------------------------------------
# user.name+tag@domain.tld
EMAIL_RE = re.compile(r"\b[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+\b")

# ---- URL --------------------------------------------------
# http(s)://domain/...   OR   domain.tld/path
URL_RE = re.compile(r"\b(?:https?://)?(?:www\.)?[a-zA-Z0-9-]+\.[a-z]{2,}(?:/[^\s]*)?\b")

# ---- PHONE CANDIDATES -----------------------------------
# Only numeric-heavy patterns, no words allowed.
# Requires ≥8 digits → blocks dates like 01-2020.
PHONE_CANDIDATE_RE = re.compile(r"(?<!\w)\+?\d[\d\s().\-\/]{5,}\d(?!\w)")

# ---- DATE BLOCKER ----------------------------------------
# Detects 01-2020, 2020-10, 01/2020 - 10/2024, etc.
DATE_RE = re.compile(r"\b(?:\d{1,2}[./-]\d{4}|\d{4}[./-]\d{1,2})(?:\s*[-–]\s*(?:\d{1,2}[./-]\d{4}|\d{4}[./-]\d{1,2}))?\b")

# ---- WORD BOUNDARY CHECK --------------------------------
WORD_CHAR = re.compile(r"[A-Za-zÄÖÜäöüß]")

# ---- SELF INTRODUCED NAMES -------------------------------
# 1. "Mein Name ist Max Mustermann"
# 2. "My name is Max Mustermann"
# 3. "Name: Max Mustermann"

NAME_INTRO_PATTERNS = [
    re.compile(r"(?i)\bmein\s+name\s+ist\s+([A-ZÄÖÜ][A-Za-zÄÖÜäöüß'-]+(?:\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß'-]+){1,3})"),
    re.compile(r"(?i)\bmy\s+name\s+is\s+([A-Z][A-Za-z'-]+(?:\s+[A-Z][A-Za-z'-]+){1,3})"),
    re.compile(r"(?im)^\s*name\s*[:–-]\s*([A-ZÄÖÜ][A-Za-zÄÖÜäöüß'-]+(?:\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß'-]+){1,3})")
]

# ==========================================================
# UTIL
# ==========================================================

def is_word_boundary(text, s, e):
    return (s == 0 or not WORD_CHAR.match(text[s-1])) and \
           (e == len(text) or not WORD_CHAR.match(text[e]))

def digits(s):
    return sum(c.isdigit() for c in s)

def looks_like_phone(s):
    if DATE_RE.search(s): return False
    return digits(s) >= 8

# ==========================================================
# ANONYMIZER
# ==========================================================

class CVAnonymizer:

    def __init__(self):
        print("Loading multilingual NER:", MODEL)
        tok = AutoTokenizer.from_pretrained(MODEL)
        mod = AutoModelForTokenClassification.from_pretrained(MODEL)
        self.ner = pipeline("token-classification", model=mod, tokenizer=tok, aggregation_strategy="simple")

    def anonymize(self, text: str) -> str:

        # ---- REGEX PASS -----------------------------------
        text = EMAIL_RE.sub("<EMAIL>", text)
        text = URL_RE.sub("<URL>", text)

        def phone_repl(m):
            return "<PHONE>" if looks_like_phone(m.group(0)) else m.group(0)

        text = PHONE_CANDIDATE_RE.sub(phone_repl, text)

        # ---- SELF NAME PASS -------------------------------
        for rx in NAME_INTRO_PATTERNS:
            text = rx.sub(lambda m: m.group(0).replace(m.group(1), "<PERSON>"), text)

        # ---- NER PASS -------------------------------------
        spans = []
        for e in self.ner(text):
            if e["entity_group"] != "PER":
                continue
            s, e2 = int(e["start"]), int(e["end"])
            if is_word_boundary(text, s, e2):
                spans.append((s, e2, e["score"]))

        # sort longest & highest confidence first
        spans.sort(key=lambda x: (x[0], -(x[1]-x[0]), -x[2]))

        for s, e2, _ in reversed(spans):
            text = text[:s] + "<PERSON>" + text[e2:]

        # cleanup
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text

# ==========================================================
# DEMO
# ==========================================================

if __name__ == "__main__":
    sample = """
    Mein Name ist Robert Mustermann.
    Kontakt: robert@test.de, +49 170 1234567
    
    01-2020 - 10-2025
    Netempire AG in Köln.
    Senior software engineer

    
    01-2010 - 10-2020
    Netflix NYC.
    Senior Forscher
    """

    print(CVAnonymizer().anonymize(sample))

