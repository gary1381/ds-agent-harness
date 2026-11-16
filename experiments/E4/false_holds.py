"""E4, pre-registered secondary: the root causes of arm H's false holds (correct answers the gate did not deliver).

The designer read the evidence behind every correct-but-held attempt (the last answer certificate's reasons, the
reconcile evidence's contradictions, the transcript) and assigned each QUESTION one root cause, as E3a did. The
mapping below is that judgement, written after the run (post hoc); the script only counts, checks that no held
correct attempt falls outside the mapping, and sets the counts beside arm H's silent errors and arm N's silent
errors on the same questions. A cause may also list a question with no hold when the same mechanism made arm H
deliver wrong answers there (F-L13). Fixes found here were found on the E4 bank and must be tested on a fresh one.

    taskset -c 6,7 .venv/bin/python experiments/E4/false_holds.py \\
        --out docs/experiments/E4/false_holds.md --fig docs/experiments/E4/false_holds.svg
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

# cause -> (label, questions, what went wrong, candidate fix, where the fix lives, a metric misreading (O3)?)
CAUSES = {
    "sales_default": ("'Product sales' read as net", ["F-H01", "F-H04", "F-H07", "F-L13"],
                      "the question says only 'product sales' (产品/商品销售额) or 'before any cancellations'; the "
                      "generic-term default reads net sales, and claim_contradictions treats that default like an "
                      "explicit reading, so a declared gross_product_sales (which suppresses net) is 'not the question "
                      "asked' (src/dsharness/tools/analytics.py:497-499). H v0.5 F23 says a generic reading must not "
                      "contradict a declared metric that suppresses it; only its qualifier half ('gross'/毛 just "
                      "before the word) was built. The authors meant gross: arm N answered gross and was right. On "
                      "F-H04 and F-L13 nine arm-H attempts switched from the right gross answer to net after the hold "
                      "and were delivered wrong (VERIFIED). F-H01's 'before any cancellations' also misses the gross "
                      "term, which expects 'before cancellations'. F-H07 is mixed: two holds on this reading (with an "
                      "unread 只计算带客户编号的订单), one quarter ratio without a month column, one undeclared",
                      "a default (generic-only) reading is satisfied by a declared metric that suppresses it; "
                      "gross term widened to 'before any cancellations'", "harness + onboarding", True),
    "net_defined": ("Net defined as gross less cancellations", ["F-H23", "F-H32", "F-L09"],
                    "the question names net product sales and then defines it ('meaning gross product sales less the "
                    "value of cancellations', 即总产品销售额减去取消金额, 'use each quarter's gross product sales less "
                    "its cancellation value'); both terms match, gross_product_sales suppresses net_product_sales, so "
                    "the reading becomes gross and the correct net answer is held",
                    "a metric named inside a definition of another explains it, it does not replace it; add "
                    "'less (the value of / its) cancellation(s)' to the net terms", "harness + onboarding", True),
    "per_month_yoy": ("Per-month change vs a year earlier", ["F-H10", "F-H30", "F-L12", "F-L31"],
                      "growth and difference steps compare one pair of months or collapse them; no step keeps one row "
                      "per month with its year-ago base, so re-execution fails ('several rows for the single value') "
                      "or the answer stays exploratory. Three Codex attempts certified each month as its own pair "
                      "(difference a/b) and then answered all months at once, rows no certificate covers. E3a's "
                      "R-H07 (change per month) asked for the same step",
                      "a per-month change step with a base offset (12 months for year on year, 1 for month on "
                      "month), like cumsum", "harness", False),
    "month_scope": ("'2025 from Jan to Nov' read as all 2025", ["F-H18", "F-H36"],
                    "months_from_text finds the scope ambiguous; the fallback in claim_contradictions then takes "
                    "every month the text names, and 'in 2025' names the whole year, so a declared January to "
                    "November is held as narrower than asked (src/dsharness/tools/analytics.py:502-508). F26 "
                    "covered '2025 through November' only",
                    "parse 'in YEAR from MONTH to MONTH' as one range; an explicit month range overrides a bare year "
                    "in the fallback", "harness", False),
    "dim_values": ("Dimension value the parser misses", ["F-H03", "F-H25"],
                   "F-H03: 物料编码 85123A names an item code, but no sku value or Chinese name for item code is "
                   "recognised, so the declared sku filter counts as 'not named'; F-H25: 'Guest orders should be left "
                   "out' names guests only to exclude them, and the parser counts both customer types as asked",
                   "read item codes after 物料编码 / 商品编码 / item code; values inside an exclusion clause ('left out', "
                   "'excluding', 不含) are excluded, not named", "harness", False),
    "metric_terms": ("Metric phrase missing or misread", ["F-L03", "F-L26", "F-L40"],
                     "F-L03: 'identified customers' (a customer-type value) matches the active-customer term, which "
                     "suppresses orders; F-L40: 'had at least one product line in an earlier month' defines repeat "
                     "customers in words the repeat terms do not cover, so 'active customers' wins; F-L26: "
                     "售出多少件商品 is not a units term (the list has 卖出多少件 and 售出的数量), so only orders is asked",
                     "term coverage test with these paraphrases; a dimension value phrase does not count as a metric "
                     "mention", "onboarding + harness", True),
    "reformatted": ("Final answer not the certified rows", ["F-L08"],
                    "a VERIFIED submission, then the final answer restated the share at full precision (794 / 973), "
                    "which no certificate covers (delivery rule S2)", "none (agent)", "agent", False),
}
Q2C = {q: c for c, v in CAUSES.items() for q in v[1]}
COL_N, COL_H = "#2a78d6", "#eb6834"                           # arm slots used by every E1/E3a chart
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
O3_ESTIMATE = 0.07                                            # share of fresh questions expected to resolve to another metric


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
    panels = (("Arm H: correct but held", "H_held", "H_n", COL_H),
              ("Arm H: wrong and delivered", "H_silent", "H_n", COL_H),
              ("Arm N: wrong and delivered", "N_silent", "N_n", COL_N))
    W, top, rh, lw, pw, gap = 1180, 92, 34, 320, 230, 50
    H = top + rh * len(order) + 40
    vmax = max(t[c][key] for c in order for _, key, _, _ in panels)
    vmax = ((vmax + 9) // 10) * 10
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
         'font-family="system-ui, -apple-system, Segoe UI, sans-serif">',
         f'<rect width="100%" height="100%" fill="{SURFACE}"/>',
         f'<text x="16" y="20" font-size="12" fill="{INK2}">Attempts per root cause (2 agents x 3 repeats per question; '
         'causes assigned post hoc by the designer; one scale for all three panels).</text>',
         f'<text x="16" y="36" font-size="12" fill="{INK2}">Left: correct answers arm H did not deliver (false holds). '
         'Middle and right: wrong answers delivered (silent errors), arm H with the harness and arm N without.</text>']
    for i, (title, key, nkey, col) in enumerate(panels):
        x0 = lw + i * (pw + gap)
        o.append(f'<text x="{x0}" y="{top - 26}" font-size="13" font-weight="600" fill="{INK}">{title}</text>')
        for v in range(0, vmax + 1, 10):
            x = x0 + pw * v / vmax
            o.append(f'<line x1="{x:.1f}" y1="{top - 8}" x2="{x:.1f}" y2="{top + rh * len(order)}" stroke="{GRID}"/>')
            o.append(f'<text x="{x:.1f}" y="{top + rh * len(order) + 16}" font-size="10" fill="{INK2}" text-anchor="middle">{v}</text>')
        for j, c in enumerate(order):
            y = top + rh * j + 8
            v = t[c][key]
            w = pw * v / vmax
            n = t[c][nkey]
            tip = f"{labels[c]}, {title.lower()}: {v} of {n} attempts"
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
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runs-root", type=Path, default=ROOT / "experiments/E4/runs")
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
    nq_all = sum(t[c]["questions"] for c in t)
    term = [c for c, v in CAUSES.items() if v[5]]
    term_q = sum(len(CAUSES[c][1]) for c in term)
    term_held = sum(t[c]["H_held"] for c in term)
    term_silent = sum(t[c]["H_silent"] for c in term)
    L = ["# E4 false holds by root cause (pre-registered secondary; causes post hoc, generated by false_holds.py)", "",
         f"Arm H held {tot['H_held']} correct answers ({tot['H_held_FAIL']} with a failed check, "
         f"{tot['H_held_INCONCLUSIVE']} inconclusive). All of them are on the {len(Q2C) - 1} held questions below "
         f"(F-L13, listed with its cause, has no hold but six wrong deliveries); on the other "
         f"{t['other']['questions']} questions arm H delivered {t['other']['H_delivered_ok']} of {t['other']['H_n']} "
         f"attempts correctly and held no correct answer. The questions below carry {tot['H_silent'] - t['other']['H_silent']} of arm "
         f"H's {tot['H_silent']} silent errors and {tot['N_silent'] - t['other']['N_silent']} of arm N's "
         f"{tot['N_silent']} ({tot['N_silent_grader']} of arm N's are right values the registered grader cannot read, "
         "designer note `grader?`). Causes were assigned by the designer after reading the evidence (post hoc); fixes "
         "found here must be tested on a fresh bank.", "",
         f"![false holds by root cause]({a.fig.name})", "",
         "| root cause | questions | H correct held (FAIL / INCONCL.) | H silent errors | N silent errors (of which grader?) | candidate fix | where |",
         "|---|---|---|---|---|---|---|"]
    for c, (label, qs, why, fix, where, _) in CAUSES.items():
        k = t[c]
        L.append(f"| {label} | {len(qs)} | {k['H_held']} of {k['H_n']} ({k['H_held_FAIL']} / {k['H_held_INCONCLUSIVE']}) | "
                 f"{k['H_silent']} | {k['N_silent']} of {k['N_n']} ({k['N_silent_grader']}) | {fix} | {where} |")
    k = t["other"]
    L += [f"| other questions | {k['questions']} | {k['H_held']} of {k['H_n']} | {k['H_silent']} | {k['N_silent']} of {k['N_n']} ({k['N_silent_grader']}) | | |", "",
          "## Term misreadings against the O3 estimate", "",
          f"O3 estimated that {O3_ESTIMATE:.0%} of fresh questions resolve to another metric, about "
          f"{O3_ESTIMATE * nq_all:.1f} of these {nq_all}, or about {O3_ESTIMATE * nq_all * 6:.0f} held attempts if every attempt "
          f"on them were held. The metric misreadings found here ({', '.join(CAUSES[c][0] for c in term)}) "
          f"cover {term_q} questions ({term_q / nq_all:.0%}). They account for {term_held} of the {tot['H_held']} false holds "
          f"and {term_silent} of arm H's {tot['H_silent']} silent errors.", "",
          "## Why hold_reasons.md shows 'answer.reconcile INCONCLUSIVE: (no reason recorded)'", "",
          "Not a harness bug. When the declared metric_call reconciles but contradicts the question, analytics.submit_answer "
          "records the sentence in the certificate's `reasons` and in the reconcile evidence's `contradictions` list "
          "(src/dsharness/tools/analytics.py:871 computes them, 917-918 stores them, 966-970 writes the reason). experiments/E1b/hold_reasons.py:41-49 (`_why`) reads only "
          "`reason` and the key/value counts of the evidence, so every contradiction hold prints as 'no reason recorded'. "
          "The causes above come from the certificates' `reasons`.", "",
          "## What went wrong, per cause", ""]
    for c, (label, qs, why, fix, where, _) in CAUSES.items():
        L.append(f"- **{label}** ({', '.join(qs)}): {why}.")
    a.out.write_text("\n".join(L) + "\n")
    print("->", a.out, a.fig)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
