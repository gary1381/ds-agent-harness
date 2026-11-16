"""Chart for H_v0.4_PLAN.md: M1 against M2 for each delivery rule of one census run (agreement_probe.py output).

    .venv/bin/python experiments/H_v04/agreement_chart.py docs/experiments/H_v0.4/agreement_E1_E1b.json E1b \
        docs/experiments/H_v0.4/agreement_E1b.svg
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

COLORS = {"N": "#2a78d6", "H": "#eb6834"}     # the census charts' fixed arm colours
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
RULES = {   # (arm, rule) -> label, number offset (dx, dy); points are numbered, the key beside the plot names them
    ("N", "recorded"): ("N: agent's own claim", (8, -6)),
    ("N", "same"): ("N: same agent's other run agrees", (8, -6)),
    ("N", "cross"): ("N: other agent agrees", (8, -6)),
    ("H", "recorded"): ("H v0.2: gate", (8, -6)),
    ("H", "both"): ("H: gate and other agent agrees", (8, -6)),
    ("H", "cross"): ("H: other agent agrees (no gate)", (-8, 14)),
    ("H", "hybrid"): ("H: gate, else other agent agrees (grader's reading)", (-14, -6)),
    ("H", "t2x"): ("H v0.4: gate, else 2nd agent with harness agrees", (8, -6)),
    ("H", "t2n"): ("H v0.4: gate, else 2nd agent without harness agrees", (-14, -6)),
}


def main() -> int:
    src, run, out = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    res = json.loads(src.read_text())
    W, H = 1060, 560
    x0, y0, pw, ph = 80, 60, 600, 420
    xmax, ymax = 0.45, 0.30
    x = lambda v: x0 + v / xmax * pw  # noqa: E731
    y = lambda v: y0 + ph - v / ymax * ph  # noqa: E731
    parts = [f'<rect width="100%" height="100%" fill="{SURFACE}"/>',
             f'<text x="16" y="20" font-size="12" fill="{INK2}">{run}, both agents pooled. Each point is one delivery '
             f'rule replayed on the recorded attempts; whiskers: 95% interval, bootstrap over questions.</text>',
             f'<text x="16" y="36" font-size="12" fill="{INK2}">Dashed: holding answers at random (each arm\'s own '
             f'accuracy). Lower left is better.</text>']
    for t in (0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30):
        parts.append(f'<line x1="{x0}" y1="{y(t):.1f}" x2="{x0 + pw}" y2="{y(t):.1f}" stroke="{GRID}"/>'
                     f'<text x="{x0 - 8}" y="{y(t) + 4:.1f}" font-size="10" fill="{INK2}" text-anchor="end">{t:.0%}</text>')
    for t in (0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45):
        parts.append(f'<line x1="{x(t):.1f}" y1="{y0}" x2="{x(t):.1f}" y2="{y0 + ph}" stroke="{GRID}"/>'
                     f'<text x="{x(t):.1f}" y="{y0 + ph + 16}" font-size="10" fill="{INK2}" text-anchor="middle">{t:.0%}</text>')
    parts.append(f'<text x="{x0 + pw / 2}" y="{y0 + ph + 36}" font-size="12" fill="{INK}" text-anchor="middle">'
                 f'M2 false holds (correct answers not delivered)</text>')
    parts.append(f'<text x="18" y="{y0 + ph / 2}" font-size="12" fill="{INK}" text-anchor="middle" '
                 f'transform="rotate(-90 18 {y0 + ph / 2})">M1 silent errors (wrong and delivered)</text>')
    for arm in ("N", "H"):
        wrong = 1 - res[f"{run}/{arm}/recorded/all"]["accuracy"]
        parts.append(f'<line x1="{x(0):.1f}" y1="{y(wrong):.1f}" x2="{x(xmax):.1f}" y2="{y(wrong * (1 - xmax)):.1f}" '
                     f'stroke="{COLORS[arm]}" stroke-width="1.5" stroke-dasharray="5 4"/>')
    key = []
    for n, ((arm, rule), (label, (dx, dy))) in enumerate(RULES.items(), 1):
        b = res.get(f"{run}/{arm}/{rule}/all")
        if not b:
            continue
        px, py = x(b["M2"]), y(b["M1"])
        (m2a, m2b), (m1a, m1b) = b["M2_ci"], b["M1_ci"]
        parts.append(f'<line x1="{x(m2a):.1f}" y1="{py:.1f}" x2="{x(m2b):.1f}" y2="{py:.1f}" stroke="{COLORS[arm]}" '
                     f'stroke-width="1.2" opacity="0.6"/>'
                     f'<line x1="{px:.1f}" y1="{y(m1a):.1f}" x2="{px:.1f}" y2="{y(m1b):.1f}" stroke="{COLORS[arm]}" '
                     f'stroke-width="1.2" opacity="0.6"/>')
        parts.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="5" fill="{COLORS[arm]}" stroke="{SURFACE}" stroke-width="2">'
                     f'<title>{label}: M1 {b["M1"]:.1%}, M2 {b["M2"]:.1%}</title></circle>')
        parts.append(f'<text x="{px + dx:.1f}" y="{py + dy:.1f}" font-size="11" font-weight="600" fill="{INK}" '
                     f'text-anchor="{"start" if dx > 0 else "end"}">{n}</text>')
        key.append((n, arm, label, b))
    lx = x0 + pw + 24
    parts.append(f'<text x="{lx}" y="{y0 + 4}" font-size="11" fill="{INK2}">#  rule: M1 silent errors, M2 false holds</text>')
    for i, (n, arm, label, b) in enumerate(key):
        ty = y0 + 26 + i * 34
        parts.append(f'<circle cx="{lx + 4}" cy="{ty - 4}" r="4" fill="{COLORS[arm]}"/>'
                     f'<text x="{lx + 14}" y="{ty}" font-size="11" font-weight="600" fill="{INK}">{n}</text>'
                     f'<text x="{lx + 28}" y="{ty}" font-size="11" fill="{INK}">{label}</text>'
                     f'<text x="{lx + 28}" y="{ty + 14}" font-size="11" fill="{INK2}">M1 {b["M1"]:.1%}, M2 {b["M2"]:.1%}</text>')
    ty = y0 + 26 + len(key) * 34 + 6
    for i, (arm, name) in enumerate((("N", "arm N: no harness"), ("H", "arm H: harness v0.2 (E1b)"))):
        parts.append(f'<rect x="{lx}" y="{ty + i * 18}" width="10" height="10" rx="2" fill="{COLORS[arm]}"/>'
                     f'<text x="{lx + 15}" y="{ty + 9 + i * 18}" font-size="11" fill="{INK}">{name}</text>')
    out.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
                   f'font-family="system-ui, -apple-system, Segoe UI, sans-serif">' + "".join(parts) + "</svg>\n",
                   encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
