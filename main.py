#!/usr/bin/env python3
import argparse
import json
from typing import Tuple, Dict

from extract_text import extract_text_structured
from redactor import redact_structured


def main(argv=None):
    p = argparse.ArgumentParser(description="Extract structured text and redact PII")
    p.add_argument("path", help="Path to input file (PDF or DOCX)")
    p.add_argument("-o", "--out", help="Output JSON file (default stdout)")
    args = p.parse_args(argv)

    structured, diag = extract_text_structured(args.path)
    redacted_struct, redactions = redact_structured(structured)

    out = {
        "structured": redacted_struct,
        "diagnostics": diag.__dict__ if hasattr(diag, "__dict__") else {},
        "redactions": redactions,
    }

    data = json.dumps(out, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(data)
    else:
        print(data)


if __name__ == "__main__":
    main()
