"""Build a governed warehouse for one source export: bronze (contract-checked) -> staging -> silver.

Output: workspace/lake/<build_id>/warehouse.duckdb + build_report.json, published atomically.
A build is READY only if every contract holds, every model built and every test passed;
otherwise it is FAILED and tools refuse to serve it (no "best effort" partial warehouse).
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import shutil

import duckdb
import pandas as pd

from ..workspace import Workspace
from .contracts import validate_source
from .runner import as_dicts, pipeline_digest, run_models, run_tests, sandbox

DEFAULT_PIPELINE = Path(__file__).parent / "pipeline"
PII_COLUMNS = {"crm_customers": ("email", "phone", "birth_year")}
_ID_COLS = {"customer_id", "invoice_id", "subscription_id", "ticket_id", "original_invoice_id", "phone",
            "contact_id"}


@dataclass
class BuildReport:
    build_id: str
    cutoff: str
    source_digest: str
    pipeline_digest: str
    status: str                       # READY | FAILED
    billing_schema_version: int | None
    contracts: list = field(default_factory=list)
    models: list = field(default_factory=list)
    tests: list = field(default_factory=list)
    reasons: list = field(default_factory=list)

    @property
    def warehouse(self) -> str:
        return "warehouse.duckdb"


def _canonical_digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_id_for(cutoff: str, pdig: str) -> str:
    return f"{cutoff}_{pdig[:8]}"


def source_digest(ws: Workspace, cutoff: str) -> str:
    """Identity of the source export a build of `cutoff` reads (its manifest, incl. file sha256s)."""
    return _canonical_digest(json.loads((ws.source_dir(cutoff) / "_export_manifest.json").read_text()))


def build_snapshot(ws: Workspace, cutoff: str, pipeline_dir: Path | None = None,
                   out_root: Path | None = None) -> BuildReport:
    pipeline_dir = Path(pipeline_dir or DEFAULT_PIPELINE)
    src = ws.source_dir(cutoff)
    manifest = json.loads((src / "_export_manifest.json").read_text())
    sdig = _canonical_digest(manifest)
    pdig = pipeline_digest(pipeline_dir)
    build_id = build_id_for(cutoff, pdig)
    out_root = Path(out_root or ws.lake)
    final = out_root / build_id
    tmp = out_root / f".{build_id}.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)

    report = BuildReport(build_id, cutoff, sdig, pdig, "FAILED", manifest.get("billing_schema_version"))
    con = duckdb.connect(str(tmp / "warehouse.duckdb"))
    try:
        con.execute("CREATE SCHEMA bronze")
        con.execute("CREATE SCHEMA bronze_safe")
        for table, meta in sorted(manifest["tables"].items()):
            raw = pd.read_csv(src / meta["file"],
                              dtype={c: "string" for c in _ID_COLS}, keep_default_na=True)
            if raw.empty:   # a header-only export (e.g. a program not started yet) has no dtypes to infer
                raw = raw.astype("string")
            result, frame = validate_source(table, raw)
            report.contracts.append(result.__dict__)
            if frame is None:
                report.reasons.append(f"contract:{table}")
                continue
            con.register("_frame", frame)
            con.execute(f"CREATE TABLE bronze.{table} AS SELECT * FROM _frame")
            con.unregister("_frame")
            safe_cols = [c for c in frame.columns if c not in PII_COLUMNS.get(table, ())]
            con.execute(f"CREATE VIEW bronze_safe.{table} AS SELECT {', '.join(safe_cols)} FROM bronze.{table}")
        # Everything after this point is pipeline SQL (possibly an agent's change): it may read the
        # warehouse only, never a file (custody/, sources/, ...), whatever the static screens miss.
        sandbox(con)
        models = run_models(con, pipeline_dir)
        report.models = as_dicts(models)
        built = {m.name for m in models if m.status == "OK"}
        report.reasons += [f"model:{m.name}:{m.status}" for m in models if m.status != "OK"]
        tests = run_tests(con, pipeline_dir, built)
        report.tests = as_dicts(tests)
        report.reasons += [f"test:{t.name}:{t.outcome}" for t in tests if t.outcome != "PASS"]
        report.status = "READY" if not report.reasons else "FAILED"
    finally:
        con.close()

    (tmp / "build_report.json").write_text(json.dumps(report.__dict__, indent=2, default=str))
    shutil.rmtree(final, ignore_errors=True)
    tmp.rename(final)
    return report


def load_report(ws: Workspace, build_id: str) -> dict:
    return json.loads((ws.lake / build_id / "build_report.json").read_text())


def latest_ready_build(ws: Workspace, cutoff: str, pipeline_dir: Path | None = None) -> str:
    """Return the READY build id for (cutoff, pipeline); raise if it is missing or failed."""
    bid = build_id_for(cutoff, pipeline_digest(Path(pipeline_dir or DEFAULT_PIPELINE)))
    rep = load_report(ws, bid)
    if rep["status"] != "READY":
        raise RuntimeError(f"build {bid} is {rep['status']}: {rep['reasons'][:5]}")
    return bid


def warehouse_path(ws: Workspace, build_id: str) -> Path:
    return ws.lake / build_id / "warehouse.duckdb"
