"""The forecast gate (E3b v2): program checks on a forecast reply, its claims.json and its predict.py runs.

Library checks only: they take the outputs the caller already produced (the agent's predict.py run in a sandbox on
warehouses cut at several dates, by experiments/E3b/mcp_model.py) and return results; nothing here runs agent code,
calls a model or opens the sealed months. Hard holds are mechanical (Luna's review of the plan, finding 4):

  f.schema       claims.json parses and every claim has the fields of CLAIMS_SPEC.md          FAIL
  f.window       forecast windows start on or after asof and end within three months of it    FAIL
  f.observed     a claim about the past (window ends by asof) equals the warehouse            FAIL
  f.numbers      every number in answer.md is a claim's value, interval end, k, or realised  INCONCLUSIVE
                 past value
  f.reproduce    predict.py --asof <asof> gives the delivered claims and files               FAIL
  f.timetravel   predict.py at the development origin gives the same output on the full     FAIL
                 warehouse and on one cut at that origin
  f.range        a stated range is narrower than predict.py's median past error at the       FAIL
                 backtest origins (a range that would have missed at least half of them)
  f.backtest     past errors, interval coverage and list metrics at each origin              advisory (PASS + notes)

Verdict: any FAIL -> REJECT, else any INCONCLUSIVE -> HOLD, else ALLOW. Reasons state what failed and the measured
facts; they never propose a replacement value or definition (E4: gate reasons steered agents to wrong answers).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
import math
from pathlib import Path
import re

import pandas as pd

from dsharness.readout.forecast import BY, QUANTITIES, REASONS, realise, realise_file

EST_REL = 0.005          # a stated number matches a value within 0.5% relative, or half its last stated digit
HORIZON_MONTHS = 3
MIN_ORIGINS = 3          # f.range needs at least this many realised backtest origins


@dataclass
class Check:
    name: str
    result: str          # PASS | FAIL | INCONCLUSIVE | ERROR
    reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# --- numbers in prose ----------------------------------------------------------------------------------------------

MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec"
DATES = re.compile(rf"\b\d{{4}}-\d{{2}}(?:-\d{{2}})?\b|\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{MONTHS})[a-z]*\b|"
                   rf"\b(?:{MONTHS})[a-z]*\s+\d{{1,2}}(?:st|nd|rd|th)?\b|\b(?:week|wk|w|q)\s?\d{{1,2}}\b", re.I)
NUM = re.compile(r"(?<![\w.])([-+−]?)(£|\$|€)?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s*"
                 r"(%|pp\b|k\b|K\b|m\b|M\b|bn\b|million\b|thousand\b)?")
MULT = {"k": 1e3, "K": 1e3, "thousand": 1e3, "m": 1e6, "M": 1e6, "million": 1e6, "bn": 1e9}


def _half_unit(whole: str, frac: str | None) -> float:
    if frac:
        return 0.5 * 10 ** -len(frac[1:])
    digits = whole.replace(",", "").rstrip("0")
    zeros = len(whole.replace(",", "")) - len(digits)
    return 0.5 * 10 ** zeros if digits else 0.5


def numbers_in(text: str) -> list[tuple[str, float, float]]:
    """(as written, value, half unit) for each number in the prose; dates, weeks, years and small bare counts
    (list markers, "three months" as 3) are left out."""
    clean = DATES.sub(" ", text or "")
    out = []
    for m in NUM.finditer(clean):
        sign, cur, whole, frac, unit = m.groups()
        v = float(whole.replace(",", "") + (frac or ""))
        if not cur and not unit and not frac and (v <= 12 or 1990 <= v <= 2035):
            continue
        mult = MULT.get(unit or "", 1)
        out.append((m.group(0).strip(), (-v if sign in ("-", "−") else v) * mult, _half_unit(whole, frac) * mult))
    return out


def covered(v: float, hu: float, refs: list[float]) -> bool:
    for ref in refs:
        for s in (1, 100):                       # shares and rates may be written as percentages
            if abs(abs(v) - abs(ref * s)) <= max(hu, EST_REL * abs(ref * s)) + 1e-9:
                return True
    return False


def claim_numbers(claims: list[dict]) -> list[float]:
    out = []
    for c in claims:
        for v in c.get("values") or []:
            out += [x for x in (v.get("point"), v.get("low"), v.get("high"), v.get("level")) if _num(x)]
        for m in c.get("claimed") or []:
            out += [x for x in (m.get("point"), m.get("low"), m.get("high"), m.get("level")) if _num(x)]
        out += [x for x in (c.get("k"), c.get("value"), c.get("probability"), c.get("level")) if _num(x)]
        if c.get("event"):
            out += [c["event"]["value"]] if _num(c["event"].get("value")) else []
        f = (c.get("filter") or {}).get("buyers") or {}
        out += [f["n"]] if _num(f.get("n")) else []
    return [float(x) for x in out]


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


# --- schema --------------------------------------------------------------------------------------------------------

TYPES = ("total", "share", "list", "per_buyer", "not_identifiable", "verdict")


def schema_errors(doc) -> list[str]:
    if not isinstance(doc, dict) or not isinstance(doc.get("claims"), list) or not doc["claims"]:
        return ["claims.json must be an object with a non-empty list `claims`"]
    errs, ids = [], set()
    for i, c in enumerate(doc["claims"]):
        where = f"claim {i + 1} ({c.get('id') if isinstance(c, dict) else '?'})"
        if not isinstance(c, dict):
            errs.append(f"{where}: not an object")
            continue
        if not c.get("id") or c["id"] in ids:
            errs.append(f"{where}: missing or repeated id")
        ids.add(c.get("id"))
        t = c.get("type")
        if t not in TYPES:
            errs.append(f"{where}: type must be one of {', '.join(TYPES)}")
            continue
        if not isinstance(c.get("says"), str) or not c["says"].strip():
            errs.append(f"{where}: `says` must quote the reply's sentence")
        if t in ("total", "share", "per_buyer"):
            if t != "share" and c.get("quantity") not in QUANTITIES:
                errs.append(f"{where}: quantity must be one of {', '.join(QUANTITIES)}")
            if t == "share" and not all(isinstance(c.get(s), dict) and c[s].get("quantity") in QUANTITIES
                                        for s in ("numerator", "denominator")):
                errs.append(f"{where}: numerator and denominator need a quantity from the list")
            if c.get("by") not in BY:
                errs.append(f"{where}: by must be null, week, month, country, sku or buyer_id")
            if t != "per_buyer":
                for v in c.get("values") or [None]:
                    if not isinstance(v, dict) or not _num(v.get("point")):
                        errs.append(f"{where}: every value needs a numeric point")
                        break
                    band = [v.get("low"), v.get("high"), v.get("level")]
                    if any(x is not None for x in band) and not all(_num(x) for x in band):
                        errs.append(f"{where}: low, high and level go together")
                        break
                    if all(_num(x) for x in band) and not (v["low"] <= v["point"] <= v["high"] and 0 < v["level"] < 1):
                        errs.append(f"{where}: needs low <= point <= high and 0 < level < 1")
                        break
        if t == "list" and not all(k in c for k in ("file", "event", "population", "k")):
            errs.append(f"{where}: a list needs file, event, population and k")
        if t == "not_identifiable" and c.get("reason") not in REASONS:
            errs.append(f"{where}: reason must be causal, absent_field or absent_population")
        if t == "verdict" and c.get("rests_on") not in {x.get("id") for x in doc["claims"] if isinstance(x, dict)}:
            errs.append(f"{where}: rests_on must name another claim")
    return errs


def windows(c: dict) -> list[list[str]]:
    w = [c["window"]] if c.get("window") else []
    if c.get("event", {}).get("window"):
        w.append(c["event"]["window"])
    return w


def _months_after(asof: str, n: int) -> date:
    p = pd.Period(asof[:7], freq="M") + n
    return p.to_timestamp().date()


# --- the checks ----------------------------------------------------------------------------------------------------

def run_checks(answer: str, doc: dict, files_dir: Path, lines: pd.DataFrame, asof: str,
               reproduce: dict | None = None, timetravel: tuple[dict, dict] | None = None,
               backtest: dict[str, dict] | None = None, facts: list[float] = ()) -> list[Check]:
    """`facts`: numbers the catalog itself states (they need no claim). `lines`: the agent's warehouse (ends the day before asof). `reproduce`: predict.py's output at asof
    ({"claims": doc, "files": {name: text}, "error": str|None}); `timetravel`: its outputs at the development origin
    on the full and the cut warehouse; `backtest`: {origin: {"claims": doc, "realised": [realise() rows]}}."""
    checks = []
    errs = schema_errors(doc)
    checks.append(Check("f.schema", "FAIL" if errs else "PASS", errs))
    if errs:
        return checks
    claims = doc["claims"]
    a = date.fromisoformat(asof)
    end_max = _months_after(asof, HORIZON_MONTHS)
    past, bad_w = [], []
    for c in claims:
        for w in windows(c):
            s, e = date.fromisoformat(w[0][:10]), date.fromisoformat(w[1][:10])
            if e <= a and c["type"] in ("total", "share"):
                past.append(c)
            elif s < a or e > end_max:
                bad_w.append(f"{c['id']}: window {w[0]}..{w[1]} is neither in the past nor within "
                             f"{asof}..{end_max.isoformat()}")
    checks.append(Check("f.window", "FAIL" if bad_w else "PASS", bad_w))

    by_id = {c["id"]: c for c in claims}
    obs_bad, realised_past = [], []
    for c in past:
        r = realise(c, lines, files_dir, by_id, asof)
        if "error" in r:
            obs_bad.append(f"{c['id']}: cannot be computed from the warehouse ({r['error']})")
            continue
        for v, got in zip(c["values"], r["values"]):
            realised_past.append(got["actual"])
            if got["actual"] is None or not covered(v["point"], _half_unit(*_parts(v["point"])), [got["actual"]]):
                obs_bad.append(f"{c['id']} {v.get('key') or ''}: states {v['point']}, the warehouse gives "
                               f"{got['actual']}".replace("  ", " "))
    checks.append(Check("f.observed", "FAIL" if obs_bad else "PASS", obs_bad))

    refs = claim_numbers(claims) + [x for x in realised_past if _num(x)] + [float(x) for x in facts]
    loose = [w for w, v, hu in numbers_in(answer) if not covered(v, hu, refs)]
    checks.append(Check("f.numbers", "INCONCLUSIVE" if loose else "PASS",
                        [f"numbers in answer.md with no claim: {', '.join(loose[:12])}"] if loose else []))

    checks.append(_reproduce(doc, files_dir, reproduce))
    checks.append(_timetravel(timetravel))
    checks += _backtest(doc, backtest or {})
    return checks


def _parts(x: float) -> tuple[str, str | None]:
    s = repr(float(x))
    whole, _, frac = s.partition(".")
    return whole.lstrip("-"), ("." + frac) if frac and frac != "0" else None


def _values(doc: dict) -> dict:
    out = {}
    for c in doc.get("claims", []):
        for v in c.get("values") or []:
            out[(c["id"], str(v.get("key")))] = tuple(v.get(k) for k in ("point", "low", "high"))
        for m in c.get("claimed") or []:
            out[(c["id"], m.get("metric"))] = tuple(m.get(k) for k in ("point", "low", "high"))
    return out


def _close(x, y) -> bool:
    if x is None or y is None:
        return x is None and y is None
    return abs(float(x) - float(y)) <= 1e-6 * max(1.0, abs(float(x)), abs(float(y)))


def _reproduce(doc: dict, files_dir: Path, rep: dict | None) -> Check:
    if rep is None:
        return Check("f.reproduce", "ERROR", ["predict.py was not run"])
    if rep.get("error"):
        return Check("f.reproduce", "FAIL", [f"predict.py --asof failed: {rep['error'][:300]}"])
    want, got = _values(doc), _values(rep["claims"])
    diff = [f"{i} {k}" for (i, k), v in want.items() if (i, k) not in got or not all(map(_close, v, got[(i, k)]))]
    extra = [f"{i} {k}" for (i, k) in got if (i, k) not in want]
    files = sorted({c["file"] for c in doc["claims"] if c.get("file")})
    fdiff = []
    for f in files:
        mine = (Path(files_dir) / f).read_text() if (Path(files_dir) / f).exists() else None
        d = "missing from the delivery" if mine is None else file_diff(mine, rep["files"].get(f))
        if d:
            fdiff.append(f"{f} ({d})")
    reasons = ([f"predict.py gives other values for: {', '.join(diff[:10])}"] if diff else []) + \
              ([f"predict.py gives claims the delivered file lacks: {', '.join(extra[:10])}"] if extra else []) + \
              ([f"predict.py writes different files: {', '.join(fdiff)}"] if fdiff else [])
    return Check("f.reproduce", "FAIL" if reasons else "PASS", reasons)


FILE_ABS, FILE_REL = 1e-5, 1e-6       # numeric cells this close are equal (row order and float noise are not leaks)


def _frame(text: str | None):
    if text is None:
        return None
    try:
        df = pd.read_csv(pd.io.common.StringIO(text), dtype=str)
    except Exception:          # not a CSV: compared as text
        return text.strip()
    return df.sort_values(list(df.columns)).reset_index(drop=True)


def file_diff(a: str | None, b: str | None) -> str | None:
    """None when two delivered files hold the same table (any row order; numbers within FILE_ABS or FILE_REL),
    else the measured difference, stated as a fact."""
    x, y = _frame(a), _frame(b)
    if x is None or y is None:
        return None if x is None and y is None else "present in one run only"
    if isinstance(x, str) or isinstance(y, str):
        return None if x == y else "different text"
    if list(x.columns) != list(y.columns):
        return f"columns {list(x.columns)} vs {list(y.columns)}"
    if len(x) != len(y):
        return f"{len(x)} vs {len(y)} rows"
    num = [c for c in x.columns if pd.to_numeric(x[c], errors="coerce").notna().all()
           and pd.to_numeric(y[c], errors="coerce").notna().all()]
    keys = [c for c in x.columns if c not in num]
    if keys:                                   # align rows on the non-numeric columns (e.g. ids), then compare
        x, y = x.sort_values(keys, kind="mergesort").reset_index(drop=True), \
            y.sort_values(keys, kind="mergesort").reset_index(drop=True)
        if not x[keys].equals(y[keys]):
            return f"different values in {', '.join(c for c in keys if not x[c].equals(y[c]))}"
    worst, where = 0.0, None
    for c in num:
        u, v = pd.to_numeric(x[c]).astype(float).values, pd.to_numeric(y[c]).astype(float).values
        if not keys:
            u, v = sorted(u), sorted(v)
        for p, q in zip(u, v):
            d = abs(p - q)
            if d > FILE_ABS and d > FILE_REL * max(abs(p), abs(q)) and d > worst:
                worst, where = d, c
    return None if where is None else f"largest difference {worst:.6g} in column {where}"


def _timetravel(tt: tuple[dict, dict] | None) -> Check:
    if tt is None:
        return Check("f.timetravel", "ERROR", ["predict.py was not run at the development origin"])
    full, cut = tt
    if full.get("error") or cut.get("error"):
        return Check("f.timetravel", "FAIL", [f"predict.py failed at the development origin: "
                                              f"{(full.get('error') or cut.get('error'))[:300]}"])
    a, b = _values(full["claims"]), _values(cut["claims"])
    diff = sorted(f"{i} {k}" for (i, k) in set(a) | set(b) if (i, k) not in a or (i, k) not in b
                  or not all(map(_close, a[(i, k)], b[(i, k)])))
    fdiff = sorted(f"{f} ({d})" for f in set(full["files"]) | set(cut["files"])
                   if (d := file_diff(full["files"].get(f), cut["files"].get(f))))
    reasons = ([f"with data after the origin present, predict.py gives other values for: {', '.join(diff[:10])}"]
               if diff else []) + ([f"with data after the origin present, predict.py writes other files: "
                                    f"{', '.join(fdiff)}"] if fdiff else [])
    return Check("f.timetravel", "FAIL" if reasons else "PASS", reasons)


def _backtest(doc: dict, bt: dict[str, dict]) -> list[Check]:
    """f.range (hard: a range narrower than the median past error) and f.backtest (advisory facts)."""
    errors: dict[tuple, list[float]] = {}
    cover: dict[tuple, list[bool]] = {}
    metrics: dict[tuple, list[float]] = {}
    failed = []
    for origin, r in sorted(bt.items()):
        if r.get("error"):
            failed.append(f"{origin}: {r['error'][:120]}")
            continue
        claims = {c["id"]: c for c in r["claims"].get("claims", [])}
        for row in r["realised"]:
            c = claims.get(row["id"])
            if c is None or "error" in row:
                continue
            if "values" in row:
                for pos, (v, got) in enumerate(zip(c.get("values") or [], row["values"])):
                    if got["actual"] in (None, 0) or not _num(v.get("point")):
                        continue
                    k = (row["id"], pos)
                    errors.setdefault(k, []).append(abs(v["point"] - got["actual"]) / abs(got["actual"]))
                    if _num(v.get("low")) and _num(v.get("high")):
                        cover.setdefault(k, []).append(v["low"] <= got["actual"] <= v["high"])
            for m in c.get("claimed") or []:
                got = (row.get("metrics") or {}).get(m.get("metric"))
                if _num(got):
                    metrics.setdefault((row["id"], m["metric"]), []).append(got)
    narrow, notes = [], []
    for c in doc["claims"]:
        for pos, v in enumerate(c.get("values") or []):
            e = errors.get((c["id"], pos), [])
            if not (_num(v.get("low")) and _num(v.get("high")) and v.get("point")):
                continue
            half = (v["high"] - v["low"]) / 2 / abs(v["point"])
            if len(e) >= MIN_ORIGINS:
                med = sorted(e)[len(e) // 2] if len(e) % 2 else sum(sorted(e)[len(e) // 2 - 1:len(e) // 2 + 1]) / 2
                if half < med:
                    narrow.append(f"{c['id']} {v.get('key') or ''}: stated {v['level']:.0%} range is ±{half:.1%} of "
                                  f"the point; at {len(e)} past origins predict.py's median error was {med:.1%}"
                                  .replace("  ", " "))
            if (c["id"], pos) in cover:
                cv = cover[(c["id"], pos)]
                notes.append(f"{c['id']} {v.get('key') or ''}: its own past ranges held at {sum(cv)} of {len(cv)} "
                             f"origins".replace("  ", " "))
        for m in c.get("claimed") or []:
            got = metrics.get((c["id"], m.get("metric")))
            if got:
                notes.append(f"{c['id']}: claimed {m['metric']} {m.get('point')}; at past origins "
                             f"{min(got):.3f}–{max(got):.3f} (n={len(got)})")
    if failed:
        notes.append(f"predict.py failed at origins: {'; '.join(failed)}")
    return [Check("f.range", "FAIL" if narrow else "PASS", narrow),
            Check("f.backtest", "PASS", [], notes)]


def decide(checks: list[Check]) -> str:
    rs = {c.result for c in checks}
    if "FAIL" in rs or "ERROR" in rs:
        return "REJECT"
    return "HOLD" if "INCONCLUSIVE" in rs else "ALLOW"


def check_forecast(answer: str, doc: dict, files_dir: Path, lines: pd.DataFrame, asof: str,
                   extra: list[Check] = (), **runs) -> dict:
    """`extra`: checks the caller ran itself (e.g. a model's meaning check), decided with the rest."""
    checks = run_checks(answer, doc, files_dir, lines, asof, **runs) + list(extra)
    return {"verdict": decide(checks), "checks": [asdict(c) for c in checks],
            "reasons": [f"{c.name}: {r}" for c in checks for r in c.reasons],
            "notes": [f"{c.name}: {n}" for c in checks for n in c.notes]}


__all__ = ["check_forecast", "run_checks", "decide", "numbers_in", "schema_errors", "realise_file"]
