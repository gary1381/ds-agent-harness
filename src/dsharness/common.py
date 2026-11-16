"""Small shared helpers: canonical digests, UTC time, read-only warehouse connections."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path

import duckdb


def canonical_json(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False,
                      ensure_ascii=False, default=str).encode("utf-8")


def digest(obj) -> str:
    """sha256 over canonical JSON. Identity for subjects, specs, policies."""
    return hashlib.sha256(canonical_json(obj)).hexdigest()


def values_digest(rows: list) -> str:
    """sha256 of answer rows read as numbers rounded to 12 significant digits: 611761 and 611761.0, or a
    float retyped without its 17th digit, digest alike, and row order does not matter. Used to match the
    rows an agent reports to the rows it submitted (H v0.3 S2)."""
    def num(v):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return v
        try:
            f = float(v)
        except OverflowError:                       # an int beyond float range
            return str(v)
        return float(f"{f:.12g}") + 0.0 if math.isfinite(f) else str(v)     # + 0.0: -0.0 reads as 0.0
    canon = sorted(canonical_json({str(k): num(v) for k, v in r.items()} if isinstance(r, dict) else num(r))
                   for r in rows)
    return hashlib.sha256(b"\n".join(canon)).hexdigest()


def file_digest(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def utcnow() -> str:
    """ISO-8601 UTC. Tests may pin time with DSH_NOW."""
    pinned = os.environ.get("DSH_NOW")
    if pinned:
        return pinned
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_ts(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def open_readonly(path: str | Path) -> duckdb.DuckDBPyConnection:
    """Agent-facing connection: read-only file, no filesystem/network access, config locked."""
    con = duckdb.connect(str(path), read_only=True)
    con.execute("SET enable_external_access = false")
    con.execute("SET lock_configuration = true")
    return con


def append_jsonl(path: str | Path, record: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str, sort_keys=True) + "\n")


def read_jsonl(path: str | Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]
