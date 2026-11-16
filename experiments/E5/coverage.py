"""E5 phase 1: the request-coverage check, replayed offline on E3b v2's and E3c v2's finished replies.

    .venv/bin/python experiments/E5/coverage.py requests                 # request texts -> runs/phase1/requests.json
    .venv/bin/python experiments/E5/coverage.py extract --model luna     # parts of each request (sees the request only)
    .venv/bin/python experiments/E5/coverage.py cover   --model luna     # per reply: part covered? quote verified by program
    .venv/bin/python experiments/E5/coverage.py score                    # the pre-registered rule, results.md/json

Pre-registration: docs/experiments/E5/PREREGISTRATION.md. A model sees only the request (extract) or the reply and the
parts (cover): never the agent, the arm, the gate, a label or the other model. All model calls go through
dsharness.bench.llm (API, daily budget). Each call is cached on disk, so a stopped run resumes.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import sys

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dsharness.bench import llm  # noqa: E402

OUT = HERE / "runs" / "phase1"
EXPS = {"E3b": ROOT / "experiments/E3b", "E3c": ROOT / "experiments/E3c"}
SEEN_E3B = {"rp90": "Which of our customers are likely to buy from us again in the next three months?",
            "spend90": "Which of our customers are likely to spend at least GBP 500 with us in the next three months?"}
TYPES = ("figure", "breakdown", "horizon", "population", "decision", "caveat", "form")
WORKERS = 4

EXTRACT = """A stakeholder sent this request to a data analyst:

<<<
{request}
>>>

List the separate things a complete reply must deliver, using only what the request itself asks for or states: a
figure, a split or breakdown, a time span, who or what it covers, a yes/no decision, a form of output (a list, a
table, a range), or a caveat the requester asked for. Do not add what a good analyst might add on their own.
Each part is one short phrase in the requester's words, with one type from: {types}. Between 1 and 8 parts.

Reply with ONLY one JSON object:
{{"parts": [{{"id": "P1", "phrase": "...", "type": "figure"}}]}}"""

COVER = """An analyst replied to a request. Decide, for each numbered part the request asked for, whether the reply
covers it.

A part is covered when the reply gives what the part asks for, even briefly, or says plainly that it cannot be given
and why. It is not covered when the reply is silent on it, gives something different, or covers only a piece of it.

REPLY
<<<
{reply}
>>>

PARTS
{parts}

For every part give "covered": "yes" or "no". For "yes", "quote" is a sentence or table row copied EXACTLY from the
reply (at most 200 characters) that covers the part; for "no", "quote" is "".

Reply with ONLY one JSON object:
{{"parts": [{{"id": "P1", "covered": "yes", "quote": "..."}}]}}"""


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[*_`|#>]", " ", s or "")).strip().lower()


def load_json(p: Path, default=None):
    return json.loads(p.read_text()) if p.exists() else default


def requests() -> dict[str, str]:
    """{"E3b/ha01": request text, ...} for every request that has a finished reply."""
    out = {}
    for exp, d in EXPS.items():
        if not (d / "bank_v2.yaml").exists():   # E3c's bank is not distributed in the public copy (docs/DATA.md)
            raise SystemExit(f"{d.name}/bank_v2.yaml is not distributed with the public copy; see docs/DATA.md")
        bank = {r["id"]: r["request"] for r in yaml.safe_load((d / "bank_v2.yaml").read_text())}
        if exp == "E3b":
            bank.update(SEEN_E3B)
        else:
            sys.path.insert(0, str(d))
            from tasks import TASKS  # noqa: E402
            bank.update({k: v.ask for k, v in TASKS.items()})
        for r in map(json.loads, (d / "runs/v2/attempts.jsonl").read_text().splitlines()):
            if r.get("status") == "OK" and r["task"] in bank:
                out[f"{exp}/{r['task']}"] = bank[r["task"]]
    return out


def replies() -> list[dict]:
    """One row per finished reply: key, exp, task, agent, arm, text."""
    rows = []
    for exp, d in EXPS.items():
        for r in map(json.loads, (d / "runs/v2/attempts.jsonl").read_text().splitlines()):
            if r.get("status") == "OK" and r.get("answer"):
                rows.append({"key": f"{r['agent']}/{r['task']}-{r['arm']}-r{r['rep']}", "exp": exp, "task": r["task"],
                             "agent": r["agent"], "arm": r["arm"], "text": r["answer"]})
    return rows


def parse(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        return json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        return {}


def run_parallel(fn, items):
    with ThreadPoolExecutor(WORKERS) as ex:
        return list(ex.map(fn, items))


def cmd_requests(_a) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    reqs = requests()
    (OUT / "requests.json").write_text(json.dumps(reqs, indent=1, ensure_ascii=False))
    print(f"{len(reqs)} requests")


def cmd_extract(a) -> None:
    reqs = load_json(OUT / "requests.json")
    d = OUT / "extract" / a.model
    d.mkdir(parents=True, exist_ok=True)

    def one(item):
        key, request = item
        f = d / (key.replace("/", "_") + ".json")
        if f.exists():
            return
        for _try in (1, 2):
            row = parse(llm.ask(EXTRACT.format(request=request.strip(), types=", ".join(TYPES)), a.model,
                                purpose="E5 extract"))
            parts = row.get("parts")
            if isinstance(parts, list) and 1 <= len(parts) <= 8 and all(
                    isinstance(p, dict) and p.get("phrase") for p in parts):
                parts = [{"id": f"P{i}", "phrase": str(p["phrase"]), "type": str(p.get("type", ""))}
                         for i, p in enumerate(parts, 1)]
                f.write_text(json.dumps({"request": request, "parts": parts}, indent=1, ensure_ascii=False))
                return
        raise SystemExit(f"{key}: no valid part list twice")
    run_parallel(one, list(reqs.items()))
    print(f"{len(list(d.glob('*.json')))} part lists ({a.model})")


def cmd_cover(a) -> None:
    ext = a.parts_from or a.model                    # whose part lists: its own by default
    d = OUT / "cover" / f"{a.model}_on_{ext}"
    d.mkdir(parents=True, exist_ok=True)
    rows = replies()

    def one(r):
        f = d / (r["key"].replace("/", "_") + f"_{r['exp']}.json")
        if f.exists():
            return
        pf = OUT / "extract" / ext / (f"{r['exp']}/{r['task']}".replace("/", "_") + ".json")
        parts = json.loads(pf.read_text())["parts"]
        plist = "\n".join(f"{p['id']}. {p['phrase']}" for p in parts)
        for _try in (1, 2):
            row = parse(llm.ask(COVER.format(reply=r["text"].strip(), parts=plist), a.model, purpose="E5 cover"))
            got = {p.get("id"): p for p in row.get("parts", []) if isinstance(p, dict)}
            if set(got) >= {p["id"] for p in parts} and all(
                    str(got[p["id"]].get("covered", "")).lower() in ("yes", "no") for p in parts):
                res = []
                for p in parts:
                    g = got[p["id"]]
                    yes = str(g["covered"]).lower() == "yes"
                    quote = g.get("quote") or ""
                    found = yes and bool(norm(quote)) and norm(quote) in norm(r["text"])
                    res.append({"id": p["id"], "phrase": p["phrase"], "said_yes": yes, "found": found, "quote": quote[:200]})
                f.write_text(json.dumps({"key": r["key"], "exp": r["exp"], "parts": res}, indent=1, ensure_ascii=False))
                return
        raise SystemExit(f"{r['key']}: no valid cover answer twice")
    run_parallel(one, rows)
    print(f"{len(list(d.glob('*.json')))} replies covered ({a.model} on {ext})")


def flag(rec: dict) -> tuple[bool, bool]:
    """(flagged, any part lost only because its quote was not found). A 'yes' with no verbatim quote is a gap."""
    gaps = [p for p in rec["parts"] if not (p["said_yes"] and p["found"])]
    return bool(gaps), any(p["said_yes"] and not p["found"] for p in gaps)


def cmd_score(a) -> None:
    import statistics  # noqa: F401
    labels = {exp: json.loads((EXPS[exp] / "runs/v2/labels.json").read_text()) for exp in EXPS}
    kind = {"final": lambda v: v, "luna": lambda v: v.get("labeller1") or v.get("designer"), "haiku": lambda v: v.get("haiku")}
    runs = {p.name: p for p in sorted((OUT / "cover").glob("*"))}
    res: dict = {}
    for name, p in runs.items():
        recs = [json.loads(f.read_text()) for f in p.glob("*.json")]
        for lab, pick in kind.items():
            for scope in ("E3b", "E3c", "both"):
                n_sup = n_sup_flag = n_er = n_er_flag = 0
                for rec in recs:
                    if scope != "both" and rec["exp"] != scope:
                        continue
                    lv = pick(labels[rec["exp"]].get(rec["key"], {}) or {})
                    if not lv:
                        continue
                    f, _lost = flag(rec)
                    if lv["supported"]:
                        n_sup += 1
                        n_sup_flag += f
                    elif "E-read" in lv.get("codes", []):
                        n_er += 1
                        n_er_flag += f
                res[f"{name}|{lab}|{scope}"] = {
                    "supported": n_sup, "false_flags": n_sup_flag, "false_rate": n_sup_flag / n_sup if n_sup else None,
                    "e_read": n_er, "caught": n_er_flag, "catch_rate": n_er_flag / n_er if n_er else None}
    (OUT / "results.json").write_text(json.dumps(res, indent=1))
    lines = ["# E5 phase 1 results", "", "| run | labels | scope | false flags on supported | catches of E-read |", "|---|---|---|---|---|"]
    for k, v in res.items():
        name, lab, scope = k.split("|")
        fr = "—" if v["false_rate"] is None else f"{v['false_rate']:.1%} ({v['false_flags']}/{v['supported']})"
        cr = "—" if v["catch_rate"] is None else f"{v['catch_rate']:.0%} ({v['caught']}/{v['e_read']})"
        lines.append(f"| {name} | {lab} | {scope} | {fr} | {cr} |")
    # the pre-registered rule: one model; E3b and E3c; final labels and the other family's labels
    lines += ["", "## Rule (fixed before the run)", "", "| run | qualifies to hold |", "|---|---|"]
    other = {"luna": "haiku", "haiku": "luna"}
    for name in runs:
        model = name.split("_on_")[0]
        ok = all(res[f"{name}|{lab}|{sc}"]["false_rate"] is not None
                 and res[f"{name}|{lab}|{sc}"]["false_rate"] <= 0.05 and res[f"{name}|{lab}|{sc}"]["catch_rate"] >= 0.5
                 for lab in ("final", other[model]) for sc in ("E3b", "E3c"))
        lines.append(f"| {name} | {'yes' if ok else 'no'} |")
    (OUT / "results.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("requests")
    for c in ("extract", "cover"):
        s = sub.add_parser(c)
        s.add_argument("--model", choices=("luna", "haiku"), required=True)
        if c == "cover":
            s.add_argument("--parts-from", choices=("luna", "haiku"), default=None)
    sub.add_parser("score")
    a = ap.parse_args()
    {"requests": cmd_requests, "extract": cmd_extract, "cover": cmd_cover, "score": cmd_score}[a.cmd](a)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
