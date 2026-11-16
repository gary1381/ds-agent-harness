"""E3c v2 analysis (docs/experiments/E3c/PREREGISTRATION.md): outcomes, metrics and the decision rules.

Every planned attempt (run.json) gets one outcome from its record and its final label (labels.json, label_v2.py):

  supported-delivery  delivered, and the reply is supported
  silent-error        delivered, and the reply is not supported (R1's numerator)
  caught              not delivered (arm H: the gate never allowed it), and the reply is not supported
  false-hold          not delivered, and the reply is supported (R2's numerator)
  no-answer           no answer.md (the run failed after its retry, or the agent wrote none)
  unlabelled          a reply without a label yet
  missing             a planned slot with no record

The final analysis refuses to run while any slot is missing or any reply unlabelled (--interim allows it, and says so).
Denominators: R1 = planned attempts; R2 = supported replies; pass^3 = (agent, request) cells, an incomplete cell
counting as not passed. Intervals: cluster bootstrap over requests (2,000 resamples, seed 20261107), percentiles by
linear interpolation (Hyndman-Fan type 7), with the number of resamples where the metric is defined.

    .venv/bin/python experiments/E3c/analyze_v2.py experiments/E3c/runs/v2 [--out docs/experiments/E3c] [--interim]
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import random
import statistics
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from label_v2 import key, latest  # noqa: E402

BOOT, SEED = 2000, 20261107
R2_MAX = 0.15
OUTCOMES = ("supported-delivery", "silent-error", "caught", "false-hold", "no-answer", "unlabelled", "missing")
# codes the gate has a check for, if the claims are declared honestly (PREREGISTRATION.md, "What the gate can see")
GATE_VISIBLE = {"E-calc", "E-select", "E-fish", "E-pool", "E-extrapolate", "E-overclaim"}
SETS = ("heldout", "exposed", "seen")


def outcome(r: dict, lab: dict) -> str:
    if r.get("missing"):
        return "missing"
    if not r.get("answer"):
        return "no-answer"
    if "supported" not in lab:
        return "unlabelled"
    if r["delivered"]:
        return "supported-delivery" if lab["supported"] else "silent-error"
    return "false-hold" if lab["supported"] else "caught"


def quantile(xs: list[float], q: float) -> float:
    """Type 7 (numpy's default): linear interpolation between the order statistics."""
    xs = sorted(xs)
    h = (len(xs) - 1) * q
    lo = int(h)
    return xs[lo] + (h - lo) * (xs[min(lo + 1, len(xs) - 1)] - xs[lo])


def rates(rs: list[dict]) -> dict:
    n = len(rs)
    c = Counter(r["outcome"] for r in rs)
    supported = c["supported-delivery"] + c["false-hold"]
    vis = sum(r["outcome"] == "silent-error" and r["visible"] for r in rs)
    return {"n": n,
            "R1": c["silent-error"] / n if n else None,
            "R1_visible": vis / n if n else None,
            "R1_blind": (c["silent-error"] - vis) / n if n else None,
            "R1_noanswer_as_error": (c["silent-error"] + c["no-answer"]) / n if n else None,
            "R1_replies_only": c["silent-error"] / (n - c["no-answer"]) if n - c["no-answer"] else None,
            "R2": c["false-hold"] / supported if supported else None,
            "delivered_supported": c["supported-delivery"] / n if n else None,
            "caught": c["caught"] / n if n else None,
            "supported_any": supported / n if n else None}


def pass3(rs: list[dict], reps: int) -> dict:
    """Share of (agent, request) cells whose every planned repetition is a supported delivery."""
    by: dict = {}
    for r in rs:
        by.setdefault((r["agent"], r["task"]), []).append(r["outcome"] == "supported-delivery")
    full = [len(v) == reps and all(v) for v in by.values()]
    return {"pass3": sum(full) / len(full) if full else None, "cells": len(full),
            "incomplete_cells": sum(len(v) != reps for v in by.values())}


def by_task(rs: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in rs:
        out.setdefault(r["task"], []).append(r)
    return out


def boot_ci(rs: list[dict], rng: random.Random,
            keys=("R1", "R1_visible", "R1_blind", "R1_noanswer_as_error", "R2", "delivered_supported", "caught")):
    groups = by_task(rs)
    ts = sorted(groups)
    point, boots = rates(rs), {k: [] for k in keys}
    for _ in range(BOOT):
        b = rates([r for t in (rng.choice(ts) for _ in ts) for r in groups[t]])
        for k in keys:
            if b[k] is not None:
                boots[k].append(b[k])
    out = {"n": point["n"], "requests": len(ts), "R1_replies_only": point["R1_replies_only"]}
    for k in keys:
        xs = boots[k]
        out[k] = point[k]
        out[k + "_ci"] = [quantile(xs, 0.025), quantile(xs, 0.975)] if xs else None
        out[k + "_draws"] = len(xs)
    return out


def paired_diff(rs: list[dict], metric: str, rng: random.Random) -> dict:
    groups = by_task(rs)
    ts = sorted(groups)

    def diff(sample):
        h = rates([r for t in sample for r in groups[t] if r["arm"] == "H"])[metric]
        n = rates([r for t in sample for r in groups[t] if r["arm"] == "N"])[metric]
        return None if h is None or n is None else h - n
    xs = [d for d in (diff([rng.choice(ts) for _ in ts]) for _ in range(BOOT)) if d is not None]
    return {"diff": diff(ts), "ci": [quantile(xs, 0.025), quantile(xs, 0.975)] if xs else None, "draws": len(xs)}


def cost(rs: list[dict]) -> dict:
    """Cost per supported delivery: dollars for Claude, tokens for Codex; median minutes per attempt."""
    def good(xs):
        return sum(r["outcome"] == "supported-delivery" for r in xs)
    cl, cx = [r for r in rs if r["agent"] == "claude"], [r for r in rs if r["agent"] == "codex"]
    usd = sum(r.get("cost_usd") or 0 for r in cl)
    tok = sum(((r.get("tokens") or {}).get("input_tokens", 0) + (r.get("tokens") or {}).get("output_tokens", 0))
              for r in cx)
    secs = [r["seconds"] for r in rs if r.get("seconds") is not None]
    return {"usd_per_supported": usd / good(cl) if good(cl) and usd else None,
            "tokens_per_supported": tok / good(cx) if good(cx) and tok else None,
            "median_min": statistics.median(secs) / 60 if secs else None}


def kappa(pairs: list[tuple[bool, bool]]) -> dict | None:
    if not pairs:
        return None
    n = len(pairs)
    po = sum(a == b for a, b in pairs) / n
    pa, pb = sum(a for a, _ in pairs) / n, sum(b for _, b in pairs) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return {"n": n, "agreement": po, "kappa": (po - pe) / (1 - pe) if pe < 1 else 1.0}


def agreement(rs: list[dict], labels: dict) -> dict | None:
    """Designer vs Haiku on `supported`, overall and by arm (both blind), and how many were adjudicated."""
    out = {}
    for name, xs in (("all", rs), ("N", [r for r in rs if r["arm"] == "N"]), ("H", [r for r in rs if r["arm"] == "H"])):
        pairs = [(labels[key(r)]["designer"]["supported"], labels[key(r)]["haiku"]["supported"]) for r in xs
                 if "designer" in labels.get(key(r), {})]
        out[name] = kappa(pairs)
    out["adjudicated"] = sum(bool(v.get("adjudicated")) for k, v in labels.items() if k != "_meta")
    return out if out["all"] else None


def planned(run: Path) -> tuple[list[dict], dict]:
    plan = json.loads((run / "run.json").read_text())
    have = {(r["agent"], r["task"], r["arm"], r["rep"]): r for r in latest(run)}
    rs = []
    for ag in plan["agents"]:
        for t in plan["tasks"]:
            for arm in plan["arms"]:
                for k in range(1, plan["reps"] + 1):
                    rs.append(have.get((ag, t, arm, k)) or {"agent": ag, "task": t, "arm": arm, "rep": k,
                                                            "set": plan["sets"][t], "missing": True,
                                                            "delivered": False, "answer": None})
    return rs, plan


def analyse(run: Path, interim: bool) -> dict:
    labels = json.loads((run / "labels.json").read_text()) if (run / "labels.json").exists() else {}
    rs, plan = planned(run)
    for r in rs:
        lab = labels.get(key(r), {})
        r["outcome"] = outcome(r, lab)
        r["visible"] = bool(set(lab.get("codes") or []) & GATE_VISIBLE)
    counts = Counter(r["outcome"] for r in rs)
    status = {"planned": len(rs), "recorded": len(rs) - counts["missing"], "missing": counts["missing"],
              "not_ok": sum(r.get("status") not in (None, "OK") for r in rs if not r.get("missing")),
              "no_answer": counts["no-answer"], "unlabelled": counts["unlabelled"], "interim": interim}
    if not interim and (counts["missing"] or counts["unlabelled"]):
        raise SystemExit(f"not final: {counts['missing']} planned slots missing, {counts['unlabelled']} replies "
                         "unlabelled (use --interim for a progress report)")
    rng = random.Random(SEED)
    res = {"status": status, "sets": {}, "rules": {}}
    for name in SETS:
        sub = [r for r in rs if r["set"] == name]
        if not sub:
            continue
        s = {"by_arm": {}, "by_agent_arm": {}, "by_task": {}, "codes": {}}
        for arm in ("N", "H"):
            xs = [r for r in sub if r["arm"] == arm]
            if not xs:
                continue
            s["by_arm"][arm] = {**boot_ci(xs, rng), **pass3(xs, plan["reps"]), **cost(xs)}
            for ag in ("claude", "codex"):
                ys = [r for r in xs if r["agent"] == ag]
                if ys:
                    s["by_agent_arm"][f"{ag} {arm}"] = {**rates(ys), **pass3(ys, plan["reps"]), **cost(ys)}
            s["codes"][arm] = {o: dict(Counter(c for r in xs if r["outcome"] == o
                                               for c in labels.get(key(r), {}).get("codes", [])))
                               for o in ("silent-error", "caught")}
        for t in sorted({r["task"] for r in sub}):
            for arm in ("N", "H"):
                xs = [r for r in sub if r["task"] == t and r["arm"] == arm]
                if xs:
                    s["by_task"][f"{t} {arm}"] = dict(Counter(r["outcome"] for r in xs))
        if len(s["by_arm"]) == 2:
            s["R1_diff"] = paired_diff(sub, "R1", rng)
            s["R1_visible_diff"] = paired_diff(sub, "R1_visible", rng)
        res["sets"][name] = s
    ho = res["sets"].get("heldout")
    if ho and len(ho["by_arm"]) == 2:
        n, h, d = ho["by_arm"]["N"], ho["by_arm"]["H"], ho["R1_diff"]
        r2_ok = h["R2"] is None or h["R2"] <= R2_MAX
        if d["ci"] and h["R1"] < n["R1"] and d["ci"][1] < 0 and r2_ok:
            verdict = "met"
        elif d["ci"] and d["ci"][0] > 0:
            verdict = "harness worse (the paired interval is above 0)"
        elif d["ci"] and d["ci"][1] < 0:
            verdict = "not met: fewer silent errors, but R2 above 15%"
        else:
            verdict = "inconclusive (the paired interval includes 0)"
        res["rules"] = {"R1": verdict, "R2 too strict (R2 > 15%)": bool(h["R2"] is not None and h["R2"] > R2_MAX)}
    res["outcomes"] = {key(r): r["outcome"] for r in rs}
    res["agreement"] = agreement([r for r in rs if r.get("answer")], labels)
    idx = run / "label_index.json"
    if idx.exists():
        gw = Counter(v["arm"] for v in json.loads(idx.read_text()).values() if v["gate_words"])
        res["replies_naming_the_gate"] = dict(gw)
    return res


def pct(x, ci=None) -> str:
    if x is None:
        return "—"
    return f"{100 * x:.1f}%" + (f" [{100 * ci[0]:.1f}, {100 * ci[1]:.1f}]" if ci else "")


def report(res: dict) -> str:
    st = res["status"]
    out = [f"# E3c v2 results{' (INTERIM)' if st['interim'] else ''}", "",
           f"Planned {st['planned']}, recorded {st['recorded']}, missing {st['missing']}, runs not OK after retry "
           f"{st['not_ok']}, no answer {st['no_answer']}, unlabelled {st['unlabelled']}.", ""]
    for name, s in res["sets"].items():
        out += [f"## {name} requests", "",
                "| arm | n | R1 silent error | gate-visible | gate-blind | R2 false hold | supported delivery | caught | "
                "pass^3 | $ per supported (Claude) | tokens per supported (Codex) | median min |",
                "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for arm, x in s["by_arm"].items():
            usd = "—" if x["usd_per_supported"] is None else f"${x['usd_per_supported']:.2f}"
            tok = "—" if x["tokens_per_supported"] is None else f"{x['tokens_per_supported'] / 1000:.0f}k"
            mm = "—" if x["median_min"] is None else f"{x['median_min']:.1f}"
            out.append(f"| {arm} | {x['n']} | {pct(x['R1'], x['R1_ci'])} | {pct(x['R1_visible'])} | "
                       f"{pct(x['R1_blind'])} | {pct(x['R2'], x['R2_ci'])} (draws {x['R2_draws']}) | "
                       f"{pct(x['delivered_supported'], x['delivered_supported_ci'])} | {pct(x['caught'])} | "
                       f"{pct(x['pass3'])} ({x['cells']} cells) | {usd} | {tok} | {mm} |")
        if s.get("R1_diff"):
            out += ["", f"R1(H) − R1(N), paired over requests: {pct(s['R1_diff']['diff'], s['R1_diff']['ci'])}; "
                        f"gate-visible errors only: {pct(s['R1_visible_diff']['diff'], s['R1_visible_diff']['ci'])}"]
        out += ["", "Sensitivity, R1 with no-answer counted as a silent error: " +
                "; ".join(f"{arm} {pct(x['R1_noanswer_as_error'], x['R1_noanswer_as_error_ci'])}"
                          for arm, x in s["by_arm"].items()) +
                ". R1 over replies only: " + "; ".join(f"{arm} {pct(x['R1_replies_only'])}"
                                                      for arm, x in s["by_arm"].items()), "",
                "Error codes: " + "; ".join(f"{arm} {json.dumps(c)}" for arm, c in s["codes"].items()), ""]
    if res["rules"]:
        out += ["## Decision rules (held-out requests)", "", "| rule | result |", "|---|---|"]
        out += [f"| {k} | {v if isinstance(v, str) else ('**yes**' if v else 'no')} |" for k, v in res["rules"].items()]
    ag = res.get("agreement")
    if ag:
        out += ["", "Blind labels, designer vs Haiku on `supported`: " +
                "; ".join(f"{k} {v['agreement']:.0%} of {v['n']}, kappa {v['kappa']:.2f}"
                          for k, v in ag.items() if isinstance(v, dict)) + f"; {ag['adjudicated']} adjudicated"]
    if "replies_naming_the_gate" in res:
        out += ["", f"Replies whose text names the gate (could unmask the arm): {res['replies_naming_the_gate']}"]
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--interim", action="store_true", help="allow missing slots and unlabelled replies")
    a = ap.parse_args()
    res = analyse(a.run, a.interim)
    out = a.out or a.run
    out.mkdir(parents=True, exist_ok=True)
    (out / "results_v2.json").write_text(json.dumps(res, indent=1, default=float))
    (out / "results_v2.md").write_text(report(res))
    print(report(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
