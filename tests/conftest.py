"""Shared fixtures: one small benchmark world per test session (seconds, not minutes)."""
from __future__ import annotations

import pytest

from dsharness.lake.build import build_snapshot
from dsharness.workspace import CUTOFFS, PIT_CUTOFFS, Workspace
from dsharness.world.build import build_world
from dsharness.world.generator import WorldConfig

SMALL = WorldConfig(n_initial=700, monthly_signups=50, n_test_accounts=10)


@pytest.fixture(scope="session")
def small_ws(tmp_path_factory) -> Workspace:
    """Workspace with sources + custody + lake builds for every cutoff (default pipeline)."""
    ws = Workspace(tmp_path_factory.mktemp("ws")).ensure()
    build_world(ws, SMALL)
    reports = {c: build_snapshot(ws, c) for c in [*CUTOFFS.values(), *PIT_CUTOFFS]}
    _REPORTS.update(reports)   # Workspace is frozen; keep reports module-level
    return ws


_REPORTS: dict = {}


@pytest.fixture(scope="session")
def build_reports(small_ws) -> dict:
    """cutoff -> BuildReport for the default pipeline (train/score/pit READY; drift/monitor FAILED)."""
    return dict(_REPORTS)


@pytest.fixture(scope="session")
def train_build(build_reports) -> str:
    return build_reports[CUTOFFS["train"]].build_id


@pytest.fixture(scope="session")
def score_build(build_reports) -> str:
    return build_reports[CUTOFFS["score"]].build_id
