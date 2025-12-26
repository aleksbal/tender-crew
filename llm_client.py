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
import time
from pathlib import Path
from jsonschema import validate as jsonschema_validate, ValidationError

try:
    import openai
except Exception:
    openai = None


class LLMClient:
    def generate(self, prompt: str, model: str, max_length: int = 2048) -> str:
        raise NotImplementedError()

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
        prompt = spath.read_text(encoding="utf-8") if spath and spath.exists() else ""

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

        composed = f"{prompt}\n\nSchema:\n{schema_text}\n\nData:\n{json.dumps(structured, ensure_ascii=False)}"

        attempt = 0
        last_error = None
        while attempt < max_retries:
            attempt += 1
            try:
                raw = self.generate(composed, model=model, max_length=max_length)
            except Exception as e:
                last_error = e
                break

            parsed = None
            try:
                parsed = json.loads(raw)
            except Exception:
                import re

                m = re.search(r"\{[\s\S]*\}", raw)
                if m:
                    try:
                        parsed = json.loads(m.group(0))
                    except Exception:
                        parsed = None

            if parsed is None:
                # ask the model to return only corrected JSON on next attempt
                composed += "\n\nPlease output only a single JSON object that conforms to the provided schema. Do not include any explanatory text."
                last_error = ValueError("LLM did not return valid JSON")
                time.sleep(0.5)
                continue

            if schema is not None:
                try:
                    jsonschema_validate(instance=parsed, schema=schema)
                    return json.dumps(parsed, ensure_ascii=False)
                except ValidationError as e:
                    composed += f"\n\nThe previous JSON did not validate: {e.message}. Please produce a corrected JSON only."
                    last_error = e
                    time.sleep(0.5)
                    continue

            return json.dumps(parsed, ensure_ascii=False)

        # If we get here, all attempts failed
        if last_error is not None:
            raise last_error
        raise RuntimeError("LLM generate_structured failed without specific error")


class OllamaClient(LLMClient):
    def __init__(self, url: Optional[str] = None, timeout: int = 10):
        # Default local Ollama HTTP API endpoint
        self.url = url or "http://127.0.0.1:11434/api/generate"
        self.timeout = timeout

    def generate(self, prompt: str, model: str = "ollama/llama2", max_length: int = 2048) -> str:
        body = {
            "model": model,
            "prompt": prompt,
            "max_length": max_length,
        }
        resp = requests.post(self.url, json=body, timeout=self.timeout)
        resp.raise_for_status()
        # Return raw text body; callers may parse JSON if needed
        return resp.text


def create_llm_client(kind: str = "ollama", **kwargs) -> LLMClient:
    kind = (kind or "ollama").lower()
    if kind == "ollama":
        return OllamaClient(**kwargs)
    if kind == "openai":
        return OpenAIClient(**kwargs)
    raise ValueError(f"Unknown LLM client kind: {kind}")


class OpenAIClient(LLMClient):
    def __init__(self, api_key: Optional[str] = None, timeout: int = 10):
        if openai is None:
            raise RuntimeError("openai package not available")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY not set")
        openai.api_key = self.api_key
        self.timeout = timeout

    def generate(self, prompt: str, model: str = "gpt-4o-mini", max_length: int = 2048) -> str:
        # Use ChatCompletion-like interface; adjust as needed for your openai SDK version
        try:
            resp = openai.ChatCompletion.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_length,
            )
            # choose first choice text
            return resp.choices[0].message.content
        except Exception as e:
            raise
