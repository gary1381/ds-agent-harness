"""Source data contracts (Pandera). Structural violations block; row anomalies are counted.

A contract states what the *source system* promises. It cannot prove that the source
recorded reality correctly -- that needs reconciliation against an independent record
(see the finance-close reconciliation test in the pipeline).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
import pandera.pandas as pa
from pandera.errors import SchemaErrors

REGIONS = ["north", "south", "east", "west"]
_ts = pa.Column(pa.DateTime, coerce=True)
_ts_null = pa.Column(pa.DateTime, coerce=True, nullable=True)

_invoice_common = {
    "invoice_id": pa.Column(str, nullable=False),
    "customer_id": pa.Column(str, nullable=False),
    "invoice_type": pa.Column(str, pa.Check.isin(["charge", "addon", "refund"])),
    "issued_at": _ts,
    "available_at": _ts,
    "original_invoice_id": pa.Column(str, nullable=True, coerce=True),
}

# table -> {schema_version: DataFrameSchema}
SOURCE_CONTRACTS: dict[str, dict[int, pa.DataFrameSchema]] = {
    "crm_customers": {1: pa.DataFrameSchema({
        "customer_id": pa.Column(str, unique=True),
        "created_at": _ts,
        "region": pa.Column(str, pa.Check.isin(REGIONS)),
        "channel": pa.Column(str),
        "email": pa.Column(str, nullable=True),
        "phone": pa.Column(str, nullable=True, coerce=True),
        "birth_year": pa.Column(int, nullable=True, coerce=True),
        "updated_at": _ts,
    }, strict=True)},
    "billing_subscriptions": {1: pa.DataFrameSchema({
        "subscription_id": pa.Column(str, unique=True),
        "customer_id": pa.Column(str),
        "plan": pa.Column(str, pa.Check.isin(["basic", "plus", "pro"])),
        "monthly_price_cents": pa.Column(int, pa.Check.gt(0), coerce=True),
        "started_at": _ts,
        "ended_at": _ts_null,
        "cancel_reason": pa.Column(str, nullable=True, coerce=True),
        "updated_at": _ts,
    }, strict=True)},
    "billing_invoices": {
        1: pa.DataFrameSchema({**_invoice_common, "amount_cents": pa.Column(int, coerce=True)}, strict=True),
        2: pa.DataFrameSchema({**_invoice_common, "amount_minor": pa.Column(int, coerce=True),
                               "currency": pa.Column(str, pa.Check.isin(["CNY"]))}, strict=True),
    },
    "app_usage_monthly": {1: pa.DataFrameSchema({
        "customer_id": pa.Column(str),
        "usage_month": pa.Column(str, pa.Check.str_matches(r"^\d{4}-\d{2}$")),
        "minutes": pa.Column(int, pa.Check.ge(0), coerce=True),
        "sessions": pa.Column(int, pa.Check.ge(0), coerce=True),
        "pricing_page_views": pa.Column(float, pa.Check.ge(0), nullable=True, coerce=True),
        "available_at": _ts,
    }, strict=True)},
    "support_tickets": {1: pa.DataFrameSchema({
        "ticket_id": pa.Column(str, unique=True),
        "customer_id": pa.Column(str),
        "created_at": _ts,
        "category": pa.Column(str, pa.Check.isin(["billing", "bug", "howto", "cancel"])),
        "csat": pa.Column(float, pa.Check.in_range(1, 5), nullable=True, coerce=True),
        "available_at": _ts,
    }, strict=True)},
    "crm_retention_contacts": {1: pa.DataFrameSchema({
        "contact_id": pa.Column(str, unique=True),
        "customer_id": pa.Column(str),
        "campaign_month": pa.Column(str, pa.Check.str_matches(r"^\d{4}-\d{2}$")),
        "arm": pa.Column(str, pa.Check.isin(["treatment", "control"])),
        "exposed": pa.Column(bool, coerce=True),
        "channel": pa.Column(str),
        "offer": pa.Column(str),
        "assigned_at": _ts,
        "available_at": _ts,
    }, strict=True)},
    "finance_closed_revenue": {1: pa.DataFrameSchema({
        "month": pa.Column(str, pa.Check.str_matches(r"^\d{4}-\d{2}$")),
        "region": pa.Column(str, pa.Check.isin(REGIONS)),
        "net_revenue_fen": pa.Column(int, coerce=True),
    }, strict=True)},
}

# Row-level anomaly probes: counted and reported, never silently dropped. The governed
# pipeline decides (in reviewed SQL) what to do with each class.
ANOMALY_PROBES = {
    "billing_invoices": {
        "duplicate_invoice_id_rows": lambda df: int(df.duplicated("invoice_id", keep="first").sum()),
        "charge_amount_below_cheapest_plan": lambda df: int(
            ((df["invoice_type"] == "charge") & (df[_amount_col(df)] < 1000)).sum()),
    },
    "app_usage_monthly": {
        "rows_available_more_than_10d_after_month_end": lambda df: int(
            (pd.to_datetime(df["available_at"])
             - pd.PeriodIndex(df["usage_month"], freq="M").end_time.normalize()).dt.days.gt(10).sum()),
    },
    "crm_customers": {
        "internal_test_accounts": lambda df: int(df["email"].fillna("").str.endswith("@qa.internal").sum()),
    },
}


def _amount_col(df: pd.DataFrame) -> str:
    return "amount_cents" if "amount_cents" in df.columns else "amount_minor"


@dataclass
class ContractResult:
    table: str
    schema_version: int | None
    ok: bool
    n_rows: int
    errors: list[dict] = field(default_factory=list)
    anomalies: dict[str, int] = field(default_factory=dict)


def detect_version(table: str, columns: list[str]) -> int | None:
    for version, schema in SOURCE_CONTRACTS.get(table, {}).items():
        if set(schema.columns) == set(columns):
            return version
    return None


def validate_source(table: str, df: pd.DataFrame) -> tuple[ContractResult, pd.DataFrame | None]:
    """Validate one source table. Returns the coerced frame when the contract holds."""
    version = detect_version(table, list(df.columns))
    if version is None:
        known = {v: sorted(s.columns) for v, s in SOURCE_CONTRACTS.get(table, {}).items()}
        return ContractResult(table, None, False, len(df), [{
            "check": "schema_version", "error": "columns match no known contract version",
            "observed_columns": sorted(df.columns), "known_versions": known}]), None
    try:
        coerced = SOURCE_CONTRACTS[table][version].validate(df, lazy=True)
    except SchemaErrors as exc:
        cases = exc.failure_cases.head(20)
        errors = [{"check": str(r.get("check")), "column": str(r.get("column")),
                   "failure_case": str(r.get("failure_case"))} for r in cases.to_dict("records")]
        return ContractResult(table, version, False, len(df), errors), None
    anomalies = {name: fn(coerced) for name, fn in ANOMALY_PROBES.get(table, {}).items()}
    return ContractResult(table, version, True, len(df), [], anomalies), coerced
