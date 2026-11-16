"""Run an agent's `predict.py --asof D` in a bwrap sandbox on a given warehouse (the forecast gate's runs).

Only the agent's source files are copied in (CODE_SUFFIXES, each at most MAX_CODE_BYTES): no data, no fitted model,
no delivered output, so every number has to be rebuilt from the warehouse it is given. The sandbox has no network,
no host home and no credentials; the warehouse is bound read-only at `warehouse.duckdb` in the working directory
(so a script that ignores `--warehouse` still sees the cut copy) and `--out` is a fresh empty directory.

    out = run_predict(work, warehouse, "2025-06-01", run_dir / "p", tool_env=TOOL_ENV, ro_paths=[UV_PYTHON])
    # {"claims": {...} | None, "files": {name: text}, "error": str | None, "seconds": float}
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

CODE_SUFFIXES = (".py", ".sql", ".yaml", ".yml", ".toml")
MAX_CODE_BYTES = 1_000_000
MAX_OUT_BYTES = 50_000_000
HOME = Path.home()
WORK_IN = str(HOME / "work")
OUT_IN = str(HOME / "gate_out")
SKIP_DIRS = {"__pycache__", ".git", ".venv", "venv", "node_modules", "gate_out"}


def code_files(work: Path) -> list[Path]:
    """The agent's source files, relative to work, in a fixed order."""
    out = []
    for p in sorted(work.rglob("*")):
        rel = p.relative_to(work)
        if p.is_file() and p.suffix in CODE_SUFFIXES and not (set(rel.parts[:-1]) & SKIP_DIRS) \
                and p.stat().st_size <= MAX_CODE_BYTES:
            out.append(rel)
    return out


def code_sha(work: Path) -> str:
    h = hashlib.sha256()
    for rel in code_files(work):
        h.update(str(rel).encode() + b"\0" + (work / rel).read_bytes() + b"\0")
    return h.hexdigest()


def copy_code(work: Path, dest: Path) -> None:
    for rel in code_files(work):
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(work / rel, dest / rel)


def run_predict(work: Path, warehouse: Path, asof: str, d: Path, *, tool_env: Path, ro_paths: list[Path] = (),
                timeout_s: int = 600) -> dict:
    """One run in ``d`` (replaced). Reads `claims.json` and the files its claims name from the output directory."""
    if d.exists():
        shutil.rmtree(d)
    code, out = d / "code", d / "out"
    code.mkdir(parents=True)
    out.mkdir()
    copy_code(work, code)
    if not (code / "predict.py").exists():
        return {"claims": None, "files": {}, "error": "there is no predict.py in the work directory", "seconds": 0.0}
    (code / "warehouse.duckdb").touch()                          # mount point
    a = ["bwrap", "--die-with-parent", "--new-session", "--unshare-all", "--ro-bind", "/", "/", "--dev", "/dev",
         "--proc", "/proc", "--tmpfs", "/home", "--tmpfs", "/tmp", "--tmpfs", "/run/user"]
    for hidden in ("/mnt", "/media", "/root", "/srv", "/var/tmp", "/opt", "/snap"):
        if Path(hidden).is_dir():
            a += ["--tmpfs", hidden]
    for p in [tool_env, *ro_paths]:
        p = Path(p).resolve()
        a += ["--ro-bind", str(p), str(p)]
    a += ["--bind", str(code), WORK_IN, "--ro-bind", str(Path(warehouse).resolve()), f"{WORK_IN}/warehouse.duckdb",
          "--bind", str(out), OUT_IN, "--chdir", WORK_IN, "--clearenv"]
    env = {"HOME": str(HOME), "LANG": "C.UTF-8", "PATH": f"{Path(tool_env).resolve()}/bin:/usr/bin:/bin",
           "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "POLARS_MAX_THREADS": "1",
           "PYTHONHASHSEED": "0", "MPLBACKEND": "Agg", "USER": os.environ.get("USER", "agent")}
    for k, v in env.items():
        a += ["--setenv", k, v]
    a += ["--", "python", "predict.py", "--asof", asof, "--warehouse", "warehouse.duckdb", "--out", OUT_IN]
    t0 = time.perf_counter()
    try:
        p = subprocess.run(a, capture_output=True, text=True, timeout=timeout_s, stdin=subprocess.DEVNULL)
        rc, err = p.returncode, p.stderr
    except subprocess.TimeoutExpired:
        rc, err = 124, f"predict.py did not finish within {timeout_s} s"
    secs = round(time.perf_counter() - t0, 1)
    (d / "stderr.txt").write_text(err or "")
    res = {"claims": None, "files": {}, "error": None, "seconds": secs}
    if rc != 0:
        tail = (err or "").strip().splitlines()[-6:]
        res["error"] = f"exit {rc}: " + " | ".join(tail)[-600:]
        return res
    cj = out / "claims.json"
    if not cj.exists():
        res["error"] = "predict.py wrote no claims.json into --out"
        return res
    try:
        res["claims"] = json.loads(cj.read_text())
    except ValueError as exc:
        res["error"] = f"claims.json written by predict.py is not valid JSON: {exc}"
        return res
    claims = res["claims"].get("claims") if isinstance(res["claims"], dict) else None
    for c in claims if isinstance(claims, list) else []:
        f = c.get("file") if isinstance(c, dict) else None
        if not isinstance(f, str):
            continue
        fp = (out / f).resolve()
        if out.resolve() not in fp.parents or not fp.is_file():
            res["files"][f] = None
        elif fp.stat().st_size > MAX_OUT_BYTES:
            res["files"][f] = None
            res["error"] = f"{f} is larger than {MAX_OUT_BYTES // 1_000_000} MB"
        else:
            res["files"][f] = fp.read_text(errors="replace")
    return res
