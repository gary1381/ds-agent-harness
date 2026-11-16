"""Static analysis of agent SQL before it touches the warehouse.

Trust boundary: an agent's SQL is untrusted input. This check runs before execution and
decides whether the query may run at all (hard violations), and what the result must be
labelled with (tier, warnings). It is defense in depth. The connection is also read-only with
external access disabled (common.open_readonly). The static layer exists so that a refusal can say
*why*, and so that harmless-looking but wrong queries (raw tables, missing test-account
filter, current-state columns) are flagged instead of silently trusted.

The analysis fails closed. Anything it cannot parse or resolve is a violation:
unknown or unqualified tables, table functions outside a small allowlist, any statement that is not a query,
system-catalog views. An unqualified name is accepted only when it is a CTE visible in the scope of that
reference (`table_references`); the same helper screens pipeline SQL in tools/de.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
from sqlglot.optimizer.scope import Scope, traverse_scope

from ..context.catalog import Catalog

# Generators only. They read no data and touch no files.
_ALLOWED_TABLE_FUNCS = {"range", "generate_series", "unnest", "explode"}
# Scalar or table functions that read files, run SQL from strings, or expose system state.
_DENIED_FUNC_PREFIXES = ("read_", "parquet_", "sniff_", "glob", "query", "duckdb_", "pragma_", "iceberg_",
                         "delta_", "sqlite_", "postgres_", "mysql_", "http", "json_execute", "getenv",
                         "current_setting", "st_read", "load_", "install_", "csv_", "json_scan", "arrow_scan")
_FORBIDDEN_NODES = (exp.DDL, exp.DML, exp.Command, exp.Copy, exp.Set, exp.Pragma, exp.Attach, exp.Detach,
                    exp.Install, exp.Use, exp.Describe, exp.Show, exp.Drop, exp.Alter, exp.Transaction,
                    exp.Commit, exp.Rollback, exp.Export, exp.LoadData, exp.Kill, exp.Analyze, exp.Summarize,
                    exp.Refresh, exp.Cache, exp.Uncache)
# DuckDB's system catalog (database paths, every schema incl. restricted ones, settings, view DDL).
_SYSTEM_SCHEMAS = {"information_schema", "pg_catalog", "system", "temp"}
_SYSTEM_NAME_PREFIXES = ("duckdb_", "pragma_", "sqlite_", "pg_")
_PREDICATE_CONTEXTS = (exp.Where, exp.Having, exp.Qualify, exp.Join, exp.Filter, exp.If, exp.Case)
_TEMPORAL_RISK = ("mutable_current", "post_outcome")


@dataclass
class StaticReport:
    ok: bool
    read_only: bool
    single_statement: bool
    tables: list[str]
    schemas: list[str]
    tier: str                                   # certified | uncertified
    pii_columns: list[str]
    mutable_or_post_outcome_columns: list[str]
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _func_name(f: exp.Expression) -> str:
    if isinstance(f, exp.Anonymous):
        return str(f.name).lower()
    return f.sql_name().lower() if isinstance(f, exp.Func) else type(f).__name__.lower()


def _star_except(star: exp.Star) -> set[str]:
    cols = star.args.get("except_") or star.args.get("except") or []
    return {c.name.lower() for c in cols if isinstance(c, exp.Expression)}


def table_references(stmt: exp.Expression) -> list[tuple[exp.Table, bool]] | None:
    """Every Table node of `stmt`, paired with whether it is a CTE visible in its own scope.

    CTE names are resolved per scope (sqlglot scopes, identifiers normalised the way DuckDB
    resolves them), never as one statement-wide set: a CTE defined in a subquery, a UNION
    branch or another CTE's body does not cover a sibling or outer reference to the same name,
    which DuckDB resolves to a real table, a system view or a replacement-scanned file.
    Returns None when scoping fails; callers must then treat every unqualified name as unknown.
    """
    try:
        norm = normalize_identifiers(stmt.copy(), dialect="duckdb")
        cte_ids = {id(node) for scope in traverse_scope(norm)
                   for node, source in scope.selected_sources.values()
                   if isinstance(node, exp.Table) and isinstance(source, Scope) and not node.db}
        original, normalised = list(stmt.find_all(exp.Table)), list(norm.find_all(exp.Table))
    except Exception:          # any scoping failure fails closed (no name is accepted as a CTE)
        return None
    if len(original) != len(normalised):
        return None
    return [(t, id(n) in cte_ids) for t, n in zip(original, normalised)]


def system_catalog_reference(t: exp.Table) -> bool:
    """A system schema/view (information_schema.*, pg_catalog.*, duckdb_*, pragma_*, ...), even
    when a CTE of the same name exists: no governed query needs one, so none is accepted."""
    parts = [p.lower() for p in (t.catalog, t.db, t.name) if p]
    return any(p in _SYSTEM_SCHEMAS for p in parts[:-1]) or (bool(parts) and parts[-1].startswith(_SYSTEM_NAME_PREFIXES))


def _dotted(t: exp.Table) -> str:
    return ".".join(p for p in (t.catalog, t.db, t.name) if p)


def _refused(violations: list[str], *, single: bool = False) -> StaticReport:
    return StaticReport(False, False, single, [], [], "uncertified", [], [], violations, [])


def analyze_sql(sql: str, catalog: Catalog) -> StaticReport:
    if not isinstance(sql, str) or not sql.strip():
        return _refused(["empty SQL"])
    try:
        statements = [s for s in sqlglot.parse(sql, dialect="duckdb") if s is not None]
    except SqlglotError as exc:
        return _refused([f"parse error: {str(exc).splitlines()[0][:300]}"])
    if not statements:
        return _refused(["no SQL statement found"])

    violations: list[str] = []
    warnings: list[str] = []
    single = len(statements) == 1
    if not single:
        violations.append(f"multiple statements ({len(statements)}): submit exactly one SELECT")

    read_only = True
    for stmt in statements:
        if not isinstance(stmt, exp.Query):
            if isinstance(stmt, exp.Command):
                kind = str(stmt.this).split()[0].upper() if stmt.this else "COMMAND"
            elif isinstance(stmt, (exp.Column, exp.Identifier)):   # bare keywords such as CHECKPOINT
                kind = stmt.sql(dialect="duckdb").upper()[:40]
            else:
                kind = type(stmt).__name__.upper()
            violations.append(f"non-SELECT statement ({kind}): only read-only queries are allowed")
            read_only = False
        bad = sorted({type(n).__name__.upper() for n in stmt.walk() if isinstance(n, _FORBIDDEN_NODES)})
        if bad and isinstance(stmt, exp.Query):
            violations.append(f"query contains forbidden operations: {bad}")
        read_only = read_only and not bad

    tables: set[str] = set()
    pii: set[str] = set()
    referenced_any = False
    for stmt in statements:
        refs = table_references(stmt)
        if refs is None:
            violations.append("table references could not be resolved by scope")
            refs = [(t, False) for t in stmt.find_all(exp.Table)]
        for t, is_cte in refs:
            if isinstance(t.this, exp.Func):
                fname = _func_name(t.this)
                if fname not in _ALLOWED_TABLE_FUNCS:
                    violations.append(f"table function not allowed: {fname}() (file/system/dynamic-SQL access)")
                continue
            referenced_any = True
            if not isinstance(t.this, exp.Identifier):
                violations.append(f"unsupported table expression: {t.sql(dialect='duckdb')[:80]}")
                continue
            name, db = t.name.lower(), t.db.lower()
            if system_catalog_reference(t):
                violations.append(f"table not in catalog: {_dotted(t)[:80]} is a system catalog view, "
                                  f"never allowed (use describe_table / the governed catalog)")
            elif t.catalog:
                violations.append(f"cross-database reference not allowed: {t.sql(dialect='duckdb')}")
            elif not db:
                if not is_cte:            # only a CTE visible in this reference's own scope
                    violations.append(f"unknown or unqualified table {t.name!r}: use catalogued schema.table names "
                                      f"(e.g. silver.invoices)")
            elif db in catalog.restricted_schemas:
                violations.append(f"{db}.{name}: schema '{db}' is restricted (raw data incl. PII); agents never "
                                  f"query it. Use silver.* or bronze_safe.*")
            elif f"{db}.{name}" not in catalog.tables:
                violations.append(f"table not in catalog: {db}.{name}")
            else:
                tables.add(f"{db}.{name}")
        for f in stmt.find_all(exp.Func):
            fname = _func_name(f)
            if isinstance(f.parent, exp.Table):
                continue                       # table functions were judged above
            if fname.startswith(_DENIED_FUNC_PREFIXES) and fname not in _ALLOWED_TABLE_FUNCS:
                violations.append(f"function not allowed: {fname}() (file/system/dynamic-SQL access)")
        for col in stmt.find_all(exp.Column):
            if col.name.lower() in catalog.pii_columns:
                pii.add(col.sql(dialect="duckdb"))
        if any(True for _ in stmt.find_all(exp.Columns)):   # COLUMNS('regex') selects columns without naming them
            violations.append("COLUMNS(...) expressions are not allowed: list columns explicitly")
    for p in sorted(pii):
        violations.append(f"PII column referenced: {p}")

    risky, approx = _temporal_columns(statements, catalog, tables)
    if approx:
        warnings.append("column lineage could not be resolved by scope; temporal columns matched by name")
    for fq in sorted(risky):
        t, c = fq.rsplit(".", 1)
        warnings.append(f"{fq} is {catalog.tables[t].columns[c].temporal}: value as known at build time, "
                        f"not point-in-time safe (never use as a predictive feature without a known-at guard)")

    if not referenced_any:
        warnings.append("query reads no warehouse tables: the result is not backed by governed data")
    for fq in sorted(tables):
        doc = catalog.tables[fq]
        if doc.tier != "certified":
            first_caveat = f": {doc.caveats[0]}" if doc.caveats else ""
            warnings.append(f"uncertified table {fq} (tier {doc.tier}){first_caveat}")
    _check_required_filters(statements, catalog, tables, warnings)

    tier = "certified" if tables and all(catalog.tables[t].tier == "certified" for t in tables) else "uncertified"
    return StaticReport(ok=not violations, read_only=read_only, single_statement=single,
                        tables=sorted(tables), schemas=sorted({t.split(".")[0] for t in tables}), tier=tier,
                        pii_columns=sorted(pii), mutable_or_post_outcome_columns=sorted(risky),
                        violations=list(dict.fromkeys(violations)), warnings=list(dict.fromkeys(warnings)))


def _temporal_columns(statements, catalog: Catalog, tables: set[str]) -> tuple[set[str], bool]:
    """Columns tagged mutable_current/post_outcome that the query reads, as 'schema.table.column'.

    Uses sqlglot scopes so that `b.ended_at` is attributed to the table aliased `b`. Star projections
    count as reading every column of the starred table. If scope resolution fails, every column name
    is matched against every referenced table. That fallback over-reports and never under-reports.
    """
    hits: set[str] = set()

    def note(fq: str, cols) -> None:
        doc = catalog.tables.get(fq)
        for c in cols:
            if doc and c in doc.columns and doc.columns[c].temporal in _TEMPORAL_RISK:
                hits.add(f"{fq}.{c}")

    try:
        for stmt in statements:
            if not isinstance(stmt, exp.Query):
                continue
            for scope in traverse_scope(stmt):
                srcs = _table_sources(scope, catalog)
                for col in scope.columns:
                    name = col.name.lower()
                    # Walk outwards so correlated references (outer alias inside a subquery) resolve too.
                    s, found = scope, False
                    while s is not None and not found:
                        local = srcs if s is scope else _table_sources(s, catalog)
                        if col.table:
                            alias = col.table.lower()
                            if alias in local:
                                note(local[alias], [name])
                                found = True
                            elif alias in {a.lower() for a in s.selected_sources}:
                                found = True          # a CTE/subquery alias: its own scope is checked
                        else:
                            owners = [fq for fq in local.values() if name in catalog.tables[fq].columns]
                            for fq in owners:
                                note(fq, [name])
                            # Stop here if a table owns the column or a CTE/subquery source could provide it.
                            # Otherwise it may be a correlated reference to an outer scope.
                            found = bool(owners) or len(local) < len(s.selected_sources)
                        s = s.parent
                if isinstance(scope.expression, exp.Select):
                    for proj in scope.expression.expressions:
                        if isinstance(proj, exp.Star):
                            for fq in srcs.values():
                                note(fq, set(catalog.tables[fq].columns) - _star_except(proj))
                        elif isinstance(proj, exp.Column) and isinstance(proj.this, exp.Star):
                            fq = srcs.get(proj.table.lower())
                            if fq:
                                note(fq, set(catalog.tables[fq].columns) - _star_except(proj.this))
        return hits, False
    except SqlglotError:
        hits.clear()
        names = {c.name.lower() for s in statements for c in s.find_all(exp.Column)}
        has_star = any(isinstance(n, exp.Star) for s in statements for n in s.walk())
        for fq in tables:
            note(fq, set(catalog.tables[fq].columns) if has_star else names)
        return hits, True


def _table_sources(scope, catalog: Catalog) -> dict[str, str]:
    """alias -> catalogued table for the FROM/JOIN sources of one scope."""
    out: dict[str, str] = {}
    for alias, (_, source) in scope.selected_sources.items():
        if isinstance(source, exp.Table) and isinstance(source.this, exp.Identifier):
            fq = f"{source.db.lower()}.{source.name.lower()}"
            if fq in catalog.tables:
                out[alias.lower()] = fq
    return out


def _check_required_filters(statements, catalog: Catalog, tables: set[str], warnings: list[str]) -> None:
    """Heuristic: each required-filter column must appear in a predicate (WHERE/JOIN/HAVING/QUALIFY/CASE).

    It proves the filter is *considered*, not that it is right (`WHERE is_test_account` passes).
    Wrong logic is caught by reconciliation against governed metrics, not here.
    """
    needed: dict[str, list[str]] = {}
    for fq in sorted(tables):
        for rule in catalog.tables[fq].required_filters:
            needed.setdefault(rule, []).append(fq)
    for rule, fqs in needed.items():
        column = str(catalog.rules[rule].get("column", "")).lower()
        found = any(col.name.lower() == column and col.find_ancestor(*_PREDICATE_CONTEXTS) is not None
                    for s in statements for col in s.find_all(exp.Column))
        if not found:
            warnings.append(f"missing {rule} filter: {', '.join(fqs)} require a predicate on {column} "
                            f"(internal test accounts inflate counts and revenue); join silver.customers "
                            f"and filter NOT {column}")
