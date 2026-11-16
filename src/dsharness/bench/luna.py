"""Run GPT-6 Luna (Codex CLI, reasoning high, no web) once in the bwrap sandbox, for labelling and reviews.

The sandbox sees only its own work directory (files given here) and a sanitised Codex home; the repository, the
runs and the host's home stay hidden. Luna may read and write files in the work directory.

    text, work = ask(prompt, run_dir, files={"sheets/S001.md": "..."})
"""
from __future__ import annotations

from pathlib import Path
import subprocess

from dsharness.bench import sandbox as sb
from dsharness.bench.census import parse_codex_output

MODEL = "gpt-6-luna"
CONFIG = f'model = "{MODEL}"\nmodel_reasoning_effort = "high"\nweb_search = "disabled"\n'


class LunaError(RuntimeError):
    pass


TOOL_ENV = (Path(__file__).resolve().parents[3].parent / "agent_env_dt").resolve()   # the agents' Python env
UV_PYTHON = sb.HOME / ".local/share/uv/python"


def ask(prompt: str, d: Path, files: dict[str, str] | None = None, timeout_s: int = 2400,
        python: bool = False) -> tuple[str, Path]:
    """One Codex exec in ``d`` (created; must not exist). Returns Luna's last message and the work directory.
    ``python``: put the agents' tool environment (pandas, duckdb, ...) on PATH, read-only."""
    work, home = d / "work", d / "home"
    work.mkdir(parents=True, exist_ok=False)
    for name, text in (files or {}).items():
        (work / name).parent.mkdir(parents=True, exist_ok=True)
        (work / name).write_text(text, encoding="utf-8")
    cli = sb.find_cli("codex")
    sb.prepare_home(home, cli, codex_config=CONFIG)
    cmd = [str(cli.binary), "exec", "--skip-git-repo-check", "--dangerously-bypass-approvals-and-sandbox", "--json",
           "-o", f"{sb.HOME}/last_message.txt", prompt]
    try:
        extra = dict(ro_paths=[TOOL_ENV, UV_PYTHON],
                     env={"PATH": f"{TOOL_ENV}/bin:{cli.binary_dir}:/usr/bin:/bin", "OMP_NUM_THREADS": "1"}) if python else {}
        p = subprocess.run(sb.bwrap_command(cmd, work=work, home=home, cli=cli, **extra), capture_output=True, text=True,
                           timeout=timeout_s, stdin=subprocess.DEVNULL)
        out, err, rc = p.stdout, p.stderr, p.returncode
    except subprocess.TimeoutExpired as exc:
        out, err, rc = exc.stdout or "", f"timed out after {timeout_s}s", 124
    out = out.decode() if isinstance(out, bytes) else out
    (d / "stdout.txt").write_text(out)
    (d / "stderr.txt").write_text(err)
    last = home / "last_message.txt"
    res = parse_codex_output(out, rc, last.read_text() if last.exists() else None, err)
    if res["status"] != "OK":
        raise LunaError(f"{d.name}: {res['reason']}")
    return res["text"] or "", work
