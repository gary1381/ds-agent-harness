"""Onboarding check O3 (H v0.5): do a semantic model's term lists reach the way people actually ask?

A model that has not seen the term lists (Haiku, no tools) gets each metric's label, unit and definition, and
writes questions a business user might ask about it, in English and in Chinese. Every question must resolve to
its own metric through `match_governed_metrics`. A miss means the terms do not reach that phrasing: the designer
edits the terms (or decides the phrasing really means another metric) and runs the check again. The model must
pass before it is frozen; the report lists every miss.

    DSH_SRC=<v0.5 tree>/src .venv/bin/python experiments/E4/term_coverage.py \\
        --model experiments/E4/context/semantic_model.yaml --out docs/experiments/E4/term_coverage.md

The paraphrases are cached next to the report (`.json`); `--reuse` re-checks the cached ones without calling the
model, which is what a term edit needs.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, os.environ.get("DSH_SRC") or str(ROOT / "src"))
from dsharness.context.semantic import load_semantic_model     # noqa: E402
from dsharness.tools.analytics import match_governed_metrics    # noqa: E402

PER_LANG = 8
PROMPT = """You write test questions for a business analytics assistant. Below are the metrics of one company's
data. For EACH metric write {n} questions in English and {n} in Chinese (简体中文) that a manager, analyst or
shop owner might really type to get that number. Vary the wording: everyday words and synonyms, the jargon of
the trade, short and long, some with a month or a market, a few with typos or informal phrasing. Each question
must clearly ask for that metric and no other. Do not use the metric's identifier.

Company: {company}

Metrics:
{metrics}

Reply with JSON only: {{"<metric identifier>": {{"en": [...], "zh": [...]}}, ...}}"""


def describe(model) -> str:
    out = []
    for name, m in model.metrics.items():
        out.append(f"- {name}: {m.label} ({m.unit}). {m.description} {m.definition}".strip())
    return "\n".join(out)


def paraphrase(model) -> dict:
    spec = model.spec
    prompt = PROMPT.format(n=PER_LANG, company=" ".join(str(spec.get("description", "")).split()),
                           metrics=describe(model))
    res = subprocess.run(["claude", "-p", "--model", "claude-haiku-5-5", "--tools", "", "--output-format", "json",
                          prompt], capture_output=True, text=True, timeout=600,
                         cwd=tempfile.mkdtemp(prefix="dsh-terms-"))       # no project files in its context
    if res.returncode != 0:
        raise SystemExit(f"claude -p failed: {res.stderr[:500]}")
    text = json.loads(res.stdout)["result"]
    found = re.search(r"\{.*\}", text, re.S)
    if not found:
        raise SystemExit(f"no JSON in the reply: {text[:300]}")
    return json.loads(found.group(0))


def check(model, items: dict) -> tuple[list[dict], Counter, Counter]:
    rows, total, hit = [], Counter(), Counter()
    for metric, langs in items.items():
        if metric not in model.metrics:
            continue
        for lang in ("en", "zh"):
            for q in langs.get(lang) or []:
                got = match_governed_metrics(model, q)
                ok = got == [metric]
                total[(metric, lang)] += 1
                hit[(metric, lang)] += ok
                if not ok:
                    rows.append({"metric": metric, "lang": lang, "question": q, "resolved": got})
    return rows, total, hit


def report(model_path: str, rows: list[dict], total: Counter, hit: Counter) -> str:
    metrics = sorted({m for m, _ in total})
    n, k = sum(total.values()), sum(hit.values())
    other = Counter((r["metric"], r["lang"]) for r in rows if r["resolved"])
    none = Counter((r["metric"], r["lang"]) for r in rows if not r["resolved"])
    lines = [f"# Term coverage: `{model_path}`", "",
             f"{k} of {n} paraphrased questions resolve to their own metric ({100 * k / max(n, 1):.0f}%); "
             f"{sum(other.values())} resolve to another metric (a right declaration would be contradicted: a false "
             f"hold) and {sum(none.values())} to none (the declared metric is not checked against the question). "
             "Questions written by Haiku from each metric's label and definition, without the term lists "
             "(experiments/E4/term_coverage.py).", "",
             "| metric | right, en | right, zh | another metric | none |", "|---|---|---|---|---|"]
    for m in metrics:
        lines.append(f"| {m} | {hit[(m, 'en')]}/{total[(m, 'en')]} | {hit[(m, 'zh')]}/{total[(m, 'zh')]} | "
                     f"{other[(m, 'en')] + other[(m, 'zh')]} | {none[(m, 'en')] + none[(m, 'zh')]} |")
    lines += ["", "## Misses", "", "| metric | lang | question | resolved to |", "|---|---|---|---|"]
    for r in rows:
        q = r["question"].replace("|", "\\|")
        lines.append(f"| {r['metric']} | {r['lang']} | {q} | {', '.join(r['resolved']) or 'nothing'} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--reuse", action="store_true", help="re-check the cached paraphrases")
    ap.add_argument("--cache", help="paraphrase file to read or write (default: the report's name, .json)")
    args = ap.parse_args()
    model = load_semantic_model(args.model)
    cache = Path(args.cache) if args.cache else Path(args.out).with_suffix(".json")
    items = json.loads(cache.read_text(encoding="utf-8")) if args.reuse else paraphrase(model)
    if not args.reuse:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    rows, total, hit = check(model, items)
    Path(args.out).write_text(report(args.model, rows, total, hit), encoding="utf-8")
    print(f"{sum(hit.values())}/{sum(total.values())} resolve; {len(rows)} misses -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
