import json
from pathlib import Path


def test_generate_structured_retries(monkeypatch, tmp_path):
    # Prepare fake client that returns invalid JSON first, then valid JSON
    calls = {"count": 0}

    from llm_client import LLMClient

    class FakeClient(LLMClient):
        def __init__(self, system_prompt_path=None, user_prompt_path=None):
            super().__init__(system_prompt_path=system_prompt_path, user_prompt_path=user_prompt_path)
            self.call_count = 0
            # Set a dummy user prompt template if none was loaded
            if not self.user_prompt_template:
                self.user_prompt_template = "DOCUMENT TEXT: {DOCUMENT_TEXT}\nJSON SCHEMA: {SCHEMA_TEXT}"

        def _call_llm_api(self, user_prompt: str) -> str:
            self.call_count += 1
            # first call -> invalid JSON
            if self.call_count == 1:
                return "I am not JSON"
            # second call -> valid JSON that conforms to cv_schema.json (CV format)
            return json.dumps({
                "personal_info": {
                    "first_name": "Test",
                    "last_name": "User",
                    "phone": "",
                    "email": ""
                },
                "summary": "",
                "experience": [],
                "education": [],
                "skills": {
                    "programming_languages": [],
                    "technologies": [],
                    "soft_skills": []
                },
                "projects": [],
                "certifications": [],
                "languages": []
            })

    # fake factory that returns our FakeClient and captures it
    created = {}

    def fake_factory(kind="ollama", system_prompt_path=None, user_prompt_path=None, **kwargs):
        client = FakeClient(system_prompt_path=system_prompt_path, user_prompt_path=user_prompt_path)
        created['client'] = client
        return client

    # patch main's create_llm_client
    import text_2_json_cli as main_mod
    monkeypatch.setattr(main_mod, "create_llm_client", fake_factory)

    out_file = tmp_path / "out.json"
    # run main with --llm; the fake client will be used and should be called twice
    main_mod.main(["realistic_cv.docx", "--llm", "--llm-kind", "ollama", "-o", str(out_file)])

    # Ensure the fake client was created and called more than once (retry happened)
    assert 'client' in created
    assert created['client'].call_count >= 2

    # Ensure output file exists - when LLM is used, output is the LLM response directly (CV JSON)
    out = json.loads(Path(out_file).read_text(encoding="utf-8"))
    # Should contain CV structure (personal_info, experience, etc.)
    assert 'personal_info' in out or 'experience' in out or 'summary' in out
