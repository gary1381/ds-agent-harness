"""Governed builds: READY/FAILED semantics, registered-incident scoping and price-book validity.

The incident and price-book tests are world-independent: they derive regions, months and rows
from the exports themselves (no hard-coded counts), so they hold for the small test world and
the full world alike."""
from __future__ import annotations

import json
from pathlib import Path
import shutil

import duckdb
import pandas as pd
import pytest

from dsharness.common import file_digest
from dsharness.lake.build import DEFAULT_PIPELINE, build_snapshot, warehouse_path
from dsharness.workspace import CUTOFFS, PIT_CUTOFFS, Workspace

STG = (DEFAULT_PIPELINE / "models" / "stg_invoices.sql").read_text()
AMOUNT_LINE = "    amount_cents                                     AS amount_fen_raw,"
GOOD_FIX = STG.replace(AMOUNT_LINE, "    COLUMNS('^amount_(cents|minor)$')                AS 'amount_fen_raw',")
PRICES = (DEFAULT_PIPELINE / "models" / "ref_plan_prices.sql").read_text()
INCIDENTS = (DEFAULT_PIPELINE / "models" / "ref_known_incidents.sql").read_text()
INVOICES = (DEFAULT_PIPELINE / "models" / "invoices.sql").read_text()
SCOPE_TESTS = {"invoices:charge_amount_on_price_book", "invoices:no_unregistered_unit_incident",
               "invoices:registered_incident_rows_were_affected", "invoices:unit_corrected_iff_registered_scope",
               "invoices:amount_equals_export_outside_registered_incidents"}
# The pre-registry rule (judges' finding D3): "fix" ANY cheap charge whose x100 is a list price.
HEURISTIC = """
    CASE WHEN d.invoice_type = 'charge' AND d.amount_fen_raw < 1000
              AND d.amount_fen_raw * 100 IN (SELECT price_fen FROM silver.ref_plan_prices)
         THEN d.amount_fen_raw * 100 ELSE d.amount_fen_raw END          AS amount_fen,
    (d.invoice_type = 'charge' AND d.amount_fen_raw < 1000
        AND d.amount_fen_raw * 100 IN (SELECT price_fen FROM silver.ref_plan_prices)) AS {flag},"""
REGISTERED = """
    d.amount_fen_raw * COALESCE(k.factor, 1)                             AS amount_fen,
    k.incident_id IS NOT NULL                                            AS unit_corrected,"""
assert GOOD_FIX != STG and REGISTERED in INVOICES


def test_governed_builds_ready_and_reconcile(build_reports):
    for cutoff in (CUTOFFS["train"], CUTOFFS["score"], *PIT_CUTOFFS):
        rep = build_reports[cutoff]
        assert rep.status == "READY", rep.reasons
        rec = [t for t in rep.tests if t["name"].startswith("reconcile:")]
        assert rec and all(t["outcome"] == "PASS" for t in rec)
        assert SCOPE_TESTS <= {t["name"] for t in rep.tests if t["outcome"] == "PASS"}


def test_schema_change_fails_explicitly(build_reports):
    for cutoff in (CUTOFFS["drift"], CUTOFFS["monitor"]):
        rep = build_reports[cutoff]
        assert rep.status == "FAILED"
        assert rep.billing_schema_version == 2
        assert any(r.startswith("model:stg_invoices:ERROR") for r in rep.reasons)
        assert any("SKIPPED" in r for r in rep.reasons)   # dependants are not half-built


def test_anomalies_are_counted_not_dropped(build_reports):
    rep = build_reports[CUTOFFS["train"]]
    inv = next(c for c in rep.contracts if c["table"] == "billing_invoices")
    assert inv["anomalies"]["duplicate_invoice_id_rows"] > 0
    assert inv["anomalies"]["charge_amount_below_cheapest_plan"] > 0


# =========================================================================== helpers
def pipeline_variant(root: Path, files: dict[str, str]) -> Path:
    pipe = root / "pipeline"
    shutil.copytree(DEFAULT_PIPELINE, pipe, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for rel, content in files.items():
        (pipe / rel).write_text(content)
    return pipe


def failing(rep) -> set[str]:
    return {t["name"] for t in rep.tests if t["outcome"] != "PASS"}


def unclosed_unit_incident_export(ws: Workspace, cutoff: str, root: Path) -> tuple[Workspace, dict]:
    """Copy of the `cutoff` export with a NEW unit incident: every charge of the latest month that
    Finance has not closed yet, in a region no registered incident covers, exported in yuan."""
    src = ws.source_dir(cutoff)
    probe = Workspace(root).ensure()
    dest = probe.source_dir(cutoff)
    dest.mkdir(parents=True)
    manifest = json.loads((src / "_export_manifest.json").read_text())
    inv = pd.read_csv(src / "billing_invoices.csv", dtype=str, keep_default_na=False)
    cust = pd.read_csv(src / "crm_customers.csv", dtype=str, usecols=["customer_id", "region"])
    closed = set(pd.read_csv(src / "finance_closed_revenue.csv", dtype=str)["month"])
    inv_month = inv["issued_at"].str[:7]
    month = max(set(inv_month[inv["invoice_type"] == "charge"]) - closed)
    region = sorted(set(cust["region"]) - {"south"})[0]          # INC-2026-03-SOUTH is the only registration
    in_region = inv["customer_id"].isin(set(cust.loc[cust["region"] == region, "customer_id"]))
    hit = (inv["invoice_type"] == "charge") & (inv_month == month) & in_region
    assert hit.sum() > 0
    inv.loc[hit, "amount_cents"] = (inv.loc[hit, "amount_cents"].astype(int) // 100).astype(str)
    for name, meta in manifest["tables"].items():
        if name != "billing_invoices":
            (dest / meta["file"]).symlink_to((src / meta["file"]).resolve())
    inv.to_csv(dest / "billing_invoices.csv", index=False)
    manifest["tables"]["billing_invoices"].update(sha256=file_digest(dest / "billing_invoices.csv"))
    (dest / "_export_manifest.json").write_text(json.dumps(manifest, indent=2))
    return probe, {"region": region, "month": month, "rows": int(inv.loc[hit, "invoice_id"].nunique())}


# =========================================================================== D3: registered incidents
def test_unit_correction_is_exactly_the_registered_incident(small_ws, train_build):
    with duckdb.connect(str(warehouse_path(small_ws, train_build)), read_only=True) as con:
        registry = con.execute("SELECT incident_id, region, month, invoice_type, factor FROM "
                               "silver.ref_known_incidents").fetchall()
        assert [r[0] for r in registry] == ["INC-2026-03-SOUTH"]
        scope = con.execute("""
            SELECT i.unit_corrected, count(*) AS n,
                   bool_and(i.amount_fen / k.factor < 2900) AS raw_below_cheapest
            FROM silver.invoices i JOIN silver.ref_known_incidents k
              ON k.region = i.region AND k.month = i.revenue_month AND k.invoice_type = i.invoice_type
            GROUP BY 1""").fetchall()
        assert [(flag, ok) for flag, _, ok in scope] == [(True, True)]      # every in-scope row, really affected
        outside = con.execute("""
            SELECT count(*) FROM silver.invoices i WHERE i.unit_corrected AND NOT EXISTS (
              SELECT 1 FROM silver.ref_known_incidents k WHERE k.region = i.region
                AND k.month = i.revenue_month AND k.invoice_type = i.invoice_type)""").fetchone()[0]
        assert outside == 0


def test_new_unit_incident_in_an_unclosed_month_fails_the_build_loudly(small_ws, tmp_path):
    cutoff = CUTOFFS["train"]
    probe, planted = unclosed_unit_incident_export(small_ws, cutoff, tmp_path / "probe")
    rep = build_snapshot(probe, cutoff, out_root=tmp_path / "lake")
    assert rep.status == "FAILED"
    assert {"invoices:no_unregistered_unit_incident", "invoices:charge_amount_on_price_book"} <= failing(rep)
    # Finance has not closed the month: the reconciliation could not have caught it
    assert "reconcile:revenue_matches_finance_close" not in failing(rep)
    bad = next(t for t in rep.tests if t["name"] == "invoices:no_unregistered_unit_incident")
    assert bad["failing_rows"] == planted["rows"]
    # the old "fix anything cheap" rule would silently correct it; with the registry it is a FAIL
    broad = INVOICES.replace(REGISTERED, HEURISTIC.format(flag="unit_corrected"))
    rep2 = build_snapshot(probe, cutoff, pipeline_variant(tmp_path / "broad", {"models/invoices.sql": broad}),
                          out_root=tmp_path / "lake")
    assert rep2.status == "FAILED"
    assert failing(rep2) == {"invoices:unit_corrected_iff_registered_scope",
                             "invoices:amount_equals_export_outside_registered_incidents"}
    # ... and hiding the correction behind a false flag does not help either
    hidden = INVOICES.replace(REGISTERED, HEURISTIC.format(flag="_flag") + "\n    FALSE AS unit_corrected,")
    rep3 = build_snapshot(probe, cutoff, pipeline_variant(tmp_path / "hidden", {"models/invoices.sql": hidden}),
                          out_root=tmp_path / "lake")
    assert "invoices:amount_equals_export_outside_registered_incidents" in failing(rep3)


def test_a_registration_whose_rows_were_not_affected_fails(small_ws, train_build, tmp_path):
    with duckdb.connect(str(warehouse_path(small_ws, train_build)), read_only=True) as con:
        region, month = con.execute("""
            SELECT region, revenue_month FROM silver.invoices
            WHERE invoice_type = 'charge' AND NOT unit_corrected
            GROUP BY ALL ORDER BY count(*) DESC, 1, 2 LIMIT 1""").fetchone()
    wide = INCIDENTS.replace(
        "     'finance-controller@company (CHG-2026-0412)')",
        "     'finance-controller@company (CHG-2026-0412)'),\n"
        f"    ('INC-WRONG', '{region}', '{month}', 'charge', 100, 'over-broad registration', 'nobody')")
    assert wide != INCIDENTS
    pipe = pipeline_variant(tmp_path, {"models/ref_known_incidents.sql": wide})
    rep = build_snapshot(small_ws, CUTOFFS["train"], pipe, out_root=tmp_path / "lake")
    assert rep.status == "FAILED"
    assert {"invoices:registered_incident_rows_were_affected", "invoices:charge_amount_on_price_book"} <= failing(rep)


# =========================================================================== price-book validity
@pytest.mark.parametrize("cutoff, prices, fix", [
    # plus still at 5900 after the 2026-08-01 rise
    ("drift", PRICES.replace("    ('plus',  5900, DATE '2000-01-01', DATE '2026-07-31'),\n"
                             "    ('plus',  6900, DATE '2026-08-01', DATE '9999-12-31'),",
                             "    ('plus',  5900, DATE '2000-01-01', DATE '9999-12-31'),"), True),
    # 6900 already valid before the rise
    ("train", PRICES.replace("DATE '2026-07-31'", "DATE '2025-12-31'")
                    .replace("6900, DATE '2026-08-01'", "6900, DATE '2026-01-01'"), False),
])
def test_charges_must_match_the_price_valid_when_issued(small_ws, tmp_path, cutoff, prices, fix):
    assert prices != PRICES
    files = {"models/ref_plan_prices.sql": prices, **({"models/stg_invoices.sql": GOOD_FIX} if fix else {})}
    rep = build_snapshot(small_ws, CUTOFFS[cutoff], pipeline_variant(tmp_path, files), out_root=tmp_path / "lake")
    assert rep.status == "FAILED"
    assert failing(rep) == {"invoices:charge_amount_on_price_book"}


def test_correct_v2_fix_builds_drift_and_monitor_with_dated_prices(small_ws, tmp_path):
    pipe = pipeline_variant(tmp_path, {"models/stg_invoices.sql": GOOD_FIX})
    for name in ("drift", "monitor"):
        rep = build_snapshot(small_ws, CUTOFFS[name], pipe, out_root=tmp_path / "lake")
        assert rep.status == "READY", rep.reasons
        assert SCOPE_TESTS <= {t["name"] for t in rep.tests if t["outcome"] == "PASS"}
        with duckdb.connect(str(tmp_path / "lake" / rep.build_id / "warehouse.duckdb"), read_only=True) as con:
            plus = dict(con.execute("""
                SELECT i.issued_at >= TIMESTAMP '2026-08-01' AS after_rise, list(DISTINCT i.amount_fen)
                FROM silver.invoices i JOIN silver.subscriptions s USING (customer_id)
                WHERE i.invoice_type = 'charge' AND s.plan = 'plus' AND NOT i.unit_corrected
                GROUP BY 1""").fetchall())
        assert plus == {False: [5900], True: [6900]}


def test_workspace_coerces_str_root(tmp_path):
    ws = Workspace(str(tmp_path / "ws")).ensure()
    assert isinstance(ws.root, Path)
    assert ws.sources.is_dir()
