"""E1b: turn the authors' raw YAML into a draft question bank and a review sheet for the designer.

Same brief and authors as E1 (docs/experiments/E1/author_brief.md; Luna and Haiku in the bwrap
sandbox), new runs. Each question gets qid C-L## (Luna) or C-H## (Haiku). The script runs every
truth SQL on the evaluator database, checks the brief's format and period rules, and lists each new
question's closest E1 question by text similarity. A question whose truth equals the truth of an
E1 question or of an earlier E1b question (same keys; values equal, or equal after share <-> percent
or fen <-> CNY, which the grader treats alike) is the same item reworded: it is marked dropped. The
designer reads review_draft.md, writes REVIEW.md (truth fixes, other drops) and then bank.yaml.

    .venv/bin/python experiments/E1/run_authors.py --out experiments/E1b/authors
    .venv/bin/python experiments/E1/run_authors.py --out experiments/E1b/authors2      # second round
    .venv/bin/python experiments/E1b/build_bank.py --authors experiments/E1b/authors experiments/E1b/authors2
"""
from __future__ import annotations

import argparse
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
import sys

import duckdb
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from dsharness.bench import census as C  # noqa: E402

E1_BANK = ROOT / "experiments/E1/questions/bank.yaml"
E1_TRUTH = ROOT / "experiments/E1/runs/main/truth.json"
EVALDB = ROOT / "experiments/E1/questions/evaluator.duckdb"
PREFIX = {"luna": "C-L", "haiku": "C-H"}
UNITS = {"count", "fen", "cny", "share", "percent", "minutes", "other"}


def parse_raw(text: str) -> list[dict]:
    m = re.search(r"```(?:yaml)?\s*\n(.*?)```", text, re.S)
    data = yaml.safe_load(m.group(1) if m else text)
    if not isinstance(data, list):
        raise ValueError("author output is not a YAML list")
    return data


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower()).strip()


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
        last = "2026-04" if re.search(r"minute|watch|观看|分钟", q.get("question", ""), re.I) else "2026-05"
        if ms[0] < "2025-01" or ms[1] > last:
            out.append(f"months {ms} break the period rule (2025-01..{last})")
    return out


def truth_check(q: dict, con) -> tuple[str, str]:
    try:
        df = con.execute(q["truth_sql"]).df()
    except Exception as exc:                                  # noqa: BLE001 - report every SQL failure
        return "ERROR", str(exc).splitlines()[0][:200]
    keys = list(q.get("keys") or [])
    missing = [k for k in (*keys, "value") if k not in df.columns]
    extra = [c for c in df.columns if c not in (*keys, "value")]
    if missing or extra:
        return "FAIL", f"columns {list(df.columns)}; missing {missing}, extra {extra}"
    if df.empty:
        return "FAIL", "no rows"
    if df["value"].isna().any():
        return "FAIL", "NULL value"
    if keys and df.duplicated(keys).any():
        return "FAIL", "several rows per key"
    if not keys and len(df) != 1:
        return "FAIL", f"{len(df)} rows for a single value"
    head = df.head(4).to_dict("records")
    return "OK", f"{len(df)} rows; {head}"


def truth_values(q: dict, con) -> dict[str, float] | None:
    try:
        df = con.execute(q["truth_sql"]).df()
    except Exception:                                         # noqa: BLE001 - reported by truth_check
        return None
    keys = list(q.get("keys") or [])
    if any(k not in df.columns for k in (*keys, "value")):
        return None
    return {"|".join(C._norm_key(r[k]) for k in keys): float(r["value"]) for _, r in df.iterrows()}


def same_truth(a: dict | None, b: dict | None) -> bool:
    if not a or not b or set(a) != set(b):
        return False
    return any(all(abs(a[k] * f - b[k]) <= 1e-9 * max(1.0, abs(b[k])) for k in a) for f in (1.0, 100.0, 0.01))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--authors", type=Path, nargs="+", default=[ROOT / "experiments/E1b/authors"])
    ap.add_argument("--out", type=Path, default=ROOT / "experiments/E1b/questions")
    a = ap.parse_args()
    e1 = [q for q in C.load_questions(E1_BANK)]
    e1_truth = {k: {kk.lower(): vv for kk, vv in v.items()} for k, v in json.loads(E1_TRUTH.read_text()).items()}
    rows, bank, seen = [], [], {}
    con = duckdb.connect(str(EVALDB), read_only=True)
    for rnd, adir in enumerate(a.authors):
        for author, pre in PREFIX.items():
            raw = (adir / author / "questions_raw.yaml").read_text(encoding="utf-8")
            for i, q in enumerate(parse_raw(raw), 1):
                qid = f"{pre}{20 * rnd + i:02d}"
                entry = {"qid": qid, "set": "C", "author": author, "lang": q.get("lang", "en"),
                         "question": q.get("question"), "keys": list(q.get("keys") or []), "unit": q.get("unit"),
                         "months": q.get("months"), "truth_sql": q.get("truth_sql"), "notes": q.get("notes")}
                tv = truth_values(q, con) if q.get("truth_sql") else None
                dup = next((k for k, v in e1_truth.items() if same_truth(tv, v)), None) or \
                    next((k for k, v in seen.items() if same_truth(tv, v)), None)
                if dup:
                    entry["dropped"] = f"same truth as {dup}"
                seen[qid] = tv
                bank.append(entry)
                probs = problems(q)
                status, detail = truth_check(q, con) if q.get("truth_sql") else ("ERROR", "no truth_sql")
                best = max(e1, key=lambda e: SequenceMatcher(None, norm(e.question), norm(q.get("question") or "")).ratio())
                sim = SequenceMatcher(None, norm(best.question), norm(q.get("question") or "")).ratio()
                rows.append((qid, entry, probs, status, detail, best, sim))
    con.close()
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "bank_v0.yaml").write_text(yaml.safe_dump(bank, allow_unicode=True, sort_keys=False), encoding="utf-8")
    lines = ["# E1b review draft (generated; the designer writes REVIEW.md from it)", "",
             f"{len(rows)} questions. Similarity = text ratio to the closest E1 question (1.0 = identical).", "",
             "| qid | lang | keys | unit | months | truth | format | closest E1 | sim | dropped |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for qid, e, probs, status, detail, best, sim in rows:
        lines.append(f"| {qid} | {e['lang']} | {e['keys']} | {e['unit']} | {e['months']} | {status} | "
                     f"{'; '.join(probs) or 'ok'} | {best.qid} | {sim:.2f} | {e.get('dropped', '')} |")
    lines += ["", "## Questions", ""]
    for qid, e, probs, status, detail, best, sim in rows:
        lines += [f"### {qid}", "", f"- question: {e['question']}", f"- closest E1 ({best.qid}, {sim:.2f}): "
                  f"{best.question}", f"- truth {status}: {detail}", f"- author notes: {e['notes']}", "",
                  "```sql", (e["truth_sql"] or "").strip(), "```", ""]
    (a.out / "review_draft.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    bad = sum(1 for r in rows if r[3] != "OK" or r[2])
    print(f"{len(rows)} questions, {sum('dropped' in r[1] for r in rows)} same-truth duplicates, "
          f"{bad} with truth or format problems, "
          f"{sum(1 for r in rows if r[6] >= 0.8)} with an E1 question at similarity >= 0.8 -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
