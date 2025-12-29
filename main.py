#!/usr/bin/env python3
import argparse
import json
import logging
import shutil
import subprocess
from typing import Optional
from pathlib import Path

from cv_text_scrubber_de import CvAnonymizer, AnonymizeConfig
from llm_client import create_llm_client
from mymupdf_extractor import extract_plain_text

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def _check_tool(name: str) -> Optional[str]:
    return shutil.which(name)


def _run_cmd(cmd: list, *, check: bool = True):
    return subprocess.run(cmd, check=check)


def main(argv=None):
    p = argparse.ArgumentParser(description="Extract structured text and redact PII with optional preprocessing")
    p.add_argument("path", help="Path to input file (PDF or DOCX)")
    p.add_argument("-o", "--out", help="Output JSON file (default stdout)")
    p.add_argument("--pandoc-ast", action="store_true", help="Export pandoc JSON AST for DOCX (requires pandoc installed)")
    p.add_argument("--redact", action="store_true", help="Enable PII redaction (default: disabled)")
    p.add_argument("--llm", action="store_true", help="Call local Ollama LLM to post-process structured output")
    p.add_argument("--llm-model", default="ollama/llama2", help="LLM model name for local Ollama")
    p.add_argument("--llm-kind", default="ollama", help="LLM provider kind (ollama|openai)")
    p.add_argument("--system-prompt", default="cv_extraction_system_prompt.txt", help="Path to system prompt file")
    p.add_argument("--user-prompt", default="cv_extraction_user_prompt.txt", help="Path to user prompt template file")
    p.add_argument("--schema", default="schema.json", help="Path to JSON schema to present to LLM")
    args = p.parse_args(argv)

    src = Path(args.path)
    if not src.exists():
        raise SystemExit(f"Input not found: {src}")

    logger.info(f"Processing file: {src}")
    
    try:

        logger.info("Starting conversion...")

        text_for_json = extract_plain_text(src)

        # Apply redaction only if --redact flag is set
        if args.redact:
            logger.info("Redacting PII using header-based approach...")
            anon = CvAnonymizer(AnonymizeConfig(debug=True, url_policy="keep_domain"))
            text_for_json = anon.anonymize(text_for_json, preferred_language="de")
        else:
            logger.info("PII redaction skipped (use --redact to enable)")

        logger.info(text_for_json)

        llm_response = None
        if args.llm:
            logger.info("Starting LLM processing...")
            logger.info(f"LLM kind: {args.llm_kind}, model: {args.llm_model}")
            
            sys_prompt_path = Path(args.system_prompt)
            usr_prompt_path = Path(args.user_prompt)
            json_schema_path = Path(args.schema)
            
            if not sys_prompt_path.exists():
                logger.warning(f"System prompt file not found: {sys_prompt_path}")
            else:
                logger.info(f"Using system prompt: {sys_prompt_path}")

            if not usr_prompt_path.exists():
                logger.warning(f"User prompt file not found: {sys_prompt_path}")
            else:
                logger.info(f"Using user prompt: {usr_prompt_path}")

            if not json_schema_path.exists():
                logger.warning(f"Schema file not found: {json_schema_path}")
            else:
                logger.info(f"Using schema: {json_schema_path}")
            
            try:
                logger.info("Creating LLM client...")
                client = create_llm_client(
                    kind=args.llm_kind,
                    system_prompt_path=args.system_prompt,
                    user_prompt_path=args.user_prompt,
                    schema_path=args.schema,
                    model=args.llm_model,
                    max_length=4096,
                    max_retries=3,
                )
                logger.info(f"LLM client created: {type(client).__name__}")
                
                logger.info("Calling LLM to generate structured output...")
                llm_response = client.generate_structured(text_for_json)
                logger.info("LLM processing completed successfully")
            except Exception as e:
                logger.error(f"LLM call failed: {e}", exc_info=True)
                llm_response = f"LLM call error: {e}"

        # If LLM was used, the LLM response is the primary output (converted CV JSON)
        # Otherwise, output the raw extracted structured data
        # If LLM was used, the LLM response is the primary output (converted CV JSON)
        # Otherwise, output the raw extracted structured data
        if llm_response is not None:
            try:
                # LLM response is the converted CV JSON - this is what we want as output
                llm_json = json.loads(llm_response)
                out = llm_json
                logger.info("Using LLM-generated JSON as primary output")
            except json.JSONDecodeError:
                # If LLM failed, fall back to raw data and include error
                logger.warning("LLM response is not valid JSON, using raw extracted data")
                out = {
                    "structured": "",
                    "diagnostics": "",
                    "llm_error": llm_response,
                }
        else:
            # No LLM processing - output raw extracted data
            out = {
                "structured": "",
                "diagnostics": "",
            }

        logger.info("Generating output...")

        data = json.dumps(out, ensure_ascii=False, indent=2)
        if args.out:
            logger.info(f"Writing output to: {args.out}")
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(data)
        else:
            print(data)

        if llm_response is not None:
            print('\n---- LLM response (also in output file) ----')
            print(llm_response)

    finally:
        # cleanup tmp files
        logger.info("Processing completed successfully")

if __name__ == "__main__":
    main()
