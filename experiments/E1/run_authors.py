"""E1: have two outside models write the blind question set.

Each author runs in the bwrap sandbox (no repository, no harness code, no past transcripts), in its
own empty directory, and sees only docs/experiments/E1/author_brief.md.

    .venv/bin/python experiments/E1/run_authors.py --out experiments/E1/authors
    .venv/bin/python experiments/E1/run_authors.py --out experiments/E1b/authors   # E1b: same brief, new runs
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from dsharness.bench import sandbox as sb  # noqa: E402

BRIEF = ROOT / "docs/experiments/E1/author_brief.md"
AUTHORS = {
    "luna": {"cli": "codex", "model": "gpt-6-luna"},
    "haiku": {"cli": "claude", "model": "claude-haiku-5-5"},
}


def run_author(name: str, out: Path, brief: Path = BRIEF, timeout_s: int = 1800) -> dict:
    spec = AUTHORS[name]
    d = out / name
    work, home = d / "work", d / "home"
    work.mkdir(parents=True, exist_ok=False)
    cli = sb.find_cli(spec["cli"])
    prompt = brief.read_text()
    if spec["cli"] == "codex":
        sb.prepare_home(home, cli, codex_config=f'model = "{spec["model"]}"\nmodel_reasoning_effort = "high"\n'
                                                 'web_search = "disabled"\n')
        last = f"{sb.HOME}/last_message.txt"
        cmd = [str(cli.binary), "exec", "--skip-git-repo-check", "--dangerously-bypass-approvals-and-sandbox",
               "--json", "-o", last, prompt]
    else:
        sb.prepare_home(home, cli)
        cmd = [str(cli.binary), "-p", prompt, "--model", spec["model"], "--output-format", "json",
               "--tools", "", "--permission-mode", "dontAsk"]
    argv = sb.bwrap_command(cmd, work=work, home=home, cli=cli)
    p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s, stdin=subprocess.DEVNULL)
    (d / "stdout.txt").write_text(p.stdout)
    (d / "stderr.txt").write_text(p.stderr)
    if spec["cli"] == "codex":
        text = (home / "last_message.txt").read_text() if (home / "last_message.txt").exists() else ""
    else:
        text = json.loads(p.stdout).get("result", "") if p.stdout.strip().startswith("{") else ""
    (d / "questions_raw.yaml").write_text(text)
    return {"author": name, "exit": p.returncode, "chars": len(text)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--authors", nargs="+", default=list(AUTHORS))
    ap.add_argument("--brief", type=Path, default=BRIEF)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=2) as ex:
        for r in ex.map(lambda n: run_author(n, a.out, a.brief), a.authors):
            print(json.dumps(r))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
