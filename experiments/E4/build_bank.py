"""E4: turn the authors' raw YAML into a draft question bank and a review sheet for the designer.

Same procedure as E3a (experiments/E3a/build_bank.py) with the E4 brief (docs/experiments/E4/author_brief.md;
Luna and Haiku in the bwrap sandbox, no check code, no v0.5 list). Each question gets qid F-L## (Luna) or
F-H## (Haiku). Every truth SQL runs on the evaluator database (build B, as E3a). A question is dropped
mechanically when its truth equals the truth of an earlier E4 question or of any E3a question (same keys;
values equal, or equal after share <-> percent): the E3a questions are the ones H v0.5 was fixed on, so they
cannot test it. The review sheet lists each question's closest E4 and closest E3a question by text. The
designer reads review_draft.md, writes REVIEW.md (truth fixes, other drops, ties at a ranking's cut-off) and
then bank.yaml.

    .venv/bin/python experiments/E1/run_authors.py --brief docs/experiments/E4/author_brief.md \\
        --out experiments/E4/authors
    .venv/bin/python experiments/E4/build_bank.py --authors experiments/E4/authors experiments/E4/authors2
"""
from __future__ import annotations

import argparse
from difflib import SequenceMatcher
import importlib.util
from pathlib import Path
import sys

import duckdb
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from dsharness.bench import census as C  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


W = _load("e4_world", ROOT / "experiments/E4/world.py")
E3A_BANK = _load("e3a_build_bank", ROOT / "experiments/E3a/build_bank.py")
norm, parse_raw, same_truth, truth_check = E3A_BANK.norm, E3A_BANK.parse_raw, E3A_BANK.same_truth, E3A_BANK.truth_check
problems = E3A_BANK.problems
PREFIX = {"luna": "F-L", "haiku": "F-H"}


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
    ap.add_argument("--authors", type=Path, nargs="+", default=[ROOT / "experiments/E4/authors"])
    ap.add_argument("--out", type=Path, default=ROOT / "experiments/E4/questions")
    ap.add_argument("--e3a-bank", type=Path, default=ROOT / "experiments/E3a/questions/bank_v0.yaml",
                    help="every question the E3a authors wrote, kept or not")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    evaldb = W.E3A.evaluator_db(W.WS, a.out / "evaluator.duckdb")
    con = duckdb.connect(str(evaldb), read_only=True)
    old = [q for q in yaml.safe_load(a.e3a_bank.read_text(encoding="utf-8")) if q.get("truth_sql")]
    old_truth = {q["qid"]: truth_values(q, con) for q in old}
    rows, bank, seen, n_by_author = [], [], {}, {a: 0 for a in PREFIX}
    for adir in a.authors:
        for author, pre in PREFIX.items():
            raw = (adir / author / "questions_raw.yaml").read_text(encoding="utf-8")
            for q in parse_raw(raw):
                n_by_author[author] += 1                       # numbered across rounds, never reused
                qid = f"{pre}{n_by_author[author]:02d}"
                entry = {"qid": qid, "set": "F", "author": author, "lang": q.get("lang", "en"),
                         "question": q.get("question"), "keys": list(q.get("keys") or []), "unit": q.get("unit"),
                         "months": q.get("months"), "truth_sql": q.get("truth_sql"), "notes": q.get("notes")}
                tv = truth_values(q, con) if q.get("truth_sql") else None
                dup = next((k for k, v in seen.items() if same_truth(tv, v)), None)
                dup = dup or next((f"E3a {k}" for k, v in old_truth.items() if same_truth(tv, v)), None)
                if dup:
                    entry["dropped"] = f"same truth as {dup}"
                probs = problems(q)
                status, detail = truth_check(q, con) if q.get("truth_sql") else ("ERROR", "no truth_sql")
                text = norm(q.get("question") or "")
                closest = lambda pool: max(((e, SequenceMatcher(None, norm(e["question"] or ""), text).ratio())  # noqa: E731
                                            for e in pool), key=lambda t: t[1], default=(None, 0.0))
                best, sim = closest(bank)
                best3, sim3 = closest(old)
                seen[qid] = tv
                bank.append(entry)
                rows.append((qid, entry, probs, status, detail, best, sim, best3, sim3))
    con.close()
    (a.out / "bank_v0.yaml").write_text(yaml.safe_dump(bank, allow_unicode=True, sort_keys=False), encoding="utf-8")
    lines = ["# E4 review draft (generated; the designer writes REVIEW.md from it)", "",
             f"{len(rows)} questions. Similarity = text ratio to the closest earlier E4 question and to the closest "
             "E3a question (1.0 = identical).", "",
             "| qid | lang | keys | unit | months | truth | format | closest | sim | closest E3a | sim | dropped |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for qid, e, probs, status, detail, best, sim, best3, sim3 in rows:
        lines.append(f"| {qid} | {e['lang']} | {e['keys']} | {e['unit']} | {e['months']} | {status} | "
                     f"{'; '.join(probs) or 'ok'} | {best['qid'] if best else ''} | {sim:.2f} | "
                     f"{best3['qid'] if best3 else ''} | {sim3:.2f} | {e.get('dropped', '')} |")
    lines += ["", "## Questions", ""]
    for qid, e, probs, status, detail, best, sim, best3, sim3 in rows:
        lines += [f"### {qid}", "", f"- question: {e['question']}",
                  f"- closest ({best['qid']}, {sim:.2f}): {best['question']}" if best else "- closest: none",
                  f"- closest E3a ({best3['qid']}, {sim3:.2f}): {best3['question']}" if best3 else "- closest E3a: none",
                  f"- truth {status}: {detail}", f"- author notes: {e['notes']}", "",
                  "```sql", (e["truth_sql"] or "").strip(), "```", ""]
    (a.out / "review_draft.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    bad = sum(1 for r in rows if r[3] != "OK" or r[2])
    print(f"{len(rows)} questions, {sum('dropped' in r[1] for r in rows)} same-truth duplicates (E4 or E3a), "
          f"{bad} with truth or format problems, {sum(1 for r in rows if r[6] >= 0.8)} with an earlier E4 question "
          f"and {sum(1 for r in rows if r[8] >= 0.8)} with an E3a question at similarity >= 0.8 -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
