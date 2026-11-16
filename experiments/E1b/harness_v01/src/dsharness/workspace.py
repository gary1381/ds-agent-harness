"""On-disk layout. One root per environment; trust zones are separate directories so that
production deployments can map each one to a different mount / service account."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

# Source exports used by the reference scenario (see docs/DESIGN.zh-CN.md, scenario timeline).
CUTOFFS = {
    "train": "2026-05-31",     # model development; June churn is the sealed final test
    "score": "2026-06-30",     # production scoring as of 2026-07-01, July experiment
    "drift": "2026-08-31",     # billing schema change (D8) + population shift (D9)
    "monitor": "2026-09-30",   # matured labels for monitoring
}
# Extra historical exports used only by the point-in-time (time-travel) leakage check.
# Real deployments get these from warehouse time travel / Iceberg-Delta snapshots / CDC.
PIT_CUTOFFS = ("2026-02-28", "2026-03-31")


@dataclass(frozen=True)
class Workspace:
    root: Path

    @classmethod
    def default(cls) -> "Workspace":
        return cls(Path(os.environ.get("DSH_WORKSPACE", Path.cwd() / "workspace")).resolve())

    @property
    def sources(self) -> Path:      # zone: source-system exports (ingest service only)
        return self.root / "sources"

    @property
    def lake(self) -> Path:         # zone: governed data (agents: read-only via tools)
        return self.root / "lake"

    @property
    def custody(self) -> Path:      # zone: ground truth & sealed labels (evaluator only)
        return self.root / "custody"

    @property
    def registry(self) -> Path:     # zone: candidates, evidence, releases (controller writes)
        return self.root / "registry"

    @property
    def runs(self) -> Path:         # zone: per-run scratch outputs (candidate-writable)
        return self.root / "runs"

    def source_dir(self, cutoff: str) -> Path:
        return self.sources / cutoff

    def ensure(self) -> "Workspace":
        for p in (self.sources, self.lake, self.custody, self.registry, self.runs):
            p.mkdir(parents=True, exist_ok=True)
        return self
