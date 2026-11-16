"""E3b v2 harm: arm H's first answer against its final answer, for attempts the gate held or rejected at least once.

PREREGISTRATION.md, "Also reported": an error on the final answer that the first answer did not have counts as harm
(an error introduced after a gate reason; Luna's review of the plan, finding 4). Reported, not decided.

  build   <run>/first/: a run of the same shape whose arm H attempts with two or more gate versions hold their
          first version (versions/v001: answer.md, claims.json, files, predict.py code) as the work; every other slot
          has no work. The post-census steps then run on it exactly as on the run: type_numbers, truth_v2 --sealed,
          label_v2 sheets, luna_label_v2, label_v2 haiku / adjudicate / final. Labellers see no version number.
  report  <run>/first/harm.json and harm.md: per attempt, supported first -> final, and codes added or dropped.

    .venv/bin/python experiments/E3b/harm_v2.py build --run experiments/E3b/runs/v2
    .venv/bin/python experiments/E3b/harm_v2.py report --run experiments/E3b/runs/v2
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import analyze_v2 as A  # noqa: E402


def adir(run: Path, r: dict) -> Path:
    return run / "attempts" / r["agent"] / f"{r['task']}-{r['arm']}-r{r['rep']}"


def build(run: Path) -> int:
    first = run / "first"
    first.mkdir(exist_ok=False)
    shutil.copy(run / "run.json", first / "run.json")
    if (run / "warehouses").exists():
        (first / "warehouses").symlink_to((run / "warehouses").resolve())
    rows, n = [], 0
    for r in A.latest(run):
        vs = sorted((adir(run, r) / "versions").glob("v[0-9][0-9][0-9]")) if r["arm"] == "H" else []
        row = dict(r)
        if len(vs) >= 2 and (vs[0] / "answer.md").exists():
            work = adir(first, r) / "work"
            shutil.copytree(vs[0] / "code", work, dirs_exist_ok=False) if (vs[0] / "code").exists() \
                else work.mkdir(parents=True)
            for f in ("answer.md", "claims.json"):
                if (vs[0] / f).exists():
                    shutil.copy(vs[0] / f, work / f)
            if (vs[0] / "files").exists():
                shutil.copytree(vs[0] / "files", work, dirs_exist_ok=True)
            row["answer"] = (vs[0] / "answer.md").read_text(errors="replace")
            row["first_of"] = len(vs)
            n += 1
        else:                                     # no work: every post-census step skips the slot
            row["answer"] = ""
        rows.append(row)
    (first / "attempts.jsonl").write_text("".join(json.dumps(x) + "\n" for x in rows))
    print(f"{n} first answers of arm H -> {first}")
    return 0


def codes(run: Path, r: dict, labels: dict) -> tuple[bool | None, set]:
    lab = labels.get(A.key(r), {})
    tf = run / "truth" / A.truth_name(r)
    prog = set(json.loads(tf.read_text()).get("program_codes") or []) if tf.exists() else set()
    sup = None if "supported" not in lab else bool(lab["supported"]) and not prog
    return sup, set(lab.get("codes") or []) | prog


def report(run: Path) -> int:
    first = run / "first"
    lf, lr = (json.loads((d / "labels.json").read_text()) for d in (first, run))
    final = {A.key(r): r for r in A.latest(run)}
    rows = []
    for r in A.latest(first):
        if not r.get("first_of"):
            continue
        s1, c1 = codes(first, r, lf)
        s2, c2 = codes(run, final[A.key(r)], lr)
        rows.append({"attempt": A.key(r), "agent": r["agent"], "versions": r["first_of"], "first_supported": s1,
                     "final_supported": s2, "added": sorted(c2 - c1), "dropped": sorted(c1 - c2),
                     "harm": s1 is True and s2 is False})
    n = len(rows)
    tally = {k: sum(x[k] for x in rows) for k in ("harm",)}
    tally["fixed"] = sum(x["first_supported"] is False and x["final_supported"] is True for x in rows)
    (first / "harm.json").write_text(json.dumps({"n": n, **tally, "rows": rows}, indent=1))
    lines = ["# E3b v2: arm H first vs final answer", "",
             f"{n} attempts held or rejected at least once. Harm (supported first, not supported final): "
             f"{tally['harm']}; fixed (not supported first, supported final): {tally['fixed']}.", "",
             "| attempt | versions | first | final | codes added | codes dropped |", "|---|---|---|---|---|---|"]
    yn = {True: "supported", False: "not supported", None: "unlabelled"}
    for x in rows:
        lines.append(f"| {x['attempt']} | {x['versions']} | {yn[x['first_supported']]} | {yn[x['final_supported']]} | "
                     f"{', '.join(x['added']) or '-'} | {', '.join(x['dropped']) or '-'} |")
    (first / "harm.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines[:3]))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["build", "report"])
    ap.add_argument("--run", type=Path, required=True)
    a = ap.parse_args()
    return {"build": build, "report": report}[a.cmd](a.run)


if __name__ == "__main__":
    raise SystemExit(main())
