"""End-to-end test of DS Harness on REAL public data: UCI Online Retail II (CC BY 4.0).

What it exercises, reusing the harness modules unchanged:
  1. source contract + anomaly probes          (lake.contracts)
  2. governed pipeline + data tests + conservation reconciliation, sandboxed build (lake.build/runner)
  3. governed metric vs naive SQL answers       (context.semantic + verify.reconcile)
  4. time-travel leakage check on real history  (same rule as verify.leakage)
  5. repurchase model: time-based dev CV vs a holdout sealed in a LATER snapshot (verify.stats)
  6. evidence + gate decision                    (evidence.records / evidence.gate)
  7. what cannot be verified on this data       (no independent ledger, no randomized intervention)

Run from the repo root:  .venv/bin/python examples/online_retail/run_e2e.py
Needs workspace/external/online_retail_ii/online_retail_ii.parquet (see README section in the report).
"""
from __future__ import annotations

from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import shutil
import sys

import numpy as np
import pandas as pd
import pandera.pandas as pa
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from dsharness.common import open_readonly
from dsharness.context.semantic import load_semantic_model, query_metric
from dsharness.evidence.gate import decide
from dsharness.evidence.records import EvidenceStore
from dsharness.lake import contracts as C
from dsharness.lake.build import build_snapshot, warehouse_path
from dsharness.verify.reconcile import reconcile_answer_to_metric
from dsharness.verify.stats import auc
from dsharness.workspace import Workspace

REPO = Path(__file__).resolve().parents[2]
EX = Path(__file__).resolve().parent
DATA = REPO / "workspace/external/online_retail_ii/online_retail_ii.parquet"
WS = Workspace(REPO / "workspace/external/online_retail_ii/ws")
TABLE = "retail_transactions"
CUT_PIT, CUT_TRAIN, CUT_SCORE = "2011-02-28", "2011-05-31", "2011-08-31"
HORIZON = 90                     # label: buys again within 90 days of as_of
TRAIN_AS_OF = [f"{y}-{m:02d}-01" for y, m in [(2010, i) for i in range(3, 13)] + [(2011, 1), (2011, 2), (2011, 3)]]
SEALED_AS_OF = "2011-06-01"
LOG: list[str] = []


def log(msg=""):
    print(msg)
    LOG.append(msg)


# --------------------------------------------------------------------------- 1. source contract
CONTRACT = pa.DataFrameSchema({
    "invoice": pa.Column(str), "stock_code": pa.Column(str), "description": pa.Column(str, nullable=True),
    "quantity": pa.Column(int, coerce=True), "invoice_ts": pa.Column(pa.DateTime, coerce=True),
    "price": pa.Column(float, coerce=True), "customer_id": pa.Column(str, nullable=True),
    "country": pa.Column(str), "available_at": pa.Column(pa.DateTime, coerce=True),
}, strict=True)
PROBES = {
    "exact_duplicate_lines": lambda d: int(d.duplicated().sum()),
    "cancellation_lines": lambda d: int(d["invoice"].str.startswith("C").sum()),
    "lines_without_customer": lambda d: int(d["customer_id"].isna().sum()),
    "nonpositive_price_lines": lambda d: int((d["price"] <= 0).sum()),
    "non_product_stock_codes": lambda d: int((~d["stock_code"].str.match(r"^\d{5}")).sum()),
}
C.SOURCE_CONTRACTS[TABLE] = {1: CONTRACT}
C.ANOMALY_PROBES[TABLE] = PROBES


def export_snapshot(raw: pd.DataFrame, cutoff: str) -> None:
    """What the shop's order system would export at `cutoff` (rows known by end of that day)."""
    out = WS.source_dir(cutoff)
    out.mkdir(parents=True, exist_ok=True)
    end = pd.Timestamp(cutoff) + pd.Timedelta(hours=23, minutes=59, seconds=59)
    df = raw[raw["invoice_ts"] <= end]
    path = out / f"{TABLE}.csv"
    df.to_csv(path, index=False)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    (out / "_export_manifest.json").write_text(json.dumps(
        {"cutoff": cutoff, "tables": {TABLE: {"file": path.name, "rows": int(len(df)), "sha256": sha}}}, indent=1))


def load_raw() -> pd.DataFrame:
    if not DATA.exists():
        sys.exit(f"missing {DATA}: download https://archive.ics.uci.edu/dataset/502/online+retail+ii first")
    d = pd.read_parquet(DATA)
    d = d.rename(columns={"Invoice": "invoice", "StockCode": "stock_code", "Description": "description",
                          "Quantity": "quantity", "InvoiceDate": "invoice_ts", "Price": "price",
                          "Customer ID": "customer_id", "Country": "country"})
    d["customer_id"] = d["customer_id"].astype("Int64").astype("string")
    d["available_at"] = d["invoice_ts"]          # online orders are recorded when placed
    return d


# --------------------------------------------------------------------------- 4. features (point-in-time)
POP = """SELECT DISTINCT customer_id FROM silver.transactions
         WHERE line_type = 'product' AND customer_id IS NOT NULL
           AND invoice_ts >= {as_of} - INTERVAL 365 DAY AND available_at < {as_of}"""
TRUSTED = {
    "recency_days": "SELECT customer_id, date_diff('day', max(invoice_ts), {as_of}) AS v FROM silver.transactions "
                    "WHERE line_type='product' AND available_at < {as_of} GROUP BY 1",
    "orders_365d": "SELECT customer_id, count(DISTINCT invoice) AS v FROM silver.transactions WHERE line_type='product' "
                   "AND invoice_ts >= {as_of} - INTERVAL 365 DAY AND available_at < {as_of} GROUP BY 1",
    "spend_365d": "SELECT customer_id, sum(amount) AS v FROM silver.transactions WHERE line_type IN ('product','cancellation') "
                  "AND invoice_ts >= {as_of} - INTERVAL 365 DAY AND available_at < {as_of} GROUP BY 1",
    "orders_90d": "SELECT customer_id, count(DISTINCT invoice) AS v FROM silver.transactions WHERE line_type='product' "
                  "AND invoice_ts >= {as_of} - INTERVAL 90 DAY AND available_at < {as_of} GROUP BY 1",
    "cancel_lines_365d": "SELECT customer_id, count(*) AS v FROM silver.transactions WHERE line_type='cancellation' "
                         "AND invoice_ts >= {as_of} - INTERVAL 365 DAY AND available_at < {as_of} GROUP BY 1",
    "tenure_days": "SELECT customer_id, date_diff('day', min(invoice_ts), {as_of}) AS v FROM silver.transactions "
                   "WHERE available_at < {as_of} GROUP BY 1",
    "distinct_items_365d": "SELECT customer_id, count(DISTINCT stock_code) AS v FROM silver.transactions WHERE line_type='product' "
                           "AND invoice_ts >= {as_of} - INTERVAL 365 DAY AND available_at < {as_of} GROUP BY 1",
}
# Realistic mistakes: aggregate the whole table instead of 'what was known at as_of'.
LEAKY = {
    "orders_all_time": "SELECT customer_id, count(DISTINCT invoice) AS v FROM silver.transactions "
                       "WHERE line_type='product' GROUP BY 1",
    "last_seen_minus_as_of_days": "SELECT customer_id, date_diff('day', {as_of}, max(invoice_ts)) AS v "
                                  "FROM silver.transactions GROUP BY 1",
}


def feature_frame(con, as_of: str, feats: dict) -> pd.DataFrame:
    lit = f"TIMESTAMP '{as_of} 00:00:00'"
    X = con.execute(POP.format(as_of=lit)).df()
    for name, sql in feats.items():
        f = con.execute(sql.format(as_of=lit)).df().rename(columns={"v": name})
        X = X.merge(f, on="customer_id", how="left")
    return X


def labels(con, as_of: str) -> pd.DataFrame:
    """Evaluator side: did the customer buy again within HORIZON days? Needs a build that covers the window."""
    lit = f"TIMESTAMP '{as_of} 00:00:00'"
    return con.execute(f"""SELECT DISTINCT customer_id, 1 AS y FROM silver.transactions WHERE line_type='product'
        AND invoice_ts >= {lit} AND invoice_ts < {lit} + INTERVAL {HORIZON} DAY""").df()


def time_travel(early_con, late_con, as_of: str, name: str, sql: str) -> dict:
    a = feature_frame(early_con, as_of, {name: sql})
    b = feature_frame(late_con, as_of, {name: sql})
    m = a.merge(b, on="customer_id", suffixes=("_early", "_late"))
    x, y = m[f"{name}_early"].astype(float), m[f"{name}_late"].astype(float)
    diff = ~((x == y) | (x.isna() & y.isna()))
    return {"outcome": "FAIL" if diff.any() else "PASS", "n_compared": int(len(m)), "n_diff": int(diff.sum())}


def boot_auc(y, s, reps=500, seed=0):
    rng = np.random.default_rng(seed)
    y, s = np.asarray(y), np.asarray(s)
    vals = []
    for _ in range(reps):
        i = rng.integers(0, len(y), len(y))
        if y[i].min() != y[i].max():
            vals.append(auc(y[i], s[i]))
    return float(np.quantile(vals, 0.025)), float(np.quantile(vals, 0.975))


# --------------------------------------------------------------------------- main
def main() -> None:
    if WS.root.exists():
        shutil.rmtree(WS.root)
    WS.ensure()
    raw = load_raw()
    log(f"## 0. 数据：UCI Online Retail II，{len(raw):,} 行，{raw.invoice_ts.min():%Y-%m-%d} 至 {raw.invoice_ts.max():%Y-%m-%d}")

    # 1-2 contracts + governed builds
    log("\n## 1–2. 源契约与受治理构建")
    builds = {}
    for cut in (CUT_PIT, CUT_TRAIN, CUT_SCORE):
        export_snapshot(raw, cut)
        rep = build_snapshot(WS, cut, pipeline_dir=EX / "pipeline")
        builds[cut] = rep.build_id
        ct = rep.contracts[0]
        log(f"- 快照 {cut}：{rep.status}；契约 {'PASS' if ct['ok'] else 'FAIL'}，{ct['n_rows']:,} 行；异常计数 {ct['anomalies']}")
        tests = {t['name']: t['outcome'] for t in rep.tests}
        log(f"  数据测试：{sum(v == 'PASS' for v in tests.values())}/{len(tests)} PASS；守恒对账 {tests.get('reconcile:classification_conserves_amount')}")
        if rep.status != "READY":
            sys.exit(f"build {cut} not READY: {rep.reasons}")

    con_pit, con_train, con_score = (open_readonly(warehouse_path(WS, builds[c])) for c in (CUT_PIT, CUT_TRAIN, CUT_SCORE))
    mix = con_score.execute("""SELECT line_type, count(*) n, round(sum(amount)) gbp FROM silver.transactions
                               GROUP BY 1 ORDER BY gbp DESC""").df()
    log("\n  治理层把每一行归类（没有丢弃任何一行；金额用精确 DECIMAL 计算——最初用浮点数时，守恒对账在 2011-05-31 快照上差了 £0.01 而失败）：")
    log("  " + mix.to_string(index=False).replace("\n", "\n  "))

    # 3. governed metric vs naive answers
    log("\n## 3. 问数：'2011 年 3 月按市场（UK / 非 UK）的净商品销售额是多少？'")
    model = load_semantic_model(EX / "semantic_model.yaml")
    gov = query_metric(con_train, model, "net_product_sales", ["market"], ("2011-03", "2011-03"))
    rows_gov = gov.to_dict("records")
    naive_sql = {
        "naive_raw_sum（原始导出直接 SUM(数量×单价)）":
            "SELECT '2011-03' AS month, CASE WHEN country='United Kingdom' THEN 'UK' ELSE 'non-UK' END AS market, "
            "SUM(quantity*price) AS value FROM bronze_safe.retail_transactions WHERE strftime(invoice_ts,'%Y-%m')='2011-03' GROUP BY 2",
        "naive_no_dedup（分类正确但不去重）":
            "SELECT '2011-03' AS month, market, SUM(quantity*price) AS value FROM ("
            "SELECT s.*, CASE WHEN country='United Kingdom' THEN 'UK' ELSE 'non-UK' END AS market FROM staging.stg_transactions s) "
            "WHERE strftime(invoice_ts,'%Y-%m')='2011-03' AND regexp_matches(stock_code,'^[0-9]{5}') "
            "AND invoice NOT LIKE 'A%' AND (invoice LIKE 'C%' OR (price>0 AND quantity>0)) GROUP BY 2",
        "naive_ignore_cancellations（忽略退货）":
            "SELECT month, market, SUM(amount) AS value FROM silver.transactions WHERE month='2011-03' "
            "AND line_type='product' GROUP BY 1,2",
    }
    answers = {"careful（受治理指标 + 声明主张）": rows_gov}
    for k, sql in naive_sql.items():
        answers[k] = con_train.execute(sql).df().to_dict("records")
    qa = []
    for k, rows in answers.items():
        r = reconcile_answer_to_metric(con_train, model, rows, "net_product_sales", ["market"], ("2011-03", "2011-03"),
                                       rel_tol=1e-9, abs_tol=0.01)
        total = sum(float(x["value"]) for x in rows)
        tier = "VERIFIED → 标签 T1（没有独立账本，无法到 T0）" if r["outcome"] == "PASS" else "FLAGGED → 拒绝交付"
        qa.append((k, total, r["outcome"], tier))
        log(f"- {k}：合计 £{total:,.2f} → 对账 {r['outcome']} → {tier}")

    gap = con_score.execute("""
        WITH raw AS (SELECT SUM(quantity*price) v FROM bronze_safe.retail_transactions WHERE invoice_ts >= '2010-01-01' AND invoice_ts < '2011-01-01'),
             nodup AS (SELECT SUM(quantity*price) v FROM (SELECT DISTINCT * FROM staging.stg_transactions) WHERE invoice_ts >= '2010-01-01' AND invoice_ts < '2011-01-01'),
             cls AS (SELECT line_type, SUM(amount) v FROM silver.transactions WHERE month LIKE '2010-%' GROUP BY 1)
        SELECT (SELECT v FROM raw) raw_total, (SELECT v FROM raw)-(SELECT v FROM nodup) duplicates,
               (SELECT v FROM cls WHERE line_type='product') product, (SELECT v FROM cls WHERE line_type='cancellation') cancellations,
               (SELECT sum(v) FROM cls WHERE line_type NOT IN ('product','cancellation')) non_product""").df().iloc[0]
    net = gap["product"] + gap["cancellations"]
    log(f"\n  2010 全年拆解：原始 SUM £{gap['raw_total']:,.0f} → 其中重复行 £{gap['duplicates']:,.0f}、"
        f"退货 £{gap['cancellations']:,.0f}、非商品行（运费/手续费/调整/坏账等）£{gap['non_product']:,.0f}；"
        f"受治理净商品销售额 £{net:,.0f}（原始 SUM 高估 {(gap['raw_total'] / net - 1) * 100:.1f}%）")

    # 4. time-travel leakage on real history
    log(f"\n## 4. 特征泄漏：时间穿越检查（as_of 2011-03-01：{CUT_PIT} 快照 vs {CUT_TRAIN} 快照）")
    leak = {}
    for name, sql in {**TRUSTED, **LEAKY}.items():
        r = time_travel(con_pit, con_train, "2011-03-01", name, sql)
        leak[name] = r
        log(f"- {name:28s} {'可信' if name in TRUSTED else '朴素'}：{r['outcome']}（比较 {r['n_compared']} 个客户，{r['n_diff']} 个不一致）")

    # 5. model: dev CV (train build only) vs sealed holdout (labels only in the later snapshot)
    log(f"\n## 5. 回购预测：未来 {HORIZON} 天是否再次购买")
    def train_frame(feats, con_feat):
        parts = []
        for a in TRAIN_AS_OF:
            X = feature_frame(con_feat, a, feats)
            y = labels(con_train, a)
            d = X.merge(y, on="customer_id", how="left").fillna({"y": 0})
            d["as_of"] = a
            parts.append(d)
        return pd.concat(parts, ignore_index=True)

    results = {}
    for cand, feats, con_feat in [("careful_logreg", TRUSTED, con_train), ("careful_hgb", TRUSTED, con_train),
                                  ("naive_leaky_hgb", {**TRUSTED, **LEAKY}, con_score)]:
        D = train_frame(feats, con_feat)
        cols = list(feats)
        def make():
            if cand.endswith("logreg"):
                return make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), LogisticRegression(max_iter=2000, C=0.5))
            return HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05, random_state=0)
        cv = []
        for k in (3, 2, 1):            # expanding-window time CV: validate on the last three as_of months
            va = TRAIN_AS_OF[-k]
            tr = D[D.as_of < va]
            te = D[D.as_of == va]
            mdl = make().fit(tr[cols], tr.y)
            cv.append(auc(te.y, mdl.predict_proba(te[cols])[:, 1]))
        mdl = make().fit(D[cols], D.y)
        # Sealed: features as they exist at serving time (train build, cutoff before as_of);
        # labels from the later snapshot only the evaluator holds.
        Xs = feature_frame(con_train, SEALED_AS_OF, feats)
        ys = Xs[["customer_id"]].merge(labels(con_score, SEALED_AS_OF), on="customer_id", how="left").fillna({"y": 0})
        s = mdl.predict_proba(Xs[cols])[:, 1]
        a = auc(ys.y, s)
        lo, hi = boot_auc(ys.y.values, s)
        results[cand] = {"dev_cv_auc": float(np.mean(cv)), "sealed_auc": a, "ci": (lo, hi), "n": len(ys), "base_rate": float(ys.y.mean())}
        log(f"- {cand}：开发期 CV AUC {np.mean(cv):.3f} → 封存 AUC {a:.3f} [{lo:.3f}, {hi:.3f}]，n={len(ys)}，基准回购率 {ys.y.mean():.1%}")
    base = results["careful_logreg"]
    log("  （封存集：2011-06-01 时点的活跃客户，标签来自 2011-08-31 快照——训练时这些数据还不存在）")

    # 6. evidence + gate
    log("\n## 6. 证据与门禁（复用 harness 的 gate 与 gates.yaml）")
    store = EvidenceStore(WS)
    for cand, used in [("careful_logreg", list(TRUSTED)), ("naive_leaky_hgb", list(TRUSTED) + list(LEAKY))]:
        subject = {"kind": "candidate", "candidate_id": cand, "build_id": builds[CUT_TRAIN], "dataset": "uci-online-retail-ii"}
        r = results[cand]
        store.record(check_id="model.sealed_eval", check_version="1", stage="model_shadow", subject=subject,
                     outcome="PASS" if r["ci"][0] > 0.5 else "FAIL", producer="evaluator:custody",
                     metrics={"auc": r["sealed_auc"], "auc_ci95": list(r["ci"]), "n": r["n"]})
        bad = [f for f in used if leak[f]["outcome"] != "PASS"]
        store.record(check_id="model.leakage", check_version="1", stage="model_shadow", subject=subject,
                     outcome="FAIL" if bad else "PASS", producer="verifier:leakage", metrics={"failed_features": bad})
        d = decide(WS, "model_shadow", subject, requested_by="agent:retail-ds")
        log(f"- {cand}：model_shadow → **{d.verdict}** {list(d.reasons) or ''}")

    # 7. what cannot be claimed
    log("\n## 7. 在这份数据上无法验证的（harness 会如实拒绝）")
    log("- 没有独立账本（财务关账/支付结算）：问数最高只到 T1，不能给 T0。")
    log("- 去重是 owner 决策：数据本身无法证明完全相同的行是导出重复还是真实重复购买；这一口径写在指标 caveats 里，影响额见第 3 节。")
    log("- 没有随机化的干预记录：模型只能证明'预测得准'（V4），不能声称任何营销动作的业务价值（V5）；要谈价值必须先做 G5 实验。")

    report = REPO / "docs/examples/ONLINE_RETAIL_E2E.zh-CN.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    header = ("# 真实数据端到端测试：UCI Online Retail II\n\n"
              "由 `examples/online_retail/run_e2e.py` 自动生成。数据来源：UCI Machine Learning Repository，"
              "Online Retail II（Chen, 2012），CC BY 4.0；本地文件 SHA-256 记录在 `workspace/external/online_retail_ii/`。\n")
    report.write_text(header + "\n".join(LOG) + "\n")
    (WS.runs / "summary.json").write_text(json.dumps({"qa": qa, "leakage": leak, "models": results}, indent=1, default=str))
    print(f"\nreport: {report}")


if __name__ == "__main__":
    main()
