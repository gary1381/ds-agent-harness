"""E3b v2 analysis (docs/experiments/E3b/PREREGISTRATION.md): outcomes, metrics and the decision rules.

Every planned attempt (run.json) gets one outcome from its record, its final label (labels.json, label_v2.py) and its
program codes (truth/<agent>_<task>-<arm>-r<rep>.json, truth_v2.py --sealed). "Not supported" means the label says
false OR the truth script set any program code (E-gross, typed E-overclaim, E-calc on a typed past figure):

  supported-delivery  delivered, supported, no program error
  silent-error        delivered, and not supported or with a program error (R1's numerator)
  caught              not delivered, and not supported or with a program error
  false-hold          not delivered, supported, no program error (R2's numerator)
  no-answer           no answer.md (or an empty one)
  unlabelled          a reply without a label yet
  missing             a planned slot with no record

Silent errors split three ways (keys_v2.yaml, header): gate-visible (E-calc, label or program), backtest-targeted
(a program code and no gate-visible one), gate-blind (the rest; a label E-overclaim is semantic, so gate-blind).
The final analysis refuses to run while any slot is missing, any reply unlabelled, any reply without a sealed truth
file (--interim allows it, and says so). Denominators: R1 = planned attempts; R2 = supported replies without a program
error, arm H; pass^k = (agent, request) cells, an incomplete cell counting as not passed; C = stated ranges; T =
replies with a truth file. Intervals: cluster bootstrap over requests (2,000 resamples, seed 20261108), percentiles by
linear interpolation (Hyndman-Fan type 7), with the number of resamples where the metric is defined. The helpers are
copies of experiments/E3c/analyze_v2.py's, not imports, so E3c's frozen code and this one cannot drift together.

    .venv/bin/python experiments/E3b/analyze_v2.py experiments/E3b/runs/v2 [--out docs/experiments/E3b] [--interim]
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import random
import statistics

BOOT, SEED = 2000, 20261108
R2_MAX = 0.15
OUTCOMES = ("supported-delivery", "silent-error", "caught", "false-hold", "no-answer", "unlabelled", "missing")
GATE_VISIBLE = {"E-calc"}                         # label or program codes the gate can see (f.observed, typed past figure)
TARGETED = {"E-gross", "E-overclaim"}             # program codes the backtest aims to prevent (f.range, f.backtest)
SETS = ("heldout", "seen")
BOOT_KEYS = ("R1", "R1_visible", "R1_targeted", "R1_blind", "R1_noanswer_as_error", "R1_ungraded_as_error", "R2",
             "R2_ungraded_as_error", "delivered_supported", "caught")


def key(r: dict) -> str:
    return f"{r['agent']}/{r['task']}-{r['arm']}-r{r['rep']}"


def truth_name(r: dict) -> str:
    return f"{r['agent']}_{r['task']}-{r['arm']}-r{r['rep']}.json"


def latest(run: Path) -> list[dict]:
    keep = {}
    for line in (run / "attempts.jsonl").read_text().splitlines():
        r = json.loads(line)
        keep[(r["agent"], r["task"], r["arm"], r["rep"])] = r
    return sorted(keep.values(), key=lambda r: (r["task"], r["agent"], r["arm"], r["rep"]))


def has_answer(r: dict) -> bool:
    return bool((r.get("answer") or "").strip())


def outcome(r: dict, lab: dict, program: set, extra_bad: bool = False) -> str:
    if r.get("missing"):
        return "missing"
    if not has_answer(r):
        return "no-answer"
    if "supported" not in lab:
        return "unlabelled"
    bad = not lab["supported"] or bool(program) or extra_bad
    if r["delivered"]:
        return "silent-error" if bad else "supported-delivery"
    return "caught" if bad else "false-hold"


def error_class(lab: dict, program: set) -> str:
    if (set(lab.get("codes") or []) | program) & GATE_VISIBLE:
        return "visible"
    if program & TARGETED:
        return "targeted"
    return "blind"


def quantile(xs: list[float], q: float) -> float:
    """Type 7 (numpy's default): linear interpolation between the order statistics."""
    xs = sorted(xs)
    h = (len(xs) - 1) * q
    lo = int(h)
    return xs[lo] + (h - lo) * (xs[min(lo + 1, len(xs) - 1)] - xs[lo])


def _rates(rs: list[dict], field: str) -> dict:
    n = len(rs)
    c = Counter(r[field] for r in rs)
    clean = c["supported-delivery"] + c["false-hold"]
    return {"n": n, "R1": c["silent-error"] / n if n else None,
            "R2": c["false-hold"] / clean if clean else None, "counts": c, "clean": clean}


def rates(rs: list[dict]) -> dict:
    n = len(rs)
    p, ug = _rates(rs, "outcome"), _rates(rs, "outcome_ug")
    c = p["counts"]
    cls = Counter(r["error_class"] for r in rs if r["outcome"] == "silent-error")
    return {"n": n,
            "R1": p["R1"],
            "R1_visible": cls["visible"] / n if n else None,
            "R1_targeted": cls["targeted"] / n if n else None,
            "R1_blind": cls["blind"] / n if n else None,
            "R1_noanswer_as_error": (c["silent-error"] + c["no-answer"]) / n if n else None,
            "R1_ungraded_as_error": ug["R1"],
            "R1_replies_only": c["silent-error"] / (n - c["no-answer"]) if n - c["no-answer"] else None,
            "R2": p["R2"],
            "R2_ungraded_as_error": ug["R2"],
            "delivered_supported": c["supported-delivery"] / n if n else None,
            "caught": c["caught"] / n if n else None,
            "supported_any": p["clean"] / n if n else None,
            "E_gross": sum("E-gross" in r["program"] for r in rs) / n if n else None,
            "E_overclaim_typed": sum("E-overclaim" in r["program"] for r in rs) / n if n else None}


def pass_k(rs: list[dict], reps: int) -> dict:
    """Share of (agent, request) cells whose every planned repetition is a supported delivery."""
    by: dict = {}
    for r in rs:
        by.setdefault((r["agent"], r["task"]), []).append(r["outcome"] == "supported-delivery")
    full = [len(v) == reps and all(v) for v in by.values()]
    return {"pass_k": sum(full) / len(full) if full else None, "k": reps, "cells": len(full),
            "incomplete_cells": sum(len(v) != reps for v in by.values())}


def by_task(rs: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in rs:
        out.setdefault(r["task"], []).append(r)
    return out


def boot_ci(rs: list[dict], rng: random.Random, keys=BOOT_KEYS) -> dict:
    groups = by_task(rs)
    ts = sorted(groups)
    point, boots = rates(rs), {k: [] for k in keys}
    for _ in range(BOOT):
        b = rates([r for t in (rng.choice(ts) for _ in ts) for r in groups[t]])
        for k in keys:
            if b[k] is not None:
                boots[k].append(b[k])
    out = {k: v for k, v in point.items() if k not in keys}
    out["requests"] = len(ts)
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
    """R3, cost per supported delivery: dollars for Claude, tokens for Codex, never added; median minutes."""
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


def coverage(rs: list[dict]) -> dict:
    """C: share of stated ranges holding the realised value, by stated level (read against the level, not decided)."""
    by: dict = {}
    for r in rs:
        for c in r.get("coverage") or []:
            lv = "unstated" if c.get("level") is None else f"{float(c['level']):g}"
            lv += " (per-buyer bands)" if c.get("per_buyer") else ""         # one row per buyer: kept apart
            by.setdefault(lv, []).append(bool(c["hit"]))
    return {lv: {"n": len(v), "hit": sum(v) / len(v)} for lv, v in sorted(by.items())}


def typing(rs: list[dict]) -> dict:
    """T: numbers left without a claim after post-hoc typing, per reply with a truth file; and ungraded replies."""
    xs = [r for r in rs if r.get("not_typed") is not None]
    return {"replies": len(xs),
            "not_typed_per_reply": sum(r["not_typed"] for r in xs) / len(xs) if xs else None,
            "replies_with_any": sum(r["not_typed"] > 0 for r in xs) / len(xs) if xs else None,
            "ungraded": sum(bool(r.get("ungraded")) for r in xs)}


def kappa(pairs: list[tuple[bool, bool]]) -> dict | None:
    if not pairs:
        return None
    n = len(pairs)
    po = sum(a == b for a, b in pairs) / n
    pa, pb = sum(a for a, _ in pairs) / n, sum(b for _, b in pairs) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return {"n": n, "agreement": po, "kappa": (po - pe) / (1 - pe) if pe < 1 else 1.0}


def agreement(rs: list[dict], labels: dict) -> dict | None:
    """Labeller 1 (Luna) vs labeller 2 (Haiku) on `supported`, overall, by arm, by agent and by agent and arm."""
    groups = [("all", lambda r: True)]
    groups += [(a, lambda r, a=a: r["arm"] == a) for a in ("N", "H")]
    groups += [(g, lambda r, g=g: r["agent"] == g) for g in ("claude", "codex")]
    groups += [(f"{g} {a}", lambda r, g=g, a=a: r["agent"] == g and r["arm"] == a)
               for g in ("claude", "codex") for a in ("N", "H")]
    out = {}
    for name, keep in groups:
        pairs = [(labels[key(r)]["labeller1"]["supported"], labels[key(r)]["haiku"]["supported"]) for r in rs
                 if keep(r) and "labeller1" in labels.get(key(r), {})]
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


def verdict(n: dict, h: dict, d: dict) -> str:
    r2_ok = h["R2"] is not None and h["R2"] <= R2_MAX      # undefined R2 (no supported H reply) never passes
    if d["ci"] and h["R1"] < n["R1"] and d["ci"][1] < 0 and r2_ok:
        return "met"
    if d["ci"] and d["ci"][1] < 0 and h["R2"] is None:
        return "inconclusive (fewer silent errors, but R2 undefined: no supported reply in arm H)"
    if d["ci"] and d["ci"][0] > 0:
        return "harness worse (the paired interval is above 0)"
    if d["ci"] and d["ci"][1] < 0:
        return "not met: fewer silent errors, but R2 above 15%"
    return "inconclusive (the paired interval includes 0)"


def analyse(run: Path, interim: bool) -> dict:
    labels = json.loads((run / "labels.json").read_text()) if (run / "labels.json").exists() else {}
    tdir = run / "truth"
    summ = json.loads((tdir / "summary.json").read_text()) if (tdir / "summary.json").exists() else {}
    rs, plan = planned(run)
    no_truth = 0
    for r in rs:
        lab = labels.get(key(r), {})
        tf = tdir / truth_name(r)
        tr = json.loads(tf.read_text()) if tf.exists() else None
        if has_answer(r) and not r.get("missing") and tr is None:
            no_truth += 1
        tr = tr or {}
        r["program"] = set(tr.get("program_codes") or [])
        r["ungraded"] = bool(tr.get("ungraded"))
        r["not_typed"] = len(tr["not_typed"]) if "not_typed" in tr else None
        r["coverage"] = [c for g in tr.get("claims") or [] for c in g.get("coverage") or []]
        r["outcome"] = outcome(r, lab, r["program"])
        r["outcome_ug"] = outcome(r, lab, r["program"], extra_bad=r["ungraded"])
        r["error_class"] = error_class(lab, r["program"])
        r["codes"] = sorted(set(lab.get("codes") or []) | r["program"])
    counts = Counter(r["outcome"] for r in rs)
    status = {"planned": len(rs), "recorded": len(rs) - counts["missing"], "missing": counts["missing"],
              "not_ok": sum(r.get("status") not in (None, "OK") for r in rs if not r.get("missing")),
              "no_answer": counts["no-answer"], "unlabelled": counts["unlabelled"], "no_truth": no_truth,
              "truth_mode": summ.get("mode"), "interim": interim}
    if not interim and (counts["missing"] or counts["unlabelled"] or no_truth or summ.get("mode") != "sealed"):
        raise SystemExit(f"not final: {counts['missing']} planned slots missing, {counts['unlabelled']} replies "
                         f"unlabelled, {no_truth} replies without a truth file, truth mode {summ.get('mode')!r} "
                         "(need 'sealed'); use --interim for a progress report")
    rng = random.Random(SEED)
    res = {"status": status, "sets": {}, "rules": {}}
    for name in SETS:
        sub = [r for r in rs if r["set"] == name]
        if not sub:
            continue
        s = {"by_arm": {}, "by_agent_arm": {}, "by_task": {}, "codes": {}, "coverage": {}, "typing": {}}
        for arm in ("N", "H"):
            xs = [r for r in sub if r["arm"] == arm]
            if not xs:
                continue
            s["by_arm"][arm] = {**boot_ci(xs, rng), **pass_k(xs, plan["reps"]), **cost(xs)}
            for ag in ("claude", "codex"):
                ys = [r for r in xs if r["agent"] == ag]
                if ys:
                    s["by_agent_arm"][f"{ag} {arm}"] = {**rates(ys), **pass_k(ys, plan["reps"]), **cost(ys)}
            s["codes"][arm] = {o: dict(Counter(c for r in xs if r["outcome"] == o for c in r["codes"]))
                               for o in ("silent-error", "caught")}
            s["coverage"][arm] = coverage([r for r in xs if has_answer(r)])
            s["typing"][arm] = typing([r for r in xs if has_answer(r)])
        for t in sorted({r["task"] for r in sub}):
            for arm in ("N", "H"):
                xs = [r for r in sub if r["task"] == t and r["arm"] == arm]
                if xs:
                    s["by_task"][f"{t} {arm}"] = dict(Counter(r["outcome"] for r in xs))
        if len(s["by_arm"]) == 2:
            for m in ("R1", "R1_targeted", "R1_blind", "R1_ungraded_as_error"):
                s[f"{m}_diff"] = paired_diff(sub, m, rng)
        res["sets"][name] = s
    ho = res["sets"].get("heldout")
    if ho and len(ho["by_arm"]) == 2:
        n, h = ho["by_arm"]["N"], ho["by_arm"]["H"]
        res["rules"] = {"R1": verdict(n, h, ho["R1_diff"]),
                        "R2 too strict (R2 > 15%)": bool(h["R2"] is not None and h["R2"] > R2_MAX),
                        "R1, ungraded counted as errors (sensitivity)": verdict(
                            {"R1": n["R1_ungraded_as_error"]},
                            {"R1": h["R1_ungraded_as_error"], "R2": h["R2_ungraded_as_error"]},
                            ho["R1_ungraded_as_error_diff"])}
    res["outcomes"] = {key(r): r["outcome"] for r in rs}
    res["agreement"] = agreement([r for r in rs if has_answer(r)], labels)
    idx = run / "label_index.json"
    if idx.exists():
        gw = Counter(v["arm"] for v in json.loads(idx.read_text()).values() if v["gate_words"])
        res["replies_naming_the_gate"] = dict(gw)
    return res


def pct(x, ci=None) -> str:
    if x is None:
        return "—"
    return f"{100 * x:.1f}%" + (f" [{100 * ci[0]:.1f}, {100 * ci[1]:.1f}]" if ci else "")


def num(x) -> str:
    return "—" if x is None else f"{x:.2f}"


def report(res: dict) -> str:
    st = res["status"]
    out = [f"# E3b v2 results{' (INTERIM)' if st['interim'] else ''}", "",
           f"Planned {st['planned']}, recorded {st['recorded']}, missing {st['missing']}, runs not OK after retry "
           f"{st['not_ok']}, no answer {st['no_answer']}, unlabelled {st['unlabelled']}, without a truth file "
           f"{st['no_truth']}; truth mode {st['truth_mode'] or '—'}.", "",
           "Not supported = label false or any program code (E-gross, typed E-overclaim, E-calc). Silent errors split "
           "into gate-visible (E-calc, label or program), backtest-targeted (a program code, no E-calc) and gate-blind "
           "(the rest).", ""]
    for name, s in res["sets"].items():
        out += [f"## {name} requests", "",
                "| arm | n | R1 silent error | gate-visible | backtest-targeted | gate-blind | R2 false hold | "
                "supported delivery | caught | pass^k | $ per supported (Claude) | tokens per supported (Codex) | "
                "median min |",
                "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for arm, x in s["by_arm"].items():
            usd = "—" if x["usd_per_supported"] is None else f"${x['usd_per_supported']:.2f}"
            tok = "—" if x["tokens_per_supported"] is None else f"{x['tokens_per_supported'] / 1000:.0f}k"
            mm = "—" if x["median_min"] is None else f"{x['median_min']:.1f}"
            out.append(f"| {arm} | {x['n']} | {pct(x['R1'], x['R1_ci'])} | {pct(x['R1_visible'])} | "
                       f"{pct(x['R1_targeted'])} | {pct(x['R1_blind'])} | {pct(x['R2'], x['R2_ci'])} "
                       f"(draws {x['R2_draws']}) | {pct(x['delivered_supported'], x['delivered_supported_ci'])} | "
                       f"{pct(x['caught'])} | {pct(x['pass_k'])} (k={x['k']}, {x['cells']} cells) | {usd} | {tok} | "
                       f"{mm} |")
        if s.get("R1_diff"):
            out += ["", "R1(H) − R1(N), paired over requests: " + "; ".join(
                f"{label} {pct(s[m + '_diff']['diff'], s[m + '_diff']['ci'])}"
                for label, m in (("all", "R1"), ("backtest-targeted", "R1_targeted"), ("gate-blind", "R1_blind"),
                                 ("ungraded as error", "R1_ungraded_as_error")))]
        out += ["", "Sensitivity, R1 with no-answer counted as a silent error: " +
                "; ".join(f"{arm} {pct(x['R1_noanswer_as_error'], x['R1_noanswer_as_error_ci'])}"
                          for arm, x in s["by_arm"].items()) +
                ". R1 with every ungraded reply counted as an error: " +
                "; ".join(f"{arm} {pct(x['R1_ungraded_as_error'], x['R1_ungraded_as_error_ci'])}"
                          for arm, x in s["by_arm"].items()) +
                ". R1 over replies only: " + "; ".join(f"{arm} {pct(x['R1_replies_only'])}"
                                                      for arm, x in s["by_arm"].items()), "",
                "Program errors, share of planned attempts: " +
                "; ".join(f"{arm} E-gross {pct(x['E_gross'])}, typed E-overclaim {pct(x['E_overclaim_typed'])}"
                          for arm, x in s["by_arm"].items()), ""]
        if s["by_agent_arm"]:
            out += ["| agent, arm | n | R1 | R2 | supported delivery | pass^k |", "|---|---|---|---|---|---|"]
            out += [f"| {k} | {x['n']} | {pct(x['R1'])} | {pct(x['R2'])} | {pct(x['delivered_supported'])} | "
                    f"{pct(x['pass_k'])} |" for k, x in s["by_agent_arm"].items()]
            out.append("")
        out += ["C, range coverage by stated level (read against the level, not decided):", "",
                "| arm | level | ranges | holding the realised value |", "|---|---|---|---|"]
        out += [f"| {arm} | {lv} | {v['n']} | {pct(v['hit'])} |" for arm, c in s["coverage"].items()
                for lv, v in c.items()] or ["| — | — | 0 | — |"]
        out += ["", "T, numbers left without a claim after post-hoc typing: " + "; ".join(
            f"{arm} {num(t['not_typed_per_reply'])} per reply, {pct(t['replies_with_any'])} of {t['replies']} "
            f"replies with any, {t['ungraded']} ungraded" for arm, t in s["typing"].items()), "",
            "Error codes (label and program): " + "; ".join(f"{arm} {json.dumps(c)}" for arm, c in s["codes"].items()),
            ""]
    if res["rules"]:
        out += ["## Decision rules (held-out requests)", "", "| rule | result |", "|---|---|"]
        out += [f"| {k} | {v if isinstance(v, str) else ('**yes**' if v else 'no')} |" for k, v in res["rules"].items()]
    ag = res.get("agreement")
    if ag:
        out += ["", "Blind labels, labeller 1 (Luna) vs labeller 2 (Haiku) on `supported`: " +
                "; ".join(f"{k} {v['agreement']:.0%} of {v['n']}, kappa {v['kappa']:.2f}"
                          for k, v in ag.items() if isinstance(v, dict)) + f"; {ag['adjudicated']} adjudicated"]
    if "replies_naming_the_gate" in res:
        out += ["", f"Replies whose text names the gate (could unmask the arm): {res['replies_naming_the_gate']}"]
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--interim", action="store_true",
                    help="allow missing slots, unlabelled replies and missing or rehearsal truth files")
    a = ap.parse_args()
    res = analyse(a.run, a.interim)
    out = a.out or a.run
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(res, indent=1, default=float))
    (out / "results.md").write_text(report(res))
    print(report(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
