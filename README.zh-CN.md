# DS Agent Harness

> English: [README.md](README.md) · [docs/DESIGN.en-US.md](docs/DESIGN.en-US.md)

**DS Agent Harness** 是一层可验证的交付层。它让任意 AI Agent（Claude Code、Codex、Qwen Code、平台原生 agent 等）能够在企业数据上做数据科学和数据工程工作，并且每个交付物都附带证据。

- 完整设计方案：[docs/DESIGN.zh-CN.md](docs/DESIGN.zh-CN.md)
- 调研证据：[docs/REFERENCES.md](docs/REFERENCES.md)

本仓库是这份方案的**参考实现与测量仪器**，不是生产平台。它用一家合成的订阅制公司来做实验：这家公司的每个数字都有已知真值，源数据里埋了真实世界里常见的缺陷。借助它可以实测一件事：任意 agent 加上这层 harness 之后，静默错误还能剩下多少。

> 合成结果只能证明**机制**有效，不能证明真实准确率，也不能证明业务价值。

## 快速开始

需要 Python 3.12 和 [uv](https://docs.astral.sh/uv/)。全部离线运行，不需要 API key。

```bash
uv sync
```

生成合成世界（源系统导出和 custody 真值）：

```bash
.venv/bin/dsh world
```

构建受治理的数据快照。drift 和 monitor 两个快照会按设计失败（计费 schema 变成了 v2）：

```bash
.venv/bin/dsh build --all
```

端到端场景，约 1–2 分钟：

```bash
.venv/bin/dsh demo
```

脚本化基准：

```bash
.venv/bin/dsh bench
```

ROI 计算器，每个输入都标注了来源或"假设"：

```bash
.venv/bin/dsh roi --profile cn
```

全部测试：

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider
```

## 合成世界里埋了什么

| 代号 | 缺陷 | 不处理会怎样 |
|---|---|---|
| D1 | 至少一次投递导致的重复发票行（invoice_id 相同） | 收入虚高 |
| D2 | 同一客户、同一天、同一金额、但 id 不同的合法加购 | 按金额去重会误删 |
| D3 | 南区 2026-03 的收费以"元"导出 | 当月收入少约 22%，南区约少 100% |
| D4 | 退款单独成行，记在退款发生的月份 | 净收入口径错 |
| D5 | 内部 QA 测试账号 | 指标被污染 |
| D6 | 用量数据迟到数周 | 特征穿越 |
| D7 | 可变的"当前状态"列（价格、ended_at、cancel_reason） | 时间泄漏 |
| D8 | 2026-08 起计费导出 schema 变更 | 管道断裂 |
| D9 | 2026-08 起出现新渠道，plus 套餐涨价 | 分布漂移 |

**业务杠杆**：

- 2026-03 至 06，公司做了一个**随机化**的挽留项目，数据导出为 `crm_retention_contacts`。
- 挽留的真实效果是异质的：价格敏感但仍活跃的客户有反应；已经流失倾向很强的"无望客户"没反应；一部分客户被打扰后反而更容易流失（"sleeping dog"）。
- 因此**按流失风险排序 ≠ 按价值排序**。

## 实测结果（合成世界，详见设计方案 §9）

| 项目 | 结果 |
|---|---|
| 脚本化基准（38 项，其中 28 项被植入错误） | 不加 harness：28 项错误全部以"成功"交付。加上 harness：**静默交付 0 项**。代价是 3 项正确结果没有自动交付（2 项转人审，1 项按设计 HOLD） |
| 问数 | 裸 SQL、按外观去重、漏排测试账号、忽略退款、主张与数字不符：全部被与受治理指标的对账拦下 |
| 管道断修 | ÷100 修复、只适配 v2 的修复、用财务表"校准"、删除对账：全部被 REJECT；向后兼容的正确修复被 ALLOW |
| 泄漏 | 自报 dev AUC 0.948 的泄漏模型（封存真值约 0.67）被 REJECT；正确模型的封存 AUC 为 0.712 [0.679, 0.747] |
| 决策 | 按风险联系客户，真值 −1.69 元/人，naive 却声称 +19.84 元/人。uplift 策略真值 +0.78 元/人，但单月实验只能给出 INCONCLUSIVE，所以扩量被 HOLD。电话渠道对任何策略都亏损，被拦下 |
| 测试 | 694 个（含对抗审查的回归测试） |

## 架构（对应设计方案 §3–§5）

```
src/dsharness/
  world/        合成公司：已知真值 + 9 类缺陷 + 随机化挽留项目
  lake/         源契约（Pandera）→ bronze → staging → silver（dbt 风格 SQL + 数据测试 + 与财务关账对账）
  context/      语义层（指标定义）、目录（PII/时间语义标签）、verified queries（运行集/评测集分离）
  verify/       SQL 静态检查、对账、时间穿越泄漏检查、统计（bootstrap、SRM、校准）
  evidence/     证据记录 + 门禁（ALLOW/HOLD/REJECT）+ 审批（禁止自审）+ in-toto 导出
  ml/           时点正确的特征、声明式训练模板、封存评估（有预算）、uplift 学习
  decision/     策略、随机化实验、一次性 oracle（仅评估方可用）、实验读数
  monitor/      漂移（PSI）
  economics/    价值与成本模型（CPVC、盈亏点）
  tools/        受治理的工具层（按角色隔离、审计、预算）—— agent 唯一的数据入口
  mcp_server.py MCP 服务（mcp 2.x，stdio）
  bench/        基准：真值来自评估方；careful 和 naive 两类脚本 agent；真实 agent 运行器（bwrap 沙箱）
  readout/      评测读数门禁：重读逐题结果，检查错误行、评委、作答模型、重复批次、区间、第二评委、接线
.claude/skills/ Agent Skills：analytics-answer / churn-modeling / pipeline-breakfix / retention-uplift
AGENTS.md       给任何编码 agent 的仓库约定
.mcp.json       三个角色的 MCP 服务器：dsh-analyst / dsh-ds / dsh-de
```

信任区（`workspace/`）：

| 目录 | 内容 | 谁能访问 |
|---|---|---|
| `sources/` | 源系统导出 | — |
| `lake/` | 受治理快照 | agent 只能通过工具读取 |
| `custody/` | 真值、封存标签、实验账本 | 只有评估方 |
| `registry/` | 证据、审批、门禁、审计 | 只追加，不改写 |
| `runs/` | 候选产物和 CI 构建 | — |

## 让真实 agent 接入

1. 先执行 `dsh world`、`dsh build --all`。
2. 在仓库根目录启动 Claude Code。`.mcp.json` 里的三个 MCP 服务器和 `.claude/skills/` 会被自动发现；项目级 MCP 服务器第一次使用时需要你批准。
3. 也可以用其他 CLI：Codex、Gemini CLI、Qwen Code 都读 AGENTS.md，也支持 MCP 和 Skills。
4. 想对真实 agent 打分（需要你已登录 `claude`，否则会显示 SKIPPED）：

   ```bash
   .venv/bin/dsh live --tasks A1 A2 A3 --k 3 --model claude-sonnet-5-5 --budget-usd 0.5
   ```

   它会输出 pass@k、pass^k 和每个正确答案的成本。也可以用 `--command-template` 接入其他 CLI。

## 评测读数门禁（`dsh readout`）

“A 比 B 好 +1.2 分”“两者一样”这类评测结论，在写进文档或做决定之前，先过一道门：重读结论背后的逐题结果文件，做 9 项检查（错误行没被算分、同一评委、同一作答模型、至少两批、题目配对、重算区间、小差值要第二评委、两组答案不完全相同、对照同批），按 `readout_publish` 门禁给出 ALLOW / HOLD / REJECT。

```bash
.venv/bin/dsh readout path/to/claim.yaml --out decisions.json   # 退出码：0 放行，3 暂缓，4 拒绝
```

每条结论一个 YAML：处理组和对照组、结果文件目录、声称的结论（passed / not_passed / equal / worse）和出处。用法见 `src/dsharness/readout/core.py` 和 `tests/test_readout.py`。

## 生产化时替换什么

| 参考实现 | 生产中用 |
|---|---|
| 自研的极小 SQL runner | dbt / SQLMesh / DataWorks |
| YAML 语义层 | MetricFlow / Cube / 平台语义视图（OSI 便携副本） |
| DuckDB | 企业数仓（查询下推，OBO + RLS） |
| 本地 JSONL 证据 | CI OIDC 签名的 in-toto 证明 + 受保护环境 |
| 进程内候选构建 | gVisor/Kata 沙箱 |
| 字符串身份 | IdP/EMA |

详见设计方案 §3.4 与 §11（ADR）。

## 真实数据端到端示例：UCI Online Retail II

用公开的真实零售交易数据（106 万行，CC BY 4.0）跑一遍完整流程：源契约 → 受治理管道 → 问数对账 → 时间穿越泄漏检查 → 封存评估 → 门禁。结果见 [docs/examples/ONLINE_RETAIL_E2E.zh-CN.md](docs/examples/ONLINE_RETAIL_E2E.zh-CN.md)。

```bash
uv sync --group external   # 读取 xlsx 需要 openpyxl
```

再从 https://archive.ics.uci.edu/dataset/502/online+retail+ii 下载数据，转换成 `workspace/external/online_retail_ii/online_retail_ii.parquet`。转换方法见 `examples/online_retail/run_e2e.py` 的文档字符串。最后运行：

```bash
.venv/bin/python examples/online_retail/run_e2e.py   # 本机负载较高时约需 1 小时以上
```
