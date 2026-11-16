"""A minimal dbt-style SQL model runner + data tests.

Deliberately small: production deployments should use dbt / SQLMesh / the platform's own
pipeline tool. What matters here is the contract the harness relies on:
  * models are reviewed SQL files in a versioned directory (pipeline_digest),
  * a failed model fails explicitly and its dependants are SKIPPED, never half-built,
  * tests are queries that return offending rows; zero rows == PASS.

Trust boundary: model and test SQL may come from an agent's pipeline change. It only ever runs
on a sandboxed connection (`sandbox`: no filesystem/network/extension access, configuration
locked), whatever the static screen in tools/de.py misses; run_models/run_tests refuse any other.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
from pathlib import Path
import re

import duckdb
import yaml

_REF = re.compile(r"\b(staging|silver)\.([A-Za-z_][A-Za-z0-9_]*)")


def schema_for(model: str) -> str:
    return "staging" if model.startswith("stg_") else "silver"


def pipeline_digest(pipeline_dir: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(Path(pipeline_dir).rglob("*")):
        if path.is_file() and path.suffix in {".sql", ".yml", ".yaml"}:
            h.update(path.relative_to(pipeline_dir).as_posix().encode())
            h.update(b"\0")
            h.update(path.read_bytes())
            h.update(b"\0")
    return h.hexdigest()


@dataclass
class ModelResult:
    name: str
    schema: str
    status: str            # OK | ERROR | SKIPPED
    rows: int | None = None
    error: str | None = None


@dataclass
class TestResult:
    name: str
    model: str | None
    outcome: str           # PASS | FAIL | ERROR
    failing_rows: int | None = None
    sample: list = field(default_factory=list)
    error: str | None = None


def sandbox(con: duckdb.DuckDBPyConnection) -> None:
    """Lock a build connection before pipeline SQL runs on it: no file, network, extension or
    replacement-scan access, and no SET can undo it. Irreversible for the connection's database."""
    con.execute("SET enable_external_access = false")
    con.execute("SET lock_configuration = true")


def _require_sandbox(con: duckdb.DuckDBPyConnection) -> None:
    state = con.execute("SELECT current_setting('enable_external_access'), "
                        "current_setting('lock_configuration')").fetchone()
    if tuple(state) != (False, True):
        raise RuntimeError(f"pipeline SQL runs only on a sandboxed connection "
                           f"(external_access, lock_configuration) = {tuple(state)}; call runner.sandbox first")


def _load_models(models_dir: Path) -> dict[str, str]:
    return {p.stem: p.read_text() for p in sorted(Path(models_dir).glob("*.sql"))}


def _toposort(models: dict[str, str]) -> list[str]:
    deps = {m: {name for _, name in _REF.findall(sql) if name in models and name != m}
            for m, sql in models.items()}
    order, done, visiting = [], set(), set()

    def visit(m):
        if m in done:
            return
        if m in visiting:
            raise ValueError(f"cycle at model {m}")
        visiting.add(m)
        for d in sorted(deps[m]):
            visit(d)
        visiting.discard(m)
        done.add(m)
        order.append(m)

    for m in sorted(models):
        visit(m)
    return order


def run_models(con: duckdb.DuckDBPyConnection, pipeline_dir: Path) -> list[ModelResult]:
    _require_sandbox(con)
    models = _load_models(Path(pipeline_dir) / "models")
    order = _toposort(models)
    con.execute("CREATE SCHEMA IF NOT EXISTS staging")
    con.execute("CREATE SCHEMA IF NOT EXISTS silver")
    failed: set[str] = set()
    results = []
    for m in order:
        upstream = {name for _, name in _REF.findall(models[m]) if name in models}
        sch = schema_for(m)
        if upstream & failed:
            failed.add(m)
            results.append(ModelResult(m, sch, "SKIPPED", error=f"upstream failed: {sorted(upstream & failed)}"))
            continue
        try:
            con.execute(f"CREATE OR REPLACE TABLE {sch}.{m} AS {models[m]}")
            rows = con.execute(f"SELECT COUNT(*) FROM {sch}.{m}").fetchone()[0]
            results.append(ModelResult(m, sch, "OK", rows=rows))
        except duckdb.Error as exc:
            failed.add(m)
            results.append(ModelResult(m, sch, "ERROR", error=str(exc).splitlines()[0][:500]))
    return results


def _run_test(con, name, model, sql) -> TestResult:
    try:
        rows = con.execute(f"SELECT * FROM ({sql}) LIMIT 5").fetchall()
        n = con.execute(f"SELECT COUNT(*) FROM ({sql})").fetchone()[0]
        return TestResult(name, model, "PASS" if n == 0 else "FAIL", n, [list(map(str, r)) for r in rows])
    except duckdb.Error as exc:
        return TestResult(name, model, "ERROR", error=str(exc).splitlines()[0][:500])


def run_tests(con: duckdb.DuckDBPyConnection, pipeline_dir: Path,
              built: set[str]) -> list[TestResult]:
    _require_sandbox(con)
    spec = yaml.safe_load((Path(pipeline_dir) / "schema.yml").read_text()) or {}
    out: list[TestResult] = []
    for model, cfg in (spec.get("models") or {}).items():
        table = f"silver.{model}"
        if model not in built:
            out.append(TestResult(f"{model}:exists", model, "ERROR", error="model not built"))
            continue
        for col, tests in (cfg.get("columns") or {}).items():
            for t in tests:
                if t == "unique":
                    sql = f"SELECT {col} FROM {table} GROUP BY {col} HAVING COUNT(*) > 1"
                    name = f"{model}.{col}:unique"
                elif t == "not_null":
                    sql = f"SELECT * FROM {table} WHERE {col} IS NULL"
                    name = f"{model}.{col}:not_null"
                elif isinstance(t, dict) and "accepted_values" in t:
                    vals = ", ".join("'" + str(v).replace("'", "''") + "'" for v in t["accepted_values"])
                    sql = f"SELECT * FROM {table} WHERE {col} IS NOT NULL AND {col} NOT IN ({vals})"
                    name = f"{model}.{col}:accepted_values"
                elif isinstance(t, dict) and "relationships" in t:
                    r = t["relationships"]
                    sql = (f"SELECT a.{col} FROM {table} a LEFT JOIN silver.{r['to']} b "
                           f"ON a.{col} = b.{r['field']} WHERE a.{col} IS NOT NULL AND b.{r['field']} IS NULL")
                    name = f"{model}.{col}:relationships"
                else:
                    out.append(TestResult(f"{model}.{col}:{t}", model, "ERROR", error="unknown test"))
                    continue
                out.append(_run_test(con, name, model, sql))
        for name, expr in (cfg.get("expressions") or {}).items():
            out.append(_run_test(con, f"{model}:{name}", model, f"SELECT * FROM {table} WHERE NOT ({expr})"))
    for name, sql in (spec.get("reconciliations") or {}).items():
        out.append(_run_test(con, f"reconcile:{name}", None, sql))
    return out


def as_dicts(items) -> list[dict]:
    return [asdict(i) for i in items]
