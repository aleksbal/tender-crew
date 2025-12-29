"""LLM client for transforming structured document data to JSON.

Provides a simple interface to call LLM providers (Ollama, OpenAI) for
single query/response transformations. Not conversational - just sends
system and user prompts and returns the JSON response.
"""
from typing import Optional, Dict
import requests
import os
import json
import logging
import time
from pathlib import Path
from jsonschema import ValidationError

logger = logging.getLogger(__name__)

try:
    import openai
except Exception:
    openai = None


class LLMClient:
    def __init__(self, system_prompt_path: str | Path | None = None, user_prompt_path: str | Path | None = None):
        """
        Initialize LLM client with system prompt and user prompt template.
        Both are loaded once at construction time since they never change.
        """
        # Load system prompt
        self.system_prompt = ""
        if system_prompt_path:
            spath = Path(system_prompt_path)
            if spath.exists():
                self.system_prompt = spath.read_text(encoding="utf-8")
                logger.info(f"Loaded system prompt from: {spath}")
            else:
                logger.warning(f"System prompt file not found: {spath}")
        
        # Load user prompt template
        self.user_prompt_template = ""
        if user_prompt_path:
            upath = Path(user_prompt_path)
            if upath.exists():
                self.user_prompt_template = upath.read_text(encoding="utf-8")
                logger.info(f"Loaded user prompt template from: {upath}")
            else:
                logger.warning(f"User prompt template file not found: {upath}")
    
    def _call_llm_api(self, user_prompt: str, model: str, max_length: int = 4096) -> str:
        """
        Internal method to call the LLM API with system and user prompts.
        Implemented by subclasses.
        """
        raise NotImplementedError()

    def generate_structured(
        self,
        document_text: str,
        schema_path: str | Path | None = None,
        model: str = "ollama/llama2",
        max_length: int = 4096,
        max_retries: int = 3,
    ) -> str:
        """
        Extract structured data from CV/resume and convert to JSON using LLM.
        Uses template-based user prompt with document text and schema injected.
        
        Returns the final validated JSON string (serialized). Raises on fatal errors.
        """
        # Load schema
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
            else:
                logger.warning(f"Schema file not found: {schema_path}")

        logger.info(f"Extracted document text for LLM (length: {len(document_text)} characters)")
        logger.info("=" * 80)
        logger.info("CV TEXT BEING SENT TO LLM:")
        logger.info("=" * 80)
        # Log the document text (truncate if too long for readability)
        if len(document_text) > 5000:
            logger.info(f"{document_text}...\n[Total length: {len(document_text)} characters]")
        else:
            logger.info(document_text)
        logger.info("=" * 80)
        
        # Load user prompt template and replace placeholders with schema and document text
        if not self.user_prompt_template:
            raise RuntimeError("User prompt template not loaded. Ensure user_prompt.txt exists or provide user_prompt_path.")
        
        user_prompt = self.user_prompt_template.format(
            DOCUMENT_TEXT=document_text,
            SCHEMA_TEXT=schema_text
        )

        attempt = 0
        last_error = None
        
        logger.info(f"Starting LLM generation (max_retries={max_retries})")
        logger.info(f"Document text length: {len(document_text)} characters")

        while attempt < max_retries:
            attempt += 1
            logger.info(f"Attempt {attempt}/{max_retries} - Calling LLM with model: {model}")
            try:
                # Call LLM API with system prompt (loaded in constructor) and user prompt
                raw = self._call_llm_api(user_prompt, model=model, max_length=max_length)
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
                m = re.search(r"\{[\s\S]*}", raw)
                if m:
                    try:
                        parsed = json.loads(m.group(0))
                    except json.JSONDecodeError:
                        parsed = None

            if parsed is None:
                logger.warning(f"Attempt {attempt}: Failed to parse JSON from response")
                if attempt < max_retries:
                    # ask the model to return only corrected JSON on next attempt
                    user_prompt += "\n\nPlease output only a single JSON object that conforms to the provided schema. Do not include any explanatory text."
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
                    """
                    jsonschema_validate(instance=parsed, schema=schema)
                    """
                    logger.info("Schema validation passed!")
                    return json.dumps(parsed, ensure_ascii=False)
                except ValidationError as e:
                    error_msg = e.message if hasattr(e, 'message') else str(e)
                    logger.warning(f"Attempt {attempt}: Schema validation failed: {error_msg}")
                    if attempt < max_retries:
                        user_prompt += f"\n\nThe previous JSON did not validate: {error_msg}. Please produce a corrected JSON only."
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
    def __init__(self, url: Optional[str] = None, timeout: int = 60, system_prompt_path: str | Path | None = None, user_prompt_path: str | Path | None = None):
        # Initialize base class with system prompt and user prompt template
        super().__init__(system_prompt_path=system_prompt_path, user_prompt_path=user_prompt_path)
        # Default local Ollama HTTP API endpoint
        base_url = url or "http://127.0.0.1:11434"
        self.generate_url = f"{base_url}/api/generate"
        self.timeout = timeout

    def _call_llm_api(self, user_prompt: str, model: str = "ollama/llama2", max_length: int = 8192) -> str:
        model_name = model.replace("ollama/", "") if model.startswith("ollama/") else model

        body = {
            "model": model_name,
            "prompt": user_prompt,
            "stream": False,
            "num_predict": max_length,
            **({"system": self.system_prompt} if self.system_prompt else {}),
            "format": "json",
            "options": {
                "temperature": 0,
                "top_p": 1.0,
                "seed": 1234,
                "num_ctx": 8192
            },
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


def create_llm_client(kind: str = "ollama", system_prompt_path: str | Path | None = None, user_prompt_path: str | Path | None = None, **kwargs) -> LLMClient:
    """
    Create LLM client instance. System prompt and user prompt template are loaded once at construction time.
    """
    kind = (kind or "ollama").lower()
    if kind == "ollama":
        return OllamaClient(system_prompt_path=system_prompt_path, user_prompt_path=user_prompt_path, **kwargs)
    if kind == "openai":
        return OpenAIClient(system_prompt_path=system_prompt_path, user_prompt_path=user_prompt_path, **kwargs)
    raise ValueError(f"Unknown LLM client kind: {kind}")


class OpenAIClient(LLMClient):
    def __init__(self, api_key: Optional[str] = None, timeout: int = 60, system_prompt_path: str | Path | None = None, user_prompt_path: str | Path | None = None):
        # Initialize base class with system prompt and user prompt template
        super().__init__(system_prompt_path=system_prompt_path, user_prompt_path=user_prompt_path)
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

    def _call_llm_api(self, user_prompt: str, model: str = "gpt-4o-mini", max_length: int = 2048) -> str:
        try:
            # Build messages with system prompt (loaded in constructor) and user prompt
            messages = []
            if self.system_prompt:
                messages.append({"role": "system", "content": self.system_prompt})
            messages.append({"role": "user", "content": user_prompt})
            
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
