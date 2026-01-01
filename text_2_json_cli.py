#!/usr/bin/env python3
import argparse
import json
import logging
import sys
from pathlib import Path

from text_2_json_service import convert_text_to_json

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def main(argv=None):
    p = argparse.ArgumentParser(description="Extract structured text and redact PII with optional preprocessing")
    p.add_argument("path", help="Path to input file (PDF or DOCX)")
    p.add_argument("-o", "--out", help="Output JSON file (default stdout)")
    p.add_argument("--redact", action="store_true", help="Enable PII redaction (default: disabled)")
    p.add_argument("--pii-limit", type=int, metavar="N", help="Limit PII obfuscation to first N characters (0 = whole text, omit = no obfuscation)")
    p.add_argument("--llm-model", default="ollama/llama2", help="LLM model name for local Ollama")
    p.add_argument("--llm-kind", default="ollama", help="LLM provider kind (ollama|openai)")
    p.add_argument("--system-prompt", default="cv_extraction_system_prompt.txt", help="Path to system prompt file")
    p.add_argument("--user-prompt", default="cv_extraction_user_prompt.txt", help="Path to user prompt template file")
    p.add_argument("--schema", default="cv_schema.json", help="Path to JSON schema to present to LLM")
    args = p.parse_args(argv)

    try:
        # Call service to perform conversion
        result = convert_text_to_json(
            path=args.path,
            pii_limit=args.pii_limit,
            redact=args.redact,
            llm_kind=args.llm_kind,
            llm_model=args.llm_model,
            system_prompt=args.system_prompt,
            user_prompt=args.user_prompt,
            schema=args.schema,
        )

        # Format and output result
        data = json.dumps(result, ensure_ascii=False, indent=2)
        if args.out:
            logger.info(f"Writing output to: {args.out}")
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(data)
        else:
            print(data)

    except FileNotFoundError as e:
        logger.error(str(e))
        sys.exit(1)
    except Exception as e:
        logger.error(f"Conversion failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
