"""Point-in-time (PIT) features, population and labels for the monthly churn task.

Trust boundary: a feature is a SQL definition over the certified `silver.*` tables of ONE
build, evaluated on a read-only connection. Candidates choose features by name; they never
ship feature code that runs outside this module. A feature is PIT-correct when its value
at `as_of` uses only information that existed *before* `as_of`:

  * event rows only if `available_at < as_of` (late-arriving rows, D6, stay invisible),
  * entity attributes only if they are static, or guarded by when they became known,
  * never mutable current-state or post-outcome columns (D7) -- `NAIVE_FEATURES` shows
    exactly those mistakes and exists for negative tests and the benchmark.

Feature SQL contract: the query returns `(customer_id, value)`, at most one row per customer.
It may reference the relation `population` (customer_id of the population at `as_of`) and
the placeholder `{as_of}`, which is rendered as a DATE literal. Trusted features use a
strict `< {as_of}` bound so that a build whose cutoff is the day before `as_of` (end of that
day) sees exactly the same rows -- that is what the time-travel check in
`dsharness.verify.leakage` relies on.

Labels come from the same build, and only when they are mature: a label is usable only if
`as_of + 1 month + 7 days <= build cutoff`. Immature labels are unknown, never "no churn".
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
import re

import duckdb
import pandas as pd

from ..common import open_readonly

AS_OF_PLACEHOLDER = "{as_of}"
LABEL_MATURITY_DAYS = 7
ORIGINS = ("trusted", "proposed")
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class FeatureError(ValueError):
    """A feature definition violated the feature SQL contract (columns, cardinality, origin)."""


@dataclass(frozen=True)
class FeatureDef:
    name: str
    sql: str
    description: str
    origin: str  # 'trusted' | 'proposed'

    def __post_init__(self):
        if not _NAME.match(self.name or ""):
            raise FeatureError(f"feature name {self.name!r} must match {_NAME.pattern}")
        if self.origin not in ORIGINS:
            raise FeatureError(f"feature {self.name}: origin must be one of {ORIGINS}, got {self.origin!r}")
        if not (self.sql or "").strip():
            raise FeatureError(f"feature {self.name}: empty sql")


@contextmanager
def readonly_connection(path: str | Path):
    """`common.open_readonly`, closed on exit.

    DuckDB shares one database instance per file within a process. When another caller in
    this process already holds a locked read-only connection to the same warehouse, setting
    the options again fails even though they are in force; in that case the options are
    verified on the shared instance instead of being assumed."""
    try:
        con = open_readonly(path)
    except duckdb.InvalidInputException as exc:
        if "locked" not in str(exc):
            raise
        con = duckdb.connect(str(path), read_only=True)
        state = con.execute("SELECT current_setting('enable_external_access'), "
                            "current_setting('lock_configuration')").fetchone()
        if tuple(state) != (False, True):
            con.close()
            raise RuntimeError(f"warehouse {path} is open in this process without the read-only "
                               f"lockdown (external_access, lock_configuration) = {state}") from exc
    try:
        yield con
    finally:
        con.close()


# ---------------------------------------------------------------------------- dates
def parse_day(value: str) -> date:
    if not isinstance(value, str) or not _DATE.match(value):
        raise ValueError(f"expected a 'YYYY-MM-DD' date string, got {value!r}")
    return date.fromisoformat(value)


def validate_as_of(as_of: str) -> date:
    d = parse_day(as_of)
    if d.day != 1:
        raise ValueError(f"as_of must be the first day of a month, got {as_of}")
    return d


def add_months(d: date, n: int) -> date:
    """Shift a first-of-month date by n months."""
    if d.day != 1:
        raise ValueError(f"add_months expects a first-of-month date, got {d}")
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def label_mature(as_of: str, cutoff: str) -> bool:
    """A churn label for the month starting at `as_of` is known once the month has ended and
    late cancellations had `LABEL_MATURITY_DAYS` to arrive."""
    due = add_months(validate_as_of(as_of), 1) + timedelta(days=LABEL_MATURITY_DAYS)
    return due <= parse_day(cutoff)


# ---------------------------------------------------------------------------- SQL rendering
def render_sql(sql: str, as_of: str) -> str:
    """Substitute the `{as_of}` placeholder by a DATE literal (as_of is validated, never raw input)."""
    parse_day(as_of)
    body = sql.strip()
    if body.endswith(";"):
        body = body[:-1].rstrip()
    return body.replace(AS_OF_PLACEHOLDER, f"DATE '{as_of}'")


_POPULATION = """
SELECT DISTINCT s.customer_id
FROM silver.subscriptions s
JOIN silver.customers c ON c.customer_id = s.customer_id
WHERE NOT c.is_test_account
  AND s.started_at < {as_of}
  AND (s.ended_at IS NULL OR s.ended_at >= {as_of})
"""


def population_sql(as_of: str) -> str:
    """Non-test customers active at the start of the month `as_of`, as known in the build.

    `ended_at` is deliberately used without an availability guard here: the population is
    "who is a customer at as_of according to this build". A churn shortly before `as_of` that
    is reported after it makes early and late builds differ in membership; the time-travel
    check reports that separately and only compares feature values on the intersection."""
    validate_as_of(as_of)
    return render_sql(_POPULATION, as_of)


# ---------------------------------------------------------------------------- feature library
def _m(n: int) -> str:
    """'YYYY-MM' of the month n months before as_of (SQL expression)."""
    return f"strftime({{as_of}} - INTERVAL {n} MONTH, '%Y-%m')"


def _ticket_count(days: int, category: str | None) -> str:
    cat = f" AND t.category = '{category}'" if category else ""
    return f"""
SELECT p.customer_id, COUNT(t.ticket_id) AS value
FROM population p
LEFT JOIN silver.tickets t
  ON t.customer_id = p.customer_id
 AND t.created_at >= {{as_of}} - INTERVAL {days} DAY AND t.created_at < {{as_of}}
 AND t.available_at < {{as_of}}{cat}
GROUP BY p.customer_id"""


def _invoice_count(days: int, invoice_type: str) -> str:
    return f"""
SELECT p.customer_id, COUNT(i.invoice_id) AS value
FROM population p
LEFT JOIN silver.invoices i
  ON i.customer_id = p.customer_id AND i.invoice_type = '{invoice_type}'
 AND i.issued_at >= {{as_of}} - INTERVAL {days} DAY AND i.issued_at < {{as_of}}
 AND i.available_at < {{as_of}}
GROUP BY p.customer_id"""


def _usage_last_month(column: str, guarded: bool = True) -> str:
    guard = " AND available_at < {as_of}" if guarded else ""
    return f"""
SELECT customer_id, CAST(SUM({column}) AS DOUBLE) AS value
FROM silver.usage_monthly
WHERE usage_month = {_m(1)}{guard}
GROUP BY customer_id"""


def _f(name: str, sql: str, description: str, origin: str = "trusted") -> FeatureDef:
    return FeatureDef(name, sql.strip(), description, origin)


TRUSTED_FEATURES: dict[str, FeatureDef] = {f.name: f for f in [
    _f("tenure_months", """
SELECT customer_id, date_diff('day', created_at, {as_of}) / 30.4375 AS value
FROM silver.customers""", "Months since signup (customers.created_at is static)."),
    _f("new_customer_3m", """
SELECT customer_id, CAST(created_at >= {as_of} - INTERVAL 3 MONTH AS INTEGER) AS value
FROM silver.customers""", "1 if the customer signed up within the 3 months before as_of."),
    _f("plan", """
SELECT customer_id, arg_max(plan, started_at) AS value
FROM silver.subscriptions
WHERE started_at < {as_of}
GROUP BY customer_id""", "Plan of the latest subscription started before as_of (categorical)."),
    _f("region", "SELECT customer_id, region AS value FROM silver.customers", "Customer region (categorical, static)."),
    _f("channel", "SELECT customer_id, channel AS value FROM silver.customers",
       "Acquisition channel (categorical, static)."),
    _f("last_charge_fen", """
SELECT customer_id, CAST(amount_fen AS DOUBLE) AS value
FROM silver.invoices
WHERE invoice_type = 'charge' AND issued_at < {as_of} AND available_at < {as_of}
QUALIFY ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY issued_at DESC, invoice_id DESC) = 1""",
       "Amount of the last charge issued and available before as_of: the price actually paid "
       "(price history), not the mutable subscriptions.monthly_price_fen."),
    _f("minutes_last_month", _usage_last_month("minutes"),
       "Usage minutes in the month before as_of; NULL if that row had not arrived by as_of (D6)."),
    _f("minutes_3m_avg", """
SELECT customer_id, AVG(minutes) AS value
FROM silver.usage_monthly
WHERE usage_month IN (""" + ", ".join(_m(k) for k in (1, 2, 3)) + """) AND available_at < {as_of}
GROUP BY customer_id""", "Mean monthly minutes over the 3 months before as_of (rows available by as_of)."),
    _f("pricing_views_3m", """
SELECT customer_id, SUM(pricing_page_views) AS value
FROM silver.usage_monthly
WHERE usage_month IN (""" + ", ".join(_m(k) for k in (1, 2, 3)) + """) AND available_at < {as_of}
GROUP BY customer_id""", "Pricing-page views over the 3 months before as_of; NULL where not yet tracked (before 2026-01)."),
    _f("minutes_trend", """
SELECT customer_id,
       ln(1 + SUM(minutes) FILTER (WHERE usage_month = """ + _m(1) + """))
     - ln(1 + AVG(minutes) FILTER (WHERE usage_month IN (""" + _m(2) + ", " + _m(3) + """))) AS value
FROM silver.usage_monthly
WHERE usage_month IN (""" + ", ".join(_m(k) for k in (1, 2, 3)) + """) AND available_at < {as_of}
GROUP BY customer_id""", "log(1+minutes last month) - log(1+mean minutes of the two months before)."),
    _f("sessions_last_month", _usage_last_month("sessions"),
       "Sessions in the month before as_of; NULL if not yet arrived by as_of."),
    _f("tickets_90d", _ticket_count(90, None), "Support tickets created and available in the 90 days before as_of."),
    _f("billing_tickets_90d", _ticket_count(90, "billing"), "Billing tickets in the 90 days before as_of."),
    _f("cancel_tickets_90d", _ticket_count(90, "cancel"), "Cancellation-intent tickets in the 90 days before as_of."),
    _f("avg_csat_180d", """
SELECT customer_id, AVG(csat) AS value
FROM silver.tickets
WHERE created_at >= {as_of} - INTERVAL 180 DAY AND created_at < {as_of} AND available_at < {as_of}
GROUP BY customer_id""", "Mean CSAT of rated tickets in the 180 days before as_of (NULL if none)."),
    _f("refunds_180d", _invoice_count(180, "refund"), "Refunds issued and available in the 180 days before as_of."),
    _f("addons_90d", _invoice_count(90, "addon"), "Add-on purchases in the 90 days before as_of."),
]}

# Leaky on purpose. Not trusted; used by negative tests and the benchmark's naive agents.
NAIVE_FEATURES: dict[str, FeatureDef] = {f.name: f for f in [
    _f("minutes_last_month_naive", _usage_last_month("minutes", guarded=False),
       "LEAKY: last-month minutes ignoring available_at (late rows leak in later builds).", "proposed"),
    _f("current_price_fen", """
SELECT customer_id, CAST(monthly_price_fen AS DOUBLE) AS value FROM silver.subscriptions""",
       "LEAKY: subscriptions.monthly_price_fen is the CURRENT price (mutable_current), not the price at as_of.",
       "proposed"),
    _f("has_cancel_reason", """
SELECT customer_id, CAST(cancel_reason IS NOT NULL AS INTEGER) AS value FROM silver.subscriptions""",
       "LEAKY: cancel_reason only exists after the outcome (post_outcome).", "proposed"),
    _f("known_end_flag", """
SELECT customer_id, CAST(ended_at IS NOT NULL AS INTEGER) AS value FROM silver.subscriptions""",
       "LEAKY: ended_at without an availability guard (updated_at) reveals future churn.", "proposed"),
]}


def resolve_features(names, extra: dict[str, FeatureDef] | None = None) -> dict[str, FeatureDef]:
    """Name -> definition. Trusted names win; `extra` may add names but never redefine a trusted one."""
    extra = dict(extra or {})
    for key, f in extra.items():
        if not isinstance(f, FeatureDef):
            raise FeatureError(f"extra feature {key!r} is not a FeatureDef")
        if key != f.name:
            raise FeatureError(f"extra feature key {key!r} does not match its name {f.name!r}")
        trusted = TRUSTED_FEATURES.get(key)
        if trusted is not None and trusted != f:
            raise FeatureError(f"extra feature {key!r} would redefine a trusted feature")
    names = list(names)
    if len(set(names)) != len(names):
        raise FeatureError(f"duplicate feature names in {names}")
    out = {}
    for n in names:
        if n in TRUSTED_FEATURES:
            out[n] = TRUSTED_FEATURES[n]
        elif n in extra:
            out[n] = extra[n]
        else:
            raise KeyError(f"unknown feature {n!r}: not trusted and not supplied in extra")
    return out


# ---------------------------------------------------------------------------- evaluation
def _with_population(as_of: str) -> str:
    return f"WITH population AS ({population_sql(as_of)})\n"


def population(con: duckdb.DuckDBPyConnection, as_of: str) -> pd.Series:
    ids = con.execute(f"SELECT customer_id FROM ({population_sql(as_of)}) ORDER BY customer_id").df()
    return ids["customer_id"]


def same_ids(a: pd.Series, b: pd.Series) -> bool:
    """Same customer ids in the same order (dtype-agnostic: pandas may infer str or object)."""
    return len(a) == len(b) and all(x == y for x, y in zip(a.tolist(), b.tolist()))


def compute_feature(con: duckdb.DuckDBPyConnection, as_of: str, feature: FeatureDef) -> pd.DataFrame:
    """Evaluate one feature for the population at `as_of`: DataFrame(customer_id, value), one row
    per population member, sorted by customer_id. Raises FeatureError on contract violations."""
    validate_as_of(as_of)
    body = render_sql(feature.sql, as_of)
    head = _with_population(as_of)
    cols = [d[0] for d in con.execute(f"{head}SELECT * FROM ({body}) AS f LIMIT 0").description]
    if cols != ["customer_id", "value"]:
        raise FeatureError(f"feature {feature.name}: must return columns (customer_id, value), got {cols}")
    df = con.execute(
        f"{head}SELECT p.customer_id, f.value FROM population p "
        f"LEFT JOIN ({body}) AS f ON f.customer_id = p.customer_id ORDER BY p.customer_id").df()
    dup = df["customer_id"][df["customer_id"].duplicated()]
    if len(dup):
        raise FeatureError(f"feature {feature.name}: more than one row per customer "
                           f"(join would add rows), e.g. {sorted(set(dup))[:5]}")
    return df


def build_features(con: duckdb.DuckDBPyConnection, as_of: str, names: list[str],
                   extra: dict[str, FeatureDef] | None = None) -> pd.DataFrame:
    """customer_id + one column per feature, one row per population member at `as_of`."""
    defs = resolve_features(names, extra)
    ids = population(con, as_of)
    if ids.empty:
        raise ValueError(f"empty population at {as_of}: is this build's cutoff before as_of?")
    out = pd.DataFrame({"customer_id": ids.values})
    for name, f in defs.items():
        col = compute_feature(con, as_of, f)
        if not same_ids(col["customer_id"], out["customer_id"]):
            raise FeatureError(f"feature {name}: population rows changed during evaluation")
        out[name] = col["value"].values
    return out


def _build_horizon(con: duckdb.DuckDBPyConnection) -> datetime | None:
    return con.execute("""
        SELECT max(t) FROM (
            SELECT max(available_at) AS t FROM silver.invoices
            UNION ALL SELECT max(available_at) FROM silver.usage_monthly
            UNION ALL SELECT max(available_at) FROM silver.tickets
            UNION ALL SELECT max(updated_at) FROM silver.subscriptions)""").fetchone()[0]


def build_labels(con: duckdb.DuckDBPyConnection, as_of: str, cutoff: str) -> pd.DataFrame:
    """customer_id, churned_next_month (nullable Int64; NA when immature), label_mature (bool).

    `cutoff` must be the build's export cutoff. A cutoff later than the build would make
    unknown churns look like "no churn"; one earlier than the build's data is detected here."""
    mature = label_mature(as_of, cutoff)
    horizon = _build_horizon(con)
    end_of_cutoff = datetime.combine(parse_day(cutoff), time(23, 59, 59))
    if horizon is not None and horizon > end_of_cutoff:
        raise ValueError(f"build contains rows available at {horizon}, after the stated cutoff {cutoff}: "
                         f"wrong cutoff for this build")
    nxt = add_months(validate_as_of(as_of), 1).isoformat()
    df = con.execute(f"""
        {_with_population(as_of)}
        SELECT p.customer_id,
               COALESCE(bool_or(s.ended_at >= DATE '{as_of}' AND s.ended_at < DATE '{nxt}'), FALSE) AS churned
        FROM population p
        LEFT JOIN silver.subscriptions s
          ON s.customer_id = p.customer_id AND s.started_at < DATE '{as_of}'
        GROUP BY p.customer_id
        ORDER BY p.customer_id""").df()
    labels = df["churned"].astype(int).astype("Int64")
    if not mature:
        labels[:] = pd.NA
    return pd.DataFrame({"customer_id": df["customer_id"].values,
                         "churned_next_month": labels.values,
                         "label_mature": mature})


def build_training_frame(con: duckdb.DuckDBPyConnection, as_of_list, names, cutoff: str,
                         extra: dict[str, FeatureDef] | None = None) -> pd.DataFrame:
    """Stacked (as_of, customer_id, features..., churned_next_month) with immature labels dropped.

    The as_of values that were dropped as immature are listed in `frame.attrs["immature_as_of"]`
    so callers can refuse instead of silently training on less data."""
    parts, immature = [], []
    for as_of in as_of_list:
        X = build_features(con, as_of, list(names), extra)
        y = build_labels(con, as_of, cutoff)
        if not same_ids(X["customer_id"], y["customer_id"]):
            raise ValueError(f"features and labels disagree on the population at {as_of}")
        if not label_mature(as_of, cutoff):
            immature.append(as_of)
            continue
        part = X.copy()
        part.insert(0, "as_of", as_of)
        part["churned_next_month"] = y["churned_next_month"].astype("int64").values
        parts.append(part)
    cols = ["as_of", "customer_id", *names, "churned_next_month"]
    frame = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=cols)
    frame.attrs["immature_as_of"] = immature
    return frame
