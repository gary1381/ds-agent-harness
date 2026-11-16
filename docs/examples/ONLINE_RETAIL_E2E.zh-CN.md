# 真实数据端到端测试：UCI Online Retail II

由 `examples/online_retail/run_e2e.py` 自动生成。数据来源：UCI Machine Learning Repository，Online Retail II（Chen, 2012），CC BY 4.0；本地文件 SHA-256 记录在 `workspace/external/online_retail_ii/`。
## 0. 数据：UCI Online Retail II，1,067,371 行，2009-12-01 至 2011-12-09

## 1–2. 源契约与受治理构建
- 快照 2011-02-28：READY；契约 PASS，630,796 行；异常计数 {'exact_duplicate_lines': 30042, 'cancellation_lines': 12110, 'lines_without_customer': 144137, 'nonpositive_price_lines': 4230, 'non_product_stock_codes': 3685}
  数据测试：5/5 PASS；守恒对账 PASS
- 快照 2011-05-31：READY；契约 PASS，734,490 行；异常计数 {'exact_duplicate_lines': 30814, 'cancellation_lines': 13989, 'lines_without_customer': 167903, 'nonpositive_price_lines': 4982, 'non_product_stock_codes': 4371}
  数据测试：5/5 PASS；守恒对账 PASS
- 快照 2011-08-31：READY；契约 PASS，846,166 行；异常计数 {'exact_duplicate_lines': 31550, 'cancellation_lines': 16053, 'lines_without_customer': 196579, 'nonpositive_price_lines': 5489, 'non_product_stock_codes': 5089}
  数据测试：5/5 PASS；守恒对账 PASS

  治理层把每一行归类（没有丢弃任何一行；金额用精确 DECIMAL 计算——最初用浮点数时，守恒对账在 2011-05-31 快照上差了 £0.01 而失败）：
         line_type      n        gbp
           product 789737 15444467.0
               fee   3198   130120.0
           voucher     98     1637.0
  other_nonproduct    174     1357.0
  zero_or_negative   5220        0.0
        adjustment   1511  -239127.0
      cancellation  14678  -458750.0

## 3. 问数：'2011 年 3 月按市场（UK / 非 UK）的净商品销售额是多少？'
- careful（受治理指标 + 声明主张）：合计 £679,204.00 → 对账 PASS → VERIFIED → 标签 T1（没有独立账本，无法到 T0）
- naive_raw_sum（原始导出直接 SUM(数量×单价)）：合计 £683,267.08 → 对账 FAIL → FLAGGED → 拒绝交付
- naive_no_dedup（分类正确但不去重）：合计 £680,457.29 → 对账 FAIL → FLAGGED → 拒绝交付
- naive_ignore_cancellations（忽略退货）：合计 £689,708.49 → 对账 FAIL → FLAGGED → 拒绝交付

  2010 全年拆解：原始 SUM £9,488,595 → 其中重复行 £409,011、退货 £-238,515、非商品行（运费/手续费/调整/坏账等）£-55,607；受治理净商品销售额 £9,135,191（原始 SUM 高估 3.9%）

## 4. 特征泄漏：时间穿越检查（as_of 2011-03-01：2011-02-28 快照 vs 2011-05-31 快照）
- recency_days                 可信：PASS（比较 4260 个客户，0 个不一致）
- orders_365d                  可信：PASS（比较 4260 个客户，0 个不一致）
- spend_365d                   可信：PASS（比较 4260 个客户，0 个不一致）
- orders_90d                   可信：PASS（比较 4260 个客户，0 个不一致）
- cancel_lines_365d            可信：PASS（比较 4260 个客户，0 个不一致）
- tenure_days                  可信：PASS（比较 4260 个客户，0 个不一致）
- distinct_items_365d          可信：PASS（比较 4260 个客户，0 个不一致）
- orders_all_time              朴素：FAIL（比较 4260 个客户，1581 个不一致）
- last_seen_minus_as_of_days   朴素：FAIL（比较 4260 个客户，1617 个不一致）

## 5. 回购预测：未来 90 天是否再次购买
- careful_logreg：开发期 CV AUC 0.773 → 封存 AUC 0.792 [0.777, 0.807]，n=4324，基准回购率 36.6%
- careful_hgb：开发期 CV AUC 0.772 → 封存 AUC 0.786 [0.771, 0.801]，n=4324，基准回购率 36.6%
- naive_leaky_hgb：开发期 CV AUC 0.984 → 封存 AUC 0.604 [0.587, 0.622]，n=4324，基准回购率 36.6%
  （封存集：2011-06-01 时点的活跃客户，标签来自 2011-08-31 快照——训练时这些数据还不存在）

## 6. 证据与门禁（复用 harness 的 gate 与 gates.yaml）
- careful_logreg：model_shadow → **ALLOW** 
- naive_leaky_hgb：model_shadow → **REJECT** ['model.leakage:fail — ev-c7b8ac0d73c9ad7fd1a9ef13 by verifier:leakage at 2026-10-20T01:03:37+00:00; a FAIL stands until an authorised record explicitly supersedes it (a fixed artefact is a new subject)']

## 7. 在这份数据上无法验证的（harness 会如实拒绝）
- 没有独立账本（财务关账/支付结算）：问数最高只到 T1，不能给 T0。
- 去重是 owner 决策：数据本身无法证明完全相同的行是导出重复还是真实重复购买；这一口径写在指标 caveats 里，影响额见第 3 节。
- 没有随机化的干预记录：模型只能证明'预测得准'（V4），不能声称任何营销动作的业务价值（V5）；要谈价值必须先做 G5 实验。
