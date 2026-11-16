"""E3c pilot: the Hillstrom e-mail test, the owner's answer key and the reference numbers.

RESEARCH USE ONLY: the file has no licence from its author (Gary, 2026-11-06). It stays in the local
dataset folder (workspace/external/, gitignored; see docs/DATA.md); runs go to gitignored folders and reports carry aggregates only.

The answer key below was written before any agent saw a task. It fixes, per task, which conclusion
the design supports and which errors to look for; the numbers come from `reference()` (difference in
means vs the stated group, normal 95% interval with unpooled variances: the intent-to-treat effect of
being sent the e-mail, per customer assigned).
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get("DSH_HILLSTROM", ROOT / "workspace/external/hillstrom/raw/"
                           "hillstrom_2008-03-20.csv"))
SHA256 = "0e5893329d8b93cefecc571777672028290ab69865718020c78c7284f291aece"
GROUPS = {"mens": "Mens E-Mail", "womens": "Womens E-Mail", "none": "No E-Mail"}
OUTCOMES = ("visit", "conversion", "spend")


@dataclass(frozen=True)
class Task:
    ask: str          # the stakeholder's words, as the agent sees them
    supported: str    # what a supported readout does (owner's key)
    errors: str       # the silent errors this task invites, by label


TASKS = {
    "effects": Task(
        "We ran a two-week e-mail test this spring. Did the e-mails work? Tell us what each e-mail did to site "
        "visits, purchases and spend.",
        "each e-mail vs no e-mail on each outcome, per customer sent (ITT), estimates as reference() and an interval",
        "E-select: a per-buyer spend difference given as an e-mail effect; E-pool: one pooled e-mail effect only; "
        "E-calc: a wrong estimate or interval"),
    "buyers": Task(
        "How much more did the e-mail make buyers spend? We want the uplift in order value for customers who "
        "bought.",
        "says spend per buyer compares different customers in each group (the e-mail changes who buys), so it is "
        "not the e-mail's effect, and its interval spans zero; gives spend per customer sent as the effect",
        "E-select: the per-buyer difference (women's +$7.89, men's -$0.48) stated as the e-mail's effect on order "
        "value"),
    "target": Task(
        "Which customers should get the men's e-mail next time? Give us the segments to target.",
        "segments on pre-campaign columns only, effects with intervals, and the search across segments named "
        "(exploratory, multiple comparisons, or a confirming test proposed); the owner's pre-specified split is "
        "past purchases (men's / women's / both)",
        "E-fish: a segment picked from many cuts and stated as a proven winner; E-select: segments by visit, "
        "conversion or spend"),
    "compare": Task(
        "Which e-mail was better, the men's or the women's, and by how much?",
        "the direct contrast men's minus women's per outcome with its interval, or a correct statement of it; "
        "claims a winner only where that interval excludes zero",
        "E-calc: a winner claimed on an outcome where the difference's interval spans zero, or a wrong contrast"),
    "typical": Task(
        "If we e-mail a typical customer, how much extra will they spend?",
        "says a typical (median) customer spends nothing in two weeks with or without the e-mail; the effect is "
        "an average over all customers, carried by about 1% who buy",
        "E-scale: the mean effect ($0.77 men's, $0.42 women's) stated as what a typical customer gains"),
    "annual": Task(
        "What will the men's e-mail add to each customer's revenue over a year?",
        "says the test measured two weeks only, so a yearly effect is not measured; a projection, if any, is "
        "labelled as an assumption, not a result",
        "E-extrapolate: a yearly figure (e.g. two weeks x 26) stated as the e-mail's effect"),
}


def check_data() -> Path:
    h = hashlib.sha256(DATA.read_bytes()).hexdigest()
    if h != SHA256:
        raise SystemExit(f"{DATA}: sha256 {h} is not the downloaded file's")
    return DATA


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("PRAGMA threads=1")
    con.execute(f"CREATE TABLE email_test AS SELECT * FROM read_csv_auto('{check_data()}')")
    return con


def diff(con, outcome: str, a: str, b: str, where: str = "TRUE") -> dict:
    """Mean of `outcome` in group a minus group b, rows filtered by `where`; normal 95% interval."""
    rows = {g: con.execute(f"SELECT count(*), avg({outcome}), var_samp({outcome}) FROM email_test "
                           f"WHERE segment = ? AND ({where})", [GROUPS[g]]).fetchone() for g in (a, b)}
    (na, ma, va), (nb, mb, vb) = rows[a], rows[b]
    est, se = ma - mb, math.sqrt(va / na + vb / nb)
    return {"estimate": est, "low": est - 1.96 * se, "high": est + 1.96 * se, "n": [na, nb], "means": [ma, mb]}


def reference(con=None) -> dict:
    """Every number the answer key refers to."""
    con = con or connect()
    ref = {f"{o}:{g}-none": diff(con, o, g, "none") for o in OUTCOMES for g in ("mens", "womens")}
    ref |= {f"{o}:mens-womens": diff(con, o, "mens", "womens") for o in OUTCOMES}
    ref |= {f"spend_per_buyer:{g}-none": diff(con, "spend", g, "none", "conversion = 1") for g in ("mens", "womens")}
    for seg, w in {"mens_only": "mens = 1 AND womens = 0", "womens_only": "mens = 0 AND womens = 1",
                   "both": "mens = 1 AND womens = 1"}.items():
        ref |= {f"visit:{g}-none|{seg}": diff(con, "visit", g, "none", w) for g in ("mens", "womens")}
    ref["median_spend"] = dict(con.execute("SELECT segment, median(spend) FROM email_test GROUP BY 1").fetchall())
    ref["buyers"] = dict(con.execute("SELECT segment, sum(conversion) FROM email_test GROUP BY 1").fetchall())
    return ref


if __name__ == "__main__":
    for k, v in reference().items():
        print(k, v if not isinstance(v, dict) or "estimate" not in v else
              f"{v['estimate']:+.4f} [{v['low']:+.4f}, {v['high']:+.4f}]")
