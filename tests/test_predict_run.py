"""predict_run: the agent's predict.py runs sandboxed, sees only its source files and the warehouse it is given."""
import json
from pathlib import Path
import shutil
import sys

import duckdb
import pytest

from dsharness.readout.predict_run import code_files, code_sha, run_predict

pytestmark = pytest.mark.skipif(shutil.which("bwrap") is None, reason="needs bubblewrap")
ENV = Path(sys.prefix)                            # this venv has duckdb
BASE = [Path(sys.base_prefix)]                   # its interpreter (uv python, under the hidden /home)

PREDICT = """
import argparse, json, os, pathlib, socket
import duckdb
ap = argparse.ArgumentParser(); [ap.add_argument(x) for x in ("--asof", "--warehouse", "--out")]
a = ap.parse_args()
n = duckdb.connect(a.warehouse, read_only=True).execute("select count(*) from t").fetchone()[0]
seen = sorted(os.listdir("."))
secret = any(os.path.exists(os.path.expanduser(f)) for f in ("~/.codex/auth.json", "~/.claude/.credentials.json",
                                                              "~/.ssh", "REPO/workspace", "REPO/src"))
try:
    socket.create_connection(("1.1.1.1", 53), timeout=2); net = True
except OSError:
    net = False
doc = {"asof": a.asof, "claims": [{"id": "n", "type": "total", "says": "x", "quantity": "orders", "filter": {},
       "window": [a.asof, a.asof], "by": None, "values": [{"key": None, "point": n}]}], "seen": seen, "secret": secret,
       "net": net}
json.dump(doc, open(os.path.join(a.out, "claims.json"), "w"))
"""


def wh(path: Path, n: int) -> Path:
    con = duckdb.connect(str(path))
    con.execute(f"create table t as select range as x from range({n})")
    con.close()
    return path


def test_runs_isolated_on_the_given_warehouse(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "predict.py").write_text(PREDICT.replace("REPO", str(Path(__file__).resolve().parents[1])))
    (work / "helper.py").write_text("X = 1\n")
    (work / "model.pkl").write_bytes(b"fitted")             # not copied: no saved models
    (work / "claims.json").write_text("{}")                  # not copied: no delivered output
    (work / "scores.csv").write_text("a\n1\n")               # not copied: no data
    assert [str(p) for p in code_files(work)] == ["helper.py", "predict.py"]
    r = run_predict(work, wh(tmp_path / "w.duckdb", 7), "2025-06-01", tmp_path / "run", tool_env=ENV, ro_paths=BASE)
    assert r["error"] is None, r
    d = r["claims"]
    assert d["claims"][0]["values"][0]["point"] == 7
    assert d["seen"] == ["helper.py", "predict.py", "warehouse.duckdb"]
    assert d["secret"] is False and d["net"] is False


def test_a_failing_script_returns_its_error_and_sha_tracks_code(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "predict.py").write_text("raise SystemExit('no model here')\n")
    s1 = code_sha(work)
    r = run_predict(work, wh(tmp_path / "w.duckdb", 1), "2025-06-01", tmp_path / "run", tool_env=ENV, ro_paths=BASE)
    assert r["claims"] is None and "no model here" in r["error"]
    (work / "notes.md").write_text("ignored")
    assert code_sha(work) == s1
    (work / "predict.py").write_text("pass\n")
    assert code_sha(work) != s1
    r = run_predict(work, tmp_path / "w.duckdb", "2025-06-01", tmp_path / "run", tool_env=ENV, ro_paths=BASE)
    assert "no claims.json" in r["error"]
    json.dumps(r)
