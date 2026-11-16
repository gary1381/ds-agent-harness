"""Chart for E3b/DESIGN.md: claim error (claimed AUC minus AUC at the development date) per model,
over every task and development date that reference.py ran.

    .venv/bin/python experiments/E3b/calibration_chart.py docs/experiments/E3b/calibration_2025-04-01.json \
        docs/experiments/E3b/calibration_2025-05-01.json docs/experiments/E3b/calibration_2025-06-01.json \
        --out docs/experiments/E3b/claim_error_dev.svg
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

COLORS = {"honest": "#2a78d6", "trap": "#eb6834"}
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
ROWS = (("recency", "recency only"), ("logit", "logistic regression"), ("hgb", "gradient boosting"),
        ("hgb E-split", "E-split: random split across dates"), ("hgb E-label", "E-label: immature labels"),
        ("hgb E-leak", "E-leak: features over the whole warehouse"))
MARKS = {"rp90": "circle", "rp30": "square", "spend90": "diamond", "hv_lapse90": "triangle"}


def mark(shape: str, cx: float, cy: float, fill: str, title: str) -> str:
    r = 5
    pts = {"square": f"{cx - r},{cy - r} {cx + r},{cy - r} {cx + r},{cy + r} {cx - r},{cy + r}",
           "diamond": f"{cx},{cy - r - 1} {cx + r + 1},{cy} {cx},{cy + r + 1} {cx - r - 1},{cy}",
           "triangle": f"{cx},{cy - r - 1} {cx + r + 1},{cy + r} {cx - r - 1},{cy + r}"}
    t = f"<title>{title}</title>"
    if shape == "circle":
        return f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r}" fill="{fill}" stroke="{SURFACE}" stroke-width="1.5">{t}</circle>'
    return f'<polygon points="{pts[shape]}" fill="{fill}" stroke="{SURFACE}" stroke-width="1.5">{t}</polygon>'


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    rows = []
    for p in a.runs:
        date = p.stem.removeprefix("calibration_")
        rows += [dict(r, date=date) for r in json.loads(p.read_text())]
    W, H = 900, 70 + 46 * len(ROWS) + 90
    x0, y0, pw = 270, 60, 560
    xmin, xmax = -0.10, 0.45
    x = lambda v: x0 + (v - xmin) / (xmax - xmin) * pw  # noqa: E731
    ph = 46 * len(ROWS)
    dates = sorted({r["date"] for r in rows})
    parts = [f'<rect width="100%" height="100%" fill="{SURFACE}"/>',
             f'<text x="16" y="20" font-size="12" fill="{INK2}">Claimed AUC (the model\'s own validation) minus its AUC at the '
             f'development date; {len(dates)} dates ({", ".join(dates)}) x 4 tasks.</text>',
             f'<text x="16" y="36" font-size="12" fill="{INK2}">Right of a dashed line = a silent claim error at that margin. '
             f'No sealed data: every label window ends before 2025-09-01.</text>']
    for t in (-0.10, -0.05, 0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45):
        parts.append(f'<line x1="{x(t):.1f}" y1="{y0}" x2="{x(t):.1f}" y2="{y0 + ph}" stroke="{GRID}"/>'
                     f'<text x="{x(t):.1f}" y="{y0 + ph + 16}" font-size="10" fill="{INK2}" text-anchor="middle">{t:+.2f}</text>')
    for m, lab in ((0.03, "margin 0.03"), (0.05, "margin 0.05")):
        parts.append(f'<line x1="{x(m):.1f}" y1="{y0 - 6}" x2="{x(m):.1f}" y2="{y0 + ph}" stroke="{INK2}" '
                     f'stroke-dasharray="4 3"/>'
                     f'<text x="{x(m) + (4 if m > 0.04 else -4):.1f}" y="{y0 - 10}" font-size="10" fill="{INK2}" '
                     f'text-anchor="{"start" if m > 0.04 else "end"}">{lab}</text>')
    parts.append(f'<text x="{x0 + pw / 2}" y="{y0 + ph + 36}" font-size="12" fill="{INK}" text-anchor="middle">'
                 f'claim error (claimed AUC − true AUC)</text>')
    for i, (model, label) in enumerate(ROWS):
        cy = y0 + 23 + 46 * i
        sub = [r for r in rows if r["model"] == model]
        kind = sub[0]["kind"] if sub else "honest"
        parts.append(f'<text x="{x0 - 12}" y="{cy + 4}" font-size="12" fill="{INK}" text-anchor="end">{label}</text>')
        for j, r in enumerate(sorted(sub, key=lambda r: (r["task"], r["date"]))):
            jit = (dates.index(r["date"]) - 1) * 9
            parts.append(mark(MARKS[r["task"]], x(r["claim_error"]), cy + jit, COLORS[kind],
                              f'{label}, {r["task"]}, {r["date"]}: claim {r["claim"]:.3f}, true {r["dev_auc"]:.3f}, '
                              f'error {r["claim_error"]:+.3f}'))
    ly = y0 + ph + 56
    lx = 16
    for kind, name in (("honest", "honest models"), ("trap", "the traps of DESIGN.md (gradient boosting)")):
        parts.append(f'<rect x="{lx}" y="{ly}" width="10" height="10" rx="2" fill="{COLORS[kind]}"/>'
                     f'<text x="{lx + 15}" y="{ly + 9}" font-size="11" fill="{INK}">{name}</text>')
        lx += 15 + 7 * len(name) + 24
    for task, shape in MARKS.items():
        parts.append(mark(shape, lx + 5, ly + 5, INK2, task) +
                     f'<text x="{lx + 15}" y="{ly + 9}" font-size="11" fill="{INK}">{task}</text>')
        lx += 15 + 7 * len(task) + 18
    parts.append(f'<text x="16" y="{ly + 28}" font-size="11" fill="{INK2}">Within a row the three dates are offset '
                 f'vertically (earliest on top).</text>')
    a.out.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
                     f'font-family="system-ui, -apple-system, Segoe UI, sans-serif">' + "".join(parts) + "</svg>\n",
                     encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
