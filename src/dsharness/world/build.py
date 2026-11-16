"""Materialise the benchmark world into a workspace: source exports + evaluator custody."""
from __future__ import annotations

from ..workspace import CUTOFFS, PIT_CUTOFFS, Workspace
from .generator import WorldConfig, export_sources, simulate, write_custody


def build_world(ws: Workspace, cfg: WorldConfig | None = None) -> dict:
    ws.ensure()
    world = simulate(cfg)
    manifests = {name: export_sources(world, cutoff, ws.source_dir(cutoff))
                 for name, cutoff in CUTOFFS.items()}
    for cutoff in PIT_CUTOFFS:
        manifests[f"pit_{cutoff}"] = export_sources(world, cutoff, ws.source_dir(cutoff))
    write_custody(world, ws.custody)
    return {"defects": world.defects, "exports": manifests}
