"""E3b v2: GPT-6 Luna writes an independent `realise()` from docs/experiments/E3b/CLAIMS_SPEC.md alone.

Luna sees only the spec (no data, no designer code, no keys) and the agents' Python environment. It writes
`realise_luna.py`, its own tests on toy data, and AMBIGUITIES.md (every point it had to decide that the spec left
open). The designer's implementation (src/dsharness/readout/forecast.py) is compared with it on the development
data before the pre-registration is frozen (Luna's review of the plan, finding 9: an independent truth build).

    .venv/bin/python experiments/E3b/luna_realise.py           # -> experiments/E3b/runs/luna_realise/
    .venv/bin/python experiments/E3b/luna_realise.py --revise  # Luna updates it to the settled spec -> luna_realise2/
    .venv/bin/python experiments/E3b/luna_realise.py --round3  # edge cases 17-22 + speed -> luna_realise3/
"""
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from dsharness.bench.luna import ask  # noqa: E402

PROMPT = """Read CLAIMS_SPEC.md. Implement it in one Python module, realise_luna.py, with this entry point:

    def realise(claim: dict, lines: pandas.DataFrame, files_dir: pathlib.Path) -> dict

`lines` holds order lines with the spec's columns (ordered_at is a pandas Timestamp, amount and unit_price floats,
buyer_id a nullable integer, NaN/None for guests). It may run past the claim's window; your code must use only the
lines the spec allows. `files_dir` holds the files a claim names (lists, per-buyer files, buyer-id files). Return
exactly the output the spec describes, including {"id", "error"} for a claim that cannot be realised. Also write
`shift_claim(claim: dict, asof: str, origin: str) -> dict` for the backtest shift. Use pandas and numpy only (python3
on PATH has them).

Write test_realise_luna.py with toy data you construct by hand that checks every claim type, every quantity, every
filter, the week rule, ties in AUC, missing buyers in a list, and the history quantities; run it with
`python3 -m pytest -q` until it passes.

Write AMBIGUITIES.md: every point where the spec was unclear or allowed more than one reading, the reading you
chose, and why. Be specific; this list decides what the spec must say before it is frozen. Then reply "done"."""


REVISE = """CLAIMS_SPEC.md has been revised: its new last section, "Edge cases", settles the points of your
AMBIGUITIES.md, the signature is now realise(claim, lines, files_dir, claims_by_id=None, data_end=None), and the
section "Backtest origins" now gives the date-shift rule (months for firsts of a month, whole weeks for weekly
claims). Your earlier realise_luna.py and test_realise_luna.py are here. Update both so they follow the revised spec
exactly, including every edge case, and keep shift_claim(claim, asof, origin). Run the tests with
`python3 test_realise_luna.py` (pytest is not installed; make the file runnable that way) until they pass. Write
CHANGES.md: what you changed, and any point the revised spec still leaves open. Then reply "done"."""


ROUND3 = """CLAIMS_SPEC.md has a new subsection, "Settled after the first comparison" (edge cases 17-22). Your
realise_luna.py was compared with the designer's implementation on real development data: 173 cases, 7 apart, and
these points settle them. Update realise_luna.py and test_realise_luna.py to follow 17-22 exactly. Also make it fast:
the truth script runs it on about 700,000 lines and lists of about 5,000 buyers, so AUC must be O(n log n) (rank-based
Mann-Whitney, ties counting half) and no per-row Python loops over lines. Undefined metrics must be null (None), never
NaN. Run `python3 test_realise_luna.py` until it passes. Append to CHANGES.md what you changed. Then reply "done"."""


def main() -> int:
    if "--round3" in sys.argv:
        out, old = ROOT / "experiments/E3b/runs/luna_realise3", ROOT / "experiments/E3b/runs/luna_realise2"
        if out.exists():
            raise SystemExit(f"{out} exists")
        files = {"CLAIMS_SPEC.md": (ROOT / "docs/experiments/E3b/CLAIMS_SPEC.md").read_text(),
                 **{f: (old / f).read_text() for f in ("realise_luna.py", "test_realise_luna.py", "CHANGES.md")}}
        text, work = ask(ROUND3, out, files, timeout_s=3600, python=True)
        for f in ("realise_luna.py", "test_realise_luna.py", "CHANGES.md"):
            if (work / f).exists():
                shutil.copyfile(work / f, out / f)
        print(text[-300:])
        return 0
    revise = "--revise" in sys.argv
    out = ROOT / ("experiments/E3b/runs/luna_realise2" if revise else "experiments/E3b/runs/luna_realise")
    if out.exists():
        raise SystemExit(f"{out} exists")
    files = {"CLAIMS_SPEC.md": (ROOT / "docs/experiments/E3b/CLAIMS_SPEC.md").read_text()}
    if revise:
        old = ROOT / "experiments/E3b/runs/luna_realise"
        files |= {f: (old / f).read_text() for f in ("realise_luna.py", "test_realise_luna.py", "AMBIGUITIES.md")}
    text, work = ask(REVISE if revise else PROMPT, out, files, timeout_s=3600, python=True)
    for f in ("realise_luna.py", "test_realise_luna.py", "AMBIGUITIES.md", "CHANGES.md"):
        if (work / f).exists():
            shutil.copyfile(work / f, out / f)
    print(text[-500:], [f.name for f in out.glob("*.py")], (out / "AMBIGUITIES.md").exists())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
