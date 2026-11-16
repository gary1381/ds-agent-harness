"""Data-engineering tools: build status, read-only pipeline source, and a governed CI for changes.

Trust boundary: an agent may propose new contents for `models/*.sql` and `schema.yml` only. Its
SQL then runs inside the build process on a writable DuckDB file (filesystem access is switched
off before any pipeline SQL runs), and every changed file is screened BEFORE anything executes (single SELECT, schemas
bronze/bronze_safe/staging/silver only, no file/system functions, no new reads of personal data,
test SQL restricted to staging/silver). Production runs candidate builds in a sandbox without the
custody mount or network; the screen is what keeps this reference process honest.

CI for a change (all evidence producer `ci:pipeline`, stage `pipeline_merge`):

  pipeline.ci_build            the drift cutoff builds READY into runs/<change_id>/lake: contracts,
                               models and every baseline test, including the Finance-close
                               reconciliation. Removing or editing a baseline test is FAIL
                               ('tests removed'); an empty oracle (nothing reconciled) is FAIL.
                               So is any change to the reference data the tests check against
                               (the price book and the registered-incident table): adding a price
                               or registering / widening a unit incident would make an off-book
                               charge pass, so it needs a human-approved change, not an agent's.
                               CI also reads the RAW export itself (bronze, harness SQL): a charge
                               below the cheapest plan outside every registered incident is FAIL,
                               even if the change "corrected" it in a staging model where the
                               schema.yml tests cannot see the original amount.
  pipeline.regression_diff     the already-served score cutoff (billing schema v1) rebuilt with the
                               change must reproduce every silver table of the current READY build
                               exactly. A fix that only handles schema v2 fails here, by design.
                               The drift build must also reproduce every silver table the governed
                               pipeline builds from the same drift export (its build FAILED on D8,
                               but usage, tickets, customers, ... built), so rows dated after the
                               served cutoff cannot change unseen. Tables the governed pipeline cannot
                               build there (invoices) have no baseline: reported as not_compared.

Pipeline SQL runs on a sandboxed build connection (lake.runner.sandbox: no file access, config
locked), and the screen resolves CTE names per scope (verify.sql_static.table_references).
  pipeline.oracle_independence a reconciliation only proves something if its two sides are
                               independent: the transitive upstream (model ref graph) of every model
                               on the "ours" side must not read finance_closed_revenue, and the oracle
                               model itself must be unchanged. Calibrating revenue to the Finance
                               table would otherwise pass reconciliation by construction.

The gate decision for `pipeline_merge` is returned; merging itself is a human/CI action.
"""
from __future__ import annotations

from collections import deque
from pathlib import Path
import re
import shutil
import uuid

import duckdb
import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.tokens import TokenType
import yaml

from ..common import append_jsonl, canonical_json, digest, file_digest, utcnow
from ..evidence import EvidenceStore, decide
from ..lake.build import DEFAULT_PIPELINE, PII_COLUMNS, build_id_for, build_snapshot, latest_ready_build, \
    load_report, source_digest, warehouse_path
from ..lake.contracts import SOURCE_CONTRACTS
from ..lake.runner import _toposort, pipeline_digest, sandbox
from ..ml.features import readonly_connection
from ..verify.sql_static import _ALLOWED_TABLE_FUNCS, _DENIED_FUNC_PREFIXES, _FORBIDDEN_NODES, \
    system_catalog_reference, table_references
from ..workspace import CUTOFFS, Workspace
from .analytics import catalog
from .context import ToolContext, ToolError, governed_tool, jsonable, resolve_cutoff

CHECK_VERSION = "1"
STAGE = "pipeline_merge"
CI_CUTOFF = CUTOFFS["drift"]                # newest export: billing schema v2
REGRESSION_CUTOFF = CUTOFFS["score"]        # already served: billing schema v1
ORACLE_MODELS = ("finance_closed_revenue",)     # silver copy of Finance's closed books (reviewed, frozen)
ORACLE_SOURCES = ("finance_closed_revenue",)    # bronze / bronze_safe export of the same record
# Reference data owned by Finance / Billing Ops that the baseline tests check against (price-book
# validity, registered unit incidents). Frozen for agent changes: editing them weakens the tests.
REFERENCE_MODELS = ("ref_known_incidents", "ref_plan_prices")
MODEL_SCHEMAS = {"bronze", "bronze_safe", "staging", "silver"}
TEST_SCHEMAS = {"staging", "silver"}        # test rows are shown to the agent: never raw sources
MAX_FILES = 20
MAX_FILE_CHARS = 100_000
MAX_RATIONALE_CHARS = 4_000
MAX_SAMPLE_ROWS = 5
CHANGES_LOG = "pipeline_changes.jsonl"      # registry/
_MODEL_PATH = re.compile(r"^models/([a-z][a-z0-9_]{0,62})\.sql$")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_SCHEMA_KEYS = {"models", "reconciliations"}
_MODEL_CFG_KEYS = {"columns", "expressions"}


# =========================================================================== paths
def check_pipeline_path(path) -> str:
    """'schema.yml' or 'models/<name>.sql'; anything else (traversal, other dirs/suffixes) is refused."""
    if not isinstance(path, str):
        raise ToolError("refused", f"pipeline path must be a string, got {type(path).__name__}")
    if path == "schema.yml" or _MODEL_PATH.match(path):
        return path
    raise ToolError("refused", f"path {path!r} is not changeable: only 'schema.yml' and 'models/<name>.sql' "
                               f"(lower-case name) are part of the pipeline")


def _pipeline_files(pipe: Path) -> list[str]:
    return ["schema.yml", *[f"models/{p.name}" for p in sorted((pipe / "models").glob("*.sql"))]]


# =========================================================================== status + read
def report_summary(rep: dict) -> dict:
    """Build report without bulk: failing models/tests with small samples, contracts without PII values."""
    pii = {c for cols in PII_COLUMNS.values() for c in cols}
    contracts = []
    for c in rep.get("contracts", []):
        errors = [{**e, "failure_case": "<redacted>"} if str(e.get("column")) in pii else e
                  for e in (c.get("errors") or [])[:MAX_SAMPLE_ROWS]]
        contracts.append({"table": c["table"], "schema_version": c["schema_version"], "ok": c["ok"],
                          "n_rows": c["n_rows"], "anomalies": c.get("anomalies", {}), "errors": errors})
    tests = rep.get("tests", [])
    return {"build_id": rep["build_id"], "cutoff": rep["cutoff"], "status": rep["status"],
            "pipeline_digest": rep["pipeline_digest"], "billing_schema_version": rep.get("billing_schema_version"),
            "reasons": rep.get("reasons", [])[:20],
            "models": [{k: m.get(k) for k in ("name", "schema", "status", "rows", "error")}
                       for m in rep.get("models", [])],
            "n_tests": len(tests), "n_tests_passed": sum(t["outcome"] == "PASS" for t in tests),
            "failing_tests": [{"name": t["name"], "outcome": t["outcome"], "failing_rows": t.get("failing_rows"),
                               "sample": (t.get("sample") or [])[:MAX_SAMPLE_ROWS], "error": t.get("error")}
                              for t in tests if t["outcome"] != "PASS"][:20],
            "contracts": contracts}


@governed_tool("pipeline_status")
def pipeline_status(ctx: ToolContext, cutoff: str) -> dict:
    """Status of the default (governed) pipeline's build for a cutoff, READY or FAILED."""
    c = resolve_cutoff(cutoff)
    bid = build_id_for(c, pipeline_digest(DEFAULT_PIPELINE))
    try:
        rep = load_report(ctx.ws, bid)
    except FileNotFoundError:
        raise ToolError("not_found", f"no build of the governed pipeline for cutoff {c}") from None
    return {**report_summary(rep), "files": _pipeline_files(DEFAULT_PIPELINE),
            "source_contracts": {t: {str(v): sorted(s.columns) for v, s in versions.items()}
                                 for t, versions in SOURCE_CONTRACTS.items() if len(versions) > 1},
            "ci_policy": {"build_cutoff": CI_CUTOFF, "regression_cutoff": REGRESSION_CUTOFF,
                          "regression_also_compares": f"{CI_CUTOFF}: every silver table the governed pipeline "
                                                      f"builds from that export",
                          "checks": ["pipeline.ci_build", "pipeline.regression_diff",
                                     "pipeline.oracle_independence"],
                          "frozen_files": [f"models/{m}.sql" for m in (*ORACLE_MODELS, *REFERENCE_MODELS)]}}


@governed_tool("read_pipeline_file")
def read_pipeline_file(ctx: ToolContext, path: str) -> dict:
    rel = check_pipeline_path(path)
    p = DEFAULT_PIPELINE / rel
    if not p.is_file():
        raise ToolError("not_found", f"{rel} does not exist; files: {_pipeline_files(DEFAULT_PIPELINE)}")
    return {"path": rel, "content": p.read_text(encoding="utf-8"), "sha256": file_digest(p),
            "pipeline_digest": pipeline_digest(DEFAULT_PIPELINE)}


# =========================================================================== SQL screening
def screen_sql(sql: str, where: str, schemas: set[str], *, may_read_pii: bool = False) -> list[str]:
    """Problems that forbid executing `sql` inside the build process (empty list == may run)."""
    try:
        stmts = [s for s in sqlglot.parse(sql, dialect="duckdb") if s is not None]
    except SqlglotError as exc:
        return [f"{where}: parse error: {str(exc).splitlines()[0][:200]}"]
    if len(stmts) != 1 or not isinstance(stmts[0], exp.Query):
        return [f"{where}: must be exactly one SELECT query"]
    st = stmts[0]
    problems = []
    bad = sorted({type(n).__name__.upper() for n in st.walk() if isinstance(n, _FORBIDDEN_NODES)})
    if bad:
        problems.append(f"{where}: forbidden operations {bad}")
    refs = table_references(st)
    if refs is None:
        problems.append(f"{where}: table references could not be resolved by scope")
        refs = [(t, False) for t in st.find_all(exp.Table)]
    pii_tables = set(PII_COLUMNS)
    for t, is_cte in refs:
        if isinstance(t.this, exp.Func):
            fname = (t.this.name if isinstance(t.this, exp.Anonymous) else t.this.sql_name()).lower()
            if fname not in _ALLOWED_TABLE_FUNCS:
                problems.append(f"{where}: table function {fname}() is not allowed")
            continue
        if not isinstance(t.this, exp.Identifier):
            problems.append(f"{where}: unsupported table expression {t.sql(dialect='duckdb')[:60]}")
            continue
        db, name = t.db.lower(), t.name.lower()
        if system_catalog_reference(t):
            dotted = ".".join(p for p in (t.catalog, t.db, t.name) if p)
            problems.append(f"{where}: system catalog {dotted[:60]} is not allowed")
        elif t.catalog:
            problems.append(f"{where}: cross-database reference {t.sql(dialect='duckdb')}")
        elif not db:
            if not is_cte:            # a CTE only counts inside the scope that defines it
                problems.append(f"{where}: unqualified table {t.name!r} (use schema.table)")
        elif db not in schemas:
            problems.append(f"{where}: schema {db!r} is not allowed here (allowed: {sorted(schemas)})")
        elif db in ("bronze", "bronze_safe") and name in pii_tables and not may_read_pii:
            problems.append(f"{where}: reads {db}.{name}, which holds personal data; PII handling stays in the "
                            f"reviewed staging model (changes to it need human review)")
    for f in st.find_all(exp.Func):
        if isinstance(f.parent, exp.Table):
            continue
        fname = (f.name if isinstance(f, exp.Anonymous) else f.sql_name()).lower()
        if fname.startswith(_DENIED_FUNC_PREFIXES) and fname not in _ALLOWED_TABLE_FUNCS:
            problems.append(f"{where}: function {fname}() is not allowed (file/system/dynamic-SQL access)")
    pii_cols = catalog().pii_columns
    if not may_read_pii:
        for col in st.find_all(exp.Column):
            if col.name.lower() in pii_cols:
                problems.append(f"{where}: references personal-data column {col.name!r}")
    return list(dict.fromkeys(problems))


def load_schema_spec(text: str) -> tuple[dict | None, list[str]]:
    """Parse and validate schema.yml strictly (names are interpolated into test SQL by the runner)."""
    try:
        spec = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return None, [f"schema.yml: invalid YAML: {str(exc).splitlines()[0][:200]}"]
    if not isinstance(spec, dict):
        return None, ["schema.yml: top level must be a mapping"]
    problems = [f"schema.yml: unknown top-level keys {sorted(set(spec) - _SCHEMA_KEYS)}"] if set(spec) - _SCHEMA_KEYS \
        else []
    models = spec.get("models") or {}
    if not isinstance(models, dict):
        return None, problems + ["schema.yml: models must be a mapping"]
    for m, cfg in models.items():
        where = f"schema.yml models.{m}"
        if not isinstance(m, str) or not _IDENT.match(m):
            problems.append(f"{where}: model name must be an identifier")
            continue
        if not isinstance(cfg, dict) or set(cfg) - _MODEL_CFG_KEYS:
            problems.append(f"{where}: must be a mapping with keys {sorted(_MODEL_CFG_KEYS)}")
            continue
        cols = cfg.get("columns") or {}
        if not isinstance(cols, dict):
            problems.append(f"{where}.columns must be a mapping")
            cols = {}
        for col, tests in cols.items():
            if not isinstance(col, str) or not _IDENT.match(col):
                problems.append(f"{where}.columns: {col!r} must be an identifier")
            if not isinstance(tests, list):
                problems.append(f"{where}.columns.{col}: tests must be a list")
                continue
            for t in tests:
                if t in ("unique", "not_null"):
                    continue
                if isinstance(t, dict) and set(t) == {"accepted_values"} and isinstance(t["accepted_values"], list) \
                        and all(isinstance(v, (str, int, float)) for v in t["accepted_values"]):
                    continue
                if isinstance(t, dict) and set(t) == {"relationships"} and isinstance(t["relationships"], dict) \
                        and set(t["relationships"]) == {"to", "field"} \
                        and all(isinstance(v, str) and _IDENT.match(v) for v in t["relationships"].values()):
                    continue
                problems.append(f"{where}.columns.{col}: unsupported test {t!r}")
        exprs = cfg.get("expressions") or {}
        if not isinstance(exprs, dict) or not all(isinstance(k, str) and _IDENT.match(k) and isinstance(v, str)
                                                  for k, v in exprs.items()):
            problems.append(f"{where}.expressions must map identifier names to SQL boolean expressions")
    recs = spec.get("reconciliations") or {}
    if not isinstance(recs, dict) or not all(isinstance(k, str) and _IDENT.match(k) and isinstance(v, str)
                                             for k, v in recs.items()):
        problems.append("schema.yml: reconciliations must map identifier names to SQL queries")
    return (None if problems else spec), problems


def screen_schema_sql(spec: dict) -> list[str]:
    problems = []
    for m, cfg in (spec.get("models") or {}).items():
        for name, expr in (cfg.get("expressions") or {}).items():
            problems += screen_sql(f"SELECT * FROM silver.{m} WHERE NOT ({expr})", f"schema.yml {m}:{name}",
                                   TEST_SCHEMAS)
    for name, sql in (spec.get("reconciliations") or {}).items():
        problems += screen_sql(sql, f"schema.yml reconciliation {name}", TEST_SCHEMAS)
    return problems


def test_ids(spec: dict) -> dict[str, str]:
    """Identity of every test (definition included) -> readable name. An edited test is a removed test."""
    out = {}
    for m, cfg in (spec.get("models") or {}).items():
        for col, tests in ((cfg or {}).get("columns") or {}).items():
            for t in tests or []:
                kind = t if isinstance(t, str) else next(iter(t))
                out[f"{m}.{col}:{canonical_json(t).decode()}"] = f"{m}.{col}:{kind}"
        for name, expr in ((cfg or {}).get("expressions") or {}).items():
            out[f"{m}:expr:{name}:{' '.join(str(expr).split())}"] = f"{m}:{name}"
    for name, sql in (spec.get("reconciliations") or {}).items():
        out[f"reconcile:{name}:{' '.join(str(sql).split())}"] = f"reconcile:{name}"
    return out


# =========================================================================== oracle independence
def sql_refs(sql: str, names: set[str]) -> set[str]:
    """Names (models / oracle sources) a SQL text references, however qualified or quoted.

    Token-based on purpose: a reference hidden in a string literal or a quoted identifier still
    counts, comments do not. Over-reporting only makes the independence check stricter."""
    try:
        tokens = sqlglot.tokenize(sql, dialect="duckdb")
    except SqlglotError:
        words = set(re.findall(r"[a-z_][a-z0-9_]*", re.sub(r"--[^\n]*", " ", sql.lower())))
        return words & names
    found = set()
    for t in tokens:
        text = t.text.lower()
        if t.token_type == TokenType.STRING:
            found |= set(re.findall(r"[a-z_][a-z0-9_]*", text)) & names
        elif text in names:
            found.add(text)
    return found


def ref_graph(models: dict[str, str]) -> dict[str, set[str]]:
    names = set(models) | set(ORACLE_SOURCES)
    return {m: sql_refs(sql, names) - {m} for m, sql in models.items()}


def _path_to_oracle(start: str, graph: dict[str, set[str]]) -> list[str] | None:
    """Shortest ref path from `start` to an oracle model/source, or None."""
    oracle = set(ORACLE_MODELS) | set(ORACLE_SOURCES)
    prev: dict[str, str | None] = {start: None}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        for d in sorted(graph.get(node, ())):
            if d in oracle:
                path = [d, node]
                while prev[path[-1]] is not None:
                    path.append(prev[path[-1]])
                return path[::-1]
            if d in graph and d not in prev:
                prev[d] = node
                queue.append(d)
    return None


def oracle_independence(models: dict[str, str], spec: dict | None, baseline_models: dict[str, str]) -> dict:
    graph = ref_graph(models)
    violations, checked = [], []
    for om in ORACLE_MODELS:
        if models.get(om) != baseline_models.get(om):
            violations.append(f"oracle model models/{om}.sql was modified: the independent record is frozen")
    recs = (spec or {}).get("reconciliations") or {}
    names = set(models) | set(ORACLE_SOURCES)
    for name, sql in sorted(recs.items()):
        refs = sql_refs(sql, names)
        oracle_side = sorted(refs & (set(ORACLE_MODELS) | set(ORACLE_SOURCES)))
        ours = sorted(r for r in refs if r in models and r not in ORACLE_MODELS)
        paths = {}
        for m in ours:
            path = _path_to_oracle(m, graph)
            if path:
                paths[m] = path
                violations.append(f"reconciliation independence violated: {name} checks {m} against the oracle, "
                                  f"but {m} is computed from it ({' -> '.join(path)})")
        checked.append({"reconciliation": name, "oracle_side": oracle_side, "ours": ours,
                        "ours_upstream": {m: sorted(_upstream(m, graph)) for m in ours}, "violating_paths": paths})
    if not any(c["oracle_side"] for c in checked):
        violations.append("no reconciliation compares the pipeline with an independent record")
    return {"outcome": "FAIL" if violations else "PASS", "violations": violations, "reconciliations": checked,
            "oracle_models": list(ORACLE_MODELS), "oracle_sources": list(ORACLE_SOURCES)}


def _upstream(m: str, graph: dict[str, set[str]]) -> set[str]:
    seen, stack = set(), [m]
    while stack:
        for d in graph.get(stack.pop(), ()):
            if d not in seen:
                seen.add(d)
                stack.append(d)
    return seen


# =========================================================================== builds + diff
def raw_unit_incidents(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """Charges in the RAW export (bronze; harness SQL, not the change's) below the cheapest plan valid
    when issued, outside every registered incident scope, per (month, region). Independent of the
    change's models, so a correction hidden in staging cannot make a new unit incident disappear."""
    cols = {r[0] for r in con.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = "
                                      "'bronze' AND table_name = 'billing_invoices'").fetchall()}
    amount = next((c for c in ("amount_cents", "amount_minor") if c in cols), None)
    if amount is None:
        raise ValueError(f"bronze.billing_invoices has no known amount column ({sorted(cols)})")
    rows = con.execute(f"""
        WITH raw AS (
            SELECT DISTINCT b.invoice_id, b.issued_at, b.{amount} AS amount, c.region
            FROM bronze.billing_invoices b JOIN bronze.crm_customers c USING (customer_id)
            WHERE b.invoice_type = 'charge')
        SELECT strftime(r.issued_at, '%Y-%m') AS month, r.region, COUNT(*) AS n
        FROM raw r
        WHERE r.amount < (SELECT MIN(p.price_fen) FROM silver.ref_plan_prices p
                          WHERE CAST(r.issued_at AS DATE) BETWEEN p.valid_from AND p.valid_to)
          AND NOT EXISTS (SELECT 1 FROM silver.ref_known_incidents k WHERE k.region = r.region
                          AND k.month = strftime(r.issued_at, '%Y-%m') AND k.invoice_type = 'charge')
        GROUP BY 1, 2 ORDER BY 1, 2""").fetchall()
    return [{"month": m, "region": r, "rows": int(n)} for m, r, n in rows]


def _ci_build(ws: Workspace, cutoff: str, pipe: Path, out_root: Path) -> dict:
    try:
        rep = build_snapshot(ws, cutoff, pipe, out_root=out_root)
    except Exception as exc:        # a crash is not a verdict about the change: ERROR, never PASS
        return {"status": "CRASHED", "cutoff": cutoff, "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
    return {**report_summary(dict(vars(rep))), "warehouse": str(out_root / rep.build_id / "warehouse.duckdb")}


def _silver_tables(con) -> dict[str, list[tuple[str, str]]]:
    rows = con.execute("SELECT table_name, column_name, data_type FROM information_schema.columns "
                       "WHERE table_schema = 'silver' ORDER BY table_name, ordinal_position").fetchall()
    out: dict[str, list[tuple[str, str]]] = {}
    for t, c, ty in rows:
        out.setdefault(t, []).append((c, ty))
    return out


def diff_silver(baseline: Path, candidate: Path, only: list[str] | None = None) -> dict:
    """Silver tables must be identical (schema and row multiset).

    Default: every silver table of either build. With `only` (the tables the baseline could
    build), just those are compared and every other silver table is listed under not_compared."""
    tables = []
    with readonly_connection(baseline) as b, readonly_connection(candidate) as c:
        bt, ct = _silver_tables(b), _silver_tables(c)
        names = sorted(set(bt) | set(ct)) if only is None else sorted(set(only))
        not_compared = [] if only is None else sorted((set(bt) | set(ct)) - set(only))
        mem = duckdb.connect()
        sandbox(mem)
        try:
            for name in names:
                if name not in ct:
                    tables.append({"table": f"silver.{name}", "outcome": "FAIL",
                                   "reason": "table missing in the candidate build"})
                    continue
                if name not in bt:
                    tables.append({"table": f"silver.{name}", "outcome": "FAIL",
                                   "reason": "new silver table: no baseline to compare (certified surface changed)"}
                                  if only is None else
                                  {"table": f"silver.{name}", "outcome": "ERROR",
                                   "reason": "the baseline build lacks a table its report lists as built"})
                    continue
                if bt[name] != ct[name]:
                    tables.append({"table": f"silver.{name}", "outcome": "FAIL", "reason": "schema changed",
                                   "baseline_columns": bt[name], "candidate_columns": ct[name]})
                    continue
                mem.register("b_t", b.execute(f"SELECT * FROM silver.{name}").to_arrow_table())
                mem.register("c_t", c.execute(f"SELECT * FROM silver.{name}").to_arrow_table())
                n_b = mem.execute("SELECT COUNT(*) FROM b_t").fetchone()[0]
                n_c = mem.execute("SELECT COUNT(*) FROM c_t").fetchone()[0]
                only_b = mem.execute("SELECT COUNT(*) FROM (SELECT * FROM b_t EXCEPT ALL SELECT * FROM c_t)"
                                     ).fetchone()[0]
                only_c = mem.execute("SELECT COUNT(*) FROM (SELECT * FROM c_t EXCEPT ALL SELECT * FROM b_t)"
                                     ).fetchone()[0]
                entry = {"table": f"silver.{name}", "n_baseline": n_b, "n_candidate": n_c,
                         "rows_only_in_baseline": only_b, "rows_only_in_candidate": only_c,
                         "outcome": "PASS" if only_b == 0 and only_c == 0 else "FAIL"}
                if entry["outcome"] == "FAIL":
                    cols = [col for col, _ in bt[name]]
                    for side, q in (("baseline_sample", "SELECT * FROM b_t EXCEPT ALL SELECT * FROM c_t"),
                                    ("candidate_sample", "SELECT * FROM c_t EXCEPT ALL SELECT * FROM b_t")):
                        entry[side] = [dict(zip(cols, r)) for r in
                                       mem.execute(f"SELECT * FROM ({q}) LIMIT 3").fetchall()]
                tables.append(entry)
                mem.unregister("b_t")
                mem.unregister("c_t")
        finally:
            mem.close()
    failed = [t["table"] for t in tables if t["outcome"] == "FAIL"]
    outcome = "FAIL" if failed or not tables else "ERROR" if any(t["outcome"] == "ERROR" for t in tables) else "PASS"
    out = {"outcome": outcome, "tables": tables, "changed_tables": failed}
    if only is not None:
        out["not_compared"] = [f"silver.{n}" for n in not_compared]
    return out


def _same_export_baseline(ws: Workspace, cutoff: str, out_root: Path) -> tuple[str, dict, Path, str]:
    """The governed pipeline's build of `cutoff` from the export CI builds: the lake's own build
    when it was made from that export, otherwise a fresh one in the change's run directory."""
    want = source_digest(ws, cutoff)
    bid = build_id_for(cutoff, pipeline_digest(DEFAULT_PIPELINE))
    try:
        rep = load_report(ws, bid)
        if rep.get("source_digest") == want and warehouse_path(ws, bid).is_file():
            return bid, rep, warehouse_path(ws, bid), "lake"
    except FileNotFoundError:
        pass
    rep = dict(vars(build_snapshot(ws, cutoff, DEFAULT_PIPELINE, out_root=out_root)))
    return rep["build_id"], rep, out_root / rep["build_id"] / "warehouse.duckdb", "rebuilt from the same export"


def drift_regression(ws: Workspace, cand_build: dict, out_root: Path) -> dict:
    """The newest cutoff, rebuilt with the change, must reproduce every silver table the GOVERNED
    pipeline builds there, even when that build as a whole FAILED (D8: invoices cannot build, but
    customers, subscriptions, usage, tickets, ... can). Without this, a change touching only rows
    after the served cutoff would never meet a baseline. Tables the governed pipeline cannot build
    have no baseline: they are listed under not_compared and left to the ci_build tests."""
    out: dict = {"cutoff": CI_CUTOFF}
    if cand_build["status"] == "CRASHED":
        return {**out, "outcome": "ERROR", "reason": f"build of {CI_CUTOFF} crashed: {cand_build['error']}"}
    try:
        base_id, base_rep, base_wh, origin = _same_export_baseline(ws, CI_CUTOFF, out_root)
    except Exception as exc:        # no baseline is not a verdict about the change: ERROR, never PASS
        return {**out, "outcome": "ERROR", "reason": f"no baseline build of the governed pipeline for {CI_CUTOFF}: "
                                                     f"{type(exc).__name__}: {str(exc)[:300]}"}
    silver = [m for m in base_rep.get("models", []) if m.get("schema") == "silver"]
    comparable = sorted(m["name"] for m in silver if m.get("status") == "OK")
    unbuilt = {m["name"]: f"{m.get('status')}: {m.get('error') or ''}".strip() for m in silver
               if m.get("status") != "OK"}
    out.update(baseline_build_id=base_id, baseline_status=base_rep.get("status"), baseline_origin=origin,
               compared_tables=[f"silver.{n}" for n in comparable])
    if not comparable:
        return {**out, "outcome": "INCONCLUSIVE",
                "reason": f"the governed pipeline builds no silver table on {CI_CUTOFF}: nothing to compare"}
    d = diff_silver(base_wh, Path(cand_build["warehouse"]), only=comparable)
    d["not_compared"] = [{"table": t, "reason": (
        f"the governed pipeline cannot build it on this export ({unbuilt[t.split('.', 1)[1]]}); "
        f"covered by the pipeline.ci_build tests and reconciliation" if t.split(".", 1)[1] in unbuilt
        else "not built by the governed pipeline: no baseline")} for t in d["not_compared"]]
    if d["outcome"] == "FAIL":
        d["reason"] = (f"silver tables the governed pipeline builds on {CI_CUTOFF} changed: {d['changed_tables']} "
                       f"(a change to data after the served cutoff needs a human-approved change)")
    elif d["outcome"] == "ERROR":
        d["reason"] = f"the drift comparison could not complete: {[t for t in d['tables'] if t['outcome'] == 'ERROR']}"
    return {**out, **d}


def _combine(*outcomes: str) -> str:
    for o in ("FAIL", "ERROR", "INCONCLUSIVE"):
        if o in outcomes:
            return o
    return "PASS"


# =========================================================================== propose
def _validate_files(files) -> dict[str, str]:
    if not isinstance(files, dict) or not files:
        raise ValueError("files must be a non-empty object {path: new file content}")
    if len(files) > MAX_FILES:
        raise ValueError(f"at most {MAX_FILES} files per change")
    out = {}
    for path, content in files.items():
        rel = check_pipeline_path(path)
        if not isinstance(content, str) or not content.strip():
            raise ValueError(f"{rel}: content must be a non-empty string (files cannot be deleted)")
        if len(content) > MAX_FILE_CHARS:
            raise ValueError(f"{rel}: longer than {MAX_FILE_CHARS} characters")
        out[rel] = content
    return out


def _models(pipe: Path) -> dict[str, str]:
    return {p.stem: p.read_text(encoding="utf-8") for p in sorted((pipe / "models").glob("*.sql"))}


def changed_pipeline_files(proposed_dir: Path, base_dir: Path = DEFAULT_PIPELINE) -> dict[str, str]:
    """The `files` argument of propose_pipeline_change for a whole proposed pipeline directory
    (e.g. a pull request's checkout): every schema.yml / models/*.sql that is new or differs from
    the governed pipeline. Deleting a governed file is not a supported change; raises ValueError."""
    proposed_dir, base_dir = Path(proposed_dir), Path(base_dir)
    if not (proposed_dir / "schema.yml").is_file() or not (proposed_dir / "models").is_dir():
        raise ValueError(f"{proposed_dir} is not a pipeline directory (schema.yml + models/*.sql)")
    proposed, base = _pipeline_files(proposed_dir), _pipeline_files(base_dir)
    deleted = sorted(set(base) - set(proposed))
    if deleted:
        raise ValueError(f"the proposed pipeline deletes {deleted}; files cannot be deleted through a change")
    files = {}
    for rel in proposed:
        content = (proposed_dir / rel).read_text(encoding="utf-8")
        if rel not in base or (base_dir / rel).read_text(encoding="utf-8") != content:
            files[rel] = content
    if not files:
        raise ValueError("the proposed pipeline is identical to the governed one")
    return files


@governed_tool("propose_pipeline_change")
def propose_pipeline_change(ctx: ToolContext, files: dict[str, str], rationale: str) -> dict:
    """Apply files to a copy of the governed pipeline, screen them, run CI, record evidence, ask the gate."""
    files = _validate_files(files)
    if not isinstance(rationale, str) or not rationale.strip() or len(rationale) > MAX_RATIONALE_CHARS:
        raise ValueError(f"rationale must be a non-empty string of at most {MAX_RATIONALE_CHARS} characters")
    ws = ctx.ws
    base_digest = pipeline_digest(DEFAULT_PIPELINE)
    ws.runs.mkdir(parents=True, exist_ok=True)
    staging = ws.runs / f".chg-{uuid.uuid4().hex}"
    try:
        shutil.copytree(DEFAULT_PIPELINE, staging / "pipeline",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for rel, content in files.items():
            (staging / "pipeline" / rel).write_text(content, encoding="utf-8")
        cand_digest = pipeline_digest(staging / "pipeline")
        if cand_digest == base_digest:
            raise ValueError("the change leaves the pipeline identical to the governed one")
        change_id = "chg-" + digest({"base": base_digest, "pipeline": cand_digest})[:16]
        run_dir = ws.runs / change_id
        shutil.rmtree(run_dir, ignore_errors=True)
        staging.rename(run_dir)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    pipe = run_dir / "pipeline"
    subject = {"kind": "pipeline_change", "change_id": change_id, "base_pipeline_digest": base_digest,
               "pipeline_digest": cand_digest}
    meta = {"change_id": change_id, "principal": ctx.principal, "ts": utcnow(), "rationale": rationale,
            "files": {rel: digest(c) for rel, c in files.items()}, "subject": subject}

    # ---- screen before anything executes ------------------------------------------------------
    models, baseline_models = _models(pipe), _models(DEFAULT_PIPELINE)
    changed = sorted(m for m in models if models[m] != baseline_models.get(m))
    problems = []
    for m in changed:
        problems += screen_sql(models[m], f"models/{m}.sql", MODEL_SCHEMAS)
    spec, spec_problems = load_schema_spec((pipe / "schema.yml").read_text(encoding="utf-8"))
    problems += spec_problems
    if spec is not None and "schema.yml" in files:
        problems += screen_schema_sql(spec)
    try:
        _toposort(models)               # the runner's own ordering: a cycle would crash the build
    except ValueError as exc:
        problems.append(f"model dependency {exc}")
    if problems:
        append_jsonl(ws.registry / CHANGES_LOG, {**meta, "status": "refused", "problems": problems})
        raise ToolError("refused", "pipeline change refused before CI: it would run unsafe or invalid code",
                        change_id=change_id, problems=problems)

    # ---- CI -----------------------------------------------------------------------------------
    baseline_spec = yaml.safe_load((DEFAULT_PIPELINE / "schema.yml").read_text(encoding="utf-8")) or {}
    base_tests, cand_tests = test_ids(baseline_spec), test_ids(spec)
    removed = sorted(base_tests[k] for k in set(base_tests) - set(cand_tests))
    added = sorted(cand_tests[k] for k in set(cand_tests) - set(base_tests))
    independence = oracle_independence(models, spec, baseline_models)
    independence["changed_models"] = changed
    reference_changed = sorted(m for m in REFERENCE_MODELS if models.get(m) != baseline_models.get(m))

    lake = run_dir / "lake"
    build = _ci_build(ws, CI_CUTOFF, pipe, lake)
    ci_reasons, raw_error = [], None
    if removed:
        ci_reasons.append(f"tests removed: {removed} (baseline tests and reconciliations cannot be deleted or edited)")
    if reference_changed:
        ci_reasons.append(f"reference data changed: {[f'models/{m}.sql' for m in reference_changed]} is what the "
                          f"price-book and incident-scope tests check against; registering or widening a unit "
                          f"incident or changing a price needs a human-approved change, not an agent pipeline change")
    if build["status"] == "CRASHED":
        # a crash says nothing about the change, but removed tests / edited reference data already do
        ci_outcome = "FAIL" if ci_reasons else "ERROR"
        ci_reasons.append(f"build crashed: {build['error']}")
    else:
        if build["status"] != "READY":
            ci_reasons.append(f"build of {CI_CUTOFF} is {build['status']}: {build['reasons'][:5]}")
        else:
            with readonly_connection(Path(build["warehouse"])) as con:
                oracle_rows = con.execute("SELECT COUNT(*) FROM silver.finance_closed_revenue").fetchone()[0]
                try:
                    build["raw_unregistered_unit_incidents"] = raw_unit_incidents(con)
                except (duckdb.Error, ValueError) as exc:
                    raw_error = f"raw unit-incident check could not run: {str(exc).splitlines()[0][:300]}"
            build["oracle_rows"] = oracle_rows
            if oracle_rows == 0:
                ci_reasons.append("the Finance close is empty in this build: the reconciliation compared nothing")
            if build.get("raw_unregistered_unit_incidents"):
                ci_reasons.append(f"the raw export has charges below the cheapest plan outside every registered "
                                  f"incident: {build['raw_unregistered_unit_incidents'][:5]}; a new unit incident is "
                                  f"registered by a human-approved change, never corrected in the pipeline")
        ci_outcome = "FAIL" if ci_reasons else "ERROR" if raw_error else "PASS"
        if raw_error:
            ci_reasons.append(raw_error)
    ci = {"outcome": ci_outcome, "reasons": ci_reasons, "cutoff": CI_CUTOFF, "build": build,
          "tests_removed": removed, "tests_added": added, "reference_data_changed": reference_changed}

    reg_build = _ci_build(ws, REGRESSION_CUTOFF, pipe, lake)
    try:
        baseline_id = latest_ready_build(ws, REGRESSION_CUTOFF)
    except (FileNotFoundError, RuntimeError) as exc:
        baseline_id, diff = None, {"outcome": "ERROR", "reason": f"no READY baseline build to compare: {exc}"}
    if baseline_id is not None:
        if load_report(ws, baseline_id).get("source_digest") != source_digest(ws, REGRESSION_CUTOFF):
            diff = {"outcome": "ERROR", "reason": f"the served build {baseline_id} was not built from the current "
                                                  f"{REGRESSION_CUTOFF} export: nothing to compare against"}
        elif reg_build["status"] == "CRASHED":
            diff = {"outcome": "ERROR", "reason": f"regression build crashed: {reg_build['error']}"}
        elif reg_build["status"] != "READY":
            diff = {"outcome": "FAIL", "reason": f"the change breaks the already-served cutoff {REGRESSION_CUTOFF}: "
                                                 f"{reg_build['reasons'][:5]}"}
        else:
            diff = diff_silver(warehouse_path(ws, baseline_id), Path(reg_build["warehouse"]))
            if diff["outcome"] == "FAIL":
                diff["reason"] = f"silver tables changed on the already-served cutoff: {diff['changed_tables']}"
    diff.update(cutoff=REGRESSION_CUTOFF, baseline_build_id=baseline_id, candidate_build=reg_build)
    # the newest cutoff too, for every silver table the governed pipeline can build there (DE-2)
    drift = drift_regression(ws, build, run_dir / "baseline_lake")
    served_outcome = diff["outcome"]
    reasons = [x["reason"] for x in (diff, drift) if x["outcome"] != "PASS" and x.get("reason")]
    diff.update(outcome=_combine(served_outcome, drift["outcome"]), served_cutoff_outcome=served_outcome,
                drift=drift, changed_tables_by_cutoff={REGRESSION_CUTOFF: diff.get("changed_tables", []),
                                                       CI_CUTOFF: drift.get("changed_tables", [])})
    if reasons:
        diff["reason"] = "; ".join(reasons)

    # ---- evidence + gate ----------------------------------------------------------------------
    store = EvidenceStore(ws)
    common = {"check_version": CHECK_VERSION, "stage": STAGE, "subject": subject, "producer": "ci:pipeline"}
    strip = ("warehouse",)
    records = [
        store.record(check_id="pipeline.ci_build", outcome=ci_outcome,
                     metrics=jsonable({**ci, "build": {k: v for k, v in build.items() if k not in strip}}),
                     limitations=["one cutoff of synthetic exports; contracts and tests cover known failure modes"],
                     **common),
        store.record(check_id="pipeline.regression_diff", outcome=diff["outcome"],
                     metrics=jsonable({**diff, "candidate_build": {k: v for k, v in reg_build.items()
                                                                   if k not in strip}}),
                     limitations=["exact equality of silver tables on the already-served cutoff, and on the newest "
                                  "cutoff for the tables the governed pipeline builds there; tables it cannot build "
                                  "have no baseline (ci_build tests only)"], **common),
        store.record(check_id="pipeline.oracle_independence", outcome=independence["outcome"],
                     metrics=jsonable(independence),
                     limitations=["static ref graph over model SQL text; numbers hard-coded into SQL are not "
                                  "detected"], **common),
    ]
    decision = decide(ws, STAGE, subject, requested_by=ctx.principal)
    append_jsonl(ws.registry / CHANGES_LOG, {**meta, "status": "ci_done", "verdict": decision.verdict,
                                             "evidence_ids": [r.evidence_id for r in records]})
    for b in (build, reg_build):
        b.pop("warehouse", None)
    return {"change_id": change_id, "subject": subject, "pipeline_digest": cand_digest, "changed_models": changed,
            "schema_changed": "schema.yml" in files,
            "ci": {**ci, "oracle_independence": independence}, "diff": diff,
            "evidence_ids": [r.evidence_id for r in records], "gate": decision.to_dict(),
            "note": "gate ALLOW permits the merge; merging and rebuilding the served cutoffs is a separate step"}
