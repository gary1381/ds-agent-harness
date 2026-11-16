"""Small shared helpers: canonical digests, UTC time, read-only warehouse connections."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

import duckdb


def canonical_json(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False,
                      ensure_ascii=False, default=str).encode("utf-8")


def digest(obj) -> str:
    """sha256 over canonical JSON. Identity for subjects, specs, policies."""
    return hashlib.sha256(canonical_json(obj)).hexdigest()


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
