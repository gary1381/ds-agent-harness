"""Context layer: governed metrics reproduce custody truth; catalog matches the warehouse;
verified queries respect the context/eval wall; reconciliation catches wrong answers."""
from __future__ import annotations

import json

import pandas as pd
import pytest

from dsharness.common import open_readonly
from dsharness.context import (compile_metric, describe_metric, describe_table, list_metrics, load_catalog,
                               load_semantic_model, load_verified_queries, metric_definition_digest, query_metric,
                               run_verified_query, search_catalog)
from dsharness.context.catalog import TEMPORAL_TAGS
from dsharness.lake.build import warehouse_path
from dsharness.verify.reconcile import compare_frames, reconcile_answer_to_metric
from dsharness.verify.sql_static import analyze_sql
from dsharness.workspace import PIT_CUTOFFS

COMPLETE = ("2025-01", "2026-04")     # months fully known in the train build (cutoff 2026-05-31)


@pytest.fixture(scope="module")
def model():
    return load_semantic_model()


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


@pytest.fixture(scope="module")
def con(small_ws, train_build):
    c = open_readonly(warehouse_path(small_ws, train_build))
    yield c
    c.close()


@pytest.fixture(scope="module")
def truth_cm(small_ws) -> pd.DataFrame:
    tcm = pd.read_parquet(small_ws.custody / "truth_customer_month.parquet")
    return tcm[~tcm["is_test"]]


def _in(df, months, col="month"):
    return df[(df[col] >= months[0]) & (df[col] <= months[1])]


# ------------------------------------------------------------------ semantic model
def test_semantic_model_contract(model):
    assert set(model.metrics) == {"net_revenue_fen", "active_subscribers_eom", "active_subscribers_bom",
                                  "churned_customers", "churn_rate", "addon_units", "arpu_fen", "avg_csat",
                                  "program_enrolled", "program_churned", "program_churn_rate"}
    for m in model.metrics.values():
        assert m.description and m.owner and m.caveats and m.dimensions
        assert set(m.dimensions) <= {"region", "plan", "channel", "invoice_type", "ticket_category", "arm"}
    assert [n for n, m in model.metrics.items() if "arm" in m.dimensions] == [
        "program_enrolled", "program_churned", "program_churn_rate"]
    assert model.dimensions["arm"].values == ("treatment", "control")      # randomised arm, never exposure
    assert "invoice_type" in model.metrics["net_revenue_fen"].dimensions
    assert "invoice_type" not in model.metrics["churn_rate"].dimensions
    assert len(model.digest) == 64 and load_semantic_model().digest == model.digest
    d = describe_metric(model, "churn_rate")
    assert d["numerator"] == "churned_customers" and d["denominator"] == "active_subscribers_bom"
    assert d["dimensions"][0]["name"] == "month"
    assert {m["name"] for m in list_metrics(model)} == set(model.metrics)


def test_net_revenue_matches_custody_truth(small_ws, con, model):
    truth = _in(pd.read_parquet(small_ws.custody / "truth_revenue.parquet"), COMPLETE).rename(
        columns={"net_revenue_fen": "value"})
    got = query_metric(con, model, "net_revenue_fen", ["region"], COMPLETE)
    res = compare_frames(got, truth, ["month", "region"], "value", rel_tol=0.0)
    assert res["outcome"] == "PASS" and res["n_compared"] == 16 * 4, res
    total = query_metric(con, model, "net_revenue_fen", [], COMPLETE)
    by_month = truth.groupby("month", as_index=False)["value"].sum()
    assert compare_frames(total, by_month, ["month"], "value", rel_tol=0.0)["outcome"] == "PASS"


def test_subscriber_metrics_match_custody_truth(con, model, truth_cm):
    # active at end of month m == active at start of month m+1 in custody
    bom = truth_cm[truth_cm["active_at_start"]].groupby("month").size()
    eom = bom.copy()
    eom.index = (pd.PeriodIndex(eom.index, freq="M") - 1).astype(str)
    churned = truth_cm.groupby("month")["churned"].sum()
    for metric, truth in (("active_subscribers_eom", eom), ("active_subscribers_bom", bom),
                          ("churned_customers", churned)):
        got = query_metric(con, model, metric, [], COMPLETE)
        exp = _in(truth.rename("value").rename_axis("month").reset_index(), COMPLETE)
        res = compare_frames(got, exp, ["month"], "value", rel_tol=0.0)
        assert res["outcome"] == "PASS" and res["n_compared"] == 16, (metric, res)


def test_churn_rate_by_month_and_region_matches_truth(con, model, truth_cm):
    t = truth_cm.groupby("month").agg(ch=("churned", "sum"), ar=("at_risk", "sum")).reset_index()
    t["value"] = t["ch"] / t["ar"]
    got = query_metric(con, model, "churn_rate", [], COMPLETE)
    assert compare_frames(got, _in(t, COMPLETE), ["month"], "value", rel_tol=1e-12)["outcome"] == "PASS"

    cust = con.execute("SELECT customer_id, region FROM silver.customers").df()   # region is a static attribute
    tr = truth_cm.merge(cust, on="customer_id").groupby(["month", "region"]).agg(
        ch=("churned", "sum"), ar=("at_risk", "sum")).reset_index()
    tr["value"] = tr["ch"] / tr["ar"]
    got = query_metric(con, model, "churn_rate", ["region"], COMPLETE)
    res = compare_frames(got, _in(tr, COMPLETE), ["month", "region"], "value", rel_tol=1e-12)
    assert res["outcome"] == "PASS" and res["n_compared"] == 16 * 4, res


def test_program_itt_metrics_match_custody_truth(small_ws, con, model):
    """Enrolment and churn by randomised arm (ITT) equal the evaluator's program truth for every
    campaign month the train build fully knows (churn is known up to 3 days late)."""
    months = ("2026-03", "2026-04")
    tp = _in(pd.read_parquet(small_ws.custody / "truth_program.parquet"), months, "campaign_month")
    t = (tp.groupby(["campaign_month", "arm"]).agg(n=("contact_id", "size"), ch=("churned", "sum"))
         .reset_index().rename(columns={"campaign_month": "month"}))
    assert len(t) == 4 and t["n"].min() > 0
    for metric, col in (("program_enrolled", "n"), ("program_churned", "ch")):
        got = query_metric(con, model, metric, ["arm"], months)
        res = compare_frames(got, t.rename(columns={col: "value"}), ["month", "arm"], "value", rel_tol=0.0)
        assert res["outcome"] == "PASS" and res["n_compared"] == 4, (metric, res)
    rate = query_metric(con, model, "program_churn_rate", ["arm"], months)
    res = compare_frames(rate, t.assign(value=t["ch"] / t["n"]), ["month", "arm"], "value", rel_tol=1e-12)
    assert res["outcome"] == "PASS", res
    # outside the program: zero enrolments (additive), no rate (undefined), never invented
    assert query_metric(con, model, "program_enrolled", [], ("2026-01", "2026-02"))["value"].tolist() == [0, 0]
    assert query_metric(con, model, "program_churn_rate", [], ("2026-01", "2026-02")).empty


def test_metric_definition_digest_tracks_only_the_definition(tmp_path, model):
    import yaml
    from dsharness.context.semantic import DEFAULT_SEMANTIC_MODEL
    digests = {m: metric_definition_digest(model, m) for m in model.metrics}
    assert len(set(digests.values())) == len(digests) and all(len(d) == 64 for d in digests.values())
    spec = yaml.safe_load(DEFAULT_SEMANTIC_MODEL.read_text())
    spec["metrics"]["avg_csat"]["caveats"].append("an unrelated edit")
    spec["datasets"]["subscriptions"]["required_filter"] = "NOT b.is_test_account AND TRUE"
    p = tmp_path / "m.yaml"
    p.write_text(yaml.safe_dump(spec))
    edited = load_semantic_model(p)
    assert metric_definition_digest(edited, "net_revenue_fen") == digests["net_revenue_fen"]
    assert metric_definition_digest(edited, "avg_csat") != digests["avg_csat"]
    # a ratio depends on its denominator's dataset: ARPU changes with the subscriptions dataset
    assert metric_definition_digest(edited, "arpu_fen") != digests["arpu_fen"]
    with pytest.raises(ValueError, match="unknown metric"):
        metric_definition_digest(model, "revenue")


def test_metrics_reproduce_truth_on_point_in_time_build(small_ws, build_reports, model):
    """An early snapshot (cutoff 2026-02-28) gives the same answers for months it fully knows."""
    early = build_reports[PIT_CUTOFFS[0]].build_id
    truth = pd.read_parquet(small_ws.custody / "truth_revenue.parquet").rename(columns={"net_revenue_fen": "value"})
    with open_readonly(warehouse_path(small_ws, early)) as c:
        got = query_metric(c, model, "net_revenue_fen", ["region"], ("2025-01", "2026-01"))
    assert compare_frames(got, _in(truth, ("2025-01", "2026-01")), ["month", "region"], "value",
                          rel_tol=0.0)["outcome"] == "PASS"


def test_months_are_inclusive_and_bounded_by_build_coverage(con, model):
    got = query_metric(con, model, "addon_units", ["region"], ("2026-01", "2026-04"))
    assert sorted(got["month"].unique()) == ["2026-01", "2026-02", "2026-03", "2026-04"]
    assert list(got.columns) == ["month", "region", "value"]
    full = query_metric(con, model, "addon_units", [])
    assert full["month"].min() == "2025-01" and full["month"].max() == "2026-05"   # train coverage window
    with pytest.raises(ValueError, match="outside this build's coverage"):
        query_metric(con, model, "addon_units", [], ("2026-05", "2026-07"))    # never zeros for unknown months
    with pytest.raises(ValueError, match="outside"):
        query_metric(con, model, "net_revenue_fen", [], ("2024-12", "2025-01"))
    assert len(query_metric(con, model, "churn_rate", ["month"], ("2025-12", "2026-01"))) == 2   # month is implicit


def test_filters_and_zero_fill_semantics(con, model):
    by_region = query_metric(con, model, "net_revenue_fen", ["region"], ("2026-03", "2026-03"))
    south = query_metric(con, model, "net_revenue_fen", [], ("2026-03", "2026-03"), {"region": "south"})
    assert south["value"].tolist() == by_region.loc[by_region["region"] == "south", "value"].tolist()
    refunds = query_metric(con, model, "net_revenue_fen", ["invoice_type"], COMPLETE, {"invoice_type": "refund"})
    assert (refunds["value"] < 0).all() and set(refunds["invoice_type"]) == {"refund"}
    two = query_metric(con, model, "net_revenue_fen", [], ("2026-03", "2026-03"), {"region": ["south", "north"]})
    assert two["value"].iloc[0] == by_region.set_index("region").loc[["south", "north"], "value"].sum()
    # no livestream customers before 2026-08: additive metrics are a true zero, ratios are undefined (no row)
    live = query_metric(con, model, "net_revenue_fen", [], ("2026-01", "2026-04"), {"channel": "livestream"})
    assert live["value"].tolist() == [0, 0, 0, 0]
    assert query_metric(con, model, "churn_rate", [], ("2026-01", "2026-04"), {"channel": "livestream"}).empty
    csat = query_metric(con, model, "avg_csat", ["ticket_category"], ("2026-01", "2026-03"))
    assert csat["value"].between(1, 5).all() and set(csat["ticket_category"]) <= {"billing", "bug", "howto", "cancel"}


@pytest.mark.parametrize("kwargs, match", [
    ({"metric": "revenue", "dimensions": []}, "unknown metric"),
    ({"metric": "churn_rate", "dimensions": ["invoice_type"]}, "does not support"),
    ({"metric": "net_revenue_fen", "dimensions": ["country"]}, "does not support"),
    ({"metric": "net_revenue_fen", "dimensions": "region"}, "list of names"),
    ({"metric": "net_revenue_fen", "dimensions": ["region", "region"]}, "duplicate"),
    ({"metric": "net_revenue_fen", "dimensions": [], "months": ("2026-04", "2026-01")}, "empty"),
    ({"metric": "net_revenue_fen", "dimensions": [], "months": ("2026-4", "2026-05")}, "YYYY-MM"),
    ({"metric": "net_revenue_fen", "dimensions": [], "months": "2026-04"}, "pair"),
    ({"metric": "net_revenue_fen", "dimensions": [], "filters": {"region": "North"}}, "unknown values"),
    ({"metric": "net_revenue_fen", "dimensions": [], "filters": {"region": "x' OR 1=1 --"}}, "unknown values"),
    ({"metric": "net_revenue_fen", "dimensions": [], "filters": {"ticket_category": "billing"}}, "cannot be filtered"),
    ({"metric": "net_revenue_fen", "dimensions": [], "filters": {"month": "2026-01"}}, "months="),
    ({"metric": "net_revenue_fen", "dimensions": [], "filters": {"region": []}}, "non-empty"),
])
def test_compile_rejects_invalid_requests(model, kwargs, match):
    with pytest.raises(ValueError, match=match):
        compile_metric(model, **kwargs)


def test_compiled_sql_is_governed_by_the_static_analyzer(model, catalog):
    for name, m in model.metrics.items():
        for dims in ([], m.dimensions):
            for months in (None, ("2025-06", "2025-09")):
                sql = compile_metric(model, name, dims, months, {"region": ["east", "west"]})
                rep = analyze_sql(sql, catalog)
                assert rep.ok and rep.read_only and rep.tier == "certified", (name, rep.violations)
                assert rep.schemas == ["silver"]
                assert not [w for w in rep.warnings if "exclude_test_accounts" in w], (name, rep.warnings)
                # the only soft warning a governed metric may carry is reading ended_at (current state)
                assert set(rep.mutable_or_post_outcome_columns) <= {"silver.subscriptions.ended_at"}


def test_semantic_model_validation_fails_loudly(tmp_path):
    import yaml
    from dsharness.context.semantic import DEFAULT_SEMANTIC_MODEL
    spec = yaml.safe_load(DEFAULT_SEMANTIC_MODEL.read_text())
    del spec["datasets"]["invoices"]["required_filter"]
    p = tmp_path / "m.yaml"
    p.write_text(yaml.safe_dump(spec))
    with pytest.raises(ValueError, match="required_filter"):
        load_semantic_model(p)
    spec = yaml.safe_load(DEFAULT_SEMANTIC_MODEL.read_text())
    spec["metrics"]["churn_rate"]["dimensions"].append("invoice_type")
    p.write_text(yaml.safe_dump(spec))
    with pytest.raises(ValueError, match="not supported by numerator"):
        load_semantic_model(p)


# ------------------------------------------------------------------ catalog
def test_catalog_matches_warehouse_exactly(con, catalog):
    live = con.execute("""
        SELECT table_schema || '.' || table_name AS t, list(column_name ORDER BY ordinal_position) AS cols
        FROM information_schema.columns WHERE table_schema IN ('silver', 'staging', 'bronze_safe')
        GROUP BY 1""").fetchall()
    live = {t: set(cols) for t, cols in live}
    assert set(live) == set(catalog.tables), set(live) ^ set(catalog.tables)
    for t, cols in live.items():
        assert cols == set(catalog.tables[t].columns), (t, cols ^ set(catalog.tables[t].columns))


def test_catalog_temporal_tags_and_tiers(catalog):
    tag = catalog.temporal_tag
    assert tag("silver.subscriptions", "monthly_price_fen") == "mutable_current"
    assert tag("silver.subscriptions", "ended_at") == "mutable_current"
    assert tag("silver.subscriptions", "cancel_reason") == "post_outcome"
    assert tag("silver.subscriptions", "updated_at") == "availability_time"
    assert tag("silver.usage_monthly", "usage_month") == "event_time"
    assert tag("bronze_safe.billing_subscriptions", "monthly_price_cents") == "mutable_current"
    for t in catalog.tables.values():
        if "available_at" in t.columns:
            assert t.columns["available_at"].temporal == "availability_time", t.name
        for c in t.columns.values():
            assert c.temporal in TEMPORAL_TAGS and c.description
        assert t.tier == {"silver": "certified", "staging": "staging", "bronze_safe": "uncertified"}[t.schema]
    for t in ("silver.subscriptions", "staging.stg_subscriptions", "bronze_safe.billing_subscriptions"):
        assert set(catalog.tables[t].columns_tagged("mutable_current", "post_outcome")) >= {"ended_at", "cancel_reason"}
    assert not [t for t in catalog.tables if t.startswith("bronze.")]
    assert {"email", "phone", "birth_year"} <= catalog.pii_columns
    assert "bronze" in catalog.restricted_schemas
    for t in ("silver.invoices", "silver.customers", "bronze_safe.billing_invoices"):
        assert "exclude_test_accounts" in catalog.tables[t].required_filters
    caveats = " ".join(catalog.tables["bronze_safe.billing_invoices"].caveats)
    for d in ("D1", "D2", "D3", "D4", "D5"):
        assert d in caveats


def test_describe_and_search_catalog(catalog):
    d = describe_table(catalog, "silver.invoices")
    assert d["tier"] == "certified" and {c["name"] for c in d["columns"]} >= {"amount_fen", "is_test_account"}
    assert d["required_filters"][0]["column"] == "is_test_account"
    assert describe_table(catalog, "INVOICES")["name"] == "silver.invoices"     # unique short name resolves
    assert "uncertified" in describe_table(catalog, "bronze_safe.billing_invoices")["usage"].lower()
    json.dumps(d)                                                                # agent-facing: JSON-safe
    with pytest.raises(ValueError, match="restricted"):
        describe_table(catalog, "bronze.crm_customers")
    with pytest.raises(ValueError, match="unknown table"):
        describe_table(catalog, "silver.nope")
    with pytest.raises(ValueError, match="ambiguous"):
        describe_table(catalog, "finance_closed_revenue")
    hits = search_catalog(catalog, "revenue")
    assert hits[0]["name"] == "silver.finance_closed_revenue" and hits[0]["tier"] == "certified"
    assert search_catalog(catalog, "retention program arm")[0]["name"] == "silver.retention_contacts"
    assert search_catalog(catalog, "unit incident correction")[0]["name"] == "silver.ref_known_incidents"
    inc = describe_table(catalog, "silver.ref_known_incidents")
    assert inc["tier"] == "certified"
    assert {c["name"] for c in inc["columns"]} >= {"incident_id", "factor", "approved_by"}
    rc = describe_table(catalog, "silver.retention_contacts")
    assert "ITT" in " ".join(rc["caveats"]) and any(c["name"] == "exposed" for c in rc["columns"])
    assert search_catalog(catalog, "late usage minutes")[0]["name"] == "silver.usage_monthly"
    assert search_catalog(catalog, "cancel reason")[0]["name"] == "silver.subscriptions"
    assert search_catalog(catalog, "zzzz") == []
    with pytest.raises(ValueError):
        search_catalog(catalog, "  ")


def test_catalog_validation_fails_loudly(tmp_path):
    import yaml
    from dsharness.context.catalog import DEFAULT_CATALOG
    spec = yaml.safe_load(DEFAULT_CATALOG.read_text())
    spec["tables"]["silver.subscriptions"]["columns"]["cancel_reason"]["temporal"] = "fine"
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(spec))
    with pytest.raises(ValueError, match="temporal"):
        load_catalog(p)


# ------------------------------------------------------------------ verified queries
def test_verified_queries_respect_the_context_eval_wall(con, model, catalog):
    with pytest.raises(TypeError):
        load_verified_queries()                       # caller must choose a side
    with pytest.raises(ValueError):
        load_verified_queries("train")
    allq = load_verified_queries("all")
    ctx, ev = load_verified_queries("context"), load_verified_queries("eval")
    assert len(allq) >= 12 and len(ev) >= 4 and len(ctx) + len(ev) == len(allq)
    assert all(q["split"] == "context" for q in ctx) and all(q["split"] == "eval" for q in ev)
    ctx_calls = {json.dumps(q["metric_call"], sort_keys=True) for q in ctx if q["metric_call"]}
    ctx_sql = {q["sql"] for q in ctx if q["sql"]}
    for q in ev:   # an eval item must not be answerable by copying a context example
        assert q["question"] not in {c["question"] for c in ctx}
        assert json.dumps(q["metric_call"], sort_keys=True) not in ctx_calls if q["metric_call"] else q["sql"] not in ctx_sql
    program = [q for q in ctx if (q["metric_call"] or {}).get("metric", "").startswith("program_")
               or "retention_contacts" in (q["sql"] or "")]
    assert len(program) >= 2 and all(q["split"] == "context" for q in program)
    for q in allq:
        df = run_verified_query(con, model, q)
        assert len(df) > 0 and "value" in df.columns and df["value"].notna().all(), q["id"]
        if q["sql"]:
            rep = analyze_sql(q["sql"], catalog)
            assert rep.ok and rep.tier == "certified" and not rep.warnings, (q["id"], rep)


def test_verified_query_loader_rejects_stale_entries(tmp_path, model):
    p = tmp_path / "vq.yaml"
    p.write_text("queries:\n  - {id: a, split: context, question: q, metric_call: {metric: gone, dimensions: []}}\n")
    with pytest.raises(ValueError, match="unknown metric"):
        load_verified_queries("all", path=p, model=model)
    p.write_text("queries:\n  - {id: a, split: both, question: q, sql: SELECT 1}\n")
    with pytest.raises(ValueError, match="split"):
        load_verified_queries("all", path=p, model=model)


# ------------------------------------------------------------------ reconciliation
def test_reconcile_answer_to_metric(con, model):
    months = ("2026-03", "2026-03")
    rows = query_metric(con, model, "net_revenue_fen", ["region"], months).to_dict("records")
    ok = reconcile_answer_to_metric(con, model, rows, "net_revenue_fen", ["region"], months)
    assert ok["outcome"] == "PASS" and ok["n_compared"] == 4 and ok["semantic_digest"] == model.digest
    json.dumps(ok, allow_nan=False)

    off = [dict(r) for r in rows]
    off[0]["value"] = off[0]["value"] + 100
    bad = reconcile_answer_to_metric(con, model, off, "net_revenue_fen", ["region"], months)
    assert bad["outcome"] == "FAIL" and bad["n_mismatches"] == 1 and bad["mismatches"][0]["abs_diff"] == 100
    assert reconcile_answer_to_metric(con, model, off, "net_revenue_fen", ["region"], months,
                                      abs_tol=100)["outcome"] == "PASS"

    partial = reconcile_answer_to_metric(con, model, rows[:2], "net_revenue_fen", ["region"], months)
    assert partial["outcome"] == "FAIL" and partial["n_missing_keys"] == 2
    empty = reconcile_answer_to_metric(con, model, [], "net_revenue_fen", ["region"], months)
    assert empty["outcome"] == "FAIL" and empty["n_missing_keys"] == 4

    total = sum(r["value"] for r in rows)
    alt = [{"month": "2026-03-01", "net_revenue_fen": total}]       # agent's natural column names
    res = reconcile_answer_to_metric(con, model, alt, "net_revenue_fen", [], months)
    assert res["outcome"] == "PASS" and res["value_column_used"] == "net_revenue_fen"

    assert reconcile_answer_to_metric(con, model, rows, "revenue", [], months)["outcome"] == "ERROR"
    far = reconcile_answer_to_metric(con, model, rows, "net_revenue_fen", ["region"], ("2026-03", "2026-08"))
    assert far["outcome"] == "ERROR" and "coverage" in far["error"]
    nodim = reconcile_answer_to_metric(con, model, [{"month": "2026-03", "value": 1}], "net_revenue_fen",
                                       ["region"], months)
    assert nodim["outcome"] == "ERROR" and "lacks columns" in nodim["error"]


def test_naive_raw_sql_is_flagged_and_fails_reconciliation(con, model, catalog):
    """The classic silent failure: plausible SQL on the raw export (D1 dups, D3 yuan, D5 QA accounts)."""
    naive = ("SELECT strftime(issued_at, '%Y-%m') AS month, SUM(amount_cents) AS value "
             "FROM bronze_safe.billing_invoices WHERE issued_at >= '2026-03-01' AND issued_at < '2026-04-01' "
             "GROUP BY 1")
    rep = analyze_sql(naive, catalog)
    assert rep.ok and rep.tier == "uncertified"
    assert any("uncertified table bronze_safe.billing_invoices" in w for w in rep.warnings)
    assert any("exclude_test_accounts" in w for w in rep.warnings)
    rows = con.execute(naive).df().to_dict("records")
    res = reconcile_answer_to_metric(con, model, rows, "net_revenue_fen", [], ("2026-03", "2026-03"))
    assert res["outcome"] == "FAIL"


def test_compare_frames_edge_cases():
    e = pd.DataFrame({"k": ["a", "b"], "v": [100.0, float("nan")]})
    assert compare_frames(e.copy(), e, ["k"], "v")["outcome"] == "PASS"             # NaN == NaN (both undefined)
    a = pd.DataFrame({"k": ["a", "b"], "v": [100.0 + 1e-6, float("nan")]})   # tolerance is relative to expected
    assert compare_frames(a, e, ["k"], "v", rel_tol=1e-9)["outcome"] == "FAIL"
    assert compare_frames(a, e, ["k"], "v", rel_tol=1e-9, abs_tol=1e-5)["outcome"] == "PASS"
    assert compare_frames(a, e, ["k"], "v", rel_tol=1e-6)["outcome"] == "PASS"
    one_nan = pd.DataFrame({"k": ["a", "b"], "v": [100.0, 1.0]})
    assert compare_frames(one_nan, e, ["k"], "v")["outcome"] == "FAIL"
    extra = pd.DataFrame({"k": ["a", "b", "c"], "v": [100.0, float("nan"), 0.0]})
    r = compare_frames(extra, e, ["k"], "v")
    assert r["outcome"] == "FAIL" and r["extra_keys"] == [{"k": "c"}]
    dup = pd.DataFrame({"k": ["a", "a"], "v": [1, 1]})
    assert compare_frames(dup, e, ["k"], "v")["outcome"] == "ERROR"
    assert compare_frames(pd.DataFrame({"k": ["a"], "v": ["n/a"]}), e, ["k"], "v")["outcome"] == "ERROR"
    assert compare_frames(e, e, ["k"], "missing")["outcome"] == "ERROR"
    assert compare_frames(e, e, "k", "v")["outcome"] == "ERROR"
    none = pd.DataFrame({"k": [], "v": []})
    assert compare_frames(none, none, ["k"], "v")["outcome"] == "INCONCLUSIVE"
    big = pd.DataFrame({"k": [f"x{i}" for i in range(50)], "v": range(50)})
    r = compare_frames(big.assign(v=big["v"] + 1), big, ["k"], "v")
    assert r["n_mismatches"] == 50 and len(r["mismatches"]) == 20 and r["max_abs_diff"] == 1.0
    inf = compare_frames(pd.DataFrame({"k": ["a"], "v": [float("inf")]}), pd.DataFrame({"k": ["a"], "v": [1.0]}),
                         ["k"], "v")
    assert inf["outcome"] == "FAIL"
    json.dumps(inf, allow_nan=False)
