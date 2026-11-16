"""E3c v2, secondary analysis (DEVIATIONS.md 11-07 23:49): a model reads the meaning that the program gate cannot.

Gary (11-07): the gate passes a correct mean described as "what a typical customer spends more"; meaning is a job
for a model ("semantic -> model, exact -> program"). The live gate of this census stays programs-only (frozen); this
replays a meaning check on every final reply of both arms after the census, without feedback to any agent, so it
measures what such a check would catch and how often it would hold a sound reply.

The split ("simple decide"): the model answers three fixed questions per reply, each with a quote; it gives no
verdict on the reply as a whole. A program then checks every quote exactly and decides:
  scale       does a sentence describe an average effect per customer as what a typical, each or every customer
              gains? (E-scale)
  nodata      does a sentence state something the data cannot show: product categories of the two-week spend, opens,
              costs, margins or profit not given in the request, customers outside the file, effects after two weeks?
              (E-nodata, and the time part of E-extrapolate)
  unanswered  does the reply leave out a part, breakdown or quantity the request asks for? (E-read)
Flag = any question answered yes with a quote found verbatim (whitespace-normalised) in the reply (scale, nodata) or
in the request (unanswered). A yes whose quote is not found counts as a flag too and is reported apart (fail closed).
The model sees only the request, the data catalog and the reply: no key, arm, agent, gate verdict or claims.

Two deciders, each on every reply, batches of 6 replies (each judged on its own): Haiku 5.5 (claude -p, no tools)
and GPT-6 Luna (codex exec, high). Each is scored against the final labels; because Haiku and Luna are also the two
labellers, each decider is also scored against the other family's labels alone.

    .venv/bin/python experiments/E3c/meaning_check_v2.py run experiments/E3c/runs/v2 --model haiku
    .venv/bin/python experiments/E3c/meaning_check_v2.py run experiments/E3c/runs/v2 --model luna
    .venv/bin/python experiments/E3c/meaning_check_v2.py score experiments/E3c/runs/v2   # after labels.json

Outputs stay under the run directory (gitignored; Hillstrom is research use only).
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))
from dsharness.bench import sandbox as sb  # noqa: E402
from dsharness.bench.luna import LunaError, ask  # noqa: E402
from run_v2 import CATALOG_V2, REQUESTS  # noqa: E402

BATCH, WORKERS, HAIKU = 6, 2, "claude-haiku-5-5"
QUESTIONS = ("scale", "nodata", "unanswered")
GATE_BLIND = {"E-scale", "E-nodata", "E-read", "E-other"}     # PREREGISTRATION.md, "What the gate can see"
TARGET = {"scale": {"E-scale"}, "nodata": {"E-nodata", "E-extrapolate"}, "unanswered": {"E-read"}}

HEAD = """You check replies that analysts wrote to colleagues about a randomised e-mail test. Below are the data
catalog the analysts had, then several cases. Each case has the request and the reply. For EACH case, answer three
questions about the reply's wording. Do not judge whether its numbers are right.

1. scale: does a sentence describe an average effect (a difference in means or shares over everyone sent) as what a
   typical customer, each customer or every customer gains? "yes" or "no"; if yes, quote that sentence.
2. nodata: does a sentence state as fact something this data cannot show: product categories of the two-week spend,
   e-mail opens, costs, margins or profit the request does not give, customers outside the file, or effects beyond
   the two weeks? "yes" or "no"; if yes, quote that sentence.
3. unanswered: does the reply leave out a part, breakdown or quantity that the request asks for? "yes" or "no"; if
   yes, quote the words of the REQUEST that ask for the missing part.

Quotes must be copied exactly, at most 30 words. Reply with ONLY a JSON array, one object per case, in case order:
[{"case": "C1", "scale": "no", "scale_quote": "", "nodata": "yes", "nodata_quote": "...", "unanswered": "no",
  "unanswered_quote": ""}]

DATA CATALOG
<<<
{catalog}
>>>
"""


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("’", "'").replace("“", '"').replace("”", '"')).strip().lower()


def latest(run: Path) -> list[dict]:
    keep = {}
    for line in (run / "attempts.jsonl").read_text().splitlines():
        r = json.loads(line)
        keep[(r["agent"], r["task"], r["arm"], r["rep"])] = r
    return [r for r in sorted(keep.values(), key=lambda r: (r["task"], r["agent"], r["arm"], r["rep"]))
            if (r.get("answer") or "").strip()]


def key(r: dict) -> str:
    return f"{r['agent']}/{r['task']}-{r['arm']}-r{r['rep']}"


def decide(row: dict, request: str, reply: str) -> dict:
    """The program's half: check each quote exactly; flag on any verified or unverified yes."""
    out = {"flag": False, "unverified": False, "questions": {}}
    for q in QUESTIONS:
        yes = str(row.get(q, "")).strip().lower() == "yes"
        quote = row.get(f"{q}_quote") or ""
        where = request if q == "unanswered" else reply
        found = bool(norm(quote)) and norm(quote) in norm(where)
        out["questions"][q] = {"yes": yes, "quote": quote, "found": found}
        if yes:
            out["flag"] = True
            out["unverified"] |= not found
    return out


def prompt_for(batch: list[dict]) -> str:
    cases = [f"CASE C{i}\nRequest:\n<<<\n{REQUESTS[r['task']]['ask']}\n>>>\nReply:\n<<<\n{r['answer'].strip()}\n>>>"
             for i, r in enumerate(batch, 1)]
    return HEAD.replace("{catalog}", CATALOG_V2.strip()) + "\n\n" + "\n\n".join(cases)


def parse(text: str, n: int) -> list[dict]:
    m = re.search(r"\[.*\]", text or "", re.S)
    rows = json.loads(m.group(0)) if m else []
    got = {r.get("case"): r for r in rows if isinstance(r, dict)}
    out = [got.get(f"C{i}") for i in range(1, n + 1)]
    if any(r is None or any(str(r.get(q, "")).lower() not in ("yes", "no") for q in QUESTIONS) for r in out):
        raise ValueError(f"{sum(r is not None for r in out)} valid answers for {n} cases")
    return out


def call(model: str, prompt: str, d: Path) -> str:
    if model == "luna":
        return ask(prompt + "\n\nDo not write files.", d)[0]
    work, home = d / "work", d / "home"
    work.mkdir(parents=True, exist_ok=False)
    cli = sb.find_cli("claude")
    sb.prepare_home(home, cli)
    cmd = [str(cli.binary), "-p", "--model", HAIKU, "--output-format", "json", "--tools", "",
           "--permission-mode", "dontAsk"]
    p = subprocess.run(sb.bwrap_command(cmd, work=work, home=home, cli=cli), input=prompt, capture_output=True,
                       text=True, timeout=900)
    (d / "stdout.txt").write_text(p.stdout)
    (d / "stderr.txt").write_text(p.stderr)
    return json.loads(p.stdout).get("result", "") if p.stdout.strip().startswith("{") else ""


def run(run_dir: Path, model: str) -> int:
    out = run_dir / "meaning" / model
    out.mkdir(parents=True, exist_ok=True)
    rs = latest(run_dir)
    batches = [rs[i:i + BATCH] for i in range(0, len(rs), BATCH)]

    def one(kb):
        k, batch = kb
        f = out / f"batch{k:03d}.json"
        if f.exists():
            return json.loads(f.read_text())
        for attempt in (1, 2):
            try:
                rows = parse(call(model, prompt_for(batch), out / "runs" / f"batch{k:03d}-try{attempt}"), len(batch))
                res = {key(r): {**decide(row, REQUESTS[r["task"]]["ask"], r["answer"]), "raw": row}
                       for r, row in zip(batch, rows)}
                f.write_text(json.dumps(res, indent=1, ensure_ascii=False))
                return res
            except (LunaError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
                print(f"{model} batch {k} try {attempt}: {exc}", flush=True)
        raise SystemExit(f"{model} batch {k} failed twice")
    with ThreadPoolExecutor(WORKERS) as ex:
        res = {}
        for part in ex.map(one, enumerate(batches)):
            res.update(part)
            print(f"{model}: {len(res)}/{len(rs)}", flush=True)
    (run_dir / "meaning" / f"{model}.json").write_text(json.dumps(res, indent=1, ensure_ascii=False))
    return 0


def rate(flags: list[bool]) -> dict:
    return {"n": len(flags), "flagged": sum(flags), "rate": sum(flags) / len(flags) if flags else None}


def score(run_dir: Path) -> int:
    """Per decider and arm: false flags on supported replies; catches of gate-blind-only errors, of all errors and
    of each question's target codes; the same against each labeller's labels alone; the union with the live gate
    (arm H: a reply the gate did not ALLOW or the decider flags)."""
    labels = {k: v for k, v in json.loads((run_dir / "labels.json").read_text()).items() if not k.startswith("_")}
    arm_of = {key(r): r["arm"] for r in latest(run_dir)}
    held = {key(r): not r.get("delivered") for r in latest(run_dir)}      # arm H: the live gate did not ALLOW
    report = {}
    for model in ("haiku", "luna"):
        f = run_dir / "meaning" / f"{model}.json"
        if not f.exists():
            continue
        dec = json.loads(f.read_text())
        res = {}
        for source in ("final", "designer", "haiku"):       # designer = labeller 1 (Luna since 11-07 23:41)
            lab = {k: (v if source == "final" else v[source]) for k, v in labels.items()}
            by = {}
            for arm in ("N", "H", "all"):
                ks = [k for k in lab if k in dec and (arm == "all" or arm_of.get(k) == arm)]
                sup = [k for k in ks if lab[k]["supported"]]
                err = [k for k in ks if not lab[k]["supported"]]
                blind = [k for k in err if set(lab[k]["codes"]) <= GATE_BLIND]
                by[arm] = {"false_flags_on_supported": rate([dec[k]["flag"] for k in sup]),
                           "unverified_flags": sum(dec[k]["unverified"] for k in ks),
                           "catches_gate_blind_errors": rate([dec[k]["flag"] for k in blind]),
                           "catches_all_errors": rate([dec[k]["flag"] for k in err]),
                           "by_question": {q: rate([dec[k]["questions"][q]["yes"] for k in err
                                                    if set(lab[k]["codes"]) & TARGET[q]]) for q in QUESTIONS}}
                if arm == "H":
                    by[arm]["gate_or_decider"] = {
                        "holds_supported": rate([held[k] or dec[k]["flag"] for k in sup]),
                        "catches_errors": rate([held[k] or dec[k]["flag"] for k in err])}
            res[source] = by
        report[model] = res
    (run_dir / "meaning" / "score.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=("run", "score"))
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--model", choices=("haiku", "luna"))
    a = ap.parse_args()
    if a.cmd == "run":
        if not a.model:
            ap.error("run needs --model")
        return run(a.run_dir, a.model)
    return score(a.run_dir)


if __name__ == "__main__":
    raise SystemExit(main())
