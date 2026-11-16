"""Regression tests for confirmed review findings (package F1).

DSH-CORR-001  propose_pipeline_change could read arbitrary local files (incl. custody/) through a
              CTE named like a quoted file path: the screen's CTE set was statement-wide, and the
              build connection had external access on. Fixed twice: per-scope CTE resolution in the
              screen, and a sandboxed build connection (lake.runner.sandbox) whatever the screen misses.
DSH-CORR-002  analyze_sql had the same statement-wide CTE set (fail-open), which also let agents read
              DuckDB's system catalog (warehouse path, restricted bronze column names) via run_sql.
DE-2          pipeline.regression_diff compared only the served score cutoff, so a change to rows
              dated after it was never compared with any baseline.

No test here reads custody/: file-read attempts target a synthetic parquet file in tmp_path.
"""
from __future__ import annotations

from pathlib import Path
import shutil

import duckdb
import pandas as pd
import pytest
import yaml

from dsharness import tools as T
from dsharness.common import open_readonly
from dsharness.context.catalog import load_catalog
from dsharness.evidence import EvidenceStore
from dsharness.lake import runner
from dsharness.lake.build import DEFAULT_PIPELINE, build_snapshot
from dsharness.tools import de as D
from dsharness.verify import sql_static
from dsharness.verify.sql_static import analyze_sql, table_references
from dsharness.workspace import CUTOFFS, Workspace

STG = (DEFAULT_PIPELINE / "models" / "stg_invoices.sql").read_text()
AMOUNT_LINE = "    amount_cents                                     AS amount_fen_raw,"
GOOD_FIX = STG.replace(AMOUNT_LINE, "    COLUMNS('^amount_(cents|minor)$')                AS 'amount_fen_raw',")
assert GOOD_FIX != STG
SECRET_VALUES = ("SYNTH-SECRET-0", "SYNTH-SECRET-1")


def shadow(path: str) -> str:
    """The DSH-CORR-001 shape: an out-of-scope reference "hidden" by a CTE of the same name."""
    return f'SELECT t.* FROM "{path}" AS t, (WITH "{path}" AS (SELECT 1 AS one) SELECT one FROM "{path}") AS q'


@pytest.fixture(scope="module")
def secret_file(tmp_path_factory) -> str:
    p = tmp_path_factory.mktemp("synthetic") / "secret.parquet"
    pd.DataFrame({"s": list(SECRET_VALUES)}).to_parquet(p)
    return str(p)


@pytest.fixture(scope="module")
def cat():
    return load_catalog()


def private_ws(base: Workspace, root: Path) -> Workspace:
    """Shares base's lake and sources (read-only); own registry and runs."""
    root.mkdir(parents=True)
    (root / "lake").symlink_to(base.lake, target_is_directory=True)
    (root / "sources").symlink_to(base.sources, target_is_directory=True)
    (root / "custody").mkdir()
    for f in base.custody.iterdir():
        if f.suffix in (".parquet", ".json"):
            (root / "custody" / f.name).symlink_to(f)
    return Workspace(root).ensure()


@pytest.fixture(scope="module")
def ws(small_ws, tmp_path_factory) -> Workspace:
    return private_ws(small_ws, tmp_path_factory.mktemp("security") / "ws")


@pytest.fixture(scope="module")
def de(ws):
    ctx = T.make_context(ws, "score", "data_engineer", "agent:de-sec")
    yield ctx
    ctx.close()


@pytest.fixture(scope="module")
def analyst(ws):
    ctx = T.make_context(ws, "score", "analyst", "agent:analyst-sec")
    yield ctx
    ctx.close()


# =========================================================================== DSH-CORR-002: analyze_sql
SCOPE_ATTACKS = {
    "finding_exact": shadow("/abs/path.parquet"),
    "plain_name": "SELECT * FROM foo, (WITH foo AS (SELECT 1 AS one) SELECT one FROM foo) q",
    "where_subquery": 'SELECT * FROM "/abs/p.parquet" WHERE EXISTS (WITH "/abs/p.parquet" AS (SELECT 1) '
                      'SELECT 1 FROM "/abs/p.parquet")',
    "union_branch": 'SELECT * FROM "/abs/p.parquet" UNION ALL (WITH "/abs/p.parquet" AS (SELECT 1 AS a) '
                    'SELECT * FROM "/abs/p.parquet")',
    "cte_body_sibling": "WITH a AS (WITH x AS (SELECT 1 AS v) SELECT * FROM x) SELECT * FROM x, a",
    "forward_reference": "WITH a AS (SELECT * FROM b), b AS (SELECT 1 AS x) SELECT * FROM a",
    "mixed_case_shadow": "SELECT * FROM Foo, (WITH foo AS (SELECT 1 AS one) SELECT one FROM FOO) q",
}
SYSTEM_CATALOG = {
    "shadowed_databases": "SELECT t.path FROM duckdb_databases AS t, "
                          "(WITH duckdb_databases AS (SELECT 1 AS one) SELECT one FROM duckdb_databases) AS q",
    "shadowed_columns": "SELECT t.column_name FROM duckdb_columns AS t, "
                        "(WITH duckdb_columns AS (SELECT 1 AS one) SELECT one FROM duckdb_columns) AS q",
    "cte_named_like_a_view": "WITH duckdb_tables AS (SELECT 1 AS a) SELECT * FROM duckdb_tables",
    "plain_view": "SELECT database_name, path FROM duckdb_databases",
    "information_schema": "SELECT * FROM information_schema.columns",
    "pg_catalog": "SELECT * FROM pg_catalog.pg_class",
    "pragma_view": "SELECT * FROM pragma_database_list",
    "system_main": "SELECT * FROM system.main.duckdb_tables",
}
LEGIT_CTES = {
    "same_scope": "WITH x AS (SELECT * FROM silver.invoices WHERE NOT is_test_account) SELECT count(*) FROM x",
    "outer_cte_in_subquery": "WITH x AS (SELECT customer_id FROM silver.customers WHERE NOT is_test_account) "
                             "SELECT * FROM (SELECT * FROM x) s",
    "outer_cte_in_where": "WITH x AS (SELECT customer_id FROM silver.customers WHERE NOT is_test_account) "
                          "SELECT count(*) FROM silver.invoices i WHERE i.customer_id IN (SELECT customer_id FROM x)",
    "chained": "WITH a AS (SELECT customer_id FROM silver.customers WHERE NOT is_test_account), "
               "b AS (SELECT * FROM a) SELECT count(*) FROM b",
    "recursive": "WITH RECURSIVE r AS (SELECT 1 AS n UNION ALL SELECT n + 1 FROM r WHERE n < 5) SELECT * FROM r",
    "mixed_case": "WITH Foo AS (SELECT 1 AS a) SELECT * FROM foo",
    "join_two_ctes": "WITH a AS (SELECT customer_id FROM silver.customers WHERE NOT is_test_account), "
                     "b AS (SELECT customer_id FROM silver.customers WHERE NOT is_test_account) "
                     "SELECT count(*) FROM a JOIN b USING (customer_id)",
    "nested_with_in_subquery": "SELECT * FROM (WITH z AS (SELECT 1 AS v) SELECT v FROM z) q",
    "quoted_same_scope": 'WITH "My Cte" AS (SELECT 1 AS a) SELECT * FROM "My Cte"',
}


@pytest.mark.parametrize("name", sorted(SCOPE_ATTACKS))
def test_analyze_sql_resolves_cte_names_per_scope(cat, name):
    r = analyze_sql(SCOPE_ATTACKS[name], cat)
    assert not r.ok, name
    assert any("unknown or unqualified table" in v for v in r.violations), r.violations


@pytest.mark.parametrize("name", sorted(SYSTEM_CATALOG))
def test_analyze_sql_refuses_the_system_catalog(cat, name):
    r = analyze_sql(SYSTEM_CATALOG[name], cat)
    assert not r.ok, name
    assert any("system catalog" in v or "not in catalog" in v for v in r.violations), r.violations


@pytest.mark.parametrize("name", sorted(LEGIT_CTES))
def test_legitimate_ctes_still_pass(cat, name):
    r = analyze_sql(LEGIT_CTES[name], cat)
    assert r.ok, (name, r.violations)


def test_table_references_marks_only_in_scope_ctes():
    import sqlglot
    refs = table_references(sqlglot.parse_one(shadow("/abs/p.parquet"), dialect="duckdb"))
    assert [(t.name, is_cte) for t, is_cte in refs] == [("/abs/p.parquet", False), ("/abs/p.parquet", True)]


def test_scope_failure_fails_closed(cat, monkeypatch):
    def boom(*a, **k):
        raise sql_static.SqlglotError("scope exploded")
    monkeypatch.setattr(sql_static, "traverse_scope", boom)
    r = analyze_sql(LEGIT_CTES["same_scope"], cat)
    assert not r.ok and "table references could not be resolved by scope" in r.violations
    problems = D.screen_sql(LEGIT_CTES["same_scope"], "models/x.sql", D.MODEL_SCHEMAS)
    assert any("could not be resolved by scope" in p for p in problems)
    assert any("unqualified table 'x'" in p for p in problems)        # no name accepted as a CTE


def test_run_sql_refuses_shadowed_file_and_catalog_reads(analyst, secret_file):
    """Before the fix the file read died at execution (query_error) and the catalog reads ran."""
    for sql in (shadow(secret_file), SYSTEM_CATALOG["shadowed_databases"], SYSTEM_CATALOG["shadowed_columns"]):
        r = T.run_sql(analyst, sql)
        assert r.get("kind") == "refused", r
        assert not any(v in str(r) for v in SECRET_VALUES)


def test_submit_answer_with_shadowed_catalog_sql_is_flagged(analyst):
    sql = SYSTEM_CATALOG["shadowed_databases"]
    r = T.submit_answer(analyst, question="Which database file backs this build?",
                        answer_rows=[{"path": "x"}], sql=sql)
    assert r["tier"] == "FLAGGED", r


# =========================================================================== DSH-CORR-001: pipeline CI
@pytest.mark.parametrize("schemas", [D.MODEL_SCHEMAS, D.TEST_SCHEMAS])
def test_screen_sql_refuses_cte_shadowed_paths_and_catalog(secret_file, schemas):
    for sql in (shadow(secret_file), shadow("ws/custody/x.parquet"), shadow("**/custody/*.parquet"),
                SCOPE_ATTACKS["union_branch"], SCOPE_ATTACKS["cte_body_sibling"]):
        problems = D.screen_sql(sql, "models/leak.sql", schemas)
        assert any("unqualified table" in p for p in problems), (sql, problems)
    for sql in SYSTEM_CATALOG.values():
        assert D.screen_sql(sql, "models/leak.sql", schemas), sql


def test_screen_sql_keeps_accepting_the_governed_pipeline():
    for path in sorted((DEFAULT_PIPELINE / "models").glob("*.sql")):
        assert D.screen_sql(path.read_text(), f"models/{path.name}", D.MODEL_SCHEMAS, may_read_pii=True) == [], path
    spec = yaml.safe_load((DEFAULT_PIPELINE / "schema.yml").read_text())
    assert D.screen_schema_sql(spec) == []


def test_pipeline_change_cannot_exfiltrate_a_file(ws, de, secret_file):
    spec = yaml.safe_load((DEFAULT_PIPELINE / "schema.yml").read_text())
    spec["models"]["leak"] = {"expressions": {"show_rows": "false"}}     # every row "fails" -> sampled back
    r = T.propose_pipeline_change(de, {"models/leak.sql": shadow(secret_file) + "\n",
                                       "schema.yml": yaml.safe_dump(spec, sort_keys=False)}, "add a helper model")
    assert r["kind"] == "refused", r
    assert any("unqualified table" in p for p in r["problems"]), r["problems"]
    assert not (ws.runs / r["change_id"] / "lake").exists()            # nothing executed
    assert not any(v in str(r) for v in SECRET_VALUES)


def pipeline_variant(root: Path, files: dict[str, str]) -> Path:
    shutil.copytree(DEFAULT_PIPELINE, root, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for rel, content in files.items():
        (root / rel).write_text(content)
    return root


def test_build_connection_cannot_read_files_even_if_the_screen_misses(small_ws, secret_file, tmp_path):
    """Defence in depth: pipeline SQL that never went through any screen still cannot read a file."""
    pipe = pipeline_variant(tmp_path / "pipe", {
        "models/leak.sql": shadow(secret_file),
        "models/leak_fn.sql": f"SELECT * FROM read_parquet('{secret_file}')",
        "models/leak_attach.sql": "SELECT 1 AS one",
    })
    spec = yaml.safe_load((pipe / "schema.yml").read_text())
    spec["models"]["leak_attach"] = {"expressions": {"peek": f"EXISTS (SELECT * FROM \"{secret_file}\")"}}
    (pipe / "schema.yml").write_text(yaml.safe_dump(spec, sort_keys=False))
    rep = build_snapshot(small_ws, CUTOFFS["score"], pipe, out_root=tmp_path / "lake")
    by_name = {m["name"]: m for m in rep.models}
    for leak in ("leak", "leak_fn"):
        assert by_name[leak]["status"] == "ERROR" and "Permission Error" in by_name[leak]["error"], by_name[leak]
    peek = next(t for t in rep.tests if t["name"] == "leak_attach:peek")
    assert peek["outcome"] == "ERROR" and "Permission Error" in peek["error"], peek
    assert all(m["status"] == "OK" for n, m in by_name.items() if not n.startswith("leak"))   # honest models build
    assert not any(v in str(rep.__dict__) for v in SECRET_VALUES)
    with open_readonly(tmp_path / "lake" / rep.build_id / "warehouse.duckdb") as con:
        assert con.execute("SELECT COUNT(*) FROM silver.invoices").fetchone()[0] > 0


def test_runner_refuses_an_unsandboxed_connection(tmp_path):
    con = duckdb.connect(str(tmp_path / "w.duckdb"))
    try:
        with pytest.raises(RuntimeError, match="sandboxed"):
            runner.run_models(con, DEFAULT_PIPELINE)
        with pytest.raises(RuntimeError, match="sandboxed"):
            runner.run_tests(con, DEFAULT_PIPELINE, set())
        runner.sandbox(con)
        with pytest.raises(duckdb.Error):
            con.execute("SET enable_external_access = true")             # locked: cannot be undone
    finally:
        con.close()


def test_default_builds_stay_ready(build_reports):
    """The sandbox changes nothing for the governed pipeline: same READY/FAILED pattern as before."""
    for name in ("train", "score"):
        assert build_reports[CUTOFFS[name]].status == "READY"
    drift = build_reports[CUTOFFS["drift"]]
    assert drift.status == "FAILED"
    assert {m["name"]: m["status"] for m in drift.models}["stg_invoices"] == "ERROR"


# =========================================================================== DE-2: drift regression
def outcomes(ws, r) -> dict:
    by_id = {x.evidence_id: x for x in EvidenceStore(ws).all()}
    return {by_id[i].check_id: by_id[i].outcome for i in r["evidence_ids"]}


def test_correct_d8_fix_passes_the_drift_comparison(ws, de):
    r = T.propose_pipeline_change(de, {"models/stg_invoices.sql": GOOD_FIX},
                                  "select the amount column of either billing schema version")
    assert outcomes(ws, r) == {"pipeline.ci_build": "PASS", "pipeline.regression_diff": "PASS",
                               "pipeline.oracle_independence": "PASS"}, r["diff"]
    assert r["gate"]["verdict"] == "ALLOW"
    drift = r["diff"]["drift"]
    assert drift["outcome"] == "PASS" and drift["cutoff"] == D.CI_CUTOFF and drift["baseline_status"] == "FAILED"
    assert {"silver.customers", "silver.subscriptions", "silver.usage_monthly", "silver.tickets",
            "silver.retention_contacts"} <= set(drift["compared_tables"])
    assert all(t["outcome"] == "PASS" for t in drift["tables"])
    assert [x["table"] for x in drift["not_compared"]] == ["silver.invoices"]   # the baseline could not build it
    assert "cannot build" in drift["not_compared"][0]["reason"]
    assert r["diff"]["changed_tables_by_cutoff"] == {D.REGRESSION_CUTOFF: [], D.CI_CUTOFF: []}


def test_change_to_rows_after_the_served_cutoff_fails_regression(ws, de):
    """Before DE-2 this was ALLOW: the served cutoff has no usage after 2026-06, so it diffed clean."""
    trimmed = "SELECT * FROM staging.stg_usage WHERE usage_month < '2026-07'\n"
    r = T.propose_pipeline_change(de, {"models/stg_invoices.sql": GOOD_FIX, "models/usage_monthly.sql": trimmed},
                                  "schema v2, and drop the unfinished quarter")
    out = outcomes(ws, r)
    assert out["pipeline.ci_build"] == "PASS" and out["pipeline.regression_diff"] == "FAIL", r["diff"]
    assert r["diff"]["served_cutoff_outcome"] == "PASS"                   # served months really are untouched
    drift = r["diff"]["drift"]
    assert drift["outcome"] == "FAIL" and drift["changed_tables"] == ["silver.usage_monthly"]
    usage = next(t for t in drift["tables"] if t["table"] == "silver.usage_monthly")
    assert usage["rows_only_in_baseline"] > 0 and usage["rows_only_in_candidate"] == 0
    assert usage["baseline_sample"] and all(s["usage_month"] >= "2026-07" for s in usage["baseline_sample"])
    assert r["diff"]["changed_tables_by_cutoff"] == {D.REGRESSION_CUTOFF: [], D.CI_CUTOFF: ["silver.usage_monthly"]}
    assert D.CI_CUTOFF in r["diff"]["reason"]
    assert r["gate"]["verdict"] == "REJECT"


@pytest.fixture(scope="module")
def fixed_drift_build(small_ws, tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("drift_fix")
    pipe = pipeline_variant(root / "pipe", {"models/stg_invoices.sql": GOOD_FIX})
    rep = build_snapshot(small_ws, D.CI_CUTOFF, pipe, out_root=root / "lake")
    assert rep.status == "READY"
    return {"status": rep.status, "warehouse": str(root / "lake" / rep.build_id / "warehouse.duckdb")}


def test_drift_baseline_must_come_from_the_same_export(small_ws, fixed_drift_build, tmp_path, monkeypatch):
    res = D.drift_regression(small_ws, fixed_drift_build, tmp_path / "baseline")
    assert res["outcome"] == "PASS" and res["baseline_origin"] == "lake"
    assert not (tmp_path / "baseline").exists()
    # the lake's baseline was built from another export: CI rebuilds the governed pipeline on this one
    monkeypatch.setattr(D, "source_digest", lambda ws, cutoff: "not-the-lake-export")
    res = D.drift_regression(small_ws, fixed_drift_build, tmp_path / "baseline")
    assert res["outcome"] == "PASS" and res["baseline_origin"] == "rebuilt from the same export"
    assert (tmp_path / "baseline" / res["baseline_build_id"] / "warehouse.duckdb").exists()


def test_drift_regression_never_turns_a_missing_comparison_into_a_pass(small_ws, fixed_drift_build, tmp_path,
                                                                     monkeypatch):
    crashed = D.drift_regression(small_ws, {"status": "CRASHED", "error": "boom"}, tmp_path / "b")
    assert crashed["outcome"] == "ERROR"

    def no_baseline(*a, **k):
        raise FileNotFoundError("no export")
    monkeypatch.setattr(D, "_same_export_baseline", no_baseline)
    assert D.drift_regression(small_ws, fixed_drift_build, tmp_path / "b")["outcome"] == "ERROR"
    monkeypatch.setattr(D, "_same_export_baseline",
                        lambda *a, **k: ("x", {"status": "FAILED", "models": [
                            {"name": "invoices", "schema": "silver", "status": "SKIPPED"}]}, Path("/nonexistent"), "lake"))
    assert D.drift_regression(small_ws, fixed_drift_build, tmp_path / "b")["outcome"] == "INCONCLUSIVE"
    assert D._combine("PASS", "INCONCLUSIVE") == "INCONCLUSIVE" and D._combine("ERROR", "FAIL") == "FAIL"
    assert D._combine("PASS", "PASS") == "PASS" and D._combine("ERROR", "PASS") == "ERROR"
