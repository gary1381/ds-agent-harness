"""Haiku pilot on E3b's real requests: does the Claude agent on Haiku 5.5 (API key) finish and deliver them, and at
what cost? Reuses E3b's frozen runner (run_v2.py, unchanged) with the Claude agent switched to the API key.

    systemd-run --user --unit dsh-e5-pilot --property=CPUAffinity=4,5 --working-directory=$PWD \
        .venv/bin/python experiments/E5/run_pilot_haiku.py --out experiments/E5/runs/pilot_haiku \
        --agents claude --tasks ha01 ha02 --arms N H --reps 1

The key is read as in docs/MODEL_POLICY.md: a 0600 file bound read-only into the sandbox. Each attempt is checked
against and charged to the daily budget, and the attempt directory is scrubbed of any copy of the key.
"""
from __future__ import annotations

import atexit
import importlib.util
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from dsharness.bench import budget, census, claude_api, sandbox as sb  # noqa: E402

spec = importlib.util.spec_from_file_location("run_v2", ROOT / "experiments/E3b/run_v2.py")
run_v2 = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(ROOT / "experiments/E3b"))
spec.loader.exec_module(run_v2)

assert census.AGENTS["claude"].get("api_model") == census.CLAUDE_API_MODEL, "set DSH_CLAUDE_TRANSPORT=api"
KEY_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / f"dsh-key-{os.getpid()}"
KEY_FILE = sb.write_key_file(KEY_DIR, claude_api.api_key())
atexit.register(shutil.rmtree, KEY_DIR, ignore_errors=True)
_find_cli, _run_attempt = sb.find_cli, run_v2.run_attempt


def find_cli(name, binary=None, **kw):
    return _find_cli(name, binary, api_key_file=KEY_FILE) if name == "claude" else _find_cli(name, binary, **kw)


def run_attempt(run, agent, task, arm, rep, timeout_s):
    if agent == "claude":
        budget.check(0.5)
    r = _run_attempt(run, agent, task, arm, rep, timeout_s)
    if agent == "claude":
        budget.charge(census.CLAUDE_API_MODEL, r.get("cost_usd") or 0.0, f"E5 pilot {task} {arm}")
        census._scrub(run / "attempts" / agent / f"{task}-{arm}-r{rep}", claude_api.api_key())
    return r


sb.find_cli = find_cli
run_v2.run_attempt = run_attempt
raise SystemExit(run_v2.main())
