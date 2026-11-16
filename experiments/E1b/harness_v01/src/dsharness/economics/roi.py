"""Portfolio ROI model for agent-assisted data work, with measured overrides.

Why this exists: agentic data projects get cancelled for unclear value and runaway cost, and
ROI decks built on "hours saved" alone ignore the two numbers that decide the case -- what the
agent costs per *correct* task and how often it is wrong while claiming success. This model
makes every input explicit, labels where it came from, and lets the benchmark replace
assumptions with measurements (``silent_failure_rate``, ``cost_per_correct_task``, ...).

Sourced numbers (from docs/research/findings_2026-10-19.txt, economics section):
  * cn: NBS 2025 average annual wage, IT sector, non-private units: CNY 248,752.
    Employer social insurance + housing fund (~30-40% on top) is NOT verified there; the
    1.4 multiplier is an assumption.
  * us: BLS median data-scientist wage USD 120,230 (May 2025); BLS ECEC (June 2026) puts
    wages at 70.0% of private-industry compensation, so loaded = 120,230 / 0.70 ~= 172k.
Every other number (task volumes, minutes, adoption, LLM cost, platform cost, error cost,
setup cost, FX) is an ASSUMPTION for a mid-sized data team and must be replaced with local
data before anyone relies on the output. Revenue-side value defaults to zero: it is only
counted when an experiment readout measured it -- and then conservatively: ``portfolio_estimate(
..., readout=...)`` credits the decision use case with the readout's 95% LOWER bound per contact
times the contacts per month at scale, and only when the readout PASSed (``decision_value``).

Monthly model per use case (currency = profile currency):
  handled        = tasks_per_month * adoption
  gross_labor    = handled * (1 - flag_rate) * minutes_saved / 60 * loaded_cost_per_hour
                   (flagged tasks are redone by a human, so they save nothing)
  gross_value    = gross_labor + experiment_value_per_month
  llm_cost       = handled * llm_cost_per_task
  review_cost    = handled * review_minutes / 60 * loaded_cost_per_hour
  platform_cost  = platform_cost_per_month allocated by agent-handled work volume
                   (handled * baseline_minutes)
  error_cost     = handled * silent_failure_rate * error_cost_per_silent_failure
  net_value      = gross_value - (llm + review + platform + error)
  payback_months = setup_cost / net_value  (only if net_value > 0; otherwise it never pays back)
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, replace
import math

import pandas as pd

PROFILES = ("cn", "us")
_RATE_FIELDS = ("adoption", "silent_failure_rate", "flag_rate", "correct_rate")


@dataclass(frozen=True)
class UseCase:
    name: str
    description: str
    tasks_per_month: float
    baseline_minutes_per_task: float     # human-only effort per task
    minutes_saved_per_task: float        # human minutes saved when the agent drafts the work
    review_minutes_per_task: float       # human verification of every agent output
    adoption: float                      # share of tasks routed through the agent
    llm_cost_per_task: float             # model spend per handled task
    silent_failure_rate: float           # wrong but delivered as success (bench-measurable)
    flag_rate: float                     # caught or false-blocked -> human redo (bench-measurable)
    error_cost_per_silent_failure: float # rework + cost of a decision made on a wrong result
    setup_cost: float                    # one-off: context curation, verified queries, integration
    experiment_value_per_month: float = 0.0   # revenue side: only from a measured experiment readout

    def validate(self) -> None:
        for f in fields(self):
            v = getattr(self, f.name)
            if isinstance(v, (int, float)) and (not math.isfinite(v) or v < 0) \
                    and f.name != "experiment_value_per_month":
                raise ValueError(f"{self.name}.{f.name} must be finite and >= 0, got {v}")
        for r in ("adoption", "silent_failure_rate", "flag_rate"):
            if getattr(self, r) > 1:
                raise ValueError(f"{self.name}.{r} must be a share in [0, 1]")
        if self.silent_failure_rate + self.flag_rate > 1:
            raise ValueError(f"{self.name}: silent_failure_rate + flag_rate exceed 1")
        if self.minutes_saved_per_task > self.baseline_minutes_per_task:
            raise ValueError(f"{self.name}: cannot save more minutes than the task takes")
        if not math.isfinite(self.experiment_value_per_month):
            raise ValueError(f"{self.name}.experiment_value_per_month must be finite")


# name, description, tasks/mo, baseline min, saved min, review min, adoption,
# llm cost (CNY, USD), silent failure, flag rate, error cost (CNY, USD), setup (CNY, USD)
# ALL ASSUMPTIONS (illustrative mid-sized data team), see module docstring.
_USE_CASE_TABLE = (
    ("analytics_qa", "ad-hoc metric questions (ChatBI / text-to-SQL) answered via governed metrics",
     600, 40, 25, 5, 0.6, (2.0, 0.50), 0.05, 0.10, (1_500, 500), (200_000, 100_000)),
    ("pipeline_breakfix", "source schema change / pipeline break-fix with CI + reconciliation",
     8, 300, 150, 45, 0.6, (40.0, 10.0), 0.02, 0.20, (80_000, 25_000), (100_000, 50_000)),
    ("churn_model", "build / retrain a churn model through the ML factory (leakage checks, sealed eval)",
     2, 2_400, 1_200, 480, 0.5, (300.0, 60.0), 0.05, 0.20, (150_000, 50_000), (150_000, 80_000)),
    ("experiment_readout", "design and read out a targeting experiment; value claim vs ITT readout",
     4, 480, 240, 90, 0.5, (60.0, 15.0), 0.05, 0.10, (100_000, 30_000), (60_000, 30_000)),
)


def _use_cases(profile: str) -> tuple[UseCase, ...]:
    i = PROFILES.index(profile)
    return tuple(UseCase(name, desc, tasks, base, saved, review, adopt, llm[i], silent, flag, err[i], setup[i])
                 for (name, desc, tasks, base, saved, review, adopt, llm, silent, flag, err, setup)
                 in _USE_CASE_TABLE)


_DIGEST = "docs/research/findings_2026-10-19.txt"
_SOURCES = {
    "cn": {
        "annual_wage": f"NBS 2025 avg annual wage, IT sector (non-private units) CNY 248,752 [{_DIGEST}]",
        "loaded_multiplier": "ASSUMPTION 1.4: employer social insurance + housing fund ~30-40% "
                             f"(not verified in {_DIGEST})",
        "fx_usd_to_local": "ASSUMPTION 7.1 CNY per USD (converts bench costs measured in USD)",
    },
    "us": {
        "annual_wage": f"BLS median data-scientist wage USD 120,230, May 2025 [{_DIGEST}]",
        "loaded_multiplier": f"BLS ECEC June 2026: wages are 70.0% of compensation -> 1/0.70 [{_DIGEST}]",
        "fx_usd_to_local": "identity (profile currency is USD)",
    },
}
_COMMON_SOURCES = {
    "working_hours_per_year": "ASSUMPTION 2,000 paid hours/year",
    "platform_cost_per_month": "ASSUMPTION: hosting, warehouse compute share, observability, eval runs",
    "use_cases": "ASSUMPTION: illustrative mid-sized data team; replace with logged volumes and timings",
}


@dataclass(frozen=True)
class Assumptions:
    """Defaults are the "cn" profile; ``Assumptions.for_profile("us")`` gives the USD profile."""
    profile: str = "cn"
    currency: str = "CNY"
    annual_wage: float = 248_752.0
    loaded_multiplier: float = 1.4
    working_hours_per_year: float = 2_000.0
    fx_usd_to_local: float = 7.1
    platform_cost_per_month: float = 10_000.0
    use_cases: tuple[UseCase, ...] = field(default_factory=lambda: _use_cases("cn"))
    sources: dict = field(default_factory=lambda: {**_SOURCES["cn"], **_COMMON_SOURCES})

    @classmethod
    def for_profile(cls, profile: str) -> "Assumptions":
        if profile == "cn":
            return cls()
        if profile == "us":
            return cls(profile="us", currency="USD", annual_wage=120_230.0, loaded_multiplier=1 / 0.70,
                       fx_usd_to_local=1.0, platform_cost_per_month=3_000.0, use_cases=_use_cases("us"),
                       sources={**_SOURCES["us"], **_COMMON_SOURCES})
        raise ValueError(f"unknown profile {profile!r}; known: {PROFILES}")

    @property
    def loaded_annual_cost(self) -> float:
        return self.annual_wage * self.loaded_multiplier

    @property
    def loaded_cost_per_hour(self) -> float:
        return self.loaded_annual_cost / self.working_hours_per_year

    def validate(self) -> None:
        for name in ("annual_wage", "loaded_multiplier", "working_hours_per_year", "fx_usd_to_local"):
            v = getattr(self, name)
            if not math.isfinite(v) or v <= 0:
                raise ValueError(f"{name} must be positive, got {v}")
        if not math.isfinite(self.platform_cost_per_month) or self.platform_cost_per_month < 0:
            raise ValueError("platform_cost_per_month must be >= 0")
        if not self.use_cases:
            raise ValueError("no use cases")
        names = [u.name for u in self.use_cases]
        if len(set(names)) != len(names):
            raise ValueError("duplicate use-case names")
        for u in self.use_cases:
            u.validate()


_UC_NUMERIC = tuple(f.name for f in fields(UseCase) if f.name not in ("name", "description"))
_DERIVED_KEYS = ("cost_per_correct_task", "cost_per_correct_task_usd", "llm_cost_per_task_usd", "correct_rate")
MEASURED_KEYS = _UC_NUMERIC + _DERIVED_KEYS
_COST_KEYS = {"llm_cost_per_task", *_DERIVED_KEYS}


def _apply_measured(a: Assumptions, uc: UseCase, measured: dict) -> tuple[UseCase, list[str]]:
    """Merge global then use-case-specific measurements into one use case; return provenance."""
    merged = {k: v for k, v in measured.items() if k in MEASURED_KEYS}
    specific = measured.get(uc.name) or {}
    if not isinstance(specific, dict):
        raise ValueError(f"measured[{uc.name!r}] must be a dict of overrides")
    unknown = set(specific) - set(MEASURED_KEYS)
    if unknown:
        raise ValueError(f"unknown measured keys for {uc.name}: {sorted(unknown)}; known: {MEASURED_KEYS}")
    if set(specific) & _COST_KEYS:   # a use-case cost measurement replaces any global cost form
        merged = {k: v for k, v in merged.items() if k not in _COST_KEYS}
    merged.update(specific)
    for k, v in merged.items():
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
            raise ValueError(f"measured {k} for {uc.name} must be a finite number, got {v!r}")
        if k in _RATE_FIELDS and not 0 <= v <= 1:
            raise ValueError(f"measured {k} for {uc.name} must be in [0, 1], got {v}")

    direct = {k: float(v) for k, v in merged.items() if k in _UC_NUMERIC}
    notes = sorted(direct)
    usd = {"cost_per_correct_task_usd": "cost_per_correct_task", "llm_cost_per_task_usd": "llm_cost_per_task"}
    local = {k: float(merged[k]) for k in ("cost_per_correct_task", "llm_cost_per_task") if k in merged}
    for k_usd, k_local in usd.items():
        if k_usd in merged:
            if k_local in local:
                raise ValueError(f"{uc.name}: give {k_local} or {k_usd}, not both")
            local[k_local] = float(merged[k_usd]) * a.fx_usd_to_local
            notes.append(k_usd)
    if "cost_per_correct_task" in local:
        if "llm_cost_per_task" in local:
            raise ValueError(f"{uc.name}: give llm_cost_per_task or cost_per_correct_task, not both")
        if "correct_rate" in merged:
            # spend per handled task = spend per correct task * share of tasks that are correct
            direct["llm_cost_per_task"] = local["cost_per_correct_task"] * float(merged["correct_rate"])
            notes.append("correct_rate")
        else:
            # conservative: charge the per-correct cost on every handled task (overstates spend)
            direct["llm_cost_per_task"] = local["cost_per_correct_task"]
        if "cost_per_correct_task" in merged:
            notes.append("cost_per_correct_task")
    elif "llm_cost_per_task" in local:
        direct["llm_cost_per_task"] = local["llm_cost_per_task"]
    elif "correct_rate" in merged:
        raise ValueError(f"{uc.name}: correct_rate is only used with cost_per_correct_task")
    out = replace(uc, **direct)
    out.validate()
    return out, sorted(set(notes))


DECISION_USE_CASE = "experiment_readout"
FEN_PER_CNY = 100


def decision_value(readout: dict, contacts_per_month: float, a: Assumptions | None = None) -> dict:
    """Monthly decision value (profile currency) from a randomised readout, counted conservatively:
    the value-per-contact 95% LOWER bound times the contacts per month at scale, and zero unless the
    readout PASSed. A projection (the per-contact effect is assumed to transfer to the scaled
    population of the same targeting rule), never a measurement of the scaled programme."""
    a = a or Assumptions()
    if not isinstance(readout, dict) or "outcome" not in readout:
        raise ValueError("readout must be a decision.experiment.readout result")
    if not isinstance(contacts_per_month, (int, float)) or isinstance(contacts_per_month, bool) \
            or not math.isfinite(contacts_per_month) or contacts_per_month < 0:
        raise ValueError(f"contacts_per_month must be a finite number >= 0, got {contacts_per_month!r}")
    vpc = readout.get("value_per_contact_fen") or {}
    ci = vpc.get("ci")
    if readout["outcome"] != "PASS" or not ci:
        return {"value_per_month": 0.0, "currency": a.currency, "counted": False,
                "reason": f"readout {readout['outcome']}: no decision value is counted without a PASSing readout"}
    lcb = float(ci[0])
    if not math.isfinite(lcb) or lcb <= 0:
        raise ValueError(f"a PASSing value readout must have a positive lower bound, got {lcb}")
    # readouts are in fen (CNY); the USD profile converts with the cn profile's FX assumption
    per_contact = lcb / FEN_PER_CNY / (Assumptions().fx_usd_to_local if a.currency == "USD" else 1.0)
    return {"value_per_month": per_contact * contacts_per_month, "currency": a.currency, "counted": True,
            "lcb95_fen_per_contact": lcb, "contacts_per_month": float(contacts_per_month),
            "basis": "readout value-per-contact LCB95 x contacts per month at scale (projection)"}


def portfolio_estimate(a: Assumptions | None = None, measured: dict | None = None, *,
                       readout: dict | None = None, contacts_per_month: float | None = None) -> pd.DataFrame:
    """Monthly gross value, costs, net value and payback per use case, plus a TOTAL row.

    ``measured`` overrides assumptions with benchmark / production measurements. Top-level
    keys from ``MEASURED_KEYS`` apply to every use case; a key equal to a use-case name maps to
    overrides for that use case only (these win). ``*_usd`` keys are converted with
    ``fx_usd_to_local``. Unknown keys raise: a misspelt measurement must not silently leave an
    assumption in place. The ``measured_fields`` column records which inputs were measured;
    everything else is an assumption.

    ``readout`` (+ ``contacts_per_month``) sets the decision use case's revenue side from a
    randomised readout via ``decision_value`` (LCB95, PASS only); it cannot be combined with a
    measured ``experiment_value_per_month`` for that use case.
    """
    a = a or Assumptions()
    a.validate()
    measured = dict(measured or {})
    if readout is not None:
        if contacts_per_month is None:
            raise ValueError("readout needs contacts_per_month (contacts at scale per month)")
        if DECISION_USE_CASE not in {u.name for u in a.use_cases}:
            raise ValueError(f"no {DECISION_USE_CASE} use case to credit the readout to")
        spec = dict(measured.get(DECISION_USE_CASE) or {})
        if "experiment_value_per_month" in spec or "experiment_value_per_month" in measured:
            raise ValueError("give the decision value as a readout or as experiment_value_per_month, not both")
        dv = decision_value(readout, contacts_per_month, a)
        spec["experiment_value_per_month"] = dv["value_per_month"]
        measured[DECISION_USE_CASE] = spec
    elif contacts_per_month is not None:
        raise ValueError("contacts_per_month is only used with readout")
    names = {u.name for u in a.use_cases}
    unknown = set(measured) - names - set(MEASURED_KEYS)
    if unknown:
        raise ValueError(f"unknown measured keys {sorted(unknown)}; use-case names: {sorted(names)}, "
                         f"fields: {MEASURED_KEYS}")
    rate = a.loaded_cost_per_hour
    ucs = [_apply_measured(a, u, measured) for u in a.use_cases]
    volume = [u.tasks_per_month * u.adoption * u.baseline_minutes_per_task for u, _ in ucs]
    total_volume = sum(volume)
    rows = []
    for (u, notes), vol in zip(ucs, volume):
        handled = u.tasks_per_month * u.adoption
        hours_saved = handled * (1 - u.flag_rate) * u.minutes_saved_per_task / 60
        gross_labor = hours_saved * rate
        gross = gross_labor + u.experiment_value_per_month
        llm = handled * u.llm_cost_per_task
        review = handled * u.review_minutes_per_task / 60 * rate
        share = vol / total_volume if total_volume > 0 else 1 / len(ucs)
        platform = a.platform_cost_per_month * share
        error = handled * u.silent_failure_rate * u.error_cost_per_silent_failure
        total = llm + review + platform + error
        rows.append({"use_case": u.name, "currency": a.currency, "loaded_cost_per_hour": rate,
                     "handled_per_month": handled, "hours_saved_per_month": hours_saved,
                     "gross_labor_value": gross_labor, "experiment_value": u.experiment_value_per_month,
                     "gross_value": gross, "llm_cost": llm, "platform_cost": platform,
                     "review_cost": review, "error_cost": error, "total_cost": total,
                     "net_value": gross - total, "setup_cost": u.setup_cost,
                     "silent_failure_rate": u.silent_failure_rate, "llm_cost_per_task": u.llm_cost_per_task,
                     "measured_fields": ",".join(notes)})
    df = pd.DataFrame(rows)
    money = ["hours_saved_per_month", "handled_per_month", "gross_labor_value", "experiment_value",
             "gross_value", "llm_cost", "platform_cost", "review_cost", "error_cost", "total_cost",
             "net_value", "setup_cost"]
    tot = {c: float(df[c].sum()) for c in money}
    tot.update({"use_case": "TOTAL", "currency": a.currency, "loaded_cost_per_hour": rate,
                "silent_failure_rate": (float((df["handled_per_month"] * df["silent_failure_rate"]).sum()
                                              / tot["handled_per_month"]) if tot["handled_per_month"] else 0.0),
                "llm_cost_per_task": (tot["llm_cost"] / tot["handled_per_month"]
                                      if tot["handled_per_month"] else 0.0),
                "measured_fields": ",".join(sorted({n for _, ns in ucs for n in ns}))})
    df = pd.concat([df, pd.DataFrame([tot])], ignore_index=True)
    df["pays_back"] = df["net_value"] > 0
    df["payback_months"] = [s / n if n > 0 else math.nan for s, n in zip(df["setup_cost"], df["net_value"])]
    return df


def assumptions_table(a: Assumptions | None = None) -> pd.DataFrame:
    """Every input with its value and provenance, for printing next to the estimate."""
    a = a or Assumptions()
    rows = [{"name": k, "value": getattr(a, k), "source": a.sources.get(k, "ASSUMPTION")}
            for k in ("annual_wage", "loaded_multiplier", "working_hours_per_year",
                      "fx_usd_to_local", "platform_cost_per_month")]
    rows.append({"name": "loaded_cost_per_hour", "value": a.loaded_cost_per_hour,
                 "source": "derived: annual_wage * loaded_multiplier / working_hours_per_year"})
    for u in a.use_cases:
        for k, v in asdict(u).items():
            if k not in ("name", "description"):
                rows.append({"name": f"{u.name}.{k}", "value": v, "source": a.sources.get("use_cases", "ASSUMPTION")})
    return pd.DataFrame(rows).assign(currency=a.currency, profile=a.profile)
