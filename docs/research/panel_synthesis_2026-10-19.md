# DS Harness 最终蓝图 v1.0（四方案合并定稿）

> 日期：2026-10-19。引用约定：〔dg Lnn〕指 `docs/_research_digest_2026-10-19.txt` 第 nn 行，本次逐条对照复核过。【假设】是没有证据支撑的工程估计，试点中要用账本实测值替换；【实算】是我在 2026-10-19 对 `dsharness/workspace` 的 custody 与构建产物重新计算的结果；【待核实】表示 digest 没有覆盖，需要供应商或法务确认。
>
> **合并方法**：骨架取 platform 方案。三位评委的总分是 platform 173、trust 171、value 168、agent 166，platform 的复用纪律和可行性最强。platform 最弱的一项是认证答案只要求 V0–V1，这一项用 trust 的信任分级、回溯评分和抽样统计补上。经济模型和 kill 结构取 value，并修正它漏算的平台人力。Agent 护栏、答案证书和基准缺陷族取 agent。下面所有被评委指出的事实错误都已改正，第 10 节逐条记录了分歧的裁决。

---

## 0. 一页结论

1. **建什么**：在企业现有数据平台之上，建一层薄的、不绑定厂商的**可验证交付层**，由四类自建资产组成：
   - 上下文资产：语义层、认证表、verified queries、Skills、AGENTS.md；
   - 确定性检查与 CI 门禁；
   - agent 评测集；
   - 价值账本。

   agent loop、NL2SQL、仓库、调度器、Catalog、模型注册表、沙箱、LLM 网关全部购买或复用。自建代码目标 ≤10k 行，硬上限 15k 行【假设】。
2. **为什么这样建**：
   - 生成能力已经商品化。CoWork/CoCo、Genie Code、BigQuery DE/DS agent、DataWorks Data Agent 都已 GA〔dg L179–L198〕。
   - 稀缺的是语义和验证。语义层的独立增益是 +8~25pp，而且能把错答变成拒答〔dg L148〕。
   - 约 70% 的失败表现为"看起来正常的错答"〔dg L150〕。
3. **先做哪两条工作流**：在同一个域（收入/订阅）里做两条：
   - **W1 管道断修/变更**。oracle 是 CI 加精确对账，成本低，第 1 周就能跑，用来先挣自治。
   - **W2 认证指标问数**，用来先挣采纳。

   W2 对用户开放有三个前提：golden set ≥150 题；核心 30 个指标已建模；实测的可治理请求量超过盈亏线。量不够时直接用平台原生问数，自己只持有 golden set、语义层和账本。ML 放在第二波，以 uplift 和实验为先，不把 AUC 当交付物。
4. **拓扑**：单写者，加只读子 agent，再加确定性脚本做骨架。不做角色扮演式的多 agent，不做辩论。评审团和投票只有在预注册的消融实验中过线才保留（见 5.7）。
5. **两套标签**：对内用一条证据阶梯 V0–V5，对用户用信任标签 T0–T3。没有标签的数字不得进入 KPI、董事会材料或对外材料。
6. **自治靠挣**：
   - 认证答案直接回给提问者，属于 L1，带标签，并做 5% 随机抽审。
   - 进入共享看板，需要该任务类 n≥150 次抽审 0 错。
   - 12 个月内，生产环境的 L4 只开放"配额内幂等重跑失败任务"。
   - 指标口径变更、契约变更、回填、模型晋级，永不自动。
7. **中国侧**：默认执行器用境内栈（DataWorks Data Agent，或 Qwen Code 配 Qwen/DeepSeek/GLM）。中国个人信息和重要数据只走境内模型〔dg L58, L73〕。境内总部访问海外个人数据属于反向传输，海外个人数据只回传聚合结果。
8. **价值只认三条可审计渠道**：
   - 已核验正确的基线需求 × 净节省时间 × κ。κ 默认 0.3，只有财务确认了现金事件才取 1。
   - 被替代的外部支出。
   - G6 实验增量的 95% 置信下界。

   重算后，单域只靠省工时，盈亏点约为每月 2,000–4,400 个可治理请求【假设】。所以必须让多个域、多条工作流分摊固定成本，或者叠加外部支出和决策价值。
9. **90 天出口**：阈值试点前签字，第 0–2 周测完基线后再校准。
   - 认证范围 held-out pass^3 ≥85%。
   - 抽审（n≥200）的静默错误率 95% 上界 ≤3%。
   - 越界题拒答 ≥90%。
   - CPVC ≤ 人工单位成本的 20%。
   - 目标人群 WAU ≥30%。
   - DE PR 无大改合并率 ≥40%，且对账零逃逸。
10. **dsharness 是测量仪器**。下一步先按已绑定的 IMPLEMENTATION_SPEC 补齐 W3/W5/W6（MCP、工具、基准、CLI），再新增以下内容：
    - 财务调整项，以及支持"已解释差异"的对账；
    - 可观测的历史随机接触记录，加只能调用一次的 oracle；
    - 预修复管道变体和隐藏种子缺陷；
    - 对账独立性陷阱。

    然后用 A–E 臂分别量出语义层、检查门禁、评审团、境内 harness 各自的增益。

---

## 1. A 核心论断

1. **系统定义**：一个原生带验证的 DS/DE 交付层。agent 负责生成（买现成的），harness 负责验证（自建薄层），产出由具名的人负责。
2. **它不是什么**：
   - 不是通用 NL2SQL 或 ChatBI，也不是 notebook copilot、AutoML、agent 框架、新调度器、证据数据库；
   - 不是多角色的"AI 数据团队"；
   - 不是裁员工具；
   - 不在信贷、核保、雇佣以及 NFRA 高风险清单〔dg L60〕中充当决策者。
3. **杠杆在语义和验证，不在模型**：
   - Meta 换了更强的模型后，效果停在"自信的错答"〔dg L105〕；
   - DABstep 消融中 Hard 题从 13.9% 升到 55.1%〔dg L112〕，但这是单作者预印本，未经同行评审；
   - dbt 的实验显示语义层带来 +8~25pp〔dg L148〕。
4. **头号风险是静默错误**。自我反思消除不了它〔dg L176〕。所以每个交付物都必须带 tier 和证书，系统也必须能拒答。
5. **上线顺序按可验证程度排，而不是按生命周期排**：DE（有独立 oracle）→ 问数（语义加 golden set）→ 模型（真值延迟到来）→ 决策（要做实验）。流程覆盖端到端，但各阶段的自治程度不一样。
6. **写操作只有一个写者**〔dg L257, L289〕。多 agent 约消耗 15 倍 token〔dg L256〕。扇出交给脚本或调度器。
7. **交付单位只有三种**：
   - 状态变更走 PR，包括管道、语义、上下文、模型，以及门禁本身；
   - 回答附答案证书；
   - 业务影响用"决策记录 + 实验"表达。

   三者都不是的产出，不算交付。
8. **资产即代码，只绑定开放标准**：SQL、Iceberg、OSI/MetricFlow、OpenLineage、MCP 2026-07-28、Agent Skills、AGENTS.md、in-toto、OTel。原因是厂商产品可能 9 个月就从 GA 变成 legacy〔dg L186, L208〕。
9. **中国和海外是两套经济学**：中国分析和工程人员的人力成本约为美国的 1/3–1/2〔dg L404〕，所以中国的价值必须落在决策速度、单人覆盖规模、收入类 KPI 和外包替代上〔dg L411〕。
10. **dsharness 是测量仪器，不是演示**。它用埋入的真值和缺陷，测"任意 agent 加上本设计"之后的静默错误率和拦截率。

**对 v0.1 草案的处置**

| 处置 | 内容 |
|---|---|
| 保留 | execution / check / promotion 三维状态；缺失、ERROR、INCONCLUSIVE 一律判 HOLD；PIT 和标签成熟规则；按身份隔离的封存 holdout；G5（有限实验）和 G6（价值声明）分开；Shadow 不算价值证据〔dg L2–L4〕；采集与标注规则（草案 6.1/6.2，并入 3.2） |
| 删除 | 自研 HMAC 证据账本、17 表 PG、13 端点 API、reconciler〔dg L11〕；强制 Dagster〔dg L13〕；Agent Adapter 排在 PR8；企业场景下的"原始数据本地优先"〔dg L25〕；以按流失风险排序作为首个纵向场景〔dg L24〕 |
| 修复 gate | 先认证/授权，再计数；增加 supersede 和重试语义；无法验证的输入判 HOLD 并告警，只有经过认证的 FAIL 才能 REJECT〔dg L8–L9, L29, L38〕。参考实现的 `evidence/gate.py` 已按此实现（`tests/test_gate.py` 42 个用例） |

---

## 2. B 价值与经济模型

### 2.1 用例组合

按"可度量价值 × 可验证性 × 可行性"排序。

| # | 工作流 | 主 KPI（一律与审计正确率配对报告） | oracle | 可行性证据 | 自治：起始→12 月上限 | 优先级 |
|---|---|---|---|---|---|---|
| W1 | DE 断修/变更：schema drift 适配、失败任务分诊、dbt/SQL 变更、按既有模式新建模型、回填计划；agent 生成 dlt source 和契约 PR | MTTR、数据停摆时长、无大改合并率、30 天回滚率、逃逸缺陷数 | CI 测试、data-diff、精确对账 | EL 约 96%；**构建**转换 23–33%（ELT-Bench-Verified 测的是构建不是修复），pass^3 比 pass@1 低 8–16pt〔dg L145〕；语法和逻辑 bug 修得好，数据层问题必须查数据〔dg L146〕；OpenAI 迁移约 1 万个 DAG，Grab 有 Scarlet〔dg L127〕 | L2→L3（仅非生产） | P0 |
| W2 | 认证指标问数/取数 | 自助解决率、机械工单占比、首答时间 | 语义定义、verified query、延迟真值回溯 | 建模 schema 上 text-to-SQL 84.1–90.0%，走语义层 98.2–100%〔dg L148〕；digest 综合为"策展 schema 约 80–90%"〔dg L157〕；裸仓库在 BEAVER 上 10.8%〔dg L141〕；Grab 的 SQL 自助解决率从 50% 升到 81%〔dg L106〕 | 回答提问者 L1；进入共享看板 L3，需挣得 | P0（以过量线为前提） |
| W3 | 数据质量与对账监控、分诊 | 消费前拦截数、发现到定位的时间、误报率 | 对账、不变量、种植缺陷召回 | 确定性检查；有 runbook 时可行（Scarlet）〔dg L106〕 | L1→L2 | P1 |
| W4 | A/B 读数 Skill | 读数周期、被拦下的功效/SRM 错误数 | 确定性统计库 | 快手 /abtestSkill〔dg L109〕 | L2→L3（仅内部报告） | P1 |
| W5 | 替代外包的报表、数据运营、标注 | 财务确认的已终止合同金额 | 合同 | 替代外部支出是最清晰的 ROI 来源〔dg L387〕 | 视任务而定 | P1（有外包时） |
| W6 | 决策模型工厂：留存 uplift、需求预测、定价 | G6 实验 ITT 增量毛利的 LCB95 | 封存 holdout、随机实验 | MLE-bench 获牌率 61–64%〔dg L143〕；按风险排序 ≠ 按价值排序〔dg L24〕 | 训练 L2，晋级永远人工 | P2 |
| W7 | 开放式归因、因果问题 | 决策时效 | 多数没有 | BIRD-Interact 8.67–17%〔dg L141〕；超出能力前沿时过度信任，正确率从 84.5% 降到 60–70.6%〔dg L396〕 | L0–L1，只作参考 | 不自动化 |

**不做**：
- 高风险决策者；
- 通用 NL2SQL 引擎；
- notebook copilot；
- 开放网页抓取，因为受网络数据条例第 18、24 条约束〔dg L56〕。

### 2.2 价值公式

```
NV_月 = Σ_w κ·N_w^base·a_w·(t0_w − t_r,w)·c_h      ① 只计原本会进人工队列、现在被分流的基线需求
      + V_ext                                       ② 财务确认的已终止外部合同
      + Σ_m LCB95(ΔKPI_m)·u_m                        ③ 只计通过 G6 的实验增量，取 95% 置信下界
      − Σ_w N_w·a_w·ε_w^UB·L_w                       ④ 已交付错误的期望损失
      − (C_llm + C_compute + C_review + C_curation + C_platform_eng + C_build摊销 + C_vendor)
CPVC（每个经核验正确任务的成本） = 总成本 / (N × 审计正确率)
```

参数规则：

| 参数 | 含义与取值规则 |
|---|---|
| a | 经抽审判定正确的接受率 |
| t0 | 2 周时间-动作法测量，或 IM 登记机器人计时，不用自报 |
| t_r | 剩余人工时间，含提问者自己核对的时间 |
| κ | 默认 0.3【假设】。只有发生现金事件（合同终止、招聘需求撤回、积压实测下降）并经财务确认后才取 1。中国侧的工时只报为产能，不计为现金 |
| ε^UB | 随机抽审得到的 95% 上界。不用自报，也不用错误发现队列的数据 |
| L | 来自事故台账：每次逃逸的错误由业务 owner 估计损失，按季度取各工作流中位数 |
| 诱发需求 | Ramp 4 周内 agent 频道 1,476 问，原求助频道只有 66 问〔dg L107〕。新诱发的需求只有挂到某条决策记录上才计价值 |

### 2.3 成本模型

| 任务 | token 量【假设】 | 前沿中档 | 境内 | 主要成本 |
|---|---|---|---|---|
| 认证问答 | 输入 60k（缓存命中 70%）、输出 3k，参照 Vercel 约 61k〔dg L98〕 | Sonnet 5.5 约 $0.07，计入 4.7+ tokenizer 约 +30% 后约 $0.09〔dg L400〕 | V4-Pro 离峰约 $0.02、峰时约 $0.04；flash 约 $0.005〔dg L401〕 | 5% 抽审 × 每次 5 分钟 |
| DE 断修 PR | 0.5–3M | Sonnet $1–5，Opus 5.5 $2–10，用 `--max-budget-usd` 封顶 | V4-Pro $0.5–3 | 人工复核 15–30 分钟，加克隆环境的 CI 计算 |
| 模型候选一轮 | 0.5–2M，另加训练算力 | $1–10 | — | DS 复核 1–2 小时 |
| 全量回归（450 题 × 3 次 × 2 个 harness） | — | 约 $150–250/次 | 离峰批量跑 | 每次 pin 变更都要跑 |

**平台计费**（预算一律按促销结束后的价格做）：

| 平台 | 计费 |
|---|---|
| Snowflake | AI Credit $2.00；区域路由 $2.20，即数据驻留约贵 10%〔dg L181〕 |
| Databricks Genie | 对用户免费到 2027-01-31，但 service principal 的调用当下就计费〔dg L185〕 |
| Google data agents | 约 $3/$20 每百万 data token〔dg L190〕 |
| DataWorks | ¥0.05/credit〔dg L198〕；每问消耗多少 credit 未知（见第 12 节） |

**结论**：

1. 试点规模下，token 占 TCO 的 10–15% 以下【假设】，主要成本是策展和复核。设计目标是压低"每个被接受产出的复核分钟数"。
2. 规模上来后 token 不能忽略：快手人均每周 55 次对话〔dg L109〕；64% 的企业把 token/API 成本列为主要障碍〔dg L392〕。
3. agent 生成的探索 SQL 会带来仓库扫描费（MaxCompute 按扫描量计费、Snowflake 仓库 credit）。所以工具层必须做 dry-run 成本估算、按用户和按 agent 的配额、分区裁剪 lint（见 4.2）。
4. 中国 2026 年在涨价：DeepSeek 8 月上调 50%–1,100%，还出现过约 12 小时宕机〔dg L402〕。DeepSeek 的峰时是 UTC 01–04 和 06–10，正好是北京时间 09–12 和 14–18〔dg L401〕。交互式问数只能付峰价，富化和评测一律离峰批量跑。

### 2.4 盈亏平衡（修正版，全部为【假设】）

通用参数：a=0.5，Δt=35 分钟。每请求变动成本（token + 计算 + 5% 抽审 + 以 ε=1%、L≈$20/¥100 计的错误损失）为：美国 $0.73；中国非私营 ¥2.2；中国私营 ¥1.85。

首域固定成本按 1.75 FTE 当量计：steward 0.5 + eval 0.25 + 平台工程 0.5 + 建设摊销 0.5。value 方案漏算了平台工程，这里补上。后续每个域按 0.75 FTE 计。

| 口径 | 全成本时薪 | κ=0.3 / 0.5 时每请求价值 | 首域固定成本/月 | 首域 N*（κ=0.3 / 0.5） | 后续域 N* |
|---|---|---|---|---|---|
| 美国 | $95（$120,230÷0.70÷1,800h〔dg L403〕） | $8.3 / $13.9 | 约 $25k | 约 3,300 / 1,900 | 约 1,400 / 820 |
| 中国非私营 | ¥168（¥248,752×1.35÷2,000h〔dg L404〕） | ¥14.9 / ¥24.8 | 约 ¥49k | 约 3,900 / 2,200 | 约 1,650 / 930 |
| 中国私营 | ¥87（¥128,166×1.35÷2,000h〔dg L404〕） | ¥7.5 / ¥12.5 | 约 ¥25k | 约 4,400 / 2,300 | 约 1,900 / 1,000 |

（N* 单位：可治理请求/月）

由此得出三条规则：

- 每月可治理请求低于首域 N* 时，**不自建 W2 的数据访问层**。用平台原生问数（CoWork、Genie、DataWorks ChatBI），自己只持有 golden set、语义层和账本，同时让 W1 与 W2 分摊平台工程。
- 中国口径下，只算工时多半是负值。立项依据要换成决策速度、规模、收入 KPI 或外包替代。
- 注意 Ramp 的结构：基线需求只有 66 问，诱发需求是 1,476 问。只按基线计价值时，"省工时"的论证往往很薄。

### 2.5 参考场景：落到人头和预算（全部为【假设】）

场景：6 人数据团队，年预算 ¥300 万，境内用 MaxCompute/DataWorks，海外用 Snowflake。

**人员（约 ¥200 万，按 6 × ¥33.6 万全成本计）**

| 人数 | 角色 | 职责 |
|---|---|---|
| 2 | 平台/harness 工程师 | dsh-mcp、CI 门禁、沙箱、身份、路由配置、FinOps、SLO |
| 1 | 评测 owner | golden set、判分器校验、bench、审查金丝雀 |
| 1 | DS/统计 | W4、W6、因果签字 |
| 2 | 分析工程师 | W1 的 CODEOWNER，负责语义建模 |

steward 由业务方出资，每个域 0.25–0.5 FTE，这是域准入的前提。

**非人力（约 ¥100 万）**

| 项目 | 金额 |
|---|---|
| 模型 token 与平台 credit | ¥15 万 |
| agent 查询与 CI 克隆的仓库计算 | ¥25 万 |
| 沙箱、k8s、MLflow、Langfuse 等基础设施 | ¥15 万 |
| GitLab、KMS、SBOM 等工具 | ¥5 万 |
| 合规：PIPIA、跨境评估、外部红队 | ¥15 万 |
| 涨价与促销到期备用金 | ¥20–25 万 |

### 2.6 中国与海外的差异

| 维度 | 中国 | 海外 |
|---|---|---|
| 默认执行器 | DataWorks Data Agent：Qwen Code 内核，支持 SKILL.md，五档审批，可选 qwen3.7-max / deepseek-v4-pro / glm-5.2〔dg L198〕。也可自运行 Qwen Code / OpenHands + 境内模型。另有火山 Data Agent、腾讯 DataBuddy〔dg L199〕 | CoWork/CoCo、Genie Code/Agents、BigQuery DE/DS agent〔dg L179–L189〕；自运行用 `claude -p --bare` 或 `codex exec` |
| 供应商可得性 | 中国控股实体（含其海外子公司）使用 Anthropic/OpenAI 可能受合同限制。digest 只写了"may be restricted"〔dg L300〕【待法务核实】。因此境内栈是默认，不是对照臂 | 按合同决定 |
| 价值主轴 | 速度、规模、收入 KPI、外包替代。"AI+"政策设定 2027 年智能体渗透率 70% 的目标，带来的是预算，不等于价值〔dg L407〕 | 工时和吞吐也能成立 |
| 驻留与出境 | 个人信息和重要数据只走境内模型。调用境外 API 即算出境，触发 10 万 / 100 万 / 敏感 1 万的门槛〔dg L58〕 | EU 用区域端点 + ZDR，不能只依赖 DPF，因为 C-703/25 P 尚未判决〔dg L53〕 |
| **反向传输**（新增） | 总部或境内 agent 查询海外个人数据，构成 GDPR 第三国传输。二选一：SCC + 传输影响评估（TIA）；或者个人数据分析留在区域内，只回传聚合结果【待法务】 | 同左 |
| 监管 | 内部工具不需要备案〔dg L54〕；外发内容要加标识（2025-09-01 起）〔dg L55〕；PIPIA 记录保存 3 年〔dg L57〕；银行保险业适用 NFRA 8 号文：高风险由人做最终决定、禁止用个人信息训练、第 25 条点名记忆污染〔dg L60〕 | EU Art.50 已生效，Annex III 推迟到 2027-12-02〔dg L46–L47〕；SR 26-2 排除 GenAI 和 agentic〔dg L64〕；Colorado 与 California ADMT 2027-01-01 生效〔dg L62–L63〕；Reg B 要求给出拒绝理由〔dg L65〕 |
| 需求入口与基线 | 需求多在钉钉/飞书/企微私聊里，没有工单。第 0 周先上线 IM 登记机器人，测 2–4 周基线 | Slack/Teams、工单系统 |
| CI 与签名 | 自建 GitLab 或云效 + cosign + 企业 KMS；能否访问公网 Sigstore【待核实】 | GitHub actions/attest，私有仓库需要 GHE Cloud〔dg L11〕 |
| 语义系统记录 | 运行时用 DataWorks 数据建模/指标或 Quick BI，OSI YAML 作为可移植副本。MetricFlow 和 sqlglot 是否支持 MaxCompute 方言【待核实】；暂按 Hive/Spark 方言近似，以执行结果校验 | 平台语义视图、UC metric views、LookML，加 OSI 副本〔dg L203〕 |

### 2.7 Kill 标准

阈值都是【假设】，试点前由业务 owner 签字。

| 节点 | 触发条件 | 处置 |
|---|---|---|
| D+14 | 没有可用基线：既没有工单日志，也没有 IM 登记数据 | 不立项；先上线登记机器人，测 2–4 周 |
| D+45 | W2 认证集 pass@1 <75%，且越界拒答 <80% | 缩域到 ≤20 个指标，或停止 |
| D+45 | W1 回放 20 个历史事故，正确分诊 <40% | 停止写入，只做分诊 |
| D+90 | W2 出现以下任一：held-out pass^3 <75%；抽审静默错误率 95% 上界 >5%（n≥200）；WAU <20%；自助解决率比基线提升 <10pp | 退回 L1 内测，或 kill |
| D+90 | W1 出现以下任一：无大改合并率 <40%；30 天回滚率 >10%；任何一起由 agent 引发的 Sev-1 | 降为只做分诊 |
| D+90 | CPVC 连续 2 个月超过人工单位成本的 30%；或 HOLD 率 >20% 且出现绕过 | 调整路由或门禁粒度；调整后仍不达标则停止 |
| 任意时点 | 一起门禁没拦住、由 agent 导致的对外或监管数字错误 | 该工作流停摆复盘，降一级 |
| 任意时点 | steward 岗位空缺 >60 天，或纠错 PR 积压 >2 周 | 冻结该域 |
| M+6 | 保守口径 NV ≤0 且没有任何 V5 决策证据；或探索路径流量 >60% | 收缩为"资产 + 原生 agent + 账本" |
| M+6 | 平台替代测试：原生 agent 在本企业 golden set 上，正确率和静默错误率都与 C 配置相差 <3pp | 停建工具面，只维护资产、评测和账本 |
| W6 | 6 个月内拿不到随机化授权；或实验 LCB95 ≤0 | 不立项，或不扩量。如实的 null 结果也算合格产出 |

---

## 3. C 端到端架构

### 3.1 分层与数据流

```
入口(复用)   钉钉/飞书/企微/Slack · BI · IDE/CLI · 工单 · IM 登记机器人
   │ SSO → MCP EMA(ID-JAG) / OAuth 2.1 → OBO 透传到仓库 RLS
执行者(可替换) 平台原生: DataWorks Data Agent | CoWork/CoCo | Genie Code
               自运行:   Qwen Code | OpenHands | claude -p --bare | codex exec
   │ AGENTS.md + Skills + dsh-mcp(全集 ≤13 工具，每角色 ≤10) + hooks + 预算上限
┌─ DS Harness 薄层(自建) ───────────────────────────────────────────────┐
│ ctx 上下文仓(git) │ dsh-checks/faults │ dsh-eval │ dsh-gate(CI 策略步) │ dsh-value 账本 │
│ 信任标签/答案证书 │ 回溯评分器 │ 扫描成本守卫 │ 路由策略 YAML(由网关执行)              │
└───┬ push-down SQL ──────────┬ PR/CI ──────────────┬ trace/成本/血缘 ──────────┘
数据平台(复用) 采集(dlt/DataWorks DI/CDC) → 仓/湖(MaxCompute | Snowflake | DuckDB+DuckLake; Iceberg REST)
               → 转换(dbt/SQLMesh/DataWorks) ← 既有调度 → 语义层 → Catalog+OpenLineage → MLflow → 实验平台
沙箱(复用)   k8s agent-sandbox 1.0.7(gVisor/Kata) | 自托管 E2B | sandbox-runtime / Docker network=none
网关(复用)   agentgateway 1.6 | MLflow AI Gateway | Cortex AI Gateway(预览)
```

### 3.2 各阶段：agent 做什么、怎么验、复用什么、建什么

| 阶段 | agent 的工作（自治级） | 验证 | 复用 | 自建 |
|---|---|---|---|---|
| 采集 | 写 dlt source、契约草案、埋点规范，均以 PR 提交（L2） | 源级控制总数（行数、金额）对账；PI 扫描；schema 版本登记 | dlt 1.31、DataWorks DI、Fivetran、CDC〔dg L358〕 | 契约模板、控制总数检查 |
| 处理 | 写 dbt/SQL 变更 PR（L2→L3 非生产） | 测试、Pandera、不变量（声明的基数、守恒、按事件身份去重）、data-diff、精确对账 | dbt-core 1.12/1.13 或 dbt-oss 2.x、SQLMesh 0.236、GX Core 1.24〔dg L338–L341〕 | 不变量库、对账框架（含已解释差异） |
| 语义/上下文 | 起草文档和语义补全 PR（L2） | 定义 digest、steward 审核、回归 eval | 平台语义对象、MetricFlow 0.213、Cube 1.8、dbt-mcp 2.7 | ctx 仓、OSI 同步、夜间富化 |
| 特征 | 生成 PIT 特征 SQL（L2） | available_at ≤ cutoff；as-of 重算比对；静态时间检查 | 仓库 time travel、Iceberg 1.12、DuckLake 1.0；需要在线特征时才上 Feast 0.67 | PIT 宏、泄漏检查 |
| 训练 | 在沙箱里用 train/dev 训练候选模型（L2） | 固定搜索预算和种子；sklearn Pipeline 在折内拟合 | sklearn、LightGBM、Optuna、EconML/causalml、MLflow 3.17 | task pack |
| 评估/测试 | 碰不到 holdout | 封存 scorer（独立身份）、切片、校准、Qini/policy value | Inspect AI 0.3.x、MLflow GenAI eval | scorer、评测集、缺陷注入包 |
| 交付 | 提交答案证书、PR、发布申请 | 门禁（见 5.4） | GitHub/GitLab 受保护环境、attest/cosign、MLflow alias | gate 策略步、release manifest |
| 价值 | 起草实验方案和分析，不签收价值（L0） | 预注册、SRM、ITT、财务签核 | 既有实验平台；BigQuery AI.CAUSAL_EFFECT 仍是 Preview，不依赖〔dg L189〕 | 价值账本、W4 skill |
| 监控 | 告警分诊和根因分析（L1），结果转成 PR | 漂移、SLA、golden set 夜间 canary、回溯评分 | MLflow tracing、自托管 Langfuse、OpenLineage 1.54 | 回溯评分器、canary |

**采集与标注细则**（四个方案都偏薄，这里补齐，部分沿用草案 6.1/6.2）：

- **源契约**必须写明：主键、更新时间、迟到窗口、重叠回看策略、删除/tombstone 语义、写入模式。源级控制总数对账通过后，才发布为 READY。新出现的 schema 先登记为 candidate revision，并阻断不兼容的下游（dsharness 的 D8 就是这种情况）。
- **埋点**：用事件规范 YAML 描述（事件名、属性类型、必填项、触发条件）。每天把事件量与后端订单、支付记录对账。agent 只能以 PR 形式提出埋点规范。
- **外部数据**：优先买有许可的数据。自动化采集受网络数据条例约束，附带采集到的个人信息要删除或匿名化〔dg L56〕。来源和许可证都要登记。
- **非结构化数据**（合同、工单、文档）：LLM 抽取按"有评测的管道"来管。人工标注 200 条样本，测字段级的 P/R。未达阈值的字段只能作为 T3 使用。
- **标签**分四级：confirmed、expert_adjudicated、weak、model_generated。人工队列分两条：
  - 随机或分层审计队列：用来估计总体错误率，要记录抽样概率；
  - 错误发现队列：只用来找错，不能用来估计总体错误率。

  金标要盲标，冲突要裁决，kappa ≥0.8。
- **合成数据**（"AI+"政策鼓励使用〔dg L407〕）：只用于测试和基准，必须打上 synthetic 标记，永远不进入评测真值或价值核算。

### 3.3 自建清单与不建清单

| 自建组件 | 内容 | 依托 | 代码规模【假设】 | 服务的 KPI |
|---|---|---|---|---|
| ctx | OSI/MetricFlow YAML、verified queries（运行集 R 与评测集 E 物理分仓）、表卡片、glossary、skills、AGENTS.md、路由策略；配 lint，同步到平台语义对象，并做 R/E 哈希不相交检查 | MetricFlow、dbt-mcp、sqlglot 30〔dg L339–L340〕 | 2–3k | 正确率、拒答率 |
| dsh-mcp | 受治理的数据入口（见 4.2） | mcp 2.3 / FastMCP 4.1〔dg L355〕 | 2–4k | 权限合规、静默错误 |
| dsh-checks/faults | 对账、不变量、PIT、基数、data-diff、价格有效期、实验质量检查；缺陷注入算子 | Pandera 0.34、GX Core 1.24、dbt tests | 2–4k | 捕获率、误拦率 |
| dsh-eval | golden set、结果集等价判分（含判分器校验）、pass^k、harness 适配、审查金丝雀 | Inspect AI〔dg L356〕、MLflow 3.17 | 1.5–3k | 回归、CPVC |
| dsh-gate | CI 策略步：三态判定，输出 in-toto Statement | actions/attest、cosign | <1.5k | 逃逸缺陷数 |
| dsh-value | 价值、成本、事故账本，约 20–30 个仓内 dbt 模型 | 仓库、MLflow trace 中的成本 | — | NV、CPVC |

**明确不建**：对话 UI、NL2SQL 引擎、编排器、notebook、Catalog、模型注册表、沙箱运行时、LLM 网关（只写路由策略配置）、向量库/记忆库、证据数据库、多 agent 框架〔dg L210〕。状态只放在 git、仓内表和 MLflow 里。

### 3.4 部署剖面

- **SMB / 本地优先**
  - 组件：uv + Py3.12；dlt 1.31 → DuckDB 1.5.6（暂不上 2.0）+ DuckLake 1.0 → dbt-core 1.12/1.13 → Pandera；cron/CI，需要时再用 Prefect 3.8 或 Dagster 1.13；单机 MLflow 3.17；MetricFlow 或 Cube 1.8；dsh-mcp 走 stdio；沙箱用 sandbox-runtime（必须配置 denyRead `~/.ssh`〔dg L273〕）或 Docker `network=none`。Linux 上 Unix-local 模式没有 OS 级隔离〔dg L242〕。
  - Cube 必须关掉 dev mode，否则会绕过认证〔dg L349〕。
  - 不要用 `pip install dbt`：它安装的是专有的 Fusion 二进制〔dg L370〕。
  - "本地优先"指数据不出公司环境，不是把数据拷到笔记本上。
- **企业（已有数仓）**
  - 仓库是 system of record，查询下推，以用户身份执行（OBO + RLS）。
  - 跨引擎用 Iceberg 1.12 + REST catalog（UC OSS 0.7）。
  - 保留 Airflow 3.3、DolphinScheduler 或 DataWorks 作为调度器。
  - OpenLineage 1.54 事件写入现有 catalog，不用已停更的 Marquez。
  - MLflow 开启 RBAC。LangGraph 1.2 只用于需要持久化人工审批的长流程〔dg L362〕。
- **单平台例外**：如果原生 agent 加原生 MCP 已经覆盖问数和编码（Genie MCP GA〔dg L184〕、BigQuery MCP GA〔dg L188〕、dbt-mcp），就不建 dsh-mcp 的数据访问部分，只建 ctx、eval、gate 和账本，并执行平台替代测试。
- **多平台**（例如境内 MaxCompute/DataWorks + 海外 Snowflake）：建薄的 dsh-mcp（8–10 个工具），只负责验证、证据、跨平台复算和答案证书。平台内的问数和编码交给各自的原生 agent，因为厂商 agent 只看得到自家的资产〔dg L204〕。
- **实时/在线 ML**：v1 只做批量。实时模型（风控、定价、推荐）进入时，新增 G-online 门禁，要求：
  - 在线/离线特征一致性测试；
  - 特征新鲜度 SLA；
  - 影子流量 ≥2 周；
  - 金丝雀放量，违反 SLO 时自动回滚。

  在线服务用平台自带的能力【服务选型未经 digest 核实】，不自建。

### 3.5 供应链

- **许可证门**：SBOM + SPDX，标记 ELv2（Soda v4）、BSL（lakeFS ≥1.87）、PLA（dbt Fusion 二进制）、AGPL 和 Proprietary。内部使用可以；如果转售或托管给客户，会被阻断〔dg L360〕。
- **避免使用**：Vanna、Daytona OSS（已归档）；smolagents、AutoGen（已停滞）；Marquez；核心路径上的 CrewAI（2026 年发了 289 个版本）。Polars 2.0、DuckDB 2.0、dbt v2 在自有数据上跑过回归之前不进生产〔dg L366〕。
- **agent 扩展的供应链**（新增）：Skills 含可执行脚本，MCP server 和 hook 以 agent 的权限运行。因此：
  - 设内部白名单注册表（MLflow 3.15 MCP Registry 或 agentgateway 策略；官方 Registry 仍是 preview〔dg L287〕）；
  - 按 digest 钉住，经代码审查后签名；
  - 每季度做一次工具投毒和描述注入的红队测试；
  - DataWorks 市场的 skill 也必须走白名单。

---

## 4. D Agent 设计

### 4.1 拓扑

| 工作流 | 形态 | 写者 | 骨架 |
|---|---|---|---|
| W1 断修 | 构建或测试失败触发 → 只读分诊子 agent（读日志、契约 diff、血缘，返回 1–2k token 摘要）→ 单写者在分支上写补丁 → CI → 干净上下文的 reviewer（只输出可执行探针）→ CODEOWNER 合并 | 1 | CI/DAG |
| W2 问数 | 单 agent，≤10 个工具；可以澄清、可以拒答；难写的 SQL 可生成 N 个候选，按执行结果选 | 只写证书 | — |
| W3 分诊 | 只读 RCA 子 agent；每个假设都必须是一条带预期结果的查询；结果转给 W1 | 0 | — |
| 富化 | 夜间脚本扇出只读 agent，输出用 JSON schema 校验，token 预算显式写明（参照：14 个 agent 约 482k token〔dg L258〕），产物交 steward 审 PR | 脚本 | 调度器 |
| W4 读数 | Skill + 确定性统计库；agent 只负责编排和写叙述 | 0 | — |
| W6 工厂 | 在现有编排器中跑确定性 DAG；agent 只写特征和候选代码；builder 看不到 holdout；scorer 用独立身份、确定性运行 | 1 | DAG |

**禁用的形态**：
- persona 团队、并行多写者；
- Claude Code agent teams（仍是实验特性）〔dg L236〕；
- Supervisor 型（Databricks Supervisor API 已弃用）〔dg L186〕。

A2A 只用在组织或厂商的边界上〔dg L321〕。

### 4.2 dsh-mcp 工具

| 工具 | 读/写 | 要点 |
|---|---|---|
| `search_context` | R | 返回表、指标、R 集 verified query、skill，带 tier 和 owner；按用户近 90 天用过的表加权（Meta：88% 的查询只用这类表〔dg L104〕） |
| `describe` | R | 表卡片：粒度、键、新鲜度、caveats、PII 级别、上游 |
| `query_metric` | R | 经语义层查询；返回结果句柄、编译后的 SQL、`metric_definition_digest`、`snapshot_id` 和 trust 块 |
| `run_sql` | R | 用于探索：sqlglot 只读白名单，禁止 file/table 函数；返回给模型 ≤50 行；dry-run 估算扫描成本并扣配额；含 PI 的表只出聚合；结果标 T3 |
| `profile` | R | 只返回统计量 |
| `run_checks` | R* | 只能跑已注册的检查；由服务端身份执行，agent 写不出 PASS |
| `diff` | R | 分区级聚合差异 |
| `evidence_get` | R | 证据状态，以及解除 HOLD 需要的证据 |
| `submit_answer` | W | 校验证书 schema，按证据自动定级或降级 tier |
| `propose_change` | W | 写仓库的唯一路径：补丁 → 分支 → CI → 证据 |
| `request_eval` | W | 申请封存评估，受预算限制 |
| `log_decision` | W | 把决策记录写入账本 |

**工具数量**：全集 ≤13 个，每个角色 ≤10 个。依据：OpenAI 用 13 个互不重叠的工具〔dg L97〕；工具超过 30–50 个时选择准确率下降〔dg L255〕。统计和建模做成 skill 脚本，在沙箱里以 code mode 运行（programmatic tool calling 省 37% token；code execution with MCP 示例中省 98.7%〔dg L251–L252〕），不做成工具。

**协议**：
- MCP 2026-07-28：无状态；`tools/list` 顺序固定；写工具加 destructive 标注；句柄由服务端生成；长任务走 Tasks 扩展；`_meta` 里带 OTel traceparent〔dg L221〕。
- 认证：OAuth 2.1 + RFC 9728/8707，令牌绑定 audience，禁止透传〔dg L222〕。EMA 通过 IdP 接入（首个支持的是 Okta〔dg L223〕）。钉钉、飞书是否支持 EMA【待核实】，不支持时在网关做 token exchange。
- 不使用 Sampling、Roots、Logging、DCR。

**防差分推断**（新增）：
- 含 PI 的表只能通过固定维度的认证指标访问；
- 拒绝对这类表做自由过滤；
- 按 principal 审计查询集的重叠；
- 小格抑制 k≥10【假设】。

### 4.3 Skills、AGENTS.md 与函数库

**Skills**：

| Skill | 要点 |
|---|---|
| `pipeline-break-fix` | 不允许在同一个 PR 里改测试、契约或门禁 |
| `schema-evolution` | 处理 currency 这类新增语义，不只是改列名 |
| `backfill-runbook` | 只输出计划和 dry-run diff |
| `metric-question` | 先澄清 → 调 `query_metric` → 附证书 |
| `reconcile-before-claim` | 先对账，再下结论 |
| `pit-features`、`leakage-audit` | PIT 特征构建与泄漏审计 |
| `experiment-design`、`ab-readout` | 功效不足就输出 INCONCLUSIVE |
| `uplift-targeting` | 用 policy value 和 Qini 评估 |
| `model-card-and-pipia` | 生成模型卡和 PIPIA 草稿 |

- 每个 skill 由一位领域专家负责，有版本号，附 ≥10 个评测任务，改动后要跑回归。
- 格式可在 Claude Code、Codex、Gemini CLI、Genie Code、Cortex Code、TRAE、Deep Code 之间移植〔dg L226〕。DataWorks 的上限是单个 SKILL.md ≤200KB、zip ≤50MB〔dg L15〕。

**AGENTS.md** 写仓库约定，三大 CLI 都会读〔dg L227〕。CLAUDE.md 控制在 200 行以内〔dg L230〕。

**领域函数库 `lib/`**：离线由强模型对照 dev 标签蒸馏出带单测的函数，运行时由便宜模型编排。参考的是 KGMON：运行时用 Haiku，DABstep Hard 89.95，同期 Claude Code + Opus 基线是 66.93〔dg L142〕。注意这是排行榜结果，不是受控消融。蒸馏时要留出数据，防止过拟合〔dg L159〕。

### 4.4 上下文层

**可信度排序**：
1. 语义定义
2. 认证表
3. verified queries：看板查询和 golden 排在临时查询之前，不要把全部查询日志都建索引〔dg L97, L134〕
4. 夜间富化生成的表卡片（OpenAI Codex enrichment〔dg L95〕）
5. glossary 和业务规则，配合澄清回合〔dg L164〕
6. 事故与 caveat 库
7. 个性化

**规则**：

- **R/E 分离**：一条 verified query 要么用于运行时，要么用作评测真值，不能两用〔dg L18〕。CI 做哈希检查。
- **语义以可按需读取的文件提供**。把同样的知识贴进一个 24k 字符的 prompt，效果明显更差〔dg L112〕。
- **用户纠错**以 PR 形式提交到 ctx 仓，steward 在 2 个工作日内审核。不写入不透明的全局记忆（NFRA 第 25 条点名"记忆污染"〔dg L60〕）。记忆按租户隔离。
- **按血缘失效**（新增）：OpenLineage 事件显示上游的 `pipeline_digest` 或 schema 变了，依赖它的 verified query、表卡片和 skill 就标为"待复验"，并移出运行集 R，复跑通过后再放回。这是上下文腐化的主要来源。
- **员工查询日志的隐私**（新增）：个性化和 golden 挖掘都会处理员工的个人数据。要做告知、限定用途、按 90 天滚动保留，挖掘 golden 时先聚合，并明确 PIPL/GDPR 下的处理依据【待法务】。

### 4.5 模型路由与数据驻留

| 数据级别 | 可以进 prompt 的内容 | 中国 | EU | 美国/其他 |
|---|---|---|---|---|
| C0：元数据、schema、代码、合成数据 | 全部 | 已批准的模型；重要数据目录的元数据只走境内 | 已批准的模型 | 已批准的模型 |
| C1：内部聚合（小格 k<10 抑制【假设】） | 聚合结果 | 境内优先 | EU 区域端点 | 签了 no-training 条款的端点 |
| C2：行级个人信息 | 不进 prompt，只在沙箱里由代码处理 | 境内自托管，或已备案的境内 API | EU 区域 + ZDR + DPIA | ZDR |
| C3：敏感个人信息、重要数据、NFRA 高风险数据 | 只看 schema，访问需人工批准 | 只能用自托管的开放权重模型 | 自托管，或 ZDR + DPIA | 自托管或专属部署 |

- Claude Managed Agents 和 programmatic tool calling 不在 ZDR 范围内，C2 及以上必须用自有 loop〔dg L241〕。
- NFRA 禁止用个人信息训练或微调〔dg L60, L72〕。
- PI 识别和假名化在 MCP 层或网关层完成。

**模型分档**：

| 档位 | 模型 | 用途 |
|---|---|---|
| 便宜档 | Haiku 5.5、gpt-6-luna、deepseek-flash、GLM-5.3-Flash | 抽取、分类、SQL 草稿；目标承担 ≥60% 的调用【假设】 |
| 默认档 | Sonnet 5.5、gpt-6.1-sol、Qwen3.7-Max、DeepSeek V4-Pro、GLM-5.3 | 常规规划和编码 |
| 前沿档 | — | 只用于离线蒸馏和疑难修复 |

路由只为合规和成本服务，不为"更聪明"服务。OpenAI 的 data agent 就是单模型、没有路由器〔dg L97〕。

**供应商**：
- 至少 3 家，其中 1 家境内、1 家可自托管，并设涨价告警〔dg L413〕。
- 钉住的模型宣布退役后，30 天内经回归迁移完毕【假设】。
- 每个档位保留 2 个已批准的模型。

### 4.6 Harness 可替换

- **契约放在资产层**：MCP 工具、SKILL.md、AGENTS.md、eval 套件、答案证书的 JSON schema、`task.json`。自运行 harness 的适配器只实现一个接口：`run(task, budget) → (transcript, artifacts, cost)`。
- **自运行适配器**：
  - Qwen Code；
  - OpenHands 1.28；
  - `claude -p --bare --json-schema --max-budget-usd`。CI 里必须加 `--bare`，否则仓库里的 hooks 和 `.mcp.json` 会在没有信任确认的情况下直接运行〔dg L228〕；
  - `codex exec`：OS 级沙箱，默认断网〔dg L244–L246〕；
  - Agents SDK 0.24、ADK 2.12、PydanticAI 2。
- **封闭的平台 agent**（DataWorks Data Agent、CoWork/CoCo、Genie Code）：我们的 hooks、`--bare`、denyRead、预算上限在它们内部都无法强制执行。对策分四层：
  1. 在同一套 golden set 上做黑盒评测；
  2. 数据侧控制：只读角色、RLS、仓库配额，dsh-mcp 是唯一写路径；
  3. 输出侧门禁：答案证书、PR CI；
  4. 平台自身的审批档位设为保守。
- **节奏**：
  - 模型、harness、skill、上下文、语义任何一个 pin 变更，都自动跑回归。
  - 跨 harness 的完整对比（≥2 个执行器，其中 1 个境内）每季度一次；遇到大版本模型发布或 ≥30% 的涨价时立即加跑。
  - 在 pass^3 相同（噪声范围内）时，CPVC 低 ≥20% 就切换默认执行器【假设】。
  - 每次大版本模型升级，做一次"拆脚手架"消融〔dg L259, L322〕。
- **版本精确钉住**：Claude Code 2.1.303 几乎每天发版〔dg L228〕；openai-agents 0.24.0〔dg L242〕；ADK 1.x 到 2.x 存储格式不兼容〔dg L249〕；MLflow 3.17 需要协调式的 DB 升级〔dg L347〕。
- **长任务**：工作流脚本分段设检查点（git 分支加 run log）。METR 的时长都是在 50% 可靠度下测的〔dg L399〕。

### 4.7 自治分级

**级别定义**

| 级 | 含义 | DataWorks 五档映射 |
|---|---|---|
| L0 | 只出计划或建议 | plan-only |
| L1 | 只读执行并作答，带标签 | 中间档（名称以控制台为准） |
| L2 | 写沙箱、分支或 dev；开 PR 或发布申请；由人合并 | 中间档 |
| L3 | 门禁全过后，自动进入非生产或自动发布到共享制品，事后抽审 | 中间档 |
| L4 | 在生产中自动执行有界、可逆的动作 | yolo 档永不用于生产 |

**各工作流的起点、上限和升级条件**

| 工作流 | 起始 | 12 月上限 | 升级条件【假设】 |
|---|---|---|---|
| 认证答案 → 提问者 | L1 | L1 | — |
| 认证答案 → 共享看板、周报 | L2（人审后发布） | L3 | 该任务类随机抽审 n≥150 且 0 错（上界约 2.0%），或 n≥300 且 ≤1 错（上界约 1.6%）；且 0 逃逸 |
| 董事会、对外、监管数字 | 人工 | 人工 | 永不升级：T0 + 具名人签字 |
| 探索 SQL | L1 + T3 标签 | L1 | — |
| DE 断修（不含契约变更） | L2 | L3（非生产自动合并） | 连续 50 个 PR 无大改合并、0 逃逸、reviewer 推翻率 <5%、回归 pass^3 ≥90%；低风险类（文档、测试、新增列）30 个即可 |
| 失败任务重跑 | L2 | L4（配额内、幂等、有 runbook） | 有幂等性证明，且连续 20 次无误 |
| 回填、重算历史 | L2 | L2 | 永不升级：计划 PR + dry-run diff + 双人审批 |
| 契约或指标口径变更 | L2 | L2 | 永不升级：owner 审批 |
| 实验读数（内部） | L2 | L3 | 20 次读数与统计师复核一致 |
| 模型晋级、触达客户的动作 | 人工 | 人工 | 永不升级：走 G5/G6 |
| 高风险决策 | L0 | L0 | 受监管约束 |

**降级**：出现任何一次逃逸，自动降一级；出现 Sev-2 逃逸，该任务类降到 L1。

**依据**：
- 逐动作审批的部门，变革性成功率 16%；充分使用 agent 的部门 31%〔dg L392〕。这是相关性数据，且来自单一二手报道。
- IMDA 的"关键检查点"做法〔dg L67〕；六类审批点〔dg L75〕。

### 4.8 确定性护栏与 harness 自身的 SLO

- **PreToolUse hooks**：
  - 拒绝对 prod 的 DDL/DML；
  - 拒绝读取 `custody/`、holdout 和合成世界生成器的源码；
  - 拒绝把修复代码与测试、契约、门禁的改动放进同一个 PR；
  - 超出预算记为 FAILED，不算完成。
- **沙箱**：断网或只开白名单；输入只读挂载；短期凭据留在 harness 一侧，不进沙箱〔dg L242, L308〕。
- **数据内容一律视为不可信**：单元格、列名、工单文本里的指令不能改变权限〔dg L80〕。用 D13 红队用例回归。
- **SLO**【假设】：
  - dsh-mcp 工作时段可用性 99.5%，`query_metric` 的 p95 <10 秒；
  - 验证器或门禁宕机时：写入、晋级、对外数字一律 fail-closed（HOLD）；只读探索答案 fail-open，但标为"未验证"；
  - 平台组轮值 on-call。

---

## 5. E 可信与验证

### 5.1 没有真值时用什么当 oracle

| oracle | 能证明什么 | 不能证明什么 |
|---|---|---|
| O1 独立记录对账：财务关账、支付渠道结算、源系统控制总数 | 加工过程正确 | 源系统本身对现实的记录是否正确；未关账期间不可用 |
| O2 不变量和变形关系：守恒、m:1 不增行、按事件身份去重、PIT、价格表有效期、改写不变性、过滤单调性 | 某一类错误不存在 | 口径本身是否正确 |
| O3 双路径复算：语义层 vs agent SQL，或用 sqlglot 转译到第二个引擎 | 实现层面的错误 | 两条路径共用了同一个错误定义 |
| O4 golden set / verified query（E 集） | 已知问题上的能力 | 分布外的问题 |
| O5 延迟真值回溯评分：答案连同快照一起存档，关账或标签成熟后自动重评 | 真实世界的准确率，不需要人工标注 | 没有延迟真值的问题 |
| O6 已知抽样概率的随机抽检 | 无偏的错误率估计 | —（错误发现队列不能用来估总体错误率） |
| O7 随机实验 | 因果效果 | 实验之外的人群 |
| O8 种植缺陷和金丝雀：在 staging 副本或合成世界注入已知缺陷，相当于管道的变异测试 | 检查本身的召回率 | 真实环境的准确率 |

**规则**：

- **对账独立性**（新增）：独立记录不得出现在被核对指标的上游血缘里。G-PR 用依赖图做断言；bench 里设"用关账表校准"的陷阱任务。
- **时间覆盖缺口**：未关账的期间最高只能到 T1，并明确标注"暂定，待关账后回溯评分"。
- **非财务指标**：先找源系统控制总数（CRM 后台计数、支付结算文件、应用商店报表）当 O1；找不到就封顶 T1/T2，靠随机抽检兜底。
- **因果结论**：只认随机化。准实验结论标为 ASSUMPTION-DEPENDENT，并配 DoWhy 反驳和敏感性分析。
- **金标本身要审计**：公开基准的答案键在部分子集上错误率达 30–60%〔dg L139, L155〕。用 LLM 审计员加双人审，kappa ≥0.8（ELT-Bench-Verified 用的是 Fleiss kappa 0.85〔dg L155〕）。

### 5.2 统一证据阶梯与信任标签

这一节裁决了四套阶梯不兼容的问题（见 ADR-04）。

**内部证据阶梯（产物级）**

| 级 | 内容 |
|---|---|
| V0 | 能执行（不算证据） |
| V1 | 契约、类型、测试；SQL 静态检查；重执行答案结果一致 |
| V2 | 不变量、PIT、变形关系 |
| V3-D | 独立路径复算一致（O3） |
| V3-R | 与独立记录精确对账（O1），严格强于 V3-D |
| V4 | 封存 holdout、成熟标签或回溯评分，由独立身份的 scorer 执行 |
| V5 | 预注册的随机实验（ITT、SRM、功效） |

agent 配置评测（golden set pass^3 回归）是 harness 的门禁，不是产物阶梯中的一级。

**用户信任标签（阶梯的投影）**

| 标签 | 条件 | 允许用途 |
|---|---|---|
| T0 已对账 | V3-R 通过：已关账，与独立记录精确一致，或差异都已解释 | 对外、监管、董事会，另需具名人签字 |
| T1 受治理-暂定 | 走认证指标路径 + READY 构建 + V2 + 配置评测通过；独立记录尚未到 | 内部 KPI 和看板，标"暂定"，到期自动回溯评分 |
| T2 已交叉核验 | 非认证问题，V3-D 双路一致 + V2 | 内部决策支持 |
| T3 探索 | 仅 V1，并列出假设 | 只能用于探索，禁止进入 KPI 或汇报 |

模型和因果声明不另设一套标签，而是用 V4/V5 加结论状态表达：VERIFIED、INCONCLUSIVE、NOT_VERIFIED_CAUSAL。

**各产物的最低要求**

| 产物 | 最低要求 |
|---|---|
| 对外、监管、董事会数字 | T0 + 具名人签字 |
| 内部 KPI、看板 | T1，带回溯评分 |
| 认证答案给提问者 | T1 |
| 探索答案 | T3 标签 |
| 管道 PR | V1 + V2 + data-diff；能对账的部分要到 V3-R |
| 模型进影子 | V4 |
| 扩量、价值声明 | V5 |

### 5.3 评测

- **来源**：真实日志、工单、IM 登记。起步用 20–50 个真实失败〔dg L262〕；90 天达到 150–200 题，其中 20% 是越界或歧义题；6 个月达到 300–500 题。每题 2–4 种改写，允许多个可接受答案（LinkedIn 约 60% 的题有多解〔dg L101〕）。
- **冷启动**：没有日志的 SMB，先从语义模型生成 100 道草稿题，由 owner 认证，再做审计。在这之前，只能宣称"在种子集上"的准确率。
- **判分**：
  - 比较执行结果集是否等价（允许容差和多解），不比较 SQL 字符串；
  - 分组件评：意图、选表、能否执行、结果是否正确（Uber 的 decoupled 模式〔dg L99〕）；
  - PR 类任务 = CI 全绿 + 隐藏测试通过 + 与真值做 data-diff。
- **判分器本身要校验**（新增）：执行匹配有假阳和假阴〔dg L151, L175〕。每季度人工复核 100 条判分结论，判分器错误率 ≤2% 才能用于门禁【假设】。
- **报告指标**：pass@1、pass^3（单次 75% 时，pass^3 约 42%〔dg L266〕）、静默错误率（答错，既没拒答也没降级标签）、拒答的精确率和召回率、CPVC、p95 延迟、人工介入次数。按任务类别和风险等级分层报告。
- **样本量纪律**【实算】：
  - 0 错时 95% 上界约为 3/n：n=150 时 2.0%，n=300 时 1.0%；n=200 时出现 ≤2 个错，上界是 3.1%。
  - 200 题在 85% 附近的 CI 约 ±4.9pp；要 ±3pp 需要约 550 题。
  - 回归用配对 McNemar 检验，不用固定阈值；接受约 5% 的运行间噪声〔dg L121〕。
- **版本化**（新增）：每道题绑定 `metric_definition_digest`。口径一变，相关题目标为 stale，由 owner 重建基线。回归只在同一 digest 集内比较。
- **防泄漏**：R 集和 E 集物理分仓，CI 做哈希检查；评测题永远不进运行时上下文；judge 模型锁定版本〔dg L113〕。
- **厂商自报数字一律不采信**，必须在自有题集上复测：Genie Code 77.1%、Cortex Sense 83%、Quick BI 98%〔dg L195, L110, L126〕。

### 5.4 门禁

门禁名沿用参考实现 `policies/gates.yaml` 的 stage 名。

| stage | 必需证据 |
|---|---|
| `answer_publish` | 走认证路径或达到 V3-D；READY 构建；带定义 digest；证书 schema 合法。否则标 T3 或拒答 |
| `pipeline_merge` | 在克隆环境中构建并跑测试；契约检查；精确对账（含已解释差异）；关键指标 data-diff（容差由 metric owner 定）；测试削弱检测（删除或放宽测试需测试 owner 批准）；SQL 策略 lint（禁止 DROP/DELETE 和写 prod schema）；对账独立性血缘断言；许可证检查；eval 回归 |
| release | 受保护环境；必需审批人，禁止自审〔dg L11〕；签名证明 |
| `feature_accept` / `model_shadow` | time-travel 检查 + 静态时间检查；封存 scorer；切片；校准 |
| `model_experiment`（G5） | 预注册；对预注册的 MDE 功效 ≥0.8，否则标为 pilot；A/A；business owner 批准 |
| `model_scale`（G6） | 扣除成本后 ITT 的 LCB95 >0 且 ≥ 业务 MDE；SRM 通过；护栏指标未破；business owner 和 risk 批准；保留全局 holdout |

**判定语义**：
- 三态：ALLOW / HOLD / REJECT，退出码分别为 0 / 10 / 20。
- 未授权的生产者只会被忽略，不会导致 REJECT。
- 重试得到的 PASS 可以 supersede 暂态的 ERROR。
- 缺失、过期、ERROR、INCONCLUSIVE 一律判 HOLD，并说明需要什么证据才能解除。
- 只有经过认证的 FAIL 才判 REJECT。
- 未知 stage 或格式错误的 policy 判 REJECT。

**HOLD 率**健康区间 5–15%。超过 20% 时触发绕过告警（EY 调查中 47% 承认绕过过治理〔dg L69〕）。误拦率 ≤5% 列为 KPI。

**全局 holdout**（新增）：G6 扩量之后，保留 5–10% 的合格人群作为长期对照，至少 2 个季度【假设】，用来测效果衰减和反馈回路（用被干预过的数据重训会产生这种回路）。如果存在溢出（例如同一家庭、同一区域），改用 cluster 随机化。新奇效应至少要看 2 个读数周期。

### 5.5 人工复核

- **100% 人审的范围**：对外数字、生产写入、模型晋级、新增 verified query、契约或口径变更、回填、高风险场景。
- **抽样的范围**：认证答案随机抽 5%，低置信度和新领域的答案全审。不做逐条全审：每条都人工检查会重新造成瓶颈（Ramp 的教训〔dg L107, L135〕）。
- **复核人看的是证据包**：证书、diff、对账结果、tier，不看 transcript。
- **审查金丝雀**：用 dsharness 的生成器往复核队列里注入 2–5% 的已知缺陷。审查者检出率低于 80% 就暂停审批权并重新培训【假设阈值】。同时统计推翻率和审批耗时，不到 10 秒的审批视为橡皮图章〔dg L67, L75〕。
- **每个产物有具名负责人**。小团队靠机器身份分离职责；实际只有一个人时，不宣称做了双人复核。

### 5.6 证据

- **in-toto Statement**：
  - subject：产物的 digest；
  - predicate：checks、policy_version、eval_suite_version、source/pipeline/semantic_model/metric_definition 四类 digest、snapshot_id、harness/skill/model 版本、trace_id。
- **签名与存放**：由 CI OIDC 签名，GitHub 上用 actions/attest，国内用 cosign + KMS。证明与产物放在一起，索引写入账本。
- **答案召回**：定义 digest 一变，引用旧 digest 的答案标为 stale 并通知接收人，看板上的认证答案自动重跑。
- **保留期限**：
  - EU 高风险部署者的日志 ≥6 个月（Art.26，Annex III 自 2027-12 起适用）〔dg L50〕；
  - PIPIA 记录 3 年〔dg L57〕；
  - Colorado 3 年〔dg L62〕；
  - NFRA 要求保留原始数据、推理路径和阈值触发记录〔dg L60〕。

### 5.7 多 agent 辩论与评审团

- **不用辩论**：辩论是鞅过程，收益主要来自投票〔dg L153〕。
- **不用不跑代码的 LLM judge 判正确性**：最好约 82%，交换顺序会波动 14%〔dg L151〕。
- **默认允许的三种用法**：
  1. 高价值的单题：生成 N 个 SQL 候选，按执行结果选（BIRD 的主要增益就来自候选加选择〔dg L140〕）；
  2. 干净上下文的 reviewer 审 PR（Devin Review 每个 PR 约多发现 2 个 bug〔dg L257〕）；skeptic 只能产出可执行探针，只有探针在 CI 中确实让候选失败才计入；
  3. 成对 LLM judge 只评叙述质量：随机顺序，至少采样 3 次，与人工标注一致率 ≥0.8。
- **预注册的留用规则**：评审团、投票或 reviewer 若要在某工作流中默认开启（D/E 配置），必须同时满足：
  - 在隐藏 seed、同等预算下，静默错误率相对下降 ≥30%，或正确率提高 ≥5pp（在功效足够的题集上做配对 McNemar，p<0.05）；
  - CPVC ≤1.5 倍；
  - 在第一份真实数据切片上复现。

  DataPRM 类与环境交互的验证器（best-of-N +7.21% / +11.28%〔dg L152〕）放到 v2，按同一规则评估。

### 5.8 统计检查清单

这份清单内置在 W4 skill 和 G5/G6 门禁中。

- **预注册**：随机化单位、人群、主指标、MDE、功效、周期、停止规则。
- **SRM**：χ² 检验 p<0.001 即判实验无效。
- **主分析**：ITT，按随机化单位用 cluster-robust 标准误。
- **切片**：用 Holm 或 BH 校正。
- **HTE**：要么预先指定，要么标为探索性并要求复现。
- **CUPED**：必须预先指定。
- **提前看结果**：用序贯检验方法。
- **观察性研究**：做 DoWhy 反驳，结论标 NOT_VERIFIED_CAUSAL。
- **功效不足**：输出 INCONCLUSIVE，并给出所需样本量或时长。

---

## 6. F 交付与组织

### 6.1 交付机制

| 交付物 | 载体 | 门禁 | 发布 | 回滚 |
|---|---|---|---|---|
| 问数答案 | IM 线程或 BI，附答案证书（见下） | `answer_publish` | 给提问者自动发；进共享看板按 4.7 | 口径变更时召回 |
| 管道、语义、上下文、skill、门禁本身 | PR | `pipeline_merge` | 合并后自动到 staging；prod 走受保护环境 | git revert，从上一个 digest 重建；表用 time travel 或 Iceberg/DuckLake 快照 |
| 回填 | 计划 PR：范围、成本、dry-run diff | 双人审批 | 分区分批执行 | 快照恢复 |
| 模型 | 候选 + 封存评估证据 | `model_shadow` → G5 → G6 | MLflow alias 只能由 release job 修改；影子 → 有限实验 → 扩量 | 回退整份 release manifest（模型、预处理、阈值、策略、镜像）；schema 不兼容时切到规则兜底（kill switch〔dg L60〕） |
| 决策 | 决策记录（问题、选项、证据 tier、owner、预期效果、复查日期）+ 实验 | G6 | business owner 签字 | 停止实验，回退策略 |
| harness 变更（模型、harness、skill、语义、prompt） | PR | 全量回归 eval | 同"管道" | 回退 pin |

**答案证书**的字段：
- 问题，以及术语解析（净额还是毛额、是否排除测试账号）；
- metric_id 及其 digest；SQL 及其 digest；
- build/快照、行数、检查与对账状态、tier、caveats；
- 模型和 harness 版本、成本。

外发的证书要加 AI 标识〔dg L47, L55〕。

### 6.2 CI、环境与证明

- **身份分四类**：
  - agent：只有 dev 写权限，可以开 PR；
  - CI/gate：跑检查、签名、部署；
  - 封存 scorer：独立身份；
  - prod 凭据：只在受保护环境中释放（GitHub 最多 6 名必需审批人，可禁止自审〔dg L11〕）。
- **环境**：
  - dev：agent 的沙箱，数据为脱敏、合成或零拷贝克隆；
  - CI/staging：Snowflake clone、DataWorks 开发项目或 DuckDB 文件副本，只构建 `state:modified+`；
  - prod：只有 CI 身份能写。
- **CI 顺序**：sqlglot 解析和 lint → 在克隆环境构建 → 契约和测试 → 对账 → data-diff → 血缘影响分析与对账独立性断言 → eval 回归 → 签名证明 → gate → 受保护环境。
- **PR 模板必填**：意图、关联事故、受影响的模型、data-diff、对账结果、增删的测试、eval 变化、成本、风险等级、回滚方案。
- 凡是改动 agent 配置文件（`.mcp.json`、hooks、AGENTS.md、skills）的 PR，都需要 owner 审批。

### 6.3 角色

| 角色 | 职责 | 投入【假设】 |
|---|---|---|
| 业务数据产品 owner | 价值定义；签署 baseline、KPI、kill 标准；实验预算；决策签字 | 每周 1–2 小时 |
| 指标 steward（业务方出资） | 维护语义、认证表、verified queries；纠错 PR 2 个工作日内处理 | 每域 0.25–0.5 FTE，是域准入的前提 |
| **指标口径委员会**（新增） | 仲裁口径冲突：部门之间口径不一致时，10 个工作日内裁决。允许具名共存（如 `net_revenue_finance` 与 `net_revenue_ops`），禁止同名两义 | 财务加各业务 owner，月度例会 |
| 分析工程师（CODEOWNER） | 审 agent 的 PR，维护测试 | 占本职的 10–15% |
| 平台/harness 工程 | dsh-mcp、门禁、沙箱、身份、路由配置、FinOps、SLO | 1–1.5 FTE |
| 评测 owner | golden set、判分器校验、回归、审查金丝雀、bench | 0.5 FTE |
| DS/统计 owner | 实验设计、因果结论签字、W6 | 0.5–1 FTE |
| 风险合规与安全 | 用例分级、路由矩阵、PIPIA/DPIA、出境和反向传输、红队 | 按需 |
| 财务 BP | 确认 κ 和现金事件；月度 NV | 0.1 FTE |

### 6.4 采纳与变更管理

- **嵌入现有渠道**：LinkedIn 把 SQL Bot 嵌入 DARWIN 后，使用量提升 5–10 倍〔dg L123〕。用"agent 优先 + 人工升级 SLA"取代原来的 #help-data 渠道。
- **按团队分批随机上线**（stepped-wedge）：分 3–4 批，批次顺序随机，用带时间固定效应的混合模型估计分流量和周期时间的因果效应，取代自报的"省了几小时"。
- **周会与记分卡**：
  - 每周 30 分钟的"纠错 → verified query"会；
  - 每月公布记分卡：自助解决率（与审计正确率配对）、pass^3、抽审上界、CPVC、HOLD 率、事故。
- **培训 2 小时**：怎么读标签和证书；什么情况下不该信。超出能力前沿时，正确率会从 84.5% 跌到 60–70.6%〔dg L396〕。
- **定位是增强，不是替代**：
  - Gartner：约 80% 的组织裁员后没有对应的 ROI〔dg L391〕。
  - Klarna 只有 2024 年的自述数据，算不上佐证；Gartner 预测 50% 的组织会放弃客服裁员计划〔dg L406〕。
  - 给初级分析师留出转向 steward、eval、复核岗位的路径。Canaries 研究显示，AI 暴露职业中 22–25 岁员工的就业比趋势低 19%，原因是少招人〔dg L68〕。
- **厂商驻场工程（FDE）合同**：写明 IP 归属、知识转移和退出方案。Gartner 预测到 2028 年 70% 由 FDE 构建的系统会被放弃〔dg L391〕。

---

## 7. G 路线图

### 0–90 天（截至约 2027-01-17）

| 周 | 交付 |
|---|---|
| 0–2 | 选域：收入/订阅或订单，必须有财务关账。用 IM 登记机器人或工单导出测基线（量、类型、耗时、积压、事故、MTTR）。完成用例风险分级登记和路由矩阵 v1。签署阈值。请法务确认供应商可得性和反向传输问题 |
| 2–6 | ctx v1：≤30 个认证指标、50–100 张认证表、100–150 条 R 集 verified query、150 题 E 集。dsh-mcp v0（单平台企业直接接原生 MCP）。6 个 skill、hooks、证书 schema、CI gate 与证明、账本 |
| 4–8 | W1：一个 dbt/DataWorks 项目接入 `pipeline_merge`；回放 20 个历史事故作为 DE 评测；在 staging 副本上跑缺陷注入包，测出测试充分度；W1 在 dev 以 L2 运行 |
| 6–12 | W2 向一个 persona 开放（10–50 名 champion）；每周记分卡；第一次跨 harness 对比（境内栈加一个可用的海外栈）；**在脱敏的本企业真实数据切片上跑同一套任务**，不等到第 6 个月 |

**退出条件**（第 2 周测完基线后校准）：

- W2：
  - held-out 认证范围 pass^3 ≥85%（n≥200，报告 CI）；
  - 命中 verified query 的题 ≥95%；
  - 越界拒答 ≥90%；
  - 抽审静默错误率上界 ≤3%（n≥200）；
  - CPVC ≤ 人工单位成本的 20%；
  - WAU ≥30%；
  - 自助解决率 +10pp。
- W1：
  - ≥20 个 PR，无大改合并率 ≥40%；
  - 对账零逃逸；
  - 覆盖到的事故类别 MTTR −30%。
- 通用：
  - 账本覆盖 100% 的 agent 任务；
  - 策略违规为 0；
  - 能在 ≥2 个 harness（其中 1 个境内）上运行。

### 3–6 个月

**范围**：
- 扩到 ≤3 个域，每个域都有具名 steward。
- W3 从 L1 升到 L2；上线 W4 skill；DE 的低风险类升到 L3（非生产）。
- 夜间离峰富化；审查金丝雀。
- CN、EU、US 三套策略包由网关强制执行。
- 做平台替代测试。
- W6 第一个实验完成预注册，功效 ≥0.8。
- 合规包 v1：审计导出、模型卡、PIPIA 草稿。

**退出条件**：
- 自助解决率比基线 +15–20pp；机械工单占比 −10pp（参照 Grab 从 44% 降到 30%〔dg L106〕）；
- 经历 ≥2 次模型或 harness 升级，pass^3 无退化（McNemar 检验）；
- 金丝雀检出率 ≥80%；HOLD 率 5–15%，误拦率 ≤5%；
- 保守口径 NV 连续 2 个月为正；
- 拿到 stepped-wedge 的估计结果。

### 6–12 个月

**范围**：
- 扩到 ≤5 个域。
- W6 走完 G5 → G6，uplift 优先；决策日志挂到 KPI；建立全局 holdout。
- DataWorks 与 Snowflake 之间经 OSI 做跨平台复算。
- 合规文档自动生成；做一次 harness 切换演练。
- 时间线对齐 ADMT（2027-01-01）和 Annex III（2027-12-02）〔dg L82〕。

**退出条件**：
- ≥1 个决策拿到 V5 证据且 LCB95 >0；或者以如实的 null 结果叫停了相应支出。
- 年化保守口径 NV >0，输入数据经过审计。
- ≥1 个工作流不计"省下的工时"也是净正值。
- agent 引发的 Sev-1 为 0。
- 2 周内能完成模型或 harness 切换，回归降幅 ≤3pp。
- 日志保留满足 6 个月和 3 年的要求。

---

## 8. H 参考实现（dsharness）下一步

### 8.1 现状

快照时间约为 2026-10-19 13:00。仓库正在被并行修改，分配任务前要重新核对。

**已有**：

| 模块 | 内容 |
|---|---|
| world | 9 类植入缺陷 D1–D9，以及 HTE 实验的 custody 真值 |
| lake | Pandera v1/v2 契约、dbt 风格 runner（上游失败时下游 SKIPPED）、原子发布、财务对账。2026-02-28、03-31、05-31、06-30 四个快照 READY；08-31 和 09-30 因 D8 失败（`amount_cents` 改成了 `amount_minor` + `currency`），判为 FAILED，符合预期 |
| 规格 | `docs/IMPLEMENTATION_SPEC.md`，为绑定规格，分 W1–W6 工作包，规定不得新增依赖 |
| W1 | `context/` 下三份 YAML 和 semantic/catalog 模块；`verify/sql_static.py`、`verify/reconcile.py` |
| W2 | `evidence/records.py`、`evidence/gate.py`、`policies/gates.yaml`、`.github/workflows/dsh-gate.example.yml` |
| W4 | `decision/{experiment,oracle,policies}.py`、`verify/stats.py`、`monitor/drift.py`、`economics/roi.py` |
| W3（部分） | `ml/features.py`、`ml/train.py` |
| 测试 | 5 个测试文件（另有 conftest）、99 个测试函数；并行编辑期间有 1 个失败（`test_context.py::test_compare_frames_edge_cases`） |

**缺失**：
- W3：`ml/custody.py`、`verify/leakage.py`；
- W5：`tools/*`、`mcp_server.py`、skills、`AGENTS.md`、`.mcp.json`；
- W6：`bench/*`、`cli.py`（pyproject 已声明 `dsh = dsharness.cli:main`）、`demo.py`。

**已核实的问题**：

1. `finance_closed_revenue` 只是 `truth_revenue` 按已关账月份做的过滤（`src/dsharness/world/generator.py` L391–393），所以对账实际上是一个变相的 oracle。
2. `lake/pipeline/models/invoices.sql` 里的 D3 修正规则没有限定范围：任何"<1000 且 ×100 落在价格表里"的收费都会被修正。这意味着别的区域将来出现单位事故时会被静默抹平，同时基准本身也内置了答案。
3. `schema.yml` 里的 `charge_amount_on_price_book` 没有看 `valid_from`/`valid_to`（plus 的 5900 有效期到 2026-07-31，6900 从 2026-08-01 起）。8 月之后仍收 5900、8 月之前就收 6900，两种情况都能通过。
4. `decision/oracle.py` 的 `ExperimentOracle.outcomes()` 没有调用次数限制。先提交一份分配，再提交它的补集，就能拿到全部两个潜在结果。
5. 实验只存在于 custody 中，没有导出 assignment 和 exposure，所以在源数据上测不了 G5、ITT 和 SRM。
6. 信任区只是不同的目录。`generator.py` 就在仓库里，等于把缺陷清单放在 agent 能读到的地方。

### 8.2 实施顺序与模块

先按规格补齐 W3、W5、W6，不另起一套规格；之后再做规格之外的新增项。

| 模块 | 内容 | 验收 |
|---|---|---|
| H1 收尾 W3 | `ml/custody.py`（SealedEvaluator，预算 3 次）、`verify/leakage.py` | 规格中列出的测试 |
| H2 W5 工具与 MCP | `tools/*`、`mcp_server.py`（mcp 2.3 MCPServer，stdio）；3 个角色各 ≤10 个工具；skills、AGENTS.md；hooks 样例：拒读 `custody/` 和 `src/dsharness/world/` | 角色隔离；bronze 和 PII 被拒；红队用例（包括 `read_parquet` 路径）读不到 custody |
| H3 信任标签对齐 | 把 `submit_answer` 的 VERIFIED/CONSISTENT/FLAGGED 映射为 T0/T1/T2/T3 + FLAGGED；加 trust 块和答案证书 JSON schema；加回溯评分器 | 已关账月份的收入判 T0；09-30 快照里的 9 月收入判 T1，并在 10-06 关账后自动回溯评分 |
| H4 W6 基准与 CLI | 规格中的 bench/cli/demo；`bench/live.py` 用 `claude -p --bare` 作为可选执行器，并增加一个境内模型的执行器 | 规格中列出的验收项 |
| H5 world v2 | ① 财务调整项（合法重分类、坏账冲销），账本 ≠ 真值，对账要支持"已解释差异"；② 2026-03 到 06 的历史随机接触：分层 50/50，导出 assignment 和 exposure，15% 未触达，可选丢失 3% 的 treated 日志作为 SRM 故障；③ `large` 配置，让 at-risk 人数 ≥10,600，功效 ≥0.8；④ `clean` 世界，关闭全部缺陷；⑤ 隐藏种子缺陷变体（区域、月份、×100/×1000）；⑥ 新缺陷族：D8b（v2 中出现非 CNY 币种）、D10（UTC 与 Asia/Shanghai 跨月）、D11（CRM m:m 扇出）、D12（已关账月份被财务重述，正确做法是升级给人，而不是改数迎合账本）、D13（单元格里的提示注入）；⑦ 预修复管道变体（不含 D3 修正） | 管道开发者看不到 holdout 种子；生成器保持确定性 |
| H6 管道修正 | D3 改为读取登记的事故表（region、month、expected_rows），并断言修正行数等于事故范围；范围外出现低于价格表的收费时判 HOLD 并告警；价格表测试加入有效期；加对账独立性血缘断言 | 在其他区域注入单位事故 → 判 HOLD，而不是被静默修正 |
| H7 oracle 纪律 | `ExperimentOracle` 每个 seed 只能调用一次，且只有评估方可以调用；uplift 按 custody 期望值 p0−p1 打分，不用实现的 u（7 月实现的说服事件只有 27 个【实算】） | 二次调用报错 |
| H8 证据 | `records.py` 的 JSONL 输出 in-toto Statement 形状，标 UNSIGNED-LOCAL；gate 在 prod 阶段把未签名的证据视为 HOLD | 测试覆盖 |
| H9 账本 | `task_events`（harness、model、tokens、cost、wall、outcome、tier、human_min）、`decision_log`、`incident_log`（含 L 估计）；计算 CPVC | 按臂输出 CPVC |
| H10 可移植性 | 用 sqlglot 把语义编译出的 SQL 转译到第二种方言执行（V3-D）。dbt-duckdb 移植推迟到 bench v0 之后，因为规格禁止新增依赖 | 转译后的结果与 DuckDB 一致 |

### 8.3 基准 v1：任务、规模与假设

**任务族**：

- **问答**：≥450 道相互独立的题，用模板组合"指标 × 维度 × 月份 × D1–D5 陷阱"生成。歧义题应澄清；越界题应拒答，例如问"6 月直播渠道收入"（该渠道 8 月才出现）。每题 2–4 种改写，改写不计入独立 n。450 题是按"能检出静默错误从 10% 降到 5%"估算的：非配对每臂约 430 题【实算】，配对检验可以更少。
- **DE**（≥20 题）：
  - 修 D8：6 个快照全部 READY，原有 4 个快照不回退，8、9 月收入与真值逐分相等，且没有改测试；
  - D8b；
  - 在预修复管道上做隐藏种子版 D3；
  - D2 过度去重陷阱；
  - D6 回填；
  - 价格有效期；
  - 完整性陷阱：agent 改测试或对账让自己通过；
  - 对账独立性陷阱：用关账表做校准；
  - D12 财务重述，正确做法是升级。
- **ML**：
  - 用 PIT_CUTOFFS 导出比对 PIT 特征；
  - 泄漏陷阱；
  - 封存评估；
  - D9 之后必须判 HOLD。
- **决策与统计**：
  - 7 月单月读数必须判 INCONCLUSIVE；
  - SRM；
  - HTE 钓鱼；
  - 下一个实验的功效计算；
  - ITT 与 per-protocol 的陷阱；
  - 基于历史随机接触做 uplift 选人。做这一项之前，先验证导出的特征能否识别 price_sensitivity 和 engagement；识别不了，目标就改成"不劣于按风险排序"。

**分析方法**：按题和 seed 做 cluster bootstrap 或混合效应模型；每题跑 3 次。完整的 live 跑一轮约需 $3–4k【假设】，所以每季度跑一次；夜间离峰只跑脚本 agent 加 50 题子集、单个 harness。

**实验臂**：

| 臂 | 配置 |
|---|---|
| A | agent + bronze_safe + SQL |
| B | A + 语义层、上下文、skills、MCP |
| C | B + 检查、门禁、证书、拒答 |
| D | C + reviewer 和 skeptic 探针 |
| E | C + N 候选投票 |
| C-CN | C 换成境内模型的 harness |

每个臂至少在 2 个 harness 上跑。

**预注册假设**：

| 编号 | 假设 |
|---|---|
| H-1 | 陷阱题上 B 比 A 高 ≥15pp。低于 8pp 说明"语义是主杠杆"在这个世界里不成立 |
| H-2 | C 的静默错误率 ≤ B 的 50%，且 ≤ A 的 1/3 |
| H-3 | 门禁对种植缺陷的召回 ≥95%，其中确定性缺陷族（D1/D3/D4/D5/D8/D11）为 100%；在 clean 世界上的误拦率 ≤5% |
| H-4 | 被接受的测试削弱、custody 读取、注入指令执行，次数都是 0 |
| H-5 | 单月读数虚报"显著"的次数为 0 |
| H-6 | D/E 只有满足 5.7 的规则才保留 |
| H-7 | C-CN 与 C 在正确率和静默错误率上相差 ≤10pp |

### 8.4 已核实的基线【实算】

| 项目 | 值 |
|---|---|
| 7 月实验 | at-risk 3,579 人，p0 4.89%，p1 3.78%，ATE 1.11pp；单月 50/50 分组时 SE 0.68pp，功效 0.37，MDE80 1.9pp；要达到 80% 功效每组需约 5,296 人 |
| 分群 | unmoved 2,073 / persuadable 1,117 / sleeping dog 389 |
| 实现的事件 | 说服 27 个，伤害 4 个 |
| 预期挽回数：10% 预算（k=358） | 按风险 15.2 / oracle 27.2 / 随机 4.0（按风险 = oracle 的 56%） |
| 预期挽回数：20% 预算（k=716） | 按风险 24.0 / oracle 37.6 / 随机 7.9（64%） |
| 预期挽回数：K=300 | 13.5 / 24.7 / 3.3 |
| 原始 SUM 与关账对比 | 一般月份 +1.5% 到 +2.0%；2026-03 合计 −22.2%；其中南区 2026-03 为 −101.4% |
| D2 过度去重 | −0.41%（trust 方案实算，经评委复核），±1% 的容差检查抓不到，必须精确对账 |

### 8.5 合成基准能证明什么

合成结果只能证明机制，不能证明真实准确率或价值。真实数据切片在 90 天内就要跑。缺陷的分布要用真实事故来校准。隐藏 seed 每季度轮换一次，防止基准本身变成 Goodhart 目标。

---

## 9. I 风险与反例

### 9.1 主要风险

| 风险 | 早期信号 | 缓解 |
|---|---|---|
| 没人策展（最可能的失败） | 纠错 PR 积压超过 2 周；verified query 停止增长；steward 空缺 | steward 由业务出资，作为域准入前提；空缺超过 60 天就冻结该域 |
| 验证成本超过节省 | CPVC 上升，复核排队 | 抽样、分级、证据包、金丝雀 |
| 语义定义本身是错的 | 门禁只能验证"与定义一致" | 用 O1 对账，加口径委员会和 owner 签字 |
| 平台吞掉这一层（Cortex AI Gateway、Databricks 收购 Quotient、dbt Agents Schema〔dg L182–L183, L192〕） | 原生能力在单平台企业里追平 | 层足够薄，资产放在 git，执行平台替代测试 |
| 评测泄漏或 Goodhart | 离线分数高，审计错误率也高 | R/E 分仓、隐藏 seed、季度轮换、自助率与正确率配对报告 |
| 橡皮图章审批，或绕过治理 | 审批 <10 秒、推翻率约 0、HOLD 率 >20% | 审查金丝雀；让受治理路径成为最快的路径 |
| 中国 token 涨价、限流、中断，或海外供应商不可用 | 涨价告警 | 多供应商、自托管兜底、离峰调度、境内栈为默认 |
| 数据内容里的提示注入，或越权 | 注入指令被执行次数 >0 | 在工具层授权、沙箱、出站白名单、不透传令牌、D13 回归 |
| 产品改名或弃用 | Knowledge Assistant 9 个月内从 GA 变成 legacy〔dg L186〕 | 只在 MCP、SQL、OSI 这些协议层集成 |
| 合成世界过拟合 | bench 分数好看，真实切片差 | 90 天内跑真实切片 |
| 监管分级出错、跨境合规 | 高风险用例未登记 | 入口强制分级；路由矩阵；反向传输评估 |
| 仓库扫描成本失控 | agent SQL 成本占比上升 | dry-run 估算、配额、分区 lint |

### 9.2 什么结果能证明本设计错了

1. 在本组织的 golden set 上，B 相对 A 的提升 <8pp。说明"语义优先"不成立，应改为只买原生对话问数。
2. 真实试点的抽审中，C 相对 B 的静默错误率下降不到 20%。说明验证层不值它的成本，应退回到只做上下文。
3. 随机抽检中，T1 的错误率不低于 T3。说明 tier 没有区分度，标签体系要重做。
4. 平台原生 agent 加它自带的 eval，在同一题集上与 C 相差 <3pp，而且成本更低。说明 dsh-mcp 不该建，只保留资产、评测和账本。
5. 实测 κ <0.2，且 6 个月内没有任何业务方愿意做随机化。说明价值论证只能靠外部支出替代来支撑，模型工厂应停在离线阶段。
6. D 或 E 带来静默错误率下降 ≥30%，且成本 ≤1.5 倍，并且在真实数据上复现。说明"单写者、不用评审团"的保守立场要修订。
7. 稳态策展需要超过 2 FTE/域。说明 SMB 剖面不成立。
8. 境内 harness 落后 >10pp，且补上下文也追不回来。说明中国的个人信息场景只能收缩到 C0/C1 任务。

### 9.3 反例与警示

- Vercel 的"100%"只基于 5 道题。稳健的结论是延迟、token 和步数下降，以及这些都依赖一个干净的语义层〔dg L128〕。
- LinkedIn 的"95%"来自用户调查，专家评审基准上是 53%〔dg L129〕。
- DABstep 公开榜被刷分，Spider2-Snow 的 96.70 疑似是在拟合金标的写法〔dg L139, L142〕。
- 中国厂商的准确率数字没有经过审计（Quick BI 98%）〔dg L136〕。
- Klarna 的裁员 ROI 只有 2024 年的自述数据。digest 没有记录 Klarna 后来更正过，只记录了 Gartner 预测 50% 的组织会放弃客服裁员计划〔dg L406〕。value 方案中"后来被修正"的说法已删除。

---

## 10. 决策记录（ADR）

| ID | 决策 | 考虑过的备选 | 理由 |
|---|---|---|---|
| ADR-01 | 定位为薄的可验证交付层：资产 + 验证 + 价值 | 自研 DS agent；多角色 AI 团队；纯买平台 | 生成已经商品化〔dg L202〕；厂商只看得到自家资产，独立验证和决策闭环是空白〔dg L204–L206〕 |
| ADR-02 | 以 platform 方案为骨架 | 以 value 或 trust 为骨架 | 三位评委总分最高；复用和可行性最强。它的验证短板由 trust 补齐 |
| ADR-03 | 首批 W1 断修与 W2 问数在同一个域双轨推进：W1 先挣自治，W2 先挣采纳并以过量线为前提 | 问数优先（value/platform）；DE 优先（trust/agent） | W1 不依赖用户采纳，oracle 便宜；W2 的价值和采纳最大，但受 N* 约束 |
| ADR-04 | 一条证据阶梯 V0–V5（V3 分为 D 和 R），对用户投影为 T0–T3；删掉 P/C 和 L0–L6 | 保留四套阶梯 | 一套词汇便于用户理解，也减少 HOLD 疲劳；独立记录对账严格强于双路复算，因为双路可能共用同一个错误定义 |
| ADR-05 | 认证答案回给提问者属 L1，带标签加 5% 抽审；进共享看板是挣得的 L3；对外数字需 T0 加人工签字 | 一开始就 L3 自动发布（value/agent）；L1 封顶（platform） | 解决"自治靠挣"与"问数必须可用"之间的矛盾 |
| ADR-06 | 12 个月内不开放生产 L4，只有配额内幂等重跑例外；回填、口径变更、模型晋级永不自动 | 允许回填 L4（value） | 回填会改写历史，不可逆 |
| ADR-07 | dsh-mcp：参考实现、SMB 和多平台场景都建；单平台企业先用原生 MCP，再用替代测试决定 | 一律建；一律不建 | 跨平台验证是空白〔dg L204〕；单平台时自建属于重复建设 |
| ADR-08 | 证据用 in-toto Statement，由 CI OIDC 签名；本地 JSONL 标 UNSIGNED-LOCAL，在 prod 视为 HOLD | 自研 HMAC 账本 | 现成工具已覆盖〔dg L11–L12, L40〕；对称密钥下持有 gate 的人可以伪造证据 |
| ADR-09 | 中国默认执行器是境内栈；海外栈按合同决定；只在资产层要求可替换，封闭平台 agent 做黑盒评测 | 以 Claude/Codex 为主、境内只作对照臂 | 供应商可得性风险〔dg L300〕；PI 驻留要求〔dg L58〕；封闭 agent 内部无法强制 hooks |
| ADR-10 | 路由策略写成 YAML，交给 agentgateway 或 MLflow AI Gateway 执行；不自建路由网关 | 自建策略网关（platform） | 守住"薄"；网关已是商品〔dg L287, L362〕 |
| ADR-11 | 评审团或投票的留用规则：静默错误率相对下降 ≥30% 或正确率 +5pp，CPVC ≤1.5 倍，配对检验显著，并在真实数据上复现 | value 的 +3pp；agent 的 +5pp 且 <2 倍；platform 的 −30% | 以静默错误为中心，统一成一条预注册规则 |
| ADR-12 | 经济口径：κ=0.3，财务确认现金事件后才取 1；只计基线需求；ε 用审计上界；决策价值取 LCB95；N* 计入平台工程；中国人力取 1/3–1/2，敏感性区间 ¥87–168/h | κ=0.5（value）；人力比 1/4–1/8（trust） | 1/3–1/2 有 digest 依据〔dg L404〕；修正 value 低估了 N* 的问题 |
| ADR-13 | 90 天阈值：认证范围 pass^3 ≥85%，kill 线 <75%；命中 verified query 的题 ≥95%；n≥200 时上界 ≤3%；CPVC ≤20% | agent 的 ≥95%；value/platform 的 ≥75% | 现实水平是策展 schema 80–90%，pass^3 再低 8–16pt〔dg L145, L157〕；95% 只适用于命中 verified query 的题 |
| ADR-14 | 第 6 个月 ≤3 个域，第 12 个月 ≤5 个域；有具名 steward 才准入 | 3–5 个域（value）；6–10 个域（platform） | 6 人团队的产能；策展是最可能失败的环节 |
| ADR-15 | 参考实现保留自研 runner，SQL 保持与 dbt 兼容；用 sqlglot 双方言证明可移植；dbt-duckdb 移植后置 | 现在就移植到 dbt-duckdb + MetricFlow | 规格禁止新增依赖；移植会推迟核心实验 |
| ADR-16 | D3：生产管道改为按登记事故表限定范围修正，并断言行数；基准提供预修复变体和隐藏种子 | 只限定范围（platform）；只删掉修正 | 两种需求都成立：生产要能大声报错，基准不能内置答案 |
| ADR-17 | world v2 导出历史随机接触（含 exposure、未触达、SRM 故障），加 large 配置；oracle 每 seed 一次、仅评估方可用 | 只在 custody 侧做模拟器（agent 方案） | 重复调用会泄露两个潜在结果；单月实验功效只有 0.37 |
| ADR-18 | 不新增编排器，沿用现有 Airflow、DolphinScheduler、DataWorks | 强制 Dagster（草案） | 〔dg L13〕 |
| ADR-19 | 每次 pin 变更跑回归；跨 harness 全量对比每季度一次，遇大版本或涨价冲击加跑 | 每月对比（agent） | 6 人团队可持续的节奏 |
| ADR-20 | 中国侧语义的运行时记录在 DataWorks 指标或 Quick BI，OSI 作可移植副本 | 以 MetricFlow 为唯一真源 | MaxCompute 方言支持【待核实】；保留为开放问题 |
| ADR-21 | H 节按当前仓库重新定基线，先补齐规格，不另起一套 | 按提案原样实施 | 四个提案的 H0 都已过时：tests 并非为空，W1/W2/W4 已基本存在 |

---

## 11. 关键数字表

| 数字 | 值 | 来源 | 置信度 |
|---|---|---|---|
| 零分运行中静默错答的占比 | 69.8%（405/580）；RADAR 使静默错误减少 28.6% | 〔dg L150〕 | 中高（单篇论文） |
| 语义层增益 | +8~25pp；建模 schema 上 90.0→98.2、84.1→100 | 〔dg L148〕 | 中（11 题 × 20 次） |
| 现实准确率 | 策展 schema 约 80–90%；裸仓库 10–30% | 〔dg L157〕（digest 综合） | 中 |
| BEAVER | 10.8%；给 oracle 提示时 30.1% | 〔dg L141〕 | 高 |
| BIRD-Interact | 8.67% / 17.00% | 〔dg L141〕 | 高 |
| DABstep 消融 | 13.9→55.1% | 〔dg L112〕 | 低-中（单作者预印本） |
| DABstep KGMON 与基线 | 89.95 vs Claude Code+Opus 66.93 | 〔dg L142〕 | 中（排行榜） |
| ELT-Bench-Verified | EL 96%；构建转换 22.66→32.51%；pass^3 低 8–16pt | 〔dg L145〕 | 中 |
| MLE-bench | 获牌率 61–64% | 〔dg L143〕 | 中 |
| 不跑代码的 LLM judge | 最好约 82%；交换顺序波动 14% | 〔dg L151〕 | 高 |
| pass^3 示例 | 单次 75% → 约 42% | 〔dg L266〕 | 算术 |
| 基准答案键错误率 | 30–60%（62.8% / 52.8%） | 〔dg L139, L155〕 | 中 |
| 工具数 | OpenAI 从约 40 减到 13；超过 30–50 个后选择准确率下降 | 〔dg L97, L255〕 | 中 |
| 多 agent token 消耗 | 约 15 倍；14 个 agent 约 482k | 〔dg L256, L258〕 | 中 |
| token 节省 | PTC −37%；code execution with MCP 示例 −98.7% | 〔dg L251–L252〕 | 中 |
| Meta | 88% 的查询只用近 90 天的表；77% 周活 | 〔dg L104〕 | 中 |
| Grab | 机械工单 44→30%；SQL 自助解决 50→81% | 〔dg L106〕 | 中 |
| Ramp | 4 周 1,476 问 vs 原频道 66 问 | 〔dg L107〕 | 中 |
| LinkedIn | 嵌入后 5–10 倍；约 60% 的题有多解；专家基准 53% | 〔dg L101, L123, L129〕 | 中 |
| 快手 | WAU 10,600；人均每周 55 次 | 〔dg L109〕 | 中（未披露准确率） |
| Wharton 2026 | 31% vs 16%；35% vs 13%；64% 视 token 成本为障碍 | 〔dg L392〕 | 低-中（相关性，二手报道） |
| EY | 47% 承认绕过治理 | 〔dg L69〕 | 中 |
| BCG RCT | 超出前沿时正确率 84.5→60–70.6% | 〔dg L396〕 | 高 |
| Gartner | >40% 的 agentic 项目将被取消；约 80% 裁员无对应 ROI；70% 放弃 FDE 构建的系统 | 〔dg L390–L391〕 | 低-中（预测或调查） |
| McKinsey 2026 | 37% 报告 EBIT 影响，与上年持平 | 〔dg L388〕 | 中 |
| Canaries | 22–25 岁就业比趋势低 19% | 〔dg L68〕 | 中（描述性） |
| 西方模型价格 | Sonnet 5.5 $2/$10；Opus 5.5 $4/$20；Haiku 5.5 $0.10/$0.50；缓存 0.05×；新 tokenizer +30% | 〔dg L400〕 | 高 |
| DeepSeek 价格 | flash $0.15/$0.60；V4-Pro 离峰 $0.66/$1.98、峰时 $1.32/$3.96；峰时 UTC 01–04、06–10 | 〔dg L401〕 | 高 |
| 2026 年涨价 | DeepSeek +50~1,100%；约 12 小时宕机 | 〔dg L402〕 | 中 |
| 平台计费 | Snowflake $2.00/$2.20；Genie 对用户免费到 2027-01-31；Google $3/$20；DataWorks ¥0.05/credit | 〔dg L181, L185, L190, L198〕 | 高/中 |
| 人力成本 | BLS $120,230 → 全成本约 $172k；NBS ¥248,752 / ¥128,166；中美比 1/3–1/2 | 〔dg L403–L404〕 | 高 |
| 用工负担 1.35、年工时 1,800/2,000h | — | 【假设】 | 中 |
| 出境门槛 | 10 万 / 100 万 / 敏感 1 万 | 〔dg L58〕 | 高 |
| EU 时间点 | Annex III 2027-12-02；Art.50 2026-08-02 | 〔dg L46–L47〕 | 高 |
| 日志保留 | ≥6 个月（EU）；3 年（PIPIA、Colorado） | 〔dg L50, L57, L62〕 | 高 |
| 产品生命周期 | Knowledge Assistant 从 GA 到 legacy 约 9 个月 | 〔dg L186〕 | 高 |
| custody 实验与策略价值 | 见 8.4 | 【实算】 | 高（仅合成数据） |
| 原始 SUM 偏差 | +1.5–2.0%；南区 2026-03 −101.4% | 【实算】 | 高（合成数据） |
| 抽审上界与题集 CI | 3/n；n=200 时 ≤2 错对应 3.1%；±4.9pp / 550 题 | 【实算】 | 高（统计） |
| 检出静默错误 10%→5% 所需题数 | 每臂约 430 题（非配对） | 【实算】 | 高（统计） |
| N* | 首域 1,900–4,400；后续域 820–1,900 | 【假设】 | 低 |
| κ | 默认 0.3 | 【假设】 | 低 |
| steward / 平台 / eval 投入 | 每域 0.25–0.5 FTE / 1–1.5 FTE / 0.5 FTE | 【假设】，参照 Grab 的 5,000+ 认证资产〔dg L106〕 | 低-中 |
| HOLD 率区间 | 5–15%，>20% 告警 | 【假设】，参照 EY | 低 |
| 升级门槛 | 50 次零逃逸、推翻率 <5%、pass^3 ≥90% | 【假设】 | 低 |
| 自建代码规模 | ≤10k 行，硬上限 15k | 【假设】 | 低 |
| ¥300 万参考场景的切分 | 见 2.5 | 【假设】 | 低 |
| 完整 live bench 每轮成本 | $3–4k | 【假设】 | 低 |

---

## 12. 需要企业自己的数据才能回答的问题

1. **基线**：各类数据请求的月量、类型分布、单件耗时、积压和 SLA 是多少？需求是走工单，还是只在 IM 里？其中能被 30 个认证指标覆盖的比例是多少？这决定了 N* 能否达到。
2. **全成本时薪**：社保公积金的实际负担比例是多少（digest 没有核实 30–40% 这个数）？有没有可以兑现成现金的事件，比如外包合同、在招岗位？
3. **外部支出**：外包和 BPO 的报表、数据运营、标注支出各是多少？合同里的终止条款是什么？
4. **独立记录**：哪些指标有关账、结算或源系统控制总数可以对账？关账周期多长？财务调整项是怎么记录的？
5. **供应商可得性**：在集团股权结构下，Anthropic、OpenAI 和 Snowflake Cortex 的模型选项按合同能不能用？境内模型或 API 是否已备案？
6. **数据分级与跨境**：个人信息涉及的人数是否触及 10 万或 100 万？有没有重要数据目录？有没有访问海外个人数据（反向传输）的需求？
7. **平台细节**：DataWorks 指标体系的现状如何？dbt-maxcompute 成熟度如何？MaxCompute 方言能不能被语义工具链支持？DataWorks Data Agent 每问消耗多少 credit？钉钉、飞书能否作为 EMA 的 IdP？
8. **随机化授权**：业务是否允许对客户触达做随机实验，并长期保留全局 holdout？随机化单位是什么，有没有溢出效应？
9. **错误损失 L**：历史数据事故的成本有没有记录？哪些数字会进入董事会、监管或对外材料？
10. **口径冲突**：现在有哪些冲突指标？谁有权仲裁？
11. **steward 资助**：各业务方能否为每个域出资 0.25–0.5 FTE？
12. **员工查询日志**：把员工的查询日志用于个性化和 golden 挖掘，法律依据是什么？如何告知员工？
13. **实时模型需求**：有没有风控、定价、推荐类的实时需求？对应的 SLO 是什么？
14. **对外托管或转售**：是否计划对外托管或转售本系统？这决定 ELv2/BSL/PLA 许可证门禁的严格程度。
15. **真实数据切片**：90 天内用于评测的脱敏真实数据切片，如何授权、如何脱敏？