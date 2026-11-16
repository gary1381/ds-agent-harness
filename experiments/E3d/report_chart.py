"""Chart for docs/experiments/E3d/REPORT.md, from docs/experiments/E3d/results.json.

Left: held-out silent-error rate P1 by arm, as pre-registered and with harmless ci/ edits not counted (exploratory).
Right: held-out sound-report rate P5 on the hold tasks, by agent and arm.

    .venv/bin/python experiments/E3d/report_chart.py --out docs/experiments/E3d/e3d_results.svg
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARMS = [("N", "N: agent alone", "#9a9993"), ("NI", "NI: alone + control total", "#eb6834"),
        ("H", "H: harness gate", "#2a78d6")]


def bars(out, x0, y0, w, h, groups, title, sub):
    """groups: [(label, {arm: value 0..1})]; vertical bars, 0-100% axis."""
    out += [f'<text x="{x0}" y="{y0 - 34}" font-size="14" font-weight="600" fill="#1f1f1d">{title}</text>',
            f'<text x="{x0}" y="{y0 - 16}" fill="#5c5b57">{sub}</text>']
    for t in (0, 25, 50, 75, 100):
        y = y0 + h - h * t / 100
        out += [f'<line x1="{x0 + 34}" x2="{x0 + w}" y1="{y:.1f}" y2="{y:.1f}" stroke="#e6e5e0"/>',
                f'<text x="{x0 + 28}" y="{y + 4:.1f}" text-anchor="end" fill="#5c5b57">{t}%</text>']
    gw = (w - 44) / len(groups)
    bw = min(34, (gw - 24) / len(ARMS))
    for i, (label, vals) in enumerate(groups):
        gx = x0 + 44 + i * gw + (gw - bw * len(ARMS) - 4 * (len(ARMS) - 1)) / 2
        for j, (arm, _, color) in enumerate(ARMS):
            v = vals.get(arm)
            if v is None:
                continue
            bh = max(h * v, 1.5)
            x = gx + j * (bw + 4)
            out += [f'<rect x="{x:.1f}" y="{y0 + h - bh:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="3" fill="{color}">'
                    f'<title>{label}, {arm}: {100 * v:.1f}%</title></rect>',
                    f'<text x="{x + bw / 2:.1f}" y="{y0 + h - bh - 5:.1f}" text-anchor="middle" font-size="11" '
                    f'fill="#1f1f1d">{100 * v:.0f}</text>']
        out.append(f'<text x="{x0 + 44 + i * gw + gw / 2:.1f}" y="{y0 + h + 18}" text-anchor="middle" '
                   f'fill="#1f1f1d">{label}</text>')


def svg(res: dict) -> str:
    W, H = 900, 400
    ho = res["sets"]["held_out"]
    p1 = {a: ho["by_arm"][a]["P1"] for a in ho["by_arm"]}
    ci = res["sensitivity"]["harmless_ci_not_unsound"]["P1"]
    p5 = {ag: {a: ho["by_agent_arm"][f"{ag} {a}"]["P5"] for a in ho["by_arm"]} for ag in ("claude", "codex")}
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="system-ui, sans-serif" '
           f'font-size="12"><rect width="{W}" height="{H}" fill="#fcfcfb"/>',
           '<text x="16" y="26" font-size="16" font-weight="600" fill="#1f1f1d">E3d: pipeline months, 6 held-out '
           'tasks, 108 attempts (2 agents × 3 arms × 3 reps)</text>']
    x = 16
    for _, label, color in ARMS:
        out += [f'<rect x="{x}" y="40" width="12" height="12" rx="2" fill="{color}"/>',
                f'<text x="{x + 18}" y="50" fill="#1f1f1d">{label}</text>']
        x += 30 + 7 * len(label)
    bars(out, 16, 120, 400, 220, [("pre-registered", p1), ("harmless ci/ edits not counted", ci)],
         "Silent error rate P1 (lower is better)", "All 14 silent errors that vanish are ci/ edits")
    bars(out, 470, 120, 410, 220, [("Claude", p5["claude"]), ("Codex", p5["codex"])],
         "Hold tasks: note names the real problem P5", "Codex in H mostly repeats the gate's control-total gap")
    out.append("</svg>")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--results", type=Path, default=ROOT / "docs/experiments/E3d/results.json")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    a.out.write_text(svg(json.loads(a.results.read_text())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
