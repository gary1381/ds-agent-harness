"""E3b v2 power: how likely is the R1 rule to be met, over request clusters, for a given base rate and catch rate?

Model (written 2026-11-07, before any held-out v2 attempt; PREREGISTRATION.md, "Power"):
- 40 held-out requests (bank_v2.yaml), 2 agents x k attempts per arm per request (k = 3 or 2, by the timing
  rule; both shown);
- request r has an arm-N silent-error probability p_r ~ Beta(mu * phi, (1 - mu) * phi): phi = 1 puts the errors on
  a few requests (as the pilot's), phi = 5 spreads them;
- the harness catches each error with probability f, so p_r(H) = p_r * (1 - f); it adds none;
- the rule is met when the paired cluster bootstrap (2,000 resamples over requests) puts the upper end of
  R1(H) - R1(N) below 0 (R2 is not modelled).

    .venv/bin/python experiments/E3b/power_v2.py      # one core, about a minute
"""
from __future__ import annotations

import numpy as np

REQUESTS, BOOT, SIMS, SEED = 40, 2000, 1000, 20261108
PER_ARM = 6
MU = (0.05, 0.10, 0.20, 0.30)
F = (0.25, 0.50, 0.75, 1.00)
PHI = (1.0, 5.0)


def power(mu: float, f: float, phi: float, rng: np.random.Generator, idx: np.ndarray, per_arm: int = PER_ARM) -> float:
    met = 0
    for _ in range(SIMS):
        p = rng.beta(mu * phi, (1 - mu) * phi, REQUESTS)
        en = rng.binomial(per_arm, p)
        eh = rng.binomial(per_arm, p * (1 - f))
        d = (eh - en)[idx].sum(axis=1) / (REQUESTS * per_arm)
        met += bool(eh.sum() < en.sum() and np.quantile(d, 0.975) < 0)
    return met / SIMS


def main() -> int:
    rng = np.random.default_rng(SEED)
    idx = rng.integers(0, REQUESTS, (BOOT, REQUESTS))
    print(f"P(R1 rule met), {SIMS} simulations each; rows: arm-N silent-error rate mu; columns: share of errors the "
          f"harness catches f\n")
    for per_arm, phi in [(6, 1.0), (6, 5.0), (4, 1.0)]:
        print(f"k = {per_arm // 2}, phi = {phi:g} ({'errors on few requests' if phi < 2 else 'errors spread'})\n")
        print("| mu \\ f | " + " | ".join(f"{f:.0%}" for f in F) + " |")
        print("|---|" + "---|" * len(F))
        for mu in MU:
            print(f"| {mu:.0%} | " + " | ".join(f"{power(mu, f, phi, rng, idx, per_arm):.2f}" for f in F) + " |", flush=True)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
