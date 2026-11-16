"""E3b v2: type what a final reply states without a claim (LABEL_GUIDE_v2.md, "Untyped statements").

After the census, before the truth script, for every reply in both arms. Each goes on a blind sheet: the request,
the reply, the `says` and values of the claims it already has, and the numbers no claim covers (the gate's own
f.numbers rule, with the catalog's facts); no agent, arm, gate verdict or outcome. GPT-6 Luna writes the claim for
every listed number and every other material statement without a claim, in numbers or in words ("about half",
"most", "will grow"), in CLAIMS.md format with the reply's words quoted in `says`, or skips it with a reason. A
program keeps a claim only if its `says` is verbatim in the reply, it passes the gate's schema check, and (when it
types a listed number) it covers that number; truth_v2.py grades the kept claims with the reply's own (marked
`posthoc`). Luna Review 2 (11-08, finding 2): numbers alone let verbal forecasts escape grading.

    .venv/bin/python experiments/E3b/type_numbers.py --run experiments/E3b/runs/v2
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import random
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))
from dsharness.bench import openai_api  # noqa: E402
from dsharness.readout import forecast_gate as G  # noqa: E402
from mcp_model import FACTS  # noqa: E402
from run_v2 import REQUESTS, claims_md  # noqa: E402

BATCH, WORKERS, SEED = 10, 2, 20261108
PROMPT = """You turn numbers in analysts' written replies into structured claims. `CLAIMS.md` (below) defines the claim
format. Each sheet below holds a request to an analyst at a UK gift wholesaler (today is 2025-09-01; the data ends
2025-08-31), the analyst's reply, the claims the reply already has, and a list of numbers in the reply that have no
claim yet.

For every listed number, and for every other statement in the reply that forecasts, ranks, compares or states a
figure, in numbers or in words ("about half of sales", "most customers", "UK sales will keep growing", "the top 20
will not reach half"), that no existing claim already states, decide:
- if it is a forecast, a list metric (precision, recall, hit rate, AUC, base rate) or a figure from the data that
  CLAIMS.md can express, write the one claim that the reply's sentence states: its type, quantity, filter, window,
  breakdown key, point, and its range only if the sentence gives one. Put the exact words of the reply that state it
  (a sentence or part of one, copied character for character) in `says`. Use only what the reply states; where it
  leaves a detail open, take the plainest reading and add nothing. A statement in words gets the plainest number it
  commits to ("about half": point 0.5; "will keep growing": a total for the window whose point is the comparison
  value it names, with a `verdict` resting on it);
- otherwise (a cost or budget the requester gave, an assumption, a count of items in the reply, a rank position, a
  model setting, a number CLAIMS.md cannot express) skip it, with a short reason.

Do not judge whether the reply is right, and do not fix it. Reply with ONLY one JSON object with one entry per sheet:
{"T001": {"claims": [ ...claims, each with a unique id... ], "skipped": [{"number or statement": "<as written>", "why": "..."}]}, ...}"""
RETRY = """

This is a second pass: the numbers listed on each sheet were left unaddressed the first time. Address every one:
a claim, or a skip that names the number as written."""


def untyped(answer: str, doc) -> list[str]:
    claims = doc.get("claims", []) if isinstance(doc, dict) else []
    refs = G.claim_numbers([c for c in claims if isinstance(c, dict)]) + [float(x) for x in FACTS]
    return [w for w, v, hu in G.numbers_in(answer) if not G.covered(v, hu, refs)]


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("’", "'").replace("“", '"').replace("”", '"')).strip()


def verify(c: dict, answer: str, numbers: list[str], context: list[dict] = ()) -> str | None:
    """None if the claim is kept, else why not."""
    if not isinstance(c, dict):
        return "not an object"
    if not isinstance(c.get("says"), str) or norm(c["says"]) not in norm(answer):
        return "says is not verbatim in the reply"
    errs = G.schema_errors({"claims": [c] + list(context)})
    errs = [e for e in errs if c.get("id") in e or "claims.json" in e]
    if errs:
        return "; ".join(errs)[:200]
    nums = [(w, v, hu) for w, v, hu in G.numbers_in(c["says"]) if w in numbers]
    if nums and not any(G.covered(v, hu, G.claim_numbers([c])) for _, v, hu in nums):
        return "quotes a listed number but does not state it"
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", type=Path, required=True)
    a = ap.parse_args()
    out = a.run / "typed"
    out.mkdir(exist_ok=True)
    latest = {}
    for line in (a.run / "attempts.jsonl").read_text().splitlines():
        r = json.loads(line)
        latest[(r["agent"], r["task"], r["arm"], r["rep"])] = r
    items = []
    for (agent, task, arm, rep) in sorted(latest):
        work = a.run / "attempts" / agent / f"{task}-{arm}-r{rep}" / "work"
        ans = work / "answer.md"
        if not ans.exists():
            continue
        answer = ans.read_text(errors="replace")
        try:
            doc = json.loads((work / "claims.json").read_text())
        except Exception:
            doc = {}
        have = [c for c in (doc.get("claims") or [] if isinstance(doc, dict) else []) if isinstance(c, dict)]
        items.append({"attempt": f"{agent}_{task}-{arm}-r{rep}", "task": task, "answer": answer,
                      "numbers": untyped(answer, doc), "have": have})
    random.Random(SEED).shuffle(items)
    for i, it in enumerate(items, 1):
        it["sheet"] = f"T{i:03d}"
    (out / "index.json").write_text(json.dumps({it["sheet"]: it["attempt"] for it in items}, indent=1))

    def run(rnd: str, bi: int, batch: list[dict]) -> None:
        d = out / rnd / f"b{bi:02d}"
        if (d / "done").exists():
            return
        if d.exists():
            import shutil
            shutil.rmtree(d)
        files = {"CLAIMS.md": claims_md()}
        for it in batch:
            files[f"sheets/{it['sheet']}.md"] = (f"# {it['sheet']}\n\n## Request\n\n{REQUESTS[it['task']]['ask']}\n\n"
                                                 f"## Reply\n\n{it['answer']}\n\n## Claims it already has\n\n"
                                                 + ("\n".join(f"- {c.get('says')!r}: " + json.dumps(
                                                     c.get("values") or c.get("claimed") or c.get("answer"))
                                                     for c in it["have"]) or "(none)")
                                                 + "\n\n## Numbers without a claim\n\n"
                                                 + ("\n".join(f"- {n}" for n in it["numbers"]) or "(none)") + "\n")
        text = "\n\n".join(f"=== {name} ===\n{body.strip()}" for name, body in files.items())
        try:                       # by API (DEVIATIONS 11-08); the per-sheet files are written as before
            res, _ = openai_api.ask(PROMPT + (RETRY if rnd != "luna" else "") + "\n\n" + text, model="gpt-6-luna",
                                    effort="high", log_dir=d, purpose=f"E3b typing ({rnd})")
            m = re.search(r"\{.*\}", res, re.S)
            got = json.loads(m.group(0)) if m else {}
            (d / "work" / "typed").mkdir(parents=True, exist_ok=True)
            for sid, v in got.items():
                if isinstance(v, dict) and re.fullmatch(r"T\d{3}", sid):
                    (d / "work" / "typed" / f"{sid}.json").write_text(json.dumps(v, ensure_ascii=False, indent=1))
        except (openai_api.OpenAIAPIError, ValueError) as exc:
            print(f"{rnd} batch {bi}: {exc}", flush=True)
            return
        (d / "done").write_text("")
        print(f"{rnd} batch {bi}: done", flush=True)

    def collect(rnd: str, bi: int, it: dict, rec: dict) -> None:
        f = out / rnd / f"b{bi:02d}" / "work" / "typed" / f"{it['sheet']}.json"
        try:
            got = json.loads(f.read_text())
        except (OSError, ValueError):
            got = {"claims": [], "skipped": [], "missing": True}
        new = [c for c in got.get("claims") or [] if isinstance(c, dict)]
        ids = {x.get("id") for x in new}
        for c in new:
            why = verify(c, it["answer"], it["numbers"], [x for x in new if x is not c])
            if why:
                rec["dropped"].append({"claim": c, "why": why})
            else:
                rest = c.get("rests_on")
                rec["claims"].append({**c, "id": f"posthoc_{rnd}_{c.get('id')}", "posthoc": True,
                                      **({"rests_on": f"posthoc_{rnd}_{rest}"} if rest in ids else {})})
        rec["skipped"] += got.get("skipped") or []
        rec["missing"] = rec.get("missing", False) or bool(got.get("missing"))

    def unaddressed(it: dict, rec: dict) -> list[str]:
        """Listed numbers neither covered by a kept claim nor named in a skip."""
        said = " ".join(json.dumps(x) for x in rec["skipped"])
        refs = G.claim_numbers(rec["claims"])
        return [w for w, v, hu in G.numbers_in(it["answer"]) if w in it["numbers"]
                and not G.covered(v, hu, refs) and w not in said]

    recs = {it["sheet"]: {"attempt": it["attempt"], "numbers": it["numbers"], "claims": [], "dropped": [],
                          "skipped": []} for it in items}
    batches = [items[i:i + BATCH] for i in range(0, len(items), BATCH)]
    with ThreadPoolExecutor(WORKERS) as ex:
        list(ex.map(lambda b: run("luna", *b), enumerate(batches)))
    for bi, batch in enumerate(batches):
        for it in batch:
            collect("luna", bi, it, recs[it["sheet"]])
    again = [{**it, "numbers": un} for it in items if (un := unaddressed(it, recs[it["sheet"]]))]
    retry = [again[i:i + BATCH] for i in range(0, len(again), BATCH)]       # one retry, as for failed runs
    with ThreadPoolExecutor(WORKERS) as ex:
        list(ex.map(lambda b: run("luna_retry", *b), enumerate(retry)))
    for bi, batch in enumerate(retry):
        for it in batch:
            collect("luna_retry", bi, it, recs[it["sheet"]])
    summary = []
    for it in items:
        rec = recs[it["sheet"]]
        rec["unaddressed"] = unaddressed(it, rec)
        (out / f"{it['attempt']}.json").write_text(json.dumps(rec, indent=1))
        summary.append({k: (len(v) if isinstance(v, list) else v) for k, v in rec.items()})
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(f"{len(items)} replies; kept {sum(s['claims'] for s in summary)} claims, dropped "
          f"{sum(s['dropped'] for s in summary)}, skipped {sum(s['skipped'] for s in summary)}, unaddressed "
          f"{sum(s['unaddressed'] for s in summary)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
