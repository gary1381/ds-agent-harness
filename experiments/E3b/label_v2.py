"""E3b v2 labelling (LABEL_GUIDE_v2.md): outcome-blind sheets, two labellers on every reply, blind adjudication.

    .venv/bin/python experiments/E3b/label_v2.py sheets <run>      # sheets/S###.md (blind) + label_index.json
    .venv/bin/python experiments/E3b/label_v2.py haiku <run>       # labeller 2: Haiku on every sheet
    .venv/bin/python experiments/E3b/label_v2.py adjudicate <run>  # disagreements on `supported` -> adjudicate/D###.md
    .venv/bin/python experiments/E3b/label_v2.py final <run>       # -> <run>/labels.json

Every sheet shows the request, its key (supported, unsupported; the general rules in GENERAL.md beside the sheets),
reference figures about the past, the files the reply's claims name (name, columns, first 10 rows) and the reply, in
a shuffled order (seed 20261108). No agent, arm, delivery, gate verdict, claims.json or realised outcome: labels judge
meaning only, the truth script judges accuracy (PREREGISTRATION.md, "Outcomes"). Past reference figures are the
key's total and share estimands whose window ends on or before 2025-09-01, realised on the agents' own warehouse
(wh_2025-09-01.duckdb); nothing here reads a sealed month. The map from sheet to attempt is `<run>/label_index.json`,
outside the sheet folder; labellers never read it. Labeller 1 is Luna (luna_label_v2.py, labels/designer_luna_*.json),
labeller 2 Haiku. Where they disagree on `supported`, a fresh Claude subagent that labelled no sheet adjudicates from
the blind sheet with both labels shown unattributed, in random order. RESEARCH USE ONLY: everything stays under the
run directory (gitignored).
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import random
import re
import sys

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))
from dsharness.bench import claude_api  # noqa: E402
from dsharness.readout import forecast as D  # noqa: E402
from run_v2 import REQUESTS, named_files  # noqa: E402
import truth_v2 as T  # noqa: E402
import warehouse_v2 as W  # noqa: E402

CODES = ("E-causal", "E-nodata", "E-read", "E-define", "E-extrapolate", "E-overclaim", "E-calc", "E-other")
KEYS = T._iso(yaml.safe_load((HERE / "keys_v2.yaml").read_text()))
SEED, BATCH, MODEL = 20261108, 6, "claude-haiku-5-5"
FILE_ROWS, CELL = 10, 200
# words that could tell a labeller the reply came from arm H; counted per arm, not removed
GATE_WORDS = re.compile(r"\b(gate|check_forecast|realise_claim|describe_task|dsh-model|claims\.json|forecast gate|"
                        r"ALLOW|HOLD|REJECT)\b")


def key(r: dict) -> str:
    return f"{r['agent']}/{r['task']}-{r['arm']}-r{r['rep']}"


def latest(run: Path) -> list[dict]:
    keep = {}
    for line in (run / "attempts.jsonl").read_text().splitlines():
        r = json.loads(line)
        keep[(r["agent"], r["task"], r["arm"], r["rep"])] = r
    return sorted(keep.values(), key=lambda r: (r["task"], r["agent"], r["arm"], r["rep"]))


def has_answer(r: dict) -> bool:
    return bool((r.get("answer") or "").strip())


def request_and_key(task: str) -> tuple[str, str]:
    req, k = REQUESTS[task], KEYS["keys"][task]
    ask = f"({req['requester']}) {req['ask']}" if req.get("requester") else req["ask"]
    return ask, f"Supported: {k['supported']}\nUnsupported: " + "; ".join(k["unsupported"])


GENERAL = "General rules of the key:\n" + "\n".join(f"- {g}" for g in KEYS["general"])


def _describe(e: dict) -> str:
    if e["type"] == "share":
        what = (f"share: {e['numerator']['quantity']} {json.dumps(e['numerator'].get('filter') or {})} / "
                f"{e['denominator']['quantity']} {json.dumps(e['denominator'].get('filter') or {})}")
    else:
        what = f"total: {e['quantity']} {json.dumps(e.get('filter') or {})}"
    return f"{what}, window [{e['window'][0]}, {e['window'][1]}) (end exclusive)" + (f", by {e['by']}"
                                                                                   if e.get("by") else "")


def _fig(x) -> str:
    """Every digit realise() gives (2 decimals for amounts, 6 for shares), so a past figure can be checked to 0.5%."""
    return "—" if x is None else f"{int(x):,}" if float(x).is_integer() else f"{x:,}"


def past_figures(wh: Path) -> dict[str, list[str]]:
    """Per request, the key's total and share estimands that end on or before the data end, realised on the agents'
    warehouse. List and per-buyer estimands have no single past figure and are left out."""
    lines = W.lines(wh)
    assert lines.ordered_at.max() < pd.Timestamp(W.DATA_END)
    out = {}
    for task, k in KEYS["keys"].items():
        rows = []
        for e in k.get("estimands") or []:
            if e["type"] not in ("total", "share") or str(e["window"][1])[:10] > W.DATA_END:
                continue
            real = D.realise({**e, "says": "", "values": [{"key": x} for x in T.estimand_keys(e, lines, HERE)]},
                             lines, HERE, None, W.DATA_END)
            if "error" in real:
                raise SystemExit(f"{task} {e['id']}: past figure does not realise: {real['error']}")
            vals = "; ".join(("" if v["key"] is None else f"{v['key']}: ") + _fig(v["actual"]) for v in real["values"])
            rows.append(f"- {e['id']} ({_describe(e)}): {vals}")
        out[task] = rows
    return out


def file_view(work: Path, name: str) -> str:
    """A delivered file as its name, columns and first rows; never more."""
    p = work / name
    head = f"File `{name}`"
    if not p.is_file():
        return f"{head}: (missing)"
    try:
        if p.suffix.lower() in (".csv", ".tsv", ".txt"):
            df = pd.read_csv(p, sep="\t" if p.suffix.lower() == ".tsv" else ",", nrows=FILE_ROWS, dtype=str)
        elif p.suffix.lower() == ".parquet":
            df = pd.read_parquet(p).head(FILE_ROWS).astype(str)
        else:
            raise ValueError("not a table")
        df = df.map(lambda x: x if not isinstance(x, str) or len(x) <= CELL else x[:CELL] + "…")
        return (f"{head}, columns: {', '.join(map(str, df.columns))}; first {min(FILE_ROWS, len(df))} rows:\n\n"
                + df.to_csv(index=False).strip())
    except Exception:                 # not readable as a table: its first lines as text
        text = p.read_text(errors="replace").splitlines()[:FILE_ROWS]
        return f"{head}, first {len(text)} lines:\n\n" + "\n".join(x[:CELL] for x in text)


def sheet(r: dict, sid: str, past: list[str], work: Path) -> str:
    req, k = request_and_key(r["task"])
    files = [file_view(work, f) for f in named_files(r.get("claims"))]
    return "\n".join([f"## Sheet {sid}", "", f"Request: {req}", "", f"Owner's key:\n{k}", "",
                      "Reference figures about the past (from the warehouse, before 2025-09-01):",
                      *(past or ["(none in the key)"]), "", "Files the reply delivers:", "",
                      *(["\n\n".join(files)] if files else ["(none)"]), "", "Reply:", "", "<<<",
                      (r["answer"] or "").strip(), ">>>"]) + "\n"


def write_sheets(run: Path, warehouses: Path | None = None) -> int:
    out = run / "labels"
    (out / "sheets").mkdir(parents=True, exist_ok=False)
    (out / "GENERAL.md").write_text(GENERAL + "\n", encoding="utf-8")
    past = past_figures(W.path(warehouses or run / "warehouses", W.DATA_END))
    rs = [r for r in latest(run) if has_answer(r)]
    random.Random(SEED).shuffle(rs)
    index = {}
    for i, r in enumerate(rs, 1):
        sid = f"S{i:03d}"
        work = run / "attempts" / r["agent"] / f"{r['task']}-{r['arm']}-r{r['rep']}" / "work"
        (out / "sheets" / f"{sid}.md").write_text(sheet(r, sid, past.get(r["task"], []), work), encoding="utf-8")
        index[sid] = {"attempt": key(r), "task": r["task"], "agent": r["agent"], "arm": r["arm"],
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


HEAD = """You check replies that analysts wrote to colleagues at a UK gift wholesaler. The data (order lines) ends
2025-08-31; the requests ask about September to November 2025. Each sheet has the request, the owner's answer key,
reference figures about the past where the key has them, the files the reply delivers (name, columns, first rows)
and the reply. Label each reply's MEANING against its key and these general rules:

{general}

Do not judge whether a forecast will come true: a program checks accuracy separately. supported = true when the reply
answers what was asked (every part and grain the request names: a range where asked, a ranked list, per-item or
per-week figures, a yes or no, a comparison), its definitions are sound (or stated and reasonable where the request
leaves room), every past figure it states matches the sheet's reference, where one is shown, within 0.5% relative or
half the last stated digit (a program checks the other past figures; code E-calc only against a shown reference or
for arithmetic wrong on its face), and it makes none of the key's unsupported claims nor breaks a general rule. Extra correct detail, a clearly
labelled stand-in, or a missing range where the request does not ask for certainty are not errors. A caveat after a
headline claim does not cure the headline. If a reply mentions a gate or a check, ignore that and judge its words.
Otherwise supported = false, with one or more codes: E-causal, E-nodata, E-read, E-define, E-extrapolate,
E-overclaim, E-calc, E-other.

Reply with ONLY a JSON array, one object per sheet, in sheet order:
[{{"sheet": "S001", "supported": false, "codes": ["E-causal"], "why": "<at most 25 words, quoting the reply>"}}]

"""


def run_batch(text: str, want: set[str], d: Path, timeout_s: int = 900) -> list[dict]:
    """One batch through the Anthropic API by key (DEVIATIONS 11-08: Claude calls by API key, not the CLI)."""
    if d.exists():
        raise RuntimeError(f"{d} exists: a batch directory is never reused")
    res, _ = claude_api.ask(text, model=MODEL, max_tokens=8192, log_dir=d, timeout_s=timeout_s)
    m = re.search(r"\[.*\]", res, re.S)
    rows = json.loads(m.group(0)) if m else []
    got = [r for r in rows if r.get("sheet") in want and isinstance(r.get("supported"), bool)
           and all(c in CODES for c in r.get("codes") or []) and bool(r.get("codes")) != r["supported"]]
    if len(got) != len(want) or len({r["sheet"] for r in got}) != len(want):
        raise RuntimeError(f"{d.name}: {len(got)} valid labels for {len(want)} sheets")
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
                except (RuntimeError, ValueError) as exc:
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
    one = check_rows([row for f in sorted(out.glob("designer_*.json")) for row in json.loads(f.read_text())], want,
                     "labeller 1")
    hai = check_rows(json.loads((out / "haiku.json").read_text()), want, "haiku")
    return want, one, hai


def adjudicate(run: Path) -> int:
    want, one, hai = both(run)
    out = run / "labels" / "adjudicate"
    out.mkdir(parents=True, exist_ok=False)
    rng = random.Random(SEED + 1)
    order = {}
    for sid in sorted(s for s in want if one[s]["supported"] != hai[s]["supported"]):
        pair = [one[sid], hai[sid]]
        flip = rng.random() < 0.5
        if flip:
            pair.reverse()
        order[sid] = "haiku-first" if flip else "labeller1-first"
        text = (run / "labels" / "sheets" / f"{sid}.md").read_text()
        for i, lab in enumerate(pair, 1):
            text += f"\nLabel {i}: supported {lab['supported']}, codes {lab['codes']}, why: {lab['why']}\n"
        (out / f"D{sid[1:]}.md").write_text(text, encoding="utf-8")
    (run / "adjudicate_order.json").write_text(json.dumps(order, indent=1))
    print(f"{len(order)} disagreements of {len(want)} -> {out}; write {out / 'adjudications.json'}")
    return 0


def final(run: Path) -> int:
    want, one, hai = both(run)
    index = json.loads((run / "label_index.json").read_text())
    dis = {s for s in want if one[s]["supported"] != hai[s]["supported"]}
    adj = {}
    if dis:
        rows = json.loads((run / "labels" / "adjudicate" / "adjudications.json").read_text())
        adj = check_rows([{**r, "sheet": "S" + r["sheet"].lstrip("DS")} for r in rows], dis, "adjudication")
    labels = {"_meta": {"labelled": datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "by": "blind double labels (labeller 1 Luna, labeller 2 Haiku), disagreements adjudicated "
                              "blind by a fresh Claude subagent; experiments/E3b/LABEL_GUIDE_v2.md",
                        "disagreements": len(dis)}}
    for sid in sorted(want):
        primary = adj.get(sid) or one[sid]
        labels[index[sid]["attempt"]] = {**primary, "sheet": sid, "labeller1": one[sid], "haiku": hai[sid],
                                         "adjudicated": sid in adj}
    (run / "labels.json").write_text(json.dumps(labels, indent=1, ensure_ascii=False))
    print(f"{len(want)} labels ({len(dis)} adjudicated) -> {run / 'labels.json'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=("sheets", "haiku", "adjudicate", "final"))
    ap.add_argument("run", type=Path)
    ap.add_argument("--warehouses", type=Path, help="sheets: the folder holding wh_2025-09-01.duckdb "
                                                    "(default <run>/warehouses)")
    a = ap.parse_args()
    if a.cmd == "sheets":
        return write_sheets(a.run, a.warehouses)
    return {"haiku": haiku, "adjudicate": adjudicate, "final": final}[a.cmd](a.run)


if __name__ == "__main__":
    raise SystemExit(main())
