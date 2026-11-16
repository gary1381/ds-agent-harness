"""E3a, exploratory: the root causes of arm H's false holds (correct answers the gate did not deliver).

The designer read the evidence behind every correct-but-held attempt (the matched answer certificate,
the reconcile reasons and contradictions) and assigned each QUESTION one root cause: every attempt at
these questions was held for the same reason, whichever agent or check outcome. The mapping below is
that judgement, written after the run (post hoc); the script only counts, checks that no held correct
attempt falls outside the mapping, and sets the counts beside arm N's silent errors on the same
questions. Fixes found here were found on the test bank and must be tested on a fresh bank (E4).

    taskset -c 4,5 .venv/bin/python experiments/E3a/false_holds.py \\
        --out docs/experiments/E3a/false_holds.md --fig docs/experiments/E3a/false_holds.svg
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from html import escape
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from dsharness.common import digest, read_jsonl  # noqa: E402

# cause -> (label, questions, what went wrong, candidate fix, where the fix lives)
CAUSES = {
    "ratio": ("Ratio of two metrics", ["R-L11", "R-L13", "R-L19", "R-L20", "R-L31", "R-L33", "R-L39", "R-H13", "R-H30"],
              "per customer, per order, share of customers: no governed metric and no derive step combines two "
              "metrics, so agents either declared nothing (INCONCLUSIVE) or declared the numerator, which "
              "reconcile then compared with the ratio (FAIL: 10.9% vs 108 new customers)",
              "a derive step over two declared metric_calls (ratio of the governed rows, both recomputed)", "harness"),
    "dim_sku": ("Item code not a dimension", ["R-H09", "R-H29", "R-H39", "R-L30"],
                "top-N products by item code: sku is not a governed dimension, so nothing can re-compute the "
                "list; two attempts that declared a month range got the shape FAIL before the dimension gap",
                "add an item-code dimension in onboarding; read an ungoverned key column as INCONCLUSIVE "
                "before the shape check", "onboarding + harness"),
    "dim_guest": ("Guest orders not a dimension", ["R-H10", "R-H34"],
                  "share of sales from guest orders (no customer number): no guest/identified dimension",
                  "add a customer-type dimension in onboarding", "onboarding"),
    "terms": ("Term list misreads the question", ["R-H08", "R-L40", "R-H37"],
              "the model's terms lack 毛产品销售额, so the generic 销售额 marks net sales as asked; "
              "'customers ordered ... who had already bought' matches the active-customer term, not repeat; "
              "the contradiction then blocks a correct gross or repeat-customer answer",
              "term coverage test on paraphrased questions at onboarding; generic terms must not contradict "
              "a declared metric that refines them", "onboarding + harness"),
    "period_growth": ("Growth between two periods", ["R-L18", "R-H14"],
                      "Q2 to Q3 and Jan-Nov year on year: derive growth compares the first and last month "
                      "of the declared range (99.5% vs the correct 22.5%)",
                      "a growth step over two month windows (each rolled up by the metric's rule)", "harness"),
    "mom": ("Change for each month", ["R-H07"],
            "month-over-month change per month: growth collapses the months; no step keeps one row per month",
            "a per-month change step (base: the month before), like cumsum", "harness"),
    "undeclared": ("Step available, not declared", ["R-L34"],
                   "a month's share of the quarter: across share over months exists; one attempt did not declare it",
                   "none (agent)", "agent"),
}
Q2C = {q: c for c, v in CAUSES.items() for q in v[1]}
COL_N, COL_H = "#2a78d6", "#eb6834"                           # arm slots used by every E1/E3a chart
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"


def outcome(adir: Path, rows) -> str:
    """FAIL if any check on the certificate of the submitted rows failed, else INCONCLUSIVE (or NO CERT)."""
    f = adir / "harness_ws/registry/answer_certificates.jsonl"
    certs = list(read_jsonl(f)) if f.exists() else []
    if not certs:
        return "NO CERT"
    want = digest(rows) if rows is not None else None
    m = [c for c in certs if c["answer"]["rows_digest"] == want]
    c = m[-1] if m else certs[-1]
    return "FAIL" if any(k["outcome"] == "FAIL" for k in c["checks"]) else "INCONCLUSIVE"


def tally(run: Path):
    keep = {}
    for r in read_jsonl(run / "attempts.jsonl"):
        if r.get("status") not in ("RETRY", "SKIPPED"):
            keep[(r["agent"], r["arm"], r["qid"], r["rep"])] = r
    grader = set()                                # designer label "grader?": the value is right, the grader cannot read it
    lab = run / "labels"
    if (lab / "designer_labels.json").exists():
        idx = {r["sheet"]: (r["agent"], r["arm"], r["qid"], r["rep"]) for r in json.loads((lab / "index_designer.json").read_text())}
        grader = {idx[r["sheet"]] for r in json.loads((lab / "designer_labels.json").read_text())
                  if r["note"].startswith("grader?")}
    t = defaultdict(Counter)
    qids = defaultdict(set)
    for (ag, arm, qid, rep), r in keep.items():
        c = Q2C.get(qid, "other")
        qids[c].add(qid)
        k = t[c]
        k["questions"] = len(qids[c])
        k[f"{arm}_n"] += 1
        k[f"{arm}_wrong"] += not r["correct"]
        k[f"{arm}_silent"] += (not r["correct"]) and r["delivered"]
        k[f"{arm}_silent_grader"] += (not r["correct"]) and r["delivered"] and (ag, arm, qid, rep) in grader
        if arm == "H" and r["correct"] and not r["delivered"]:
            k["H_held"] += 1
            k["H_held_" + outcome(run / "attempts" / ag / "H" / f"{qid}-r{rep}", (r.get("final_answer") or {}).get("answer_rows"))] += 1
        if arm == "H" and r["correct"] and r["delivered"]:
            k["H_delivered_ok"] += 1
    return t


def chart(t, path: Path) -> None:
    order = list(CAUSES) + ["other"]
    nq = {c: t[c]["questions"] for c in order}
    labels = {c: CAUSES[c][0] for c in CAUSES} | {"other": f"The other {nq['other']} questions"}
    W, top, rh, lw, pw, gap = 1060, 92, 34, 250, 330, 60
    H = top + rh * len(order) + 40
    vmax = max(max(t[c]["H_held"], t[c]["N_silent"]) for c in order)
    vmax = ((vmax + 9) // 10) * 10
    x0s = (lw, lw + pw + gap)
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
         'font-family="system-ui, -apple-system, Segoe UI, sans-serif">',
         f'<rect width="100%" height="100%" fill="{SURFACE}"/>',
         f'<text x="16" y="20" font-size="12" fill="{INK2}">Attempts per root cause (2 agents x 3 repeats per question; '
         'causes assigned post hoc by the designer).</text>',
         f'<text x="16" y="36" font-size="12" fill="{INK2}">Left: correct answers arm H did not deliver (false holds). '
         'Right: wrong answers arm N delivered (silent errors).</text>']
    for i, (title, key, col) in enumerate((("Arm H: correct but held", "H_held", COL_H),
                                           ("Arm N: wrong and delivered", "N_silent", COL_N))):
        x0 = x0s[i]
        o.append(f'<text x="{x0}" y="{top - 26}" font-size="13" font-weight="600" fill="{INK}">{title}</text>')
        for v in range(0, vmax + 1, 10):
            x = x0 + pw * v / vmax
            o.append(f'<line x1="{x:.1f}" y1="{top - 8}" x2="{x:.1f}" y2="{top + rh * len(order)}" stroke="{GRID}"/>')
            o.append(f'<text x="{x:.1f}" y="{top + rh * len(order) + 16}" font-size="10" fill="{INK2}" text-anchor="middle">{v}</text>')
        for j, c in enumerate(order):
            y = top + rh * j + 8
            v = t[c][key]
            w = pw * v / vmax
            n = t[c]["H_n" if key == "H_held" else "N_n"]
            tip = f"{labels[c]}: {v} of {n} attempts"
            if w > 0:
                o.append(f'<rect x="{x0}" y="{y}" width="{w:.1f}" height="16" rx="3" fill="{col}"><title>{escape(tip)}</title></rect>')
            o.append(f'<text x="{x0 + w + 6:.1f}" y="{y + 12}" font-size="11" fill="{INK}">{v}<tspan fill="{INK2}"> / {n}</tspan></text>')
    for j, c in enumerate(order):
        y = top + rh * j + 20
        o.append(f'<text x="{lw - 12}" y="{y}" font-size="12" fill="{INK}" text-anchor="end">{escape(labels[c])}'
                 f'<tspan fill="{INK2}"> ({nq[c]} q)</tspan></text>')
    o.append("</svg>")
    path.write_text("".join(o))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs-root", type=Path, default=ROOT / "experiments/E3a/runs")
    ap.add_argument("--run", default="main")
    ap.add_argument("--out", type=Path, required=True, help="markdown table")
    ap.add_argument("--fig", type=Path, required=True, help="svg chart")
    a = ap.parse_args()
    t = tally(a.runs_root / a.run)
    if t["other"]["H_held"]:
        raise SystemExit(f"{t['other']['H_held']} held correct attempts are on questions without a cause")
    chart(t, a.fig)
    tot = Counter()
    for c in t.values():
        tot.update(c)
    L = ["# E3a false holds by root cause (exploratory, generated by false_holds.py)", "",
         f"Arm H held {tot['H_held']} correct answers ({tot['H_held_FAIL']} with a failed check, "
         f"{tot['H_held_INCONCLUSIVE']} inconclusive). All of them are on the {len(Q2C)} questions below; on the other "
         f"{t['other']['questions']} questions arm H delivered {t['other']['H_delivered_ok']} of {t['other']['H_n']} attempts, all correct, "
         f"and held none. The same {len(Q2C)} questions carry {tot['N_silent'] - t['other']['N_silent']} of arm N's "
         f"{tot['N_silent']} silent errors ({tot['N_silent_grader']} of the {tot['N_silent']} are right values the "
         "registered grader cannot read, designer note `grader?`). Causes were assigned by the designer after reading the evidence (post hoc); "
         "fixes found here must be tested on a fresh bank.", "",
         f"![false holds by root cause]({a.fig.name})", "",
         "| root cause | questions | H correct held (FAIL / INCONCL.) | H wrong (all held) | N silent errors (of which grader?) | candidate fix | where |",
         "|---|---|---|---|---|---|---|"]
    for c, (label, qs, why, fix, where) in CAUSES.items():
        k = t[c]
        L.append(f"| {label} | {len(qs)} | {k['H_held']} of {k['H_n']} ({k['H_held_FAIL']} / {k['H_held_INCONCLUSIVE']}) | "
                 f"{k['H_wrong']} | {k['N_silent']} of {k['N_n']} ({k['N_silent_grader']}) | {fix} | {where} |")
    k = t["other"]
    L += [f"| other questions | {k['questions']} | {k['H_held']} of {k['H_n']} | {k['H_wrong']} | {k['N_silent']} of {k['N_n']} ({k['N_silent_grader']}) | | |", "",
          "## What went wrong, per cause", ""]
    for c, (label, qs, why, fix, where) in CAUSES.items():
        L.append(f"- **{label}** ({', '.join(qs)}): {why}.")
    a.out.write_text("\n".join(L) + "\n")
    print("->", a.out, a.fig)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
