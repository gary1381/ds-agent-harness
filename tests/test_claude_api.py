"""The API key loader: environment first, then the key file; no network."""
from __future__ import annotations

from dsharness.bench import claude_api as C


def test_key_from_env_then_file(tmp_path, monkeypatch):
    for k in C.KEYS:
        monkeypatch.delenv(k, raising=False)
    f = tmp_path / "env"
    f.write_text("OTHER=1\nexport CLAUDE_API_KEY='from-file'\n")
    monkeypatch.setattr(C, "KEY_FILE", f)
    assert C.api_key() == "from-file"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    assert C.api_key() == "from-env"


def test_no_key_says_where(tmp_path, monkeypatch):
    for k in C.KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(C, "KEY_FILE", tmp_path / "missing")
    try:
        C.api_key()
    except C.ClaudeAPIError as e:
        assert "missing" in str(e)
    else:
        raise AssertionError("expected ClaudeAPIError")
