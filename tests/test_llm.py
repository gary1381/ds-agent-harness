from pathlib import Path

import pytest

from dsharness.bench import llm


def test_ask_routes_by_model(monkeypatch):
    seen = []
    monkeypatch.setattr(llm.claude_api, "ask", lambda p, **k: seen.append(("claude", k["model"])) or ("c", {}))
    monkeypatch.setattr(llm.openai_api, "ask", lambda p, **k: seen.append(("openai", k["model"])) or ("o", {}))
    assert llm.ask("x", "haiku") == "c" and llm.ask("x", "luna") == "o"
    assert seen == [("claude", "claude-haiku-5-5"), ("openai", "gpt-6-luna")]
    with pytest.raises(ValueError):
        llm.ask("x", "sonnet")


def test_ask_files_inlines_and_writes(monkeypatch, tmp_path: Path):
    got = {}
    monkeypatch.setattr(llm.openai_api, "ask", lambda p, **k: got.update(prompt=p) or ("ok", {}))
    text, work = llm.ask_files("Review.", "luna", tmp_path / "r", files={"a/b.md": "hello"})
    assert text == "ok" and (work / "a/b.md").read_text() == "hello"
    assert "=== FILE a/b.md ===\nhello" in got["prompt"]
