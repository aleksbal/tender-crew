"""LLM client abstraction.

Provides a minimal interface to call a local Ollama instance. Implementations
should expose `generate(prompt, model, **kwargs) -> str`.

You can add additional provider classes (OpenAI, local HTTP, etc.) and a
factory `create_llm_client()` to choose at runtime.
"""
from typing import Optional, Dict
import requests
import os
import json
import logging
import time
from pathlib import Path
from jsonschema import validate as jsonschema_validate, ValidationError

logger = logging.getLogger(__name__)

try:
    import openai
except Exception:
    openai = None


class LLMClient:
    def generate(self, prompt: str, model: str, max_length: int = 2048, system_prompt: Optional[str] = None) -> str:
        raise NotImplementedError()

    def _extract_readable_text(self, structured: Dict) -> str:
        """
        Extract readable text from structured document data while preserving
        important structural and spatial information that helps the LLM understand
        document layout and context.
        
        Preserves:
        - Page boundaries
        - Block structure (groups of related text)
        - Spatial hints (header area, column layout)
        - Section markers
        - Reading order (already in structured data)
        """
        pages = structured.get("pages", [])
        if not pages:
            return ""
        
        text_parts = []
        for page_idx, page in enumerate(pages):
            page_num = page.get("page_number", page_idx + 1)
            page_width = page.get("width")
            page_height = page.get("height")
            blocks = page.get("blocks", [])
            
            # Add page marker
            page_marker = f"\n--- Page {page_num} ---\n"
            text_parts.append(page_marker)
            
            # Analyze spatial layout for hints
            header_threshold = page_height * 0.15 if page_height else None  # Top 15% is likely header
            mid_x = page_width / 2 if page_width else None
            
            for block_idx, block in enumerate(blocks):
                block_text = block.get("text", "").strip()
                if not block_text:
                    continue
                
                bbox = block.get("bbox")
                spatial_hints = []
                
                # Add spatial hints if bbox is available
                if bbox and len(bbox) >= 4:
                    x0, y0, x1, y1 = bbox[0], bbox[1], bbox[2], bbox[3]
                    
                    # Header area hint (top of page)
                    if header_threshold and y0 < header_threshold:
                        spatial_hints.append("[HEADER_AREA]")
                    
                    # Column hints (for multi-column layouts)
                    if mid_x:
                        if x1 < mid_x * 0.7:
                            spatial_hints.append("[LEFT_COLUMN]")
                        elif x0 > mid_x * 1.3:
                            spatial_hints.append("[RIGHT_COLUMN]")
                
                # Add block marker to preserve structure
                if spatial_hints:
                    block_marker = " ".join(spatial_hints) + " "
                else:
                    block_marker = ""
                
                # Preserve block boundaries with a subtle marker
                # (empty line between blocks, but add hint if in header)
                if block_idx > 0:
                    text_parts.append("")  # Block separator
                
                # Add the block text with spatial hints if any
                if block_marker:
                    # Only add hint once at the start of block
                    lines = block_text.split("\n")
                    if lines:
                        lines[0] = block_marker + lines[0]
                        block_text = "\n".join(lines)
                
                text_parts.append(block_text)
            
            # Page separator (except for last page)
            if page_idx < len(pages) - 1:
                text_parts.append("\n")
        
        return "\n".join(text_parts).strip()

    def generate_structured(
        self,
        structured: Dict,
        system_prompt_path: str | Path | None = None,
        schema_path: str | Path | None = None,
        model: str = "ollama/llama2",
        max_length: int = 2048,
        max_retries: int = 3,
    ) -> str:
        """
        Compose a prompt from the system prompt, schema, and structured data,
        call `generate`, try to parse JSON from the response, validate against
        the schema (if provided) and retry up to `max_retries` times asking the
        model to output only valid JSON.

        Returns the final validated JSON string (serialized). Raises on fatal errors.
        """
        spath = Path(system_prompt_path) if system_prompt_path else None
        system_prompt = spath.read_text(encoding="utf-8") if spath and spath.exists() else ""

        schema_text = ""
        schema = None
        if schema_path:
            sp = Path(schema_path)
            if sp.exists():
                schema_text = sp.read_text(encoding="utf-8")
                try:
                    schema = json.loads(schema_text)
                except Exception:
                    schema = None

        # Extract readable text from structured data instead of sending complex JSON
        document_text = self._extract_readable_text(structured)
        logger.info(f"Extracted document text for LLM (length: {len(document_text)} characters)")
        logger.info("=" * 80)
        logger.info("CV TEXT BEING SENT TO LLM:")
        logger.info("=" * 80)
        # Log the document text (truncate if too long for readability)
        if len(document_text) > 5000:
            logger.info(f"{document_text[:5000]}...\n[TRUNCATED - Total length: {len(document_text)} characters]")
        else:
            logger.info(document_text)
        logger.info("=" * 80)
        
        # Compose a clear, structured user prompt
        user_prompt = f"""Extract and structure the following CV/resume document according to the JSON schema provided.

DOCUMENT TEXT:
{document_text}

DOCUMENT STRUCTURE HINTS:
- [HEADER_AREA] markers indicate text in the top portion of the page (typically contains name, contact info)
- [LEFT_COLUMN] and [RIGHT_COLUMN] markers indicate multi-column layout
- Page boundaries are marked with "--- Page N ---"
- Block boundaries are preserved (empty lines between blocks)
- Text is already in reading order

JSON SCHEMA:
{schema_text}

INSTRUCTIONS:
- Extract all information from the document text above
- Use spatial hints ([HEADER_AREA], column markers) to identify personal_info section
- Map it to the JSON schema structure exactly
- Use "YYYY-MM" format for all dates (e.g., "2020-03")
- For required fields with missing data, use empty strings "" or empty arrays []
- Output ONLY valid JSON, no markdown, no explanations
- Ensure all required fields from the schema are present"""

        attempt = 0
        last_error = None
        last_response = None
        
        logger.info(f"Starting LLM generation (max_retries={max_retries})")
        logger.info(f"Document text length: {len(document_text)} characters")
        
        # Initialize prompt variable that will be updated on retries
        current_user_prompt = user_prompt
        
        while attempt < max_retries:
            attempt += 1
            logger.info(f"Attempt {attempt}/{max_retries} - Calling LLM with model: {model}")
            try:
                # Simple single call - combine system and user prompts
                raw = self.generate(current_user_prompt, model=model, max_length=max_length, system_prompt=system_prompt)
                logger.info(f"Received response (length: {len(raw)} characters)")
                last_response = raw
            except RuntimeError as e:
                # Re-raise runtime errors (connection, API errors) immediately
                raise
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    time.sleep(0.5 * attempt)  # Exponential backoff
                    continue
                else:
                    raise RuntimeError(f"LLM generate failed after {max_retries} attempts: {e}") from e

            parsed = None
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                import re
                # Try to extract JSON from response (may have extra text)
                m = re.search(r"\{[\s\S]*\}", raw)
                if m:
                    try:
                        parsed = json.loads(m.group(0))
                    except json.JSONDecodeError:
                        parsed = None

            if parsed is None:
                logger.warning(f"Attempt {attempt}: Failed to parse JSON from response")
                if attempt < max_retries:
                    # ask the model to return only corrected JSON on next attempt
                    current_user_prompt += "\n\nPlease output only a single JSON object that conforms to the provided schema. Do not include any explanatory text."
                    last_error = ValueError(f"LLM did not return valid JSON (attempt {attempt}/{max_retries})")
                    logger.info(f"Retrying with updated prompt...")
                    time.sleep(0.5 * attempt)
                    continue
                else:
                    raise RuntimeError(
                        f"LLM did not return valid JSON after {max_retries} attempts. "
                        f"Last response (first 500 chars): {last_response[:500] if last_response else 'None'}"
                    )

            if schema is not None:
                try:
                    logger.info("Validating response against schema...")
                    jsonschema_validate(instance=parsed, schema=schema)
                    logger.info("Schema validation passed!")
                    return json.dumps(parsed, ensure_ascii=False)
                except ValidationError as e:
                    error_msg = e.message if hasattr(e, 'message') else str(e)
                    logger.warning(f"Attempt {attempt}: Schema validation failed: {error_msg}")
                    if attempt < max_retries:
                        current_user_prompt += f"\n\nThe previous JSON did not validate: {error_msg}. Please produce a corrected JSON only."
                        last_error = e
                        logger.info(f"Retrying with validation error feedback...")
                        time.sleep(0.5 * attempt)
                        continue
                    else:
                        raise RuntimeError(
                            f"LLM response did not validate against schema after {max_retries} attempts. "
                            f"Last validation error: {e.message if hasattr(e, 'message') else str(e)}"
                        ) from e

            return json.dumps(parsed, ensure_ascii=False)

        # If we get here, all attempts failed
        if last_error is not None:
            raise RuntimeError(f"LLM generate_structured failed after {max_retries} attempts: {last_error}") from last_error
        raise RuntimeError(f"LLM generate_structured failed after {max_retries} attempts without specific error")


class OllamaClient(LLMClient):
    def __init__(self, url: Optional[str] = None, timeout: int = 60):
        # Default local Ollama HTTP API endpoint
        base_url = url or "http://127.0.0.1:11434"
        self.generate_url = f"{base_url}/api/generate"
        self.timeout = timeout

    def generate(self, prompt: str, model: str = "ollama/llama2",
                 max_length: int = 4096, system_prompt: Optional[str] = None) -> str:
        model_name = model.replace("ollama/", "") if model.startswith("ollama/") else model

        body = {
            "model": model_name,
            "prompt": prompt,               # user content only
            "stream": False,
            "num_predict": max_length,
            # Use Ollama's native fields instead of concatenating
            **({"system": system_prompt} if system_prompt else {}),
            # Ask for strict JSON output
            "format": "json",
            # Decoding & context knobs (tune as you like)
            "options": {
                "temperature": 0,
                # "num_ctx": 8192,          # raise if your schema + doc are long
                # "stop": ["```"]           # optional if you ever include markdown in inputs
            },
            # "keep_alive": "5m",           # optional: keep model warm between calls
        }

        try:
            resp = requests.post(self.generate_url, json=body, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            if "response" in data:
                return data["response"]
            raise ValueError(f"Unexpected Ollama response format: {list(data.keys())}")
        except requests.exceptions.ConnectionError as e:
            raise RuntimeError(f"Failed to connect to Ollama server at {self.generate_url}. Is Ollama running?") from e
        except requests.exceptions.Timeout as e:
            raise RuntimeError(f"Ollama request timed out after {self.timeout}s. The model may be too slow or the request too large.") from e
        except requests.exceptions.HTTPError as e:
            raise RuntimeError(f"Ollama API error: {e.response.status_code} - {e.response.text}") from e
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Failed to parse Ollama response as JSON: {e}") from e

def create_llm_client(kind: str = "ollama", **kwargs) -> LLMClient:
    kind = (kind or "ollama").lower()
    if kind == "ollama":
        return OllamaClient(**kwargs)
    if kind == "openai":
        return OpenAIClient(**kwargs)
    raise ValueError(f"Unknown LLM client kind: {kind}")


class OpenAIClient(LLMClient):
    def __init__(self, api_key: Optional[str] = None, timeout: int = 60):
        if openai is None:
            raise RuntimeError("openai package not available")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY not set")
        self.timeout = timeout
        
        # Try to use modern OpenAI client API (v1.0+)
        try:
            # Check if openai has Client class (v1.0+)
            if hasattr(openai, "OpenAI"):
                self.client = openai.OpenAI(api_key=self.api_key, timeout=self.timeout)
                self.use_client_api = True
            else:
                # Fallback to older API
                openai.api_key = self.api_key
                self.client = None
                self.use_client_api = False
        except Exception as e:
            raise RuntimeError(f"Failed to initialize OpenAI client: {e}") from e

    def generate(self, prompt: str, model: str = "gpt-4o-mini", max_length: int = 2048, system_prompt: Optional[str] = None) -> str:
        try:
            # Build messages with system prompt if provided
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})
            
            if self.use_client_api:
                # Modern OpenAI SDK (v1.0+)
                response = self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=max_length,
                )
                return response.choices[0].message.content
            else:
                # Legacy OpenAI SDK (< v1.0)
                resp = openai.ChatCompletion.create(
                    model=model,
                    messages=messages,
                    max_tokens=max_length,
                )
                return resp.choices[0].message.content
        except Exception as e:
            # Handle different OpenAI SDK error types if available
            error_type = type(e).__name__
            if "APIError" in error_type or "APIException" in error_type:
                raise RuntimeError(f"OpenAI API error: {e}") from e
            elif "APIConnectionError" in error_type or "ConnectionError" in error_type:
                raise RuntimeError(f"OpenAI connection error: {e}. Check your internet connection and API key.") from e
            elif "RateLimitError" in error_type or "RateLimit" in error_type:
                raise RuntimeError(f"OpenAI rate limit exceeded: {e}") from e
            else:
                raise RuntimeError(f"Error calling OpenAI API: {e}") from e
