#!/usr/bin/env python3
import json
import logging
from typing import Optional, TypedDict
from pathlib import Path

from pii_scrubber import TextAnonymizer, AnonymizeConfig
from llm_client import create_llm_client
from cv_text_extractor import extract_plain_text

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


class ConversionResult(TypedDict):
    """Result structure from text to JSON conversion."""
    file_name: str
    plain_txt: str
    obfusc: Optional[dict]  # Complete obfuscation result with original_text, obfuscated_text, config, obfuscations
    llm_json: Optional[dict]


def convert_text_to_json(
    path: str,
    pii_limit: Optional[int] = None,
    redact: bool = False,
    llm_kind: str = "ollama",
    llm_model: str = "ollama/llama2",
    system_prompt: str = "cv_extraction_system_prompt.txt",
    user_prompt: str = "cv_extraction_user_prompt.txt",
    schema: str = "cv_schema.json",
) -> ConversionResult:
    """
    Convert a document (PDF or DOCX) to structured JSON format.
    
    Args:
        path: Path to input file (PDF or DOCX)
        pii_limit: Limit PII obfuscation to first N characters (0 = whole text, None = no obfuscation)
        redact: Legacy flag to obfuscate whole text (used if pii_limit is None)
        llm_kind: LLM provider kind (ollama|openai)
        llm_model: LLM model name
        system_prompt: Path to system prompt file
        user_prompt: Path to user prompt template file
        schema: Path to JSON schema file
        
    Returns:
        Dictionary with the following structure:
        - file_name: Name of the input file
        - plain_txt: Plain text (only if obfuscation was OFF, empty otherwise)
        - obfusc: Complete obfuscation result dict (only if obfuscation was ON, None otherwise).
                 Contains: original_text, obfuscated_text, config, obfuscations
        - llm_json: JSON result from LLM transformation (or error structure if LLM failed)
        
    Raises:
        FileNotFoundError: If input file doesn't exist
    """
    src = Path(path)
    if not src.exists():
        raise FileNotFoundError(f"Input not found: {src}")

    logger.info(f"Processing file: {src}")
    
    try:
        logger.info("Starting conversion...")

        # Extract plain text from document
        plain_text = extract_plain_text(src)
        
        # Initialize result structure
        result = {
            "file_name": src.name,
            "plain_txt": "",
            "obfusc": None,
            "llm_json": None,
        }

        # Determine which text to use for LLM processing
        text_for_json = plain_text

        # Apply redaction based on --pii-limit parameter
        # If --pii-limit is not provided: no obfuscation
        # If --pii-limit is provided with value 0: obfuscate whole text
        # If --pii-limit is provided with value N: obfuscate first N characters
        if pii_limit is not None:
            pii_limit_value = pii_limit if pii_limit > 0 else 0
            if pii_limit_value == 0:
                logger.info("Redacting PII from entire text...")
            else:
                logger.info(f"Redacting PII from first {pii_limit_value} characters...")
            anon = TextAnonymizer(AnonymizeConfig(
                debug=True,
                url_policy="keep_domain",
                pii_obfuscation_limit=pii_limit_value
            ))
            anonymize_result = anon.anonymize(plain_text, preferred_language="de")
            # Verify we got the obfuscated text from the result
            if "obfuscated_text" not in anonymize_result:
                raise ValueError("Anonymization result missing 'obfuscated_text' key")
            text_for_json = anonymize_result["obfuscated_text"]
            result["obfusc"] = anonymize_result
            logger.info(f"PII obfuscation completed: {len(anonymize_result['obfuscations'])} items obfuscated")
            logger.info(f"Text length: original={len(plain_text)}, obfuscated={len(text_for_json)}")
            # Verify obfuscation actually changed the text (unless no PII was found)
            if len(anonymize_result['obfuscations']) > 0 and text_for_json == plain_text:
                logger.warning("WARNING: Obfuscation was applied but text appears unchanged!")
        elif redact:
            # Legacy --redact flag: obfuscate whole text
            logger.info("Redacting PII from entire text (legacy --redact flag)...")
            anon = TextAnonymizer(AnonymizeConfig(debug=True, url_policy="keep_domain", pii_obfuscation_limit=0))
            anonymize_result = anon.anonymize(plain_text, preferred_language="de")
            # Verify we got the obfuscated text from the result
            if "obfuscated_text" not in anonymize_result:
                raise ValueError("Anonymization result missing 'obfuscated_text' key")
            text_for_json = anonymize_result["obfuscated_text"]
            result["obfusc"] = anonymize_result
            logger.info(f"PII obfuscation completed: {len(anonymize_result['obfuscations'])} items obfuscated")
            logger.info(f"Text length: original={len(plain_text)}, obfuscated={len(text_for_json)}")
            # Verify obfuscation actually changed the text (unless no PII was found)
            if len(anonymize_result['obfuscations']) > 0 and text_for_json == plain_text:
                logger.warning("WARNING: Obfuscation was applied but text appears unchanged!")
        else:
            logger.info("PII redaction skipped (use --pii-limit to enable)")
            result["plain_txt"] = plain_text

        logger.info(f"Text to be sent to LLM (first 200 chars): {text_for_json[:200]}")

        logger.info("Starting LLM processing...")
        logger.info(f"LLM kind: {llm_kind}, model: {llm_model}")

        sys_prompt_path = Path(system_prompt)
        usr_prompt_path = Path(user_prompt)
        json_schema_path = Path(schema)

        if not sys_prompt_path.exists():
            logger.warning(f"System prompt file not found: {sys_prompt_path}")
        else:
            logger.info(f"Using system prompt: {sys_prompt_path}")

        if not usr_prompt_path.exists():
            logger.warning(f"User prompt file not found: {usr_prompt_path}")
        else:
            logger.info(f"Using user prompt: {usr_prompt_path}")

        if not json_schema_path.exists():
            logger.warning(f"Schema file not found: {json_schema_path}")
        else:
            logger.info(f"Using schema: {json_schema_path}")

        llm_response = None
        try:
            logger.info("Creating LLM client...")
            client = create_llm_client(
                kind=llm_kind,
                system_prompt_path=system_prompt,
                user_prompt_path=user_prompt,
                schema_path=schema,
                model=llm_model,
                max_length=4096,
                max_retries=3,
            )
            logger.info(f"LLM client created: {type(client).__name__}")

            logger.info("Calling LLM to generate structured output...")
            # Ensure we're using obfuscated text if obfuscation was applied
            if result["obfusc"] is not None:
                # Always use the obfuscated text directly from the result to ensure correctness
                text_for_json = result["obfusc"]["obfuscated_text"]
                logger.info(f"Using obfuscated text for LLM (length: {len(text_for_json)})")
                logger.info(f"Obfuscation applied: {len(result['obfusc']['obfuscations'])} items obfuscated")
            else:
                logger.info(f"Using plain text for LLM (no obfuscation applied, length: {len(text_for_json)})")
            logger.info(f"Sending text to LLM (first 200 chars): {text_for_json[:200]}")
            llm_response = client.generate_structured(text_for_json)
            logger.info("LLM processing completed successfully")
        except Exception as e:
            logger.error(f"LLM call failed: {e}", exc_info=True)
            llm_response = f"LLM call error: {e}"

        # Process LLM response
        if llm_response is not None:
            try:
                # LLM response is the converted CV JSON - this is what we want as output
                llm_json = json.loads(llm_response)
                result["llm_json"] = llm_json
                logger.info("Using LLM-generated JSON as primary output")
            except json.JSONDecodeError:
                # If LLM failed, include error in result
                logger.warning("LLM response is not valid JSON, including error in result")
                result["llm_json"] = {
                    "structured": "",
                    "diagnostics": "",
                    "llm_error": llm_response,
                }
        else:
            # No LLM processing - set empty structure
            result["llm_json"] = {
                "structured": "",
                "diagnostics": "",
            }

        logger.info("Conversion completed successfully")
        return result

    except Exception as e:
        logger.error(f"Conversion failed: {e}", exc_info=True)
        raise
