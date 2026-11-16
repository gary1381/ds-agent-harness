"""E3c v2 labelling (LABEL_GUIDE_v2.md): blind sheets, two labellers on every reply, blind adjudication, labels.json.

    .venv/bin/python experiments/E3c/label_v2.py sheets <run>      # sheets/S###.md (blind) + label_index.json
    .venv/bin/python experiments/E3c/label_v2.py haiku <run>       # labeller 2: Haiku on every sheet
    .venv/bin/python experiments/E3c/label_v2.py adjudicate <run>  # disagreements on `supported` -> adjudicate/D###.md
    .venv/bin/python experiments/E3c/label_v2.py final <run>       # -> <run>/labels.json

Every sheet shows only the request, its key and the reply, in a shuffled order (seed 20261107): no agent, arm,
delivery, gate verdict, claims or numbers file (Luna's review 11-07, blocker 4). The map from sheet to attempt is
`<run>/label_index.json`, outside the sheet folder; labellers never read it. Labeller 1 is the designer's subagents
(labels/designer_*.json), labeller 2 is Haiku. Where they disagree on `supported`, a fresh designer subagent that
labelled no sheet adjudicates from the blind sheet with both labels shown unattributed, in random order. RESEARCH
USE ONLY: everything stays under the run directory (gitignored).
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import random
import re
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))
from dsharness.bench import sandbox as sb  # noqa: E402
from tasks import TASKS as SEEN  # noqa: E402

CODES = ("E-select", "E-fish", "E-pool", "E-scale", "E-extrapolate", "E-nodata", "E-overclaim", "E-calc", "E-read",
         "E-other")
# bank_v2.yaml and keys_v2.yaml are not distributed in the public copy (the Hillstrom file has no licence; docs/DATA.md)
for _f in ("bank_v2.yaml", "keys_v2.yaml"):
    if not (HERE / _f).exists():
        raise SystemExit(f"experiments/E3c/{_f} is not distributed with the public copy; see docs/DATA.md")
KEYS = yaml.safe_load((HERE / "keys_v2.yaml").read_text())
BANK = {r["id"]: r for r in yaml.safe_load((HERE / "bank_v2.yaml").read_text())}
SEED, BATCH, MODEL = 20261107, 6, "claude-haiku-5-5"
# words that could tell a labeller the reply came from arm H; counted per arm, not removed
GATE_WORDS = re.compile(r"\b(gate|check_readout|claims\.json|readout gate|ALLOW|HOLD|REJECT)\b")


def key(r: dict) -> str:
    return f"{r['agent']}/{r['task']}-{r['arm']}-r{r['rep']}"


def latest(run: Path) -> list[dict]:
    keep = {}
    for line in (run / "attempts.jsonl").read_text().splitlines():
        r = json.loads(line)
        keep[(r["agent"], r["task"], r["arm"], r["rep"])] = r
    return sorted(keep.values(), key=lambda r: (r["task"], r["agent"], r["arm"], r["rep"]))


def request_and_key(task: str) -> tuple[str, str]:
    if task in SEEN:
        t = SEEN[task]
        return t.ask, f"Supported: {t.supported}\nUnsupported: {t.errors}"
    k = KEYS["keys"][task]
    return (f"({BANK[task]['requester']}) {BANK[task]['request']}",
            f"Supported: {k['supported']}\nUnsupported: " + "; ".join(k["unsupported"]))


GENERAL = "General rules of the key:\n" + "\n".join(f"- {g}" for g in KEYS["general"])


def sheet(r: dict, sid: str) -> str:
    req, k = request_and_key(r["task"])
    return "\n".join([f"## Sheet {sid}", "", f"Request: {req}", "", f"Owner's key:\n{k}", "", "Reply:", "", "<<<",
                      (r["answer"] or "").strip(), ">>>"]) + "\n"


def write_sheets(run: Path) -> int:
    out = run / "labels"
    (out / "sheets").mkdir(parents=True, exist_ok=False)
    (out / "GENERAL.md").write_text(GENERAL + "\n", encoding="utf-8")
    rs = [r for r in latest(run) if r.get("answer")]
    random.Random(SEED).shuffle(rs)
    index = {}
    for i, r in enumerate(rs, 1):
        sid = f"S{i:03d}"
        (out / "sheets" / f"{sid}.md").write_text(sheet(r, sid), encoding="utf-8")
        index[sid] = {"attempt": key(r), "task": r["task"], "arm": r["arm"],
                      "gate_words": sorted(set(GATE_WORDS.findall(r["answer"] or "")))}
    (run / "label_index.json").write_text(json.dumps(index, indent=1))
    print(f"{len(index)} blind sheets -> {out / 'sheets'}; index -> {run / 'label_index.json'}")
    return 0


def check_rows(rows: list[dict], want: set[str], who: str) -> dict[str, dict]:
    got = {}
    for row in rows:
        assert row.get("sheet") in want, f"{who}: unknown sheet {row.get('sheet')}"
        assert isinstance(row.get("supported"), bool), row
        codes = row.get("codes") or []
        assert all(c in CODES for c in codes) and bool(codes) != row["supported"], row
        if row["sheet"] in got:
            raise SystemExit(f"{who}: {row['sheet']} labelled twice")
        got[row["sheet"]] = {"supported": row["supported"], "codes": codes, "why": row.get("why", "")}
    missing = sorted(want - set(got))
    if missing:
        raise SystemExit(f"{who}: {len(missing)} sheets without a label: {missing[:10]}")
    return got


HEAD = """You check replies that analysts wrote to colleagues about a randomised e-mail test (64,000 past-year customers,
random thirds: men's e-mail, women's e-mail, no e-mail; visits, purchases and spend over two weeks). Each sheet has
the request, the owner's answer key and the reply. Label each reply against its key and these general rules:

{general}

supported = true when the reply answers what was asked (every breakdown it names), its numbers match the key
(rounding as a person would is fine), and it makes none of the key's unsupported claims nor breaks a general rule.
A conservative slip (calling an effect unclear that the key shows) is not an error unless the request asks exactly
that. A caveat after a headline claim does not cure the headline. Otherwise supported = false, with one or more
codes: E-select, E-fish, E-pool, E-scale, E-extrapolate, E-nodata, E-overclaim, E-calc, E-read, E-other.

Reply with ONLY a JSON array, one object per sheet, in sheet order:
[{{"sheet": "S001", "supported": false, "codes": ["E-scale"], "why": "<at most 25 words, quoting the reply>"}}]

"""


def run_batch(text: str, want: set[str], d: Path, timeout_s: int = 900) -> list[dict]:
    work, home = d / "work", d / "home"
    work.mkdir(parents=True, exist_ok=False)
    cli = sb.find_cli("claude")
    sb.prepare_home(home, cli)
    cmd = [str(cli.binary), "-p", "--model", MODEL, "--output-format", "json", "--tools", "",
           "--permission-mode", "dontAsk"]
    p = subprocess.run(sb.bwrap_command(cmd, work=work, home=home, cli=cli), input=text, capture_output=True,
                       text=True, timeout=timeout_s)
    (d / "stdout.txt").write_text(p.stdout)
    (d / "stderr.txt").write_text(p.stderr)
    res = json.loads(p.stdout).get("result", "") if p.stdout.strip().startswith("{") else ""
    m = re.search(r"\[.*\]", res, re.S)
    rows = json.loads(m.group(0)) if m else []
    got = [r for r in rows if r.get("sheet") in want and isinstance(r.get("supported"), bool)]
    if len(got) != len(want):
        raise RuntimeError(f"{d.name}: {len(got)} valid labels for {len(want)} sheets (exit {p.returncode})")
    return got


def haiku(run: Path) -> int:
    """Labeller 2 on every sheet; a batch that fails is retried once, then the run stops (no partial labels)."""
    out = run / "labels"
    paths = sorted((out / "sheets").glob("S*.md"))
    head = HEAD.format(general="\n".join(f"- {g}" for g in KEYS["general"]))
    done = out / "haiku_batches"
    done.mkdir(exist_ok=True)
    labels = []
    for k in range(0, len(paths), BATCH):
        batch, f = paths[k:k + BATCH], done / f"batch{k // BATCH:03d}.json"
        if not f.exists():
            for attempt in (1, 2):
                try:
                    rows = run_batch(head + "\n\n".join(p.read_text() for p in batch), {p.stem for p in batch},
                                     out / "haiku_runs" / f"batch{k // BATCH:03d}-try{attempt}")
                    break
                except (RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
                    if attempt == 2:
                        raise SystemExit(f"Haiku batch {k // BATCH} failed twice: {exc}")
            f.write_text(json.dumps(rows, indent=1, ensure_ascii=False))
        labels += json.loads(f.read_text())
        print(f"{len(labels)}/{len(paths)} labelled", flush=True)
    check_rows(labels, {p.stem for p in paths}, "haiku")
    (out / "haiku.json").write_text(json.dumps(labels, indent=1, ensure_ascii=False))
    return 0


def both(run: Path) -> tuple[set[str], dict, dict]:
    out = run / "labels"
    want = {p.stem for p in (out / "sheets").glob("S*.md")}
    des = check_rows([row for f in sorted(out.glob("designer_*.json")) for row in json.loads(f.read_text())], want,
                     "designer")
    hai = check_rows(json.loads((out / "haiku.json").read_text()), want, "haiku")
    return want, des, hai


def adjudicate(run: Path) -> int:
    want, des, hai = both(run)
    out = run / "labels" / "adjudicate"
    out.mkdir(parents=True, exist_ok=False)
    rng = random.Random(SEED + 1)
    order = {}
    for sid in sorted(s for s in want if des[s]["supported"] != hai[s]["supported"]):
        pair = [des[sid], hai[sid]]
        flip = rng.random() < 0.5
        if flip:
            pair.reverse()
        order[sid] = "haiku-first" if flip else "designer-first"
        text = (run / "labels" / "sheets" / f"{sid}.md").read_text()
        for i, lab in enumerate(pair, 1):
            text += (f"\nLabel {i}: supported {lab['supported']}, codes {lab['codes']}, why: {lab['why']}\n")
        (out / f"D{sid[1:]}.md").write_text(text, encoding="utf-8")
    (run / "adjudicate_order.json").write_text(json.dumps(order, indent=1))
    print(f"{len(order)} disagreements of {len(want)} -> {out}; write {out / 'adjudications.json'}")
    return 0


def final(run: Path) -> int:
    want, des, hai = both(run)
    index = json.loads((run / "label_index.json").read_text())
    dis = {s for s in want if des[s]["supported"] != hai[s]["supported"]}
    adj = {}
    if dis:
        rows = json.loads((run / "labels" / "adjudicate" / "adjudications.json").read_text())
        adj = check_rows([{**r, "sheet": "S" + r["sheet"].lstrip("DS")} for r in rows], dis, "adjudication")
    labels = {"_meta": {"labelled": datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "by": "blind double labels (designer subagents, Haiku), disagreements adjudicated blind by the "
                              "designer; experiments/E3c/LABEL_GUIDE_v2.md",
                        "disagreements": len(dis)}}
    for sid in sorted(want):
        primary = adj.get(sid) or des[sid]
        labels[index[sid]["attempt"]] = {**primary, "sheet": sid, "designer": des[sid], "haiku": hai[sid],
                                         "adjudicated": sid in adj}
    (run / "labels.json").write_text(json.dumps(labels, indent=1, ensure_ascii=False))
    print(f"{len(want)} labels ({len(dis)} adjudicated) -> {run / 'labels.json'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=("sheets", "haiku", "adjudicate", "final"))
    ap.add_argument("run", type=Path)
    a = ap.parse_args()
    return {"sheets": write_sheets, "haiku": haiku, "adjudicate": adjudicate, "final": final}[a.cmd](a.run)


if __name__ == "__main__":
    raise SystemExit(main())
