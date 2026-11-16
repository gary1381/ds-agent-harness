# DS Harness 工程方案 v0.2（价值优先视角）

日期：2026-10-19。证据全部来自 `dsharness/docs/_research_digest_2026-10-19.txt`，正文用 [键] 引用，键表在文末。标"假设"的数字没有证据支撑，试点时要用价值账本的实测值替换。

---

## A. 核心论断

1. **系统定义**：DS Harness 是企业数据工作的"可验证交付层"，包含四样东西：上下文资产（语义层、认证表、已验证查询、Skills）、执行期校验与门禁、评测基准、价值账本。Agent loop、仓库、调度器、BI、模型注册表一律购买或复用现成的。自建代码控制在约 8k 行 Python 以内，维护投入 1.5–3 FTE（假设）。
2. **先做什么**：先交付两类高频、可以对账、可以计量的工作：①治理指标问数/取数；②数据管道的 break-fix 与变更。ML 放到第二阶段，以"决策 + 随机实验"的形式交付，不以 AUC 作为交付物。
3. **瓶颈在语义**：准确率的瓶颈是语义内容，不是模型或编排。证据：curated semantics 带来 +8~25pp [dbt-SL]；DABstep Hard 从 13.9% 升到 55.1% [DABstep-abl]；Meta 换更强的模型后反而停在"自信的错误答案"上 [Meta]。因此首期约 70% 的投入放在上下文和金集上（假设）。
4. **头号风险是静默错误**：约 70% 的失败表现为"看起来正常的错误结果" [RADAR]。核心能力是可执行的验证加拒答，不靠自我反思，也不靠多 agent 辩论（辩论的收益约等于投票 [Debate]）。
5. **拓扑**：单一写者，加只读子 agent，加确定性工作流脚本 [Cognition]。工具不超过 13 个 [OpenAI-DA]，通过 MCP 2026-07-28 暴露。流程封装为 Agent Skills。不做角色扮演式的"AI 数据团队"。
6. **价值只按三种可审计口径入账**：①已验证正确的任务 × 净节省工时 × 兑现系数；②被替代的外部支出；③随机实验证明的决策增量。裁员、DAU、自报的"省了几小时"都不算 ROI [MIT-NANDA][Gartner26]。
7. **中国与海外是两套经济学**：中国分析和工程人员的成本约为美国的 1/3–1/2 [NBS][BLS]。在中国只靠省工时，常常要 12 个月以上才能回本。中国方案的价值要靠规模、决策速度、外包替代和收入类 KPI。
8. **立项与自治的规则**：每个用例在 90 天内必须有书面 baseline 和 kill 标准。自治度按风险分级（L0–L4），不做逐动作审批。逐动作审批的部门成功率 16%，充分放权的是 31% [Wharton26]。
9. **它不是什么**：不是通用 NL2SQL 聊天机器人，不是 notebook copilot，也不是 AutoML 平台。不自研 agent runtime、向量库、语义格式或 HMAC 证据账本，不强制引入新调度器。在信贷、核保、雇佣等高风险决策中不做最终决定者。
10. **草稿怎么处理**：
    - 保留：三态门禁（执行/检查/准入分开）、泄漏与标签成熟规则、G5/G6 分离、按身份隔离 holdout。
    - 删除：17 表 PG ledger、13 端点 FastAPI、HMAC、强制 Dagster、"Agent Adapter 放到 PR8"、以流失风险排序为首个纵向场景。
    - 修复 gate.py 三处缺陷：先认证再计数；加 supersede/重试语义；不可验证的输入判 HOLD 并告警，只有经过认证的 FAIL 才判 REJECT [Review]。

---

## B. 价值与经济模型

### B1 用例组合

按可计量价值 × 可行性 × 可验证性排序：

| # | 用例 | 价值杠杆 | 主 KPI | 真值来源 | 现实准确率（证据） | 买/建 | 首次见效 | 级别 |
|---|---|---|---|---|---|---|---|---|
| U1 | 治理指标问数/取数（业务、分析师） | 工单自助化、决策延迟 | 自助解决率、机械工单占比、金集正确率 | 语义定义 + 财务关账对账 | 建模过的 schema 80–90%+；裸仓库 10–30% [BEAVER] | 买 loop，建语义/金集/MCP | 6–10 周 | P0 |
| U2 | 管道故障分诊与 break-fix | MTTR、数据停机、工程工时 | 正确分诊率、PR 接受率、逃逸缺陷数 | CI 测试与对账 | 转换修复 23–33% [ELT-V]；数据层问题需查数据 [ADE] | 买编码 agent，建 skills、checks、门禁 | 8–12 周 | P0 |
| U3 | dbt/SQL 变更与迁移、元数据 enrichment | 工程吞吐 | 每变更工时、CI 一次通过率、返工率 | CI | EL 约 96% [ELT-V]；OpenAI 迁移约 1 万个 DAG [Cases] | 同 U2 | 3–6 月 | P1 |
| U4 | A/B 实验读数 | 读数周期、错判 | 读数天数；SRM 漏检 = 0 | 确定性统计 | 统计由代码计算，agent 只写叙述 | 建 skill | 3–6 月 | P1 |
| U5 | 外包报表/数据运营/标注替代 | 现金 | 已终止合同金额 | 合同 | — | 视情况 | 3–9 月 | P1（有外包时） |
| U6 | 决策模型工厂（留存 uplift、需求预测、定价） | 收入/毛利 | 实验 ITT 增量毛利 | V4 holdout + V5 实验 | Kaggle 型任务 60–64% [MLE] | 建流程和门禁，算法复用 | 6–12 月 | P2 |
| — | 首年不做 | 高风险自动决策；无真值的"洞察生成器"；开放网页抓取 | | | | | | |

### B2 价值公式

```
月净价值 NV = Σu [V_time + V_ext + V_dec − C_err]
            − [C_llm + C_platform + C_compute + C_review + C_context + C_build摊销]

V_time = N·d·q·Δt·w·κ
  N   请求数
  d   覆盖率
  q   审计正确率
  Δt  净节省时间（已扣除核对时间）
  w   全成本时薪
  κ   兑现系数：默认 0.5（假设），按 backlog/吞吐实测修正

C_err = N·d·(1−q)·s·L
  s   静默比例
  L   错误决策的损失
  对外数字、财报、KPI 的 L 极高，所以这类输出必须走 V3 及以上（见 E2）

V_dec = 实验 ITT 增量 × 毛利，取 95% CI 下界；只在 G6 证据存在时计入
```

### B3 单位成本

价格采用 2026-10 牌价 [Price]：Sonnet 5.5 为 $2/$10，缓存读按 0.05× 计；deepseek-flash 闲时为 $0.15/$0.60。

| 任务 | token（假设） | 前沿中档 | 境内 flash | 人工核对 |
|---|---|---|---|---|
| 一次问数 | 60k in / 3k out，缓存 70%（参照 Vercel d0 约 61k [Vercel]） | ≈$0.07–0.09（含 4.7+ tokenizer 约 +30% 膨胀） | ≈$0.01 | 证据卡一瞥，约 1 分钟 |
| 一次 break-fix | 1.5M in / 50k out | ≈$1.5（Opus 5.5 ≈$3） | ≈$0.3 | 15 分钟 ≈ $24 / ¥42 |
| 一个模型候选 | 5–20M | $10–40 + 计算 | — | 数小时 |

结论：在这些用例里，token 约占 TCO 的 10–15%（假设，以账本实测为准）。主要成本是上下文维护和人工核对，所以设计目标是压低"每任务人工核对分钟数"。多 agent 扇出会把 token 放大约 15 倍 [MultiAgent]，默认关闭。

平台计费 [Platforms]：
- Snowflake AI Credit：$2.00，区域驻留 $2.20。
- Genie：用户使用免费到 2027-01-31，但 service principal 现在就计费。
- Google data agents：约 $3/$20 每百万 data token。
- DataWorks：¥0.05/credit。

预算一律按促销结束后的价格做。

### B4 盈亏平衡（U1，全部为假设）

参数：d = 0.5，q ≥ 0.9，Δt = 35 分钟，κ = 0.5。时薪：中国 ¥170/h（NBS IT 非私营 ¥248,752 × 1.35 ÷ 2,000h）；美国 $95/h（$120,230 ÷ 0.70 ÷ 1,800h）。

| | 中国 | 美国 |
|---|---|---|
| 每请求价值 | ≈¥24.8 | ≈$13.9 |
| 每请求变动成本 | ≈¥1.5 | ≈$0.3 |
| 月固定成本（steward 0.5 FTE + eval 0.2 FTE + 2 人 × 3 月建设，按 12 月摊销） | ≈¥34k | ≈$17.5k |
| 盈亏平衡点 N\* | ≈1,460 个可治理请求/月 | ≈1,290 个可治理请求/月 |

结论：可治理请求每月少于约 1,500 个的组织，不应自建 U1。这类组织直接用平台原生 agent，只自建金集；或者让 U1 和 U2 共担固定成本。

### B5 中国与海外的差异

| 维度 | 中国 | 海外 |
|---|---|---|
| 价值主轴 | 规模、速度、收入 KPI、外包替代 | 工时与吞吐也能成立 |
| 平台 | DataWorks Data Agent（qwen3.7-max 默认，五档审批模式）、火山 Data Agent、腾讯 DataBuddy | CoWork/CoCo、Genie Code/One、BigQuery 系列 agent、Fabric |
| 模型 | 个人信息只用境内模型；2026 年境内价格上涨，DeepSeek 有峰时价 [Price] | 区域端点 + 零留存（ZDR），不能只依赖 DPF |
| 入口 | 飞书、钉钉、企微 | Slack、Teams |
| 监管 | 内部工具无需备案；导出内容需标识；银保适用 NFRA 8 号文；出境门槛 10 万/100 万/1 万敏感 [CN-Reg] | EU Art.50 已生效；Annex III 推迟至 2027-12-02；US SR 26-2 不覆盖 agentic AI [Reg] |
| 需求侧 | "AI+"政策目标 2027 年渗透率 70%：有预算，也有 agent-washing 风险 | 2026 年 EBIT 影响持平（37%） [McK26] |

### B6 Kill 标准

| 节点 | 条件 | 动作 |
|---|---|---|
| D+14 | 拿不到 baseline（没有工单日志或工时数据） | 不立项 |
| D+45 | U1 金集正确率 < 75% 且越界拒答 < 80% | 缩域到 ≤ 20 个指标，或停止 |
| D+45 | U2 正确分诊 < 40% | 停止 U2 |
| D+90 | 出现以下任一：自助解决率 < 30%；试点人群 WAU < 30%；每正确答案全成本 > 人工的 30%；发生 1 起门禁未拦截、由 agent 导致的对外数字错误 | kill 或降回 L1 |
| M+6 | 公司级 NV（保守口径）≤ 0，或 steward 岗位空缺超过 60 天 | kill 该域 |
| U6 | 6 个月内拿不到随机化授权 | 不立项 |
| U6 | 实验 CI 下界 ≤ 0 | 不扩量 |

---

## C. 端到端架构

### C1 分层与数据流

```
采集        → 处理/存储       → 语义&上下文        → 特征/训练        → 验证/评测         → 交付                  → 价值&监控
dlt/Fivetran  DuckDB|仓库       MetricFlow/OSI       PIT 特征 SQL      checks 库          答案卡(IM/BI)           价值账本(仓库表)
DataWorks集成 dbt/SQLMesh       认证表/verified SQL  sklearn/LightGBM  金集+回归 eval     PR→CI→受保护环境        MLflow trace/OTel
Pandera 契约  现有调度器        Skills/AGENTS.md     MLflow registry   gate(CI 内)        模型(registry+批量表)   对账/漂移/成本告警
                                夜间 enrichment                        封存 holdout        决策记录+实验(G5/G6)
        └──────── dsh-mcp（agent 唯一数据入口，按用户身份透传 RLS）+ Skills ────────┘
Agent：平台原生(CoWork/Genie/DataWorks/BigQuery CA) | 编码 agent(Claude Code/Codex/Qwen Code) | CI 无头(claude -p --bare / Agents SDK)
```

### C2 买/复用与自建

| 层 | SMB / local-first | 企业（已有仓库） | 自建部分 |
|---|---|---|---|
| 采集 | dlt 1.31 | Fivetran / DataWorks 集成 / 现有 CDC | 源契约（Pandera 0.34）；agent 生成连接器时套用的测试模板 |
| 存储 | DuckDB 1.5.x（钉住版本，不上 2.0）+ Parquet/DuckLake 1.0 | 现有仓库；跨引擎用 Iceberg 1.12 | 无 |
| 转换 | dbt-core 1.12/1.13 或 SQLMesh 0.236 | 现有 dbt（内部用 Fusion 可以；若对外 SaaS 则用 dbt-oss） | 对账模型、data-diff CI |
| 调度 | cron / Prefect 3.8 / Dagster 1.13 任选 | 保留 Airflow 3.3 / DolphinScheduler / DataWorks | 无 |
| 语义 | MetricFlow 0.213 或 Cube 1.8 | 平台语义层 + OSI 便携副本 | 定义摘要（digest）、一致性检查 |
| 目录/血缘 | git 上下文仓库 | DataHub / Unity / Knowledge Catalog + OpenLineage 1.54 | enrichment 作业 |
| 训练 | sklearn/LightGBM/Optuna + MLflow 3.17 | 平台 ML 或 MLflow | PIT 特征模板、泄漏检查 |
| 评测 | pytest/Hypothesis + Inspect AI 0.3.x 或 MLflow GenAI eval | 同上；平台自带 eval 只作第二意见 | check 库、金集、eval runner、gate |
| 发布 | GitHub Actions + environments | GitHub/GitLab 受保护环境 + in-toto 证明（actions/attest；国内自建 GitLab 用 cosign + KMS） | release manifest |
| 隔离 | sandbox-runtime / Docker `network=none` | agent-sandbox 1.0.7（gVisor/Kata）或 E2B 自托管 | 无 |
| 观测 | MLflow tracing | MLflow 或自托管 Langfuse + OTel | 成本归集 |
| 网关 | — | agentgateway 1.6 / MLflow AI Gateway / Cortex AI Gateway | 路由策略 |

许可证门禁：SBOM 中出现 ELv2（Soda v4）、BSL（lakeFS 1.87+）、PLA、AGPL 时一律标记 [OSS]。避免使用：Vanna（已归档）、Daytona OSS（已归档）、smolagents/AutoGen（停滞）、Marquez（停滞）。

### C3 自建清单（全部只为 KPI 服务）

| 自建模块 | 对应 KPI |
|---|---|
| dsh-mcp | 正确率、权限合规 |
| checks 库 | 静默错误率 |
| 金集与 eval runner | 正确率、回归 |
| gate 步骤 | 逃逸缺陷数 |
| skills 包 | PR 接受率 |
| enrichment 作业 | 表检索命中率（Pinterest：文档化使命中率从 40% 升到 90% [Cases]） |
| 价值账本与 FinOps | NV、每正确任务成本 |

---

## D. Agent 设计

### D1 拓扑（按工作流）

| 工作流 | 拓扑 | 写者 | 起步 → 目标自治 |
|---|---|---|---|
| U1 问数 | 单 agent，≤ 12 个工具，不用子 agent | 无写操作 | 认证指标走 L3（5% 抽检）；探索性 SQL 走 L1 并打标签 |
| U2 break-fix | 构建失败触发 → 只读分诊子 agent（日志、契约 diff、血缘）→ 单写者在分支写 PR → CI → 干净上下文的 reviewer agent [Cognition] → 人工合并 | 1 个 | L2 → 白名单修复类（字段改名、schema 映射）达到 L3 |
| U3 变更 | 同 U2 | 1 个 | L2 |
| U5' enrichment | 夜间确定性扇出，只读子 agent 产出表卡片 → steward 审 PR | 脚本 | L2 |
| U4 读数 | Skill + 确定性统计（SRM、CUPED） | 无 | L3 |
| U6 工厂 | 工作流脚本按固定步骤调用 agent（schema 校验输出）；builder 看不到 holdout；scorer 为确定性代码；另设怀疑型评估 agent | 1 个 | L2；上线永远由人决定 |

### D2 工具（dsh-mcp）

实现：FastMCP 4.1 / mcp SDK 2.3，协议版本 2026-07-28（无状态，`tools/list` 顺序确定）。

- 只读工具：
  - `catalog_search`、`table_card`、`metrics_list`
  - `metric_query`：返回结果行、编译后的 SQL、`metric_definition_digest`、`snapshot_id`
  - `sql_readonly`：在沙箱中执行，限制行数和字节；PI 表只返回聚合（k ≥ 10，假设）
  - `verified_query_search`、`build_status`、`lineage_impact`
  - `check_run`：只能运行已注册的检查
  - `evidence_get`
- 写工具（标注 destructive，只能写分支）：
  - `patch_propose`
  - `eval_run`：长任务，走 Tasks 扩展
- 不提供任何能读 custody/holdout 的工具。

认证：OAuth 2.1，token 受众绑定（RFC 8707）。有 Okta 时用 EMA。国内 IdP（钉钉、飞书）对 EMA 的支持未经证实，需要时由网关做 token exchange。规则在工具层强制执行：未受治理的 agent 在 DABstep 消融中执行了 166 条变更语句 [DABstep-abl]。

### D3 Skills 与 AGENTS.md

Skills 由 domain owner 维护，各自有版本，并必须通过 eval：
- `revenue-reconciliation`
- `billing-schema-break-fix`
- `pit-feature-engineering`
- `leakage-audit`
- `uplift-experiment-design`
- `ab-readout`
- `model-card-pipia-draft`

仓库约定写在 AGENTS.md 中，Claude Code、Codex、Gemini CLI、Qwen/DataWorks 都能读取 [Tooling]。

### D4 上下文层（按可信度排序）

1. 语义定义
2. 认证表
3. verified queries（看板查询和金集排在临时查询之前；不要把全部查询日志都建索引 [OpenAI-DA]）
4. 夜间 enrichment 生成的表卡片（粒度、键、新鲜度、注意事项）
5. 业务规则术语表
6. 个人记忆：用户纠正先变成待审条目
7. 个性化：默认优先用户近 90 天用过的表（Meta 的数据：88% 的查询属于这类）

规则：用于评测的 verified query 不进入运行时上下文 [Snowflake-eval]。

### D5 模型路由与数据驻留

默认只把元数据和聚合结果放进 prompt。代码在数据旁边的沙箱里执行，原始行不出沙箱。PI 的检测和假名化在 MCP 层完成，不在 prompt 里做。

| 数据级别 | 中国境内数据 | EU 数据 | 美国/其他 |
|---|---|---|---|
| 元数据/schema/指标定义 | 已批准模型均可 | EU 区域端点优先 | 任意 |
| 无 PI 的业务聚合 | 境内优先；涉及"重要数据"的禁止出境 | EU 端点 + ZDR | 区域端点 |
| 个人信息（PI） | 只用境内自托管开源权重（Qwen/DeepSeek/GLM）或已备案的境内 API | EU + ZDR，不只依赖 DPF | ZDR |
| 敏感 PI / 金融客户数据 | 只用自托管；禁止用于训练或微调（NFRA） | 只用自托管/专属 | 专属 |

分层用模型：
- 廉价层（Haiku 5.5、deepseek-flash、GLM-5.3-Flash）负责抽取、分类、SQL 草稿，目标承担 ≥ 60% 的调用（假设）。
- 前沿层负责规划和 DE 编码。

供应商：至少 3 家，其中 1 家境内、1 家可自托管，并设重新定价告警（2026 年发生过 DeepSeek 涨价、约 12 小时宕机 [Price]）。注意：Claude Managed Agents 和 programmatic tool calling 不适用 ZDR，处理 PI 时要用自有 loop。

### D6 Harness 可替换

可替换的契约由四部分组成：MCP 工具、Skills、AGENTS.md、eval 套件。任何 harness 都必须先通过同一套"一致性回归"才能获准使用，包括：

- `claude -p --bare --max-budget-usd`
- `codex exec`
- Qwen Code / DataWorks
- Agents SDK 0.24 / ADK 2.12

所有版本精确钉住。每次模型或 harness 升级，都视为一次需要重跑 eval 的发布。

### D7 自治分级

| 级别 | 含义 |
|---|---|
| L0 | 只建议 |
| L1 | 只读执行 |
| L2 | 写分支、临时 schema、开 PR |
| L3 | 有条件发布：答案卡自动发布；non-prod 自动合并；抽样审计 |
| L4 | 生产中的有界可逆动作：配额内重跑/回填、自动回滚 |

模型上线和高风险决策永远不进入 L4。DataWorks 的五档审批模式可以直接映射到这五级。

升降级规则（假设）：
- 升一级：某类任务在生产中连续 ≥ 50 次零逃逸，且 reviewer 推翻率 < 5%，且回归 pass^3 ≥ 90%。
- 降一级：出现任何一次逃逸，自动降级。

---

## E. 可信与验证

### E1 缺少真值时怎么办（按可验证性分类）

1. **有独立记录**（财务关账、支付渠道流水、源系统计数）：做对账。这是最便宜的强证据。每个认证指标都必须"可对账"。
2. **只有定义**：语义层就是"含义的真值"。正确的标准是：符合定义，并通过 V2 不变量检查。
3. **真值延迟到来**：登记标签成熟期，未成熟的标签记为 unknown，不当作负例。
4. **因果问题**：只认随机实验。准实验的结论标为 ASSUMPTION-DEPENDENT。
5. **完全没有真值**：做变形测试（改写不变性、过滤单调性、分区可加性）和敏感性分析，标为 EXPLORATORY，不得流入 KPI。
6. **金标本身也要审计**：公开基准的答案键错误率在 30–60% [Bench]。用 LLM 审计员加人工审核清洗金标。

### E2 验证阶梯

| 级别 | 内容 |
|---|---|
| V0 | 能执行。不算证据 |
| V1 | 契约、类型、唯一性 |
| V2 | 不变量：join 基数、守恒、去重身份、PIT（available_at ≤ cutoff）、价格簿生效期 |
| V3 | 独立路径：对账，或第二路径重算（语义层编译的 SQL 对比 agent SQL） |
| V4 | 封存 holdout / 时间外推回测，由独立 scorer 执行 |
| V5 | 随机实验：预注册、ITT、SRM |

各类输出的最低要求：

| 输出 | 最低级别 |
|---|---|
| 对外/KPI 数字 | V3 |
| 探索性答案 | V2 + 标签 |
| 管道变更 | V1–V3（含 data-diff） |
| 模型 | V4 |
| 价值声明 | V5 |

### E3 评测

- 每个工作流从 50 题起步，6 个月内扩到 200–500 题。题目取自真实日志，每题 2–4 个改写，允许多个可接受答案，按执行结果判分，不比 SQL 字符串。
- 指标：正确率；pass^3（单次 75% 的 pass^3 约为 42% [Evals]）；静默错误率（答错且没有拒答）；拒答的精确率和召回率；每正确任务成本；p95 延迟。
- 能力套件和回归套件分开。改上下文、改 skill、改模型，都必须先过回归。
- 平台 agent 的自评分数一律不采信（Genie 77.1%、Cortex Sense 83% 都是厂商自测）。

### E4 门禁

延续草稿的三态语义，作为 CI 内的 policy 步骤运行：

| 门禁 | 作用 |
|---|---|
| G-ctx | 上下文 PR，跑回归 eval |
| G-pipe | 契约、测试、对账、data-diff |
| G-model | 封存评估 + 泄漏检查 |
| G-exp | 预注册、功效分析、SRM |
| G-value | 扩量与价值声明 |

证据主体（subject）必须包含 `metric_definition_digest`、`semantic_model_digest`、`snapshot_id`、`pipeline_digest`。定义一变，下游答案和模型自动失效 [Review]。

### E5 人工审核

- 按风险和新颖度抽样，不全量审核。全量审核在 Ramp 重新造成了瓶颈 [Ramp]。
- 监控 reviewer 推翻率，防止橡皮图章 [IMDA]。
- 审批由受保护环境执行，禁止自审。

### E6 证据

- 答案卡字段：意图、指标 ID 与定义摘要、SQL、快照与新鲜度、检查结果、验证级别、注意事项、类别（CERTIFIED / EXPLORATORY / ABSTAIN）、AI 生成标识。
- 管道和模型的证据是 CI 用 OIDC 身份签发的 in-toto 证明，与制品存放在一起。不自建 ledger。

### E7 多 agent 辩论与评审团

- 不使用普通辩论。
- SQL 的做法：生成多个候选，按执行结果选择或投票 [BIRD]。
- PR：由干净上下文的 reviewer 审。
- 模型声明：由对抗型评估者审。
- 不用不跑代码的 LLM judge 判定正确性：最好成绩约 82%，顺序一换最多相差 14% [CodeJudge]。
- 评审团（panel）只有在同预算 A/B 对照中带来 ≥ 3pp 增益、且成本不超过 1.5 倍时才保留（假设）。

---

## F. 交付与组织

### F1 交付机制

- **仓库**：`data-context`（语义、表卡片、verified queries、skills、AGENTS.md）；`pipelines`；`models`。金集放在单独仓库，agent 身份不可读。
- **环境与权限**：
  - dev：agent 沙箱，使用脱敏或合成数据。
  - staging：零拷贝克隆或 time-travel 快照。
  - prod：只有 CI service identity 有写权限，agent 永远拿不到 prod 凭据。
- **CI 步骤**：sqlglot 解析 → 在 staging 上 dbt build → 契约/测试/对账 → 关键指标 data-diff（容差由 metric owner 定）→ 血缘影响分析 → eval 回归 → 签发证明 → gate 判定 ALLOW/HOLD/REJECT。
- **合并规则**：影响指标的变更需要 metric owner 通过 CODEOWNERS 审批；agent 发起的 PR，审批人必须不同于发起人。

### F2 回滚

| 对象 | 回滚方式 |
|---|---|
| 管道 | revert 后重建；数据用快照做蓝绿视图切换 |
| 模型 | 切回上一份 release manifest（模型 + 预处理 + 阈值 + 镜像）；kill switch 切到规则兜底 |
| 答案 | 定义变更后，旧摘要的答案卡标为 stale；看板中的认证答案重跑 |

### F3 角色（首个域，投入均为假设）

| 角色 | 职责 | 投入 |
|---|---|---|
| 业务 owner（每用例 1 名） | 签 baseline、KPI、kill 标准；批准实验和扩量 | 2 h/周 |
| 指标/领域 steward | 语义、认证、verified queries；每周审反馈 | 0.25–0.5 FTE/域 |
| 数据平台工程师 | MCP、CI、沙箱、路由、FinOps | 1–1.5 FTE |
| Eval owner | 金集、回归、金标审计 | 0.5 FTE |
| Analytics engineer / 数据科学家 | 审 PR；模型工厂；实验 | 在现岗内承担 |
| 风险合规 | 风险分级、PIPIA/DPIA、出境评估、标识 | 按需 |
| 财务 | 价值账本口径、月度 NV | 0.1 FTE |

### F4 采纳与变更管理

- 嵌入飞书/钉钉/企微/Slack 和 BI。LinkedIn 嵌入后用量提升 5–10 倍 [LinkedIn]。
- 用"agent 优先 + 人工升级 SLA"取代原来的 #help-data 渠道。
- 用户纠正 → 候选 verified query → steward 周审（30 分钟）。
- 培训 2 小时：如何读答案卡，以及什么情况下不要信任答案（越出能力边界时，正确率从 84.5% 跌到 60–70.6% [BCG-RCT]）。
- 分析师的 OKR 计入认证资产产出。
- 对外口径是"增强"而非"替代"。证据显示的是初级岗位招聘减少，而不是裁员 [Canaries]，所以要规划初级分析师向 steward/eval 岗位转型。

---

## G. 路线图

**0–90 天**
- 选一个有财务关账可对账的域，例如收入/订单。
- 测 baseline，签 ROI 和 kill 标准。
- 建设内容：语义层 30–50 个指标；认证 50–100 张表；verified queries 100–200 条；金集 150 题（其中 20% 是越界或歧义题）；dsh-mcp v0；4 个 skills；CI 门禁。
- 选 loop：已在 Snowflake/Databricks/DataWorks 上的，直接用原生 agent。
- 试点 20–50 人。
- 退出标准：
  - U1：正确率 ≥ 85%，pass^3 ≥ 75%，静默错误 ≤ 3%，越界拒答 ≥ 90%，自助解决 ≥ 40%，WAU ≥ 40%，每正确答案成本 ≤ 人工的 20%。
  - U2：正确分诊 ≥ 60%，PR 接受 ≥ 40%，逃逸 = 0。
  - 账本覆盖 100% 的任务。

**3–6 月**
- 扩到 3–5 个域；上线 U3、enrichment、U4。
- 廉价层承担 ≥ 60% 的调用。
- U6 首个实验完成预注册。
- 退出标准：NV（保守口径）连续 2 个月 > 0；机械工单占比下降 10pp；MTTR 下降 30%；U3 一次通过率 ≥ 70%。

**6–12 月**
- U6 走完 G4 → G6。
- 至少 2 个 harness（含 1 个境内模型）通过一致性回归，差距 ≤ 5pp。
- 自动生成合规包（模型卡、PIPIA 草稿、日志导出）。
- 退出标准：≥ 1 个决策用例的增量毛利 95% CI 下界 > 0；实测回收期 ≤ 12 个月；日志保留期限满足 EU（≥ 6 个月）和 PIPIA（3 年）要求。

---

## H. 参考实现（dsharness）下一步

### H0 现状核查（我读代码和运行数据得到的事实）

- 已经做到：world 生成了 9 类植入缺陷；Pandera 契约支持多版本；SQL runner 在失败时让下游 SKIPPED；4 个快照为 READY，并与财务关账逐月逐区精确对账；2026-08-31 和 2026-09-30 两个快照因 D8 正确地判为 FAILED（报错为 `amount_cents not found`）。
- 存在的缺口：
  - pyproject 声明了 `dsh = dsharness.cli:main`，但 `cli.py` 不存在。
  - 依赖里有 `mcp>=2.3`，但没有 MCP server。
  - `tests/` 为空。
  - `workspace.py` 引用的 `docs/DESIGN.zh-CN.md` 不存在。
- **实验只存在于反事实中**：2026-07 的流失对所有人都用 p0 判定，没有导出任何分配表，所以从源数据里根本学不到 uplift。
- **D3 修复属于"对答案"**：`invoices.sql` 把"<1000 且 ×100 落在价格簿里"这一规则写死了，等于预先知道植入的缺陷。同时 `charge_amount_on_price_book` 没有使用 valid_from/valid_to，2026-08 之后按 5900 收 plus 的错误抓不出来。
- **财务关账与真值完全相同**：真实账本存在调整项，所以对账需要支持"已解释差异"。

### H1–H8 模块

| 模块 | 内容 | 验收 |
|---|---|---|
| H1 `cli.py` + `tests/` | `dsh world build`、`dsh lake build --cutoff`、`dsh bench run`；为每个植入缺陷写性质测试，并做故障注入 | D1–D9 每项至少 1 个测试 |
| H2 `semantics/` | MetricFlow/OSI 子集 YAML，覆盖 net_revenue、mrr、active_customers、churn_rate、arpu；编译成 DuckDB SQL；生成 `metric_definition_digest` | 指标对账通过；定义变更后答案标为 stale |
| H3 `tools/mcp_server.py` + `sandbox/sql.py` | 8 个工具（见 D2）；DuckDB 只读连接，`enable_external_access=false`，`lock_configuration`，子进程超时，限制行数；只暴露 bronze_safe/silver | 用红队用例验证 custody 不可达 |
| H4 `checks/` | pit、join_cardinality、reconcile（带容差和调整项）、price_book_validity、srm、leakage | 故障注入捕获率 ≥ 95%，误拦率 ≤ 5%（目标） |
| H5 world v2 | ①2026-04 到 07 每月随机分配 50% 的 at-risk 客户接受联系，按 u<p1 实现结果，并导出 `crm_contact_assignments`；②缺陷变体用 holdout 种子随机化（区域/月份随机，×1000 单位，时区偏移）；③财务调整项 | 管道开发者看不到 holdout 种子 |
| H6 `gate/` | 移植修复后的三态门禁，签发 in-toto statement（JSON + digest） | 补齐草稿中缺失的 supersede/重试/未认证输入测试 |
| H7 `ledger/` | task_events 表：harness、model、tokens、cost、wall、outcome、rung、human_min | 输出"每正确任务成本" |
| H8 skills + AGENTS.md | 4 个 skill（见 D3） | 由 bench 证明有增益 |

### H9 DSH-Bench v0 与实验臂

任务集（全部由 custody 真值判分）：
- 指标问答 150 题 × 3 个改写：覆盖净额/毛额歧义、测试账号、退款记账月、越界题。
- break-fix 20 题：以 D8 为种子，加字段改名、单位、新 invoice_type、时区变体。要求修复后 6 个快照全部 READY，且 4 个旧快照不回退。
- 泄漏任务：以 2026-05-31 为 cutoff 构建特征，用 PIT 快照检查 D7。
- 决策任务：比较按风险排序与按 uplift 排序的策略价值。

已有的真值基线（我在 custody 上计算）：
- 联系预算 10% 时：按风险排序预计挽回 15.2 人，oracle 为 27.2 人（是 oracle 的 56%），随机为 4.0 人。
- 联系预算 20% 时：按风险排序达到 oracle 的 64%。
- 单月实验功效：at-risk 3,579 人，基线 4.9%，ATE 1.1pp，50/50 分组时功效约 37%。要达到 80% 功效，每组约需 5,300 人，也就是至少合并 3 个月。所以系统对单月实验必须输出 INCONCLUSIVE，G6 不得触发。这正好用来测试系统是否诚实。

实验臂：
- A：只用 bronze_safe + SQL
- B：A + 语义层 + MCP
- C：B + checks、门禁、拒答
- D：C + reviewer agent
- E：C 换成境内模型的 harness

每个臂报告：正确率、pass^3、静默错误率、拒答精确率与召回率、缺陷捕获率与误拦率、每正确任务成本（USD/CNY）、wall time、人工介入次数。

预注册的假设：
- B 比 A 正确率高 ≥ 15pp。
- C 的静默错误率 ≤ A 的 1/3。
- D 只有在 ≥ +3pp 且成本 ≤ 1.5 倍时保留。
- E 与 C 的差距 ≤ 10pp。

---

## I. 风险与反例

### I1 主要风险

| 风险 | 失效方式 | 缓解 |
|---|---|---|
| 上下文维护成本失控，或 steward 流失 | 金集和语义腐化，正确率回落 | steward 写进 OKR；岗位空缺即触发 kill（见 B6） |
| 平台原生 agent 快速追平 | 自建层变得多余 | 自建部分只限上下文和 eval，这些可以迁移到任何 loop |
| 探索性答案进入决策 | 静默错误带来损失 | CERTIFIED/EXPLORATORY 双通道；KPI 只走 V3 |
| 门禁过重导致被绕过 | 47% 的企业承认绕过过治理 [EY] | 按风险分级；误拦率 ≤ 5% 作为 KPI |
| 境内 token 价格或供给冲击 | 成本或可用性出问题 | 多供应商；闲时批处理；告警 |
| 产品改名或弃用 | 集成失效 | 只通过 MCP、SQL、OSI 等协议集成 |
| 合成世界过拟合 | bench 好看但真实数据差 | 用 holdout 种子；指标仅视为机制证明 |
| 采纳失败 | 价值为 0 | 嵌入 IM；用 agent 取代原帮助渠道 |
| 自助率被刷（Goodhart） | 数字好看但答案是错的 | 自助率必须与审计正确率配对报告 |

### I2 什么结果会证明本设计错了（试点中验证）

1. 在本组织的金集上，B 相对 A 的提升 < 10pp：说明"语义优先"在该组织不成立。
2. 不做任何自建的平台原生 agent，在本组织金集上的正确率和静默错误率与 C 相当：说明 MCP 和 checks 不该再建，只保留金集，其余全部购买。
3. 门禁对注入缺陷的捕获率 < 80%，或误拦率 > 10%：说明验证层的设计需要重做。
4. 兑现系数 κ 实测 < 0.2：说明工时口径不成立，只能靠 U5/U6 支撑价值。
5. 评审团在同预算下带来 ≥ 5pp 增益、成本 ≤ 1.5 倍：说明要修订"单写者"立场。

### I3 反例警示

- Klarna 以裁员为基础的 ROI 后来被修正 [Klarna]。
- Databricks Knowledge Assistant 从 GA 到 legacy 只用了 9 个月 [Platforms]。
- Vercel 的"100%"只基于 5 道题 [Vercel]。
- DABstep 的公开榜单被刷分 [Bench]。

---

### 证据键

均出自 digest 文件。

| 键 | 来源 |
|---|---|
| Grab | InfoQ 2026-08 |
| Ramp | engineering.ramp.com |
| Uber、Cases | Uber 博客 / Pinterest / OpenAI 迁移 |
| OpenAI-DA | ByteByteGo 2026-06 |
| Meta | Analytics at Meta 2026-03 |
| Vercel | 2025-12 |
| LinkedIn | arXiv 2507.14372 |
| dbt-SL | dbt 博客 2026-04 |
| DABstep-abl | arXiv 2609.22259 |
| BEAVER / BIRD | arXiv 2510.05318；bird-bench |
| ELT-V | arXiv 2603.29399 |
| ADE | dbt 2026-04 |
| MLE | openai/mle-bench |
| RADAR | arXiv 2609.32528 |
| Debate | arXiv 2508.17536 |
| CodeJudge | arXiv 2507.10535 |
| Evals | Anthropic 2026-01 |
| Bench | arXiv 2601.08778；DABstep 公开榜 |
| Cognition | 2026-04-22 |
| MultiAgent | Anthropic 2025-06 |
| Tooling | agentskills.io、agents.md、Claude Code 文档 |
| Snowflake-eval | Cortex Analyst evaluations |
| Platforms | Snowflake/Databricks/Google/DataWorks 定价与发布说明 |
| OSS | PyPI/GitHub 2026-10 |
| Price | OpenAI/Anthropic/DeepSeek/Z.ai 定价页 |
| NBS / BLS | 2026-05 / 2025 |
| Wharton26 | 2026-10-13 |
| McK26 | 2026-08-25 |
| Gartner25/26 | 2025-06-25；2026-05/09 |
| MIT-NANDA | 2025 |
| BCG-RCT | Org. Science 2026 |
| Canaries | Stanford 2026-08 |
| EY | 2026-09 |
| IMDA | 2026 |
| CN-Reg | 网信办出境规定、标识办法、NFRA 金发〔2026〕8号 |
| Reg | EU 2026/1744、SR 26-2 |
| Review | 草稿评审：gate.py 缺陷、OSI 缺失 |
| Klarna | 2024 新闻稿；Gartner 2025-06-10 |

相关文件：
- docs/_research_digest_2026-10-19.txt
- src/dsharness/world/generator.py
- src/dsharness/lake/pipeline/models/invoices.sql
- workspace/custody/truth_experiment.parquet

#################### NEXT PROPOSAL ####################

# DS Harness 工程方案 v0.2：以可信为先的 Agentic 数据科学与数据工程交付层

日期：2026-10-19。引用的证据都来自 `dsharness/docs/_research_digest_2026-10-19.txt`，文中只写来源简称。
标注约定：【假设】是本文的工作假设，试点后要换成实测值；【实测】是我对本机 `dsharness/workspace` 的实际计算。

**与草案（EvidenceDS v0.1）的关系**

- **保留**：三维状态（execution / check / promotion），以及"缺失、ERROR、INCONCLUSIVE 一律判 HOLD"；PIT 与泄漏规则；用身份隔离的 sealed holdout；G5 有限实验和 G6 扩量分开；assignment、exposure、outcome 分开记录。
- **删除**：自研 HMAC ledger、17 张表的 PG schema、13 个端点的 API、reconciler；强制使用 Dagster；"Agent Adapter 放到最后一个 PR"。
- **改动**：首个纵向场景从"churn 风险分类"改成"DE break-fix + 治理指标问数"；MCP 改为主接口；新增 semantic layer、eval、模型路由和价值台账。

---

## A. 核心论断

1. **稀缺的是验证，不是生成。** 生成 SQL、dbt、pipeline 和模型代码的能力已经商品化：Genie Code、CoCo、BigQuery DE/DS Agent、DataWorks Data Agent、dbt Wizard 都已 GA 或公测。但 data agent 约 70% 的失败是看起来正常的错误答案（RADAR：DS-STAR 的 580 次零分里有 405 次是 silent）。本系统的价值在于：用低于人工重做的成本，判断 agent 的产出能不能用、能在多大范围用。
2. **产品定义：一个原生带验证的 agentic DS/DE 交付层。** agent 负责生成，这部分买现成的；harness 负责验证，这部分自建薄层；结果由具名的人负责。只自建 6 样东西：check library、MCP 工具服务器加 skills、eval harness、CI gate 策略步骤、证据与信任标签（trust label）、价值与成本台账。
3. **按"可验证程度"排上线顺序，而不是按生命周期顺序。**
   - 先做有独立真值、或验证很便宜的工作：管道修复要对账，指标问数用 semantic layer 加 golden set。
   - 再做真值会延迟到来的工作：预测模型。
   - 最后做真值很贵的工作：因果判断和决策。
   - 流程覆盖端到端，但自治程度不是端到端一样的。
4. **每个交付物都带 claim type 和 evidence tier（信任标签）。** 允许的用途由 tier 和风险等级共同决定。没有标签的数字不得进入 KPI、董事会材料或对外报告。
5. **真值要靠构造，不能假设它已经存在。** 来源按强度排序：独立记录对账、不变量、双路径实现（N-version）、golden set、延迟真值回溯评分、实验、合成世界。每一种只在自己的适用范围内算作证据。
6. **统计有效性是门禁的一部分。** 效果宣称必须带 CI 和样本量。实验前先算 power 和 MDE。主分析用 ITT，并检查 SRM。没有随机化就不标"因果已验证"。HTE 和细分发现默认按探索性结论处理。
7. **单写者 + 程序化验证器。** 不搞角色扮演式的多 agent 团队。debate 和投票不算正确性证据：Debate or Vote（NeurIPS 2025）显示收益主要来自投票。LLM judge 不能作为代码或 SQL 正确性的门禁：CodeJudgeBench 最好约 82%，交换答案顺序会波动最多 14%。
8. **合规是证据的副产品，不是另一套流程。** 用 CI OIDC 身份、in-toto attestation 和 protected environment 代替自研 ledger。审计日志、model card 和 PIPIA 草稿都从同一份证据自动生成。
9. **本系统不是：**
   - 通用 NL2SQL 或 ChatBI（直接买平台的）；
   - 又一个 agent 框架；
   - 强推的新编排器；
   - 以裁员为依据的 ROI 工具；
   - 授信、核保、对个人定价这类高风险决策里的 LLM 决策者。
10. **经济上按两件事计价：** 一是"每个经验证的正确任务的成本"（CPVC），二是有实验支持的业务 KPI 变化。中国的人力替代价值大约是美国的 1/4 到 1/8，所以 ROI 必须来自速度、规模和收入指标。

---

## B. 价值与经济模型

### B.1 用例组合

按"可度量价值 × 可验证性 × 可行性"排序：

| # | 用例 | 价值度量 | 真值来源 | 可行性证据 | 上线时间 | 自治 |
|---|---|---|---|---|---|---|
| 1 | DE 变更与修复：schema drift、失败任务分诊、dbt 变更、回填 | MTTR、事故数、工程工时 | tests + data diff + 独立记录对账 | EL 约 96%、T 23–33%（ELT-Bench-Verified）；ADE-bench 显示语法和逻辑 bug 修得好，数据层面的问题必须真去查数据；Grab Scarlet；OpenAI 迁移约 1 万个 DAG | 0–90 天 | A3 |
| 2 | 治理指标问数（自助分析） | 工单分流率、首次响应时间 | verified query + golden set + 延迟真值 | 有整理过的 schema 时约 80–90%；semantic layer 带来 +8 到 +25pp，并把错答变成拒答（dbt 2026-04）；Grab 的 SQL 自助率从 50% 到 81% | 0–90 天 | A1 |
| 3 | 数据质量分诊与对账监控 | 发现提前量、避免错报 | 对账、不变量 | 高 | 3–6 月 | A2 |
| 4 | 周期报表与月结辅助 | 外包和 BPO 支出减少 | 对账 | MIT NANDA：替代外部支出的回报最清楚 | 3–6 月 | A3 |
| 5 | 决策模型工厂（留存 uplift、需求预测） | 增量净贡献（实验） | 成熟标签 + 实验 | MLE-bench 61–64%，但价值只能靠实验证明 | 6–12 月 | 训练 A2，上线 A3 |
| 6 | 开放式深度分析和因果问题 | 决策质量 | 基本没有 | BIRD-Interact 8.7–17%；BCG 实验中前沿外任务正确率从 84.5% 降到 60–70.6% | 持续 | A0/A1，只作参考建议 |

明确不做：通用 notebook copilot、自研 NL2SQL 引擎、在高风险决策里让 LLM 做决策者。

### B.2 价值公式

对每个工作流 w，按月计算：

```
NetValue_w = N_w^deflected × p_ok × (t_base − t_review) × c_h      # 只算原本就会进人工队列、被分流掉的任务
           + ΔKPI_w × margin                                         # 只有 G5/G6 实验证据时才计入，否则记 0
           + ExternalSpend_displaced
           − N_w × p_escape × L_w                                    # silent error 漏出率 × 单次损失
           − (C_llm + C_compute + C_review + C_curation + C_platform + C_maint)

CPVC = 总成本 / (N × p_correct_verified)
```

三条规则：

- **只算基线需求。** Ramp 的提问量涨了约 22 倍，其中大部分是新诱发的需求；新需求只有挂到某个决策上才计价值。
- **p_escape 来自随机抽检，不用自报。**
- **ΔKPI 只认实验结果。** Wharton 2026 的数据：跟踪增长指标的部门有 35% 报告强 ROI，只看效率的部门只有 13%。

### B.3 成本模型

【假设】场景：300 名用户，每月 3,000 个治理问答，40 起管道事故。

| 成本项 | 估算 | 依据 |
|---|---|---|
| 问答 LLM | 约 80k 输入（85% 命中缓存）+ 3k 输出。Sonnet 5.5（$2/$10，缓存 0.05×）约 $0.06/题，含重试约 $0.08；DeepSeek V4-Pro 非高峰约 $0.02–0.03；Qwen3.7-Max 约 $0.04 | 官方价格页（2026-10）；Vercel d0 每题 61–102k token |
| 仓库计算 | 【假设】$0.02–0.2/题 | Snowflake AI Credit $2.00，按区域路由 $2.20 |
| DE 事故处理 | 1–3M token，约 $1.5–5/起；CI 分支构建 $1–20 | 多 agent 约 15 倍 token，所以默认单 agent |
| 人工复核 | 随机抽检每月 150 条 × 6 分钟 ≈ 15 h；PR 复核 40 × 30 分钟 ≈ 20 h；纠错分诊约 20 h | 【假设】 |
| 上下文维护 | 每约 100 个核心指标配 0.5 FTE 指标负责人；golden set 200 题 × 20 分钟 ≈ 67 h，每季度更新约 20 h | 【假设】 |
| 维护 | 0.25 FTE 平台工程；每次模型或 skill 升级都要重跑回归 | 【假设】 |

**结论：token 不到总成本的 10%，人工复核加上下文维护约占 70–80%。** 优化的重点是复核成本（抽样和分级），不是 token。

CPVC 示意：

- 美国：（$600 + 15 h × $95 + 0.5 × $15.8k）/ 2,550 个正确回答 ≈ **$3.9/题**。对比分析师工单约 30 分钟 × $95 ≈ $47。
- 中国：（¥4k + 15 h × ¥120 + 0.5 × ¥28k）/ 2,550 ≈ **¥8/题**。对比约 ¥60。

人力成本依据：BLS 中位数 $120,230，含福利约 $172k；国家统计局 2025 年 IT 行业平均工资 ¥128k–249k，加 35% 用工成本是【假设】。真正卡住结论的不是 token，而是两件事：被分流的基线工单是否真的存在，以及 p_escape 是否足够低。

### B.4 中国与全球的差异

| 维度 | 中国 | 全球 |
|---|---|---|
| 价值论点 | 速度、规模、收入；"AI+"政策目标是 2027 年智能体普及率超过 70% | 人力 + 速度 |
| 模型 | 默认 Qwen3.7-max / DeepSeek V4 / GLM-5.3 / Kimi K3。2026 年价格上涨（DeepSeek 8 月涨 50%–1,100%，另有高峰时段计价），评测类任务放到非高峰批量跑 | 按区域使用对应 endpoint，注意 ZDR 适用范围 |
| 平台 | DataWorks Data Agent（Qwen Code、SKILL.md、5 档审批）、火山引擎 Data Agent、腾讯 DataBuddy；调度用 DolphinScheduler 或 DataWorks；协作在飞书、钉钉、企业微信 | Snowflake、Databricks、BigQuery、Fabric；Airflow；Slack、Teams |
| 法规 | PIPL 第 24、55–56 条；出境门槛（10 万、100 万、敏感个人信息 1 万）；《网络数据安全管理条例》；AIGC 标识办法（2025-09-01）；只有对公众提供服务才需要备案；NFRA 金发〔2026〕8 号 | EU AI Act 第 50 条（2026-08-02）、Annex III（2027-12-02）；CJEU C-203/22 解释权；US SR 26-2（明确排除 GenAI）；Colorado ADMT（2027-01-01）；Reg B 拒绝理由通知 |
| CI 与签名 | 自托管 GitLab 或云效 + cosign + 企业 KMS | GitHub actions/attest（私有仓库需要 GHE Cloud） |

### B.5 Kill criteria

在第 90 天和第 6 个月各检查一次。

| 工作流 | 触发条件 | 动作 |
|---|---|---|
| 问数 | held-out pass^3 < 80%，或分流率比基线提升不到 10pp | 停止扩张；修上下文或终止 |
| 全部 | T1 随机抽检 silent error 的 95% 上界 > 5% | 自治等级降到 A0 |
| DE | 处理 30 起以后，PR 接受率 < 40%，或 MTTR 降幅 < 30% | 收缩为只做分诊 |
| 全部 | 连续 2 个月 CPVC 超过人工单位成本的 50% | 改模型路由或终止 |
| 全部 | 第 3 个月目标人群 WAU < 30% | 判定为嵌入失败 |
| 模型工厂 | 第 6 个月仍拿不到随机化授权 | 停在离线阶段，不宣称价值 |
| 全部 | 出现漏到对外或监管报告的 Sev-1 错误 | 该工作流停摆复盘 |

---

## C. 端到端架构

```
入口：飞书/钉钉/Slack/BI/IDE ── 平台 agent 或自有 harness
   │  MCP 2026-07-28（无状态、OAuth 2.1、EMA）
Agent 层：单写者 + 只读子 agent │ skills │ AGENTS.md │ 模型网关（按数据级别路由）
   │  ≤12 个工具，读写分离
验证层（自建）：check library │ eval harness │ gate 策略步骤 │ in-toto 证据 │ 信任标签 │ 回溯评分器
   │
语义/上下文层：MetricFlow/OSI(Ossie) 或平台语义视图 │ verified query（运行集 ≠ 评测集）│ 认证表 │ 事故与注意事项库
   │
数据层：采集 → 仓库/湖 → dbt/SQLMesh 变换 → 契约检查 → OpenLineage
ML 层：沙箱训练 → sealed scorer（独立身份）→ MLflow 3.17 registry
交付：带标签的答案 │ PR→CI→protected env │ release manifest→批量表/服务 │ 决策记录 + 实验
价值与监控：价值台账 │ 实验分析 │ tracing │ golden set 夜间 canary │ 漂移 │ 回溯评分
```

| 阶段 | agent 的工作 | 验证方式 | 买或复用 | 自建 |
|---|---|---|---|---|
| 采集 | 在沙箱里写 dlt source；提出契约草案 | 源端控制总数（行数、金额）；PII 扫描；采集合规（网络数据条例第 18、24 条） | dlt 1.31、Fivetran、DataWorks 数据集成 | 契约模板、控制总数检查 |
| 处理 | 写 dbt/SQL 变更 | dbt tests、Pandera 0.34；不变量（声明的 join 基数、守恒、按事件身份去重）；data diff；对账 | dbt-core 1.12 或 dbt-oss 2.0.x、SQLMesh 0.236、GX Core 1.24 | 不变量库、对账框架 |
| 特征 | 写 PIT 特征 | available_at ≤ cutoff；as-of diff（用历史快照重算后比对）；泄漏检测 | 仓库 time travel、Iceberg 1.12、DuckLake 1.0 | PIT 检查库（沿用草案的规则） |
| 训练 | 只拿 train/dev 训练候选模型 | 固定搜索预算和种子；sklearn Pipeline | sklearn、LightGBM、Optuna、EconML/causalml | task pack |
| 评估与测试 | 不能接触 holdout | sealed scorer、切片、校准、Qini/policy value；agent 自身跑 golden set | Inspect AI 0.3.x、MLflow GenAI eval | scorer、评测集 |
| 交付 | 提交 PR 或 release request | gate | GitHub/GitLab protected env、attest/cosign、MLflow alias | gate 策略、release manifest |
| 价值 | 起草实验方案 | power、SRM、ITT、CUPED | GrowthBook 或自有实验平台（BigQuery AI.CAUSAL_EFFECT 还在 Preview，不依赖） | 价值台账、决策日志 |
| 监控 | 根因分析 | 漂移、SLA、tracing、回溯评分 | MLflow tracing、自托管 Langfuse、OpenLineage 1.54 | 回溯评分器、canary |

**两种部署形态**

- **SMB / 本地优先**
  - 组件：uv + Python 3.12；dlt；DuckDB 1.5.6 + DuckLake 1.0；dbt-core 1.12；Pandera；Prefect 3.8，或者 cron + CI；MLflow 3.17 单机；FastMCP 4.1；MetricFlow 0.213 或 Cube 1.8；harness 用 `claude -p --bare`、Qwen Code 或 OpenHands；沙箱用 Docker `network=none` 或 sandbox-runtime。
  - 在自己的数据跑过回归之前，生产环境暂缓 Polars 2.0、DuckDB 2.0、dbt v2。
  - "本地优先"指数据不离开公司环境，不是把企业数据拷到笔记本上。
- **已有数仓的企业**
  - 数仓仍是 system of record；查询下推执行，以用户身份运行（OBO + RLS）。
  - 平台内的编码和问答买平台 agent；跨平台验证走自建 MCP server。
  - 沙箱用 k8s agent-sandbox v1.0.7（gVisor/Kata），或自托管 E2B；MLflow 开启 RBAC；只有长时间运行、需要人工介入的工作流才用 LangGraph 1.2。
  - 沿用现有的 Airflow 3.3、DolphinScheduler 或 DataWorks，不新增编排器。
- **license gate（两种形态都适用）**：SBOM 标出 ELv2（Soda v4）、BSL（lakeFS ≥1.87）、PLA（dbt Fusion 二进制）和 AGPL。内部使用可以，转售为托管服务会被阻断。

---

## D. Agent 设计

### D.1 拓扑

- **一个工作流一个写者 agent。**
- **只读子 agent** 做 schema 发现、数据画像、日志分诊，每次返回 1–2k token 的摘要。
- **流程骨架是确定性代码**（CI 或 DAG），不用 LLM supervisor。
- **大规模扇出**（比如给 500 张表做画像）用脚本或 DAG 调用 agent，输出经 schema 校验，token 预算写明。
- **Verifier 是程序，不是 agent。**
- **Challenger（可选）**：上下文干净、只读，只能输出可执行的检查或反例。只有执行后确实让候选失败才计入，从不参与投票。依据：Cognition 2026 的结论"写操作保持单线程"。

### D.2 工作流

| 工作流 | 触发 | 写什么 | 验证 | 自治 | 人工门禁 |
|---|---|---|---|---|---|
| W1 治理问数 | 用户提问 | 不写 | 语义路径 + 信任标签 + 回溯评分 | A1 | T3 不得导出；涉及重大决策时人工 |
| W2 管道修复 | build FAILED 或 test 失败 | 分支上的补丁 | CI 全量构建 + diff + 对账 | A3 | CODEOWNERS 合并 |
| W3 数据质量分诊 | 异常或对账失败 | 事故单 + 检查草案 | 每个假设都必须是一个带预期结果的查询 | A2 | 负责人确认 |
| W4 模型候选 | 任务单 | 沙箱里的候选模型 | sealed scorer | A2 | 上线走 A3 |
| W5 实验 | 决策请求 | 预注册方案和分析 | 统计检查清单（E.8） | A2 | 业务负责人和统计负责人 |
| W6 上下文补全 | 每晚 | 表文档和注意事项草稿 | 负责人审核后认证 | A2 | 负责人 |

### D.3 MCP 工具

最多 12 个。依据：OpenAI 把工具从约 40 个减到 13 个后效果更好；工具超过 30–50 个时选择准确率下降。

| 工具 | 读写 | 作用 |
|---|---|---|
| `context.get` | R | 表、指标、注意事项、认证等级 |
| `metric.query` | R | 走 semantic layer 查询 |
| `sql.readonly` | R | 只读 SQL；有行数上限、PII 掩码，结果打 T3 标签 |
| `data.profile` | R | 数据画像 |
| `check.run` | R | 按 check_id 运行已注册检查 |
| `build.branch` | W-sandbox | 在分支上构建 |
| `diff.builds` | R | 比较两次构建 |
| `pr.propose` | W-proposal | 提交 PR 和证据包 |
| `model.request_eval` | W-proposal | 申请 sealed scorer 评估 |
| `experiment.design` / `experiment.analyze` | R | 实验设计与分析 |
| `evidence.get` | R | 查看证据状态 |

实现约束：

- 用 FastMCP 4.1 / mcp 2.3，遵循 2026-07-28 版规范：无状态；tools/list 顺序固定；handle 由服务端生成；长构建和训练走 Tasks 扩展；OAuth 2.1 + RFC 9728/8707；通过 IdP 接 EMA。不用 Sampling、Roots、DCR。
- 每个响应都带 trust 块，例如：

```json
{"value": 1234567, "unit": "fen", "metric": "net_revenue@v3",
 "trust": {"tier": "T1", "build_id": "2026-09-30_ab12cd34", "pipeline_digest": "…",
  "metric_def_digest": "…", "independent_record": "none (month open)",
  "retro_score_at": "2026-10-06", "caveats": ["INC-2026-03-SOUTH: 858 rows unit-corrected"]}}
```

### D.4 Skills 与上下文

- **Skills**：`metric-<domain>`、`schema-drift-fix`、`reconcile-before-claim`、`pit-features`、`uplift-and-experiment`、`report-with-trust-labels`。由领域专家拥有，有版本号，每次改动都要过回归 eval。
- **AGENTS.md** 写仓库约定。Claude Code、Codex、Gemini CLI 都会读它。
- **上下文资产**
  - 分级的认证表。
  - OSI / MetricFlow YAML。
  - verified query 分成运行集 R 和评测集 E，两者不相交（Snowflake 的规则）。
  - 事故与注意事项库。
  - 个性化：默认用户近 90 天用过的表。Meta 的数据是 88% 的查询只用这类表。
  - 夜间补全任务。
  - DABstep 的成功做法：离线用强模型提炼一个经验证的函数库，运行时用便宜模型调用这些函数。DABstep Hard 因此从 14.55% 提升到 89.95%。
- **记忆**：用户纠错先进审核队列，负责人批准后才变成 verified query 或注意事项，不直接写进全局记忆。NFRA 第 25 条点名了记忆污染风险。

### D.5 模型路由与数据驻留

默认只把元数据、schema、聚合结果和掩码后的样本发给 LLM。原始行留在沙箱里，在沙箱内执行代码（这种做法 token 降低 37–98%）。

| 数据级别 | 中国 | 欧盟 | 美国 |
|---|---|---|---|
| 公开或内部元数据 | 任意已批准的模型 | 任意已批准的模型 | 任意已批准的模型 |
| 个人信息 | 只能用境内模型：自托管开源权重，或已备案的 API。用境外 API 就算出境 | 欧盟区 endpoint，零保留或不用于训练。Claude Managed Agents 和程序化工具调用不在 ZDR 范围内，需要自己跑 loop | 区域 endpoint + 合同条款 |
| 敏感或重要数据 | 只能自托管 | 审批后经 DPIA | 审批 |
| 金融业 | 不得用 PII 训练或优化 GenAI；保留推理路径和阈值（NFRA） | — | — |

- **模型网关**：MLflow AI Gateway 或 agentgateway 1.6。至少接 2–3 家供应商，其中一家国产、一家可自托管。每次运行有预算上限，并设涨价告警。
- **按任务分档**
  - 便宜档：Haiku 5.5、gpt-6-luna、deepseek-flash、GLM-5.3-Flash，用于分类、抽取和 SQL 草稿，都要靠执行结果验证。
  - 前沿档：用于规划和修复。
  - 评测任务放到非高峰批量跑。

### D.6 Harness 可替换

- **接口契约**：工具在 MCP，流程在 skills，指令在 AGENTS.md，评测是 Inspect 任务，输出由 JSON schema 约束。
- **适配器**：`claude -p --bare --json-schema --max-budget-usd`、Claude Agent SDK 0.2.x、`codex exec`、OpenAI Agents SDK 0.24、ADK 2.12、Qwen Code、OpenHands 1.28。
- **运维要求**
  - 锁定版本。
  - 每季度在同一 golden set 上做一次跨 harness 对比。
  - CI 里必须加 `--bare`。不加的话，仓库的 hooks 和 .mcp.json 会在没有信任确认的情况下直接运行。
- **避开**：CrewAI（2026 年发了 289 个版本）、停滞的 smolagents 和 AutoGen、已归档的 Vanna 和 Daytona OSS。核心代码不绑定任何厂商 SDK，例如 Knowledge Assistant 从 GA 到被列为 legacy 只用了 9 个月。

### D.7 自治等级

| 等级 | 含义 |
|---|---|
| A0 | 只给建议 |
| A1 | 只读执行并回答 |
| A2 | 写沙箱、分支或 dev 环境 |
| A3 | 向生产提交 PR 或 release request，由人合并 |
| A4 | 在策略范围内自动执行，事后抽检 |

- **升级 A3 → A4（按任务类别）**：连续 30 次变更被接受且零漏出；回归 pass^3 ≥ 0.9；回滚演练通过。
- **降级**：出现任何 Sev-2 漏出。
- **执行方式**：用 hooks 和权限强制，不靠提示词。例如用 PreToolUse 拦截 DROP、DELETE 和生产写入。
- **不要每步都审批**：Wharton 数据显示，逐步审批的部门变革性成功率是 16%，充分使用 agent 的是 31%。审批按风险分级。

---

## E. 可信与验证

### E.1 信任标签

| Tier | 定义 | 例子 | 允许用途 |
|---|---|---|---|
| T0 Reconciled | 与独立记录精确一致 | 已结账月份的收入对上财务结账数 | 对外、监管、董事会 |
| T1 Governed | 走认证指标定义，所有测试通过，但暂时没有独立记录 | 未结账月份的收入 | 内部 KPI，标"暂定"，结账后回溯评分 |
| T2 Cross-checked | 两条独立路径结果一致，且不变量成立 | agent 写的 SQL 与 semantic layer 结果一致 | 内部决策支持 |
| T3 Exploratory | 原始 SQL 或新分析，列出了假设 | 临时提问 | 只能探索，不进 KPI |

模型和决策类结论另有一套标签：P-offline、P-shadow、C-RCT（带 CI）、C-obs（依赖假设，永远不标"已验证"）、INCONCLUSIVE（如实报告）。

### E.2 缺少真值时的替代

| 替代来源 | 能证明什么 | 不能证明什么 |
|---|---|---|
| 独立记录对账（财务结账、银行流水、源端控制总数） | 加工过程正确 | 源系统本身记录的现实是否正确 |
| 不变量和变形关系（守恒、join 基数、幂等、PIT） | 某一类错误不存在 | 口径本身是否正确 |
| N-version 双路径（不一致就 HOLD） | 实现层面的错误 | 两条路径共用了同一个错误定义 |
| golden set / verified query | agent 在已知问题上的能力 | 分布外的问题 |
| 延迟真值回溯评分（答案带快照记录，结账或标签成熟后自动重评） | 真实世界的准确率，不需要人工标注 | 没有延迟真值的问题 |
| 已知抽样概率的随机抽检 | 无偏的错误率估计 | 发现错误的队列（有偏）不能用来估错误率 |
| 实验 | 因果效果 | 实验之外的人群 |
| 合成世界（预埋真值） | harness 和 agent 的失败模式 | 真实环境的准确率 |

抽检和评测的样本量规则：

- n 次抽检零错误时，95% 上界约为 3/n。要把错误率压到 ≤2% 需要 n ≥ 150，≤1% 需要 n ≥ 300。
- golden set 有 200 题时，85% 附近的 CI 约 ±5pp；要 ±3pp 需要约 550 题。

### E.3 验证阶梯

| 级别 | 内容 |
|---|---|
| L0 | 能执行（不算证据） |
| L1 | 契约和测试 |
| L2 | 不变量和性质测试 |
| L3 | 对账，或双路径一致 |
| L4 | golden set 回归（pass^3） |
| L5 | 专家复核（抽样，或按风险和新颖度触发） |
| L6 | 结果证据（实验、成熟标签、回溯评分） |

各交付物的最低要求：

- 对外数字：L3 + L5。
- DE 的 PR：L1–L3。
- 问答：L1 + L4，再加 L6 回溯评分。
- 模型扩量：L6。

### E.4 Agent 自身的评测

- **两套 suite**：capability suite 和 regression suite 分开，后者要求接近 100% 通过。
- **规模**：起步用 20–50 个来自真实失败的任务，逐步扩到 200–500 个。
- **评分**
  - 按执行结果集比较，允许多个可接受答案。
  - 每题 2–4 种改写。
  - 设一套超范围题，必须拒答。
  - 检查中间步骤（Uber 的 decoupled 模式）。
- **报告**：pass@1、pass^3、silent error 率（答错但很自信的比例）、拒答精确率和召回率、CPVC、延迟，按任务类型和风险等级分层。Anthropic 的算例：单次成功率 75% 时，pass^3 只有约 42%。
- **评测集也要审计**：公开基准的答案键在部分子集上错误率达 30–60%。所以评测集要双人审，kappa ≥ 0.8。
- **回归门禁**：模型、提示词、skill、语义定义任何一项变更都要过。用 McNemar 配对检验，不用固定阈值；接受约 5% 的运行噪声。
- **judge 模型锁定版本。**
- **评测题永远不进运行时上下文。**

### E.5 门禁

- **G-ANS（答案）**：tier 自动计算；T3 不能导出到汇报材料。
- **G-PR（管道变更）**：快照上构建 → 契约和测试 → 与生产做 data diff → 对账 → attest → protected env（必需审批人，禁止自己审批）→ 部署 → 下一次运行后再对账 → 失败自动 revert。
- **G-MODEL（模型）**
  - G3 离线：sealed scorer、PIT、泄漏、切片、校准、CI。
  - G4 打包。
  - G5 有限实验：对预注册的 MDE 达到 power ≥ 0.8，否则标为 pilot。
  - G6 扩量：扣除成本后 ITT 效果的 CI 下界大于 0，且护栏指标没破。
- **G-DECISION（决策）**：重大决策必须人工。解释义务按 PIPL 第 24 条、C-203/22、Reg B、Colorado 执行。
- **gate 语义**：沿用草案的三维状态，修三处：
  - 先认证，再计数。
  - 增加 supersede 和重试语义。
  - 无法验证就 HOLD 并告警；只有经过认证的 FAIL 才能 REJECT。
  - 退出码：0 = ALLOW，10 = HOLD，20 = REJECT。
- **HOLD 率健康区间 5–15%。** 超过 20% 时，用户会绕过系统；EY 调查中有 47% 的人承认绕过过治理。

### E.6 人工复核

- **按风险和新颖度触发，不是全量。** Ramp 发现每条都人工检查会重新变成瓶颈。
- **复核人看的是证据包**：SQL、diff、对账结果、tier，不是一段叙述。
- **防走过场**
  - 在复核队列里混入 5% 的预埋缺陷 canary，直接复用 dsharness 的缺陷生成器。
  - 统计检出率、推翻率和复核时长。
  - canary 检出率低于 80% 的人暂停审批权【假设阈值】。
  - 依据：BCG 实验显示，任务超出 AI 能力边界时，过度信任会把正确率从 84.5% 拉到 60–70.6%。
- **每个产出物有具名负责人。**

### E.7 多 agent debate 和评审团

不把 debate 或投票当证据。只允许三种用法：

1. 生成 N 个 SQL 候选，按执行结果集多数选。
2. 上下文干净的 challenger 输出可执行检查。
3. LLM judge 只评不可验证的质量（是否清楚、是否答了所问）：两两比较、随机顺序、至少采样 3 次，与人工标注的一致率 ≥ 0.8。不用它做正确性门禁。

评审团保留与否，看固定预算下的 A/B/C/D 消融实验：只有每单位成本能减少的 silent error 足够多才保留。

### E.8 统计检查清单

这份清单内置在 W5 的 skill 和 gate 里。

- **预注册**：随机化单位、人群、主指标、MDE、power、周期、停止规则。
- **SRM**：χ² 检验 p < 0.001 就判实验无效。
- **主分析**：ITT，按随机化单位用 cluster-robust 标准误。
- **多重比较**：切片用 Holm 或 BH 校正。
- **HTE**：要么预先指定，要么标探索性，并要求复现。
- **CUPED**：必须预先指定。
- **不偷看**：要提前看结果就用序贯检验方法。
- **观察性研究**：用 DoWhy 做反驳和敏感性分析，结论标 C-obs。

---

## F. 交付与组织

### F.1 交付机制

- **答案**
  - 在飞书、Slack 或 BI 里交付，带 tier 徽标、SQL、指标定义版本和快照 ID。
  - 发到企业外部的内容要加 AIGC 标识（中国标识办法；EU AI Act 第 50 条）。
- **管道**
  - 三个环境：dev（agent 可写）、CI（临时环境：zero-copy clone、dbt defer/state、DuckDB 快照）、prod（只有 CI 服务身份能写）。
  - attestation 的 subject 包括：pipeline_digest、source_digest、metric_def_digest、semantic_model_digest、harness 和 skill 的 digest。
  - 发布 = 合并 + 打 tag。回滚 = revert 后从上一个 digest 重建。数据回滚用 time travel 或 Iceberg/DuckLake 快照。回填单独提 PR，先 dry-run 出 diff。
- **模型**
  - release manifest 包含：模型、预处理、决策策略、运行时、证据。
  - alias 只能由 release job 修改。
  - 顺序：shadow → 有限实验 → 扩量。
  - schema 不兼容时，回退到规则策略，不做盲目回滚。
- **决策**
  - 决策记录包括：问题、选项、证据 tier、负责人、预期效果、复查日期。
  - 结果写进价值台账，字段：task_id、baseline_ticket_ref、review_min、cost_usd、tier、outcome（接受 / 修正 / 漏出）、decision_id、kpi_link。
- **harness 自身**：模型、提示词、skill、语义定义的变更都按发布处理，过回归评测门禁。

### F.2 角色

| 角色 | 负责 |
|---|---|
| 业务负责人 | 价值定义、实验预算、决策 |
| 指标负责人 / 数据管家 | 语义定义、verified query、纠错分诊 |
| 分析工程师（CODEOWNER） | 审核 DE 的 PR |
| 数据平台 / Agent 运维 | harness、CI、MCP、成本、评测 |
| DS / 统计负责人 | 评测设计、实验、因果结论签字 |
| 风险与合规 | 风险分级、路由矩阵、审计 |
| 安全 | 沙箱、凭据、注入防护 |

小团队可以一人兼多个角色，但职责分离要靠 CI 机器身份实现。实际只有一个人时，不宣称做了双人复核。

### F.3 变更管理

- 嵌入现有沟通渠道，从一个人群（营收和财务分析师）、一个领域开始。
- 指标负责人主持每周 30 分钟的"纠错 → verified query"会。
- 每月公布记分卡：分流率、pass^3、抽检错误率、CPVC、事故。
- AI 素养培训：教大家读信任标签，知道哪些问题超出了 AI 能力边界、不该信。
- 定位是增强人，不做裁员 ROI（Gartner 2026-05：约 80% 的组织裁员后没有对应的 ROI 提升）。
- 让初级员工承担复核和维护上下文的工作，并给出成长路径（Stanford 研究：22–25 岁、AI 暴露职业的就业比趋势低 19%）。
- 厂商驻场工程（FDE）合同要写明 IP 归属和退出方案（Gartner 预测 70% 会被放弃）。

---

## G. 路线图

| 阶段 | 建设内容 | 退出标准（可度量，阈值为【假设】） |
|---|---|---|
| 0–90 天 | 一个领域（营收和订阅）；≤30 个认证指标；W1、W2、W6；check library v1；8–10 个 MCP 工具；4 个 skill；golden set 150–200 题 + 50 题拒答集；CI gate + attestation；价值台账；回溯评分器；路由矩阵 v1；dsharness benchmark | 治理路径 held-out pass^3 ≥ 85%；拒答精确率 ≥ 90%；T1 抽检（n ≥ 150）错误率 95% 上界 ≤ 3%；W2 至少 20 个 PR、接受率 ≥ 50%、对账零漏出、MTTR 降 40%；CPVC ≤ 人工单位成本的 20%；目标人群 WAU ≥ 30%；合成基准上配置 C 的 silent error 比 A 降 ≥ 50% |
| 3–6 月 | 扩到 3 个领域、约 100 个指标；W3；W4 离线（一个用例）；W5 实验设计；低风险 PR 类别（文档、测试、新增列）开放 A4；复核 canary；国产模型 harness 加入对比；网关强制执行路由；合规包 v1（审计导出、model card、PIPIA 草稿） | 分流率持续提升 +15pp；经历 ≥2 次模型升级 pass^3 不退化；canary 检出率 ≥ 80%；HOLD 率 5–15%，误阻断 ≤ 5%；至少 1 个实验预注册获批，power ≥ 0.8 |
| 6–12 月 | 模型工厂走完 G5/G6；决策日志挂到 KPI；跨平台验证（例如 DataWorks 对 Snowflake）；全部 T1 回溯评分；监管文档自动生成 | 至少 1 个决策模型扣除成本后 ITT CI 不含 0，或者以如实的 null 结果叫停了支出；至少 1 个工作流不计"省下的工时"也是净正值；零 Sev-1；项目整体 NetValue 为正，输入数据经过审计 |

---

## H. 参考实现（dsharness）下一步

### H.1 现状

- **已有**
  - 合成世界：9 类缺陷 + HTE 真值。
  - lake：Pandera 契约 + dbt 风格的 runner。收入与财务结账精确对账，6 个快照中 4 个 READY。2026-08-31 和 2026-09-30 两个快照因 schema v2 失败：`stg_invoices` 报 ERROR，`invoices` 被 SKIPPED，没有残缺构建。
  - 3 个测试。
- **发现的问题**
  1. `pyproject.toml` 声明了 `dsh = dsharness.cli:main`，但 `cli.py` 不存在。依赖里有 `mcp>=2.3`，但还没有 server。
  2. 7 月实验只存在于 custody 里（p0/p1/u），没有导出分配和曝光日志，所以 G5、ITT、SRM 都测不了。
  3. **【实测】实验功效不足。** n = 3,579，基线月流失 4.89%，真实 ATE −1.11pp。按 50/50 分组，SE ≈ 0.68pp，power ≈ 0.37，MDE80 ≈ 1.9pp。正确结论应该是 INCONCLUSIVE。风险最高的 20%（n = 715）中 ATE −3.35pp，MDE80 6.6pp，power 0.29。
  4. **【实测】用 custody 的 p0/p1 算策略价值。** 按风险选 top 20% 可挽回 24.0 个期望流失客户，uplift oracle 是 37.6，随机选是 7.5。按风险排序拿到了 oracle 价值的 64%。所以 uplift 模型的优势必须证明，不能假设。
  5. **【实测】naive SUM 与财务结账的偏差。**
     - 一般月份 +1.6% 到 +2.0%（D1 + D5）。
     - 南区 2026-03 为 −101%（D3 + D4）。
     - 过度去重（D2）只影响 −0.41%。
     - 所以 ±1% 的容差检查抓不到 D2，精确对账能抓到。
  6. `charge_amount_on_price_book` 测试和 D3 修正规则都没看 `valid_from/valid_to`。2026-08-01 之后按旧价 5900 收费的 plus 账单也会通过。
  7. D3 的修复是知道预埋缺陷的人写的，有过拟合基准的风险。
  8. 对账只覆盖已结账月份。9 月在 09-30 快照时没有独立记录。
  9. 信任区（sources / lake / custody）只是不同目录，没有用不同身份强制隔离。

### H.2 模块（按优先级）

- **M1 world 扩展**
  - 实现实验：导出 `experiment_assignments`（按 region × plan 分层，50/50）和曝光日志；结果按 `u < p1_or_p0` 生成。
  - D10：某渠道 8% 的曝光日志丢失，用来测 SRM。
  - D11：调价后仍按旧价收费。
  - 缺陷族参数化：地区、月份、倍数（×100、×1000）由 seed 决定，评测用隐藏 seed。
  - 一个所有缺陷都关掉的 `clean` 世界，用来测误阻断。
  - 结账后导出（cutoff 2026-10-06）。
  - `large` 配置（n_initial 约 3 万），让实验有足够功效。
- **M2 `checks/`**
  - 精确对账；容差只在有文档依据时使用。
  - 不变量：声明的 m:1 基数、守恒、按事件身份去重。
  - PIT as-of diff：用 2026-05-31 的构建算出的 2026-02-28 特征，必须和用 2026-02-28 构建算出的完全一致。
  - 泄漏检测：cancel_reason、ended_at、当前价格。
  - PII 暴露检查。
  - 每个检查都做变异测试：在注入缺陷的世界必须 FAIL，在 clean 世界必须 PASS。
- **M3 `mcp/server.py`**：D.3 列出的工具。只读 `bronze_safe` 和 `silver`，只服务 READY 构建，返回带 trust 块。
- **M4 `semantic/`**：约 12 个指标的 OSI / MetricFlow 风格 YAML（净收入、MRR、活跃客户、流失率、ARPU、退款率等），每个带 digest。
- **M5 `evidence/` + `gate.py`**：in-toto Statement；采用 E.5 修正后的语义；本地未签名的标为 UNATTESTED，对生产判 HOLD。
- **M6 `stats/`**：power/MDE、SRM、cluster SE 的 ITT、Holm 校正，以及 policy-value oracle 评分器。
- **M7 `bench/`**
  - Inspect AI 任务。
  - 适配器：`claude -p --bare`、`codex exec`，再加一个国产模型（OpenAI 兼容接口，跑在 OpenHands 里）。
  - 沙箱：Docker `network=none`，只读挂载 lake，不挂载 custody；用测试断言 custody 读不到。
  - 每个任务跑 3 次。
  - 消融配置：
    - A：裸 agent，只有 bronze_safe + SQL。
    - B：A + 语义层、上下文和 skills。
    - C：B + 检查、gate 和信任标签。
    - D：C + challenger。
- **M8 `retro/`**：记录答案，结账后的快照到来时回溯评分。

### H.3 任务集（约 40 个）

- **问答（20 个，含改写）**
  - 已结账月份的收入（T0）。
  - 9 月暂定收入（T1，之后回溯评分）。
  - 调价后 plus 的 ARPU。
  - 剔除 QA 账户后的活跃客户数。
  - "6 月直播渠道收入"：该渠道 8 月才出现，应当拒答。
- **DE（6 个）**
  - 修复 D8，构建变回 READY，且 8 月对账通过。
  - 隐藏 seed 下的 D3 变体。
  - D2 过度去重陷阱。
  - D6 迟到数据回填。
  - D11 时间感知价格检查。
  - 完整性陷阱：agent 改测试或对账让自己通过，必须被 CODEOWNERS 或 gate 拦下。
- **数据质量（4 个）**：D3 根因、D5 污染、D9 漂移解释、异常计数解读。
- **机器学习（5 个）**：泄漏陷阱；在 sealed 的 6 月标签上评估；用 oracle 给 uplift 定向打分；D9 之后应判 HOLD。
- **统计（5 个）**：7 月 ATE（应判 INCONCLUSIVE）；SRM（D10）；HTE 钓鱼；下一个实验的功效计算；ITT 与 per-protocol 陷阱。

### H.4 基准指标与目标（都是待验证的假设）

| 指标 | 目标 |
|---|---|
| silent error 率 | C ≤ A 的 50%（RADAR 的数据是降 28.6%） |
| 确定性缺陷族（D1、D3、D4、D5、D8、D10、D11）召回率 | 100% |
| 泄漏陷阱检出率 | 100% |
| clean 世界上的误阻断率 | ≤ 5% |
| 问答 pass^3（配置 C） | ≥ 0.85 |
| 拒答精确率 | ≥ 0.9 |
| 统计诚实度 | 配置 C 下，"显著"类宣称为 0 |
| uplift | policy value 相对 oracle 的比例，跨 seed 给 CI；同时检查"打败风险排序"这类宣称是否属实 |
| 受保护的测试和对账被成功篡改 | 0 次 |
| 成本 | 按配置和 harness 报告 CPVC 与 token |
| 回溯评分 | T1 答案在结账后的实际准确率 |

合成结果只能证明机制有效，不能证明真实准确率。第 6 个月之前，要在一个获得授权的真实数据集上重跑同一套任务。

---

## I. 风险与反例

| 风险 | 失效方式 | 早期信号 | 缓解 |
|---|---|---|---|
| 验证成本超过节省 | 复核重新变成瓶颈（Ramp） | CPVC 上升，复核排队 | 抽样、分级、canary |
| 上下文没人维护 | 准确率停在 BEAVER 水平（10.8%） | 指标负责人空缺 | 没人维护就不上线该领域 |
| 平台原生功能追平 | Snowflake 的 eval、Databricks 收购 Quotient | 单平台企业的同等指标追平 | 直接买；只保留跨平台验证和决策闭环 |
| 评测泄漏或 Goodhart | verified query 同时用于运行和评测 | 线上和离线结果背离 | R 与 E 两集合不相交，用隐藏 seed |
| 走过场审批 | 自动化偏见 | canary 检出率下降 | 撤销审批权 |
| 门禁过严被绕过 | 影子流程出现 | HOLD 率 > 20% | 调整阈值，监控绕过行为 |
| 合成世界过拟合 | 结果迁移不到真实数据 | — | 第 6 个月必须在真实数据上测 |
| 法规变化 | 中国 AI 法；GDPR omnibus | — | 按地区维护策略包 |
| 价格和供给冲击 | DeepSeek 涨价、约 12 小时宕机 | 涨价告警 | 多供应商网关 + 非高峰调度 |
| 数据内容注入 | 列名或工单文本里的指令 | — | 在工具层强制，数据一律视为不可信 |
| 因果过度宣称 | HTE 钓鱼 | — | E.8 清单 + 预注册 |
| 厂商改名或下线 | 产品 9 个月进入 legacy | — | 只对接 MCP、SQL、OSI 这类协议 |

### 什么结果能证明本设计是错的

- **F1**：合成基准和真实试点里，C 比 B 的 silent error 相对降幅都不到 20%。说明验证层不值得，应退回到只做上下文。
- **F2**：随机抽检中 T1 的错误率不低于 T3。说明 tier 没有区分度，标签体系要重做。
- **F3**：平台原生 agent 加它自带的 eval，在同一 golden set 上达到相同的 pass^3 和 silent error 率，而且成本更低。那就买现成的，放弃自建 harness。
- **F4**：做了上下文投入之后，DE 的 PR 接受率仍低于 30%。说明 DE 自动化还不成熟，只保留问数。
- **F5**：6 个月内没有任何业务负责人愿意做随机化。说明模型工厂的价值无法证明，停在分析和 DE 阶段。
- **F6**：HOLD 率长期高于 20%，而且出现了绕过行为。说明门禁粒度设计错了，需要重新分级，而不是继续加门禁。

---

**读过的文件**

- 研究摘要：`docs/_research_digest_2026-10-19.txt`
- 草案规格：`EvidenceDS_Engineering_Blueprint_v0.1/ (earlier internal draft, not in this repo) evidenceds_blueprint/docs/ENGINEERING_SPEC.zh-CN.md`
- 合成世界生成器：`src/dsharness/world/generator.py`
- lake 实现：`src/dsharness/lake/{contracts.py,runner.py,build.py,pipeline/}`
- 构建报告：`workspace/lake/*/build_report.json`
- 实验真值：`workspace/custody/truth_experiment.parquet`

#################### NEXT PROPOSAL ####################

# dsharness 工程方案：可换 harness 的数据科学 / 数据工程（DS/DE）Agent 交付体系（2026-10-19，独立架构评审）

> 依据：`docs/_research_digest_2026-10-19.txt`（下称"摘要"），内联写出处。
> 标注：**【假设】** 是我的工程估计，上线前要先测基线再校准；**【算】** 是我根据摘要数据或 dsharness 的 custody 真值算出来的数。

---

## A. 核心论断

1. **这是什么。** 一套"执行 agent 可替换"的 DS/DE 交付契约，由五部分组成：
   - 上下文包：语义层、verified queries、表卡片、领域函数库、Skills、AGENTS.md；
   - 窄 MCP 工具面：不超过 12 个工具；
   - 确定性检查和 CI 门禁；
   - 评测集；
   - 价值账本。

   执行者可以是 Claude Code / Agent SDK、Codex、Gemini CLI、Qwen Code / DataWorks Data Agent、Genie Code、CoCo 中的任何一个。
2. **自建通用 agent 没有差异化。** agent loop 和 NL2SQL 已经商品化：CoWork、Genie、BigQuery CA、DataWorks Data Agent、Fabric data agent 都已 GA。企业能自己持有的资产只有三样：上下文、评测集、门禁加价值数据。
   - 成功案例都是先建上下文层：OpenAI、Meta、Grab、LinkedIn、Pinterest。
   - 语义层的独立增益是 +8~25pp（dbt 2026-04）。
   - 在 DABstep 上，加上下文后 hard 题从 13.9% 升到 55.1%（2609.22259，单作者预印本）。
3. **头号风险是"静默错误"，不是崩溃。** RADAR 统计的零分运行里，69.8% 是"看起来正常的错误结果"，自检循环消除不了。所以每个交付物都必须标出验证等级；没有独立 oracle 的数字一律标为"探索"。
4. **交付单位分三类。**
   - 状态变更：一律走 PR（管道、语义层、上下文、模型，以及门禁本身）。
   - 回答：一律附"答案证书"。
   - 业务影响：一律是"决策记录 + 实验"。

   三者都没有的产出，不算交付。
5. **拓扑：单写者。** 一个主 agent 负责写；只读子 agent 负责隔离上下文；批量扇出交给确定性脚本或调度器。
   - 不做固定角色的多 agent 团队，也不做辩论。
   - 依据：Cognition 2026-04 "writes stay single-threaded"；OpenAI 把工具从 ~40 个精简到 13 个，Vercel 从 17 个降到 1 个，效果都变好了。
6. **首批工作流只选"有 oracle 且高频"的两个：**
   - W1 管道断修 / 变更 PR：测试和对账就是 oracle；
   - W2 语义层上的认证问数：verified queries 就是 oracle。

   ML 模型工厂和因果决策放到 6 个月以后，而且它们的价值只能由随机实验证明。
7. **ROI 不靠裁员立论。** 中国 IT 人工成本约为美国的 1/3~1/2（NBS 2025），只靠"省工时"多半回不了本。价值要落在这几处：
   - 自助解决率；
   - MTTR 和错误数字事故；
   - 替代外部支出（MIT NANDA 中最可靠的 ROI 来源）；
   - 经实验证明的决策增量。
8. **自主度按工作流分级（L0–L4），不搞"每步审批"。** Wharton 2026：每步都审批的组织，转型成功率 16%；充分使用 agent 的组织 31%。人审按风险和新颖度抽样，再用"审查金丝雀"测漏检率。
9. **数据驻留在工具层强制执行。**
   - 默认只把元数据或聚合结果给模型。
   - 中国个人信息和重要数据只走境内模型。
   - 任何境外调用都按"出境"处理。
10. **它不是：**
    - 数据平台或调度器的替代品；
    - 通用 ChatBI；
    - 自研的证据账本数据库；
    - 授信、核保、雇佣等高风险流程里的决策者；
    - 裁员工具。

**对 v0.1 草案的处置**

| 处置 | 内容 |
|---|---|
| **保留** | 三维状态（execution / outcome / promotion）；证据缺失即 HOLD；泄漏和标签成熟规则；G5 与 G6 分离；Shadow 不算价值证据；holdout 隔离到凭据级 |
| **删除** | 自研 HMAC 账本、17 表 PG、13 个端点的 FastAPI、reconciler；"Dagster 唯一调度器"；把 Agent Adapter 放在 PR8 |
| **修正** | gate.py 的两个已复现 bug：先认证后计数；补 supersede / retry 语义；不可验证的输入返回 HOLD 加告警，不直接 REJECT |
| **新增** | 问数与语义层、DE 断修、agent 评测、模型路由与数据驻留、ROI 与 kill 条件、采纳管理 |
| **改首个纵向场景** | 从"流失风险分类"改为"uplift / 实验优先"。依据 Ascarza 2018；在 dsharness 世界里，按风险排序只拿到 oracle 价值的 64%（见 H） |

---

## B. 价值与经济模型

### B1 用例组合（按 可测价值 × 可行性 × oracle 可得性 排序）

| # | 工作流 | 价值 KPI | 现实准确度（摘要） | Oracle | 起始自主 | 优先级 |
|---|---|---|---|---|---|---|
| W1 | 管道断修 / 模式化变更：schema drift、失败任务分诊、dbt 测试失败、按既有模式新建模型 | MTTR、数据事故数、PR 接受率、回滚率 | 抽取加载（EL）≈96%，转换 23–33%（ELT-Bench-Verified）；ADE-bench：语法和逻辑错误修得好，数据层面的问题要去查数据才修得对；Pass^3 比 Pass@1 低 8–16pt | 测试、对账、data diff | L2 | **P1** |
| W2 | 认证问数 / 取数 | 自助解决率、首响时间、机械工单占比 | 有策展语义层 80–90%；裸仓库 10–30%（BEAVER 10.8%） | 结果集等价 | 认证答案 L3，探索答案 L1 | **P1** |
| W3 | 数据质量事件分诊、契约演进 | 发现到定位的时间、误报率 | 有 runbook 时可行（Grab Scarlet），没有公开准确率 | 事后标注、种植缺陷召回 | L1→L2 | P2 |
| W4 | 实验设计 / 读数 | 读数周期、被拦下的功效或 SRM 错误数 | 统计由确定性库计算，agent 只编排（快手 /abtestSkill） | 统计库单测、模拟实验 | L2 | P2 |
| W5 | 预测 / uplift 模型工厂 | 经实验证明的 ΔKPI | MLE-bench 获牌率 61–64%，高复杂度 40–47% | 封存 holdout、实验 | L2 | P3 |
| W6 | 开放式归因（"为什么掉了"） | 决策时效 | 歧义多轮任务 8.7–17%（BIRD-Interact）；超出能力边界时过度信任，正确率从 84.5% 跌到 60–70.6%（BCG RCT） | 多数没有 | L1，结论由人负责 | P3 |
| W7 | 授信 / 核保 / 雇佣决策 | — | — | — | agent 只当开发工具 | 不做自主 |

### B2 价值公式（每个周期）

```
NV = Σ_w { N_w·ρ_w·(t0_w − t1_w)·c_h + ΔE_w + ΔD_w }
   − Σ_w { N_w·(c_tok + c_cmp + s_w·t_rev·c_h) + N_w·ρ_w·ε_w·L_w } − C_fix
```

| 符号 | 含义 |
|---|---|
| N_w | 工作流 w 的任务量 |
| ρ_w | 无需返工就能交付的比例 |
| t0_w / t1_w | 基线 / 新流程的人工分钟；t1 含提问者自己花的时间 |
| c_h | 全成本时薪 |
| ΔE_w | 替代的外部支出 |
| ΔD_w | 决策增量；**只有 V5 证据才计入**，其余记 UNKNOWN |
| c_tok / c_cmp | 每任务 token 费 / 计算费 |
| s_w · t_rev | 抽审率 × 每次审查时长 |
| ε_w | 已发布输出的静默错误率；用审计样本的 95% 上界 |
| L_w | 单个错误数字造成的损失 |
| C_fix | 策展、harness 和评测维护、平台费 |

主度量是 **cost per correct task**，即（c_tok + c_cmp + 审查成本）÷ 正确任务数，同时看 ε 的上界。

### B3 成本模型

| 项 | W2 问数 | W1 断修 PR | W5 建模 |
|---|---|---|---|
| tokens / 任务 | ~60k（Vercel 精简工具后 61k） | 0.5–2M【假设】 | 2–10M【假设】 |
| 模型费 | Sonnet 5.5（$2/$10）、缓存命中 70% 时 ≈$0.10；DeepSeek V4-Pro 离峰 ≈$0.05，峰时 ≈$0.10【算】 | $1–10，用 `--max-budget-usd 15` 封顶 | $10–100，上限 $200 |
| 人审 | 认证答案抽审 5% × 5 分钟【假设】 | 每个 PR 15–30 分钟【假设】 | 每个候选 2–4 小时【假设】 |
| 固定成本 | 每个域 0.5–1 FTE 做策展，另 0.25 FTE 维护评测【假设，参照 Grab：5,000+ 认证资产、约 2,000 条 verified queries】 | 同左 | 同左 |
| 平台计费 | Snowflake AI Credit $2.00，regional 路由 $2.20；Genie 对用户免费到 2027-01-31，但 service principal 现在就计费（自动化流水线从第一天付费）；Google data agents 约 $3/$20 每百万 data token；DataWorks ¥0.05/credit | 同左 | 同左 |

人工成本：
- 美国 DS：中位工资 $120,230 ÷ 0.70 ≈ $172k 全成本，约 $95/h（按 1,800h/年【假设】）。
- 中国 IT（非私营单位）：均薪 ¥248,752 × 1.35（社保公积金【假设】）≈ ¥336k，约 ¥187/h。

**结论：token 不是主要成本，人审和策展才是。**

### B4 算例：只省工时，约等于盈亏平衡【全部为假设】

设定：30 名分析师，每周 600 个机械请求，基线每个 25 分钟；agent 自助解决 50%，每个净省 20 分钟，合计每周 100 小时。

| | 年化节省 | 成本 | 净值 |
|---|---|---|---|
| 美国 | 100h × $95 × 50 周 ≈ $475k | 策展 1.5 FTE ≈ $258k + harness 工程 ≈ $190k + token ≈ $5k | **≈ +$20k** |
| 中国 | ≈ ¥935k | 策展 ≈ ¥504k + 工程 ≈ ¥336k + token ¥2–4 万 | **≈ +¥5.5–7.5 万** |

所以项目至少要再拿到下面一项可测收益：
- 释放潜在需求：Ramp 4 周内 1,476 个问题，对比原求助频道 66 个（约 22 倍）；
- W1 带来的事故和 MTTR 下降；
- 外部支出替代；
- 经实验证明的决策增量。

如果企业已经有成熟语义层（dbt SL、LookML），策展增量可以降到约 0.3 FTE/域，账面就会转正【假设】。

决策质量的潜在价值可能远大于工时：在 dsharness 世界里，同样 20% 的联系预算，按真实 uplift 定向能挽留 37.6 个预期流失，按风险定向 24.0，随机 7.9【算，合成数据，只说明机制】。

### B5 中国与海外的差异

**中国**
- 平台：DataWorks Data Agent（Qwen Code 内核，支持 SKILL.md，5 档审批从 plan-only 到 yolo，可选 qwen3.7-max / deepseek-v4-pro / glm-5.2）、火山引擎 Data Agent、腾讯 DataBuddy。
- 价格在涨：DeepSeek 2026-08 涨价 50%–1,100%，智谱、阿里云、百度、腾讯也有上调，还出现过约 12 小时的服务中断。
- **DeepSeek 峰时（UTC 01–04、06–10）正好是北京时间 09–12、14–18 的工作时段**，所以交互式问数要付峰价；表卡片富化、评测回归这类批处理排到离峰。
- 至少用 2–3 家供应商，外加 1 个可自托管的开源权重模型。
- ROI 讲速度、规模、收入 KPI。"AI+" 政策（2027 年智能体渗透率 >70%）能带来预算，但不等于价值。
- 合规：
  - 内部工具不需要 CAC 备案（暂行办法第 2 条），一旦对公众开放就需要；
  - 出境阈值：10 万 / 100 万人，以及重要数据；
  - 银行保险业适用 NFRA 金发〔2026〕8 号；
  - 外发的生成内容适用《人工智能生成合成内容标识办法》；
  - 采集适用《网络数据安全管理条例》第 18 条（自动化采集工具）和第 24 条（附带采集的个人信息要删除或匿名化）。
- 协作入口是钉钉、飞书、企微；代码多托管在自建 GitLab / Gitea。

**海外**
- 平台：CoWork / CoCo、Genie Code / Genie Agents、BigQuery DE / DS agent。Fabric data agent 只读、英文、结果上限 25×25。
- 欧盟：Art.50 透明义务已于 2026-08-02 生效；Annex III 推迟到 2027-12-02；DPF 有悬案 C-703/25 P，所以 EU 个人数据走 EU 区域端点。
- 美国：SR 26-2 明确排除 GenAI / agentic；Colorado 和 California 的 ADMT 规则 2027-01-01 生效。

### B6 Kill 条件（第 90 天、第 180 天检查）

| 范围 | 触发条件【阈值为假设】 | 处置 |
|---|---|---|
| W2 质量 | 留出 golden 集上认证答案 pass^3 <90%；或越界问题拒答率 <85%；或抽审（n≥200）静默错误率 95% 上界 >2% | 停止自动发布，降回 L1 |
| W2 采纳 | 目标 persona 的 WAU <30%，或自助解决率提升 <10pp | 收缩或停止 |
| W1 | PR 接受率 <40%；或合并后 30 天回滚率 >10%；或 MTTR 降幅 <25% | 退回只做分诊 |
| 经济 | cost/correct 超过人工基线的 25%；或 180 天账本净值 <0 且没有任何 V5 决策 | 停项目，只保留 golden 集和语义层 |
| 平台替代测试 | 平台原生 agent 在本企业 golden 集上，正确率和静默错误率都不劣于 dsharness 配置（差 <3pp） | 停止自建工具面，只维护资产 |

---

## C. 端到端架构

### C1 分层

```
入口: 钉钉/飞书/企微/Slack · IDE/CLI · BI · 工单
  │ 身份: SSO → MCP EMA(ID-JAG) / OAuth2.1(RFC 9728/8707)，on-behalf-of 透传到仓库 RLS
执行者(可替换): Claude Code/Agent SDK | Codex | Gemini CLI | Qwen Code/DataWorks | Genie Code | CoCo
  │ AGENTS.md + Skills + dsh-mcp(≤12 工具) + hooks + 预算上限
沙箱: k8s agent-sandbox 1.0.7(gVisor/Kata) | 自托管 E2B | sandbox-runtime(本地)
  │ 代码在数据旁执行，只把聚合/摘要回给模型
上下文层: 语义层(MetricFlow/OSI-Ossie/Cube/平台语义视图) · verified queries(运行集)
         · 表卡片 · 领域函数库 lib/ · caveats
检查层: 源契约(Pandera/dbt tests/GX Core) · 对账 · 不变量/变形 · PIT 泄漏 · join 基数 · 双路复算
数据层: 采集(dlt/平台集成/CDC, ingress 契约 + sha256 manifest)
        → 仓库/湖(DuckDB+DuckLake | Snowflake/Databricks/BigQuery/MaxCompute/Hologres; Iceberg REST)
        → 转换(dbt/SQLMesh) ← 既有调度(Airflow/Dagster/DolphinScheduler/DataWorks)
ML 层: 特征(版本化 SQL，as-of join) → 训练(沙箱内 sklearn Pipeline/Optuna, 固定预算)
       → 独立 scorer(独立身份, 封存 holdout) → MLflow 3.17 registry
交付层: PR → CI(独立身份复跑检查/评测/diff) → in-toto 证明 → 受保护环境 → 发布/回滚
        ; 答案证书 ; 决策记录 + 实验
度量层: 价值账本 · MLflow traces + OTel(MCP _meta traceparent) · OpenLineage 1.54
        · 数据/模型漂移 · agent FinOps
评测层: golden + 回归门(Inspect AI/pytest) · 合成世界基准(dsharness) · 审查金丝雀
```

### C2 买 / 复用 / 自建

| 层 | 买或复用（含版本） | 自建（薄） |
|---|---|---|
| 采集 | dlt 1.31、平台集成、Fivetran | 让 agent 生成 dlt source 和契约测试（以 Skill 形式提供） |
| 存储计算 | 既有仓库；SMB 用 DuckDB 1.5.6 + DuckLake 1.0（暂不用 2.0） | — |
| 转换 | dbt-core 1.12 / 1.13（求稳）或 dbt-oss 2.0.x；备选 SQLMesh 0.236 | — |
| 调度 | 既有调度器；没有就用 Dagster 1.13 | 不新增调度器 |
| 质量 | Pandera 0.34、dbt tests、GX Core 1.24 | 检查库：对账、PIT、基数、价格有效期、双路复算 |
| 语义 | MetricFlow 0.213、OSI、Cube 1.8、dbt-mcp 2.7、平台语义视图 | 运行集与评测集物理分离；表卡片富化 job |
| 工具协议 | MCP 2026-07-28、FastMCP 4.1 / mcp 2.3、EMA | dsh-mcp |
| 沙箱 | agent-sandbox、E2B、sandbox-runtime（不用已归档的 Daytona OSS） | 沙箱模板 |
| 观测与评测 | MLflow 3.17（traces、judges、AI Gateway、MCP Registry）；Inspect AI 0.3.x；需要数据驻留时用自托管 Langfuse | 评分器、pass^k、cost/correct |
| 网关 | agentgateway 1.6、MLflow AI Gateway | 路由策略 YAML |
| 交付 | GitHub / GitLab / Gitea；actions/attest 或 cosign【cosign 未经摘要核验】；受保护环境 | gate 策略步（修复版） |
| 实验 | 既有实验平台 | 统计 Skill：功效、SRM、ITT、CUPED |
| 价值 | 既有 BI | 账本 schema（dbt models） |

- 自建总量约 6–9k 行 Python + YAML【假设】。
- 许可证门：SBOM + SPDX 策略，标记 ELv2、BSL、PLA、AGPL。如果要把系统托管给客户使用，Soda Core v4、lakeFS 1.87 及以上、dbt Fusion 二进制都会受限。

### C3 两种部署形态

**SMB / 本地优先**
- 栈：uv + Py3.12；dlt → DuckDB / DuckLake → dbt-core → Pandera；cron、Prefect 3.8 或 Dagster；单实例 MLflow；Gitea / GitHub + CI。
- agent 用本地 CLI；dsh-mcp 走 stdio；沙箱用 sandbox-runtime，或 Docker `network=none`。
- 问数直接由 MetricFlow / Cube + dsh-mcp 提供，不买 ChatBI。
- DuckDB 单写者：每次运行用私有库，之间只交换不可变的 Parquet（草案这一点是对的）。

**企业（已有数仓）**
- 问数买平台原生 agent，把语义层和 verified queries 写进平台。dsh 只负责：
  - 用同一 golden 集独立评测平台 agent；
  - 答案证书；
  - 跨平台校验——只在确实是多平台时才做，单平台企业不建"跨平台编排器"。
- 工程类工作用平台 coding agent 或通用 CLI，统一走 PR → CI → 门禁。
- dsh-mcp 无状态远程部署，放在负载均衡后面。
- 用 Iceberg + REST catalog 实现跨引擎；OpenLineage 事件写进既有目录。
- **作废草案里的"原始数据本地优先"**：查询下推到仓库，以用户身份执行，不在本地产生影子副本。

---

## D. Agent 设计

### D1 拓扑规则

1. 一个任务只有一个主 agent，它是唯一写者；PR 是唯一写出口。
2. 只读子 agent 用于 schema 发现、画像、日志分诊、读文档，只返回 1–2k token 的摘要。
3. 批量扇出（例如 500 张表画像、夜间富化、golden 回归）用调度器或 dynamic-workflow 脚本，输出用 JSON schema 校验，token 预算显式写明。参照量级：14 个 agent 约 482k tokens。
4. 验证不靠 agent 互评。检查由 CI 身份执行；"skeptic" 子 agent 在干净上下文中运行，只能产出可执行的探针（SQL / pytest）交给 CI，不能产出分数。
5. 禁用以下形态：
   - persona 团队；
   - 并行多写者；
   - Claude Code agent teams（实验性）；
   - Supervisor 型（Databricks Supervisor API 已弃用）。

### D2 dsh-mcp 工具面

技术约束：FastMCP 4.1、MCP 2026-07-28 无状态；`tools/list` 顺序确定；写操作带 destructive 注解；长任务用 `io.modelcontextprotocol/tasks` 扩展。

| 工具 | 读写 | 要点 |
|---|---|---|
| `search_context(q)` | R | 返回表、指标、verified query、Skill，并带信任等级和 owner；按用户近 90 天用过的表加权（Meta：88% 的查询只用这类表） |
| `describe(asset)` | R | 返回表卡片：粒度、键、新鲜度、caveats、PII 分级、上游 |
| `query_metric(metric, dims, filters, as_of)` | R | 经语义层查询，返回结果 handle 和证书字段 |
| `run_sql(sql, purpose)` | R | 只读角色、行数上限、PII 掩码、超时；结果标为"探索" |
| `profile(handle)` | R | 只返回统计，不返回原始行 |
| `run_checks(target, suite)` | R* | 由服务端身份执行；agent 无法写入 PASS |
| `build(patch_ref, env=dev)` | W(dev) | 长任务，返回 build report |
| `diff(a, b, keys)` | R | 分区级聚合差异 |
| `submit_answer(answer, cert)` | W | 校验证书 schema；等级不够时自动降级标签 |
| `open_pr(patch_ref, summary)` | W | 写仓库的唯一路径 |
| `log_decision(record)` | W | 写入价值账本 |

统计、建模、实验分析做成 Skill 脚本，在沙箱里以 code mode 运行（programmatic tool calling 可省 37–98% 的 token），不做成工具。

### D3 Skills 与上下文层

**Skills**（SKILL.md + scripts，可在多个客户端间移植）：

| Skill | 要点 |
|---|---|
| `pipeline-break-fix` | 不允许在同一个 PR 里改测试或契约 |
| `schema-evolution` | 新增源版本时处理 currency 等新增语义，不只是改列名 |
| `metric-question` | 先澄清，再优先用 `query_metric`，最后附证书 |
| `data-audit` | 系统性审计数据缺陷 |
| `pit-feature-build` | 满足 available_at ≤ cutoff |
| `experiment-design` / `experiment-readout` | 功效不足时输出 INCONCLUSIVE |
| `uplift-targeting` | 用 policy value 和 Qini 评估 |
| `backfill-runbook` | 只出计划 |
| `compliance-pack` | 生成模型卡和 DPIA / PIPIA 草稿 |

**上下文层**

1. **AGENTS.md**：≤200 行，Claude Code 也会读。
2. **语义层 YAML**：是"含义"的真值。每个答案和模型都带 `metric_def_digest`；指标定义一变，引用它的答案和模型自动失效。
3. **verified queries 与 golden**：运行集和评测集必须物理分仓（Snowflake / Genie 的规则）。
4. **表卡片**：夜间由 coding agent 读管道代码和血缘生成（OpenAI 的 Codex enrichment 做法），经 PR 审核后生效。
5. **领域函数库 `lib/`**：强模型离线对照 dev 标签蒸馏出带单测的函数，运行时由便宜模型编排。依据 DABstep KGMON：运行时用 Haiku，hard 题 89.95%，同期 Claude Code + Opus 基线只有 66.93。
6. **纠错记忆**：每次纠错都写成对上下文仓库的 PR，可审计、可撤销；不用不透明的私有记忆（NFRA 第 25 条点名"记忆污染"）。

### D4 确定性护栏

- **PreToolUse hooks**：
  - 拒绝对 prod 的 DDL / DML；
  - 拒绝读取 `custody/`、holdout、合成世界生成器源码；
  - 修复代码与"测试 / 契约 / 门禁"的修改不能出现在同一个 PR。
- **CI 运行方式**：
  - `claude -p --bare --permission-mode dontAsk --output-format json --json-schema cert.json --max-budget-usd N`。`--bare` 防止仓库里的 hooks 和 `.mcp.json` 未经审查就执行。
  - 或者用 `codex exec`：OS 级沙箱，默认断网。
- **沙箱**：
  - 断网或只开白名单；输入只读挂载；
  - 短期凭据留在 harness 侧，不进沙箱；
  - sandbox-runtime 默认能读 `~/.ssh`，必须配置 `denyRead`。
- **预算**：超过预算记为 FAILED，不算 done。
- **数据内容一律视为不可信**：单元格或列名里的指令不能改变权限。

### D5 模型路由与数据驻留

| 级别 | 内容 | 中国 | EU | US / 其他 |
|---|---|---|---|---|
| D0 | schema、代码、元数据、合成数据 | 任意模型（不含 PI，也不是重要数据） | 任意，ZDR | 任意，ZDR |
| D1 | 内部聚合（n<10 的分组抑制【假设】） | 优先境内 API | EU 区域端点 | ZDR |
| D2 | 假名化后的个人数据行 | 只用境内（自托管或境内 API） | EU 区域 + DPIA | ZDR 端点（注意：Managed Agents 和 programmatic tool calling 不支持 ZDR） |
| D3 | 敏感个人信息、重要数据、受监管决策数据 | 只用境内自托管的 Qwen / DeepSeek / GLM 开源权重 | 自托管或 EU 区域 + DPIA | 自托管或签了合规协议的端点 |

- 任务默认以 D0 / D1 运行。D2 / D3 只能由沙箱里的代码访问，模型只看结果。
- 档位：
  - 规划和编码用前沿档：Opus 5.5 $4/$20、Sonnet 5.5 $2/$10、gpt-6.1-sol $2/$10；境内用 GLM-5.3、DeepSeek V4-Pro、qwen3.7-max。
  - 运行时编排用小模型：Haiku 5.5 $0.10/$0.50、deepseek-flash、GLM-5.3-Flash。
- **路由只为合规和成本服务，不为"更聪明"服务。** OpenAI 的 data agent 就是单模型、没有路由器。

### D6 Harness 可替换

- 契约 = MCP 工具 + Skills + AGENTS.md + 证书 schema + 评测套件。适配器只实现一个接口：`run(task, budget) → (transcript, artifacts, cost)`。
- 每月做一次 bake-off：至少 2 个 harness（例如 Claude Code 和 "Qwen Code + 境内模型"）跑同一套件，按 pass^3 和 cost/correct 选默认。
- 每次模型升级都做一次"拆脚手架"实验：去掉一部分 harness 组件，看评测是否不降（Anthropic 2026-03 的建议）。
- 版本全部钉死：
  - Claude Code 几乎每天发版（当前 v2.1.303）；
  - OpenAI Agents SDK 仍是 0.x；
  - ADK 1.x 到 2.x 存储格式不兼容。

### D7 自主等级

等级定义：

| 等级 | 含义 |
|---|---|
| L0 | 只建议 |
| L1 | 只读回答，带标签 |
| L2 | 产出 PR，由人合并 |
| L3 | 门禁全过后自动进入 dev / staging，或自动发布认证答案 |
| L4 | 在预批准的范围内自动上线，事后抽审 |

各工作流的设定：

| 工作流 | 起始 | 升级条件 | 上限 |
|---|---|---|---|
| 认证问数（V2 及以上） | L3 | — | L3 |
| 探索问数 | L1 | — | L1 |
| 断修（不涉及契约变更） | L2 | 连续 30 个 PR 都无重大修改即合并且 0 回滚【假设】 | L3（上 prod 仍需人批） |
| 契约或指标定义变更 | L2 | 永不升级 | L2 + owner 审批 |
| 回填、重算历史 | L2 | 永不升级 | 双人审批 |
| 实验读数 | L2 | 20 次读数与统计师复核一致 | L3（仅内部报告） |
| 模型晋升或触达客户的动作 | L2 | 永不升级 | 走 G5 / G6 |
| 高风险决策 | L0–L1 | — | agent 不做决策 |

DataWorks 的五档审批模式可以直接映射到这张表。

---

## E. 可信与验证

### E1 没有 ground truth 时，用什么当 oracle（由强到弱）

| 编号 | Oracle | 说明 |
|---|---|---|
| O1 | 独立记录对账 | 财务关账、源系统计数 |
| O2 | 不变量 / 变形关系 | 去重幂等、m:1 join 不增行、金额守恒、价格表在有效期内 |
| O3 | 双路复算 | 语义层 vs 原始 SQL vs pandas；结果不一致就 HOLD |
| O4 | golden / verified queries | 只用于离线评测 |
| O5 | 延迟真值 | 标签成熟后回测；未成熟的标签不能当负例 |
| O6 | 随机实验 | 干预效果唯一可信的来源 |
| O7 | 人工盲标 + 分层抽样 | 要记录抽样概率 |
| O8 | **种植缺陷 / 金丝雀** | 往 staging 副本或合成世界注入已知缺陷，测检查本身的召回率（相当于数据管道的 mutation testing）；**这是真值缺失时度量"验证有多可靠"的唯一办法** |

**时间覆盖缺口**：未关账的期间拿不到 O1。例如在 2026-08-31 这个 cutoff，8 月还没关账，当期数字最高只能到 V1，并且要明确标出来。

### E2 验证阶梯与最低要求

| 等级 | 含义 |
|---|---|
| V0 | 执行成功（不构成证据） |
| V1 | 契约、测试、不变量通过 |
| V2 | O1 对账通过，或 O3 双路一致 |
| V3 | 生成该产物的配置在留出集上 pass^3 达标 |
| V4 | 在成熟结果或封存 holdout 上达标 |
| V5 | 随机实验证明了效果 |

最低等级要求：
- 内部探索：V1，并打标签；
- 给管理层或对外的数字：V2；
- 生产管道：V1 + 能对账部分的 V2；
- 模型进影子：V4；
- 扩量或宣称业务价值：V5。

### E3 答案证书

字段：问题；术语解析结果（例如 net 还是 gross、是否排除测试账号）；metric id 和 `metric_def_digest`；SQL 及其 digest；build / 快照 id；行数；检查结果和对账状态；**验证等级**；caveats；模型和 harness 版本；成本。

对外输出的证书还要附 AI 标识（中国标识办法；EU Art.50）。

### E4 Evals

- **起步与扩充**：每个工作流先从真实失败案例里取 20–50 题，6 个月内扩到 200–500 题。每题 2–4 种改写；其中 10–15% 是不可回答或有歧义的题。
- **判分**：
  - 比较结果集是否等价，可设容差，允许多个正确答案（LinkedIn 约 60% 的题有多解）；不比较 SQL 字符串。
  - PR 类任务的判分 = CI 全绿 + 隐藏测试通过 + 与真值做 data diff。
- **指标**：pass@1、pass^3、静默错误率、拒答的精确率 / 召回率、cost/correct、p90 时延、人工干预次数。
  - 换算示例：单次成功率 75%，pass^3 只有约 42%（Anthropic 的例子）。
- **防泄漏与审计**：
  - 运行集和评测集分离；
  - 答案键本身也要审计：双标注 + LLM 审计 + 人工复核。公共基准答案键错误率在 30–60%。
- **触发条件**：模型、提示、Skill、上下文、harness 任何一个变化都要跑；回归集和能力集分开；接受约 5% 的运行噪声。
- **落地**：任务跑在 Inspect AI 或 pytest 上，traces 写进 MLflow。

### E5 门禁

- 草案的语义原样保留，实现形态改成 CI 策略步：
  - 只有经过认证的 FAIL 才 REJECT；
  - 缺失、过期、ERROR、INCONCLUSIVE、未认证、重复的输入都给 HOLD，并说明怎样解除；
  - 支持 supersede：重试得到的 PASS 可以取代暂态 ERROR。
- 证据载体是 in-toto statement：
  - subject = 产物 digest；
  - predicate = 检查结果 + `metric_def_digest` + `semantic_model_digest` + 快照 id + 评估器版本；
  - 由 CI 的 OIDC 身份签名。
- 门禁清单：
  - G-data：每次构建判 READY / FAILED（dsharness 已实现）；
  - G-change：PR 的 CI；
  - G-answer：答案证书；
  - G-model：独立 scorer；
  - G-release：受保护环境 + 证据；
  - G-value：对应草案的 G5 / G6。

### E6 人审

- **必审**：新口径、新表、对外数字、契约变更。认证答案抽审 5%。
- 审查者看证据摘要（证书、diff、检查结果），不看原始 transcript。
- **审查金丝雀**：每月往审查队列里注入 2–5% 已知有问题的 PR 或答案，召回率低于 80% 就培训审查者或降低自主等级【阈值为假设；做法来自 IMDA 防"橡皮图章"】。
- 记录 override 率和同意率。

### E7 多 agent 辩论 / 评审团

- **默认不用。** 依据：
  - 辩论的收益主要来自投票本身（NeurIPS 2025 "Debate or Vote"）；
  - 不执行代码的 LLM 判官最好也只有约 82%，交换回答顺序准确率能摆动 14%（CodeJudgeBench）。
- 允许的三种用法：
  - 对执行结果做 N 候选多数投票，用于高价值的单个问题；
  - 干净上下文的 skeptic，只产出可执行的反例；
  - 成对 LLM judge，只评叙述质量，随机顺序，并与人工标注校准。
- 草案里的 A/B/C/D 对照保留为实验。面板只有在满足"正确率 +5pp 以上，或静默错误率减半，且成本 <2 倍"时才启用【假设】。

---

## F. 交付与组织

### F1 各类交付物的机制

| 交付物 | 载体 | 门禁 | 发布 | 回滚 |
|---|---|---|---|---|
| 问数答案 | IM 线程或 BI，附证书 | G-answer | 认证答案自动发布，探索答案打标签 | 指标定义变更后，引用旧 digest 的答案自动标过期并通知 |
| 管道 / 语义层 / 上下文 | PR | G-change：CI 用独立身份复跑 build、checks、evals、diff | 合并后自动到 staging；prod 走受保护环境 | git revert + Iceberg / DuckLake time travel |
| 回填 | 计划 PR（范围、成本、预估 diff） | 双人审批 | 分区分批执行 | 快照恢复 |
| 模型 | 候选 + 独立 scorer 证据 | G-model → G5 → G6 | MLflow alias 指向钉住的版本；影子 → 有限实验 → 扩量 | 回退整份 release manifest（模型、预处理、阈值、镜像） |
| 决策 | 决策记录 + 实验设计 | G-value | 业务 owner 签字 | 停实验，回退策略 |

### F2 CI、环境、证明

- 身份分两类：
  - agent：只有 dev 写权限，可以开 PR；
  - CI / gate：跑检查、签证明、执行部署。
- prod 凭据只在受保护环境里释放。GitHub 支持最多 6 名必需审批人，并可禁止自审。
- GitHub 上用 actions/attest 签证明（私有仓库需要 GHE Cloud）；国内自建 GitLab / Gitea 用 cosign + 企业 KMS。
- PR 模板必填：意图、diff、检查结果、data diff、评测增量、成本、风险等级、回滚方案。
- harness 或模型升级也走同一条 PR + 全套评测。

### F3 角色

| 角色 | 职责 | 投入【假设】 |
|---|---|---|
| 业务 owner | 定义决策和 KPI；批准实验；签署决策记录 | 每周 1 小时 |
| 指标 / 上下文 steward | 维护语义层、verified queries、表卡片；纠错 PR 在 2 个工作日内处理 | 每域 0.5–1 FTE |
| harness 工程师 | dsh-mcp、Skills、hooks、门禁、bake-off、FinOps | 试点期 1 FTE |
| 评审池（DE / DS） | 审 PR、抽审答案、标注 golden | 占本职 10–15% 时间 |
| 评测 owner | 维护和审计 golden，管回归门 | 0.25 FTE |
| 风险合规 | 用例分级、路由策略、DPIA / PIPIA | 按需 |

单人项目可以一人兼任多个角色，但机器身份必须分开。

### F4 采纳与变更管理

- **嵌入现有渠道**：LinkedIn 把 SQL Bot 嵌进 DARWIN 后，采纳量涨了 5–10 倍。
- **先窄后宽**：先做一个 persona、一个域。每个答案都展示 SQL 和验证等级。
- **反馈闭环**：用户点踩或纠错 → 自动生成上下文 PR → steward 审核 → 进入运行集或评测集（只能二选一）。
- **每周公开计分卡**。
- **定位为"增强"而非"替代"**：Stanford 的 Canaries 研究显示，AI 暴露岗位中 22–25 岁就业低于趋势 19%，原因是少招人而不是裁员。
- 提供 AI 素养培训。
- 不搞"每步审批"。

---

## G. 路线图（阈值为【假设】，需在第 0–2 周测完基线后校准）

| 阶段 | 范围与产出 | 退出标准 |
|---|---|---|
| **0–90 天**：一个域（收入 / 订阅），W1 + W2 | 第 0–2 周：测基线（请求量、耗时、积压、事故、MTTR），做风险分级，定路由策略。<br>第 2–6 周：上下文仓库（≥30 个指标；≥100 条运行集 verified queries；≥100 道留出 golden 题；核心表卡片）；dsh-mcp v0；6 个 Skills；hooks；证书 schema；CI gate + 证明。<br>第 6–12 周：W2 对一个 persona 上线；W1 在 dev 以 L2 运行；每周计分卡；第一次 bake-off | 认证答案 pass^3 ≥95%；越界拒答 ≥90%；抽审（n≥200）静默错误率上界 ≤2%；W1 PR 接受率 ≥50%，MTTR −30%；cost/correct ≤ 人工的 10%；persona WAU ≥40%；自助解决率 +15pp |
| **3–6 个月** | 扩到 3 个域；W3（L1→L2）；W4 Skill 上线；低风险断修升到 L3；夜间离峰富化；审查金丝雀；中国 / 海外双栈路由；平台原生 agent 对照评测 | 自助解决率较基线 +20pp；机械工单占比 −10pp（参照 Grab 从 44% 降到 30%）；≥3 次 Skill 读数与统计师复核一致；审查召回 ≥80%；价值账本覆盖 100% 的 agent 任务 |
| **6–12 个月** | W5 首个模型（uplift 定向或需求预测）：独立 scorer → 影子 → G5 有限随机实验；决策账本；合规包自动生成（模型卡、DPIA / PIPIA、NFRA 留痕项）；FinOps 按工作流归因 | ≥1 个决策取得 V5 证据，且增量置信区间下界 >0（证明无效后停掉也算合格产出）；年化账本净值 >0；2 周内能切换 harness 且套件指标降幅 <3pp |

---

## H. 参考实现（dsharness）下一步

### H0 现状核查

**已有**（路径都相对仓库根目录）：

- `src/dsharness/world/generator.py`：合成订阅公司，到 2026-09 约 7.6k 客户，种植了 9 类缺陷：

  | 缺陷 | 规模 |
  |---|---|
  | D1 重发重复行 | 831 行 |
  | D2 同日相同的合法附加包 | 3,046 行 |
  | D3 元 / 分单位事故 | 858 行 |
  | D4 退款 | 2,302 行 |
  | D5 测试账号 | 25 个 |
  | D6 迟到的使用记录 | 4,368 行 |

  另有 D7 可变列、D8 schema 变更、D9 人群漂移。
- custody 真值和多个 cutoff 的导出，导出带 sha256 manifest。
- `src/dsharness/lake/contracts.py`：Pandera v1 / v2 版本化契约。
- `src/dsharness/lake/runner.py`：失败模型的下游标记为 SKIPPED。
- 对账测试 `revenue_matches_finance_close`，构建按 READY / FAILED 原子发布。
- 构建结果：
  - 2026-02-28、03-31、05-31、06-30 都是 READY，对账 0 差异；
  - 2026-08-31、09-30 因 D8（`amount_cents` 改成 `amount_minor` + `currency`）而 FAILED。这是显式失败，属于正确行为。

**缺口和问题**：

1. `pyproject.toml` 声明了 `dsh = dsharness.cli:main`，但 `cli.py` 不存在；`tests/` 为空；声明了 mcp 依赖，却没有 server；`workspace.py` 引用的 `docs/DESIGN.zh-CN.md` 不存在。
2. `pipeline/models/invoices.sql` 已经内置了 D3 修复，作为基准，agent 没有机会自己"发现"这个事故。
3. `schema.yml` 里的 `charge_amount_on_price_book` 没有校验价格的 valid_from / valid_to：8 月涨价之前出现 6900 也能通过。
4. `generator.py` 就在仓库里。只要被挂进 agent 沙箱，就等于把缺陷清单（也就是 custody）交给了 agent。
5. 实验只保存了潜在结果（p0 / p1 / u），还没有分配逻辑，也没有结果模拟器。
6. 对账只覆盖已关账的月份。

### H1 要实现的模块

1. **`cli.py`**：提供 `dsh world build | lake build --cutoff | mcp serve | eval run --suite --harness --k 3 | report`。
2. **`tools/mcp_server.py`**（FastMCP 4.1，stdio + streamable HTTP）：
   - 实现 D2 中的 10 个工具，`log_decision` 留到 ledger 阶段；
   - 只服务 READY 构建的 silver 和 `bronze_safe`；
   - 行数上限 200；遇到 PII 列直接拒绝；
   - `open_pr` 只写 `runs/<id>/patch` 和本地 git 分支；
   - 附 hooks 样例：拒绝读取 `custody/` 和 `src/dsharness/world/`。
3. **`semantic/metrics.yml`**（MetricFlow / OSI 子集）：
   - 指标：net_revenue、gross_charges、refunds、MRR、active_customers、churn_rate（排除测试账号，带成熟窗口）、ARPU、tickets_per_active；每个带 digest；
   - `verified_queries/`（运行集）和 `evals/golden/`（评测集）物理分仓。
4. **`checks/`**：
   - `pit.py`：利用 `PIT_CUTOFFS` 的 as-of 导出检测 D6 / D7 泄漏；
   - `cardinality.py`；
   - 修复价格有效期测试；
   - `dual_path.py`：语义层 / SQL / pandas 三路复算；
   - 给未关账期间打标记。
5. **`world/scenarios.py`**：参数化注入，再加一批只在评测时启用的缺陷族：

   | 缺陷 | 内容 | 期望行为 |
   |---|---|---|
   | D8b | v2 导出中出现非 CNY 币种 | 只改列名的修复会产生静默错误，必须被识别 |
   | D10 | UTC 与 Asia/Shanghai 时区跨月 | — |
   | D11 | CRM 合并导致 m:m 扇出 | — |
   | D12 | 财务重述：已关账月份被合法修订 | 应升级给人，而不是改数据去迎合账本 |
   | D13 | 单元格里含提示注入文本 | 测工具层防护 |

   同时要多 seed，并提供不含 D3 修复的 "pre-fix" 管道变体。
6. **`experiment/simulator.py`**（运行在 custody 侧）：输入分配表，用固定的 u 和 p0 / p1 生成观测结果；agent 只能看到观测结果。
7. **`evals/`**：
   - harness 适配器：`claude -p --bare`、`codex exec`、Qwen Code（OpenAI 兼容端点）、无 agent 的脚本基线；
   - 评分器读 custody；
   - 结果写进 MLflow 并输出 JSON。
8. **`gate/`**：修复版门禁语义 + in-toto statement 生成。
9. **`ledger/`**：`agent_task_log`、`decision_log`、`incident_log` 三张表的 dbt 模型，以及 cost/correct 计算。

### H2 基准任务（真值都来自 custody）

| 任务 | 输入 | 判分 | 关键指标 |
|---|---|---|---|
| T1 断修 | 2026-08-31 的 FAILED 构建 | 构建变 READY；已关账月份对账 0 差异；8、9 月收入与 `truth_revenue` 逐分相等；没有改测试 | pass^3、"能修好但有静默错误"的比例、cost/correct |
| T2 问数 | 80 题，其中 ≥12 题不可答或有歧义（如"10 月收入"、未区分毛 / 净的"收入"） | 结果集等价；应拒答或澄清的题按此判 | 正确率、静默错误率、拒答 P / R |
| T3 审计 | pre-fix 管道 | D1–D9 及留出的 D10–D13 的召回率和误报率 | 召回、精确、证据查询能否复现 |
| T4 PIT 特征 | 2026-05-31 截止 | PIT 检查 + 与 custody 比对特征值 | 泄漏特征数，目标 0 |
| T5 定向 | 20% 联系预算 | 预期挽留数：随机 7.9 / 按风险 24.0 / oracle 37.6【算】 | regret = 1 − V/V_oracle；按风险排序的 regret 为 36% |
| T6 读数 | 模拟器输出的 7 月实验 | 真实 ATE 1.11pp，SE≈0.72pp，**功效 0.34**，MDE80≈2.0pp【算】。正确答案是 **INCONCLUSIVE**，并给出所需样本量或时长 | 虚报"显著"次数，目标 0 |

### H3 实验矩阵（每个假设都要可证伪）

**配置**

| 配置 | 内容 |
|---|---|
| A | agent + 原始表 |
| B | A + 上下文包 |
| C | B + 检查 / 门禁 / 证书 |
| D | C + skeptic 探针 |
| E | C + 3 候选投票 |

规模：每个配置 × ≥2 个 harness（其中一个用境内模型）× 3 次重复 × ≥2 个世界 seed。

**待证伪的假设**

- T2 上 B 比 A 高至少 20pp；
- C 的静默错误率不超过 B 的一半；
- C 配置下 T1 的 pass^3 ≥0.8；
- 门禁对种植缺陷的错误放行为 0，误阻断 ≤5%；
- T6 虚报为 0；
- D 和 E 只有满足 E7 的阈值才保留。

每晚离峰跑一次，输出 cost/correct 和各 harness 之间的差值。

---

## I. 风险与反例

### I1 主要风险

| 风险 | 失效表现 | 缓解 |
|---|---|---|
| 平台吞并 | 厂商自己补上评测和验证，自建层变得多余 | 自建部分只保留可移植资产；按 B6 的平台替代测试执行 |
| **无人策展（最可能的失败）** | 纠错 PR 积压超过 2 周；verified queries 不再增长 | 设 steward 和 SLA；控制域的数量 |
| 语义定义本身错了 | 门禁只能验证"与定义一致"，验证不了"定义正确" | 用 O1 对账 + owner 签字 |
| 评测过拟合或泄漏 | golden 题混进了上下文；合成世界被"背题" | 物理分仓、留出缺陷族、多 seed、定期换题 |
| 橡皮图章审查 | 审查者召回率低 | 审查金丝雀 |
| 过度门禁 | HOLD 率高，采纳下降 | 按风险分级；监控 HOLD 率和解除时长 |
| 中国 token 涨价、限流、中断 | 成本或可用性失控 | 多供应商 + 自托管兜底 + 批处理排离峰 |
| 产品频繁改名和弃用 | 例如 Knowledge Assistant 9 个月内从 GA 变成 legacy | 只在 MCP / SQL / OSI 协议层集成 |
| 数据内容里的提示注入 | 工具被滥用 | 默认只读、在工具层授权、用 D13 测试 |
| 合规（出境、NFRA、外发标识）和许可证（ELv2 / BSL / PLA） | 交付或运营受阻 | 路由策略 + SBOM / SPDX 门 |
| 合成基准的外推谬误 | 把合成结果当成真实准确度或价值 | 合成结果只能证明机制，不能证明真实准确度或价值 |

### I2 什么结果会证明本设计错了

1. 真实试点抽审中，配置 A 和配置 C 的静默错误率没有显著差异 → 门禁层不值它的成本，只保留上下文。
2. 认证正确率 ≥95%，但 90 天里自助解决率没有上升 → 瓶颈在工作流嵌入和信任，不在答案质量，应该转去做流程改造。
3. D 或 E 带来 ≥10pp 的提升，且成本 <2 倍 → 我对多 agent 的保守立场是错的。
4. 稳态策展需要超过 2 FTE/域 → SMB 形态不成立。
5. 6 个月后 W1 的 PR 接受率仍 <30% → 这个组织的 DE 工作不适合由 agent 写入，退回只做分诊。
6. 境内模型 harness 在套件上落后超过 10pp，且补上下文也追不回来 → 中国的个人信息场景只能收缩到 D0 / D1 任务。

#################### NEXT PROPOSAL ####################

# DS Harness：建在现有数据平台之上的可信 Agent 数据工作层
（平台复用视角 · 2026-10-19 · 独立方案）

引用约定：〔dg Lnn〕表示 `docs/_research_digest_2026-10-19.txt` 第 nn 行。〔假设〕表示还没有证据支撑的工程估计，试点中要用实测值替换。〔实算〕是我用 dsharness custody 真值算出来的数，只说明机制，不代表现实效果。

---

## A. 核心论断

1. **系统定位**：做一层薄的、不绑定厂商的控制面，起名 DS Harness。它放在企业已有的数仓/湖仓、dbt/SQLMesh、编排器、Catalog、语义层、MLflow、Git/CI 和 IdP 之上，只做四件事：
   - 把业务语义和可信查询写成代码，作为上下文提供给任何 agent；
   - 用可执行的检查验证 agent 的产出，包括答案、管线变更、模型和决策；
   - 让产出走企业现有的 PR、CI 和受保护环境发布；
   - 核算每个正确任务的成本，以及经过核验的业务价值。
2. **它不是什么**：它不是又一个 DS Agent，也不是 NL2SQL 引擎、Notebook、调度器、Catalog、证据数据库，或者多角色的"AI 数据团队"。问数和平台内的 coding agent 已经是商品：CoWork/CoCo、Genie Code/Genie Agents、BigQuery DE/DS agent、Fabric data agent、DataWorks Data Agent、dbt Wizard 都有〔dg L179–L200, L202〕。自己再做一个，没有差异化。
3. **主要杠杆是语义和验证，不是模型**：
   - DABstep 消融实验中，加入语义内容后 Hard 题准确率从 13.9% 升到 55.1%〔dg L112〕。
   - 语义层的独立增益在 +8 到 +25pp，而且能把"看起来合理的错答"变成明确的"无法回答"〔dg L148〕。
   - Meta 换了更强的模型后效果停滞，产出的是"自信的错答"〔dg L105〕。
   - OpenAI 最后定在 1 个模型配 13 个互不重叠的工具〔dg L97〕。
4. **第一批工作负载**：认证域内的指标问答，加上数据工程的故障修复和变更。
   - 问答的量最大：Grab 的 SQL 类请求自助解决率从 50% 升到 81%〔dg L106〕。
   - 数据工程最容易用执行结果验证：抽取和加载已经约 96% 可解，变换只有 23–74%，必须靠 CI 兜底〔dg L145, L157〕。
   - 模型工厂放在第二波。决策类模型必须先做 uplift 或实验，因为按风险排序不等于按价值排序〔dg L24〕。
5. **头号风险是静默错误**：约 70% 的失败会返回"看起来正常"的结果，自我反思消除不了〔dg L150, L176〕。所以每个产出都要带一张出处卡（含各类 digest 和检查结果），系统也必须能明确拒答。
6. **写入只有一个执行者，自治级别按工作流逐级挣来**：
   - 只读的子 agent 可以并行，但写代码、写 SQL、写生产数据的只能是一个 writer〔dg L257, L289〕。
   - 人工闸门只放在四处：生产写入、模型晋级、影响个人的决策、对外发布的内容。其余用抽样审计。
   - 原因：调查显示"每个动作都审批"的部门只有 16% 取得变革性成功，充分使用 agent 的部门是 31%〔dg L392〕。
7. **资产即代码，只绑定开放标准**：SQL、Iceberg、OSI（Apache Ossie）/MetricFlow、OpenLineage、MCP 2026-07-28、Agent Skills、AGENTS.md、in-toto、OTel。厂商产品可能 9 个月内就从 GA 变成 legacy〔dg L186, L208〕。
8. **两个部署剖面，共用一套资产**：SMB 本地优先；企业采用 push-down，数据不出仓，并按用户身份执行。中国和海外只在策略包、模型路由和平台 adapter 上不同。
9. **只认三类可核验的价值**：被取消的外部支出、可度量的积压或 MTTR 改善、通过实验验证的 KPI 增量。节省的工时只算产能。每个试点 90 天设 kill 标准〔dg L22, L419〕。
10. **dsharness 是测量仪器，不是演示**：它用埋入的真值和埋入的缺陷，测"任意 agent 加上本设计"的静默错误率和拦截率。

**对 v0.1 草案的取舍**

- **保留**：
  - 执行状态、检查结果、晋级决定三者分开；
  - 缺失、ERROR、INCONCLUSIVE 一律判 HOLD；
  - 泄漏规则和标签成熟规则；
  - 封存的 scorer；
  - G5（有限实验）和 G6（价值声明）分开；
  - 故障注入清单；
  - A/B/C/D 固定预算对照〔dg L2–L4〕。
- **放弃**：
  - 强制使用 Dagster；
  - 自建 HMAC 证据账本、17 表 PG 和 reconciler，改用 CI OIDC、in-toto 和受保护环境〔dg L11–L13, L40〕；
  - 把 Agent Adapter 放到 PR8；
  - 企业场景下"原始数据本地优先"〔dg L25〕；
  - 按流失风险排序作为首个案例。
- **gate.py 要修三处**〔dg L8–L9, L29, L38〕：
  - 先认证，再计数；
  - 支持 supersede 和重试；
  - 无法验证的输入判 HOLD 并告警，REJECT 只能由已认证的 FAIL 产生。

---

## B. 价值与经济模型

### B1 用例组合（按"可度量价值 × 可行性"排序）

| # | 工作流 | 价值指标 | 可行性证据 | 起始自治 | 买 / 建 |
|---|---|---|---|---|---|
| 1 | 认证域指标问答（只覆盖已建语义的域） | 自助解决率、机械工单占比、首答时间 | 有策展语义时 80–90%；无策展的原始仓 10–30%，BEAVER 上是 10.8%〔dg L141, L157〕。Grab 机械工单占比从 44% 降到 30%〔dg L106〕 | L1 | 对话入口买；上下文和评测自建 |
| 2 | 数据工程修复/变更（schema 漂移、失败任务 RCA、dbt PR、回填） | MTTR、值班小时、数据停摆时长、回滚率 | 语法和逻辑错误能修，数据层问题要靠真的去查数据〔dg L146〕。OpenAI 用 Codex 迁移了约 1 万个 DAG；Grab 的 Scarlet 做管线根因分析〔dg L127〕 | L2 | coding agent 买；CI 检查和 skills 自建 |
| 3 | 数据质量与对账监控 | 消费前拦截的缺陷数、财务差异 | 确定性检查 | L1→L3 | 复用 dbt tests、GX、Pandera；检查库和缺陷注入自建 |
| 4 | A/B 实验读数 | 读数耗时、决策时延 | 统计计算可复算；快手有 /abtestSkill〔dg L109〕 | L1 | 建成 skill |
| 5 | 重复型模型工厂（预测、uplift、评分） | 实验验证的 KPI 增量 | MLE-bench 上 61–64% 能拿奖牌〔dg L143〕。跟踪增长指标的部门 35% 报强 ROI，只跟踪效率的是 13%〔dg L392〕 | L2（晋级永远人审） | 复用 MLflow/平台 ML；任务包和封存评估自建 |
| 6 | 开放式探索/因果分析 | 分析师产能 | 超出能力前沿后过度信任，正确率从 84.5% 降到 60–70.6%〔dg L396〕 | L0 | 不自动化 |
| — | 信贷、保险、雇佣、NFRA 高风险清单 | — | 人做最终决定，必须可解释、留痕〔dg L60, L76〕 | L0 | v1 不进入 |

**数据采集的处理**：

- 内部源复用现有 ELT（Fivetran、DataWorks DI、CDC；SMB 用 dlt 1.31）。agent 只能以 PR 的形式生成 source 配置和契约。
- 外部网页采集受《网络数据安全管理条例》第 18、24 条约束〔dg L56〕，优先买有许可的数据。

### B2 价值公式

```
NV = Σ_w κ_w·N_w·a_w·(t0_w − t_r,w)·c_h  +  V_ext  +  Σ_m LCB95(ΔKPI_m)·u_m
     − (C_llm + C_compute + C_review + C_curation + C_platform)  −  N·a·s·L
```

| 符号 | 含义 |
|---|---|
| a | 审计合格的接受率（自助解决率、PR 合并率） |
| t0 | 上线前 2 周用时间-动作法测出的人工基线，不用自报 |
| t_r | 剩余的人工复核时间 |
| κ | 产能转成现金的比例。默认 0.3–0.5〔假设〕；只有在外包合同取消、计划招聘撤销或积压可测下降并经财务确认后，才取 1 |
| ΔKPI | 只计通过 G6 的结果，取置信区间下界 |
| s | 审计得到的静默错误率 |
| L | 每个被采用的错误答案的期望损失 |

依据：清晰的 ROI 来自替代外部支出，而不是裁员〔dg L387, L406, L409〕。

### B3 成本模型

价格来自〔dg L400–L402, L181, L190, L198〕；token 用量是〔假设〕。

| 任务 | token / 任务 | LLM 成本 | 主要成本 |
|---|---|---|---|
| 认证问答 | 输入 60–100k（缓存命中 ≥60%），输出 2–4k；Vercel 实测约 61k〔dg L98〕 | Sonnet 5.5 约 $0.10–0.20；DeepSeek V4-Pro 非高峰约 $0.03–0.06 | 10% 抽检，每次约 5 分钟 |
| 数据工程修复 PR | 1–3M | Sonnet 约 $1–5；Opus 5.5 约 $2–10 | 人工复核 15–30 分钟；克隆环境的 CI 计算 |
| 模型工厂一轮 | 0.5–2M | $1–5 | 训练算力；DS 复核 1–2 小时 |
| 回归评测 | 150 题 × 3 次 × 2 个 harness | 每轮约 $100–200 | 每次模型、prompt 或 skill 变更都要跑 |

**结论**

- 单个任务里 token 成本通常不到 5%，策展和复核才是大头。所以设计目标是：每个被接受的产出，复核分钟数越少越好。办法是给出 data-diff 摘要、对账证据和出处卡。
- 规模上来以后 token 会变成显著成本：快手人均每周 55 次对话；64% 的企业把 token 成本列为主要障碍〔dg L109, L392〕。国内 2026 年还在涨价，DeepSeek 部分计量项涨了 50%–1100%，并有峰时计价〔dg L402〕。
- 因此要做到：
  - 每个任务设预算上限；
  - 尽量命中缓存，非高峰时段跑批；
  - 至少接 2–3 家供应商，其中一家是可自托管的开放权重模型〔dg L413〕。
- 平台促销不计入预算。Genie 对用户免费到 2027-01-31，但服务主体的调用当天就收费〔dg L185, L207〕。
- 固定人力〔假设〕：
  - 平台工程 1–1.5 FTE（其中包括依赖每月升级、MLflow 3.17 需要协调式 DB 升级等工作〔dg L347〕）；
  - 每个语义域的策展 0.5 FTE；
  - 评测负责人 0.5 FTE。

### B4 单域盈亏平衡示例

全部参数为〔假设〕：每月 1,500 次合格请求，a=0.5，t0=40 分钟，t_r=4 分钟，得到 450 小时产能，κ=0.4。

**美国**

- 时薪约 $95：满载成本约 $172k〔dg L403〕，按每年 1,800 小时折算。
- 计入的价值约 $17k/月。
- 成本约 $17k/月：LLM 加算力约 $0.7k、抽检约 $1.2k、策展约 $7.5k、平台分摊约 $7.5k。
- 第一个域大致打平；第二、三个域复用平台后转为正收益。

**中国**

- 时薪约 ¥110：IT 行业平均工资 ¥128k–249k〔dg L404〕，再加社保〔假设〕。
- 价值约 ¥20k/月，成本约 ¥22k/月，单靠人力替代是负收益。
- 中国的商业论证必须落在三件事上：决策速度、单人覆盖规模（每人负责更多 SKU、区域或模型）、收入类 KPI〔dg L411〕。同时用平台原生 agent 压低固定成本，例如 DataWorks 每 credit ¥0.05〔dg L198〕。

### B5 中国与海外的差异

| 维度 | 中国 | 海外 |
|---|---|---|
| 平台 | DataWorks Data Agent（Qwen/DeepSeek/GLM，Skills，5 档审批）、火山 Data Agent、腾讯 DataBuddy〔dg L198–L199〕 | Snowflake、Databricks、BigQuery、Fabric |
| 模型与数据驻留 | 个人信息和重要数据只用境内模型。把这类数据发给境外 API 算出境，会触发 10 万人、100 万人或重要数据的阈值〔dg L58, L73〕 | EU 用 EU 区域的零留存端点，不能只依赖 DPF〔dg L53〕 |
| 合规 | 内部工具不需要备案〔dg L54〕；导出的内容要加标识〔dg L55〕；PIPL 的 PIA 记录保存 3 年〔dg L57〕；银行和保险适用 NFRA 金发〔2026〕8 号〔dg L60〕 | EU Annex III 推迟到 2027-12-02，Art.50 已生效〔dg L46–L47〕；美国 SR 26-2 排除了 agentic AI〔dg L64〕；Colorado ADMT 2027-01-01 生效〔dg L62〕 |
| 入口 | 钉钉、飞书、企微 | Slack、Teams |
| 供给风险 | 算力配给；DeepSeek 曾宕机约 12 小时〔dg L402〕 | Claude 新 tokenizer 同样文本多出约 30% token〔dg L400〕 |
| 发布证明 | 自建 GitLab 受保护环境 + cosign/KMS 签名的 in-toto（能否访问公网 Sigstore 是〔假设〕，需验证） | GitHub actions/attest + environments〔dg L11〕 |

### B6 Kill 标准

阈值为〔假设〕，试点前由业务方签字。

| 时间点 | 对象 | 触发条件 | 处置 |
|---|---|---|---|
| D90 | 问答 | 认证集结果匹配率 <85%，或 pass^3 <75%，或审计静默错误率 >3%（n≥200），或自助解决率 <30% | 停止扩域；再过 30 天仍不达标就关闭 |
| D90 | 数据工程 | 无需大改即合并的比例 <40%，或回滚率 >10%，或出现任何 agent 引起的 Sev1 | 降到 L1 |
| D90 | 整体项目 | 核验价值/总成本 <0.5 且趋势不向上；或每条 verified query 策展耗时 >1 小时；或探索路径流量 >60% | 整体复盘 |
| M6 | 整体项目 | 价值/成本 <1.5 | 收缩为"资产 + 原生 agent"，停建网关 |
| 任意时点 | 模型任务包 | 连续 2 个周期在预注册实验里输给现行策略 | 退役该任务包 |

---

## C. 端到端架构

### C1 分层视图

```
使用面（复用）：BI · IM · IDE · 平台原生 agent（CoWork / Genie / DataWorks Data Agent）
      │ 答案 + 出处卡 / PR / 发布申请
Agent 运行面（可替换）：Claude Agent SDK · codex exec · Gemini CLI · Qwen Code · PydanticAI
      │  AGENTS.md + Skills；MCP 2026-07-28（OBO 令牌，_meta 中带 traceparent）
┌──── DS Harness 薄层（自建）──────────────────────────────────────┐
│ dsh-mcp 网关 → 策略（数据分级 × 法域 × 自治级）→ 模型路由（agentgateway / MLflow AI Gateway）│
│ ctx 上下文仓(git) · dsh-checks/faults · dsh-eval · dsh-gate(CI) · dsh-value(仓内) │
└──────┬ push-down SQL ──────┬ PR/CI ──────────┬ trace / 成本 / 血缘 ───────┘
数据平台（复用）：仓/湖(Iceberg) · dbt/SQLMesh · 编排器 · 语义层 · Catalog · MLflow · 实验平台 · IdP · Git/CI · Sandbox
```

### C2 各阶段：复用什么、建什么、agent 做什么

| 阶段 | 复用（企业 / SMB） | 自建薄层 | agent 角色（自治级） | 门禁 / 证据 |
|---|---|---|---|---|
| 采集 | 企业：现有 ELT、CDC。SMB：dlt 1.31 | 源契约模板（Pandera / dbt contracts）、schema 版本登记 | 生成 source 配置和契约 PR（L2） | 契约检查、行数和金额对账、PI 扫描 |
| 存储计算 | 企业：现有仓 + Iceberg 1.12 REST。SMB：DuckDB 1.5.6 + DuckLake 1.0 | 无 | 只读 push-down（L1） | — |
| 处理 | 企业：现有 dbt / SQLMesh / DataWorks 和编排器。SMB：dbt-core 1.12.x | 检查库：去重身份、join 基数、对账、data-diff | 修复/变更 PR（L2→L3 dev） | CI：build、tests、对账、data-diff、测试削弱检测 |
| 语义/上下文 | 企业：平台语义对象（semantic views、UC metric views、LookML、Cube）。SMB：MetricFlow 0.213 / Cube 1.8 | ctx 仓、OSI 同步器、夜间 enrichment | 文档和语义补全 PR（L2） | 定义 digest、策展人审核 |
| 特征 | 仓内 SQL；需要在线特征时才上 Feast 0.67 | PIT 宏（as_of、available_at） | 生成特征 SQL（L2） | PIT 检查、泄漏检查 |
| 训练 | 企业：平台 ML 或 sklearn/LightGBM + MLflow 3.17。SMB：同 | 任务包（TaskSpec、切分策略、uplift 模板） | 在 sandbox 里生成候选（L2） | 预算上限、封存的 holdout |
| 评估/测试 | MLflow GenAI eval、Inspect AI 0.3.x | 封存 scorer、agent 评测、缺陷注入包 | challenger 只读，只能提交可执行的反例 | G-Model、pass^k |
| 交付 | BI/IM 里的原生 agent、Git 环境、MLflow registry | gate 步骤 + attestation、答案契约 | 答案（L1）、PR 和发布申请（L2） | 受保护环境 + 必需审批人 |
| 价值 | 现有实验平台、BI | value ledger（dbt 项目）、ab-readout skill | 只出分析，不签收价值（L0） | 预注册、SRM、ITT、财务签核 |
| 监控 | 平台监控；OpenLineage 1.54 事件写入现有 catalog | 黄金集金丝雀、漂移检查、成本遥测 | 告警分诊和 RCA（L1）→ 转 PR | 告警 → 事故 → PR |

### C3 只需自建的最小中立层

不新增数据库，状态放在 git、仓内表和 MLflow 里。规模均为〔假设〕。

| 组件 | 内容 | 依托 | 规模 |
|---|---|---|---|
| ctx | 上下文仓：OSI/MetricFlow YAML、verified queries、glossary、skills、AGENTS.md、policies。配 lint，同步到平台语义对象，并检查评测集是否混入运行时上下文 | MetricFlow、dbt-mcp 2.7、sqlglot 30 | 2–4k LOC |
| dsh-mcp | 受治理的数据网关，工具 ≤13 个；按用户 OBO 执行；行数和成本有上限；可转发到平台 MCP | mcp SDK 2.3 / FastMCP 4.1、agentgateway 1.6 | 3–5k LOC |
| dsh-checks / faults | 泄漏、PIT、基数、对账、data-diff、实验质量检查；缺陷注入算子 | Pandera 0.34、GX Core 1.24、dbt tests | 3–5k LOC |
| dsh-eval | 黄金集、结果集等价判定、pass^k、harness 适配 | Inspect AI / MLflow eval | 2–4k LOC |
| dsh-gate | CI 策略步骤，输出 ALLOW/HOLD/REJECT 和 in-toto 声明 | actions/attest、cosign | <1.5k LOC |
| dsh-value | 价值台账 + 成本归集 | 仓内 dbt 约 30 个模型；MLflow trace 中的成本；平台用量视图 | — |

**明确不建**：对话 UI、NL2SQL、编排器、Notebook、Catalog、模型注册、sandbox 运行时、LLM 网关、向量库/记忆库、证据库、多 agent 框架。

**单平台企业例外**：如果企业只有一个平台，且原生 agent 和原生 MCP 已覆盖问数和编码（Genie MCP GA、BigQuery MCP GA、dbt-mcp〔dg L184, L188, L339〕），就不建 dsh-mcp 的数据访问部分。只有在跨平台（例如 MaxCompute+StarRocks、Snowflake+Databricks）或自管开源栈时才建完整网关，因为厂商 agent 只看得到自家资产〔dg L204〕。

### C4 两个剖面

**SMB / 本地优先**（适用于约 ≤10 名数据人员、数据 <1TB〔假设〕）

- 存储计算：DuckDB 1.5.6（不要用 2.0 dev）+ DuckLake 1.0 或 Parquet。
- 采集和变换：dlt + dbt-core 1.12.x。dbt-oss 2.x 先做回归测试再切；如果要零专有条款，不要用 `pip install dbt`，它装的是专有的 Fusion 二进制〔dg L337, L370〕。
- 数据检查：Pandera。
- 语义：MetricFlow，或 Cube（必须关掉 dev mode，它会绕过认证〔dg L349〕）。
- 训练、追踪、评测：MLflow 3.17 一套搞定。
- Notebook：marimo。
- 调度：cron 或 CI 就够；需要资产编排时再上 Dagster 1.13 或 Prefect 3.8。
- MCP：FastMCP 4.1。
- 沙箱：sandbox-runtime 或 Docker `network=none`。Linux 上的 Unix-local 模式没有 OS 级隔离〔dg L242〕。
- 预计花费〔假设〕：基础设施 <$300/月，token $100–1,000/月。

**企业**

- 仓保持为系统记录；跨引擎访问用 Iceberg + REST catalog（厂商 catalog 或 UC OSS 0.7）。
- dbt：Fusion 二进制在内部使用可以接受。
- 语义：OSI YAML 是可移植的真源，平台语义对象是运行时。dbt 的 Agents Schema 只跟踪，不依赖。
- 编排：保留 Airflow 3.3、DolphinScheduler 或 DataWorks。
- 血缘：OpenLineage 写入现有 catalog，不用 Marquez（已停更）。
- 身份：IdP + MCP EMA〔dg L223〕。
- 沙箱：kubernetes-sigs/agent-sandbox 1.0.7 + gVisor/Kata；海外也可用 E2B 或 Modal。
- LangGraph 1.2 只用在需要持久化人工审批的长流程上。

**两个剖面都要遵守的供应链规则**

- 加许可证门禁：SBOM + SPDX，标记 ELv2、BSL、PLA、AGPL〔dg L360〕。
- 避免使用：Vanna（已归档）、smolagents 和 AutoGen（已停滞）、Daytona OSS（已归档）、Marquez、核心路径上的 CrewAI。如果要转售或托管给客户，避开 Soda v4 和 lakeFS ≥1.87〔dg L366〕。

---

## D. Agent 设计

### D1 拓扑：五种作业，不按人设分角色

| 作业 | 触发方式 | 形态 |
|---|---|---|
| J1 Answer | 用户提问 | 单 agent，≤11 个工具；能追问澄清，也能拒答 |
| J2 Change | 事故或需求 | 单 writer 在 sandbox 分支里写代码 → 开 PR；另有一个上下文干净的只读 reviewer |
| J3 Triage | 测试失败、契约失败、漂移告警 | 只读 RCA 子 agent，产出诊断后交给 J2 |
| J4 Model factory | 定时或按需 | 在现有编排器里跑确定性 DAG，agent 只写特征和候选代码，评分由确定性程序完成 |
| J5 Enrichment | 每晚 | 读管线代码、血缘和查询日志，提交文档 PR（OpenAI 模式〔dg L117〕） |

**扇出的做法**：像剖析 500 张表这类任务，用脚本或 DAG 驱动，输出用 schema 校验，并给 token 预算（多 agent 消耗约 15 倍 token〔dg L256, L290〕）。

**A2A 的范围**：只用在组织或厂商边界上〔dg L321〕。

### D2 工具（MCP）

| 作业 | 工具 |
|---|---|
| J1 | `catalog.search`、`semantic.describe`、`semantic.query`、`verified_query.run`、`sql.explore`（只读，标 EXPLORATORY）、`data.profile`、`lineage.get`、`checks.run`、`run.status`、`clarify`、`memory.propose` |
| J2 增加 | `repo.propose_change`、`ci.results`，以及原生 harness 的沙箱文件和 bash 工具 |

设计规则〔dg L221–L222, L292–L299〕：

- 读工具和写工具分开，写工具加 destructive 标注；
- `tools/list` 顺序固定，方便 prompt 缓存；
- 只返回结果句柄，交给 LLM 的行数不超过 50〔假设〕；
- OAuth 2.1，令牌绑定 audience，禁止令牌透传；
- 长任务用 Tasks 扩展；
- 澄清和审批用 multi round-trip；
- 不使用 Sampling、Roots、Logging、DCR；
- 工具总数控制在 30–50 以下，超过后选择准确率会下降〔dg L255〕。

### D3 Skills

首批：`metric-qa`（澄清和拒答规则）、`dbt-break-fix`、`schema-drift-adapter`、`backfill-runbook`、`reconcile-to-ledger`、`pit-features`、`uplift-experiment-design`、`ab-readout`（SRM、ITT、CUPED、功效）、`model-card-and-pia`。

- 每个 skill 有一位领域专家负责，并附 ≥10 个评测任务；skill 一改就跑回归。
- Skills 格式能在 Claude Code、Codex、Gemini CLI、Genie Code、Cortex Code、TRAE、Deep Code 之间移植〔dg L226, L300〕。

### D4 上下文层

- 对应 OpenAI 的六层上下文〔dg L95〕。
- 排序：认证资产 > 仪表盘查询 > 临时查询。不要把全部查询历史做成索引〔dg L134〕。
- 个性化：默认用该用户近 90 天碰过的表。Meta 发现 88% 的查询只用这些表〔dg L104〕。
- 语义以可按需读取的文件形式提供。直接塞一个 24k 字符的 prompt，效果明显更差〔dg L112〕。
- 用户的纠正先进入审核队列，策展人通过后才成为 verified query。
- 记忆按租户隔离，防止 NFRA 所说的"记忆污染"〔dg L60〕。

### D5 模型路由与数据驻留

| 数据级别 | 可以进 prompt 的内容 | 中国 | EU | 美国 |
|---|---|---|---|---|
| C0 元数据（schema、定义、统计） | 全部 | 审批过的端点；重要数据目录的元数据只走境内 | 审批过的端点 | 审批过的端点 |
| C1 内部聚合 | 聚合结果，小格子（k<10〔假设〕）做抑制 | 境内模型，或经评估的端点 | EU 区域 | 签了 no-training 的端点 |
| C2 行级个人信息 | 不进 prompt，只在 sandbox 里由代码处理 | 境内自托管或已备案的境内 API | EU ZDR | ZDR |
| C3 敏感个人信息、重要数据、NFRA 高风险 | 只能看 schema，访问需人工批准 | 只能自托管 | 自托管，或 ZDR + DPIA | ZDR + 审批 |

- C2 及以上不能用 Claude Managed Agents 或 programmatic tool calling，二者不在 ZDR 范围内〔dg L241, L313〕。网关先做 PI 识别和假名化〔dg L72〕。
- 模型分层：
  - 便宜层：Haiku 5.5、gpt-6-luna、deepseek-flash；
  - 默认层：Sonnet 5.5、gpt-6.1-sol、Qwen3.7-max、DeepSeek V4-Pro；
  - 前沿层：只用于离线蒸馏函数库。这是 DABstep 冠军的做法：在线用便宜模型，延迟降到约 1/30〔dg L142, L159〕。

### D6 Harness 可替换

**契约**：输入 `task.json`、允许访问的 MCP 列表、skills、AGENTS.md、输出的 JSON Schema、预算；输出结构化结果和 OTel trace。

**适配器**：

- `claude -p --bare --json-schema --max-budget-usd`〔dg L228〕
- `codex exec`〔dg L244〕
- Gemini CLI
- Qwen Code
- OpenAI Agents SDK SandboxAgent
- PydanticAI 2（接 OpenAI 兼容的国产端点）

**规则**：

- CI 里必须加 `--bare`，否则仓库里的 hooks 和 `.mcp.json` 会在没有信任确认的情况下执行。
- 凡是修改 agent 配置文件的 PR，都要配置 owner 审批。
- 每季度把同一套评测在 ≥2 个 harness 上跑一遍。同等 pass^3 下每个正确任务成本降 ≥20% 就切换〔假设〕。
- 版本精确锁定：openai-agents 0.24.0、ADK 2.12.0（1.x 到 2.x 有存储格式断裂）、Claude Code 2.1.303〔dg L301〕。

### D7 自治级别

**级别定义**

| 级别 | 含义 |
|---|---|
| L0 | 只给建议 |
| L1 | 只读执行，答案带出处卡，抽样审计 |
| L2 | 只能开 PR 或提交发布申请 |
| L3 | 非生产环境可自动合并；生产上只允许可逆、幂等、在预算内的操作 |
| L4 | v1 不开放 |

这套级别也映射到 DataWorks 从 plan-only 到 yolo 的五档审批，跨厂商保持一致〔dg L198, L210〕。

**各工作流的起点、上限和晋级条件**

| 工作流 | 起点 | 12 个月上限 | 晋级条件〔假设〕 |
|---|---|---|---|
| 认证问答 | L1 | L1（董事会和对外数字永远人审） | — |
| 探索 SQL | L1 + 标签 | L1 | — |
| 语义/文档补全 | L2 | L2 | — |
| 数据工程修复/变更 | L2 | L3（dev/staging） | 连续 50 个 PR 零回滚，复核否决率 <5%，回归 pass^3 ≥90% |
| 失败任务重跑和回填 | L2 | L3 | 已有 runbook、幂等性证明，连续 20 次无误 |
| 模型晋级 | 人工 | 人工 | 永远不自动 |
| 高风险决策 | L0 | L0 | 受法规约束 |

---

## E. 可信与验证

### E1 没有 ground truth 时，用什么替代

按优先级依次使用：

1. **独立记录对账**：财务关账、计费、流水。dsharness 已经做到收入与财务关账逐月逐区精确相等。
2. **业主签字的 verified query 或黄金集**：允许一题多个正确答案。LinkedIn 约 60% 的题目有多个可接受答案〔dg L101〕。
3. **不变量、变形测试和双路径复算**：例如语义层和原始 SQL 各算一遍，或 DuckDB 和仓各算一遍。判断不了就明确标 INCONCLUSIVE。
4. **封存的 holdout 加成熟标签**：未成熟的标签不能当负例。
5. **随机实验**：没有随机化的结论标 NOT_VERIFIED_CAUSAL。
6. **合成世界的埋入真值**：用来评检查器本身。黄金集本身也要审计，公开基准的答案键有 30–60% 的错误〔dg L139, L155〕。

### E2 验证阶梯

| 级 | 内容 |
|---|---|
| V0 | 契约 / schema |
| V1 | 能执行，并且确定性重跑结果一致（pass^k） |
| V2 | 不变量：行守恒、join 基数、PIT、幂等 |
| V3 | 独立路径复算 |
| V4 | 独立记录对账 |
| V5 | 封存 holdout |
| V6 | 随机实验 |
| H | 人工专家抽样，可以加在任何一级 |

LLM judge 只用来评文字质量，不判数字：最好的代码 judge 准确率约 82%，交换回答顺序能让结果波动 14%〔dg L151〕。

| 产出 | 必须达到 |
|---|---|
| 认证答案 | V0–V1，新增 verified query 时还要过 V3，并做抽样 H |
| 探索答案 | V0–V1，并打标签 |
| 管线 PR | V0–V4 |
| 模型 | V0–V5 |
| 价值声明 | V6 |

### E3 评测

- **题量**：每个域 100–200 题，来自日志和工单，每题 2–4 种改写。另外从 20–50 个真实失败案例起步〔dg L262〕。
- **防泄漏**：30% 的题留作评测集，永远不进运行时上下文，CI 用哈希检查有没有重叠。这是 Snowflake/Genie 的规则〔dg L18, L113〕。
- **判分**：比较结果集是否等价，不比较 SQL 文本〔dg L96〕。
- **分组件评**：意图、选表、SQL 能否执行、结果是否正确分别打分，学 Uber 的 decoupled 模式〔dg L99〕。
- **能力集和回归集分开**。认证题只要出现一个回归，就阻断变更。
- **报告口径**：报 pass^3。单次成功率 75% 时，pass^3 约等于 42%〔dg L266〕。接受约 5% 的运行间噪声。
- **生产金丝雀**：每天跑 20 题〔假设〕，并报告每个正确任务的成本。

### E4 门禁

| Gate | 必需证据 |
|---|---|
| G-Ans | 走认证路径、build 状态为 READY、带定义 digest；否则必须标 EXPLORATORY 或拒答 |
| G-PR | 在克隆环境里 build 并跑测试；对账；data-diff；测试削弱检测（删测试或放宽测试需要测试 owner 批准）；SQL 策略 lint（禁止 DROP/DELETE 和写生产 schema）；许可证检查 |
| G-Rel | 受保护环境；必需审批人；禁止自审 |
| G-Model | 封存 scorer；切片；校准；泄漏检查 |
| G5 | 预注册、A/A、SRM、功效分析 |
| G6 | ITT 置信区间下界 >0 且 ≥ 业务 MDE；护栏指标无损害 |

各 gate 都沿用 A 中修正后的三状态语义。

### E5 人工复核

- **100% 人审**：董事会和对外数字、生产写入、模型晋级、新的 verified query、高风险场景。
- **抽样**：L1 答案随机抽 5–10%，低置信或新领域的答案全审。
- **复核界面**：直接展示出处卡和 data-diff。
- **防走过场**：监测否决率和审批耗时〔dg L75〕。每个答案都人工检查，会把瓶颈原样造回来〔dg L135〕。

### E6 证据

**in-toto 声明的结构**：

- subject：产物的 digest。
- predicate 包含：
  - `checks`、`policy_version`、`eval_suite_version`；
  - 各类 digest：`source_digest`、`pipeline_digest`、`semantic_model_digest`、`metric_definition_digest`；
  - `trace_id`。

**签名与存放**：用 CI OIDC 或 KMS 签名，存放在产物旁边，索引写入 value ledger。

**定义变更**：触发"答案召回"，通知所有拿到旧 digest 答案的人。

**日志保留**：EU 至少 6 个月；PIPIA 和 Colorado 要求 3 年〔dg L74〕。

### E7 多 agent 辩论与评审团

- **默认路径不用辩论**。辩论带来的增益主要来自投票，辩论本身是鞅过程〔dg L153〕。
- **允许的三种形式**：
  1. 难写的 SQL 生成 N 个候选，执行后按结果集投票或选择〔dg L140〕；
  2. 上下文干净的只读 reviewer 审 PR，每个 PR 约能多发现 2 个 bug〔dg L257〕；
  3. Challenger 只有提交可执行的反例或失败测试才算数，单纯的意见不计分。
- **DataPRM 类环境交互验证器**：best-of-N 能提升 7–11pp〔dg L152〕，放到 v2 再评估。
- **启用评审团（配置 D）的条件**：相比配置 C，静默错误再降 ≥30%，且成本 ≤1.5 倍〔假设〕。

---

## F. 交付与组织

### F1 交付机制

**环境**

- **dev**：agent 的沙箱，数据是脱敏的、合成的或零拷贝克隆，没有生产凭据。
- **CI/staging**：生产克隆，具体用 Snowflake clone、Databricks clone、BigQuery 数据集复制或 DuckDB 文件复制，只构建 `state:modified+`。
- **prod**：受保护环境。

**PR 流程**

告警 → J3 诊断 → J2 在分支上修改 → PR → CI → reviewer agent → CODEOWNER 批准 → merge → 在受保护环境部署 → attest → 发出 OpenLineage 事件。

PR 正文必须写：意图、关联事故、影响的模型、data-diff 结果、对账结果、测试增删、回滚方案。

**发布物**

- 模型：MLflow alias 锁定在发布清单里；依次走 shadow → G5 → 放量。
- 批量产出：写入带版本和 `release_id` 的表，写入要幂等。

**回滚**

| 对象 | 方式 |
|---|---|
| 代码 | git revert 后重建 |
| 表 | time travel 或 Iceberg 快照 |
| 模型 | alias 切回上一个锁定版本 |
| 答案 | 定义 digest 失效时召回 |

注意：私有仓使用 actions/attest 需要 GHE Cloud〔dg L11〕。

### F2 角色与职责

| 角色 | 职责 |
|---|---|
| 业务数据产品负责人 | 定义价值，批准实验预算，签署 kill 标准 |
| 每域指标策展人（0.5 FTE） | 维护 OSI 定义和 verified queries；反馈队列 2 个工作日内处理〔假设〕 |
| 分析工程师（CODEOWNER） | 审 agent PR，维护测试 |
| 平台工程（1–1.5 FTE） | 网关、gate、身份、沙箱、成本 |
| 评测负责人（0.5 FTE） | 审计黄金集，管理回归闸门，做 harness 一致性测试 |
| 模型风险/合规 | 用例分级登记，PIA/DPIA |
| 财务 BP | 核验 value ledger 中的现金价值 |

小团队可以一人兼多职，但机器身份必须分开〔dg L12〕。

### F3 变革管理

- **嵌入现有工具**：LinkedIn 把 SQL Bot 嵌进 DARWIN 后，使用量提升了 5–10 倍〔dg L123〕。
- **推广方式**：先给 champions；培训"怎么读出处卡"；每周做质量复盘；每月公开准确率和价值报告。
- **用分批上线测价值**：按团队错开上线，把它当准实验来估计价值。
- **定位为增强，不是替代**：AI 暴露职业中 22–25 岁员工的就业比趋势低 19%〔dg L68〕。保留初级人员的培养路径，让他们转做策展人和复核人。
- **防止绕过治理**：47% 的企业承认为赶上线绕过过治理政策〔dg L69〕。对策是让受治理的路径本身就是最快的路径。

---

## G. 路线图

### 0–90 天（截止约 2027-01-17）

| 周 | 工作 |
|---|---|
| 1–2 | 选 1 个域（收入/订阅或订单）；做时间-动作基线；整理 100 题和 20 个历史事故；用例分级登记 |
| 3–6 | ctx v1：30 个指标、150 条 verified query、4 个 skills；dsh-mcp v1（8 个工具）或直接接平台 MCP；开放给 10 名 champion |
| 5–10 | 一个 dbt 项目接入 G-PR；回放 20 个历史事故；跑缺陷注入包，得出"测试充分度" |
| 9–12 | value ledger 上线；90 天评审 |

退出条件：达到 B6 的阈值；策略违规为 0；系统能在 2 个 harness 上运行。

### 3–6 个月

- 扩到 3 个域；上线 enrichment；数据工程放到 L3（staging）；上线 ab-readout。
- 第一个 uplift 或预测任务走到 G5；落地中国、EU、美国三套策略包。
- 退出条件：
  - 3 个域结果匹配率 ≥85%；
  - MTTR 下降 30%；
  - 机械工单占比下降 10pp；
  - 价值/成本 ≥1.5。

### 6–12 个月

- 扩到 6–10 个域；跨两个引擎做语义同步和双路径复算；3 个以上周期性模型；决策日志。
- 自动生成 model card 和 PIA/DPIA 草稿；每季度"拆掉多余脚手架"〔dg L322〕；做一次厂商替换演练。
- 退出条件：
  - 价值/成本 ≥3；
  - 认证类问题自助解决 ≥60%；
  - agent 写入导致的 Sev1 为 0；
  - 2 周内完成模型或 harness 替换，回归 ≤3pp。
- 时间线要对齐合规日历：ADMT 2027-01-01、EU Annex III 2027-12-02〔dg L82〕。

---

## H. 参考实现下一步

### H0 现状判定（已核对代码）

**已经有的**

- `world/generator.py`：埋入了 D1–D9 缺陷，生成 custody 真值，按 6 个 cutoff 导出数据。
- `lake/`：
  - Pandera 契约，按版本区分 v1/v2；
  - dbt 风格的 runner：上游失败时下游 SKIPPED；
  - 原子发布；
  - 收入与财务关账精确对账；
  - PII 在 `stg_customers` 截断，另有 `bronze_safe` 视图。
- 实际构建结果：4 个 READY；2026-08-31 和 2026-09-30 两个因为 billing v2 导致 `stg_invoices` 报 ERROR，状态 FAILED，符合预期。

**缺口和问题**

1. `pyproject.toml` 声明了 `dsh = "dsharness.cli:main"`，但 `cli.py` 不存在。`mcp` 依赖已声明，但没有 server。`tests/` 为空。`workspace.py` 引用的 `docs/DESIGN.zh-CN.md` 也不存在。
2. **实验目前观测不到**。`truth_experiment` 只有潜在结果（p0、p1、u），没有导出 assignment、exposure 或 outcome；7 月的流失全部按"未处理"模拟。所以业务闭环现在无法测量。
3. `invoices.sql` 的单位修复规则没有限定范围：只要 charge <1000 且乘 100 后在价目表里，就会被"修正"。如果其他区域出现新的单位事故，会被静默抹平，不会告警。规则应限定在 `region='south' AND revenue_month='2026-03'`，并加一条测试：修正行数必须等于事故范围内的行数。
4. `charge_amount_on_price_book` 测试没有考虑价格的有效期。

**基于 custody 的实算〔实算〕**

- 7 月处于风险中的客户 3,579 人：persuadable 1,117 人，sleeping dog 389 人。
- 联系 300 人时的期望挽回数：按风险排序 13.5，按 oracle uplift 排序 24.7，随机 3.3。
- 全量联系的真实 ATE 是 1.1pp。单月 50/50 实验的 MDE₈₀ 约 2.0pp，功效约 34%。所以诚实的结论应当是"功效不足，结果不确定"。

### H1 待实现模块

总计约 10–12 周、1–2 人〔假设〕。

1. **补齐与修正**：加 `cli.py`（`dsh world|lake|mcp|bench|gate`）、pytest；修正第 3、4 点。
2. **换成真实工具链**：把 pipeline 改为 dbt-core 1.12 + dbt-duckdb，加 MetricFlow semantic models，定义约 12 个指标（净收入、退款、MRR、活跃数、logo churn、ARPU、add-on 附着率、工单率等），带 `definition_digest`；用 dbt-mcp 暴露。dbt-duckdb 的版本〔需核实〕。目的是让 agent 练的是企业真实在用的接口。
3. **`dsharness/mcp/server.py`**：基于 mcp 2.3 / FastMCP 4.1，无状态，8 个工具。
   - 只打开 READY build（复用 `latest_ready_build`），只暴露 `silver`、`staging`、`bronze_safe`。
   - DuckDB 连接设置 `enable_external_access=false` 和 `lock_configuration=true`，再加 sqlglot 白名单（只允许 SELECT），防止通过 `read_parquet` 读到 custody。
4. **世界扩展**：
   - 导出 `crm_retention_contacts`：随机化分组 + exposure，其中 15% 未触达，用来考察 ITT 纪律；对 treated 组施加 p1；可选地注入 SRM 故障（丢失 3% 的 treated 日志）。
   - D10：财务从 2026-09 起改变净收入口径，`definition_digest` 随之变化，触发答案召回。
   - D11：在工单正文里放提示注入文本和手机号，测试 PI 清洗和工具权限。
5. **`dsharness/bench/`**，五个任务族：
   - (a) 问答约 150 题：陷阱题覆盖 D1–D5，另含歧义题（需要澄清）和不可答题（需要拒答）；
   - (b) 修复 D8：让 09-30 build 恢复 READY，8 月收入对账精确，且不能削弱测试；
   - (c) PIT 特征：与 PIT_CUTOFFS 导出的历史版本逐值一致；
   - (d) uplift 选人：在 K=300 下按 custody 计算策略价值；
   - (e) 实验读数：判分看三点——点估计和置信区间算对；置信区间上界超过 MDE 时不能说"无效"；要指出功效不足。
6. **`dsharness/gate/` 和 `value/`**：实现修正后的三状态 gate 和 in-toto 声明（本地输出明确标 UNSIGNED-LOCAL）；DuckDB 中建台账表。
7. **运行矩阵**：配置 A（原始 bronze + 强模型）、B（+ 语义层和 MCP）、C（+ 检查和 gate）、D（+ 评审团），每个配置在 ≥2 个 harness 上跑，其中至少一个是国产模型（Qwen 或 DeepSeek），每题跑 3 次。trace 写入 MLflow 3.17。

### H2 指标与预注册假设

阈值为〔假设〕。

**指标**：结果匹配率、pass^3、静默错误率（答错且没有拒答、没有标签）、拒答正确率、各类缺陷 D1–D11 的拦截召回、误拦率、测试削弱尝试次数（被接受的必须为 0）、每个正确任务的成本和 p95 延迟、custody 读取或注入指令执行次数（必须为 0）、uplift 策略后悔值。

| 假设 | 内容 | 不成立时的含义 |
|---|---|---|
| H-1 | 陷阱题上 B 比 A 高 ≥15pp | 低于 8pp，说明"语义层是主杠杆"在这个世界里不成立 |
| H-2 | C 的静默错误率 ≤ B 的一半 | — |
| H-3 | gate 对埋入缺陷的召回 ≥95%，误拦率 ≤10% | — |
| H-4 | agent 建的 uplift 策略挽回数 ≥16.2（比风险排序高 20%） | — |
| H-5 | D 相对 C 的增益 <3pp 且成本 ≥2 倍 | 成立则不采用评审团 |

**SMB 与企业两个剖面的验证**：同一份语义定义，用 sqlglot 转成第二种方言，在第二个引擎上复算，结果必须一致（V3）。

---

## I. 风险与反例

| 风险 | 早期信号 | 缓解 |
|---|---|---|
| 语义覆盖不足，大部分问题落到探索路径（无策展时准确率 10–30%） | 探索路径流量 >50% | 只在认证域开放问答；按需求做语义 |
| 策展成本超过价值（中国更明显） | 每条 >1 小时、价值/成本 <1 | 先选量大的域；用 enrichment 自动起草 |
| 平台吞掉这一层（Cortex AI Gateway、Quotient、Agents Schema〔dg L182–L183, L192〕） | 原生功能已覆盖跨平台需求 | 层足够薄；资产在 git 里，被吞也不浪费 |
| 检查之后仍有静默错误 | 埋入缺陷召回 <80% | 补检查、加 V3 和 V4 |
| 黄金集过拟合，或泄漏进上下文 | 评测分高，但审计错误率也高 | 哈希隔离、季度轮换、审计黄金集 |
| token 涨价或断供 | 单价上涨 >30% | 多供应商、自托管兜底、预算上限 |
| 数据中的提示注入，或越权 | 注入执行次数 >0 | 在工具层执行权限、沙箱、出站白名单、不透传令牌 |
| 审批走过场，或绕过治理 | 审批 <10 秒、否决率约 0 | 风险抽样，让受治理路径最快 |
| 被 Fivetran（dbt、SQLMesh、GX）锁定，或许可证变更 | 新版本改许可证 | 锚定 OSI 和 SQL，许可证门禁 |
| 监管分级错误 | 高风险用例没有登记 | 入口强制分级，高风险设为 L0 |

**哪些结果会证明本设计是错的**

1. 在企业自己的黄金集上，原始 schema + 强模型与语义层的差距 <3pp。这说明语义投资不必要，只买原生对话就够。
2. 单平台企业里，原生 agent 加厂商自带评测对埋入缺陷的拦截率与我们相当。这说明不该建网关，只保留资产和价值台账。
3. 6 个月时，3 个域中有 ≥2 个的价值/成本 <1.5。
4. gate 误拦率 >15%，导致大规模绕过。
5. 合并后的 agent PR 回滚率持续 >10%。

出现任何一条，就收缩为"资产即代码 + 平台原生 agent + 价值台账"，不再继续建控制面。