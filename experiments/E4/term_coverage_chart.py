"""Chart for docs/experiments/E4/TERM_COVERAGE.md: how the fresh test paraphrases resolve under each resolver.

    .venv/bin/python experiments/E4/term_coverage_chart.py --out docs/experiments/E4/term_coverage.svg
Numbers are the set-3 rows of TERM_COVERAGE.md (term_coverage_E3a_test.md, term_coverage_E4_test.md,
term_resolver_probe.md); edit ROWS if those are re-run.
"""
from __future__ import annotations

import argparse
from pathlib import Path

ROWS = [("Frozen E3a terms", 75, 24, 61), ("E4 terms (two tuning rounds)", 111, 11, 38),
        ("E4 terms, then classifier", 122, 12, 26)]
PARTS = [("resolves to its own metric", "#2a78d6"), ("another metric: false-hold risk", "#eb6834"),
         ("no metric: not checked", "#c9c8c2")]


def svg() -> str:
    w, left, top, bar, gap, n = 760, 230, 92, 30, 18, 160
    scale = (w - left - 40) / n
    h = top + len(ROWS) * (bar + gap) + 40
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" font-family="system-ui, sans-serif" '
           f'font-size="13"><rect width="{w}" height="{h}" fill="#fcfcfb"/>',
           '<text x="16" y="28" font-size="16" font-weight="600" fill="#1f1f1d">Which metric does a fresh question '
           'name? 160 paraphrases by a model that never saw the terms</text>',
           '<text x="16" y="48" fill="#5c5b57">Set 3 (no overlap with the two sets used to edit the terms); '
           'English and Chinese, 8 each per metric, 10 metrics</text>']
    x = 16
    for label, color in PARTS:
        out.append(f'<rect x="{x}" y="62" width="12" height="12" rx="2" fill="{color}"/>'
                   f'<text x="{x + 18}" y="72" fill="#3b3a37">{label}</text>')
        x += 18 + len(label) * 7 + 24
    for i, (name, *vals) in enumerate(ROWS):
        y = top + i * (bar + gap)
        out.append(f'<text x="{left - 10}" y="{y + bar / 2 + 4}" text-anchor="end" fill="#1f1f1d">{name}</text>')
        x = left
        for v, (_, color) in zip(vals, PARTS):
            wd = v * scale
            out.append(f'<rect x="{x:.1f}" y="{y}" width="{max(wd - 2, 0):.1f}" height="{bar}" rx="3" fill="{color}">'
                       f'<title>{name}: {v} of {n}</title></rect>')
            if wd > 26:
                ink = "#ffffff" if color != "#c9c8c2" else "#3b3a37"
                out.append(f'<text x="{x + wd / 2 - 1:.1f}" y="{y + bar / 2 + 4}" text-anchor="middle" '
                           f'fill="{ink}">{v}</text>')
            x += wd
    out.append("</svg>")
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    Path(ap.parse_args().out).write_text(svg(), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
