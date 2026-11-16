"""E1 / E1b / E3a analysis: pre-registered metrics with question-cluster intervals, decision rules, error labels,
designer-vs-Haiku kappa, and charts.

    .venv/bin/python experiments/E1/analyze.py --run main --figdir docs/experiments/E1
    .venv/bin/python experiments/E1/analyze.py --runs-root experiments/E1b/runs --run main \
        --answer-form experiments/E1b/questions/answer_form.json --prefix e1b --figdir docs/experiments/E1b
    .venv/bin/python experiments/E1/analyze.py --runs-root experiments/E3a/runs --run main --rules e3a \
        --control-run control --answer-form experiments/E3a/questions/answer_form.json --prefix e3a \
        --figdir docs/experiments/E3a
    .venv/bin/python experiments/E1/analyze.py --runs-root experiments/E4/runs --run main --rules e4 \
        --answer-form experiments/E4/questions/answer_form.json --semantic experiments/E4/context/semantic_model.yaml \
        --prefix e4 --figdir docs/experiments/E4

Reads runs/<run>/attempts.jsonl (latest record per attempt, RETRY/SKIPPED excluded), the answer-form file,
and, if present, labels/designer_labels.json + labels/haiku_blind.json with their index files. Writes
runs/<run>/analysis.json and prints the tables. Arms N and H give the E1 rules D1-D4; a run with arm H1
(the frozen v0.1 harness) gives the E1b rules D1, D4, D5, D6; `--rules e3a` gives R1-R3, arm H's delivery
counted byte for byte beside the S2 rule, and (with `--control-run`) arm N on the original names and dates;
`--rules e4` gives R1-R3 on harness v0.5 and R4, the second-opinion rules from the attempts already run
(census.second_opinions: arm H with the other agent's arm-N answer, t2n, against two agents without the
harness, pair).
Exploratory splits (DIRECT/DERIVED, harness-induced) are marked as such; the decision rules use the
pre-registered quantities only. Quantities added after E1b draw from their own random streams, so the
E1 and E1b numbers are unchanged.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import random
import sys
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from dsharness.bench import census as C  # noqa: E402
from dsharness.bench import live  # noqa: E402
from dsharness.tools.analytics import KEY_HINTS  # noqa: E402
from dsharness.common import read_jsonl  # noqa: E402
from dsharness.context.semantic import load_semantic_model  # noqa: E402
from dsharness.workspace import Workspace  # noqa: E402

BOOT, SEED = 2000, 20261106
# experiments/E1/coverage.md, fixed before the runs
COVERED_IF_METRIC = {"E-def", "E-test", "E-refund", "E-dup", "E-unit", "E-src", "E-join", "E-calc"}
COVERED = {"E-proc"}
PARTLY = {"E-time", "E-late", "E-read", "E-fmt"}
LABELS = ("E-def", "E-test", "E-refund", "E-dup", "E-unit", "E-time", "E-late", "E-src", "E-join", "E-calc",
          "E-read", "E-fmt", "E-proc", "E-other")
COLORS = {"N": "#2a78d6", "H": "#eb6834", "H1": "#1baf7a"}     # categorical slots 1, 2, 3 (fixed per arm)
CALC_ORDER = ("N", "H", "H1")             # bootstrap draw order; N, H first keeps the E1 numbers reproducible
SHOW_ORDER = ("N", "H1", "H")
HARNESS = ("H", "H1")
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
H_VERSION = ""                            # set from --rules: the harness version arm H served


# ============================================================================ metrics
def latest(records: list[dict]) -> list[dict]:
    keep = {}
    for r in records:
        keep[(r["agent"], r["arm"], r["qid"], r["rep"])] = r
    return [r for r in keep.values() if r.get("status") not in ("RETRY", "SKIPPED")]


def rates(rs: list[dict]) -> dict:
    n = len(rs)
    correct = sum(r["correct"] for r in rs)
    return {"n": n,
            "accuracy": correct / n if n else None,
            "M1": sum((not r["correct"]) and r["delivered"] for r in rs) / n if n else None,
            "M2": sum(r["correct"] and not r["delivered"] for r in rs) / correct if correct else None,
            "abstention": sum(not r.get("claimed_success") for r in rs) / n if n else None}


def cluster_ci(rs: list[dict], rng: random.Random) -> dict:
    """Point rates and 95% intervals from a bootstrap over questions (the pre-registered unit)."""
    by_q: dict[str, list[dict]] = {}
    for r in rs:
        by_q.setdefault(r["qid"], []).append(r)
    qs = sorted(by_q)
    point = rates(rs)
    boots = {k: [] for k in ("accuracy", "M1", "M2", "abstention")}
    for _ in range(BOOT):
        sample = [r for q in (rng.choice(qs) for _ in qs) for r in by_q[q]]
        b = rates(sample)
        for k in boots:
            if b[k] is not None:
                boots[k].append(b[k])
    out = {"n": point["n"], "questions": len(qs)}
    for k, xs in boots.items():
        xs.sort()
        out[k] = point[k]
        out[k + "_ci"] = [xs[int(0.025 * len(xs))], xs[int(0.975 * len(xs)) - 1]] if xs else None
    return out


# ============================================================================ labels
def load_labels(run: Path) -> tuple[dict, dict]:
    """(agent, arm, qid, rep) -> designer label row; same for Haiku's blind labels."""
    lab = run / "labels"

    def join(labels_file: str, index_file: str) -> dict:
        if not (lab / labels_file).exists():
            return {}
        idx = {x["sheet"]: (x["agent"], x["arm"], x["qid"], x["rep"]) for x in json.loads((lab / index_file).read_text())}
        return {idx[r["sheet"]]: r for r in json.loads((lab / labels_file).read_text()) if r["sheet"] in idx}
    return join("designer_labels.json", "index_designer.json"), join("haiku_blind.json", "index_blind.json")


def kappa(pairs: list[tuple[str, str]]) -> float | None:
    if not pairs:
        return None
    n = len(pairs)
    po = sum(a == b for a, b in pairs) / n
    ca, cb = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / n ** 2
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def covered(label: str, form: str) -> str:
    if label in COVERED or (label in COVERED_IF_METRIC and form == "DIRECT"):
        return "yes"
    if label in PARTLY:
        return "partly"
    return "no"


# ============================================================================ charts
def arm_names(arms: list[str]) -> dict[str, str]:
    h = "H: harness v0.2" if "H1" in arms else f"H: harness {H_VERSION}".strip()
    return {"N": "N: no harness", "H1": "H1: harness v0.1", "H": h}


def _legend(arms: list[str], x: float = 16, y: float = 30) -> tuple[list[str], float]:
    out, names = [], arm_names(arms)
    for arm in arms:
        out.append(f'<rect x="{x}" y="{y}" width="10" height="10" rx="2" fill="{COLORS[arm]}"/>'
                   f'<text x="{x + 15}" y="{y + 9}" font-size="11" fill="{INK}">{names[arm]}</text>')
        x += 15 + 7 * len(names[arm]) + 8
    return out, x


def _bar_panel(groups: list[tuple[str, dict]], arms: list[str], metric: str, title: str, x0: float, y0: float,
               w: float, h: float) -> list[str]:
    """Grouped bars, one per arm, per group with 95% interval whiskers; values labelled."""
    out = [f'<text x="{x0}" y="{y0 - 10}" font-size="13" font-weight="600" fill="{INK}">{title}</text>']
    top = max([1e-9] + [g[metric + "_ci"][1] for _, by in groups for g in by.values() if g.get(metric + "_ci")])
    step = 0.2 if top > 0.5 else 0.1
    top = min(1.0, step * (int(top / step) + 1))
    y = lambda v: y0 + h - v / top * h  # noqa: E731
    for t in [k * step for k in range(int(round(top / step)) + 1)]:
        out.append(f'<line x1="{x0}" y1="{y(t):.1f}" x2="{x0 + w}" y2="{y(t):.1f}" stroke="{GRID}"/>')
        out.append(f'<text x="{x0 - 6}" y="{y(t) + 4:.1f}" font-size="10" fill="{INK2}" text-anchor="end">{t:.0%}</text>')
    gw = w / len(groups)
    bw = min(34, gw / (len(arms) + 1))
    fs = 10 if len(arms) == 2 else 9
    for i, (name, by) in enumerate(groups):
        cx = x0 + gw * i + gw / 2
        for j, arm in enumerate(arms):
            g = by.get(arm, {})
            v = g.get(metric)
            if v is None:
                continue
            bx = cx + (j - len(arms) / 2) * (bw + 2) + 1
            out.append(f'<rect x="{bx:.1f}" y="{y(v):.1f}" width="{bw}" height="{max(0.0, y(0) - y(v)):.1f}" '
                       f'rx="2" fill="{COLORS[arm]}"/>')
            ci = g.get(metric + "_ci")
            if ci:
                mx = bx + bw / 2
                out.append(f'<line x1="{mx:.1f}" y1="{y(ci[0]):.1f}" x2="{mx:.1f}" y2="{y(ci[1]):.1f}" stroke="{INK}" '
                           f'stroke-width="1.2"/>')
            out.append(f'<text x="{bx + bw / 2:.1f}" y="{y(v) - 4 if not ci else y(ci[1]) - 4:.1f}" font-size="{fs}" '
                       f'fill="{INK}" text-anchor="middle">{v:.0%}</text>')
        out.append(f'<text x="{cx:.1f}" y="{y0 + h + 16}" font-size="11" fill="{INK}" text-anchor="middle">{name}</text>')
    return out


FORM_NAMES = {"DIRECT": "DIRECT", "DERIVED": "DERIVED", "DERIVED-step": "DERIVED: one step", "DERIVED-none": "DERIVED: no metric"}


def metrics_chart(table: dict, arms: list[str], forms: list[str], path: Path, by_form: bool = False) -> None:
    """Per agent (default) or, with by_form, pooled over agents and split by question form (exploratory)."""
    if by_form:
        groups = [(f"{FORM_NAMES[f]} ({table[f'pooled/N/{f}']['questions']} q.)",
                   {arm: table.get(f"pooled/{arm}/{f}", {}) for arm in arms}) for f in forms]
    else:
        groups = [(a, {arm: table.get(f"{a}/{arm}", {}) for arm in arms}) for a in ("claude", "codex", "pooled")]
    pw = 240 if len(arms) == 2 and len(groups) <= 3 else 300
    W, H = (900 if pw == 240 else 50 + 3 * (pw + 50)), 300
    legend, _ = _legend(arms)
    parts = [f'<rect width="100%" height="100%" fill="{SURFACE}"/>',
             f'<text x="16" y="20" font-size="12" fill="{INK2}">Bars: rate per attempt (M2: per correct answer). '
             f'Whiskers: 95% interval, bootstrap over questions. Arm N has no gate, so its M2 is 0 by design.'
             + (' Pooled over both agents; split by question form (exploratory).' if by_form else '') + '</text>',
             *legend]
    for k, (metric, title) in enumerate((("accuracy", "Accuracy"), ("M1", "M1 silent errors (wrong and delivered)"),
                                         ("M2", "M2 false holds (correct, not delivered)"))):
        parts += _bar_panel(groups, arms, metric, title, 50 + k * (pw + 50), 80, pw, 170)
    path.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
                    f'font-family="system-ui, -apple-system, Segoe UI, sans-serif">' + "".join(parts) + "</svg>\n",
                    encoding="utf-8")


def labels_chart(counts: dict[str, Counter], arms: list[str], path: Path) -> None:
    """Horizontal grouped bars: count of wrong attempts per label, one bar per arm."""
    used = [lb for lb in LABELS if any(counts[a][lb] for a in arms)]
    W, row = 900, 12 * len(arms) + 6
    H = 70 + row * len(used) + 30
    top = max([1] + [counts[a][lb] for a in arms for lb in used])
    x0, w = 120, 640
    x = lambda v: x0 + v / top * w  # noqa: E731
    legend, lx = _legend(arms)
    hatch_arm = "H" if "H" in arms else arms[-1]
    parts = [f'<rect width="100%" height="100%" fill="{SURFACE}"/>',
             f'<text x="16" y="20" font-size="12" fill="{INK2}">Wrong attempts by primary error label (designer). '
             f'Hatched part: the harness itself caused the error (exploratory tag).</text>',
             '<defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
             f'<line x1="0" y1="0" x2="0" y2="6" stroke="{SURFACE}" stroke-width="2"/></pattern></defs>',
             *legend,
             f'<rect x="{lx}" y="30" width="10" height="10" rx="2" fill="{COLORS[hatch_arm]}"/>'
             f'<rect x="{lx}" y="30" width="10" height="10" fill="url(#hatch)"/>'
             f'<text x="{lx + 15}" y="39" font-size="11" fill="{INK}">caused by the harness</text>']
    if len(arms) == 2:               # E1 chart layout, unchanged
        parts[-1] = (f'<rect x="230" y="30" width="10" height="10" rx="2" fill="{COLORS["H"]}"/>'
                     f'<rect x="230" y="30" width="10" height="10" fill="url(#hatch)"/>'
                     f'<text x="245" y="39" font-size="11" fill="{INK}">H, caused by the harness</text>')
    for i, lb in enumerate(used):
        cy = 64 + i * row
        parts.append(f'<text x="{x0 - 8}" y="{cy + 14}" font-size="12" fill="{INK}" text-anchor="end">{lb}</text>')
        for j, arm in enumerate(arms):
            v = counts[arm][lb]
            by = cy + j * 12
            if v:
                parts.append(f'<rect x="{x0}" y="{by}" width="{x(v) - x0:.1f}" height="10" rx="2" fill="{COLORS[arm]}"/>')
                hv = counts[arm + "-induced"][lb]
                if hv:
                    parts.append(f'<rect x="{x0}" y="{by}" width="{x(hv) - x0:.1f}" height="10" fill="url(#hatch)"/>')
                parts.append(f'<text x="{x(v) + 4:.1f}" y="{by + 9}" font-size="10" fill="{INK}">{v}</text>')
    path.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
                    f'font-family="system-ui, -apple-system, Segoe UI, sans-serif">' + "".join(parts) + "</svg>\n",
                    encoding="utf-8")


# ============================================================================ main
def key(r: dict) -> tuple:
    return r["agent"], r["arm"], r["qid"], r["rep"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--runs-root", type=Path, default=ROOT / "experiments/E1/runs")
    ap.add_argument("--answer-form", type=Path, default=ROOT / "experiments/E1/questions/answer_form.json")
    ap.add_argument("--prefix", default="e1", help="chart file prefix")
    ap.add_argument("--figdir", type=Path, default=None)
    ap.add_argument("--rules", choices=("auto", "e3a", "e4"), default="auto",
                    help="auto: E1 (arms N, H) or E1b (with H1); e3a: R1-R3 of the E3a pre-registration; "
                         "e4: R1-R4 of the E4 pre-registration")
    ap.add_argument("--control-run", default=None, help="E3a: run of arm N on the original names and dates")
    ap.add_argument("--semantic", type=Path, default=None,
                    help="E4: the semantic model whose dimensions are key columns for compare_answers")
    a = ap.parse_args()
    global H_VERSION
    H_VERSION = {"e3a": "v0.3", "e4": "v0.5"}.get(a.rules, "")
    run = a.runs_root / a.run
    raw = read_jsonl(run / "attempts.jsonl")
    recs = latest(raw)
    af = json.loads(a.answer_form.read_text())
    form = {r["qid"]: r["form"] for r in af}
    step = {r["qid"]: ("DERIVED-none" if r.get("metric") is None else "DERIVED-step")
            for r in af if r["form"] == "DERIVED"}
    arms = [arm for arm in CALC_ORDER if any(r["arm"] == arm for r in recs)]
    shown = [arm for arm in SHOW_ORDER if arm in arms]
    rng = random.Random(SEED)
    out: dict = {"attempts": len(recs), "retry_or_skipped": sum(r.get("status") in ("RETRY", "SKIPPED")
                                                                    for r in latest_all(raw)),
                 "status": dict(Counter(r["status"] for r in recs)), "arms": shown}
    table = {}
    for agent in ("claude", "codex", "pooled"):
        for arm in arms:
            rs = [r for r in recs if r["arm"] == arm and agent in ("pooled", r["agent"])]
            if rs:
                table[f"{agent}/{arm}"] = cluster_ci(rs, rng)
                for f in ("DIRECT", "DERIVED"):
                    sub = [r for r in rs if form[r["qid"]] == f]
                    if sub:
                        table[f"{agent}/{arm}/{f}"] = cluster_ci(sub, rng)
    for arm in arms:            # descriptive DERIVED split (E1b pre-registration), drawn after the E1 quantities
        for f in ("DERIVED-step", "DERIVED-none"):
            sub = [r for r in recs if r["arm"] == arm and step.get(r["qid"]) == f]
            if sub:
                table[f"pooled/{arm}/{f}"] = cluster_ci(sub, rng)
    out["table"] = table
    summ = C.summarize(raw)
    out["paired_M1_H_minus_N"] = {k.split("/")[0]: v for k, v in summ.items() if k.endswith("diff_M1_H_minus_N")}
    out["paired_M1"] = {k.replace("/diff_M1_", "/"): v for k, v in summ.items() if "/diff_M1_" in k}
    out["M3"] = {k: {"usd_per_correct_delivered": v.get("M3_usd_per_correct_delivered"),
                     "tokens_per_correct_delivered": v.get("M3_tokens_per_correct_delivered"),
                     "total_usd": v.get("total_usd"), "median_seconds": v.get("median_seconds")}
                 for k, v in summ.items() if "/" in k and "/diff_" not in k}
    out["M4_pass_hat_3"] = {k: v.get("M4_pass_hat_3") for k, v in summ.items() if "M4_pass_hat_3" in v}

    # decision rules (pre-registered quantities only)
    pN, pH = table["pooled/N"], table["pooled/H"]
    diff = out["paired_M1_H_minus_N"].get("pooled", {})
    designer, haiku = load_labels(run)
    d1 = {"M1_H": pH["M1"], "M1_N": pN["M1"], "ratio": pH["M1"] / pN["M1"] if pN["M1"] else None,
          "diff_ci95": diff.get("ci95"), "M2_H": pH["M2"],
          "met": bool(pN["M1"] and pH["M1"] <= 0.8 * pN["M1"] and diff.get("ci95") and diff["ci95"][1] < 0
                      and pH["M2"] is not None and pH["M2"] <= 0.15)}
    d4 = {"M2_H": pH["M2"], "met": pH["M2"] is not None and pH["M2"] > 0.15}
    if a.rules in ("e3a", "e4"):  # E3a rules; R1 and R2 are E1's D1 and D4 on the new data; E4 repeats them
        nD = [r for r in recs if r["arm"] == "N" and form[r["qid"]] == "DIRECT"]
        hD = [r for r in recs if r["arm"] == "H" and form[r["qid"]] == "DIRECT"]
        r3 = C._paired_boot(nD, hD, random.Random(SEED), BOOT)
        out["decision"] = {
            "R1": d1, "R2": d4,
            "R3": {"M1_H_DIRECT": rates(hD)["M1"], "M1_N_DIRECT": rates(nD)["M1"], "questions": r3.get("questions"),
                   "diff_M1_H_minus_N": r3.get("point"), "diff_ci95": r3.get("ci95"),
                   "met": bool(r3.get("ci95") and rates(hD)["M1"] < rates(nD)["M1"] and r3["ci95"][1] < 0)},
        }
        out["delivery_rule"] = delivery_rule(run, recs)
        if a.control_run:
            out["control"] = control(a.runs_root / a.control_run, recs, form)
        if a.rules == "e4":
            hints = set(KEY_HINTS) | set(load_semantic_model(a.semantic).dimensions if a.semantic else ())
            out["second_opinion"] = second_opinion(run, recs, form, hints)
            out["decision"]["R4"] = out["second_opinion"]["R4"]
    elif "H1" in arms:           # E1b rules
        p1 = table["pooled/H1"]
        dh = summ.get("pooled/diff_M1_H_minus_H1", {})
        hD, h1D = table.get("pooled/H/DIRECT", {}), table.get("pooled/H1/DIRECT", {})
        out["decision"] = {
            "D1": d1, "D4": d4,
            "D5": {"diff_M1_H_minus_H1": dh.get("point"), "diff_ci95": dh.get("ci95"), "M2_H": pH["M2"],
                   "M2_H1": p1["M2"],
                   "met": bool(dh.get("ci95") and dh["ci95"][1] < 0 and pH["M2"] is not None
                               and p1["M2"] is not None and pH["M2"] < p1["M2"])},
            "D6": {"M1_H_DIRECT": hD.get("M1"), "M1_H1_DIRECT": h1D.get("M1"),
                   "met": bool(hD.get("M1") is not None and h1D.get("M1") is not None
                               and hD["M1"] > h1D["M1"] + 0.05)},
        }
    else:
        wrong_n = [r for r in recs if r["arm"] == "N" and not r["correct"]]
        lab_n = [(designer[k]["label"], form[k[2]]) for k in (key(r) for r in wrong_n) if k in designer]
        cov = Counter(covered(lb, f) for lb, f in lab_n)
        out["decision"] = {
            "D1": d1,
            "D2": {"M1_N": pN["M1"], "met": pN["M1"] < 0.02},
            "D3": {"labelled_wrong_N": len(lab_n), "of": len(wrong_n), "coverage": dict(cov),
                   "uncovered_share_strict": cov["no"] / len(lab_n) if lab_n else None,
                   "uncovered_share_incl_partly": (cov["no"] + cov["partly"]) / len(lab_n) if lab_n else None,
                   "met": (cov["no"] / len(lab_n) > 0.5) if lab_n else None},
            "D4": d4,
        }
    # labels
    counts: dict[str, Counter] = {k: Counter() for arm in arms for k in (arm, arm + "-induced")}
    for (agent, arm, qid, rep), r in designer.items():
        counts[arm][r["label"]] += 1
        if r.get("harness_induced"):
            counts[arm + "-induced"][r["label"]] += 1
    out["labels"] = {k: dict(v) for k, v in counts.items()}
    out["labels_by_agent"] = {f"{ag}/{arm}": dict(Counter(r["label"] for (g, ar, _, _), r in designer.items()
                                                          if g == ag and ar == arm))
                              for ag in ("claude", "codex") for arm in arms}
    pairs = [(designer[k]["label"], haiku[k]["label"]) for k in haiku if k in designer]
    out["kappa"] = {"n": len(pairs), "agreement": (sum(x == y for x, y in pairs) / len(pairs)) if pairs else None,
                    "cohen_kappa": kappa(pairs)}
    induced_by_arm = {}
    for arm in (h for h in HARNESS if h in arms):
        wrong_h = [r for r in recs if r["arm"] == arm and not r["correct"]]
        induced_by_arm[arm] = {
            "wrong": len(wrong_h), "induced": sum(1 for r in wrong_h if designer.get(key(r), {}).get("harness_induced")),
            "silent": sum(1 for r in wrong_h if r["delivered"]),
            "silent_induced": sum(1 for r in wrong_h if r["delivered"]
                                  and designer.get(key(r), {}).get("harness_induced"))}
    if "H1" in arms:
        out["harness_induced"] = induced_by_arm
    else:
        h = induced_by_arm["H"]
        out["harness_induced"] = {"wrong_H": h["wrong"], "induced": h["induced"], "silent_H": h["silent"],
                                  "silent_H_induced": h["silent_induced"]}
    # exploratory: attempts the designer marked "grader?" (value right, but the pre-registered grader cannot
    # read it: 0.200 arrives as the JSON number 0.2; a period key such as 2026-Q1 fails the month check)
    art = {k for k, r in designer.items() if r["note"].startswith("grader?")}
    flipped = [{**r, "correct": True} if key(r) in art else r for r in recs]
    out["sensitivity_grader"] = {
        "n": len(art), "by_arm": dict(Counter(k[1] for k in art)),
        "delivered": sum(1 for r in recs if key(r) in art and r["delivered"]),
        "pooled": {arm: rates([r for r in flipped if r["arm"] == arm]) for arm in arms}}
    # exploratory: questions whose truth does not match their wording (designer note "question?"), left out
    qbad = sorted({k[2] for k, r in designer.items() if r["note"].startswith("question?")})
    kept = [r for r in recs if r["qid"] not in qbad]
    out["sensitivity_question"] = {"questions": qbad,
                                   "pooled": {arm: rates([r for r in kept if r["arm"] == arm]) for arm in arms}}
    (run / "analysis.json").write_text(json.dumps(out, indent=1, default=str))
    if a.figdir:
        a.figdir.mkdir(parents=True, exist_ok=True)
        forms = ["DIRECT", "DERIVED"] + (["DERIVED-step", "DERIVED-none"] if "H1" in arms or a.rules == "e3a" else [])
        metrics_chart(table, shown, forms, a.figdir / f"{a.prefix}_metrics.svg")
        metrics_chart(table, shown, forms, a.figdir / f"{a.prefix}_by_form.svg", by_form=True)
        if designer:
            labels_chart(counts, shown, a.figdir / f"{a.prefix}_labels.svg")
    print(json.dumps({k: out[k] for k in ("attempts", "status", "decision", "kappa", "harness_induced",
                                          "sensitivity_grader", "sensitivity_question", "delivery_rule", "control")
                      if k in out},
                     indent=1, default=str))
    for k, v in table.items():
        if k.count("/") in (1, 2):
            print(f"{k:22s} n={v['n']:3d} acc {v['accuracy']:.3f} {fmt(v['accuracy_ci'])}  M1 {v['M1']:.3f} "
                  f"{fmt(v['M1_ci'])}  M2 {v['M2'] if v['M2'] is None else round(v['M2'], 3)} {fmt(v['M2_ci'])}")
    return 0


def byte_only_delivered(run: Path, r: dict) -> bool:
    """Arm H's delivery under the E1/E1b census rule: the final rows equal a submission byte for byte."""
    adir = run / "attempts" / r["agent"] / r["arm"] / f"{r['qid']}-r{r['rep']}"
    principal = f"agent:census-{r['agent']}-{r['arm']}-{r['qid']}-r{r['rep']}".lower()
    with mock.patch.object(live, "values_digest", lambda rows: None):
        return bool(live.harness_from_audit(Workspace(adir / "harness_ws"), principal,
                                            (r.get("final_answer") or {}).get("answer_rows"))["delivered"])


def delivery_rule(run: Path, recs: list[dict]) -> dict:
    """Arm H's M1 and M2 with delivery matched byte for byte (E1, E1b) and as numbers too (v0.3 S2). Only a
    final answer matched by value alone can differ; the byte rule then looks for an earlier exact match."""
    out = {}
    for agent in ("claude", "codex", "pooled"):
        rs = [r for r in recs if r["arm"] == "H" and agent in ("pooled", r["agent"])]
        if not rs:
            continue
        byte = [{**r, "delivered": byte_only_delivered(run, r)} if (r.get("harness") or {}).get("final_rows_match")
                == "values" else r for r in rs]
        s2, b = rates(rs), rates(byte)
        out[agent] = {"delivered_S2": sum(r["delivered"] for r in rs), "delivered_byte": sum(r["delivered"] for r in byte),
                      "matched_by_value_only": sum((r.get("harness") or {}).get("final_rows_match") == "values" for r in rs),
                      "M1_S2": s2["M1"], "M1_byte": b["M1"], "M2_S2": s2["M2"], "M2_byte": b["M2"]}
    return out


def control(run: Path, recs: list[dict], form: dict) -> dict:
    """Exploratory: arm N on the original column names and dates (N-orig) beside arm N on the disguised copy."""
    ctl = latest(read_jsonl(run / "attempts.jsonl"))
    rng = random.Random(SEED + 1)
    out = {"attempts": len(ctl), "status": dict(Counter(r["status"] for r in ctl))}
    for agent in ("claude", "codex", "pooled"):
        n = [r for r in recs if r["arm"] == "N" and agent in ("pooled", r["agent"])]
        o = [r for r in ctl if agent in ("pooled", r["agent"])]
        if o:
            out[agent] = {"N": cluster_ci(n, rng), "N-orig": cluster_ci(o, rng),
                          "diff_M1_Norig_minus_N": C._paired_boot(n, o, rng, BOOT),
                          "DIRECT": {"N": rates([r for r in n if form[r["qid"]] == "DIRECT"]),
                                     "N-orig": rates([r for r in o if form[r["qid"]] == "DIRECT"])}}
    return out


def _frac(rows: list[tuple[dict, float]]) -> tuple[float | None, float | None]:
    """M1 and M2 when an attempt is delivered with probability d (a share of possible second opinions)."""
    n, c = len(rows), sum(r["correct"] for r, _ in rows)
    return (sum((not r["correct"]) * d for r, d in rows) / n if n else None,
            sum(r["correct"] * (1 - d) for r, d in rows) / c if c else None)


def second_opinion(run: Path, recs: list[dict], form: dict, hints: set[str]) -> dict:
    """E4 R4 (H v0.4 T2f): arm H delivered by the gate, or else by the other agent's arm-N answer when the final
    certificate allows it (t2n), against two agents without the harness (arm N claimed and the other agent's
    arm-N answer agrees: pair). Both from the attempts already run; agreement is compare_answers on the raw rows.
    An attempt without a second opinion (the other agent has no arm-N attempt) counts as not delivered by it.
    Intervals: cluster bootstrap over questions, paired between the rules, own random stream. Cost: agent runs
    per attempt (pair 2; t2n 1 plus the share that needs a second run) and per correct delivered answer (token
    counts are not comparable between the two CLIs)."""
    so = C.second_opinions(recs, run, hints)
    rule = {"N": "pair", "H": "t2n"}
    rows = {arm: [(r, so[key(r)][rule[arm]]) for r in recs if r["arm"] == arm] for arm in ("N", "H")}
    rng = random.Random(f"{SEED}/e4/R4")
    out: dict = {"no_second_opinion": {arm: sum(so[key(r)]["missing"] for r, _ in rows[arm]) for arm in rows}}
    for agent in ("claude", "codex", "pooled"):
        for f in ("all", "DIRECT", "DERIVED"):
            sub = {arm: [(r, d) for r, d in rows[arm] if agent in ("pooled", r["agent"]) and f in ("all", form[r["qid"]])]
                   for arm in rows}
            by_q = {arm: {} for arm in sub}
            for arm, rs in sub.items():
                for r, d in rs:
                    by_q[arm].setdefault(r["qid"], []).append((r, d))
            qs = sorted(set(by_q["N"]) & set(by_q["H"]))
            if not qs:
                continue

            def stats(sample):
                h = [x for q in sample for x in by_q["H"][q]]
                n = [x for q in sample for x in by_q["N"][q]]
                (m1h, m2h), (m1n, m2n) = _frac(h), _frac(n)
                return {"M1_t2n": m1h, "M1_pair": m1n, "M2_t2n": m2h, "M2_pair": m2n,
                        "diff_M1": m1h - m1n, "diff_M2": None if m2h is None or m2n is None else m2h - m2n}
            point = stats(qs)
            boots = [stats([rng.choice(qs) for _ in qs]) for _ in range(BOOT)]
            ci = {k: (lambda xs: [xs[int(0.025 * len(xs))], xs[int(0.975 * len(xs)) - 1]] if xs else None)(
                sorted(b[k] for b in boots if b[k] is not None)) for k in point}
            hs = [x for q in qs for x in by_q["H"][q]]
            ns = [x for q in qs for x in by_q["N"][q]]
            asks = sum(so[key(r)]["asks"] for r, _ in hs) / len(hs)
            cd_h, cd_n = sum(r["correct"] * d for r, d in hs), sum(r["correct"] * d for r, d in ns)
            out[f"{agent}/{f}"] = {
                "questions": len(qs), "attempts_H": len(hs), "attempts_N": len(ns), **point,
                **{f"{k}_ci95": v for k, v in ci.items()},
                "runs_per_attempt_t2n": 1 + asks, "runs_per_attempt_pair": 2.0, "second_run_share_t2n": asks,
                "runs_per_correct_delivered_t2n": len(hs) * (1 + asks) / cd_h if cd_h else None,
                "runs_per_correct_delivered_pair": 2.0 * len(ns) / cd_n if cd_n else None}
    p = out.get("pooled/all", {})
    out["R4"] = {k: p.get(k) for k in ("M1_t2n", "M1_pair", "diff_M1", "diff_M1_ci95", "M2_t2n", "M2_pair",
                                        "runs_per_attempt_t2n")}
    out["R4"]["met"] = bool(p and p["diff_M1_ci95"] and p["diff_M1_ci95"][1] <= 0.01 and p["M2_t2n"] is not None
                            and p["M2_pair"] is not None and p["M2_t2n"] <= p["M2_pair"]
                            and p["runs_per_attempt_t2n"] < 2.0)
    return out


def latest_all(records: list[dict]) -> list[dict]:
    keep = {}
    for r in records:
        keep[(r["agent"], r["arm"], r["qid"], r["rep"])] = r
    return list(keep.values())


def fmt(ci) -> str:
    return f"[{ci[0]:.3f}, {ci[1]:.3f}]" if ci else ""


if __name__ == "__main__":
    raise SystemExit(main())
