"""DS Harness API spend ledger and daily cap (decision of 2026-11-08).

Every API-billed call (claude_api, openai_api, a Claude Code agent run on the API key) is charged to an append-only
ledger outside the repository (DSH_SPEND_LEDGER, default ~/.local/state/dsharness/api_spend.jsonl), by local day.
`check()` refuses a call once the day's total, plus the caller's estimate, would pass the cap (DSH_DAILY_USD,
default 10). Prices are list prices per million tokens; a model without a price is refused.
"""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path

DAILY_USD = float(os.environ.get("DSH_DAILY_USD", "10"))
LEDGER = Path(os.environ.get("DSH_SPEND_LEDGER", Path.home() / ".local/state/dsharness/api_spend.jsonl"))

# USD per million tokens (Anthropic and OpenAI list prices; Terra checked 2026-11-08)
PRICES = {
    "claude-haiku-5-5": {"input": 0.10, "cached": 0.01, "cache_write": 0.125, "output": 0.50},
    "gpt-6-luna": {"input": 0.10, "cached": 0.01, "output": 0.50},
    "gpt-6.1-terra": {"input": 1.00, "cached": 0.05, "output": 5.00},
}


class BudgetError(RuntimeError):
    pass


def price(model: str, *, input: int = 0, cached: int = 0, cache_write: int = 0, output: int = 0) -> float:
    """Cost of one call; `input` excludes cached and cache-write tokens."""
    if model not in PRICES:
        raise BudgetError(f"no price for {model}: add it to budget.PRICES before using it")
    p = PRICES[model]
    return (input * p["input"] + cached * p["cached"] + cache_write * p.get("cache_write", p["input"])
            + output * p["output"]) / 1e6


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def spent(day: str | None = None) -> float:
    day = day or today()
    if not LEDGER.exists():
        return 0.0
    return sum(r["usd"] for r in map(json.loads, LEDGER.read_text().splitlines()) if r["day"] == day)


def check(estimate: float = 0.0) -> None:
    s = spent()
    if s + estimate > DAILY_USD:
        raise BudgetError(f"DS Harness API budget: {s:.2f} USD spent today, cap {DAILY_USD:.2f} "
                          f"(this call needs about {estimate:.2f})")


def charge(model: str, usd: float, purpose: str = "") -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    row = {"at": datetime.now().isoformat(timespec="seconds"), "day": today(), "model": model,
           "usd": round(usd, 6), "purpose": purpose}
    with LEDGER.open("a") as f:
        f.write(json.dumps(row) + "\n")
