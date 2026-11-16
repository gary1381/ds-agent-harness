"""GPT-6 Luna and GPT-6.1 Terra through the OpenAI Responses API, paid by API key (decision of 2026-11-08: Haiku or Luna by
API for every workflow; Terra as adjudicator).

Stdlib only (urllib). Key from OPENAI_API_KEY in the environment, else the key file (DSH_OPENAI_KEY_FILE, default
~/.config/dsharness/env); never logged. Fixed base URL. Every call is checked against and charged to the
DS Harness spend ledger (budget.py). The Codex agent itself runs through the Codex CLI, not through this module.

    text, usage = ask(prompt, model="gpt-6-luna", effort="high", log_dir=d, purpose="E3b labels")
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request

from dsharness.bench import budget

API_URL = "https://api.openai.com/v1/responses"
KEY_FILE = Path(os.environ.get("DSH_OPENAI_KEY_FILE", Path.home() / ".config/dsharness/env"))
RETRY = {408, 409, 429, 500, 502, 503, 504}


class OpenAIAPIError(RuntimeError):
    pass


def api_key() -> str:
    if os.environ.get("OPENAI_API_KEY"):
        return os.environ["OPENAI_API_KEY"]
    if KEY_FILE.exists():
        for line in KEY_FILE.read_text().splitlines():
            name, _, value = line.removeprefix("export ").partition("=")
            if name.strip() == "OPENAI_API_KEY" and value.strip():
                return value.strip().strip("'\"")
    raise OpenAIAPIError(f"no OpenAI API key: set OPENAI_API_KEY or put it in {KEY_FILE}")


def cost(model: str, usage: dict) -> float:
    cached = (usage.get("input_tokens_details") or {}).get("cached_tokens", 0)
    return budget.price(model, input=usage.get("input_tokens", 0) - cached, cached=cached,
                        output=usage.get("output_tokens", 0))


def ask(prompt: str, *, model: str = "gpt-6-luna", effort: str | None = "high", instructions: str | None = None,
        max_output_tokens: int = 32000, log_dir: Path | None = None, purpose: str = "", timeout_s: int = 1200,
        tries: int = 5) -> tuple[str, dict]:
    """One request; returns the output text and the usage block."""
    budget.check()
    body = {"model": model, "input": prompt, "max_output_tokens": max_output_tokens}
    if effort:
        body["reasoning"] = {"effort": effort}
    if instructions:
        body["instructions"] = instructions
    data = json.dumps(body).encode()
    headers = {"Authorization": "Bearer " + api_key(), "Content-Type": "application/json"}
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
                raise OpenAIAPIError(last) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = repr(e)[:300]
        time.sleep(min(60, 2 ** i * 3))
    else:
        raise OpenAIAPIError(f"gave up after {tries} tries: {last}")
    usage = res.get("usage", {})
    budget.charge(model, cost(model, usage), purpose)
    if log_dir is not None:
        (log_dir / "response.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
    if res.get("status") not in (None, "completed"):
        raise OpenAIAPIError(f"response {res.get('status')}: {res.get('incomplete_details')}")
    text = "".join(c.get("text", "") for o in res.get("output", []) if o.get("type") == "message"
                   for c in o.get("content", []) if c.get("type") == "output_text")
    return text, usage
