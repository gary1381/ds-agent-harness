"""A workspace can bring its own reviewed definitions (context/semantic_model.yaml, catalog.yaml, pipeline);
without them the built-in reference scenario applies unchanged (harness v0.3, docs/experiments/E3a/DESIGN.md)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from dsharness import tools as T
from dsharness.bench.run import private_workspace
from dsharness.context.semantic import DEFAULT_SEMANTIC_MODEL, compile_metric, load_semantic_model
from dsharness.tools import analytics as A


def own_model(tmp: Path) -> Path:
    """The built-in model plus one metric with its own vocabulary."""
    spec = yaml.safe_load(DEFAULT_SEMANTIC_MODEL.read_text(encoding="utf-8"))
    spec["metrics"]["gross_charges_fen"] = {
        "type": "simple", "terms": [r"\bgross charges?\b"], "label": "Gross charges (fen)", "unit": "fen",
        "owner": "test", "description": "Charges before add-ons and refunds.", "dataset": "invoices",
        "measure": "amount_fen", "filter": "b.invoice_type = 'charge'", "dimensions": ["region"],
        "caveats": ["test metric"]}
    p = tmp / "semantic_model.yaml"
    p.write_text(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return p


@pytest.fixture(scope="module")
def ws_own(small_ws, tmp_path_factory):
    base = tmp_path_factory.mktemp("own")
    ctx_dir = base / "context_src"
    ctx_dir.mkdir()
    own_model(ctx_dir)
    ws = private_workspace(small_ws, base / "ws")
    ws.context.mkdir()
    (ws.context / "semantic_model.yaml").write_bytes((ctx_dir / "semantic_model.yaml").read_bytes())
    return ws


def test_workspace_model_is_served_and_default_is_untouched(ws_own, small_ws):
    ctx = T.make_context(ws_own, "score", "analyst", "agent:own")
    try:
        names = {m["name"] for m in T.list_metrics(ctx)["metrics"]}
        assert "gross_charges_fen" in names
        assert A.match_governed_metrics(A.semantic_model(ctx), "What were gross charges in March 2026?") == \
            ["gross_charges_fen"]
        q = T.query_metric(ctx, "gross_charges_fen", [], ["2026-03", "2026-03"])
        assert q["rows"] and "error" not in q
    finally:
        ctx.close()
    base = T.make_context(small_ws, "score", "analyst", "agent:base")
    try:
        assert "gross_charges_fen" not in {m["name"] for m in T.list_metrics(base)["metrics"]}
    finally:
        base.close()


def test_private_workspace_shares_the_context(ws_own, tmp_path):
    child = private_workspace(ws_own, tmp_path / "child")
    assert child.context_file("semantic_model.yaml") is not None
    assert child.context_file("catalog.yaml") is None and child.context_file("pipeline") is None


def test_a_metric_without_vocabulary_is_refused(tmp_path):
    spec = yaml.safe_load(DEFAULT_SEMANTIC_MODEL.read_text(encoding="utf-8"))
    del spec["metrics"]["avg_csat"]["terms"]
    p = tmp_path / "m.yaml"
    p.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="terms"):
        load_semantic_model(p)
    spec["metrics"]["avg_csat"]["terms"] = ["(unclosed"]
    p.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="not a regex"):
        load_semantic_model(p)


def test_independent_record_comes_from_the_model():
    model = load_semantic_model()
    assert model.metrics["net_revenue_fen"].independent_record == "silver.finance_closed_revenue"
    assert [n for n, m in model.metrics.items() if m.independent_record] == ["net_revenue_fen"]


def test_an_independent_record_without_a_check_is_refused(tmp_path):
    spec = yaml.safe_load(DEFAULT_SEMANTIC_MODEL.read_text(encoding="utf-8"))
    spec["metrics"]["avg_csat"]["independent_record"] = "silver.csat_survey_close"
    p = tmp_path / "m.yaml"
    p.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="has no reconciliation check"):
        load_semantic_model(p)


def test_money_with_cents_is_not_cast_to_integers(tmp_path):
    """Onboarding E3a found additive sums always cast to BIGINT (fen); GBP lost its pence."""
    from dsharness.context.semantic import compile_metric
    spec = yaml.safe_load(DEFAULT_SEMANTIC_MODEL.read_text(encoding="utf-8"))
    assert "AS BIGINT" in compile_metric(load_semantic_model(), "net_revenue_fen", [], ("2026-01", "2026-01"), None)
    spec["datasets"]["invoices"]["measures"]["amount_fen"]["cast"] = "double"
    p = tmp_path / "m.yaml"
    p.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    sql = compile_metric(load_semantic_model(p), "net_revenue_fen", [], ("2026-01", "2026-01"), None)
    assert "AS DOUBLE" in sql and "AS BIGINT" not in sql
    spec["datasets"]["invoices"]["measures"]["amount_fen"]["cast"] = "decimal"
    p.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="cast"):
        load_semantic_model(p)


def test_derive_example_follows_the_model(ws_own):
    assert A.derive_guidance(load_semantic_model()) == A.DERIVE_GUIDANCE
    spec = yaml.safe_load((ws_own.context / "semantic_model.yaml").read_text(encoding="utf-8"))
    spec["derive_example"] = "Example: own."
    p = ws_own.root / "m2.yaml"
    p.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    g = A.derive_guidance(load_semantic_model(p))
    assert "Example: own." in g and "net_revenue_fen" not in g


E3A_MODEL = Path(__file__).resolve().parents[1] / "experiments/E3a/context/semantic_model.yaml"


@pytest.mark.parametrize("question, dims, filters, honest", [
    ("What were net product sales in the UK in March 2025, in GBP?", [], {"market": "UK"}, True),
    ("What were net product sales in the UK in March 2025, in GBP?", [], {"country": "United Kingdom"}, True),
    ("What were net product sales in the UK in March 2025, in GBP?", ["market"], None, True),
    ("Net product sales in the United Kingdom in March 2025?", [], {"market": "UK"}, True),
    ("Net product sales outside the UK in March 2025?", [], {"market": "non-UK"}, True),
    ("Net product sales outside the UK in March 2025?", [], {"market": "UK"}, False),
    ("Net product sales in non UK markets in March 2025?", [], {"market": "non-UK"}, True),
    ("Net product sales, UK vs non-UK, March 2025?", ["market"], None, True),
    ("2025年3月非英国市场的净产品销售额？", [], {"market": "non-UK"}, True),
    ("2025年3月英国以外的净产品销售额？", [], {"market": "non-UK"}, True),
    ("2025年3月英国市场的净产品销售额？", [], {"market": "non-UK"}, False),
    ("Net product sales in Germany in March 2025?", [], {"country": "Germany"}, True),
    ("Net product sales in Germany in March 2025?", [], {"market": "non-UK"}, False),
    ("2025年3月德国的净产品销售额？", [], {"country": "Germany"}, True),
    ("Net product sales in Ireland in March 2025?", [], {"country": "EIRE"}, True),
    ("Net product sales for non-UK customers by country in March 2025?", ["country"], {"market": "non-UK"}, True),
    ("Net product sales in March 2025?", [], {"market": "UK"}, False),
    ("How did the Duke Street shop do in March 2025?", [], None, True),
])
def test_dimension_values_with_capitals_and_overlapping_spellings(question, dims, filters, honest):
    """Retail values are capitalised and overlap ("UK" in "non-UK", 英国 in 非英国); one phrase can name two
    dimensions ("UK": market or country). Found by the E3a smoke run: "in the UK" was read as no market."""
    model = load_semantic_model(E3A_MODEL)
    call = A.normalize_metric_call({"metric": "net_product_sales", "dimensions": dims,
                                    "months": ["2025-03", "2025-03"], "filters": filters})
    assert (A.claim_contradictions(model, question, call) == []) == honest


def test_a_misspelled_country_is_an_error_not_an_empty_answer():
    model = load_semantic_model(E3A_MODEL)
    with pytest.raises(ValueError, match="unknown values"):
        compile_metric(model, "net_product_sales", [], ("2025-03", "2025-03"), {"country": "Ireland"})
