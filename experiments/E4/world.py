"""E4 world for the census: the E3a warehouse (disguised UCI Online Retail II) with the E4 semantic model.

    .venv/bin/python experiments/E4/world.py            # build workspace/external/e4/ws (idempotent)

Same source export, pipeline, catalog and truth (build B) as E3a; only context/semantic_model.yaml differs
(experiments/E4/context: item code and customer type as dimensions, the round-2 term lists; H_v0.5_PLAN.md
O1-O3). The workspace is its own, so E3a's frozen workspace is never written. The census switches on the
benchmark fixes decided before E4 (H_v0.5_PLAN.md): B1, the prompt names each question's key columns and asks
for full precision; B2, the grader reads other spellings of a key column (item_code for sku) and a month
written as a quarter ('2025-Q1').
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import re
import shutil
import sys

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from dsharness.bench.census import CensusError, World, render_catalog_md  # noqa: E402
from dsharness.lake import contracts as LC  # noqa: E402
from dsharness.lake.build import build_snapshot, latest_ready_build, load_report  # noqa: E402
from dsharness.lake.build import build_id_for, pipeline_digest  # noqa: E402
from dsharness.workspace import Workspace  # noqa: E402

HERE = Path(__file__).resolve().parent
E3A_DIR = ROOT / "experiments/E3a"
_spec = importlib.util.spec_from_file_location("e3a_world", E3A_DIR / "world.py")
E3A = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(E3A)

WS = Workspace(ROOT / "workspace/external/e4/ws")
CUTOFF, TABLE, SCHEMAS = E3A.CUTOFF, E3A.TABLE, E3A.SCHEMAS
MODEL = HERE / "context" / "semantic_model.yaml"


def _key(v: str) -> str:                           # census._norm_key without the alias lookup
    return re.sub(r"[\s\-]+", "_", str(v).strip().lower())


def _aliases() -> tuple[dict[str, str], dict[str, str]]:
    """Key-value spellings (as E3a, plus customer type) and key-column spellings (B2), both from the E4 model's
    reviewed dimension aliases and names. Market UK and country United Kingdom share the key "uk"."""
    values = {"united_kingdom": "uk", "nonuk": "non_uk", "non_united_kingdom": "non_uk", "outside_uk": "non_uk",
              "us": "usa", "united_states_of_america": "usa", "yes": "true", "no": "false"}
    columns: dict[str, str] = {}
    dims = yaml.safe_load(MODEL.read_text(encoding="utf-8"))["dimensions"]
    for d in ("market", "country", "customer_type"):
        for v, spellings in (dims[d].get("aliases") or {}).items():
            target = values.get(_key(v), _key(v))
            for a in (v, *spellings):
                values.setdefault(_key(a), target)
    for d in ("sku", "customer_type"):
        for name in dims[d].get("names") or ():
            columns.setdefault(_key(name), d)
            columns.setdefault(_key(name).replace("_", ""), d)
    return values, columns


ALIASES, COLUMN_ALIASES = _aliases()


def _sync_context(ws: Workspace) -> None:
    ws.context.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(E3A_DIR / "context" / "catalog.yaml", ws.context / "catalog.yaml")
    shutil.copytree(E3A_DIR / "context" / "pipeline", ws.context / "pipeline", dirs_exist_ok=True)
    shutil.copyfile(MODEL, ws.context / "semantic_model.yaml")


def prepare(ws: Workspace, log=print) -> dict:
    """Context, source export and the governed build; a READY build for the current pipeline is reused."""
    ws.ensure()
    _sync_context(ws)
    E3A._export(ws)
    LC.SOURCE_CONTRACTS[TABLE] = {1: E3A.CONTRACT}
    pipe = ws.context / "pipeline"
    bid = build_id_for(CUTOFF, pipeline_digest(pipe))
    if (ws.lake / bid / "build_report.json").exists():
        status = load_report(ws, bid)["status"]
        if status != "READY":
            raise CensusError(f"E4 build {bid} is {status}; remove it to rebuild")
        return {"build": bid, "status": status}
    rep = build_snapshot(ws, CUTOFF, pipeline_dir=pipe)
    log(f"built {rep.build_id}: {rep.status} {rep.reasons[:5]}")
    if rep.status != "READY":
        raise CensusError(f"E4 build {rep.build_id} is {rep.status}: {rep.reasons[:5]}")
    return {"build": rep.build_id, "status": rep.status}


def certified(ws: Workspace) -> str:
    return latest_ready_build(ws, CUTOFF, ws.context / "pipeline")


def catalog_md(ws: Workspace) -> str:
    return render_catalog_md(ws.context / "catalog.yaml", ws.context / "semantic_model.yaml", tiers=E3A.TIERS)


WORLD = World(name="e4-retail", company="an online gift wholesaler", key_example='"month", "market"',
              prepare=prepare, certified=certified, evaluator_db=E3A.evaluator_db, catalog_md=catalog_md,
              cutoff=CUTOFF, schemas=SCHEMAS, key_aliases=ALIASES,
              name_keys=True, column_aliases=COLUMN_ALIASES, quarter_keys=True)


if __name__ == "__main__":
    print(prepare(WS))
