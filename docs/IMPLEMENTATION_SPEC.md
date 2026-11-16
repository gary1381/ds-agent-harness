# DS Harness — reference implementation spec (for builders)

> 说明：这是并行实现时给各个 builder agent 的绑定接口契约（英文）。实现中有记录在案、理由充分的偏离，以代码为准；用户文档见 `DESIGN.zh-CN.md` 与 `README.md`。

Status: binding interface contract for parallel implementation. Language: English (code), Chinese (user docs).
Python 3.12, deps fixed in `pyproject.toml` (duckdb 1.5, pandas 3, numpy 2, scikit-learn 1.9, pandera 0.34,
sqlglot 30, pyyaml, mcp 2.3 (`from mcp.server.mcpserver import MCPServer`), scipy). **Do not add dependencies.**
Run tests with `.venv/bin/python -m pytest -q`. Use the session fixtures in `tests/conftest.py`
(`small_ws`, `build_reports`, `train_build`, `score_build`); never touch `./workspace` from tests.

## 0. What exists (do not rewrite; you may read)

- `dsharness.world.generator`: `WorldConfig`, `simulate()`, `export_sources(world, cutoff, dir)`, `write_custody(world, dir)`.
  Planted defects D1–D9 are documented in its module docstring. Custody files (evaluator-only):
  `custody/truth_customer_month.parquet` (customer_id, month 'YYYY-MM', active_at_start, at_risk, churned,
  engagement, price_sensitivity, hazard, price_fen, minutes, tickets, is_test),
  `custody/truth_revenue.parquet` (month, region, net_revenue_fen), `custody/truth_experiment.parquet`
  (customer_id, p0, p1, u, tau_logit, price_fen, segment_truth) for July 2026, `custody/world_meta.json`.
- `dsharness.workspace`: `Workspace(root)` with zones `sources/ lake/ custody/ registry/ runs/`;
  `CUTOFFS = {train: 2026-05-31, score: 2026-06-30, drift: 2026-08-31, monitor: 2026-09-30}`,
  `PIT_CUTOFFS = (2026-02-28, 2026-03-31)`.
- `dsharness.lake.build`: `build_snapshot(ws, cutoff, pipeline_dir=None, out_root=None) -> BuildReport`
  (status READY|FAILED, build_id = f"{cutoff}_{pipeline_digest[:8]}"), `latest_ready_build(ws, cutoff, pipeline_dir=None)`,
  `warehouse_path(ws, build_id)`, `load_report(ws, build_id)`, `DEFAULT_PIPELINE`.
  `dsharness.lake.runner`: `pipeline_digest(dir)`, `run_models`, `run_tests`.
  Default pipeline: drift/monitor builds FAIL (billing schema v2: `amount_minor`, `currency`) — intended break-fix scenario.

### Warehouse schemas (DuckDB file per build)

- `bronze.*` raw typed source tables (contains PII: crm_customers.email/phone/birth_year). **Agents never query `bronze`.**
- `bronze_safe.*` views of bronze minus PII columns (uncertified, defect-laden; agents may query, results flagged).
- `staging.stg_*` light renames.
- `silver.*` certified tables:
  - `customers(customer_id, created_at TIMESTAMP, region, channel, is_test_account BOOL)`
  - `subscriptions(subscription_id, customer_id, plan, monthly_price_fen [MUTABLE current], started_at, ended_at [NULL if active/unknown; known-at = updated_at], cancel_reason [POST-OUTCOME], updated_at)`
  - `invoices(invoice_id UNIQUE, customer_id, invoice_type charge|addon|refund, amount_fen [fen, refunds negative, unit-corrected], unit_corrected BOOL, issued_at, available_at, revenue_month 'YYYY-MM', original_invoice_id, region, is_test_account)`
  - `usage_monthly(customer_id, usage_month 'YYYY-MM', minutes, sessions, available_at)` — late rows exist (D6)
  - `tickets(ticket_id, customer_id, created_at, category billing|bug|howto|cancel, csat NULL|1..5, available_at)`
  - `finance_closed_revenue(month, region, net_revenue_fen)` — independent official record
  - `ref_plan_prices(plan, price_fen, valid_from DATE, valid_to DATE)`

### Verified metric SQL (matches custody truth exactly; reuse it)

Month spine for months [a, b): `SELECT strftime(m,'%Y-%m') AS month, m AS month_start, (m + INTERVAL 1 MONTH - INTERVAL 1 DAY) AS month_end FROM range(DATE a, DATE b, INTERVAL 1 MONTH) t(m)`.
- active at end of month: `started_at < month_end + 1 day AND (ended_at IS NULL OR ended_at >= month_end + 1 day)`, non-test.
- active at start of month: `started_at < month_start AND (ended_at IS NULL OR ended_at >= month_start)`, non-test.
- churned in month: active at start AND `ended_at >= month_start AND ended_at < month_end + 1 day`.
- net revenue: `SUM(amount_fen) FROM silver.invoices WHERE NOT is_test_account GROUP BY revenue_month[, region]`.

## 1. Shared conventions

- Digest: `dsharness.common.digest(obj) -> str` = sha256 of canonical JSON (`sort_keys`, `separators=(",",":")`, `allow_nan=False`, `default=str`). File digest: `file_digest(path)`; dir digest: reuse `pipeline_digest`.
- Time: ISO-8601 UTC strings in records; `dsharness.common.utcnow()` (overridable for tests via `DSH_NOW` env var).
- Outcomes: `PASS | FAIL | INCONCLUSIVE | ERROR` (check results) — never booleans in records.
- Every module that writes to `registry/` appends JSON lines; never rewrites history.
- Read-only warehouse connection for anything agent-facing: `dsharness.common.open_readonly(path)` →
  `duckdb.connect(path, read_only=True)` then `SET enable_external_access=false; SET lock_configuration=true`.
- No network, no LLM calls inside library code (live agents are an optional runner, see §8).

`src/dsharness/common.py` already exists (digest, file_digest, utcnow, parse_ts, open_readonly, append_jsonl, read_jsonl). Package `__init__.py` files already exist. Extend common.py only if strictly needed, append-only.

## 2. W1 — context layer + SQL verification  (`context/`, `verify/sql_static.py`, `verify/reconcile.py`, `common.py`)

Files: `src/dsharness/context/{__init__,semantic,catalog}.py`, `context/semantic_model.yaml`, `context/catalog.yaml`,
`context/verified_queries.yaml`, `src/dsharness/verify/{__init__,sql_static,reconcile}.py`, `tests/test_context.py`, `tests/test_sql_static.py`.

Semantic model YAML (OSI/MetricFlow-shaped, small): metrics `net_revenue_fen`, `active_subscribers_eom`,
`active_subscribers_bom`, `churned_customers`, `churn_rate`, `addon_units`, `arpu_fen` (net revenue / active at start),
`avg_csat`; dimensions `month` (required time grain), `region`, `plan`, `channel`, `invoice_type` (where meaningful);
each metric has `description`, `owner`, `caveats`, allowed dimensions.

API:
```python
@dataclass class Metric: name, description, owner, dimensions: list[str], caveats: list[str]
class SemanticModel:
    metrics: dict[str, Metric]; digest: str
def load_semantic_model(path=None) -> SemanticModel
def compile_metric(model, metric: str, dimensions: list[str], months: tuple[str,str] | None = None,
                   filters: dict[str, str|list[str]] | None = None) -> str   # SQL over silver.*; raises ValueError on unknown metric/dim
def query_metric(con, model, metric, dimensions, months=None, filters=None) -> pd.DataFrame  # columns: month, <dims...>, value
```
`months=("2026-01","2026-04")` is inclusive. `month` always present in output.

Catalog YAML: per table: `schema`, `tier` (`certified|staging|uncertified`), `description`, `grain`, `columns` with
`description`, `pii: bool`, `temporal` in {`static`,`event_time`,`availability_time`,`mutable_current`,`post_outcome`},
`caveats`. Include `bronze_safe.*` tables (tier uncertified, caveats listing D1–D5 pitfalls in plain words) and `silver.*`.
Required-filter rules: any query reading `silver.invoices` or `silver.customers`-derived counts must exclude test accounts
(`is_test_account`), and any query on `bronze_safe.billing_invoices` is uncertified.
```python
def load_catalog(path=None) -> Catalog            # .tables: dict[str, TableDoc]; .digest
def describe_table(catalog, name) -> dict
def search_catalog(catalog, query: str) -> list[dict]   # simple keyword ranking over names/descriptions
```

Verified queries YAML: list of `{id, question, metric_call: {metric, dimensions, months, filters} | sql, split: context|eval}`.
At least 12 entries; ≥4 marked `eval` (never exposed to agents at runtime — the Snowflake rule: a verified query is either
runtime context or eval ground truth, not both). Provide `load_verified_queries(split=...)`.

`verify/sql_static.py`:
```python
@dataclass class StaticReport:
    ok: bool; read_only: bool; single_statement: bool; tables: list[str]; schemas: list[str]
    tier: str  # certified | uncertified
    pii_columns: list[str]; mutable_or_post_outcome_columns: list[str]
    violations: list[str]   # hard: non-SELECT, multiple statements, bronze.*, PII column, file/table functions (read_csv, read_parquet, glob...), ATTACH/COPY/INSTALL/LOAD/SET/PRAGMA
    warnings: list[str]     # soft: uncertified tables, missing is_test_account filter, mutable/post_outcome columns
def analyze_sql(sql: str, catalog) -> StaticReport          # sqlglot dialect="duckdb"; parse failure -> ok=False, violation
```
`verify/reconcile.py`:
```python
def compare_frames(actual: pd.DataFrame, expected: pd.DataFrame, keys: list[str], value_col: str,
                   rel_tol=1e-9, abs_tol=0.0) -> dict   # {outcome PASS|FAIL|ERROR, n_expected, n_actual, mismatches:[...<=20], missing_keys, extra_keys, max_abs_diff}
def reconcile_answer_to_metric(con, model, answer_rows: list[dict], metric, dimensions, months, filters=None) -> dict
```
Tests: compiled metrics equal custody truth on `small_ws` (net revenue by month×region; active EOM; churn rate by month);
static analyzer blocks `SELECT * FROM bronze.crm_customers`, `COPY`, `read_csv('/etc/passwd')`, multi-statements, PII columns;
warns on `bronze_safe.billing_invoices` and on missing test-account filter.

## 3. W2 — evidence store + gate + approvals  (`evidence/`, `policies/`, CI example)

Files: `src/dsharness/evidence/{__init__,records,gate}.py`, `src/dsharness/policies/gates.yaml`,
`.github/workflows/dsh-gate.example.yml` (example CI: build → checks → `dsh gate` → `actions/attest` → protected environment),
`tests/test_gate.py`.

```python
@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str; check_id: str; check_version: str; stage: str
    subject: dict          # e.g. {"kind":"candidate","candidate_id":..,"build_id":..,"semantic_digest":..,"eval_plan_digest":..}
    subject_digest: str    # digest(subject)
    outcome: str           # PASS|FAIL|INCONCLUSIVE|ERROR
    metrics: dict; producer: str; issued_at: str; expires_at: str | None
    limitations: list[str]; supersedes: str | None = None
class EvidenceStore:      # append-only JSONL at registry/evidence.jsonl
    def __init__(self, ws): ...
    def record(self, *, check_id, check_version, stage, subject, outcome, metrics, producer, ttl_s=None, limitations=(), supersedes=None) -> EvidenceRecord
    def all(self) -> list[EvidenceRecord]
    def for_subject(self, subject_digest) -> list[EvidenceRecord]
@dataclass(frozen=True)
class Approval: approval_id, stage, subject_digest, approver, role, requested_by, issued_at
class ApprovalStore: record(...), for_subject(...)   # registry/approvals.jsonl
@dataclass(frozen=True)
class GateDecision: verdict: str  # ALLOW|HOLD|REJECT
    stage: str; subject_digest: str; policy_digest: str; reasons: tuple[str,...]; evidence_ids: tuple[str,...]; ignored: tuple[str,...]
def load_policy(path=None) -> dict
def evaluate_gate(policy: dict, stage: str, subject: dict, evidence: list[EvidenceRecord],
                  approvals: list[Approval], requested_by: str, now: str | None = None) -> GateDecision
```
Gate rules (fixes the draft's bugs — see `docs/research/findings_2026-10-19.txt`, draft-critique findings 8–12):
1. Only records whose `subject_digest == digest(subject)` and whose `check_id` is required for the stage are considered.
2. **Authenticate/authorise before counting**: records from producers not in the check's `allowed_producers` are
   *ignored* (listed in `ignored`), never cause REJECT by themselves.
3. **Supersede semantics**: for each required check use the latest (by `issued_at`, tie → later in file) authorised record;
   an explicit `supersedes` chain is honoured. A retried PASS after ERROR ⇒ PASS.
4. Expired (`expires_at < now`) or older than `max_age_s` ⇒ HOLD (`stale`).
5. Latest FAIL ⇒ REJECT. Latest ERROR/INCONCLUSIVE or missing ⇒ HOLD with a reason saying what evidence would unblock.
6. Approvals: per stage `{role, count, no_self_approval}`; approver ≠ requested_by when `no_self_approval`; missing ⇒ HOLD.
7. Unknown stage / malformed policy ⇒ REJECT (configuration error is not permission).
Policy YAML stages (minimum): `answer_publish` (analytics), `pipeline_merge` (DE), `feature_accept`, `model_shadow`,
`model_experiment` (requires business_owner approval), `model_scale` (requires experiment readout PASS + business_owner + risk approvals),
`retrain_promote`. Producer identities are strings like `verifier:sql_static`, `verifier:reconcile`, `evaluator:custody`,
`ci:pipeline`, `experiment:readout`. In production they map to CI OIDC identities + Sigstore/in-toto attestations (document
in the YAML comments; do not implement crypto). Tests: ≥20 cases incl. retry-after-error, untrusted producer ignored,
wrong subject ignored, FAIL rejects, stale holds, self-approval refused, unknown stage rejects.

## 4. W3 — ML factory  (`ml/`, `verify/leakage.py`)

Files: `src/dsharness/ml/{__init__,features,train,custody}.py`, `src/dsharness/verify/leakage.py`, `tests/test_ml.py`, `tests/test_leakage.py`.

Population at `as_of` (first day of a month, 'YYYY-MM-DD'): non-test customers active at start of that month (see §0),
as known in the build. Note: a churn a day before `as_of` may only become *available* after `as_of`, so early vs late
builds can differ slightly in population membership. `time_travel_check` compares values only for customers present in
both populations and reports the membership difference separately (informational, not a feature leak). Sealed
evaluation inner-joins scores with custody `at_risk` customers.
Labels: `churned_next_month` = churned in the month starting at `as_of`. **Maturity**: label usable only if
`as_of + 1 month + 7 days <= build cutoff`; immature labels are excluded, never treated as 0.

`ml/features.py`:
```python
@dataclass(frozen=True) class FeatureDef: name: str; sql: str; description: str; origin: str  # 'trusted'|'proposed'
TRUSTED_FEATURES: dict[str, FeatureDef]   # point-in-time correct, use {as_of} placeholder (DATE literal)
def population_sql(as_of) -> str
def build_features(con, as_of: str, names: list[str], extra: dict[str, FeatureDef] | None = None) -> pd.DataFrame  # customer_id + features, one row per population member
def build_labels(con, as_of: str, cutoff: str) -> pd.DataFrame   # customer_id, churned_next_month, label_mature
def build_training_frame(con, as_of_list, names, cutoff, extra=None) -> pd.DataFrame  # adds as_of col; drops immature
```
Each feature SQL returns `(customer_id, value)` for the population and may only use rows with
`available_at <= {as_of}` / `created_at < {as_of}` / `issued_at < {as_of}` (invoices `available_at <= {as_of}`), and
`ended_at` only as "known by as_of" (`updated_at <= {as_of}`). Trusted set (≥10): tenure_months, plan (categorical),
region, channel, last_charge_fen (last charge amount before as_of = price history; NOT subscriptions.monthly_price_fen),
minutes_last_month, minutes_3m_avg, minutes_trend, sessions_last_month, tickets_90d, billing_tickets_90d,
cancel_tickets_90d, avg_csat_180d, refunds_180d, addons_90d. Also define (NOT trusted, for benchmark/negative tests) in
`NAIVE_FEATURES`: `minutes_last_month_naive` (ignores available_at), `current_price_fen` (subscriptions.monthly_price_fen),
`has_cancel_reason` (post-outcome), `known_end_flag` (ended_at not null without availability guard).

`verify/leakage.py`:
```python
def time_travel_check(ws, feature: FeatureDef, as_of: str, early_build: str, late_build: str, tol=1e-9) -> dict
    # compute feature at as_of on a build whose cutoff is the day before as_of (early) and on a later build;
    # any per-customer difference => FAIL (the feature uses information not available at as_of); returns
    # {outcome, n_compared, n_diff, examples}
def static_temporal_check(sql: str, catalog) -> dict   # uses catalog temporal tags: mutable_current/post_outcome columns without guard => FAIL; availability columns not referenced for event tables => INCONCLUSIVE
def check_feature(ws, feature, catalog) -> dict        # both checks; PASS only if both PASS
```
Use PIT builds: as_of `2026-03-01` (early build cutoff 2026-02-28) and `2026-04-01` (early 2026-03-31); late build = train (2026-05-31).
Expected: all TRUSTED pass; `minutes_last_month_naive`, `has_cancel_reason`, `known_end_flag` FAIL time-travel;
`current_price_fen` passes time-travel in this window (no price change yet) but FAILS static check (mutable_current) — this
asymmetry must be asserted in tests and documented (some leaks are only visible statically or later).

`ml/train.py`:
```python
@dataclass(frozen=True) class CandidateSpec:
    features: tuple[str, ...]; model: str  # 'logreg' | 'hgb'
    params: dict; train_as_of: tuple[str, ...]; seed: int = 0; extra_features: dict | None = None
def train_candidate(ws, build_id, spec, *, requested_by: str) -> dict
  # trusted template: sklearn Pipeline(ColumnTransformer(OneHot cats, impute+scale nums) + model), fit inside folds only;
  # expanding-window time CV over train_as_of (>=3 folds), metrics per fold: auc, brier, logloss, base_rate;
  # saves runs/<candidate_id>/{model.joblib, spec.json, cv.json}; candidate_id = digest(spec+build_id+code version)[:16]
  # returns {candidate_id, cv: [...], cv_mean: {...}, artifact_digest, feature_set_digest}
def load_candidate(ws, candidate_id) -> (pipeline, spec)
def score(ws, candidate_id, build_id, as_of) -> pd.DataFrame  # customer_id, score
```
`ml/custody.py` — sealed final evaluation:
```python
class SealedEvaluator:
    FINAL_AS_OF = "2026-06-01"; BUDGET_PER_TASK = 3
    def __init__(self, ws, task_id="churn-2026-06"): ...
    def evaluate(self, candidate_id, build_id) -> dict
      # features from build (train cutoff), scores via ml.train.score, labels from custody truth (June 2026 churn, non-test at_risk),
      # metrics: auc, brier, logloss, ece (10 bins), base_rate, n, bootstrap 95% CI for auc & brier (customer resampling, 500 reps, seeded),
      # per-region auc; NEVER returns per-row labels; decrements budget (custody/ledger.jsonl); over budget => {"outcome":"ERROR","reason":"holdout budget exhausted"}
```
Do not write evidence records here: library functions return result dicts and the tools layer (W5) is the single
place that records evidence (producer `evaluator:custody`, `verifier:leakage`, ...). Same rule for W1/W3/W4 checks.
Tests: a logreg candidate on trusted features gets sealed AUC > 0.6; naive-feature candidate's dev CV inflates vs sealed;
budget enforced; per-row labels never returned.

## 5. W4 — decision loop, monitoring, stats, economics  (`decision/`, `monitor/`, `verify/stats.py`, `economics/`)

Files: `src/dsharness/decision/{__init__,policies,experiment,oracle}.py`, `src/dsharness/monitor/{__init__,drift}.py`,
`src/dsharness/verify/stats.py`, `src/dsharness/economics/{__init__,roi}.py`, `tests/test_decision.py`, `tests/test_stats.py`.

```python
# verify/stats.py
def bootstrap_ci(values_or_fn, n=1000, alpha=0.05, seed=0, cluster=None) -> (lo, hi)
def two_proportion_diff(x_a, n_a, x_b, n_b, alpha=0.05) -> dict  # diff=p_a-p_b, se, ci, z, p_value (normal approx)
def srm_test(n_a, n_b, expected_share_a=0.5) -> dict             # chi-square, p_value, outcome PASS if p>=0.001
def auc(y, s); brier(y, p); ece(y, p, bins=10)
# decision/policies.py
def top_k(scores: pd.DataFrame, k: int, col="score") -> list[str]; random_k(ids, k, seed)
# decision/experiment.py
def assign(customer_ids, salt: str, p_treat=0.5) -> pd.DataFrame  # deterministic sha256(salt+id) bucketing; columns customer_id, arm in {treatment,control}
@dataclass class ValueParams: horizon_months_retained_value: int = 6; margin_rate: float = 0.6; contact_cost_fen: int = 1500; offer_cost_rate: float = 0.2
def readout(assignments, outcomes: pd.DataFrame, prices: pd.DataFrame, vp: ValueParams) -> dict
  # ITT: churn_control - churn_treatment with CI (two_proportion_diff), srm, incremental_retained (point+CI),
  # incremental_value_fen = retained*horizon*margin*price_mean - contacts*(contact_cost + offer_cost_rate*price) for treatment arm (point+CI),
  # outcome PASS if srm ok and n sufficient, INCONCLUSIVE if CI spans 0, FAIL if srm fails.
# decision/oracle.py  (custody only — evaluator side)
class ExperimentOracle:  # July 2026; uses truth_experiment p0, p1, u
    def outcomes(self, assignments) -> pd.DataFrame   # customer_id, churned (u < p1 if treatment else u < p0)
    def true_effect(self, customer_ids) -> dict        # mean(p0-p1), expected retained, expected value under ValueParams
    def segment_truth(self, customer_ids) -> pd.Series
# monitor/drift.py
def psi(ref: pd.Series, cur: pd.Series, bins=10) -> float   # quantile bins on ref; categorical supported
def drift_report(ref_df, cur_df, features, threshold=0.2) -> dict  # per-feature psi, flagged list
# economics/roi.py
@dataclass class Assumptions: ... (analyst loaded cost per hour CNY & USD profiles, tasks/month by type, minutes saved, adoption, review minutes per task, LLM cost per task, platform cost/month, error cost)
def portfolio_estimate(a: Assumptions, measured: dict | None = None) -> pd.DataFrame  # per use case: gross value, costs, net, payback; measured overrides (from bench)
```
Tests: SRM detects 45/55 at n=10k; two_proportion CI covers truth in simulation (≥90% of 200 reps); experiment readout on
`small_ws` with risk-top-k targeting: estimated ITT CI contains oracle true effect for the targeted set (seeded);
psi≈0 for identical, >0.2 for shifted distributions.

## 6. W5 — governed tools + MCP server + skills + DE break-fix  (`tools/`, `mcp_server.py`, `.claude/skills/`, `AGENTS.md`)

Files: `src/dsharness/tools/{__init__,context,analytics,ds,de,audit}.py`, `src/dsharness/mcp_server.py`,
`.claude/skills/{analytics-answer,churn-modeling,pipeline-breakfix}/SKILL.md`, `AGENTS.md` (repo root), `.mcp.json`,
`tests/test_tools.py`, `tests/test_mcp.py`.

```python
@dataclass class ToolContext:
    ws: Workspace; build_id: str; role: str  # analyst | data_scientist | data_engineer
    principal: str; task_id: str | None = None; max_rows: int = 200; budget_calls: int = 200
def make_context(ws, cutoff: str, role: str, principal: str, task_id=None) -> ToolContext   # uses latest_ready_build; refuses FAILED builds
ROLE_TOOLS = {"analyst": [...], "data_scientist": [...], "data_engineer": [...]}   # ≤ 10 tools each, non-overlapping
# analytics (all roles)
def list_metrics(ctx) -> dict; def describe_metric(ctx, name) -> dict
def query_metric(ctx, metric, dimensions, months=None, filters=None) -> dict   # {rows, sql, tier:'governed', semantic_digest}
def search_catalog(ctx, query) -> dict; def describe_table(ctx, table) -> dict
def run_sql(ctx, sql) -> dict      # analyze_sql first; violations => refused; executes with LIMIT max_rows; returns rows, tier, warnings
def submit_answer(ctx, question: str, answer_rows: list[dict], sql: str | None = None, metric_call: dict | None = None,
                  claims: list[str] = ()) -> dict
  # verification ladder: static (if sql) -> re-execute sql and compare to answer_rows (honesty check) ->
  # if a governed metric covers the question (metric_call given or matched) reconcile to it -> finance-close reconciliation for revenue totals ->
  # tier: VERIFIED (reconciled to governed metric/official record) | CONSISTENT (re-executes, static ok, no oracle) | FLAGGED (any mismatch/violation);
  # records evidence (stage answer_publish), returns {tier, reasons, evidence_ids}
# data_scientist
def list_features(ctx) -> dict; def propose_feature(ctx, name, sql, description) -> dict   # leakage.check_feature -> evidence (feature_accept)
def train_candidate(ctx, features, model, params, train_as_of) -> dict; def request_final_evaluation(ctx, candidate_id) -> dict
def request_release(ctx, candidate_id, stage="model_shadow") -> dict   # evaluate_gate; never deploys by itself
# data_engineer
def pipeline_status(ctx, cutoff) -> dict; def read_pipeline_file(ctx, path) -> dict
def propose_pipeline_change(ctx, files: dict[str, str], rationale: str) -> dict
  # copies DEFAULT_PIPELINE to runs/<change_id>/pipeline, applies files (only models/*.sql, schema.yml), then CI:
  # (1) build drift cutoff into runs/<change_id>/lake (must be READY: contracts+models+tests incl. finance reconciliation),
  # (2) regression: build score cutoff with the new pipeline and diff every silver table vs the current READY score build (must be identical),
  # records evidence (stage pipeline_merge, producer ci:pipeline); returns {change_id, ci, diff, gate}
```
All tool calls append to `registry/audit.jsonl` (`tools/audit.py`): ts, principal, role, tool, args digest + redacted args, result summary, duration.
Budget exhaustion ⇒ explicit error. Tools never raise raw exceptions to agents: return `{"error": ..., "kind": ...}`.

`mcp_server.py`: `build_server(ctx) -> MCPServer` registering the role's tools with clear docstrings; `main(argv)`
(`--role`, `--cutoff`, `--workspace`, `--principal`) running stdio. Tool descriptions must tell the agent: use governed
metrics first; raw SQL results are uncertified; submit answers through `submit_answer`.

Skills (Agent Skills format: YAML front-matter `name`, `description`; body = procedure, pitfalls, checklists; reference the
MCP tool names). `AGENTS.md`: repo conventions for coding agents (how to run tests, trust zones, never read custody/).
`.mcp.json`: three servers (analyst, data_scientist, data_engineer) launched via `.venv/bin/python -m dsharness.mcp_server`.
Tests: role isolation (analyst cannot call train), bronze/PII refused, submit_answer tiers (correct governed → VERIFIED;
wrong numbers → FLAGGED; honest SQL with no oracle → CONSISTENT), pipeline break-fix: a correct v2 fix passes CI and the
regression diff; a wrong fix (divide by 100) fails reconciliation; MCP server lists tools per role (call `list_tools` in-process).

## 7. W6 — benchmark + CLI + demo  (`bench/`, `cli.py`, `demo.py`)

Files: `src/dsharness/bench/{__init__,tasks,agents,run,live}.py`, `src/dsharness/cli.py`, `src/dsharness/demo.py`,
`tests/test_bench.py`.

Tasks (truth from custody / verified metric SQL computed by the evaluator, never from agent output):
- Analytics A1–A6: e.g. net revenue by region for 2026-03 (D1,D3,D4,D5 traps); active subscribers at end of 2026-04;
  churn rate 2026-04; add-on units sold 2026-02 (D1,D2 traps); ARPU 2026-04; average CSAT of billing tickets in Q1 2026.
- DE E1: billing schema v2 break-fix (CI must pass incl. finance reconciliation + regression diff).
- ML M1: churn model for 2026-06; truth = sealed evaluation.
- Decision D1: "estimate the incremental value of contacting the top-600 risk customers in July" — naive agents claim value
  from predicted risk; truth = oracle true effect.
Scripted agents (deterministic, no LLM): `careful` (governed metrics, trusted features, correct fix, experiment readout) and
several `naive_*` variants, each embodying one realistic mistake (raw bronze_safe SQL; dedup by (customer, day, amount);
forget test accounts; ignore refunds; leaky features; wrong unit fix; value = sum of risk × LTV).
Runner computes for each (task, agent): `correct` (vs truth within tolerance), `claimed_success`, harness verdict.
Metrics: correct rate; **silent failure rate** = wrong ∧ delivered-as-success; without harness (agent's own claim) vs with
harness (tier/gate); caught rate; false-block rate (correct but FLAGGED/HOLD); write `runs/bench/<ts>/report.{json,md}`.
`bench/live.py` (optional, not run in tests): runs `claude -p --bare --mcp-config <tmp json> --strict-mcp-config
--output-format json --json-schema <schema> --max-budget-usd <b>` per analytics task, captures `total_cost_usd`, grades;
computes pass^k when k>1; must be skipped gracefully if `claude` is absent.
CLI (`dsh`): `world`, `build --cutoff`, `bench [--agents ...]`, `demo` (end-to-end scenario printing each gate decision),
`mcp --role ... --cutoff ...`, `gate --stage --subject-json` (for CI), `roi`.
`demo.py` timeline: build world → builds → analytics answers (good+bad) → DE break-fix → features + leakage →
train → sealed eval → gate model_shadow → approval → model_experiment → July experiment readout vs truth → value claim
→ drift detection Aug/Sep → retrain proposed (not auto-promoted). Prints a compact log + writes `runs/demo/summary.json`.

## 8. Non-goals for the reference implementation

Real warehouse adapters, auth (OAuth/OIDC), containers/gVisor, signatures, UI. Each is described in the design doc with
the production component that replaces the reference piece.
