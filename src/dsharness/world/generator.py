"""Synthetic subscription company with planted ground truth and planted data defects.

Why this exists: the harness has to be *measured*, not just described. In this world
every number an agent can be asked about has a known true value (kept in custody,
never shown to agents), and the source-system exports contain the defects that turn
careless pipelines into silent errors:

  D1  at-least-once export: exact duplicate invoice rows (same invoice_id)      -> must dedup by id
  D2  legitimate look-alikes: two add-ons, same customer/day/amount, diff ids   -> must NOT dedup
  D3  unit incident: south region charges in 2026-03 exported in yuan, not fen  -> contract + reconcile
  D4  refunds are separate negative rows, booked in the refund month            -> net revenue semantics
  D5  internal QA accounts (@qa.internal) generate real-looking activity       -> exclude from metrics
  D6  late-arriving usage rows (available_at weeks after the usage month)       -> point-in-time features
  D7  mutable current-state columns (price, ended_at, cancel_reason)            -> time-travel leakage
  D8  source schema change from 2026-08 (amount_cents -> amount_minor+currency) -> pipeline break-fix
  D9  population shift from 2026-08 (new livestream channel, plus price rise)   -> drift monitoring

Business levers (v2): from 2026-03 to 2026-06 the company ran a *randomised* retention program
(app message + 10% discount; 20% of at-risk customers enrolled each month, 50/50 treatment vs
control, 85% of treated actually exposed). It is exported as crm_retention_contacts, so the
heterogeneous effect can be learned from data rather than only from custody truth. Price
sensitivity is latent but leaves observable traces (billing/cancel ticket share, fewer add-ons) and,
from 2026-01 when product analytics started tracking it, pricing-page views in app_usage_monthly
(NULL before tracking began). Whether uplift is learnable therefore depends on a data-collection
decision -- which is the point: the harness should be able to show both outcomes.

A July-2026 retention-contact experiment has a *heterogeneous* true effect: price-sensitive
but still-engaged customers respond, disengaged "lost causes" do not, and some highly
engaged low-sensitivity customers react badly ("sleeping dogs"). Ranking by churn risk is
therefore not the same as ranking by value -- the gap the business loop must measure.

Synthetic results demonstrate mechanisms. They are not evidence of real-world accuracy
or business value.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

FEN_PER_YUAN = 100
REGIONS = ("north", "south", "east", "west")
REGION_P = (0.30, 0.25, 0.30, 0.15)
REGION_LOGIT = {"north": 0.0, "south": 0.15, "east": -0.10, "west": 0.05}
PLANS = ("basic", "plus", "pro")
PLAN_P = (0.45, 0.40, 0.15)
BASE_CHANNELS = ("organic", "paid_search", "referral")
BASE_CHANNEL_P = (0.40, 0.35, 0.25)


@dataclass(frozen=True)
class WorldConfig:
    seed: int = 20261019
    n_initial: int = 3000
    monthly_signups: int = 220
    first_month: str = "2025-01"
    last_month: str = "2026-09"
    plan_price_fen: dict = field(default_factory=lambda: {"basic": 2900, "plus": 5900, "pro": 9900})
    addon_price_fen: int = 990
    n_test_accounts: int = 25
    dup_resend_rate: float = 0.01           # D1
    addon_rate: float = 0.06                # D2 (a third of add-on buyers buy two identical packs)
    unit_incident: tuple = ("south", "2026-03")   # D3
    refund_rate: float = 0.03               # D4
    late_usage_rate: float = 0.06           # D6
    schema_change_from: str = "2026-08-01"  # D8: exports on/after this date use billing schema v2
    shift_month: str = "2026-08"            # D9
    plus_new_price_fen: int = 6900          # D9
    experiment_month: str = "2026-07"
    finance_close_lag_days: int = 5
    program_months: tuple = ("2026-03", "2026-04", "2026-05", "2026-06")
    program_share: float = 0.20             # of at-risk customers enrolled per month
    program_exposure_rate: float = 0.85     # treated customers who actually saw the message
    pricing_tracking_from: str | None = "2026-01"   # None = never instrumented (uplift not learnable)


@dataclass
class World:
    cfg: WorldConfig
    months: pd.PeriodIndex
    customers: pd.DataFrame
    subscriptions: pd.DataFrame
    invoices: pd.DataFrame
    usage: pd.DataFrame
    tickets: pd.DataFrame
    truth_customer_month: pd.DataFrame
    truth_revenue: pd.DataFrame
    truth_experiment: pd.DataFrame
    defects: dict
    contacts: pd.DataFrame
    truth_program: pd.DataFrame


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def _tau(s, e):
    """True logit effect of a retention contact: persuadables respond, sleeping dogs react badly."""
    tau = np.where((s > 0.45) & (e > -0.8), -1.3, 0.0)
    return np.where((s < 0.20) & (e > 0.6), 0.4, tau)


def _month_start(p: pd.Period) -> pd.Timestamp:
    return p.start_time.normalize()


def _month_end(p: pd.Period) -> pd.Timestamp:
    return p.end_time.normalize()


def _rand_day(rng, p: pd.Period, n: int) -> np.ndarray:
    days = rng.integers(0, p.days_in_month, size=n)
    return (_month_start(p) + pd.to_timedelta(days, unit="D")).values


def simulate(cfg: WorldConfig | None = None) -> World:
    cfg = cfg or WorldConfig()
    rng = np.random.default_rng(cfg.seed)
    months = pd.period_range(cfg.first_month, cfg.last_month, freq="M")
    T = len(months)
    t_shift = months.get_loc(pd.Period(cfg.shift_month, "M"))
    t_exp = months.get_loc(pd.Period(cfg.experiment_month, "M"))

    # ---------------------------------------------------------------- customers
    signups = rng.poisson(cfg.monthly_signups, size=T)
    n_new = int(signups.sum())
    N = cfg.n_initial + n_new
    signup_t = np.concatenate([np.full(cfg.n_initial, -1), np.repeat(np.arange(T), signups)])

    signup_date = np.empty(N, dtype="datetime64[ns]")
    span = (pd.Timestamp("2024-12-31") - pd.Timestamp("2023-01-01")).days
    signup_date[: cfg.n_initial] = (pd.Timestamp("2023-01-01")
                                    + pd.to_timedelta(rng.integers(0, span, cfg.n_initial), unit="D")).values
    pos = cfg.n_initial
    for t, k in enumerate(signups):
        signup_date[pos:pos + k] = _rand_day(rng, months[t], int(k))
        pos += k

    region = rng.choice(REGIONS, size=N, p=REGION_P)
    plan = rng.choice(PLANS, size=N, p=PLAN_P)
    channel = rng.choice(BASE_CHANNELS, size=N, p=BASE_CHANNEL_P).astype(object)
    late_joiners = signup_t >= t_shift
    live = late_joiners & (rng.random(N) < 0.30)
    channel[live] = "livestream"
    s = rng.beta(2, 3, size=N)                      # latent price sensitivity
    s[live] = rng.beta(3, 2, size=int(live.sum()))
    e0 = rng.normal(0.3, 0.8, size=N)               # latent baseline engagement
    e0[channel == "referral"] += 0.2
    e0[live] = rng.normal(-0.3, 0.8, size=int(live.sum()))

    is_test = np.zeros(N, dtype=bool)
    is_test[rng.choice(cfg.n_initial, size=cfg.n_test_accounts, replace=False)] = True
    cust_ids = np.array([f"C{i:06d}" for i in range(1, N + 1)])
    emails = np.where(is_test, [f"qa{i}@qa.internal" for i in range(N)],
                      [f"user{i}@mail.example" for i in range(N)])
    phones = np.array([f"1{rng.integers(30, 99)}{rng.integers(10_000_000, 99_999_999)}" for _ in range(N)])
    birth_year = rng.integers(1955, 2006, size=N)

    # ---------------------------------------------------------------- monthly dynamics
    e = e0.copy()
    region_logit = np.array([REGION_LOGIT[r] for r in region])
    signup_idx = pd.DatetimeIndex(signup_date)
    alive = np.zeros(N, dtype=bool)
    alive[: cfg.n_initial] = True
    churn_date = np.full(N, np.datetime64("NaT", "ns"), dtype="datetime64[ns]")
    churn_t = np.full(N, -1)
    price_change_date = _month_start(months[t_shift])

    cm_rows, usage_rows, ticket_rows, inv_rows, prog_rows = [], [], [], [], []
    exp_rows = None
    rng_prog = np.random.default_rng(cfg.seed + 7)   # program randomisation has its own stream
    rng_sig = np.random.default_rng(cfg.seed + 11)   # instrumented behavioural signal, own stream
    t_track = (months.get_loc(pd.Period(cfg.pricing_tracking_from, "M"))
               if cfg.pricing_tracking_from else T)
    program_t = {months.get_loc(pd.Period(x, "M")) for x in cfg.program_months}
    inv_seq = 0
    tick_seq = 0

    def price_at(t):
        p = np.array([cfg.plan_price_fen[x] for x in plan])
        if t >= t_shift:
            p = np.where(plan == "plus", cfg.plus_new_price_fen, p)
        return p

    for t in range(T):
        m = months[t]
        joined = signup_t == t
        alive |= joined
        at_risk = alive & (signup_t < t) & ~is_test
        present = alive.copy()

        e = np.where(present, 0.75 * e + 0.25 * e0 + rng.normal(0, 0.35, N), e)
        price = price_at(t)

        # usage (new joiners only for the remainder of the month)
        frac = np.ones(N)
        if joined.any():
            jd = pd.DatetimeIndex(signup_date[joined])
            frac[joined] = 1.0 - (jd.day.values - 1) / m.days_in_month
        minutes = np.round(np.exp(4.2 + 0.6 * e + rng.normal(0, 0.3, N)) * frac).astype(int)
        sessions = rng.poisson(np.maximum(1.0, minutes / 12.0))
        tickets = rng.poisson(0.08 + 0.25 * _sigmoid(-2.0 * e) + 0.10 * s)

        tenure = np.where(signup_t < 0,
                          (_month_start(m) - signup_idx).days.values / 30.4,
                          t - signup_t)
        logit = (-3.4 - 0.9 * e + 1.6 * s * (price / cfg.plan_price_fen["plus"])
                 + 0.35 * np.minimum(tickets, 3) + 0.7 * (tenure < 3)
                 + region_logit)
        if t >= t_shift:  # the price rise bites price-sensitive plus customers
            logit = logit + np.where(plan == "plus", 0.8 * s, 0.0)
        tau = _tau(s, e)
        treated_exposed = np.zeros(N, dtype=bool)
        if t in program_t:
            elig = np.where(at_risk)[0]
            enrolled = elig[rng_prog.random(len(elig)) < cfg.program_share]
            is_treat = rng_prog.random(len(enrolled)) < 0.5
            exposed = is_treat & (rng_prog.random(len(enrolled)) < cfg.program_exposure_rate)
            treated_exposed[enrolled[exposed]] = True
            prog_rows.append(pd.DataFrame({
                "customer_id": cust_ids[enrolled], "campaign_month": str(m),
                "arm": np.where(is_treat, "treatment", "control"), "exposed": exposed,
                "assigned_at": _month_start(m) + pd.Timedelta(hours=9),
                "available_at": _month_start(m) + pd.Timedelta(days=1, hours=9),
                "_p0": _sigmoid(logit[enrolled]), "_p1": _sigmoid(logit[enrolled] + tau[enrolled]),
                "_tau_logit": tau[enrolled]}))
        hazard = _sigmoid(logit + np.where(treated_exposed, tau, 0.0))
        u = rng.random(N)
        churned = at_risk & (u < hazard)
        if prog_rows and prog_rows[-1]["campaign_month"].iat[0] == str(m):
            ids = np.searchsorted(cust_ids, prog_rows[-1]["customer_id"].values)
            prog_rows[-1]["_churned"] = churned[ids]

        if t == t_exp:
            p1 = _sigmoid(logit + tau)
            idx = np.where(at_risk)[0]
            exp_rows = pd.DataFrame({
                "customer_id": cust_ids[idx], "p0": hazard[idx], "p1": p1[idx], "u": u[idx],
                "tau_logit": tau[idx], "price_fen": price[idx], "segment_truth": np.select(
                    [tau[idx] < 0, tau[idx] > 0], ["persuadable", "sleeping_dog"], "unmoved")})

        cdays = rng.integers(1, m.days_in_month, size=N)
        churn_date = np.where(churned, (_month_start(m) + pd.to_timedelta(cdays, unit="D")).values, churn_date)
        churn_t = np.where(churned, t, churn_t)

        idx = np.where(present)[0]
        cm_rows.append(pd.DataFrame({
            "customer_id": cust_ids[idx], "month": str(m), "active_at_start": (signup_t[idx] < t),
            "at_risk": at_risk[idx], "churned": churned[idx], "engagement": e[idx], "price_sensitivity": s[idx],
            "hazard": hazard[idx], "price_fen": price[idx], "minutes": minutes[idx], "tickets": tickets[idx],
            "is_test": is_test[idx]}))

        # usage rows (D6 late arrivals)
        late = rng.random(len(idx)) < cfg.late_usage_rate
        avail = np.where(late,
                         (_month_end(m) + pd.to_timedelta(rng.integers(15, 46, len(idx)), unit="D")).values,
                         (_month_end(m) + pd.Timedelta(hours=23)).to_datetime64())
        # pricing-page views: price-sensitive customers check prices more (NULL before tracking began)
        views = rng_sig.poisson(frac[idx] * (0.2 + 3.0 * s[idx])).astype(float)
        if t < t_track:
            views[:] = np.nan
        usage_rows.append(pd.DataFrame({
            "customer_id": cust_ids[idx], "usage_month": str(m), "minutes": minutes[idx],
            "sessions": sessions[idx], "pricing_page_views": views, "available_at": avail}))

        # tickets
        tk = np.repeat(idx, tickets[idx])
        if len(tk):
            created = _rand_day(rng, m, len(tk))
            csat = rng.choice([1, 2, 3, 4, 5, -1], size=len(tk), p=[.08, .10, .17, .30, .20, .15]).astype(float)
            csat[csat < 0] = np.nan
            # price-sensitive customers raise more billing/cancel tickets (an observable trace of s)
            p_bill, p_cancel = 0.15 + 0.40 * s[tk], 0.08 + 0.12 * s[tk]
            p_bug = (1.0 - p_bill - p_cancel) * 0.45
            uu = rng.random(len(tk))
            category = np.select([uu < p_bill, uu < p_bill + p_cancel, uu < p_bill + p_cancel + p_bug],
                                 ["billing", "cancel", "bug"], "howto")
            ticket_rows.append(pd.DataFrame({
                "ticket_id": [f"T{tick_seq + i:07d}" for i in range(len(tk))], "customer_id": cust_ids[tk],
                "created_at": created, "category": category,
                "csat": csat, "available_at": created + pd.to_timedelta(rng.integers(0, 3, len(tk)), unit="D").values}))
            tick_seq += len(tk)

        # charges: at month start for continuing customers, at signup for joiners
        bill = present & (signup_t < t) | joined
        bidx = np.where(bill)[0]
        issued = np.where(joined[bidx], signup_date[bidx], _month_start(m).to_datetime64())
        n_b = len(bidx)
        inv = pd.DataFrame({
            "invoice_id": [f"I{inv_seq + i:08d}" for i in range(n_b)], "customer_id": cust_ids[bidx],
            "invoice_type": "charge", "amount_fen_true": price[bidx], "issued_at": issued,
            "original_invoice_id": None})
        inv_seq += n_b
        # add-ons (D2): a third of buyers buy two identical packs the same day
        buyers = bidx[rng.random(n_b) < cfg.addon_rate * (1.5 - 1.25 * s[bidx])]   # price-sensitive buy fewer
        if len(buyers):
            two = rng.random(len(buyers)) < 1 / 3
            ab = np.concatenate([buyers, buyers[two]])
            day = dict(zip(buyers, _rand_day(rng, m, len(buyers))))
            add = pd.DataFrame({
                "invoice_id": [f"I{inv_seq + i:08d}" for i in range(len(ab))], "customer_id": cust_ids[ab],
                "invoice_type": "addon", "amount_fen_true": cfg.addon_price_fen,
                "issued_at": [day[b] for b in ab], "original_invoice_id": None})
            inv_seq += len(ab)
            inv = pd.concat([inv, add], ignore_index=True)
        # refunds (D4): booked 5-20 days after the charge
        charges = inv[inv.invoice_type == "charge"]
        ref = charges[rng.random(len(charges)) < cfg.refund_rate]
        if len(ref):
            rf = pd.DataFrame({
                "invoice_id": [f"I{inv_seq + i:08d}" for i in range(len(ref))],
                "customer_id": ref.customer_id.values, "invoice_type": "refund",
                "amount_fen_true": -ref.amount_fen_true.values,
                "issued_at": (pd.DatetimeIndex(ref.issued_at)
                              + pd.to_timedelta(rng.integers(5, 21, len(ref)), unit="D")).values,
                "original_invoice_id": ref.invoice_id.values})
            inv_seq += len(ref)
            inv = pd.concat([inv, rf], ignore_index=True)
        inv_rows.append(inv)

        alive &= ~churned

    customers = pd.DataFrame({
        "customer_id": cust_ids, "created_at": signup_date, "region": region, "channel": channel,
        "email": emails, "phone": phones, "birth_year": birth_year})
    customers["updated_at"] = customers["created_at"]

    sub = pd.DataFrame({"subscription_id": [f"S{i:06d}" for i in range(1, N + 1)], "customer_id": cust_ids,
                        "plan": plan, "started_at": signup_date, "ended_at": churn_date})
    sub["ended_available_at"] = sub["ended_at"] + pd.to_timedelta(rng.integers(0, 4, N), unit="D")
    reasons = np.where(s > 0.55, "price", rng.choice(["product", "service", "other"], N, p=[.5, .3, .2]))
    sub["cancel_reason"] = np.where(sub["ended_at"].notna(), reasons, None)
    sub["_plan_price_before_fen"] = [cfg.plan_price_fen[x] for x in plan]
    sub["_plan_price_after_fen"] = price_at(T - 1)
    sub["_price_changed_at"] = np.where((plan == "plus"), price_change_date.to_datetime64(),
                                        np.datetime64("NaT", "ns"))

    invoices = pd.concat(inv_rows, ignore_index=True)
    invoices["issued_at"] = pd.to_datetime(invoices["issued_at"])
    invoices = invoices[invoices["issued_at"] <= _month_end(months[-1])].reset_index(drop=True)
    invoices["available_at"] = invoices["issued_at"] + pd.to_timedelta(rng.integers(0, 2, len(invoices)), unit="D")
    reg_of = dict(zip(cust_ids, region))
    invoices["_region"] = invoices["customer_id"].map(reg_of)
    invoices["_is_test"] = invoices["customer_id"].isin(set(cust_ids[is_test]))
    # D3 unit incident: exported amount in yuan for one region-month of charges
    ui_region, ui_month = cfg.unit_incident
    ui = ((invoices["_region"] == ui_region) & (invoices["invoice_type"] == "charge")
          & (invoices["issued_at"].dt.to_period("M") == pd.Period(ui_month, "M")))
    invoices["amount_fen_exported"] = np.where(ui, invoices["amount_fen_true"] // FEN_PER_YUAN,
                                               invoices["amount_fen_true"])
    invoices["_unit_incident"] = ui
    # D1 at-least-once duplicates
    dups = invoices.sample(frac=cfg.dup_resend_rate, random_state=cfg.seed).copy()
    dups["_is_resend_duplicate"] = True
    invoices["_is_resend_duplicate"] = False
    invoices = pd.concat([invoices, dups], ignore_index=True).sort_values(
        ["issued_at", "invoice_id"], kind="stable").reset_index(drop=True)

    usage = pd.concat(usage_rows, ignore_index=True)
    usage["available_at"] = pd.to_datetime(usage["available_at"])
    tickets_df = pd.concat(ticket_rows, ignore_index=True)
    truth_cm = pd.concat(cm_rows, ignore_index=True)

    # ---------------------------------------------------------------- true finance revenue
    clean = invoices[~invoices["_is_resend_duplicate"] & ~invoices["_is_test"]].copy()
    clean["month"] = clean["issued_at"].dt.to_period("M").astype(str)
    truth_rev = (clean.groupby(["month", "_region"], as_index=False)["amount_fen_true"].sum()
                 .rename(columns={"_region": "region", "amount_fen_true": "net_revenue_fen"}))

    defects = {
        "D1_resend_duplicates": int(invoices["_is_resend_duplicate"].sum()),
        "D2_same_day_identical_addons": int(
            invoices[(invoices.invoice_type == "addon") & ~invoices._is_resend_duplicate]
            .duplicated(["customer_id", "issued_at", "amount_fen_true"], keep=False).sum()),
        "D3_unit_incident_rows": int(invoices["_unit_incident"].sum()),
        "D4_refund_rows": int((invoices.invoice_type == "refund").sum()),
        "D5_test_accounts": int(is_test.sum()),
        "D6_late_usage_rows": int((usage["available_at"]
                                   - pd.PeriodIndex(usage["usage_month"], freq="M").end_time.normalize())
                                  .dt.days.gt(10).sum()),
        "D8_schema_change_from": cfg.schema_change_from,
        "D9_shift_month": cfg.shift_month,
    }
    prog = pd.concat(prog_rows, ignore_index=True) if prog_rows else pd.DataFrame()
    if len(prog):
        prog.insert(0, "contact_id", [f"R{i:07d}" for i in range(1, len(prog) + 1)])
        prog["channel"] = "app_message"
        prog["offer"] = "discount_10pct_1m"
    contacts = prog[["contact_id", "customer_id", "campaign_month", "arm", "exposed", "channel", "offer",
                     "assigned_at", "available_at"]].copy() if len(prog) else prog
    truth_program = (prog[["contact_id", "customer_id", "campaign_month", "arm", "exposed",
                           "_p0", "_p1", "_tau_logit", "_churned"]]
                     .rename(columns=lambda c: c.lstrip("_")) if len(prog) else prog)
    if len(truth_program):
        truth_program["segment_truth"] = np.select(
            [truth_program["tau_logit"] < 0, truth_program["tau_logit"] > 0], ["persuadable", "sleeping_dog"],
            "unmoved")
    defects["program_contacts"] = int(len(contacts))
    return World(cfg, months, customers, sub, invoices, usage, tickets_df, truth_cm, truth_rev,
                 exp_rows, defects, contacts, truth_program)


# ====================================================================== exports
def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def export_sources(world: World, cutoff: str | pd.Timestamp, out_dir: Path) -> dict:
    """Write what the source systems would export at `cutoff` (end of that day).

    Only rows *available* by the cutoff are present, and mutable current-state columns
    show their value as known at the cutoff. Returns a manifest with per-file digests.
    """
    cfg = world.cfg
    cut = pd.Timestamp(cutoff).normalize() + pd.Timedelta(hours=23, minutes=59, seconds=59)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, pd.DataFrame] = {}

    c = world.customers[world.customers["created_at"] <= cut].copy()
    files["crm_customers"] = c

    s = world.subscriptions[world.subscriptions["started_at"] <= cut].copy()
    known_end = s["ended_available_at"] <= cut
    s.loc[~known_end, ["ended_at", "cancel_reason"]] = [pd.NaT, None]
    changed = s["_price_changed_at"].notna() & (s["_price_changed_at"] <= cut)
    s["monthly_price_cents"] = np.where(changed, s["_plan_price_after_fen"], s["_plan_price_before_fen"])
    s["updated_at"] = s[["started_at"]].max(axis=1)
    s.loc[changed, "updated_at"] = s.loc[changed, "_price_changed_at"]
    s.loc[known_end, "updated_at"] = s.loc[known_end, "ended_available_at"]
    files["billing_subscriptions"] = s[["subscription_id", "customer_id", "plan", "monthly_price_cents",
                                        "started_at", "ended_at", "cancel_reason", "updated_at"]]

    inv = world.invoices[world.invoices["available_at"] <= cut]
    v2 = cut >= pd.Timestamp(cfg.schema_change_from)
    cols = ["invoice_id", "customer_id", "invoice_type", "issued_at", "available_at", "original_invoice_id"]
    inv_out = inv[cols].copy()
    if v2:  # D8: the billing vendor upgrade renames the amount column and adds currency
        inv_out.insert(3, "amount_minor", inv["amount_fen_exported"].values)
        inv_out.insert(4, "currency", "CNY")
    else:
        inv_out.insert(3, "amount_cents", inv["amount_fen_exported"].values)
    files["billing_invoices"] = inv_out

    files["app_usage_monthly"] = world.usage[world.usage["available_at"] <= cut]
    files["support_tickets"] = world.tickets[world.tickets["available_at"] <= cut]
    files["crm_retention_contacts"] = world.contacts[world.contacts["available_at"] <= cut]

    closed = [m for m in world.truth_revenue["month"].unique()
              if _month_end(pd.Period(m, "M")) + pd.Timedelta(days=cfg.finance_close_lag_days) <= cut]
    files["finance_closed_revenue"] = (world.truth_revenue[world.truth_revenue["month"].isin(closed)]
                                       .sort_values(["month", "region"]).reset_index(drop=True))

    manifest = {"cutoff": str(pd.Timestamp(cutoff).date()), "billing_schema_version": 2 if v2 else 1,
                "tables": {}}
    for name, df in files.items():
        path = out_dir / f"{name}.csv"
        df.to_csv(path, index=False)
        manifest["tables"][name] = {"file": path.name, "rows": int(len(df)), "sha256": _sha256_file(path)}
    (out_dir / "_export_manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def write_custody(world: World, custody_dir: Path) -> None:
    """Ground truth for evaluators only. Agents and candidate code must never read this."""
    custody_dir = Path(custody_dir)
    custody_dir.mkdir(parents=True, exist_ok=True)
    world.truth_customer_month.to_parquet(custody_dir / "truth_customer_month.parquet", index=False)
    world.truth_revenue.to_parquet(custody_dir / "truth_revenue.parquet", index=False)
    world.truth_experiment.to_parquet(custody_dir / "truth_experiment.parquet", index=False)
    world.truth_program.to_parquet(custody_dir / "truth_program.parquet", index=False)
    meta = {"config": {k: (list(v) if isinstance(v, tuple) else v) for k, v in asdict(world.cfg).items()},
            "defects_planted": world.defects,
            "warning": "CUSTODY: evaluator-only ground truth. Never mount into agent or candidate sandboxes."}
    (custody_dir / "world_meta.json").write_text(json.dumps(meta, indent=2, default=str))
