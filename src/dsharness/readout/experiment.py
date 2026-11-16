"""Experiment readout gate: is a readout of a randomised test supported by the test's own per-unit rows?

An analyst replies in words ("the men's e-mail raised spend per customer by $0.77 [0.49, 1.05]") and gives one
structured claim per number. The gate re-reads the rows behind every claim and runs nine checks. Programs decide;
no model reads the words. A FAIL rejects the readout, an INCONCLUSIVE holds it, and only all-PASS (or NOT_RUN) is
ALLOW.

Scope: the gate certifies the numbers and the structure the analyst declares in the claims. It does not read what
the prose says a number means. A correct mean difference described as what "a typical customer" gains, or correct
spend figures offered as proof of profit, pass (tests/test_readout_experiment.py, known limits).

| check | FAIL (reject) | INCONCLUSIVE (hold) |
|---|---|---|
| exp.data | | the owner's data checks on the test table fail (sample ratio, balance, outcome logic) |
| exp.claims | | a claim names an unknown column, group, estimand or role |
| exp.numbers_covered | | a number in the reply that no claim (estimate, interval, scale, horizon), the request or the design accounts for |
| exp.recompute | a claim's estimate, or a mean's interval, differs from the rows | a result whose estimand the gate cannot recompute; a ratio's interval far from both delta-method intervals |
| exp.estimand | a between-group comparison on a population filtered by an outcome (post-assignment), or group means of different groups there given as results, unless marked as a caveat | |
| exp.contrast | on a comparison: "higher"/"lower" against the sign of the estimate from the rows | "higher"/"lower" where the interval includes zero (one for anyway_share), or "none" where it excludes it |
| exp.prespecified | | a subgroup result outside the owner's analysis plan, not marked exploratory with the number of segments searched |
| exp.horizon | an effect beyond the outcome window given as a result | a projection without its assumption |
| exp.pooled | | a pooled "any treatment" result, with no per-treatment result on that outcome, where the treatments differ on it (among everyone assigned or in the claim's population) |

A claim is a dict:

    {"id": "c1", "outcome": "spend", "estimand": "mean_difference", "groups": ["mens", "none"],
     "population": [{"column": "zip_code", "op": "=", "value": "Rural"}],   # [] or absent: everyone assigned
     "estimate": 0.77, "low": 0.49, "high": 1.05, "role": "result", "direction": "higher",
     "searched": null, "scale": null, "horizon_days": null, "assumption": null}

Estimands: mean_difference (a - b per unit assigned), relative_lift (mean a / mean b - 1), group_mean (a),
anyway_share (mean b / mean a: the share of a's outcome that b's level says would have happened anyway), effect_gap
(the a - b effect in `population` minus that in `population2`), median_difference, other (not recomputed). A group
key "any" pools every treatment group. `scale` multiplies estimate and interval (a projection to N units).
Roles: result, exploratory, projection, context (a descriptive number), caveat (a number given to say what is not
the answer, e.g. a per-buyer difference).
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from pathlib import Path
import re
import subprocess

import duckdb
import yaml

ESTIMANDS = ("mean_difference", "relative_lift", "group_mean", "anyway_share", "effect_gap", "median_difference",
             "other")
ROLES = ("result", "exploratory", "projection", "context", "caveat")
DIRECTIONS = ("higher", "lower", "none", "unclear", None)
OPS = {"=": "=", "==": "=", "!=": "<>", "<": "<", "<=": "<=", ">": ">", ">=": ">=", "in": "IN", "between": "BETWEEN"}
COMPARISONS = ("mean_difference", "relative_lift", "anyway_share", "effect_gap")
EST_REL, CI_WIDTH = 0.005, 0.10      # tolerances: estimate 0.5% relative; interval ends 10% of the reference width
Z = 1.959964


@dataclass(frozen=True)
class Design:
    """The owner's description of the test: where the rows are, the groups, which columns are outcomes."""
    db: Path
    table: str
    assignment: str
    groups: dict[str, str]          # key -> value in the assignment column
    control: str                    # key of the control group
    outcomes: tuple[str, ...]       # measured after assignment
    pre_treatment: tuple[str, ...]  # measured before assignment
    window_days: int
    prespecified: dict[str, dict[str, list[dict]]] = field(default_factory=dict)  # modifier -> label -> conditions
    facts: tuple[float, ...] = ()   # numbers the design itself states (units, group sizes, window)
    data_check: tuple[str, ...] = ()  # argv of the owner's data checks (exit 0 = pass)

    @classmethod
    def load(cls, path: Path) -> "Design":
        d = yaml.safe_load(Path(path).read_text())
        return cls(db=Path(d["db"]), table=d["table"], assignment=d["assignment"], groups=d["groups"],
                   control=d["control"], outcomes=tuple(d["outcomes"]), pre_treatment=tuple(d["pre_treatment"]),
                   window_days=int(d["window_days"]), prespecified=d.get("prespecified", {}),
                   facts=tuple(d.get("facts", ())), data_check=tuple(d.get("data_check", ())))

    @property
    def treatments(self) -> list[str]:
        return [g for g in self.groups if g != self.control]


class ClaimError(ValueError):
    pass


@dataclass
class Check:
    check_id: str
    outcome: str                    # PASS | FAIL | INCONCLUSIVE | NOT_RUN
    reason: str = ""
    claims: list[str] = field(default_factory=list)


def connect(design: Design) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(design.db), read_only=True)
    con.execute("PRAGMA threads=1")
    con.execute("SET enable_external_access = false")
    return con


# ---- reading the rows -------------------------------------------------------------------------------------------

def where(design: Design, conds: list[dict] | None) -> tuple[str, list, set[str]]:
    """SQL condition, parameters and columns of a population; columns must be the design's."""
    known = set(design.pre_treatment) | set(design.outcomes)
    parts, params, cols = [], [], set()
    for c in conds or []:
        col, op = c.get("column"), c.get("op", "=")
        if col not in known:
            raise ClaimError(f"unknown column {col!r} (pre-assignment: {', '.join(design.pre_treatment)}; "
                             f"outcomes: {', '.join(design.outcomes)})")
        if op not in OPS:
            raise ClaimError(f"unknown operator {op!r} (use one of {', '.join(OPS)})")
        cols.add(col)
        v = c.get("value")
        if op == "in":
            if not isinstance(v, list) or not v:
                raise ClaimError(f"'in' needs a non-empty list for {col}")
            parts.append(f'"{col}" IN ({", ".join("?" * len(v))})')
            params += v
        elif op == "between":
            if not isinstance(v, list) or len(v) != 2:
                raise ClaimError(f"'between' needs [low, high] for {col}")
            parts.append(f'"{col}" BETWEEN ? AND ?')
            params += v
        else:
            parts.append(f'"{col}" {OPS[op]} ?')
            params.append(v)
    return (" AND ".join(parts) or "TRUE"), params, cols


def _group_sql(design: Design, g: str) -> tuple[str, list]:
    if g == "any":
        vals = [design.groups[t] for t in design.treatments]
        return f'"{design.assignment}" IN ({", ".join("?" * len(vals))})', vals
    if g not in design.groups:
        raise ClaimError(f"unknown group {g!r} (groups: {', '.join(design.groups)}, or 'any' for every treatment)")
    return f'"{design.assignment}" = ?', [design.groups[g]]


def stats(con, design: Design, outcome: str, g: str, conds: list[dict] | None) -> tuple[int, float, float]:
    if outcome not in design.outcomes:
        raise ClaimError(f"unknown outcome {outcome!r} (outcomes: {', '.join(design.outcomes)})")
    gs, gp = _group_sql(design, g)
    w, wp, _ = where(design, conds)
    n, m, v = con.execute(f'SELECT count(*), avg("{outcome}"), var_samp("{outcome}") FROM "{design.table}" '
                          f"WHERE {gs} AND ({w})", gp + wp).fetchone()
    if not n or n < 2:
        raise ClaimError(f"group {g} has {n or 0} rows in that population")
    return n, float(m), float(v or 0.0)


def _diff(con, design, outcome, a, b, conds):
    (na, ma, va), (nb, mb, vb) = stats(con, design, outcome, a, conds), stats(con, design, outcome, b, conds)
    return ma - mb, math.sqrt(va / na + vb / nb), (na, ma, va), (nb, mb, vb)


def recompute(con, design: Design, c: dict) -> dict | None:
    """The claim's estimand from the rows: {estimate, low, high} (low/high None when it has no interval); ratios also
    carry low_linear/high_linear, the delta-method interval on the ratio rather than its log."""
    e, g, o, pop = c.get("estimand"), c.get("groups") or [], c.get("outcome"), c.get("population")
    need = 1 if e == "group_mean" else 2
    if e != "other" and len(g) != need:
        raise ClaimError(f"{e} needs {need} group(s) in 'groups', got {g}")
    if e == "mean_difference":
        est, se, _, _ = _diff(con, design, o, g[0], g[1], pop)
        r = {"estimate": est, "low": est - Z * se, "high": est + Z * se}
    elif e == "group_mean":
        n, m, v = stats(con, design, o, g[0], pop)
        r = {"estimate": m, "low": m - Z * math.sqrt(v / n), "high": m + Z * math.sqrt(v / n)}
    elif e in ("relative_lift", "anyway_share"):
        _, _, (na, ma, va), (nb, mb, vb) = _diff(con, design, o, g[0], g[1], pop)
        if ma <= 0 or mb <= 0:
            raise ClaimError(f"{e} needs positive means, got {ma:.4g} and {mb:.4g}")
        s = math.sqrt(va / (na * ma ** 2) + vb / (nb * mb ** 2))        # se of the log ratio (delta method)
        ratio = ma / mb if e == "relative_lift" else mb / ma
        lo, hi = ratio * math.exp(-Z * s), ratio * math.exp(Z * s)
        llo, lhi = ratio * (1 - Z * s), ratio * (1 + Z * s)                # the delta method on the ratio itself
        k = 1 if e == "relative_lift" else 0
        r = {"estimate": ratio - k, "low": lo - k, "high": hi - k, "low_linear": llo - k, "high_linear": lhi - k}
    elif e == "effect_gap":
        if not c.get("population2"):
            raise ClaimError("effect_gap needs 'population2'")
        e1, s1, _, _ = _diff(con, design, o, g[0], g[1], pop)
        e2, s2, _, _ = _diff(con, design, o, g[0], g[1], c["population2"])
        est, se = e1 - e2, math.sqrt(s1 ** 2 + s2 ** 2)
        r = {"estimate": est, "low": est - Z * se, "high": est + Z * se}
    elif e == "median_difference":
        meds = []
        for k in g:
            gs, gp = _group_sql(design, k)
            w, wp, _ = where(design, pop)
            meds.append(float(con.execute(f'SELECT median("{o}") FROM "{design.table}" WHERE {gs} AND ({w})',
                                          gp + wp).fetchone()[0]))
        r = {"estimate": meds[0] - meds[1], "low": None, "high": None}
    elif e == "other":
        return None
    else:
        raise ClaimError(f"unknown estimand {e!r} (use one of {', '.join(ESTIMANDS)})")
    k = c.get("scale")
    if k is not None:
        if not isinstance(k, (int, float)) or k <= 0:
            raise ClaimError("'scale' must be a positive number")
        r = {key: (v * k if v is not None else None) for key, v in r.items()}
    return r


# ---- tolerances and the numbers in the reply --------------------------------------------------------------------

def half_unit(x: float) -> float:
    """Half the last stated digit: 0.8 -> 0.05, 192000 -> 500, 190000 -> 5000, 3 -> 0.5."""
    s = repr(float(x)) if not float(x).is_integer() else str(int(x))
    if "e" in s:
        return abs(x) * 0.005
    if "." in s:
        return 0.5 * 10 ** -len(s.split(".")[1])
    t = len(s) - len(s.rstrip("0")) if s.strip("-0") else 0
    return 0.5 * 10 ** t


def close(claimed: float, ref: float, tol: float = 0.0) -> bool:
    return abs(claimed - ref) <= max(EST_REL * abs(ref), half_unit(claimed), tol) + 1e-12


NUM = re.compile(r"(?<![\w.])([-+−]?)(\$?)(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s*(%|pts?\b|points?\b|pp\b|k\b|K\b|M\b|million\b)?")


def numbers_in(text: str) -> list[tuple[str, float, float]]:
    """(as written, value, half unit) for every number in the text, k/M expanded; '%' and points kept as written."""
    out = []
    for m in NUM.finditer(text or ""):
        sign, dollar, whole, frac, unit = m.groups()
        v = float(whole.replace(",", "") + (frac or ""))
        hu = 0.5 * 10 ** -len(frac[1:]) if frac else half_unit(float(whole.replace(",", "")))
        mult = {"k": 1e3, "K": 1e3, "M": 1e6, "million": 1e6}.get(unit or "", 1)
        if not dollar and not unit and not frac and v <= 12:
            continue                # list markers, small counts, "two weeks" written as 2
        if not dollar and not unit and not frac and 1990 <= v <= 2035:
            continue                # years
        out.append((m.group(0).strip(), (-v if sign in "-−" and sign else v) * mult, hu * mult))
    return out


def covered(v: float, hu: float, values: list[float], signed: bool = False) -> bool:
    """A number written with a sign must match a value of that sign; an unsigned one ("fell by $0.30") either."""
    for ref in values:
        for s in (1, 100, 1000):
            gap = abs(v - ref * s) if signed else abs(abs(v) - abs(ref * s))
            if gap <= max(hu, EST_REL * abs(ref * s)) + 1e-9:
                return True
    return False


# ---- the checks -------------------------------------------------------------------------------------------------

_DATA_CACHE: dict[tuple, Check] = {}


def check_data(design: Design) -> Check:
    if not design.data_check:
        return Check("exp.data", "NOT_RUN", "no data checks declared")
    if design.data_check not in _DATA_CACHE:
        p = subprocess.run(list(design.data_check), capture_output=True, text=True, timeout=300)
        tail = (p.stdout + p.stderr).strip().splitlines()[-6:]
        _DATA_CACHE[design.data_check] = (Check("exp.data", "PASS", "; ".join(tail)[-400:]) if p.returncode == 0 else
                                          Check("exp.data", "INCONCLUSIVE", "the owner's data checks fail: "
                                                + "; ".join(tail)[-600:]))
    return _DATA_CACHE[design.data_check]


def run_checks(design: Design, answer: str, claims: list[dict], request: str = "") -> tuple[list[Check], dict]:
    con = connect(design)
    checks = [check_data(design)]
    bad, ref = [], {}
    for i, c in enumerate(claims):
        cid = str(c.get("id") or f"#{i + 1}")
        c["id"] = cid
        try:
            if c.get("role") not in ROLES:
                raise ClaimError(f"role must be one of {', '.join(ROLES)}")
            if c.get("direction") not in DIRECTIONS:
                raise ClaimError("direction must be higher, lower, none, unclear or null")
            if not isinstance(c.get("estimate"), (int, float)):
                raise ClaimError("estimate must be a number")
            for f_ in ("population", "population2"):
                where(design, c.get(f_))
            ref[cid] = recompute(con, design, c)
        except ClaimError as exc:
            bad.append(f"{cid}: {exc}")
    con.close()
    checks.append(Check("exp.claims", "INCONCLUSIVE" if bad else "PASS", "; ".join(bad)[:900],
                        [b.split(":")[0] for b in bad]))
    ok = [c for c in claims if c["id"] in ref]

    # every number in the reply is a claim, a number of the request, or a fact of the design
    vals = [x for c in claims for x in (c.get("estimate"), c.get("low"), c.get("high"), c.get("scale"),
                                        c.get("horizon_days")) if isinstance(x, (int, float))]
    vals += [v for _, v, _ in numbers_in(request)] + list(design.facts)
    loose = [w for w, v, hu in numbers_in(answer) if not covered(v, hu, vals, signed=w[:1] in "+-−")]
    checks.append(Check("exp.numbers_covered", "INCONCLUSIVE" if loose else "PASS",
                        ("numbers in the reply with no claim: " + ", ".join(loose[:10])
                         + ("; add a claim for each (role context for a descriptive number)" if loose else ""))))

    wrong, cannot, method = [], [], []
    for c in ok:
        r = ref[c["id"]]
        if r is None:
            if c["role"] in ("result", "exploratory", "projection"):
                cannot.append(f"{c['id']}: cannot recompute (estimand other); use a recomputable estimand, or role "
                              "context/caveat")
            continue
        width = (r["high"] - r["low"]) if r["low"] is not None else 0.0
        msgs, far = [], []
        if not close(c["estimate"], r["estimate"]):
            msgs.append(f"estimate {c['estimate']} vs {r['estimate']:.4g} from the rows")
        for end in ("low", "high"):
            if not isinstance(c.get(end), (int, float)) or r[end] is None or close(c[end], r[end], CI_WIDTH * width):
                continue
            if "low_linear" in r:       # a ratio: standard methods differ more than for a mean; either delta method
                lw = r["high_linear"] - r["low_linear"]
                if not close(c[end], r[end + "_linear"], CI_WIDTH * lw):
                    far.append(f"{end} {c[end]} vs {r[end]:.4g} (log delta) and {r[end + '_linear']:.4g} (delta)")
            else:
                msgs.append(f"{end} {c[end]} vs {r[end]:.4g}")
        if msgs:
            wrong.append(f"{c['id']}: " + ", ".join(msgs))
        elif far:
            method.append(f"{c['id']}: interval " + ", ".join(far) + " from the rows")
    soft = cannot + method
    checks.append(Check("exp.recompute", "FAIL" if wrong else "INCONCLUSIVE" if soft else "PASS",
                        "; ".join(wrong or soft)[:900], [w.split(":")[0] for w in wrong or soft]))

    def selected(c):
        return bool((where(design, c.get("population"))[2] | where(design, c.get("population2"))[2])
                    & set(design.outcomes))
    sel = [c["id"] for c in ok if c.get("estimand") in COMPARISONS and c["role"] != "caveat" and selected(c)]
    levels: dict[tuple, list[dict]] = {}          # group means side by side among outcome-selected units
    for c in ok:
        if c.get("estimand") == "group_mean" and c["role"] in ("result", "exploratory") and selected(c):
            levels.setdefault((c.get("outcome"), str(canon(c.get("population")))), []).append(c)
    sel += [c["id"] for cs in levels.values() if len({tuple(c.get("groups") or []) for c in cs}) > 1 for c in cs]
    checks.append(Check("exp.estimand", "FAIL" if sel else "PASS",
                        f"{', '.join(sel)}: a comparison among units selected by an outcome (e.g. buyers) is not the "
                        "effect of assignment; give it per unit assigned, or mark it role caveat (role context for "
                        "a descriptive group mean)" if sel else "", sel))

    contra, unsure = [], []
    for c in ok:
        r, d = ref[c["id"]], c.get("direction")
        if r is None or r["low"] is None or d in (None, "unclear") or c["estimand"] not in COMPARISONS:
            continue            # a direction on a level (group_mean) compares it with nothing the claim names
        null = 1.0 if c["estimand"] == "anyway_share" else 0.0
        iv = f"[{r['low']:.4g}, {r['high']:.4g}]"
        if d in ("higher", "lower") and not (r["estimate"] > null if d == "higher" else r["estimate"] < null):
            contra.append(f"{c['id']}: '{d}' but the estimate from the rows is {r['estimate']:.4g} (vs {null:g})")
        elif d in ("higher", "lower") and r["low"] <= null <= r["high"]:
            unsure.append(f"{c['id']}: '{d}' and the interval {iv} includes {null:g} ('{d}' = the reply says the "
                          "data show a difference; 'unclear' = a point estimate the interval does not separate from "
                          f"{null:g})")
        elif d == "none" and not r["low"] <= null <= r["high"]:
            unsure.append(f"{c['id']}: 'none' and the interval {iv} excludes {null:g} (the plan sets no "
                          "equivalence margin)")
    checks.append(Check("exp.contrast", "FAIL" if contra else "INCONCLUSIVE" if unsure else "PASS",
                        "; ".join(contra or unsure)[:900], [x.split(":")[0] for x in contra or unsure]))

    plan = [canon(conds) for cuts in design.prespecified.values() for conds in cuts.values()]
    unplanned = []
    for c in ok:
        pop = canon(c.get("population"))
        if c["role"] == "result" and pop and pop not in plan:
            unplanned.append(f"{c['id']}: subgroup result outside the analysis plan; mark it role exploratory and "
                             "give 'searched' (how many segments you compared)")
        elif c["role"] == "exploratory" and not (isinstance(c.get("searched"), int) and c["searched"] >= 1):
            unplanned.append(f"{c['id']}: exploratory claim without 'searched'")
    checks.append(Check("exp.prespecified", "INCONCLUSIVE" if unplanned else "PASS", "; ".join(unplanned)[:900],
                        [x.split(":")[0] for x in unplanned]))

    far = [c["id"] for c in ok if (c.get("horizon_days") or design.window_days) > design.window_days
           and c["role"] in ("result", "exploratory", "context")]
    bare = [c["id"] for c in ok if c["role"] == "projection" and not (c.get("assumption") or "").strip()]
    checks.append(Check("exp.horizon", "FAIL" if far else "INCONCLUSIVE" if bare else "PASS",
                        (f"{', '.join(far)}: beyond the {design.window_days}-day outcome window, as a result; give it "
                         "as a projection with its assumption" if far else
                         f"{', '.join(bare)}: a projection needs its 'assumption'" if bare else ""), far or bare))

    pooled = []
    if len(design.treatments) == 2:
        con = connect(design)
        for c in ok:
            if c["role"] != "result" or "any" not in (c.get("groups") or []) or c["estimand"] not in COMPARISONS:
                continue
            per = [x for x in ok if x["role"] == "result" and x.get("outcome") == c.get("outcome")
                   and "any" not in (x.get("groups") or []) and set(x.get("groups") or []) & set(design.treatments)]
            if per:
                continue
            for pop, where_ in ((None, "among everyone assigned"), (c.get("population"), "in this population")):
                est, se, _, _ = _diff(con, design, c["outcome"], *design.treatments, pop)
                if abs(est) > Z * se:
                    pooled.append(f"{c['id']}: the treatments differ on {c['outcome']} {where_} ({est:+.4g} ± "
                                  f"{Z * se:.3g}) and no claim gives each treatment's result")
                    break
        con.close()
    checks.append(Check("exp.pooled", "INCONCLUSIVE" if pooled else "PASS", "; ".join(pooled)[:900],
                        [x.split(":")[0] for x in pooled]))
    return checks, ref


def canon(conds: list[dict] | None) -> list[tuple]:
    """A population in a comparable form: 1, 1.0 and "1" are the same value; "==" is "="."""
    def val(v):
        if isinstance(v, list):
            return tuple(val(x) for x in v)
        try:
            return f"{float(v):g}"
        except (TypeError, ValueError):
            return str(v)
    return sorted((p["column"], OPS.get(p.get("op", "="), p.get("op")), val(p.get("value"))) for p in conds or [])


def decide(checks: list[Check]) -> str:
    if any(c.outcome == "FAIL" for c in checks):
        return "REJECT"
    if any(c.outcome == "INCONCLUSIVE" for c in checks):
        return "HOLD"
    return "ALLOW"


def check_readout(design: Design, answer: str, claims: list[dict], request: str = "") -> dict:
    if not isinstance(claims, list) or not all(isinstance(c, dict) for c in claims):
        return {"gate": "HOLD", "reasons": ["claims must be a list (one structured claim per number in the reply)"],
                "checks": {}}
    checks, ref = run_checks(design, answer, [dict(c) for c in claims], request)
    gate = decide(checks)
    return {"gate": gate,
            "reasons": [f"{c.check_id}: {c.reason}" for c in checks if c.outcome in ("FAIL", "INCONCLUSIVE")],
            "checks": {c.check_id: {"outcome": c.outcome, **({"reason": c.reason} if c.reason else {})}
                       for c in checks},
            "recomputed": {k: ({kk: (round(vv, 6) if vv is not None else None) for kk, vv in v.items()}
                               if v else None) for k, v in ref.items()}}
