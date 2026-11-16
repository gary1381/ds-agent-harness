"""E3c v2: blind review of the owner's answer key by an outside model (Luna, gpt-6-luna high), as for the E3d truth.

Luna sees the catalog the agents see, the 40 requests, the key and the reference numbers (aggregates only), copied
into an empty sandbox directory. It sees no harness code, no gate checks and no agent output. RESEARCH USE ONLY:
everything stays under experiments/E3c/runs/ (gitignored).

    .venv/bin/python experiments/E3c/review_keys.py --out experiments/E3c/runs/review_keys
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))
from dsharness.bench import sandbox as sb  # noqa: E402
from pilot import CATALOG  # noqa: E402

PROMPT = """You review the answer key of an evaluation set for AI data analysts. Analysts get a randomised e-mail
test's data file (described in `DATA_CATALOG.md`) and one request from a colleague (`bank_v2.yaml`). They reply
in writing. The answer key (`keys_v2.yaml`) says, for each request:
- what a supported reply does;
- which claims make a reply unsupported.

The numbers in the key come from `REFERENCE.md`, computed from the file.

Check every request's key against the request, the design and the reference numbers:
1. Is the supported reply correct and complete for what was asked? Is anything in it wrong, or stricter than a
   competent analyst could be expected to meet?
2. Would any claim listed as unsupported actually be defensible?
3. Is a likely unsupported claim missing?
4. Are the general rules at the top of the key sound for a randomised test with these columns?

Be concrete, and quote numbers. Write REVIEW.md:
- first, a table with columns `id | verdict (agree / change / unsure) | why`, one row per request;
- then, at most 600 words on the general rules and anything else.

Reply with a one-paragraph summary."""


def reference_md(ref: dict) -> str:
    f = lambda v: f"{v['estimate']:+.4f} [{v['low']:+.4f}, {v['high']:+.4f}]"  # noqa: E731
    L = ["# Reference numbers (95% normal intervals, unpooled variances; effects are e-mail group minus no e-mail, "
         "per customer assigned)", "", "## Groups", ""]
    L += [f"- {g}: {json.dumps({k: round(v, 5) if isinstance(v, float) else v for k, v in d.items()})}"
          for g, d in ref["groups"].items()]
    for sec in ("itt", "pooled", "per_buyer", "per_visitor", "gaps"):
        L += ["", f"## {sec}", ""] + [f"- {k}: {f(v)}" for k, v in ref[sec].items()]
    L += ["", "## relative_lift (mean ratio - 1)", ""] + [f"- {k}: {v:+.3f}" for k, v in ref["relative_lift"].items()]
    L += ["", "## subgroups (pre-campaign columns)", ""]
    for k, s in ref["subgroups"].items():
        L.append(f"- {k} (n={s['n']}): " + "; ".join(f"{m} {f(v)}" for m, v in s.items() if m != "n"))
    L += ["", "## heterogeneity (joint Wald test of group x subgroup interactions, OLS HC1)", ""]
    L += [f"- {k}: chi2 {v['chi2']:.1f}, df {v['df']}, p {v['p']:.4f}" for k, v in ref["heterogeneity"].items()]
    L += ["", "## mailed and control conversion by subgroup", ""]
    L += [f"- {k}: {json.dumps({g: round(x, 4) for g, x in v.items()})}" for k, v in ref["mailed_conversion"].items()]
    b = ref["balance"]
    L += ["", "## balance", "", f"- counts {b['counts']}, sample-ratio chi2 {b['srm_chi2']:.2f} p {b['srm_p']:.3f}, "
          f"max |SMD| {b['max_abs_smd']:.4f}"]
    L += ["", "## business inputs stated in requests", "", "```json", json.dumps(ref["inputs"], indent=1), "```"]
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--timeout", type=int, default=1800)
    a = ap.parse_args()
    work, home = a.out / "work", a.out / "home"
    work.mkdir(parents=True, exist_ok=False)
    for f in ("bank_v2.yaml", "keys_v2.yaml"):
        if not (HERE / f).exists():   # not distributed in the public copy (docs/DATA.md)
            raise SystemExit(f"experiments/E3c/{f} is not distributed with the public copy; see docs/DATA.md")
        shutil.copyfile(HERE / f, work / f)
    (work / "DATA_CATALOG.md").write_text(CATALOG, encoding="utf-8")
    ref = json.loads((HERE / "runs/reference_v2.json").read_text())
    (work / "REFERENCE.md").write_text(reference_md(ref), encoding="utf-8")
    (a.out / "prompt.md").write_text(PROMPT, encoding="utf-8")
    cli = sb.find_cli("codex")
    sb.prepare_home(home, cli, codex_config='model = "gpt-6-luna"\nmodel_reasoning_effort = "high"\n'
                                            'web_search = "disabled"\n')
    cmd = [str(cli.binary), "exec", "--skip-git-repo-check", "--dangerously-bypass-approvals-and-sandbox",
           "--json", "-o", f"{sb.HOME}/last_message.txt", PROMPT]
    argv = sb.bwrap_command(cmd, work=work, home=home, cli=cli)
    p = subprocess.run(argv, capture_output=True, text=True, timeout=a.timeout, stdin=subprocess.DEVNULL)
    (a.out / "stdout.txt").write_text(p.stdout)
    (a.out / "stderr.txt").write_text(p.stderr)
    print(json.dumps({"exit": p.returncode, "files": sorted(f.name for f in work.iterdir())}))
    return 0 if p.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
