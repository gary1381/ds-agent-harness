"""Column schemas of the TLC trip files, read from each parquet footer only (HTTP range requests, a few KB a file).

Finds real schema drift between months (columns added, removed, renamed or retyped) to build held-out E3d tasks.
Nothing but the footer is downloaded: the last 8 bytes give the footer length, then the footer is fetched.
Gary OK'd the footer reads on 2026-11-07.

    agent_env_dt/bin/python dsharness/experiments/E3d/tlc_footers.py --out dsharness/experiments/E3d/tlc_footers.json
"""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import time
import urllib.error
import urllib.request

import pyarrow.parquet as pq

BASE = "https://d37ci6vzurychx.cloudfront.net/trip-data"
UA = {"User-Agent": "ds-harness research (footer read)"}


def get(url: str, rng: str) -> tuple[bytes, int]:
    req = urllib.request.Request(url, headers={**UA, "Range": f"bytes={rng}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        total = int(r.headers["Content-Range"].split("/")[1])
        return r.read(), total


def footer(url: str) -> dict:
    tail, size = get(url, "-8")
    if tail[4:] != b"PAR1":
        raise ValueError("not a parquet file")
    n = int.from_bytes(tail[:4], "little")
    meta, _ = get(url, f"{size - 8 - n}-{size - 1}")
    md = pq.read_metadata(io.BytesIO(b"PAR1" + meta))   # header magic + footer is enough for the metadata
    nulls = {}
    for i in range(md.num_columns):                     # per-column null counts from the row-group statistics
        stats = [md.row_group(g).column(i).statistics for g in range(md.num_row_groups)]
        if all(st is not None and st.has_null_count for st in stats):
            nulls[md.schema.column(i).name] = sum(st.null_count for st in stats)
    return {"bytes": size, "rows": md.num_rows, "row_groups": md.num_row_groups, "footer_bytes": n + 8,
            "columns": [[f.name, str(f.physical_type), str(f.logical_type)] for f in md.schema], "nulls": nulls}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--kinds", nargs="+", default=["yellow", "green"])
    ap.add_argument("--years", nargs=2, type=int, default=[2019, 2026])
    ap.add_argument("--months", nargs="+", help="only these YYYY-MM")
    ap.add_argument("--redo", action="store_true", help="read again files already recorded (errors or no null counts)")
    ap.add_argument("--delay", type=float, default=2.0, help="seconds between files")
    a = ap.parse_args()
    out = json.loads(a.out.read_text()) if a.out.exists() else {}
    refused = 0
    for kind in a.kinds:
        for y in range(a.years[0], a.years[1] + 1):
            for m in range(1, 13):
                key = f"{kind}_tripdata_{y}-{m:02d}"
                if a.months and f"{y}-{m:02d}" not in a.months:
                    continue
                if key in out and not (a.redo and ("error" in out[key] or "nulls" not in out[key])):
                    continue
                try:
                    out[key] = footer(f"{BASE}/{key}.parquet")
                except urllib.error.HTTPError as e:
                    out[key] = {"error": f"HTTP {e.code}"}
                except Exception as e:  # noqa: BLE001 - recorded, not hidden
                    out[key] = {"error": f"{type(e).__name__}: {e}"}
                print(key, out[key].get("rows", out[key].get("error")), flush=True)
                a.out.write_text(json.dumps(out, indent=1))
                refused = refused + 1 if out[key].get("error") == "HTTP 403" else 0
                if refused >= 3:   # unpublished months also answer 403, but three in a row means the server refuses us
                    print("three refusals in a row: stopping", flush=True)
                    return 1
                time.sleep(a.delay)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
