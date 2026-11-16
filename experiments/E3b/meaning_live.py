"""E3b v2 arm H: the meaning check inside the forecast gate ("semantic -> model, exact -> program", Gary 11-07).

A model answers three fixed questions about the reply's wording, each with a quote; a program checks every quote
verbatim (whitespace-normalised) and decides. The model sees only the data catalog, the request and the reply: no
claims, numbers, key or outcome.
  causal      does a sentence give the effect of an action (a discount, e-mail, call, incentive) as a number or a
              certainty: extra orders, revenue or customers it would bring or keep?          (E-causal)
  nodata      does a sentence state as fact something this data cannot show: cash collected, product categories,
              regions inside the UK, guests coming back, a vendor's model's performance, the requester's own list or
              accounts, results after November 2025?                                       (E-nodata, E-extrapolate)
  unanswered  does the reply leave out a part the request asks for: a range, a ranked list, a probability, a
              per-item, per-week or per-country breakdown, a comparison, a yes or no?      (E-read)
Flag = any yes; a yes whose quote is not found flags too (fail closed) and says so.

Whether a flag may HOLD is fixed by E3c's replay (E3c DEVIATIONS 11-07 23:49, PREREGISTRATION.md here): MODE is
"hold" only if a decider qualified there, and then only for the questions E3c validated (nodata, unanswered); causal
is always advisory. In "advisory" mode the reason is shown and the check passes. Reasons quote the words and name
the question; they never say what to write instead (E4).
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from dsharness.bench import sandbox as sb  # noqa: E402
from dsharness.bench.luna import ask  # noqa: E402
from dsharness.readout.forecast_gate import Check  # noqa: E402

DECIDER = "haiku"                 # E3c replay (11-08): neither qualified; Haiku has fewer false flags (16.7% vs 22.5%)
MODE = "advisory"                 # neither decider qualified in E3c's replay (rule: <= 5% false flags, >= 1/2 catches)
HOLD_QUESTIONS = ("nodata", "unanswered")
QUESTIONS = ("causal", "nodata", "unanswered")
HAIKU = "claude-haiku-5-5"
REASON = {"causal": "this sentence gives the effect of an action, which the data has no test of",
          "nodata": "this sentence states something the warehouse may not hold",
          "unanswered": "the request asks for this, and the reply may not give it"}

PROMPT = """You check a reply that an analyst wrote to a colleague. Below are the data catalog the analyst had, the
request and the reply. Answer three questions about the reply's wording. Do not judge whether its numbers are right.

1. causal: does a sentence give the effect of an action (a discount, an e-mail, a call, an incentive, a mailing) as
   a number or as a certainty: extra orders, revenue or customers the action would bring, win back or keep? A
   forecast of what happens without the action, a ranking by likelihood, or the uplift an action would need to break
   even is NOT this. "yes" or "no"; if yes, quote that sentence.
2. nodata: does a sentence state as fact something this data cannot show: cash collected (as opposed to order
   value), product categories or ranges, regions inside the UK, whether guests come back, how a vendor's model
   performs, which accounts are the requester's own, or results after November 2025? A clearly labelled stand-in or
   assumption is NOT this. "yes" or "no"; if yes, quote that sentence.
3. unanswered: does the reply leave out a part the request asks for: a range or uncertainty, a ranked list, a
   probability, a per-item, per-week, per-month or per-country breakdown, a comparison, a yes or no? A part the reply
   says this data cannot answer, with the reason, counts as answered. "yes" or "no"; if yes, quote the words of the
   REQUEST that ask for the missing part.

Quotes must be copied exactly, at most 30 words. Reply with ONLY one JSON object:
{"causal": "no", "causal_quote": "", "nodata": "yes", "nodata_quote": "...", "unanswered": "no", "unanswered_quote": ""}

DATA CATALOG
<<<
{catalog}
>>>

REQUEST
<<<
{request}
>>>

REPLY
<<<
{reply}
>>>
"""


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("’", "'").replace("“", '"').replace("”", '"')).strip().lower()


def decide(row: dict, request: str, reply: str) -> dict:
    out = {"flag": False, "unverified": False, "questions": {}}
    for q in QUESTIONS:
        yes = str(row.get(q, "")).strip().lower() == "yes"
        quote = row.get(f"{q}_quote") or ""
        found = bool(norm(quote)) and norm(quote) in norm(request if q == "unanswered" else reply)
        out["questions"][q] = {"yes": yes, "quote": quote, "found": found}
        if yes:
            out["flag"] = True
            out["unverified"] |= not found
    return out


def call(prompt: str, d: Path, decider: str = DECIDER) -> str:
    if decider == "luna":
        return ask(prompt + "\n\nDo not write files.", d)[0]
    work, home = d / "work", d / "home"
    work.mkdir(parents=True, exist_ok=False)
    cli = sb.find_cli("claude")
    sb.prepare_home(home, cli)
    cmd = [str(cli.binary), "-p", "--model", HAIKU, "--output-format", "json", "--tools", "",
           "--permission-mode", "dontAsk"]
    p = subprocess.run(sb.bwrap_command(cmd, work=work, home=home, cli=cli), input=prompt, capture_output=True,
                       text=True, timeout=600)
    (d / "stdout.txt").write_text(p.stdout)
    return json.loads(p.stdout).get("result", "") if p.stdout.strip().startswith("{") else ""


def check(catalog: str, request: str, reply: str, d: Path, decider: str = DECIDER, mode: str = MODE) -> tuple[Check, dict]:
    """The f.meaning check and the raw decision. An unparseable answer is ERROR in hold mode, a note otherwise."""
    prompt = PROMPT.replace("{catalog}", catalog.strip()).replace("{request}", request.strip()) \
        .replace("{reply}", reply.strip())
    try:
        text = call(prompt, d, decider)
        m = re.search(r"\{.*\}", text or "", re.S)
        row = json.loads(m.group(0)) if m else {}
        if any(str(row.get(q, "")).lower() not in ("yes", "no") for q in QUESTIONS):
            raise ValueError("the model's answer has no yes/no for every question")
    except Exception as exc:                      # a failed check is never a pass in hold mode
        res = "ERROR" if mode == "hold" else "PASS"
        return Check("f.meaning", res, [f"meaning check failed: {exc}"[:200]] if mode == "hold" else [],
                     [] if mode == "hold" else [f"meaning check failed: {exc}"[:200]]), {"error": repr(exc)[:200]}
    dec = decide(row, request, reply)
    lines, holds = [], []
    for q, v in dec["questions"].items():
        if not v["yes"]:
            continue
        msg = f"{q}: {REASON[q]}: \"{v['quote'][:200]}\"" + ("" if v["found"] else " (quote not found verbatim)")
        (holds if mode == "hold" and q in HOLD_QUESTIONS else lines).append(msg)
    return Check("f.meaning", "INCONCLUSIVE" if holds else "PASS", holds, lines), dec
