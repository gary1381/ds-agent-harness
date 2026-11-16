"""Governed metrics: load the semantic model YAML and compile metric requests to SQL over silver.*.

Trust boundary: the SQL that defines a metric is reviewed code in `semantic_model.yaml`.
Agent input only selects a metric name, dimension names, an inclusive month range and
filter values. All of them are validated against the model before any SQL is produced, so an
agent can ask *for* revenue but cannot change what revenue *means*.
Unknown metrics, dimensions or filter values raise ValueError. Months the build does not cover are
refused rather than answered with zeros.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re

import duckdb
import pandas as pd
import yaml

from ..common import digest

DEFAULT_SEMANTIC_MODEL = Path(__file__).parent / "semantic_model.yaml"
_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")
_ADDITIVE = {"sum": "SUM({x})", "count": "COUNT({x})", "count_distinct": "COUNT(DISTINCT {x})"}
_NON_ADDITIVE = {"avg": "AVG({x})"}


# Independent records the finance-close check (tools/analytics.py) can reconcile against. A model naming any
# other record would have it silently skipped, so the loader refuses it.
CHECKABLE_RECORDS = ("silver.finance_closed_revenue",)


@dataclass(frozen=True)
class Metric:
    name: str
    description: str
    owner: str
    dimensions: list[str]
    caveats: list[str]
    label: str = ""
    unit: str = ""
    type: str = "simple"          # simple | ratio
    definition: str = ""
    terms: tuple[str, ...] = ()   # regexes (lower-case text) for wording that names this metric
    suppresses: tuple[str, ...] = ()        # less specific metrics this one overrides when both match
    generic_terms: tuple[str, ...] = ()     # words that name it only by default ("sales", 销售额)
    qualifiers: tuple[str, ...] = ()        # words that turn a default reading of a metric it suppresses into it
    independent_record: str = ""            # table of an independent record that reconciles it (e.g. a finance close)


@dataclass(frozen=True)
class Dimension:
    name: str
    type: str                     # time | categorical
    description: str
    values: tuple[str, ...] | None = None
    aliases: dict[str, tuple[str, ...]] = field(default_factory=dict)   # value -> other spellings (e.g. Chinese)
    names: tuple[str, ...] = ()                                          # other words for the dimension itself


@dataclass
class SemanticModel:
    metrics: dict[str, Metric]
    digest: str
    dimensions: dict[str, Dimension] = field(default_factory=dict)
    coverage_sql: str = ""
    spec: dict = field(default_factory=dict, repr=False)
    path: str = ""


# --------------------------------------------------------------------------- loading
def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise ValueError(f"semantic model invalid: {msg}")


def load_semantic_model(path: str | Path | None = None) -> SemanticModel:
    """Load and validate the model. A malformed model raises ValueError. It is never partly served."""
    path = Path(path or DEFAULT_SEMANTIC_MODEL)
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    _require(isinstance(spec, dict), "top level must be a mapping")
    for key in ("time_spine", "dimensions", "datasets", "metrics"):
        _require(isinstance(spec.get(key), dict), f"missing mapping '{key}'")
    coverage = spec["time_spine"].get("coverage")
    _require(isinstance(coverage, str) and coverage.strip(), "time_spine.coverage SQL is required")

    dims: dict[str, Dimension] = {}
    for name, d in spec["dimensions"].items():
        _require(bool(_IDENT.match(name)), f"bad dimension name {name!r}")
        _require(d.get("type") in ("time", "categorical"), f"dimension {name}: type must be time|categorical")
        values = d.get("values")
        _require(values is None or (isinstance(values, list) and values), f"dimension {name}: values must be a list")
        aliases = d.get("aliases") or {}
        _require(isinstance(aliases, dict) and set(aliases) <= set(values or ()),
                 f"dimension {name}: aliases must map listed values to spellings")
        names = d.get("names") or []
        _require(isinstance(names, list), f"dimension {name}: names must be a list")
        dims[name] = Dimension(name, d["type"], str(d.get("description", "")),
                               tuple(str(v) for v in values) if values else None,
                               {str(k): tuple(str(x) for x in v) for k, v in aliases.items()},
                               tuple(str(x) for x in names))
    _require("month" in dims and dims["month"].type == "time", "dimension 'month' (type time) is required")

    datasets = spec["datasets"]
    for name, ds in datasets.items():
        _require(bool(_IDENT.match(name)), f"bad dataset name {name!r}")
        _require(isinstance(ds.get("relation"), str), f"dataset {name}: relation SQL required")
        _require(isinstance(ds.get("required_filter"), str) and ds["required_filter"].strip(),
                 f"dataset {name}: required_filter is mandatory (test-account exclusion)")
        for dim, col in (ds.get("dimensions") or {}).items():
            _require(dim in dims and dim != "month", f"dataset {name}: unknown dimension {dim!r}")
            _require(bool(_IDENT.match(str(col))), f"dataset {name}: dimension column {col!r} must be a plain column")
        for mname, m in (ds.get("measures") or {}).items():
            _require(m.get("agg") in {**_ADDITIVE, **_NON_ADDITIVE}, f"dataset {name}.{mname}: unknown agg")
            _require(isinstance(m.get("expr"), str), f"dataset {name}.{mname}: expr required")
            _require(m.get("cast", "bigint") in ("bigint", "double") and (m["agg"] in _ADDITIVE or "cast" not in m),
                     f"dataset {name}.{mname}: cast must be bigint (default) or double, on an additive measure")

    metrics: dict[str, Metric] = {}
    for name, m in spec["metrics"].items():
        _require(bool(_IDENT.match(name)), f"bad metric name {name!r}")
        for key in ("description", "owner", "caveats", "dimensions"):
            _require(m.get(key) not in (None, "", []), f"metric {name}: '{key}' is required")
        mdims = list(m["dimensions"])
        _require(all(d in dims and d != "month" for d in mdims), f"metric {name}: unknown dimension in {mdims}")
        mtype = m.get("type", "simple")
        if mtype == "simple":
            ds = datasets.get(m.get("dataset"))
            _require(ds is not None, f"metric {name}: unknown dataset {m.get('dataset')!r}")
            _require(m.get("measure") in (ds.get("measures") or {}), f"metric {name}: unknown measure")
            _require(bool(m.get("month_join") or ds.get("month_join")), f"metric {name}: no month_join")
            missing = set(mdims) - set(ds.get("dimensions") or {})
            _require(not missing, f"metric {name}: dataset {m['dataset']} lacks dimensions {sorted(missing)}")
        elif mtype == "ratio":
            for part in ("numerator", "denominator"):
                ref = spec["metrics"].get(m.get(part))
                _require(ref is not None and ref.get("type", "simple") == "simple",
                         f"metric {name}: {part} must name a simple metric")
                _require(set(mdims) <= set(ref["dimensions"]),
                         f"metric {name}: dimensions not supported by {part} {m[part]}")
        else:
            _require(False, f"metric {name}: type must be simple|ratio")
        terms = m.get("terms") or []
        _require(isinstance(terms, list) and terms and all(isinstance(t, str) and t for t in terms),
                 f"metric {name}: 'terms' (regexes for the wording that names it) is required")
        generic, quals = m.get("generic_terms") or [], m.get("qualifiers") or []
        for key, lst in (("generic_terms", generic), ("qualifiers", quals)):
            _require(isinstance(lst, list) and all(isinstance(t, str) and t for t in lst),
                     f"metric {name}: '{key}' must be a list of regexes")
        for t in [*terms, *generic, *quals]:
            try:
                re.compile(t)
            except re.error as exc:
                _require(False, f"metric {name}: term {t!r} is not a regex ({exc})")
        sup = m.get("suppresses") or []
        _require(isinstance(sup, list) and all(x in spec["metrics"] and x != name for x in sup),
                 f"metric {name}: suppresses must name other metrics")
        _require(not quals or sup, f"metric {name}: qualifiers refine a metric it suppresses; name it in suppresses")
        record = str(m.get("independent_record", ""))
        _require(record in ("", *CHECKABLE_RECORDS),
                 f"metric {name}: independent_record {record!r} has no reconciliation check "
                 f"(supported: {list(CHECKABLE_RECORDS)}); leave it out and answers cap at T1")
        metrics[name] = Metric(name=name, description=str(m["description"]).strip(), owner=str(m["owner"]),
                               dimensions=mdims, caveats=[str(c) for c in m["caveats"]],
                               label=str(m.get("label", name)), unit=str(m.get("unit", "")), type=mtype,
                               definition=str(m.get("definition", "")).strip(), terms=tuple(terms),
                               suppresses=tuple(sup), generic_terms=tuple(generic), qualifiers=tuple(quals),
                               independent_record=record)
    return SemanticModel(metrics=metrics, digest=digest(spec), dimensions=dims,
                         coverage_sql=coverage.strip(), spec=spec, path=str(path))


# --------------------------------------------------------------------------- describing
def list_metrics(model: SemanticModel) -> list[dict]:
    return [{"name": m.name, "label": m.label, "unit": m.unit, "description": m.description,
             "dimensions": ["month", *m.dimensions]} for m in model.metrics.values()]


def describe_metric(model: SemanticModel, name: str) -> dict:
    """Everything an analyst needs to use a metric correctly, including its caveats."""
    m = _metric(model, name)
    raw = model.spec["metrics"][name]
    out = {"name": m.name, "label": m.label, "type": m.type, "unit": m.unit, "owner": m.owner,
           "description": m.description, "definition": m.definition, "caveats": list(m.caveats),
           "dimensions": [{"name": d, "description": model.dimensions[d].description,
                           "values": list(model.dimensions[d].values or [])} for d in ["month", *m.dimensions]],
           "months": "inclusive ('YYYY-MM', 'YYYY-MM'); must lie within the build's coverage window",
           "semantic_digest": model.digest}
    if m.type == "ratio":
        out["numerator"], out["denominator"] = raw["numerator"], raw["denominator"]
    return out


def metric_definition_digest(model: SemanticModel, name: str) -> str:
    """sha256 over everything that defines `name`: its spec, the dataset(s) it reads, its dimensions,
    the time spine and, for a ratio, numerator and denominator. Unrelated edits to the model leave it
    unchanged; a changed definition is a new digest, so answers bound to the old one become stale."""
    m = _metric(model, name)
    spec = model.spec
    parts = [name] if m.type == "simple" else [name, spec["metrics"][name]["numerator"],
                                               spec["metrics"][name]["denominator"]]
    defn: dict = {"time_spine": spec["time_spine"], "metrics": {}, "datasets": {},
                  "dimensions": {d: spec["dimensions"][d] for d in ["month", *m.dimensions]}}
    for part in parts:
        raw = spec["metrics"][part]
        defn["metrics"][part] = raw
        if raw.get("type", "simple") == "simple":
            defn["datasets"][raw["dataset"]] = spec["datasets"][raw["dataset"]]
    return digest(defn)


# --------------------------------------------------------------------------- compiling
def _metric(model: SemanticModel, name) -> Metric:
    if not isinstance(name, str) or name not in model.metrics:
        raise ValueError(f"unknown metric {name!r}; known metrics: {sorted(model.metrics)}")
    return model.metrics[name]


def _validate_months(months) -> tuple[str, str] | None:
    if months is None:
        return None
    if isinstance(months, str) or len(months) != 2:
        raise ValueError(f"months must be a pair ('YYYY-MM', 'YYYY-MM'), got {months!r}")
    a, b = (str(x) for x in months)
    if not (_MONTH.match(a) and _MONTH.match(b)):
        raise ValueError(f"months must be 'YYYY-MM' strings, got {months!r}")
    if a > b:
        raise ValueError(f"months range is empty: {a} > {b}")
    return a, b


def _validate_request(model: SemanticModel, metric: str, dimensions, filters) -> tuple[Metric, list[str], dict]:
    m = _metric(model, metric)
    if not isinstance(dimensions, (list, tuple)):
        raise ValueError(f"dimensions must be a list of names, got {dimensions!r}")
    if filters is not None and not isinstance(filters, dict):
        raise ValueError(f"filters must be a mapping dimension -> value(s), got {filters!r}")
    dims = [d for d in dimensions if d != "month"]
    if len(dims) != len(set(dims)):
        raise ValueError(f"duplicate dimensions in {list(dimensions)}")
    bad = [d for d in dims if d not in m.dimensions]
    if bad:
        raise ValueError(f"metric {metric} does not support dimensions {bad}; allowed: {['month', *m.dimensions]}")
    clean: dict[str, list[str]] = {}
    for key, val in (filters or {}).items():
        if key == "month":
            raise ValueError("filter on month via months=('YYYY-MM', 'YYYY-MM'), not filters")
        if key not in m.dimensions:
            raise ValueError(f"metric {metric} cannot be filtered by {key!r}; allowed: {m.dimensions}")
        vals = [val] if isinstance(val, str) else list(val) if isinstance(val, (list, tuple)) else None
        if not vals or not all(isinstance(v, str) for v in vals):
            raise ValueError(f"filter {key} must be a string or a non-empty list of strings, got {val!r}")
        allowed = model.dimensions[key].values
        unknown = [v for v in vals if allowed is not None and v not in allowed]
        if unknown:
            raise ValueError(f"filter {key}: unknown values {unknown}; allowed: {list(allowed)}")
        clean[key] = sorted(set(vals))
    return m, dims, clean


def _lit(v: str) -> str:
    return "'" + v.replace("'", "''") + "'"


def _spine_sql(months: tuple[str, str] | None) -> str:
    cols = ("strftime(m, '%Y-%m') AS month, m AS month_start, "
            "(m + INTERVAL 1 MONTH - INTERVAL 1 DAY) AS month_end")
    if months is None:
        return (f"  SELECT {cols}\n  FROM coverage, unnest(generate_series(CAST(first_month || '-01' AS DATE), "
                f"CAST(last_month || '-01' AS DATE), INTERVAL 1 MONTH)) t(m)")
    y, mo = map(int, months[1].split("-"))
    stop = f"{y + mo // 12:04d}-{mo % 12 + 1:02d}-01"     # first day after the inclusive end month
    return (f"  SELECT {cols}\n  FROM range(DATE '{months[0]}-01', DATE '{stop}', INTERVAL 1 MONTH) t(m)\n"
            f"  WHERE strftime(m, '%Y-%m') BETWEEN (SELECT first_month FROM coverage) "
            f"AND (SELECT last_month FROM coverage)")


def _simple_ctes(model: SemanticModel, name: str, dims: list[str], filters: dict) -> list[tuple[str, str]]:
    raw = model.spec["metrics"][name]
    ds_name = raw["dataset"]
    ds = model.spec["datasets"][ds_name]
    measure = ds["measures"][raw["measure"]]
    dim_cols = ds["dimensions"]
    where = [ds["required_filter"].strip()]
    if raw.get("filter"):
        where.append(str(raw["filter"]).strip())
    for key, vals in filters.items():
        where.append(f"b.{dim_cols[key]} IN ({', '.join(_lit(v) for v in vals)})")
    select = ["s.month", *[f"b.{dim_cols[d]} AS {d}" for d in dims], f"{measure['expr']} AS measure"]
    facts = (f"  SELECT {', '.join(select)}\n  FROM spine s\n"
             f"  JOIN ds_{ds_name} b ON {' '.join(str(raw.get('month_join') or ds['month_join']).split())}\n"
             f"  WHERE " + "\n    AND ".join(f"({w})" for w in where))
    keys = ["month", *dims]
    agg = measure["agg"]
    if agg in _ADDITIVE:
        # Zero-fill: inside the coverage window "no rows" really means 0 (e.g. no churn that month).
        # integer money (fen) by default; a measure in a currency with cents declares cast: double
        value = "CAST(" + _ADDITIVE[agg].format(x="measure") + f" AS {measure.get('cast', 'bigint').upper()})"
        grid = ("spine g" if not dims else
                f"(SELECT s.month, {', '.join(f'd.{d}' for d in dims)} FROM spine s "
                f"CROSS JOIN (SELECT DISTINCT {', '.join(dims)} FROM m_{name}_facts) d) g")
        on = " AND ".join(["a.month = g.month", *[f"a.{d} IS NOT DISTINCT FROM g.{d}" for d in dims]])
        body = (f"  SELECT {', '.join(f'g.{k}' for k in keys)}, COALESCE(a.value, 0) AS value\n  FROM {grid}\n"
                f"  LEFT JOIN (SELECT {', '.join(keys)}, {value} AS value FROM m_{name}_facts "
                f"GROUP BY {', '.join(keys)}) a\n    ON {on}")
    else:
        # A mean over zero rated rows is undefined, not 0: such groups are omitted.
        value = "CAST(" + _NON_ADDITIVE[agg].format(x="measure") + " AS DOUBLE)"
        body = (f"  SELECT {', '.join(keys)}, {value} AS value\n  FROM m_{name}_facts\n"
                f"  GROUP BY {', '.join(keys)}\n  HAVING COUNT(measure) > 0")
    return [(f"ds_{ds_name}", "  " + ds["relation"].strip().replace("\n", "\n  ")),
            (f"m_{name}_facts", facts), (f"m_{name}", body)]


def compile_metric(model: SemanticModel, metric: str, dimensions: list[str],
                   months: tuple[str, str] | None = None,
                   filters: dict[str, str | list[str]] | None = None) -> str:
    """SQL over silver.* returning (month, <dimensions...>, value), ordered by the keys.

    `months` is inclusive. If it is None, the result covers the build's whole coverage window. Additive
    metrics are zero-filled per month inside the window. Ratio and average rows whose denominator is
    zero are omitted because the value is undefined.
    """
    m, dims, flt = _validate_request(model, metric, dimensions, filters)
    rng = _validate_months(months)
    ctes: list[tuple[str, str]] = [("coverage", "  " + model.coverage_sql), ("spine", _spine_sql(rng))]
    parts = ([metric] if m.type == "simple"
             else [model.spec["metrics"][metric]["numerator"], model.spec["metrics"][metric]["denominator"]])
    for part in parts:
        for cte in _simple_ctes(model, part, dims, flt):
            if cte[0] not in {c[0] for c in ctes}:
                ctes.append(cte)
    keys = ["month", *dims]
    if m.type == "ratio":
        num, den = parts
        on = " AND ".join(["n.month = d.month", *[f"n.{d} IS NOT DISTINCT FROM d.{d}" for d in dims]])
        ctes.append((f"m_{metric}",
                     f"  SELECT {', '.join(f'd.{k}' for k in keys)}, "
                     f"CAST(COALESCE(n.value, 0) AS DOUBLE) / d.value AS value\n"
                     f"  FROM m_{den} d\n  LEFT JOIN m_{num} n ON {on}\n  WHERE d.value <> 0"))
    with_sql = ",\n".join(f"{name} AS (\n{body}\n)" for name, body in ctes)
    return (f"-- metric: {metric} | semantic_digest: {model.digest[:16]}\n"
            f"WITH {with_sql}\nSELECT {', '.join(keys)}, value\nFROM m_{metric}\nORDER BY {', '.join(keys)}")


_EVENT_JOIN = re.compile(r"^b\.\w+ = s\.month$")


def period_defined(spec: dict, metric: str) -> bool:
    """A value over a whole window (H v0.5 F19) is defined for event metrics, whose rows fall in one month
    (`month_join: b.<month> = s.month`): a customer who ordered in two months is one customer of the quarter.
    A metric measured at a point in time (subscribers active at a month's start) has no period value."""
    m = spec["metrics"][metric]
    if m.get("type", "simple") == "ratio":
        return period_defined(spec, m["numerator"]) and period_defined(spec, m["denominator"])
    join = m.get("month_join") or spec["datasets"][m["dataset"]].get("month_join")
    return bool(join) and _EVENT_JOIN.match(" ".join(str(join).split())) is not None


def compile_metric_period(model: SemanticModel, metric: str, dimensions: list[str], months: tuple[str, str],
                          filters: dict[str, str | list[str]] | None = None) -> str:
    """SQL over silver.* returning (<dimensions...>, value) for the window `months` taken as ONE period (H v0.5
    F19): a distinct count counts each key once over the window, a sum adds the months, an average is over all
    rows, a ratio divides the window's numerator by its denominator. Only where `period_defined`."""
    m, dims, flt = _validate_request(model, metric, dimensions, filters)
    rng = _validate_months(months)
    if rng is None:
        raise ValueError("a value over a period needs months (first, last)")
    if not period_defined(model.spec, metric):
        raise ValueError(f"{metric} is measured at a point in time: it has no value over a whole period")
    ctes: list[tuple[str, str]] = [("coverage", "  " + model.coverage_sql), ("spine", _spine_sql(rng))]
    parts = ([metric] if m.type == "simple"
             else [model.spec["metrics"][metric]["numerator"], model.spec["metrics"][metric]["denominator"]])
    group = f"\n  GROUP BY {', '.join(dims)}" if dims else ""
    for part in parts:
        for cte in _simple_ctes(model, part, dims, flt)[:2]:        # the dataset and its facts, not the monthly body
            if cte[0] not in {c[0] for c in ctes}:
                ctes.append(cte)
        raw = model.spec["metrics"][part]
        measure = model.spec["datasets"][raw["dataset"]]["measures"][raw["measure"]]
        agg = measure["agg"]
        if agg in _ADDITIVE:
            value = (f"CAST(COALESCE({_ADDITIVE[agg].format(x='measure')}, 0) AS "
                     f"{measure.get('cast', 'bigint').upper()})")
            having = ""
        else:
            value, having = f"CAST({_NON_ADDITIVE[agg].format(x='measure')} AS DOUBLE)", "\n  HAVING COUNT(measure) > 0"
        ctes.append((f"p_{part}", f"  SELECT {', '.join([*dims, value + ' AS value'])}\n  FROM m_{part}_facts"
                                  f"{group}{having}"))
    if m.type == "ratio":
        num, den = parts
        on = " AND ".join(f"n.{d} IS NOT DISTINCT FROM d.{d}" for d in dims) or "TRUE"
        ctes.append((f"p_{metric}",
                     f"  SELECT {', '.join([*(f'd.{d}' for d in dims), 'CAST(COALESCE(n.value, 0) AS DOUBLE) / d.value AS value'])}\n"
                     f"  FROM p_{den} d\n  LEFT JOIN p_{num} n ON {on}\n  WHERE d.value <> 0"))
    with_sql = ",\n".join(f"{name} AS (\n{body}\n)" for name, body in ctes)
    order = f"\nORDER BY {', '.join(dims)}" if dims else ""
    return (f"-- metric: {metric} over {rng[0]}..{rng[1]} as one period | semantic_digest: {model.digest[:16]}\n"
            f"WITH {with_sql}\nSELECT {', '.join([*dims, 'value'])}\nFROM p_{metric}{order}")


def query_metric_period(con: duckdb.DuckDBPyConnection, model: SemanticModel, metric: str, dimensions: list[str],
                        months: tuple[str, str], filters: dict[str, str | list[str]] | None = None) -> pd.DataFrame:
    """Execute a governed metric over one whole period. Columns: <dimensions...>, value."""
    sql = compile_metric_period(model, metric, dimensions, months, filters)
    rng = _validate_months(months)
    first, last = coverage(con, model)
    if rng[0] < first or rng[1] > last:
        raise ValueError(f"months {rng[0]}..{rng[1]} are outside this build's coverage {first}..{last}")
    return con.execute(sql).df()


def coverage(con: duckdb.DuckDBPyConnection, model: SemanticModel) -> tuple[str, str]:
    """(first_month, last_month) the build can report on. Raises ValueError if the ledger is empty."""
    row = con.execute(model.coverage_sql).fetchone()
    if row is None or row[0] is None or row[1] is None:
        raise ValueError("build has an empty billing ledger: no months can be reported")
    return str(row[0]), str(row[1])


def query_metric(con: duckdb.DuckDBPyConnection, model: SemanticModel, metric: str, dimensions: list[str],
                 months: tuple[str, str] | None = None,
                 filters: dict[str, str | list[str]] | None = None) -> pd.DataFrame:
    """Execute a governed metric. Columns: month, <dimensions...>, value."""
    sql = compile_metric(model, metric, dimensions, months, filters)   # validates before touching data
    rng = _validate_months(months)
    if rng is not None:
        first, last = coverage(con, model)
        if rng[0] < first or rng[1] > last:
            raise ValueError(f"months {rng[0]}..{rng[1]} are outside this build's coverage {first}..{last}")
    return con.execute(sql).df()


# --------------------------------------------------------------------------- verified queries
DEFAULT_VERIFIED_QUERIES = Path(__file__).parent / "verified_queries.yaml"
SPLITS = ("context", "eval")


def load_verified_queries(split: str, path: str | Path | None = None,
                          model: SemanticModel | None = None) -> list[dict]:
    """Verified queries for one split: 'context' (runtime examples for agents), 'eval' (ground truth,
    evaluator only) or 'all' (tooling/tests).

    `split` has no default on purpose. A caller must say which side of the context/eval wall it is on.
    Every metric_call is compiled against the semantic model at load time, so a stale entry fails here.
    """
    if split not in (*SPLITS, "all"):
        raise ValueError(f"split must be one of {SPLITS + ('all',)}, got {split!r}")
    model = model or load_semantic_model()
    spec = yaml.safe_load(Path(path or DEFAULT_VERIFIED_QUERIES).read_text(encoding="utf-8"))
    entries = spec.get("queries") if isinstance(spec, dict) else None
    if not isinstance(entries, list) or not entries:
        raise ValueError("verified queries invalid: 'queries' must be a non-empty list")
    seen: set[str] = set()
    out = []
    for e in entries:
        qid = e.get("id")
        if not isinstance(qid, str) or qid in seen:
            raise ValueError(f"verified queries invalid: missing or duplicate id {qid!r}")
        seen.add(qid)
        if e.get("split") not in SPLITS:
            raise ValueError(f"verified query {qid}: split must be one of {SPLITS}")
        if not isinstance(e.get("question"), str) or not e["question"].strip():
            raise ValueError(f"verified query {qid}: question required")
        if ("metric_call" in e) == ("sql" in e):
            raise ValueError(f"verified query {qid}: exactly one of metric_call / sql is required")
        item = {"id": qid, "split": e["split"], "question": e["question"].strip(),
                "metric_call": None, "sql": None, "notes": e.get("notes"), "traps": list(e.get("traps") or [])}
        if "metric_call" in e:
            mc = e["metric_call"]
            call = {"metric": mc.get("metric"), "dimensions": list(mc.get("dimensions") or []),
                    "months": tuple(mc["months"]) if mc.get("months") is not None else None,
                    "filters": dict(mc["filters"]) if mc.get("filters") else None}
            try:
                compile_metric(model, **call)
            except ValueError as exc:
                raise ValueError(f"verified query {qid}: {exc}") from exc
            item["metric_call"] = call
        else:
            if not isinstance(e["sql"], str) or not e["sql"].strip():
                raise ValueError(f"verified query {qid}: sql must be a non-empty string")
            item["sql"] = e["sql"].strip()
        out.append(item)
    return [q for q in out if split == "all" or q["split"] == split]


def run_verified_query(con: duckdb.DuckDBPyConnection, model: SemanticModel, vq: dict) -> pd.DataFrame:
    """Execute one verified query (governed metric or reviewed SQL) and return its result frame."""
    if vq.get("metric_call"):
        return query_metric(con, model, **vq["metric_call"])
    return con.execute(vq["sql"]).df()
