"""E3a: turn the authors' raw YAML into a draft question bank and a review sheet for the designer.

Same procedure as E1b with the retail brief (docs/experiments/E3a/author_brief.md; Luna and Haiku in
the bwrap sandbox). Each question gets qid R-L## (Luna) or R-H## (Haiku). The script builds the
evaluator database from the adjudicated truth build B (`truth_lines`), runs every truth SQL on it,
checks the brief's format and period rules, and lists each question's closest earlier question by
text similarity. A question whose truth equals the truth of an earlier question (same keys; values
equal, or equal after share <-> percent, which the grader treats alike) is the same item reworded:
it is marked dropped. The designer reads review_draft.md, writes REVIEW.md (truth fixes, other
drops) and then bank.yaml.

    .venv/bin/python experiments/E1/run_authors.py --brief docs/experiments/E3a/author_brief.md \\
        --out experiments/E3a/authors
    .venv/bin/python experiments/E3a/build_bank.py --authors experiments/E3a/authors
"""
from __future__ import annotations

import argparse
from difflib import SequenceMatcher
import importlib.util
from pathlib import Path
import re
import sys

import duckdb
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dsharness.bench import census as C  # noqa: E402
import world as W  # noqa: E402

_spec = importlib.util.spec_from_file_location("e1b_build_bank", ROOT / "experiments/E1b/build_bank.py")
E1B = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(E1B)
norm, parse_raw, same_truth, truth_check = E1B.norm, E1B.parse_raw, E1B.same_truth, E1B.truth_check

PREFIX = {"luna": "R-L", "haiku": "R-H"}
UNITS = {"count", "gbp", "share", "percent", "other"}
FIRST, LAST, FIRST_CUSTOMER = "2023-12", "2025-11", "2024-01"
CUSTOMER_WORDS = re.compile(r"new customer|repeat|returning|first[- ]time|新客|新顾客|回头|复购|老客|首次", re.I)


def problems(q: dict) -> list[str]:
    out = []
    for f in ("question", "keys", "unit", "months", "truth_sql"):
        if q.get(f) is None:
            out.append(f"missing {f}")
    if q.get("lang") not in ("en", "zh"):
        out.append(f"lang {q.get('lang')!r}")
    if q.get("unit") not in UNITS:
        out.append(f"unit {q.get('unit')!r}")
    ms = q.get("months") or []
    if len(ms) != 2 or not all(isinstance(m, str) and re.fullmatch(r"\d{4}-\d{2}", m) for m in ms):
        out.append(f"months {ms!r}")
    else:
        first = FIRST_CUSTOMER if CUSTOMER_WORDS.search(q.get("question", "")) else FIRST
        if ms[0] < first or ms[1] > LAST or ms[0] > ms[1]:
            out.append(f"months {ms} break the period rule ({first}..{LAST})")
    return out


def truth_values(q: dict, con) -> dict[str, float] | None:
    try:
        df = con.execute(q["truth_sql"]).df()
    except Exception:                                         # noqa: BLE001 - reported by truth_check
        return None
    keys = list(q.get("keys") or [])
    if any(k not in df.columns for k in (*keys, "value")) or df["value"].isna().any():
        return None
    return {"|".join(C._norm_key(r[k], W.ALIASES) for k in keys): float(r["value"]) for _, r in df.iterrows()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--authors", type=Path, nargs="+", default=[ROOT / "experiments/E3a/authors"])
    ap.add_argument("--out", type=Path, default=ROOT / "experiments/E3a/questions")
    ap.add_argument("--truth", type=Path, default=W.TRUTH_B, help="adjudicated truth_lines.parquet")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    W.TRUTH_B = a.truth
    evaldb = W.evaluator_db(W.WS, a.out / "evaluator.duckdb")
    rows, bank, seen, n_by_author = [], [], {}, {a: 0 for a in PREFIX}
    con = duckdb.connect(str(evaldb), read_only=True)
    for adir in a.authors:
        for author, pre in PREFIX.items():
            raw = (adir / author / "questions_raw.yaml").read_text(encoding="utf-8")
            for q in parse_raw(raw):
                n_by_author[author] += 1                       # numbered across rounds, never reused
                qid = f"{pre}{n_by_author[author]:02d}"
                entry = {"qid": qid, "set": "R", "author": author, "lang": q.get("lang", "en"),
                         "question": q.get("question"), "keys": list(q.get("keys") or []), "unit": q.get("unit"),
                         "months": q.get("months"), "truth_sql": q.get("truth_sql"), "notes": q.get("notes")}
                tv = truth_values(q, con) if q.get("truth_sql") else None
                dup = next((k for k, v in seen.items() if same_truth(tv, v)), None)
                if dup:
                    entry["dropped"] = f"same truth as {dup}"
                probs = problems(q)
                status, detail = truth_check(q, con) if q.get("truth_sql") else ("ERROR", "no truth_sql")
                text = norm(q.get("question") or "")
                best, sim = max(((e, SequenceMatcher(None, norm(e["question"] or ""), text).ratio()) for e in bank),
                                key=lambda t: t[1], default=(None, 0.0))
                seen[qid] = tv
                bank.append(entry)
                rows.append((qid, entry, probs, status, detail, best, sim))
    con.close()
    (a.out / "bank_v0.yaml").write_text(yaml.safe_dump(bank, allow_unicode=True, sort_keys=False), encoding="utf-8")
    lines = ["# E3a review draft (generated; the designer writes REVIEW.md from it)", "",
             f"{len(rows)} questions. Similarity = text ratio to the closest earlier question (1.0 = identical).", "",
             "| qid | lang | keys | unit | months | truth | format | closest | sim | dropped |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for qid, e, probs, status, detail, best, sim in rows:
        lines.append(f"| {qid} | {e['lang']} | {e['keys']} | {e['unit']} | {e['months']} | {status} | "
                     f"{'; '.join(probs) or 'ok'} | {best['qid'] if best else ''} | {sim:.2f} | "
                     f"{e.get('dropped', '')} |")
    lines += ["", "## Questions", ""]
    for qid, e, probs, status, detail, best, sim in rows:
        lines += [f"### {qid}", "", f"- question: {e['question']}",
                  f"- closest ({best['qid']}, {sim:.2f}): {best['question']}" if best else "- closest: none",
                  f"- truth {status}: {detail}", f"- author notes: {e['notes']}", "",
                  "```sql", (e["truth_sql"] or "").strip(), "```", ""]
    (a.out / "review_draft.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    bad = sum(1 for r in rows if r[3] != "OK" or r[2])
    print(f"{len(rows)} questions, {sum('dropped' in r[1] for r in rows)} same-truth duplicates, "
          f"{bad} with truth or format problems, {sum(1 for r in rows if r[6] >= 0.8)} with an earlier "
          f"question at similarity >= 0.8 -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
