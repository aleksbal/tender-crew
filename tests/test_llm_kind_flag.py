import json
from pathlib import Path


def test_llm_kind_flag_uses_factory(monkeypatch, tmp_path):
    # record if factory called
    called = {"called": False, "kind": None}

    class FakeClient:
        def __init__(self):
            self.generated = False

        def generate_structured(self, document_text: str):
            self.generated = True
            return json.dumps({"ok": True})

    def fake_factory(kind="ollama", system_prompt_path=None, user_prompt_path=None, **kwargs):
        called["called"] = True
        called["kind"] = kind
        return FakeClient()

    # monkeypatch the factory used by main
    import text_2_json_cli as main_mod
    monkeypatch.setattr(main_mod, "create_llm_client", fake_factory)

    out_file = tmp_path / "out.json"
    # call main with --llm and a chosen kind
    main_mod.main(["realistic_cv.docx", "--llm", "--llm-kind", "openai", "-o", str(out_file)])

    assert called["called"] is True
    assert called["kind"] == "openai"

    # output file should exist and be valid JSON
    # When LLM is used, output is the LLM response directly (not wrapped in "structured")
    data = json.loads(Path(out_file).read_text(encoding="utf-8"))
    assert data is not None
