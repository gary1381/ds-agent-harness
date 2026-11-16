"""One call to a helper model by API key: Haiku 5.5 or GPT-6 Luna (Gary 2026-11-08: Haiku or Luna by API for every
workflow). Replaces the sandboxed CLI calls that experiments up to E3b v2 used (`claude -p`, `codex exec`) for
meaning checks, labels, probes and reviews that need no tools and no files written. Every call is charged to the
daily budget (budget.py). Agents that run tools (the Claude Code and Codex agents under test) are not helpers and do
not come through here.

    text = ask("...")                            # Luna; "haiku" for a Claude-family check
    text, work = ask_files("...", "luna", d, files={"sheet.md": "..."})   # files are inlined in the prompt
"""
from __future__ import annotations

from pathlib import Path

from dsharness.bench import claude_api, openai_api

MODELS = {"haiku": "claude-haiku-5-5", "luna": "gpt-6-luna"}


def ask(prompt: str, model: str = "luna", *, log_dir: Path | None = None, purpose: str = "",
        max_tokens: int = 8192, effort: str = "high") -> str:
    if model == "haiku":
        return claude_api.ask(prompt, model=MODELS[model], max_tokens=max_tokens, log_dir=log_dir, purpose=purpose)[0]
    if model == "luna":
        return openai_api.ask(prompt, model=MODELS[model], effort=effort, log_dir=log_dir, purpose=purpose)[0]
    raise ValueError(f"unknown helper model {model!r}: use one of {sorted(MODELS)}")


def ask_files(prompt: str, model: str, d: Path, files: dict[str, str] | None = None, **kw) -> tuple[str, Path]:
    """Like luna.ask(prompt, d, files) for work that only reads: the files are written under d/work (so the caller's
    paths still exist) and also shown to the model inline. Returns (text, d/work)."""
    work = d / "work"
    work.mkdir(parents=True, exist_ok=False)
    parts = []
    for name, text in (files or {}).items():
        (work / name).parent.mkdir(parents=True, exist_ok=True)
        (work / name).write_text(text, encoding="utf-8")
        parts.append(f"=== FILE {name} ===\n{text}\n=== END {name} ===")
    full = prompt if not parts else prompt + "\n\n" + "\n\n".join(parts)
    return ask(full, model, log_dir=d / "api", **kw), work
