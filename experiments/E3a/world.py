"""E3a world for the census: the disguised UCI Online Retail II copy as a harness workspace.

    .venv/bin/python experiments/E3a/world.py            # build workspace/external/e3a/ws (idempotent)

The workspace carries its own reviewed definitions in context/ (copied from experiments/E3a/context:
semantic model, catalog, pipeline), one source export at CUTOFF and its governed build. The evaluator
database holds `truth_lines`, the independent build B after adjudication (DESIGN.md); the census
refuses to start without it. Data stays under workspace/ (git-ignored).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

import duckdb
import pandera.pandas as pa
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from dsharness.bench.census import CensusError, World, render_catalog_md  # noqa: E402
from dsharness.lake import contracts as LC  # noqa: E402
from dsharness.lake.build import build_snapshot, latest_ready_build, load_report  # noqa: E402
from dsharness.lake.build import build_id_for, pipeline_digest  # noqa: E402
from dsharness.workspace import Workspace  # noqa: E402

HERE = Path(__file__).resolve().parent
DATA = ROOT / "workspace/external/e3a"
RAW = DATA / "raw_lines.parquet"                  # experiments/E3a/build_world.py
TRUTH_B = DATA / "truth_lines.parquet"            # independent build B, adjudicated
WS = Workspace(DATA / "ws")
CUTOFF = "2025-12-09"                              # last day of the data
TABLE = "order_lines"
SCHEMAS = ("bronze_safe", "staging", "silver")
CONTRACT = pa.DataFrameSchema({
    "order_no": pa.Column(str, coerce=True), "sku": pa.Column(str, coerce=True),
    "item_name": pa.Column(str, nullable=True), "qty": pa.Column(int, coerce=True),
    "ordered_at": pa.Column(pa.DateTime, coerce=True), "unit_price": pa.Column(float, coerce=True),
    "buyer_id": pa.Column(float, nullable=True, coerce=True), "country": pa.Column(str),
    "available_at": pa.Column(pa.DateTime, coerce=True),
}, strict=True)
TIERS = ("Tiers: **certified** = silver.* (reviewed SQL, contract-checked, duplicates collapsed, every line "
         "typed); **staging** = staging.* (typed raw export, duplicates and non-product lines still present); "
         "**uncertified** = bronze_safe.* (the raw order-system export).")


def _key(v: str) -> str:                           # census._norm_key without the alias lookup
    return re.sub(r"[\s\-]+", "_", str(v).strip().lower())


def _key_aliases() -> dict[str, str]:
    """Answer-key spellings that mean the same market or country (format, not substance): the
    reviewed dimension aliases of the semantic model (Ireland / 爱尔兰 = EIRE), plus a few more.
    Market UK and country United Kingdom share the key "uk"; a question keys by one of them only."""
    out = {"united_kingdom": "uk", "nonuk": "non_uk", "non_united_kingdom": "non_uk", "outside_uk": "non_uk",
           "us": "usa", "united_states_of_america": "usa", "yes": "true", "no": "false"}
    dims = yaml.safe_load((HERE / "context" / "semantic_model.yaml").read_text(encoding="utf-8"))["dimensions"]
    for d in ("market", "country"):
        for v, spellings in (dims[d].get("aliases") or {}).items():
            target = out.get(_key(v), _key(v))
            for a in (v, *spellings):
                out.setdefault(_key(a), target)
    return out


ALIASES = _key_aliases()


def _sync_context(ws: Workspace) -> None:
    ws.context.mkdir(parents=True, exist_ok=True)
    for name in ("semantic_model.yaml", "catalog.yaml"):
        shutil.copyfile(HERE / "context" / name, ws.context / name)
    shutil.copytree(HERE / "context" / "pipeline", ws.context / "pipeline", dirs_exist_ok=True)


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _export(ws: Workspace) -> None:
    """The source export the build reads; an export made from an older raw file is refused, not reused."""
    out = ws.source_dir(CUTOFF)
    if not RAW.exists():
        raise CensusError(f"missing {RAW}: run experiments/E3a/build_world.py first")
    raw_sha = _sha256(RAW)
    if (out / "_export_manifest.json").exists():
        made_from = json.loads((out / "_export_manifest.json").read_text()).get("raw_sha256")
        if made_from != raw_sha:
            raise CensusError(f"{out} was exported from another raw file ({made_from}); remove the workspace and rebuild")
        return
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{TABLE}.csv"
    con = duckdb.connect()
    con.execute("SET threads = 2")
    con.execute(f"COPY (SELECT *, ordered_at AS available_at FROM read_parquet('{RAW}')) TO '{path}' (HEADER)")
    rows = con.execute(f"SELECT count(*) FROM read_parquet('{RAW}')").fetchone()[0]
    con.close()
    (out / "_export_manifest.json").write_text(json.dumps(
        {"cutoff": CUTOFF, "raw_sha256": raw_sha,
         "tables": {TABLE: {"file": path.name, "rows": int(rows), "sha256": _sha256(path)}}}, indent=1))


def prepare(ws: Workspace, log=print) -> dict:
    """Context, source export and the governed build; a READY build for the current pipeline is reused."""
    ws.ensure()
    _sync_context(ws)
    _export(ws)
    LC.SOURCE_CONTRACTS[TABLE] = {1: CONTRACT}
    pipe = ws.context / "pipeline"
    bid = build_id_for(CUTOFF, pipeline_digest(pipe))
    if (ws.lake / bid / "build_report.json").exists():
        status = load_report(ws, bid)["status"]
        if status != "READY":
            raise CensusError(f"E3a build {bid} is {status}; remove it to rebuild")
        return {"build": bid, "status": status}
    rep = build_snapshot(ws, CUTOFF, pipeline_dir=pipe)
    log(f"built {rep.build_id}: {rep.status} {rep.reasons[:5]}")
    if rep.status != "READY":
        raise CensusError(f"E3a build {rep.build_id} is {rep.status}: {rep.reasons[:5]}")
    return {"build": rep.build_id, "status": rep.status}


def certified(ws: Workspace) -> str:
    return latest_ready_build(ws, CUTOFF, ws.context / "pipeline")


def evaluator_db(ws: Workspace, path: Path) -> Path:
    """truth_lines from the independent build B. Evaluator-only (never in a sandbox)."""
    if not TRUTH_B.exists():
        raise CensusError(f"missing {TRUTH_B}: the independent truth build B is not adjudicated yet")
    path = Path(path)
    path.unlink(missing_ok=True)
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE truth_lines AS SELECT * FROM read_parquet(?)", [str(TRUTH_B)])
    finally:
        con.close()
    return path


def catalog_md(ws: Workspace) -> str:
    return render_catalog_md(ws.context / "catalog.yaml", ws.context / "semantic_model.yaml", tiers=TIERS)


WORLD = World(name="e3a-retail", company="an online gift wholesaler", key_example='"month", "market"',
              prepare=prepare, certified=certified, evaluator_db=evaluator_db, catalog_md=catalog_md,
              cutoff=CUTOFF, schemas=SCHEMAS, key_aliases=ALIASES)


if __name__ == "__main__":
    print(prepare(WS))
