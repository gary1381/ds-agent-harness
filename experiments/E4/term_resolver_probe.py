"""Exploratory (O3): can the paraphrases that test a model's term lists also train a resolver that reaches further?

Trains a character n-gram logistic regression on the tuning paraphrases plus each metric's label and definition
(nothing from the held-out set), and scores the held-out paraphrases three ways: the regex term lists alone, the
classifier alone, and the regex first with the classifier only where the regex names nothing. The classifier
abstains (names nothing) below probability 0.5, fixed before the held-out set was scored. Counts: right, another
metric (a false-hold risk if used for contradictions), none (no check).

    DSH_SRC=<v0.5 tree>/src .venv/bin/python experiments/E4/term_resolver_probe.py \\
        --model experiments/E4/context/semantic_model.yaml --train docs/experiments/E4/paraphrases_tune.json \\
        --test docs/experiments/E4/paraphrases_holdout.json --out docs/experiments/E4/term_resolver_probe.md
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, os.environ.get("DSH_SRC") or str(ROOT / "src"))
from dsharness.context.semantic import load_semantic_model     # noqa: E402
from dsharness.tools.analytics import match_governed_metrics    # noqa: E402

MIN_PROB = 0.5          # fixed before scoring the held-out set


def pairs(items: dict, metrics) -> list[tuple[str, str, str]]:
    return [(q, m, lang) for m, langs in items.items() if m in metrics for lang in ("en", "zh")
            for q in langs.get(lang) or []]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--model", required=True)
    ap.add_argument("--train", required=True)
    ap.add_argument("--test", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    model = load_semantic_model(args.model)
    train = pairs(json.loads(Path(args.train).read_text(encoding="utf-8")), model.metrics)
    train += [(f"{m.label}. {m.description} {m.definition}", name, "def") for name, m in model.metrics.items()]
    test = pairs(json.loads(Path(args.test).read_text(encoding="utf-8")), model.metrics)
    clf = make_pipeline(TfidfVectorizer(analyzer="char_wb", ngram_range=(1, 4), sublinear_tf=True, lowercase=True),
                        LogisticRegression(max_iter=2000, C=10.0))
    clf.fit([q for q, _, _ in train], [m for _, m, _ in train])

    def learned(q: str) -> list[str]:
        p = clf.predict_proba([q])[0]
        i = p.argmax()
        return [clf.classes_[i]] if p[i] >= MIN_PROB else []

    tally = {k: Counter() for k in ("regex", "classifier", "regex, then classifier")}
    for q, m, lang in test:
        rx = match_governed_metrics(model, q)
        lc = learned(q)
        for k, got in (("regex", rx), ("classifier", lc), ("regex, then classifier", rx or lc)):
            kind = "right" if got == [m] else ("another metric" if got else "none")
            tally[k][kind] += 1
            tally[k][f"{kind} {lang}"] += 1
    n = len(test)
    lines = ["# Term resolver probe (exploratory, O3)", "",
             f"Held-out paraphrases: {n} (none of them used for training or for the term edits). Training: "
             f"{len(train) - len(model.metrics)} tuning paraphrases plus each metric's label and definition. "
             f"Classifier: character 1-4-gram TF-IDF, logistic regression, abstains below p = {MIN_PROB}.", "",
             "| resolver | right | another metric (false-hold risk) | none (no check) | right, en | right, zh |",
             "|---|---|---|---|---|---|"]
    for k, t in tally.items():
        lines.append(f"| {k} | {t['right']} ({100 * t['right'] / n:.0f}%) | {t['another metric']} | {t['none']} | "
                     f"{t['right en']} | {t['right zh']} |")
    Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
