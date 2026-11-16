"""The daily API budget: prices, refusal of unknown models, the cap."""
from __future__ import annotations

import pytest

from dsharness.bench import budget as B


def test_price_and_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "LEDGER", tmp_path / "spend.jsonl")
    monkeypatch.setattr(B, "DAILY_USD", 1.0)
    assert B.price("gpt-6.1-terra", input=1_000_000, output=100_000) == pytest.approx(1.5)
    B.charge("gpt-6-luna", 0.6, "test")
    assert B.spent() == pytest.approx(0.6)
    B.check(0.3)
    with pytest.raises(B.BudgetError):
        B.check(0.5)


def test_unknown_model_refused():
    with pytest.raises(B.BudgetError):
        B.price("claude-opus-5-5", input=10)
