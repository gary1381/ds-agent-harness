"""E3c v2 power: how likely is the R1 rule to be met, over request clusters, for a given base rate and catch rate?

Model (written 2026-11-07, before any held-out v2 attempt; PREREGISTRATION.md, "Power"):
- 39 held-out requests (bank_v2.yaml minus ha17, exposed in the smoke test), 6 attempts per arm per request
  (2 agents x k = 3);
- request r has an arm-N silent-error probability p_r ~ Beta(mu * phi, (1 - mu) * phi): phi = 1 puts the errors on
  a few requests (as the pilot's), phi = 5 spreads them;
- the harness catches each error with probability f, so p_r(H) = p_r * (1 - f); it adds none;
- the rule is met when the paired cluster bootstrap (2,000 resamples over requests) puts the upper end of
  R1(H) - R1(N) below 0 (R2 is not modelled).

    .venv/bin/python experiments/E3c/power_v2.py      # one core, about a minute
"""
from __future__ import annotations

import numpy as np

REQUESTS, PER_ARM, BOOT, SIMS, SEED = 39, 6, 2000, 1000, 20261107
MU = (0.05, 0.10, 0.20, 0.30)
F = (0.25, 0.50, 0.75, 1.00)
PHI = (1.0, 5.0)


def power(mu: float, f: float, phi: float, rng: np.random.Generator, idx: np.ndarray) -> float:
    met = 0
    for _ in range(SIMS):
        p = rng.beta(mu * phi, (1 - mu) * phi, REQUESTS)
        en = rng.binomial(PER_ARM, p)
        eh = rng.binomial(PER_ARM, p * (1 - f))
        d = (eh - en)[idx].sum(axis=1) / (REQUESTS * PER_ARM)
        met += bool(eh.sum() < en.sum() and np.quantile(d, 0.975) < 0)
    return met / SIMS


def main() -> int:
    rng = np.random.default_rng(SEED)
    idx = rng.integers(0, REQUESTS, (BOOT, REQUESTS))
    print(f"P(R1 rule met), {SIMS} simulations each; rows: arm-N silent-error rate mu; columns: share of errors the "
          f"harness catches f\n")
    for phi in PHI:
        print(f"phi = {phi:g} ({'errors on few requests' if phi < 2 else 'errors spread over requests'})\n")
        print("| mu \\ f | " + " | ".join(f"{f:.0%}" for f in F) + " |")
        print("|---|" + "---|" * len(F))
        for mu in MU:
            print(f"| {mu:.0%} | " + " | ".join(f"{power(mu, f, phi, rng, idx):.2f}" for f in F) + " |", flush=True)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
