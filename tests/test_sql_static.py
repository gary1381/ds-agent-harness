"""Static SQL analyzer: hard violations refuse the query; soft warnings label the result."""
from __future__ import annotations

import logging

from hypothesis import given, settings, strategies as st
import pytest

from dsharness.common import open_readonly
from dsharness.context import load_catalog
from dsharness.lake.build import warehouse_path
from dsharness.verify.sql_static import StaticReport, analyze_sql

logging.getLogger("sqlglot").setLevel(logging.ERROR)    # 'falling back to Command' notices are expected here
CAT = load_catalog()


def rep(sql: str) -> StaticReport:
    r = analyze_sql(sql, CAT)
    assert isinstance(r, StaticReport)
    return r


def blocked(sql: str, fragment: str) -> StaticReport:
    r = rep(sql)
    assert not r.ok, sql
    assert any(fragment.lower() in v.lower() for v in r.violations), (sql, r.violations)
    return r


# ------------------------------------------------------------------ hard violations
def test_blocks_bronze_even_without_naming_pii():
    r = blocked("SELECT * FROM bronze.crm_customers", "restricted")
    assert r.tables == []           # a refused table is never listed as read
    blocked("SELECT count(*) FROM BRONZE.billing_invoices", "restricted")
    blocked('SELECT * FROM "bronze"."crm_customers"', "restricted")
    blocked("WITH x AS (SELECT * FROM bronze.crm_customers) SELECT count(*) FROM x", "restricted")
    blocked("SELECT * FROM silver.customers WHERE customer_id IN (SELECT customer_id FROM bronze.crm_customers)",
            "restricted")


@pytest.mark.parametrize("sql, fragment", [
    ("COPY (SELECT * FROM silver.invoices) TO '/tmp/out.csv'", "COPY"),
    ("COPY silver.invoices FROM '/tmp/in.csv'", "COPY"),
    ("ATTACH '/tmp/other.duckdb' AS other", "ATTACH"),
    ("DETACH other", "DETACH"),
    ("INSTALL httpfs", "INSTALL"),
    ("LOAD httpfs", "LOAD"),
    ("SET enable_external_access = true", "SET"),
    ("PRAGMA table_info('silver.invoices')", "PRAGMA"),
    ("CALL pragma_version()", "CALL"),
    ("CREATE TABLE silver.x AS SELECT 1", "CREATE"),
    ("INSERT INTO silver.invoices SELECT * FROM silver.invoices", "INSERT"),
    ("UPDATE silver.invoices SET amount_fen = 0", "UPDATE"),
    ("DELETE FROM silver.invoices", "DELETE"),
    ("DROP TABLE silver.invoices", "DROP"),
    ("USE bronze", "USE"),
    ("CHECKPOINT", "CHECKPOINT"),
    ("WITH a AS (SELECT 1) INSERT INTO t SELECT * FROM a", "INSERT"),
])
def test_blocks_non_select_statements(sql, fragment):
    r = blocked(sql, fragment)
    assert not r.read_only


@pytest.mark.parametrize("sql, fragment", [
    ("SELECT * FROM read_csv('/etc/passwd')", "read_csv"),
    ("SELECT * FROM read_csv_auto('/etc/passwd')", "read_csv"),
    ("SELECT * FROM read_parquet('s3://bucket/x.parquet')", "read_parquet"),
    ("SELECT * FROM parquet_scan('x.parquet')", "parquet_scan"),
    ("SELECT * FROM read_json('x.json')", "read_json"),
    ("SELECT * FROM glob('/home/*')", "glob"),
    ("SELECT read_text('/etc/passwd')", "read_text"),
    ("SELECT content FROM read_blob('/etc/shadow')", "read_blob"),
    ("SELECT * FROM query('SELECT * FROM bronze.crm_customers')", "query"),
    ("SELECT * FROM query_table('bronze.crm_customers')", "query_table"),
    ("SELECT * FROM duckdb_settings()", "duckdb_settings"),
    ("SELECT * FROM duckdb_secrets()", "duckdb_secrets"),
    ("SELECT getenv('HOME')", "getenv"),
    ("SELECT current_setting('enable_external_access')", "current_setting"),
    ("SELECT * FROM '/etc/passwd'", "unknown or unqualified table"),
    ("SELECT * FROM 'data.parquet'", "unknown or unqualified table"),
    ("SELECT * FROM invoices", "unknown or unqualified table"),          # unqualified: also blocks pandas replacement scans
    ("SELECT * FROM information_schema.tables", "not in catalog"),
    ("SELECT * FROM silver.secret_table", "not in catalog"),
    ("SELECT * FROM other.silver.invoices", "cross-database"),
    ("SELECT COLUMNS('.*') FROM silver.customers WHERE NOT is_test_account", "COLUMNS"),
])
def test_blocks_file_system_and_dynamic_access(sql, fragment):
    blocked(sql, fragment)


def test_blocks_multiple_statements_and_garbage():
    r = blocked("SELECT 1; SELECT 2", "multiple statements")
    assert not r.single_statement
    blocked("SELECT * FROM silver.invoices WHERE NOT is_test_account; DROP TABLE silver.invoices", "multiple")
    blocked("SELECT FROM WHERE", "parse error")
    blocked("EXPORT DATABASE '/tmp/dump'", "parse error")
    blocked("", "empty")
    blocked("   ;  ", "no SQL statement")


def test_blocks_pii_columns():
    r = blocked("SELECT email FROM bronze_safe.crm_customers", "PII column referenced: email")
    assert r.pii_columns == ["email"]
    r = blocked("SELECT c.customer_id, c.phone FROM silver.customers c WHERE NOT c.is_test_account", "c.phone")
    assert r.pii_columns == ["c.phone"]
    blocked('SELECT "Birth_Year" FROM silver.customers', "PII")
    blocked("SELECT count(*) FROM silver.customers WHERE email LIKE '%@qa.internal'", "email")


# ------------------------------------------------------------------ soft warnings + tier
def test_uncertified_raw_export_is_allowed_but_flagged():
    r = rep("SELECT SUM(amount_cents) FROM bronze_safe.billing_invoices WHERE issued_at >= '2026-03-01'")
    assert r.ok and r.read_only and r.single_statement
    assert r.tier == "uncertified" and r.tables == ["bronze_safe.billing_invoices"] and r.schemas == ["bronze_safe"]
    assert any(w.startswith("uncertified table bronze_safe.billing_invoices") for w in r.warnings)
    assert any("missing exclude_test_accounts" in w for w in r.warnings)
    s = rep("SELECT count(*) FROM staging.stg_invoices")
    assert s.ok and s.tier == "uncertified" and any("tier staging" in w for w in s.warnings)
    mixed = rep("SELECT count(*) FROM silver.invoices i JOIN bronze_safe.billing_invoices b USING (invoice_id) "
                "WHERE NOT i.is_test_account")
    assert mixed.tier == "uncertified"                          # one raw table taints the result


def test_missing_test_account_filter_warns():
    r = rep("SELECT revenue_month, SUM(amount_fen) FROM silver.invoices GROUP BY 1")
    assert r.ok and r.tier == "certified"
    assert [w for w in r.warnings if "missing exclude_test_accounts" in w and "silver.invoices" in w]
    for good in [
        "SELECT revenue_month, SUM(amount_fen) FROM silver.invoices WHERE NOT is_test_account GROUP BY 1",
        "SELECT revenue_month, SUM(amount_fen) FILTER (WHERE NOT is_test_account) FROM silver.invoices GROUP BY 1",
        "SELECT SUM(CASE WHEN is_test_account THEN 0 ELSE amount_fen END) FROM silver.invoices",
        "SELECT count(*) FROM silver.tickets t JOIN silver.customers c ON c.customer_id = t.customer_id "
        "AND NOT c.is_test_account",
        "WITH x AS (SELECT * FROM silver.invoices WHERE NOT is_test_account) SELECT count(*) FROM x",
    ]:
        g = rep(good)
        assert g.ok and g.tier == "certified" and not g.warnings, (good, g.warnings)
    # a projection is not a filter
    proj = rep("SELECT is_test_account, count(*) FROM silver.customers GROUP BY 1")
    assert any("missing exclude_test_accounts" in w for w in proj.warnings)
    # tables without customer rows need no filter
    assert not rep("SELECT * FROM silver.finance_closed_revenue WHERE month = '2026-03'").warnings
    assert not rep("SELECT plan, price_fen FROM silver.ref_plan_prices").warnings
    assert not rep("SELECT incident_id, region, month, factor FROM silver.ref_known_incidents").warnings


def test_retention_program_tables_are_catalogued_with_tiers():
    # enrolment is randomised among at-risk (never test) customers: the certified table needs no filter
    ok = rep("SELECT campaign_month, arm, count(*) AS n FROM silver.retention_contacts GROUP BY 1, 2")
    assert ok.ok and ok.tier == "certified" and not ok.warnings and ok.tables == ["silver.retention_contacts"]
    raw = rep("SELECT arm, count(*) FROM bronze_safe.crm_retention_contacts GROUP BY 1")
    assert raw.ok and raw.tier == "uncertified"
    assert any("uncertified table bronze_safe.crm_retention_contacts" in w for w in raw.warnings)
    stg = rep("SELECT count(*) FROM staging.stg_retention_contacts")
    assert stg.ok and stg.tier == "uncertified"
    blocked("SELECT * FROM bronze.crm_retention_contacts", "restricted")


def test_mutable_and_post_outcome_columns_are_reported():
    r = rep("SELECT customer_id, monthly_price_fen, cancel_reason FROM silver.subscriptions s "
            "JOIN silver.customers c USING (customer_id) WHERE NOT c.is_test_account")
    assert r.ok
    assert r.mutable_or_post_outcome_columns == ["silver.subscriptions.cancel_reason",
                                                 "silver.subscriptions.monthly_price_fen"]
    assert any("post_outcome" in w for w in r.warnings) and any("mutable_current" in w for w in r.warnings)
    star = rep("SELECT * FROM silver.subscriptions")
    assert set(star.mutable_or_post_outcome_columns) == {"silver.subscriptions.monthly_price_fen",
                                                         "silver.subscriptions.ended_at",
                                                         "silver.subscriptions.cancel_reason"}
    qualified_star = rep("SELECT s.* FROM silver.subscriptions s")
    assert len(qualified_star.mutable_or_post_outcome_columns) == 3
    excluded = rep("SELECT * EXCLUDE (monthly_price_fen, ended_at, cancel_reason) FROM silver.subscriptions")
    assert excluded.mutable_or_post_outcome_columns == []
    via_cte = rep("WITH x AS (SELECT customer_id, ended_at FROM silver.subscriptions) SELECT count(ended_at) FROM x")
    assert via_cte.mutable_or_post_outcome_columns == ["silver.subscriptions.ended_at"]
    correlated = rep("SELECT s.customer_id FROM silver.subscriptions s WHERE EXISTS "
                     "(SELECT 1 FROM silver.tickets t WHERE t.customer_id = s.customer_id AND t.created_at < s.ended_at)")
    assert correlated.mutable_or_post_outcome_columns == ["silver.subscriptions.ended_at"]
    raw = rep("SELECT monthly_price_cents FROM bronze_safe.billing_subscriptions")
    assert raw.mutable_or_post_outcome_columns == ["bronze_safe.billing_subscriptions.monthly_price_cents"]
    # same column name on a static table is not reported
    assert rep("SELECT plan FROM silver.subscriptions s JOIN silver.customers c USING (customer_id) "
               "WHERE NOT c.is_test_account").mutable_or_post_outcome_columns == []


def test_constant_queries_are_not_certified():
    assert any("reads no warehouse tables" in w for w in rep("SELECT x FROM generate_series(1, 3) t(x)").warnings)
    r = rep("SELECT 4242 AS net_revenue_fen")
    assert r.ok and r.tier == "uncertified" and r.tables == []
    assert any("reads no warehouse tables" in w for w in r.warnings)


def test_allowed_duckdb_constructs(small_ws, train_build):
    ok_sql = [
        "SELECT strftime(m, '%Y-%m') AS month FROM range(DATE '2026-01-01', DATE '2026-04-01', INTERVAL 1 MONTH) t(m)",
        "SELECT x FROM generate_series(1, 3) t(x)",
        "SELECT * FROM unnest([1, 2, 3]) t(x)",
        "SELECT invoice_id FROM silver.invoices WHERE NOT is_test_account "
        "QUALIFY row_number() OVER (PARTITION BY customer_id ORDER BY issued_at DESC) = 1",
        "SELECT region, SUM(amount_fen) AS v FROM silver.invoices WHERE NOT is_test_account GROUP BY ALL",
        "FROM silver.invoices SELECT count(*) WHERE NOT is_test_account",
        "SELECT revenue_month, SUM(amount_fen) FROM silver.invoices WHERE NOT is_test_account GROUP BY 1 "
        "UNION ALL SELECT month, SUM(net_revenue_fen) FROM silver.finance_closed_revenue GROUP BY 1",
    ]
    with open_readonly(warehouse_path(small_ws, train_build)) as con:
        for sql in ok_sql:
            r = rep(sql)
            assert r.ok and r.read_only, (sql, r.violations)
            con.execute(sql).fetchall()     # what the analyzer allows really runs on the read-only warehouse


@settings(max_examples=300, deadline=None, database=None)
@given(st.lists(st.sampled_from([
    "SELECT", "FROM", "WHERE", "silver.invoices", "bronze.crm_customers", "bronze_safe.billing_invoices", "(", ")",
    ",", "*", "1", "'x'", "AND", "JOIN", "ON", "WITH", "AS", "a", "b.c", "GROUP BY", ";", "UNION", "read_csv('x')",
    "range(3)", "EXISTS", "IN", "NOT", "is_test_account", "email", "COPY", "SET", "CASE", "WHEN", "THEN", "END",
    "QUALIFY", "VALUES", "t(m)", "::INT", "[1]", "LIMIT", "--", "\"",
]), min_size=1, max_size=14).map(" ".join) | st.text(max_size=60))
def test_analyzer_never_crashes_and_never_passes_bronze(sql):
    r = analyze_sql(sql, CAT)
    assert isinstance(r, StaticReport)
    assert r.ok == (not r.violations)
    if r.ok:
        assert r.single_statement and r.read_only
        assert not any(t.startswith("bronze.") for t in r.tables) and not r.pii_columns
