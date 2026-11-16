"""Agent-facing data catalog: table docs, trust tiers, PII flags and temporal tags.

The catalog is the allowlist of what agents may query. The SQL analyzer
(verify/sql_static.py) treats a table missing from it as a violation. The leakage
checks (verify/leakage.py) read the per-column `temporal` tags.
The loader validates every entry and fails on a malformed catalog. An unknown tag
is never read as "safe".
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import re

import yaml

from ..common import digest

DEFAULT_CATALOG = Path(__file__).parent / "catalog.yaml"
TIERS = ("certified", "staging", "uncertified")
TEMPORAL_TAGS = ("static", "event_time", "availability_time", "mutable_current", "post_outcome")
_TOKEN = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class ColumnDoc:
    name: str
    description: str
    pii: bool
    temporal: str
    caveats: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TableDoc:
    name: str                       # fully qualified: schema.table
    schema: str
    tier: str
    description: str
    grain: str
    columns: dict[str, ColumnDoc]
    caveats: list[str] = field(default_factory=list)
    required_filters: list[str] = field(default_factory=list)

    def columns_tagged(self, *tags: str) -> list[str]:
        return [c.name for c in self.columns.values() if c.temporal in tags]


@dataclass
class Catalog:
    tables: dict[str, TableDoc]
    digest: str
    rules: dict[str, dict] = field(default_factory=dict)
    restricted_schemas: dict[str, str] = field(default_factory=dict)   # schema -> reason
    pii_columns: frozenset[str] = frozenset()                          # lower-case column names

    def temporal_tag(self, table: str, column: str) -> str | None:
        t = self.tables.get(table.lower())
        c = t.columns.get(column.lower()) if t else None
        return c.temporal if c else None


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise ValueError(f"catalog invalid: {msg}")


def _str_list(v, where: str) -> list[str]:
    v = [] if v is None else v
    _require(isinstance(v, list) and all(isinstance(x, str) for x in v), f"{where} must be a list of strings")
    return list(v)


def load_catalog(path: str | Path | None = None) -> Catalog:
    path = Path(path or DEFAULT_CATALOG)
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    _require(isinstance(spec, dict) and isinstance(spec.get("tables"), dict), "missing 'tables' mapping")
    rules = spec.get("rules") or {}
    restricted = spec.get("restricted") or {}
    pii = {str(c).lower() for schema in restricted.values()
           for cols in (schema.get("pii_columns") or {}).values() for c in cols}
    tables: dict[str, TableDoc] = {}
    for fq, t in spec["tables"].items():
        _require(fq == fq.lower() and fq.count(".") == 1, f"table key {fq!r} must be lower-case schema.table")
        schema, _ = fq.split(".")
        _require(t.get("schema") == schema, f"{fq}: schema field must be {schema!r}")
        _require(schema not in restricted, f"{fq}: schema {schema} is restricted and cannot be catalogued")
        _require(t.get("tier") in TIERS, f"{fq}: tier must be one of {TIERS}")
        for key in ("description", "grain"):
            _require(isinstance(t.get(key), str) and t[key].strip(), f"{fq}: '{key}' required")
        req = _str_list(t.get("required_filters"), f"{fq}.required_filters")
        _require(all(r in rules for r in req), f"{fq}: unknown required filter rule in {req}")
        cols: dict[str, ColumnDoc] = {}
        _require(isinstance(t.get("columns"), dict) and t["columns"], f"{fq}: columns required")
        for cname, c in t["columns"].items():
            where = f"{fq}.{cname}"
            _require(isinstance(c, dict) and isinstance(c.get("description"), str), f"{where}: description required")
            _require(isinstance(c.get("pii"), bool), f"{where}: pii must be true/false")
            _require(c.get("temporal") in TEMPORAL_TAGS, f"{where}: temporal must be one of {TEMPORAL_TAGS}")
            cols[cname.lower()] = ColumnDoc(cname.lower(), c["description"], c["pii"], c["temporal"],
                                            _str_list(c.get("caveats"), f"{where}.caveats"))
            if c["pii"]:
                pii.add(cname.lower())
        tables[fq] = TableDoc(fq, schema, t["tier"], t["description"].strip(), t["grain"].strip(), cols,
                              _str_list(t.get("caveats"), f"{fq}.caveats"), req)
    return Catalog(tables=tables, digest=digest(spec), rules=rules,
                   restricted_schemas={s: str(v.get("reason", "")).strip() for s, v in restricted.items()},
                   pii_columns=frozenset(pii))


def _resolve(catalog: Catalog, name: str) -> TableDoc:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("table name required")
    key = name.strip().lower()
    schema = key.split(".")[0] if "." in key else None
    if schema in catalog.restricted_schemas:
        raise ValueError(f"{key} is in restricted schema '{schema}': {catalog.restricted_schemas[schema]}")
    if key in catalog.tables:
        return catalog.tables[key]
    if "." not in key:
        hits = [t for fq, t in catalog.tables.items() if fq.split(".")[1] == key]
        if len(hits) == 1:
            return hits[0]
        if hits:
            raise ValueError(f"ambiguous table {name!r}: {[t.name for t in hits]}")
    raise ValueError(f"unknown table {name!r}; search_catalog lists the available tables")


def describe_table(catalog: Catalog, name: str) -> dict:
    t = _resolve(catalog, name)
    out = asdict(t)
    out["columns"] = [asdict(c) for c in t.columns.values()]
    out["required_filters"] = [{"rule": r, **catalog.rules[r]} for r in t.required_filters]
    out["usage"] = {
        "certified": "Certified table. Results are governed when the required filters are applied.",
        "staging": "Staging table (not certified). Results will be flagged; prefer silver.*.",
        "uncertified": "Uncertified raw export. Results will be flagged; prefer governed metrics or silver.*.",
    }[t.tier]
    return out


_TIER_BOOST = {"certified": 0.5, "staging": 0.0, "uncertified": -0.25}


def search_catalog(catalog: Catalog, query: str) -> list[dict]:
    """Keyword ranking: table-name hits weigh most, then column names, then descriptions/caveats.

    Certified tables rank above raw copies on ties, so agents meet the governed table first.
    """
    if not isinstance(query, str):
        raise ValueError("query must be a string")
    terms = set(_TOKEN.findall(query.lower()))
    if not terms:
        raise ValueError("query has no searchable words")
    results = []
    for t in catalog.tables.values():
        name_tok = set(_TOKEN.findall(t.name))
        text_tok = set(_TOKEN.findall(" ".join([t.description, t.grain, *t.caveats]).lower()))
        score, matched_cols = 0.0, []
        for term in terms:
            if term in name_tok:
                score += 5
            for c in t.columns.values():
                col_text = set(_TOKEN.findall(f"{c.description} {' '.join(c.caveats)}".lower()))
                if term in set(_TOKEN.findall(c.name)):
                    score += 3
                    matched_cols.append(c.name)
                elif term in col_text:
                    score += 0.5
                    matched_cols.append(c.name)
            if term in text_tok:
                score += 1
        if score > 0:
            results.append({"name": t.name, "tier": t.tier, "description": t.description,
                            "score": score + _TIER_BOOST[t.tier],
                            "matched_columns": sorted(set(matched_cols))})
    results.sort(key=lambda r: (-r["score"], r["name"]))
    return results
