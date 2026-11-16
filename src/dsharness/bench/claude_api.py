"""Claude through the Anthropic Messages API, paid by API key (decision of 2026-11-08).

Stdlib only (urllib). The key comes from CLAUDE_API_KEY or ANTHROPIC_API_KEY in the environment, else from the
key file (DSH_CLAUDE_KEY_FILE, default ~/.config/dsharness/env). It is never written to a log, a run
directory or the repository. The base URL is fixed, so an agent session's ANTHROPIC_BASE_URL is never inherited.
Every call is checked against and charged to the DS Harness spend ledger (budget.py).

    text, usage = ask(prompt, model="claude-haiku-5-5", log_dir=d)
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request

from dsharness.bench import budget

API_URL = "https://api.anthropic.com/v1/messages"
VERSION = "2023-06-01"
KEYS = ("CLAUDE_API_KEY", "ANTHROPIC_API_KEY")
KEY_FILE = Path(os.environ.get("DSH_CLAUDE_KEY_FILE", Path.home() / ".config/dsharness/env"))
RETRY = {408, 409, 429, 500, 502, 503, 504, 529}


class ClaudeAPIError(RuntimeError):
    pass


def api_key() -> str:
    for k in KEYS:
        if os.environ.get(k):
            return os.environ[k]
    if KEY_FILE.exists():
        for line in KEY_FILE.read_text().splitlines():
            name, _, value = line.removeprefix("export ").partition("=")
            if name.strip() in KEYS and value.strip():
                return value.strip().strip("'\"")
    raise ClaudeAPIError(f"no Claude API key: set {KEYS[0]} or put it in {KEY_FILE}")


def cost(model: str, usage: dict) -> float:
    return budget.price(model, input=usage.get("input_tokens", 0), cached=usage.get("cache_read_input_tokens", 0),
                        cache_write=usage.get("cache_creation_input_tokens", 0), output=usage.get("output_tokens", 0))


def ask(prompt: str, *, model: str, max_tokens: int = 8192, system: str | None = None, temperature: float | None = None,
        log_dir: Path | None = None, purpose: str = "", timeout_s: int = 600, tries: int = 5) -> tuple[str, dict]:
    """One user turn; returns the text and the usage block. Transient errors are retried with backoff. With log_dir,
    the request (without the key) and the response are saved there."""
    budget.price(model)                                   # refuses a model without a price before spending
    budget.check()
    body = {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}
    if system:
        body["system"] = system
    if temperature is not None:
        body["temperature"] = temperature
    data = json.dumps(body).encode()
    headers = {"x-api-key": api_key(), "anthropic-version": VERSION, "content-type": "application/json"}
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "request.json").write_text(json.dumps(body, ensure_ascii=False, indent=1))
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(API_URL, data=data, headers=headers, method="POST"),
                                        timeout=timeout_s) as r:
                res = json.loads(r.read())
            break
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}: {e.read()[:300].decode(errors='replace')}"
            if e.code not in RETRY:
                raise ClaudeAPIError(last) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = repr(e)[:300]
        time.sleep(min(60, 2 ** i * 3))
    else:
        raise ClaudeAPIError(f"gave up after {tries} tries: {last}")
    budget.charge(model, cost(model, res.get("usage", {})), purpose)
    if log_dir is not None:
        (log_dir / "response.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
    text = "".join(b.get("text", "") for b in res.get("content", []) if b.get("type") == "text")
    return text, res.get("usage", {})
