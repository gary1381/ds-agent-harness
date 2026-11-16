# DS Harness: An Evidence-First Delivery Layer for AI Agents in Data Science and Data Engineering

**Design document v1.0, US / EU edition. 2026-10-19.**

> **Audience.** CDOs, CTOs, CFOs and data-platform leads at US and European enterprises. The default reader is a US company that runs Snowflake, Databricks, BigQuery or Microsoft Fabric. EU-specific requirements are called out where they differ.
>
> **Relationship to the Chinese edition.** The architecture, verification model and reference implementation are shared with `DESIGN.zh-CN.md`. This edition differs in four areas: economics (USD, US labor costs), the default agent and platform stack, data-residency routing, and the compliance and organizational sections (US federal and state law, banking model risk, EU AI Act and GDPR).
>
> **Basis.** The document rests on three inputs:
> - **Same-day research.** 179 web-verified findings, cited as [E-x.y]; see [REFERENCES.en.md](REFERENCES.en.md).
> - **An independent design panel.** Four competing proposals, scored by three judges and merged into one synthesis; see `research/`.
> - **A runnable reference implementation, `dsharness`.** It measures the design's mechanisms on a synthetic company with planted ground truth and planted defects; see §9.
>
> **Conventions.**
> - **[assumption]** marks an engineering estimate. Replace it with measured values during a pilot.
> - **[measured]** marks a number produced by dsharness on synthetic data.
> - Synthetic results prove *mechanisms*. They are not evidence of real-world accuracy or business value.

---

## 0. Executive summary

1. **Do not build another "AI data-science team."**
   - Generating SQL, dbt models, pipelines and model code is now a commodity. Snowflake CoWork/CoCo, Databricks Genie Code and Genie One, the BigQuery data-engineering and data-science agents, Looker Conversational Analytics, the Fabric data agent and dbt Wizard are all GA or in public preview [E-5.1–5.22].
   - A "supervisor plus a dozen fixed role agents" architecture costs a lot and returns little on tightly coupled data work, where a single writer is required [E-6.15, E-6.16].

2. **Build a thin, vendor-neutral *verifiable delivery layer*: the DS Harness.** It consists of four assets you own as code:
   1. **Context assets.** Semantic layer and metric definitions, certified tables, verified queries, Agent Skills, AGENTS.md.
   2. **Deterministic verification and CI gates.**
   3. **An agent evaluation suite.**
   4. **A value ledger.**

   Buy or reuse everything else: the agent loop, NL-to-SQL, warehouse, orchestrator, catalog, model registry, sandbox and LLM gateway. Any agent can plug in through MCP tools and Skills and deliver *data products with evidence attached*. That includes platform-native agents, Claude Code, Codex and Gemini CLI.

3. **The bottleneck is semantics and verification, not generation.**
   - **Semantic layer gains.** In dbt's small test (11 questions × 20 runs), adding a semantic layer on a modeled schema lifted accuracy to 98–100%, from 84–90% without it [E-4.11].
   - **Raw warehouses.** On BEAVER, a benchmark built from private enterprise warehouses, the best agents reach only 10.8% (30.1% with oracle hints) [E-4.4].
   - **Size of the effect.** These are different benchmarks. The independently measured semantic-layer gain is about +8 to +25 points. Its larger effect is to turn plausible wrong answers into explicit "cannot answer" [E-4.11].
   - **Silent errors dominate.** In one study, 69.8% of a baseline agent's zero-score runs were normal-looking wrong answers [E-4.13].
   - **What does not fix them.** Self-reflection and multi-agent debate do not. Debate's gains are mostly a voting effect [E-4.16], and LLM judges that do not run the code top out around 82% [E-4.14].

4. **Start with two workflows in one business domain that has an independent record**, such as revenue or subscriptions with a financial close:
   - **W1: data-pipeline break-fix and change.** The oracle is CI plus exact reconciliation. It is cheap and earns autonomy first.
   - **W2: certified-metric Q&A.** It uses the semantic layer plus a golden set, and earns adoption first.

   Machine learning comes second, delivered as *decision + randomized experiment*, never as an AUC.

5. **Without a complete ground truth, construct oracles instead of assuming one exists.** In order of strength:
   1. Reconciliation with independent records: the financial close, payment-processor settlement files, source-system control totals.
   2. Invariants and metamorphic relations.
   3. Dual-path recomputation.
   4. Golden sets.
   5. Delayed-truth back-scoring.
   6. Random audits.
   7. Randomized experiments.
   8. Fault injection.

   Each counts as evidence only within its scope (§5.1).

6. **One evidence ladder, one set of user-facing labels, one gate.**
   - **Ladder:** V0–V5.
   - **Labels:** T0–T3.
   - **Gate:** ALLOW, HOLD or REJECT.
     - Missing, stale, errored or inconclusive evidence leads to HOLD.
     - Only an authenticated FAIL leads to REJECT.
     - An agent cannot write its own PASS.

   An unlabeled number does not enter a KPI, a board deck, an external report or a SOX-scoped figure.

7. **Count value through three auditable channels only:**
   1. Audited-correct *baseline* demand × net time saved × a realization factor κ (default 0.3).
   2. Displaced external spend.
   3. Decision uplift proven by a randomized experiment, taken at its 95% lower bound.

   Layoffs, DAU and self-reported "hours saved" are not ROI. The US labor-cost base makes the hours-saved case viable at moderate volume (§2.4), but surveys still show flat EBIT impact [E-8.4]. Design for measured value, not for adoption.

8. **US/EU defaults.**
   - **Agents.** Run platform-native agents where the data lives, plus a self-run harness (Claude Code / Agent SDK, Codex, Gemini CLI) for cross-platform and CI work.
   - **Identity.** Enterprise-Managed Authorization for MCP through Okta, the first supported IdP [E-6.3].
   - **Data residency.**
     - EU personal data uses EU-region endpoints with zero data retention (ZDR). Do not rely on the EU–US Data Privacy Framework alone, because its appeal is pending [E-2.8].
     - Protected health information (PHI) goes only to services covered by a business associate agreement (BAA). Claude Managed Agents and programmatic tool calling are not ZDR- or BAA-eligible [E-6.10].
     - Multinationals: personal data of China residents stays in China.

9. **The reference implementation works, and it measured the mechanisms [measured].**
   - **What it contains.** `dsharness` includes:
     - a synthetic subscription company with 9 planted defect classes, known truth and a randomized retention program;
     - a governed pipeline and a semantic layer;
     - role-scoped MCP tools and a verification library;
     - gates, sealed evaluation, uplift learning and experiment readouts;
     - a benchmark and 444 tests.
   - **Benchmark.** The benchmark has 38 items. In 29 of them an agent used a deliberately wrong method; 28 of those 29 produced a wrong result.
     - Without the harness, all 28 wrong results were delivered as "success".
     - With the harness, **0 were delivered as trusted**.
     - The cost: 3 correct items were not auto-delivered. Two went to human review and one was held by design.
   - **The decision loop told an unwelcome truth.**
     - Contacting the highest-risk customers is truly worth −¥1.69 per contact, roughly −$0.24. A naive analysis claimed +¥19.84, roughly +$2.79.
     - The learned uplift policy is truly worth +¥0.78, roughly +$0.11, but a one-month experiment can only say INCONCLUSIVE. The harness therefore held the scale-up.
     - Phone-call outreach loses money under any targeting, and it was blocked.

   See §9.

10. **Falsification criteria come first (§10.2).** Shrink the build if either of these holds on *your* golden set:
    - the semantic layer adds less than 8 points;
    - platform-native agents come within 3 points of this design at lower cost.

    In that case keep only the assets, the evals and the ledger.

---

## 1. Reframing the problem

### 1.1 Four hard questions, plus a fifth

The four questions are:
1. Is the data correct?
2. Is the processing and use of the data correct?
3. Is the model reliable?
4. Does it ship and create value?

All four are right. A fifth is missing: **is there evidence for each claim that matches its intended use?** The five kinds of "correct" are independent, and none substitutes for another.

| Layer | Question | Typical failure | Main oracle |
|---|---|---|---|
| Computational | Does the code compute the definition? | Wrong dedup key, fan-out joins, unit errors | Invariants, dual-path recomputation, reconciliation |
| Semantic | Is the definition what the business means? | Does "net revenue" include refunds and exclude test accounts? | Semantic layer with owner sign-off; metric-definition council |
| Statistical | Is the inference valid? | Leakage, wrong split, peeking at the holdout | Sealed evaluation, time-travel checks |
| Causal | Does the action work? | Correlation taken as causation; ranking by risk instead of value | Randomized experiments |
| Business | Did a decision change, with net benefit? | AUC up, margin down | Experiment readout plus value ledger |

### 1.2 Missing ground truth, and panels of debating agents

**Missing ground truth is the normal state of data science.** The way out is not a universal verifier model. It is to build cheap oracles at every step, and to limit each conclusion to the use its evidence supports.

Most US enterprises already hold "free ground truth" that is rarely used systematically:
- the closed general ledger and SOX-controlled financial figures;
- processor settlement files from Stripe, Adyen and similar;
- billing-system control totals;
- published dashboards;
- outcomes that arrive later, such as whether a customer actually churned 30 days on.

**Multi-agent debate or a judge panel should not decide correctness.** Three reasons:
- Debate behaves like a martingale; its gains come mostly from voting [E-4.16].
- Agents built on the same models make correlated errors, so four agreeing votes are not four independent pieces of evidence.
- LLM judgments swing by up to 14% when answer order changes [E-4.14].

Panels have three legitimate uses:
1. N candidates for a high-value query, selected by execution results.
2. A clean-context reviewer on pull requests. It may output only executable probes, and a probe counts only if it actually fails the candidate in CI.
3. Pairwise, order-randomized judging of *narrative quality*, calibrated against human labels.

Any panel that is on by default must first pay for itself in a same-budget ablation (ADR-10).

### 1.3 What we keep and change from the earlier EvidenceDS v0.1 draft

| Disposition | Content | Basis |
|---|---|---|
| **Keep** | Separate execution, check and promotion states. Missing, ERROR or INCONCLUSIVE evidence means HOLD. Point-in-time and label-maturity rules. A holdout sealed by identity, not by directory. G5 (bounded experiment) kept separate from G6 (value claim). Shadow mode is not value evidence. | [E-1.1–1.3] |
| **Fix** | The original gate had three bugs: (1) it counted evidence before authenticating it, so unsigned records could force a REJECT; (2) it had no supersede or retry semantics, so a PASS after an ERROR still gave REJECT; (3) its tests overstated their coverage. | [E-1.7–1.9]; fixed in `evidence/gate.py`, 64 tests |
| **Drop** | The custom HMAC evidence ledger, the 17-table Postgres schema, the 13-endpoint API and the reconciler. Use CI OIDC identity with in-toto/Sigstore attestations and protected environments instead. With symmetric keys, whoever runs the gate can forge any evidence. Also dropped: the mandatory Dagster, and "agent adapter last". | [E-1.10–1.12] |
| **Add** | Semantic layer, context engineering, analytics Q&A, DE break-fix, an agent eval set, model routing and data residency, an ROI model with kill criteria, and adoption. | [E-1.13–1.22] |
| **Redirect** | The first vertical is no longer "rank customers by churn risk and call them". That is the textbook case where risk targeting loses to uplift targeting, and dsharness reproduces it (§9). | [E-1.23] |
| **Re-deploy** | No shadow copies of raw data on local disks. Queries are pushed down to the warehouse and run under the user's identity (OBO + row-level security). | [E-1.24] |

---

## 2. Value and economics

### 2.1 Use-case portfolio

Ranked by measurable value × verifiability × feasibility. Every KPI is reported together with an audited correctness rate.

| # | Workflow | Main KPIs | Oracle | Feasibility evidence | Autonomy: start → 12-month cap | Priority |
|---|---|---|---|---|---|---|
| W1 | **DE break-fix and change**: schema-drift adaptation, failed-job triage, dbt/SQL changes, new models from existing patterns, backfill plans | MTTR, data downtime, merged-without-rework rate, 30-day rollback rate, escaped defects | CI tests, data-diff, exact reconciliation | Extract and load ≈96%; *building* transformations 23–33%, with pass^3 8–16 points below pass@1 [E-4.8]; data-level bugs need real queries [E-4.9]; OpenAI migrated ≈10k DAGs with Codex [E-3.3]; Grab's Scarlet [E-3.12] | L2 → L3 (non-prod) | P0 |
| W2 | **Certified-metric Q&A and data pulls** | Self-serve resolution rate, share of mechanical tickets, time to first answer | Semantic definitions, verified queries, delayed-truth back-scoring | Semantic layer +8 to +25 points [E-4.11]; Grab: SQL self-serve rose from 50% to 81%, mechanical tickets fell from 44% to 30% [E-3.12]; Ramp: 22× question volume [E-3.13] | Answer to the asker L1; earned L3 for shared dashboards | P0 (only above break-even) |
| W3 | Data-quality and reconciliation monitoring and triage | Catches before consumption, time to locate, false-alarm rate | Reconciliation, invariants | Deterministic checks | L1 → L2 | P1 |
| W4 | A/B readout skill | Readout cycle time, power/SRM errors caught | Deterministic stats library | — | L2 → L3 (internal) | P1 |
| W5 | Displacing outsourced reporting, data ops and labeling | Finance-confirmed terminated contract value | Contracts | Displacing external spend is the clearest ROI source [E-8.3] | Task-dependent | P1 (if outsourced) |
| W6 | **Decision-model factory**: retention uplift, demand forecasting, pricing | 95% lower bound of G6 experiment ITT margin uplift | Sealed holdout, randomized experiment | MLE-bench medal rate 61–64% [E-4.6]; risk ranking ≠ value ranking | Training L2; promotion always human | P2 |
| W7 | **Hypothesis factory** for open-ended exploration (§4.1) | Hypothesis confirmation rate | Falsification via W2 or G5 | Interactive ambiguous tasks only 8.67–17% [E-4.4]; outside the capability frontier, human accuracy fell from 84.5% to 60–70.6% [E-8.12] | L1; produces hypotheses, not conclusions | P1 |

**Not in year one:**
- LLMs as decision-makers in credit, insurance underwriting, employment or other consequential decisions;
- a home-built general NL-to-SQL engine;
- a notebook copilot;
- open-web scraping.

### 2.2 The value formula

```
NV_month = Σ_w κ · N_w^base · a_w · (t0_w − t_r,w) · c_h      (1) baseline demand diverted from the human queue
         + V_ext                                            (2) finance-confirmed terminated external contracts
         + Σ_m LCB95(ΔKPI_m) · u_m                           (3) only G6-passed experiments, at the 95% lower bound
         − Σ_w N_w · a_w · ε_w^UB · L_w                      (4) expected loss from delivered errors
         − (C_llm + C_compute + C_review + C_curation + C_platform_eng + C_build_amortized + C_vendor)

CPVC (cost per verified-correct task) = total cost / (N × audited correctness)
```

**Parameter rules:**
- **a:** audit-confirmed acceptance rate.
- **t0:** measured with a two-week time-and-motion study or an intake-bot timer, never self-reported.
- **κ:** 0.3 by default. It becomes 1 only after a cash event (contract termination, a withdrawn requisition, a measured backlog drop) is confirmed by Finance.
- **ε^UB:** the 95% upper bound from random audits.
- **L:** taken from the incident log.
- **Induced demand earns nothing** unless it attaches to a decision record. Ramp's agent channel drew 1,476 questions in four weeks, against 66 in the old help channel [E-3.13]; most of that is new demand.

### 2.3 Cost structure

**Bottom line:** at pilot scale, tokens are usually under 10–15% of total cost of ownership [assumption]. The dominant costs are context curation and human review, so the design target is *review minutes per accepted output*.

| Task | Tokens [assumption] | US mid-tier model | Main cost |
|---|---|---|---|
| Certified Q&A | 60k in (70% cache hits) + 3k out, comparable to Vercel d0's ≈61k [E-3.4] | Sonnet 5.5 ($2/$10 per M, cached reads at 0.05×) ≈ $0.07; ≈ $0.09 including the newer tokenizer's ≈30% inflation [E-8.16] | 5% random audit at ≈5 min each |
| DE break-fix PR | 0.5–3M | $1–5, capped with `--max-budget-usd` | 15–30 min human review, plus CI compute on a warehouse clone |
| One model-candidate round | 0.5–2M, plus training compute | $1–10 | 1–2 h data-scientist review |

**Three cost traps to plan for:**
1. **Promotions expire.**
   - Databricks Genie is free for *users* through 2027-01-31, but *service-principal* calls are billed now, and automated pipelines run as service principals [E-5.7].
   - Google's data agents cost ≈$3/$20 per million data tokens after the trial [E-5.12].
   - Snowflake AI Credits cost $2.00, or $2.20 with regional routing, so data residency costs about 10% more [E-5.3].

   Budget at post-promotion prices.
2. **Warehouse scan costs.** Agent-written exploratory SQL burns warehouse credits. The tool layer needs dry-run cost estimates, per-user and per-agent quotas, and partition-pruning lint.
3. **Agent multiplication.** Multi-agent runs use about 15× the tokens of chat, or roughly 4× a single agent [E-6.15]. Fan-out is off by default.

### 2.4 Break-even: when to build rather than buy [assumption]

**Inputs:**
- Accepted rate a = 0.5; net time saved Δt = 35 min.
- Loaded US rate $95/h: BLS median $120,230 ÷ 0.70 wage share ÷ 1,800 h [E-8.19].
- Variable cost per request ≈ $0.73: tokens, compute, 5% audit, and expected error loss at ε = 1%, L ≈ $20.
- First-domain fixed cost ≈ 1.75 FTE ≈ $25k/month: steward 0.5, eval 0.25, platform engineering 0.5, build amortization 0.5. This is the first domain's *share*; the full platform and eval team in §6.3 is spread across domains. With a single domain, raise N* by 40–70%.

| | Value per request (κ = 0.3 / 0.5) | First-domain N* (κ = 0.3 / 0.5) | Each later domain N* |
|---|---|---|---|
| US | $8.3 / $13.9 | ≈ 3,300 / 1,900 governable requests per month | ≈ 1,400 / 820 |
| Western Europe | Recompute with your local loaded rate. EU data roles generally cost less than US ones [assumption], which raises N* proportionally. | — | — |

**Rules:**
- **Below the first-domain N\*, do not build W2's data-access layer.** Use platform-native Q&A (Genie One/Agents, Snowflake CoWork, Looker Conversational Analytics, the Fabric data agent) and own only the golden set, the semantic layer and the ledger. Let W1 and W2 share platform engineering.
- **Hours saved alone is a thin case at low volume.** Combine domains, or add displaced external spend and decision value.
- **The US labor base helps.** At $95/h, a 35-minute analyst ticket costs ≈ $55. Agent cost per answer, including audit, is under $1, so the economics hinge on *volume, correctness and realization*, not on tokens.

`dsh roi --profile us` in the reference implementation is a portfolio calculator, and every input is tagged with its source or marked as an assumption. Its defaults differ from this section: it uses 2,000 hours, no κ, and no N*. The N* above is computed by hand.

### 2.4.1 Buy versus build: the four alternatives you will be pitched

The harness does not replace any of these. It sits on top of them, and it is the layer you own whatever you buy.

| Option | Typical products | What it gives you | What it does not | When buying it alone is enough |
|---|---|---|---|---|
| Platform-native agents | Databricks Genie Code/One, Snowflake CoWork/CoCo, BigQuery DE/DS agents, Looker Conversational Analytics, Fabric data agent | Q&A, SQL and dbt authoring, in-platform lineage | Cross-platform recomputation, reconciliation to independent records, scores on *your* golden set, a value ledger | Single platform and volume below N* |
| ML platforms | SageMaker, Vertex AI, Databricks ML, Dataiku, DataRobot | Training, registry, deployment, monitoring | A holdout sealed by a separate identity, experiment readouts and G6, a "risk ≠ value" decision loop | You already run a mature experimentation platform |
| BI copilots and notebooks | Power BI Copilot (Fabric), Looker CA, Hex agent ($36–$75 per editor + credits [E-5.18]) | Dashboard-level Q&A and authoring | Answer certificates, claim-vs-metric reconciliation, abstention evaluation | Questions are mostly about existing dashboard metrics |
| Consultancies / vendor forward-deployed engineers (FDE) | SI partners, vendor FDE programs | Speed | Knowledge retention, long-term evaluation. Gartner expects 70% of enterprises to abandon agentic AI built by vendor FDEs by 2028 [E-8.7] | One-off migrations or warehouse builds |

**Procurement acceptance checklist** (write it into the PoC and the contract):
1. Re-test on *your* golden set. Ignore vendor self-reported scores such as Genie Code 77.1% or Cortex Sense 83% [E-5.5, E-5.17].
2. Semantic definitions, verified queries, evals and audit logs must export to open formats: OSI YAML, SQL, JSONL.
3. MCP or open APIs, so answer certificates and gates can be attached.
4. Region pinning, ZDR and BAA terms where needed (§4.5).
5. Post-promotion pricing in writing (§2.3).
6. SOC 2 Type II and your vendor-risk review. For an FDE engagement: IP ownership, knowledge transfer and an exit plan [E-8.7].

### 2.5 Reference budget: six-person team, about $1.5M per year [assumption]

**People (≈ $1.2M at ≈ $200k fully loaded each; BLS-derived [E-8.19])**

| Headcount | Role |
|---|---|
| 2 | Platform/harness engineers: MCP, CI gates, sandboxes, identity, routing, FinOps |
| 1 | Eval owner: golden set, grader validation, benchmark, review canaries |
| 1 | Data scientist / statistician: W4, W6, causal sign-off |
| 2 | Analytics engineers: CODEOWNERs for W1, semantic modeling |

Business-funded stewards (0.25–0.5 FTE per domain) are a precondition for onboarding a domain.

**Non-labor (≈ $0.3M)**

| Item | Amount |
|---|---|
| Tokens and platform credits | $60k |
| Warehouse compute for agent queries and CI clones | $90k |
| Infrastructure (sandboxes, MLflow, observability) | $50k |
| Tooling | $20k |
| Compliance reviews and external red team | $40k |
| Contingency for price changes and expiring promotions | $40k |

### 2.6 Kill criteria (thresholds signed by the business owner before the pilot)

| Checkpoint | Trigger | Action |
|---|---|---|
| Intake | Power pre-check fails: the usable population cannot reach value power 0.8 within ≤3 months, or unit economics are negative even at the theoretical optimum | Kill now, not at month 6 |
| D+14 | No baseline (no ticket logs, no intake data) | Don't start; deploy an intake bot for 2–4 weeks |
| D+45 | W2 certified-set pass@1 < 75% and out-of-scope refusal < 80% | Shrink to ≤20 metrics, or stop |
| D+45 | W1 replay of 20 historical incidents: correct triage < 40% | Triage only, no code |
| D+90 | W2: held-out pass^3 < 75%; audited silent-error 95% upper bound > 5% (n ≥ 200); WAU < 20%; or self-serve resolution up < 10 points | Back to L1, or kill |
| D+90 | W1: merged-without-rework < 40%; 30-day rollback > 10%; or any agent-caused Sev-1 | Triage only |
| D+90 | CPVC above 30% of the human unit cost for two months; or HOLD rate > 20% with bypasses | Re-route or re-scope gates, else stop |
| Any time | An agent-caused external or regulatory number error that slipped past the gate | Stop the workflow, run a postmortem, downgrade one level |
| Any time | Steward seat vacant > 60 days, or correction PRs backlogged > 2 weeks | Freeze the domain |
| M+6 | Platform-substitution test: native agents within 3 points of this design and cheaper | Stop building the tool surface; keep assets, evals and ledger |
| W6 | No randomization authority within 6 months, or experiment LCB95 ≤ 0 | Don't start, or don't scale. An honest null result is a valid deliverable |

### 2.7 A worked example: where decision value actually comes from

In the dsharness synthetic world [measured, using custody truth], 3,584 active customers form July's risk set, with an average true churn probability of 4.9%:

| Segment | Customers | Response to retention outreach |
|---|---|---|
| Persuadable | 1,130 | Responds |
| Unmoved | 2,064 | No response |
| Sleeping dog | 390 | More likely to churn when contacted |

Assume 500 contacts per month. The table shows monthly expected net value; the synthetic company bills in CNY, and USD at ¥7.1/$ is in parentheses.

| Channel and cost (discount paid only by retained customers) | Top 500 by **true** churn probability (oracle) | Top 500 by **true** uplift (oracle) | Top 500 by true expected value (oracle) | Experiment power at 250 per arm (value LCB95 > 0 / "churn went down" ITT) |
|---|---|---|---|---|
| Phone call: ¥15 + 20% discount | **−¥8,199** (−$1,155) | −¥5,844 | −¥5,722 | — |
| App message: ¥0.5 + 10% discount | ¥1,602 ($226) | **¥3,999** ($563) | ¥4,003 | risk 0.06 / 0.23; uplift 0.37 / 0.87 |

This table ranks by *true* values: it is the ceiling you reach if you know the answer. Ranking with the *learned* churn model is truly worth −¥1.69 per contact (§9.3).

**Three lessons:**
1. **Channel economics decides whether to act at all.** At a fixed 500 calls per month, calling loses money under any targeting. Even a perfect model finds only 46 customers worth a call, worth ≈ +¥764 per month. An honest system blocks this instead of crediting "a few fewer churners".
2. **Targeting decides how much you earn.** With the true uplift known, uplift targeting is worth ≈ 2.5× risk targeting.
   - Risk targeting mostly picks *unmoved* customers: 316 of the oracle's top 500, and 346 of the learned model's list. Sleeping dogs are rare: 0 and 13. This is Ascarza's "retention futility" [E-1.23].
   - Uplift has to be *learned* from limited experimental data. How much you can learn depends on what you collect, and on scale (§9.5).
3. **Better targeting makes experiments more decisive, but the bar is high.** At 250 per arm, ITT power rises from 0.23 to 0.87, but the gate's after-cost value test only rises from 0.06 to 0.37.
   - 80% value power needs ≈ 743 per arm for a true-uplift list, and ≈ 9,900 for a risk list.
   - One-month tests are usually INCONCLUSIVE (§5.6).

The money is reachable only through the full loop: randomized program → learned uplift → pre-registered experiment → PASS → scale. A high-AUC churn model does not get you there. Scaled linearly to one million subscribers, the stakes are hundreds of thousands of dollars per year [assumption].

---

## 3. End-to-end architecture

### 3.1 Layers and data flow

```
Entry (reuse)      Slack / Teams · BI · IDE/CLI · ticketing · intake bot
   │ SSO (Okta / Entra ID) → MCP Enterprise-Managed Authorization / OAuth 2.1 → on-behalf-of to warehouse RLS
Executors (swap)   platform-native: Genie Code / Genie One | Snowflake CoCo / CoWork | BigQuery DE/DS agents | Fabric data agent
                   self-run:        Claude Code / Agent SDK (claude -p --bare) | Codex (codex exec) | Gemini CLI | OpenHands
   │ AGENTS.md + Skills + dsh-mcp (≤10 tools per role) + hooks + budget caps
┌─ DS Harness thin layer (built, assets-as-code) ──────────────────────────────────────────┐
│ ctx repo (semantics / certified tables / verified queries / skills) │ dsh-checks │ dsh-eval │
│ dsh-gate (CI policy step) │ answer certificates & trust labels │ back-scorer │ dsh-value ledger │
│ scan-cost guard │ routing policy YAML (enforced by your gateway)                            │
└───┬ pushed-down SQL ─────────────┬ PR / CI ─────────────┬ traces / cost / lineage ─────────┘
Data platform (reuse)  ingest (Fivetran / dlt / CDC) → warehouse/lakehouse (Snowflake | Databricks | BigQuery | Fabric;
                       Iceberg REST across engines) → transform (dbt / SQLMesh) ← existing orchestrator (Airflow / Dagster)
                       → semantic layer → catalog (Unity / Horizon / Purview / Knowledge Catalog) + OpenLineage → MLflow → experimentation
Sandboxes (reuse)  managed: E2B, Modal, Cloudflare, Vercel | self-hosted: kubernetes-sigs/agent-sandbox (gVisor/Kata), E2B
Gateways (reuse)   agentgateway | MLflow AI Gateway | Snowflake Cortex AI Gateway (preview) — we only write routing policy
```

### 3.2 The lifecycle, stage by stage: what the agent does, how it is verified, what is reused, what is built

| Stage | Agent work (autonomy level) | Verification | Reuse | Build |
|---|---|---|---|---|
| **Intake** | Drafts a decision record: the decision to change, options, owner, baseline, expected effect, channel unit economics (cost per touch vs. the most one customer can earn you). Runs power and MDE on historical data (L0) | Negative unit economics even at the theoretical optimum → no project. Cannot reach power 0.8 within ≤3 months → rule-based policy plus long experiment, or no project. No randomization authority → only W1–W5 | Your experimentation platform's power calculator | Decision-record template, power pre-check (`decision/design.py` in the reference) |
| **Collection** | Writes Fivetran/dlt sources, contract drafts and tracking specs, all as PRs (L2) | Source-level control totals (row counts, amounts); PII scan; schema version registry, where an unknown version blocks downstream | Fivetran, dlt, CDC [E-7.23] | Contract templates, control-total checks |
| **Processing** | Writes dbt/SQL change PRs (L2 → L3 non-prod) | Tests, contracts, invariants (declared cardinality, conservation, dedup on event identity), data-diff, exact reconciliation, oracle independence | dbt-core 1.12 (move to 1.13 once it is GA) or dbt-oss 2.x, SQLMesh, GX Core [E-7.2–7.6] | Invariant library, reconciliation framework (with "explained differences") |
| **Semantics and context** | Drafts docs and semantic-model PRs (L2) | Definition digests, steward review, regression evals | Platform semantic objects (Snowflake semantic views, UC metric views, LookML, Power BI models), MetricFlow (Apache-2.0) [E-7.4], Cube [E-7.14] | ctx repo, OSI sync, nightly enrichment |
| **Features** | Writes point-in-time (PIT) feature SQL (L2) | `available_at < as_of`; **time-travel check**: the value computed on the snapshot from the day before `as_of` must equal the value computed on a later snapshot; static temporal-tag check | Warehouse time travel, Iceberg 1.12, Delta; a feature store only if you need online serving | PIT macros, leakage checks |
| **Training** | Trains candidates on train/dev only, in a sandbox (L2) | Fixed search budget and seeds; sklearn-style pipelines fitted inside folds | sklearn, LightGBM, Optuna, EconML/causalml, MLflow 3.x | Declarative training templates |
| **Evaluation and testing** | Cannot touch the holdout | Sealed scorer (separate identity, budgeted), slices, calibration, Qini/policy value | Inspect AI, MLflow GenAI eval | Scorers, eval suites, fault-injection packs |
| **Delivery** | Submits answer certificates, PRs, release requests | Gates (§5.4) | GitHub protected environments, `actions/attest`, MLflow aliases | Gate policy step, release manifest |
| **Value** | Drafts experiment designs and analyses; never signs off value (L0) | Pre-registration, SRM, ITT, Finance sign-off | Your experimentation platform. Do not depend on BigQuery `AI.CAUSAL_EFFECT`, which is still in Preview [E-5.11] | Value ledger, A/B readout skill |
| **Monitoring** | Triages alerts and does root-cause analysis (L1); fixes arrive as PRs | Drift, SLAs, nightly golden-set canary, back-scoring | MLflow tracing, Langfuse (self-hosted), OpenLineage | Back-scorer, canaries |

**Collection and labeling rules**

- **Source contracts** declare:
  - primary keys, update timestamps, late-arrival windows and look-back overlap;
  - delete and tombstone semantics, and the write mode.

  Data is published READY only after source-level control totals reconcile. A new schema is registered as a candidate revision and blocks incompatible downstream models (dsharness's D8).
- **Event tracking** uses a YAML tracking plan. Event volumes are reconciled daily against orders and payments in the backend. Agents may propose tracking changes only as PRs.
- **Unstructured data** (contracts, ticket text, call transcripts): LLM extraction is a pipeline operator and is versioned like code.
  - The model ID, prompt and output schema enter the `pipeline_digest`; any change to them is a pipeline change.
  - Each output row stores `extracted_at` and `extractor_digest`. Point-in-time availability is `max(source available_at, extracted_at)`.
  - Re-extracting history with a new model is a **backfill**: a plan PR, a dry-run diff and two-person approval, never automatic.
  - A new version merges only if field-level precision and recall do not drop on a 200-item human gold set. Fields below threshold are T3 only.
  - Text that contains personal data is routed as C2 (§4.5).
- **External data** (data brokers, partners, third-party enrichment):
  - Before onboarding, register provenance, licensed use and term, whether the data may be used for training, and any cross-border limits. Licensed-use fields go into the catalog, and the tool layer refuses queries outside them.
  - Vendors revise history, so snapshot by delivery date. PIT features use only what had been delivered.
  - Before renewal, run an incremental-value test on the same sealed holdout or experiment; no lift, no renewal.
  - For US consumer data used in eligibility decisions, check whether the vendor is a consumer reporting agency (FCRA) before using it [general knowledge — confirm with counsel].
- **Labels** have four tiers: confirmed, expert_adjudicated, weak, model_generated. There are two human queues:
  - **Random or stratified audits** estimate population error rates and record sampling probabilities.
  - **Error-discovery queues** hunt for errors but never estimate rates.

  Gold labels are blind-labeled and adjudicated, with kappa ≥ 0.8.
- **Synthetic data** is for tests and benchmarks only. It is tagged as synthetic and never enters eval truth or value accounting.

### 3.3 What we build, and what we explicitly do not

| Built | Content | Based on | Serves |
|---|---|---|---|
| ctx | OSI/MetricFlow-style YAML, verified queries (runtime set R and eval set E kept physically separate), table cards, glossary, skills, AGENTS.md, routing policy | MetricFlow, dbt-mcp, sqlglot | Correctness, abstention |
| dsh-mcp | Governed data entry point (§4.2) | MCP 2026-07-28, Python SDK v2 / FastMCP 4 [E-7.20] | Access control, silent errors |
| dsh-checks | Reconciliation, invariants, PIT, cardinality, data-diff, price-book validity, experiment quality; fault-injection operators | Pandera, GX Core, dbt tests | Catch rate, false-block rate |
| dsh-eval | Golden sets, result-set equivalence grading (with grader validation), pass^k, harness adapters, review canaries | Inspect AI [E-7.21], MLflow 3.x | Regression, CPVC |
| dsh-gate | CI policy step with three verdicts that emits in-toto Statements | `actions/attest`, cosign | Escaped defects |
| dsh-value | Value, cost and incident ledger (≈20–30 dbt models in the warehouse) | Warehouse, MLflow trace costs | NV, CPVC |

Built code is capped at ≤10k lines, with a hard ceiling of 15k [assumption].

**Explicitly not built:** a chat UI, an NL-to-SQL engine, an orchestrator, notebooks, a catalog, a model registry, a sandbox runtime, an LLM gateway, a vector or memory store, an evidence database, a multi-agent framework [E-5.22]. State lives in git, warehouse tables and MLflow.

### 3.4 Deployment profiles

**SMB / single team**
- **Stack:** uv + Python 3.12 → Fivetran or dlt → DuckDB 1.5.x (not 2.0 yet) + DuckLake 1.0, *or* a small Snowflake or BigQuery account → dbt-core 1.12 → Pandera. Then cron or CI, MLflow, MetricFlow or Cube, and dsh-mcp over stdio.
- **Sandbox:** sandbox-runtime with `denyRead` on `~/.ssh` and cloud credentials, or Docker with `network=none`.
- **Pitfalls:**
  - `pip install dbt` now installs the proprietary Fusion binary; use `dbt-core` or `dbt-oss` [E-7.2].
  - Turn off Cube dev mode, which bypasses authentication [E-7.14].

**Enterprise with an existing warehouse**
- The warehouse is the system of record. Queries are pushed down and run on behalf of the user under row-level security.
- Iceberg 1.12 with a REST catalog for cross-engine access.
- Keep your existing Airflow or Dagster.
- Send OpenLineage events to your catalog; avoid the stale Marquez [E-7.22].
- Turn on MLflow RBAC.
- Run sandboxes on kubernetes-sigs/agent-sandbox with gVisor or Kata, or use a managed service: E2B, Modal, Cloudflare or Vercel [E-6.21, E-6.22].

**Single-platform enterprise**

If native agents plus native MCP already cover Q&A and coding, **do not** build dsh-mcp's data-access part. Genie MCP and the BigQuery MCP server are GA; dbt's remote MCP server had no GA label as of this writing [E-5.6, E-5.10, E-5.15]. Build only ctx, eval, gate and the ledger, and run the platform-substitution test at month 6.

**Multi-platform enterprise** (for example Snowflake plus Databricks, or a Fabric BI estate plus a Databricks ML estate)

Build a thin dsh-mcp for verification, evidence, cross-platform recomputation and answer certificates. Leave in-platform Q&A and coding to each platform's native agent, because vendor agents work mainly over their own platform's assets and semantics: the BigQuery DE agent only targets BigQuery, and the Fabric data agent caps out at five sources [E-5.10, E-5.13].

**Real-time ML**

v1 is batch only. Real-time models (fraud, pricing, recommendations) get a G-online gate:
- online/offline feature parity;
- freshness SLAs;
- at least two weeks of shadow traffic;
- canary rollout with automatic rollback on SLO breach.

### 3.5 Supply chain and licensing

**License gate.** SBOM plus SPDX flags these licenses:

| License | Example | Effect |
|---|---|---|
| ELv2 | Soda Core v4 | Usually fine internally |
| BSL | lakeFS ≥ 1.87 | Internal production use only for *unmodified* versions |
| PLA | dbt Fusion binary | Usually fine internally |
| AGPL | — | Usually fine internally |
| Proprietary | — | — |

All of them block hosting or reselling to customers [E-7.2, E-7.7, E-7.8].

**Avoid:**
- Vanna [E-7.16] and Daytona OSS [E-6.21], both archived.
- smolagents and AutoGen, stalled, and CrewAI on core paths because of churn [E-7.19].
- Marquez, stale [E-7.22].

Polars 2.0, DuckDB 2.0 and dbt v2 stay out of production until they pass regression on your own data [E-7.2, E-7.10, E-7.11].

**Agent extensions are supply chain too.** Skills contain executable scripts, and MCP servers and hooks run with the agent's privileges. So:
- Keep an internal allowlist registry, such as the MLflow MCP Registry or agentgateway policy. The official MCP Registry is still in preview [E-6.25].
- Pin by digest and sign after review.
- Red-team quarterly for tool poisoning and description injection.

---

## 4. Agent design

### 4.1 Topology: one writer, read-only subagents, a deterministic skeleton

This is where the 2026 practice has converged:
- Cognition: "writes stay single-threaded" [E-6.16].
- Anthropic: multi-agent setups pay off for breadth-first reading, cost about 15× chat tokens (single agents about 4×), and are a poor fit for tightly coupled coding [E-6.15].
- Fan-out plans belong in a script or a DAG [E-6.17].

| Workflow | Shape | Writers | Skeleton |
|---|---|---|---|
| W1 break-fix | A failed build triggers a read-only triage subagent (logs, contract diff, lineage; returns a 1–2k-token summary) → one writer patches on a branch → CI → clean-context reviewer (executable probes only) → CODEOWNER merges | 1 | CI / DAG |
| W2 Q&A | Single agent, ≤10 tools; may clarify or abstain; for hard SQL, N candidates selected by execution | Certificates only | — |
| Enrichment | Nightly script fans out read-only agents; JSON-schema-validated outputs and explicit token budgets; stewards review the PRs | Script | Orchestrator |
| W4 readout | Skill plus deterministic statistics; the agent orchestrates and writes the narrative | 0 | — |
| W6 factory | Deterministic DAG in your orchestrator; the agent writes features and candidate *specs*; the builder never sees the holdout; the scorer runs under its own identity | 1 | DAG |

**Banned:**
- persona teams and parallel writers;
- supervisor architectures (Databricks deprecated its Supervisor API [E-5.8]);
- Claude Code agent teams, still experimental [E-6.9].

Use A2A only at organizational or vendor boundaries [E-6.5].

**W7 becomes a "hypothesis factory" (L1).** Its products are falsifiable hypotheses, not conclusions.
- The agent explores freely in a sandbox with `run_sql` and Python; every intermediate result is T3.
- It outputs at most five hypotheses. Each must carry a falsifier: either a governed-metric query that `submit_answer` can reconcile (which can reach T1/T2), or a draft experiment pre-registration (which goes to G5).
- Hypotheses without a falsifier are not reported.
- The data-science owner picks which hypotheses to test. The ledger tracks the confirmation rate; below 20% [assumption], tighten the exploration budget for that class of question.

This uses the agents' strongest capability, exploration, without letting exploration bypass the evidence ladder.

### 4.2 The dsh-mcp tool surface (≤10 per role; the reference ships 15 in total, with read-only context tools shared)

Fewer tools work better. OpenAI's data agent went from about 40 tools to 13 non-overlapping ones [E-3.3], and tool-selection accuracy degrades past 30–50 tools [E-6.14]. The reference surface:

| Role | Tools |
|---|---|
| analyst (7) | `list_metrics` `describe_metric` `query_metric` `search_catalog` `describe_table` `run_sql` `submit_answer` |
| data_scientist (10) | `list_metrics` `query_metric` `search_catalog` `describe_table` `run_sql` + `list_features` `propose_feature` `train_candidate` `request_final_evaluation` `request_release` |
| data_engineer (6) | `search_catalog` `describe_table` `run_sql` + `pipeline_status` `read_pipeline_file` `propose_pipeline_change` |

**Design rules** (all implemented in the reference):

1. **Governed metrics come first.**
   - `query_metric` goes through the semantic layer and returns the compiled SQL and a `semantic_digest`.
   - `run_sql` is for exploration only: a single SELECT, with an sqlglot allowlist and per-scope table resolution. It refuses bronze tables, PII columns, file functions, system catalogs, ATTACH, COPY and SET. Results are labeled uncertified.
2. **Answers must declare their semantic claim, and the claim must be the question.**
   - `submit_answer` requires a `metric_call`: which metric, dimensions, months and filters.
   - The harness re-reconciles the rows against the governed metric. That is how plausible-but-wrong SQL answers get caught.
   - Since the adversarial review (§9.6), the harness also checks that the declared claim matches the *question* on metric, months, filters and breakdown. A mismatch goes to human review.
   - An answer that declares no claim gets T3 at best.
3. **Read-only, locked connections.** DuckDB runs with `enable_external_access=false` and `lock_configuration=true`. In production: read-only roles, RLS and warehouse quotas.
4. **Custody is unreachable.** No tool reads ground truth, sealed labels or the world generator. Pipeline builds also run with external access off, so even an exploit that slips past the static screen cannot read files.
5. **Tools never raise raw exceptions to agents.** They return `{error, kind}`. Every call is audited, and budget exhaustion is explicit; it never counts as "done".

**Protocol:** MCP revision 2026-07-28. It is stateless, `tools/list` order is deterministic, long jobs use the Tasks extension, and OTel traceparent travels in `_meta` [E-6.1].

**Auth:** OAuth 2.1 with RFC 9728/8707 and audience-bound tokens; no token pass-through [E-6.2]. Enterprise-Managed Authorization is stable, with Okta as the first IdP and Claude, Claude Code and VS Code as clients [E-6.3]. If you run Entra ID or another IdP without EMA support, do the token exchange at the gateway.

**Differential-inference controls:**
- Tables with personal data are reachable only through fixed-dimension certified metrics.
- Small cells are suppressed (k ≥ 10 [assumption]).
- Query overlap is audited per principal.

### 4.3 Skills, AGENTS.md and a domain function library

Agent Skills (SKILL.md) are the de-facto cross-vendor packaging format. Claude Code, ChatGPT/Codex, Gemini CLI, GitHub Copilot, VS Code, Cursor, Genie Code and Cortex Code all support them [E-6.6]. The reference ships four:

| Skill | Gist |
|---|---|
| `analytics-answer` | Semantic layer first. Declare the `metric_call` that matches the question exactly. Mismatches go to review. Exploratory results are uncertified. |
| `churn-modeling` | Trusted or leakage-checked features only. Never touch the holdout. Value only through experiment readouts. |
| `pipeline-breakfix` | Fixes must be backward compatible. Never change tests, reconciliations or gates in the same PR. Never calibrate to the independent record. Registered incidents only. |
| `retention-uplift` | Learn uplift from randomized programs (ITT, never per-protocol). Validate offline. Compute power before running. Read out per policy with channel costs. Scale only on a PASS. |

**Add in production:** `schema-evolution`, `backfill-runbook` (plan plus dry-run diff only), `reconcile-before-claim`, `pit-features`, `experiment-design`, `ab-readout` (INCONCLUSIVE when underpowered), `model-card` and `dpia-draft`.

Each skill has a named owner, a version and at least 10 eval tasks, and changes must pass regression.

**AGENTS.md** holds repository conventions. Claude Code, Codex and Gemini CLI all read it [E-6.7].

**Domain function library.** A strong model distills unit-tested functions offline against dev labels; a cheap model orchestrates them at runtime. That is how the DABstep leader scored 89.95 with Haiku at runtime, against a Claude Code + Opus baseline of 66.93 [E-4.5]. Hold out data when distilling.

### 4.4 The context layer

**Trust order:**
1. Semantic definitions.
2. Certified tables.
3. Verified queries (dashboard and golden queries ahead of ad hoc ones).
4. Nightly-enriched table cards.
5. Glossary and business rules, with clarification turns.
6. Incident and caveat log.
7. Personalization.

**Rules:**
- **Keep the R and E sets apart.** A verified query is *either* runtime context *or* eval truth, never both. Snowflake's evaluation feature works this way [E-3.19]. The reference has 14 context items and 6 eval items, and a test enforces that they are disjoint.
- **Don't index all query history.** Most queries are one-offs [E-3.3], and SQL "reference cards" alone add little [E-4.12].
- **Serve semantics as fetchable files**, not as a 24k-character prompt [E-3.18].
- **User corrections arrive as PRs** to the ctx repo, and stewards review them within two business days. Avoid opaque global memory.
- **Lineage-driven invalidation.** When an upstream `pipeline_digest` or schema changes, dependent verified queries, table cards and skills are marked "needs re-verification" and removed from the runtime set. This is the main source of context rot.
- **Personalization.** Default to tables the user touched in the last 90 days; at Meta, 88% of queries use only such tables [E-3.10]. This processes employee activity data, so give notice, limit the purpose, and keep a 90-day retention window. In the EU, check this with your DPO and works council.
- **Synonyms and phrasing are part of the semantic layer.** Register business synonyms and colloquial names for every certified metric ("bookings", "ARR", "MRR", "net rev"). At least half of the golden set should use your business users' own wording. The reference parses only a fixed English vocabulary; a production deployment should generate the parser from the semantic YAML.

### 4.5 Model routing and data residency

| Data class | What may enter a prompt | US | EU | Multinationals with China operations |
|---|---|---|---|---|
| C0: metadata, schema, code, synthetic data | Everything | Any approved model | Any approved model | Metadata from important-data catalogs stays in China |
| C1: internal aggregates (small cells suppressed) | Aggregates | Endpoints with no-training terms | EU-region endpoints | In-country models preferred |
| C2: row-level personal data | **Nothing.** Processed only by code in the sandbox | ZDR endpoints. **PHI only through BAA-covered services** | EU region + ZDR + DPIA. Do not rely on the DPF alone, because C-703/25 P is pending [E-2.8] | In-country self-hosted or filed domestic models. Personal data in a prompt to an offshore API counts as an export [E-2.13] |
| C3: sensitive or special-category data, consumer credit, data for consequential decisions | Schema only; access needs human approval | Self-hosted or dedicated deployment | Self-hosted, or ZDR + DPIA | Self-hosted open-weight models only |

- **Code next to the data; raw rows stay in the sandbox.** Programmatic tool calling cut tokens by 37%, and code execution with MCP cut 98.7% in Anthropic's example [E-6.14].
  - Claude Managed Agents and programmatic tool calling are **not** ZDR-eligible, and Managed Agents are not BAA-eligible.
  - For C2 and above, run your own loop [E-6.10].
- **Model tiers:**

  | Tier | Models | Use |
  |---|---|---|
  | Cheap | Haiku 5.5, gpt-6-luna and similar ($0.10/$0.50 per M) | Extraction, classification, SQL drafts; aim for ≥60% of calls [assumption] |
  | Default | Sonnet 5.5 ($2/$10), gpt-6.1-sol ($2/$10) | Planning and coding |
  | Frontier | Opus 5.5 ($4/$20), Fable 5.1 ($10/$50) | Offline distillation and hard repairs [E-8.16] |

  Routing serves compliance and cost, not "smartness". OpenAI's own data agent uses one model and no router [E-3.3].
- **Vendors.**
  - Keep at least two approved models per tier, from at least two providers, plus one self-hostable open-weight option.
  - Set repricing alerts.
  - Whether Chinese-origin open-weight models (Qwen, DeepSeek, GLM) are allowed is an acceptable-use and supply-chain policy decision. Many US enterprises and public-sector contractors restrict them [general knowledge — check your policy]. If you do allow them, self-host them.

### 4.6 Swappable harness: the contract lives in the assets, not the framework

**The swappable contract** is: MCP tools, SKILL.md, AGENTS.md, the eval suite and the answer-certificate schema. A self-run harness needs only an adapter of the form `run(task, budget) → (transcript, artifacts, cost)`.

**Executors:**
- `claude -p --bare`. In CI, `--bare` is mandatory; otherwise a repository's hooks and `.mcp.json` run without a trust dialog [E-6.8].
- `codex exec`, with an OS-level sandbox and network off by default [E-6.12].
- Gemini CLI, OpenHands, the OpenAI Agents SDK, ADK, PydanticAI.

**Pin versions exactly.**
- Claude Code ships very frequently; it reached v2.1.303 on 2026-10-18 [E-6.8].
- The OpenAI Agents SDK is still 0.x [E-6.11].
- A third-party report says ADK 1.x and 2.x storage formats are incompatible [E-6.13].

**Closed platform agents** (Genie Code, CoCo, Fabric) cannot run our hooks. Four controls compensate:
1. black-box evals on the same golden set;
2. data-side controls: read-only roles, RLS, quotas;
3. output-side gates: answer certificates and PR CI;
4. conservative approval settings in the platform itself.

**Cadence:**
- Any pin change (model, harness, skill, context or semantics) triggers regression.
- A full cross-harness comparison runs quarterly on at least two executors.
- After every major model upgrade, run a "strip the scaffolding" ablation: every harness component encodes an assumption that goes stale [E-6.18].

### 4.7 Autonomy is earned, not configured

| Level | Meaning |
|---|---|
| L0 | Plans and suggestions only |
| L1 | Read-only execution and answers, labeled |
| L2 | Writes to a sandbox, branch or dev environment; opens PRs or release requests; a human merges |
| L3 | Auto-promotes to non-production or shared artifacts once all gates pass; audited afterwards |
| L4 | Bounded, reversible actions in production |

| Workflow | Start | 12-month cap | Promotion rule [assumption] |
|---|---|---|---|
| Certified answer to the asker | L1 | L1 | — |
| Certified answer to shared dashboards or reports | L2 | L3 | n ≥ 150 random audits with 0 errors (upper bound ≈ 2%) and 0 escapes |
| Board, external, regulatory or SOX-scoped numbers | Human | Human | **Never:** T0 plus a named sign-off |
| DE break-fix (no contract changes) | L2 | L3 (non-prod auto-merge) | 50 consecutive PRs merged without rework, 0 escapes, reviewer override rate < 5%, regression pass^3 ≥ 90% |
| Re-running failed jobs | L2 | L4 (within quota, idempotent) | Idempotency proven and 20 clean runs |
| Backfills, contract or definition changes, model promotion, customer-facing actions | L2 / human | Unchanged | **Never automatic** |

**Demotion:** any escape drops the workflow one level.

**Basis:**
- Departments that approve every action report 16% transformational success, against 31% for full use [E-8.8]. This is correlational.
- IMDA's "significant checkpoints" [E-2.22].

### 4.8 Deterministic guardrails

- **PreToolUse hooks:**
  - deny DDL/DML against prod;
  - deny reads of the custody area, the holdout and the world generator;
  - deny PRs that mix fixes with test, contract or gate changes;
  - treat budget overrun as FAILED.
- **Sandbox:** network off or allowlisted, read-only input mounts, and short-lived credentials kept on the harness side.
- **Treat data content as untrusted.** Instructions inside cells, column names or ticket text never change permissions.
- **When verifiers or gates are down:** writes, promotions and external numbers fail closed (HOLD). Read-only exploration fails open but is labeled "unverified".

---

## 5. Trust and verification without a complete ground truth

### 5.1 Oracle catalog: what each kind of evidence proves, and what it does not

| Oracle | Proves | Does not prove | Reference implementation |
|---|---|---|---|
| **O1 Reconciliation to independent records**: GL close, processor settlements, source control totals | Processing is correct | That the source recorded reality correctly; not available before close | `reconcile:revenue_matches_finance_close`, exact to the cent per month and region |
| **O2 Invariants and metamorphic relations**: conservation, m:1 joins add no rows, dedup on event identity, PIT, price-book validity | A class of errors is absent | That the definition is right | `schema.yml` tests, `verify/leakage.py` |
| **O3 Dual-path recomputation**: semantic-layer SQL vs agent SQL, or a second engine | Implementation errors | Both paths sharing a wrong definition | `submit_answer` re-reconciles the declared `metric_call` |
| **O4 Golden sets / verified queries (E set)** | Capability on known questions | Out-of-distribution questions | eval split of `verified_queries.yaml` |
| **O5 Delayed-truth back-scoring**: answers archived with their snapshot and re-scored at close or label maturity | Real-world accuracy, with no labeling cost | Questions that never get a delayed truth | Sealed evaluation on matured June churn labels |
| **O6 Random audits with known sampling probabilities** | Unbiased error-rate estimates | — (discovery queues must not be used for rates) | — |
| **O7 Randomized experiments** | Causal effect | Effect outside the experiment population | `decision/experiment.py` + one-shot oracle |
| **O8 Fault injection and review canaries** | Recall of the checks themselves | Real-world accuracy | World defects D1–D9 + naive bench variants |

**Rules:**
1. **Oracle independence.** An independent record must never be upstream of the metric it checks. A "fix" that calibrates revenue to the finance table would otherwise pass reconciliation by construction. The reference enforces this with `pipeline.oracle_independence` and plants this trap in the benchmark.
2. **Unclosed periods cap at T1**, labeled "provisional, back-scored after close".
3. **Non-financial metrics** need a source-system control total as their O1. Without one, they cap at T1/T2 and rely on random audits.
4. **Causal claims require randomization.** Quasi-experimental results are labeled ASSUMPTION-DEPENDENT and carry refutation and sensitivity checks (DoWhy).
5. **Audit gold labels.** Public answer keys have 53–63% errors in some subsets (BIRD Mini-Dev 52.8%, Spider 2.0-Snow 62.8%) [E-4.2, E-4.18]; your golden set will have errors too. Use an LLM auditor plus two-person review, with kappa ≥ 0.8.
6. **Validate the grader.** Execution matching produces false positives and false negatives [E-4.14]. Hand-review 100 grades each quarter, and allow the grader into gates only if its error rate is ≤ 2% [assumption].

### 5.2 One evidence ladder, one set of user labels

**Internal ladder (per artifact)**

| Level | Content |
|---|---|
| V0 | It runs (not evidence) |
| V1 | Contracts, types, tests; SQL static checks; the answer reproduces on re-execution |
| V2 | Invariants, PIT, metamorphic relations |
| V3-D | Independent-path recomputation agrees |
| V3-R | Exact reconciliation to an independent record; strictly stronger than V3-D |
| V4 | Sealed holdout, matured labels or back-scoring, by a separately-identified scorer |
| V5 | Pre-registered randomized experiment (ITT, SRM, power) |

**User-facing trust labels**

| Label | Condition | Allowed use |
|---|---|---|
| **T0 reconciled** | V3-R: closed period, exact match to the independent record or all differences explained | External, regulatory, board and SOX-scoped use, with a named sign-off |
| **T1 governed, provisional** | Certified-metric path, READY build, V2, configuration evals passing | Internal KPIs and dashboards, labeled "provisional" and back-scored |
| **T2 cross-checked** | Non-certified question, V3-D plus V2 | Internal decision support |
| **T3 exploratory** | V1 only, with assumptions listed | Exploration only; never in KPIs or reports |

Model and causal claims use V4/V5 with a conclusion status: VERIFIED, INCONCLUSIVE or NOT_VERIFIED_CAUSAL.

### 5.3 Evals are the harness's gate, not an afterthought

**Sources.**
- Draw from real logs, tickets and intake data. Start with 20–50 real failures [E-6.20].
- Grow to 150–200 items by day 90, 20% of them out-of-scope or ambiguous, and to 300–500 by month 6.
- Write 2–4 paraphrases per item and allow multiple correct answers. About 60% of LinkedIn's questions have more than one [E-3.7].

**Grading.**
- Compare executed result sets, never SQL strings.
- Grade components separately: intent, table choice, executes, correct. This is Uber's decoupled mode [E-3.5].

**Report:**
- pass@1 and pass^3. A 75% single-run success becomes about 42% pass^3 [E-6.20].
- Silent-error rate: wrong, without abstaining or downgrading the label.
- Abstention precision and recall.
- CPVC, p95 latency, and human touches.

**Sample-size discipline.**
- With 0 errors, the 95% upper bound is about 3/n: 2.0% at n = 150 and 1.0% at n = 300.
- 200 items near 85% give a CI of about ±4.9 points.
- Distinguishing a silent-error rate of 10% from 5% needs about 430 independent items per arm, unpaired.
- Use paired McNemar tests for regressions.

**Versioning.**
- Bind each item to a `metric_definition_digest`; when a definition changes, its items become stale.
- Keep the R and E sets physically separate, with hash checks in CI.

**Never accept vendor self-reported scores**, such as Genie Code 77.1% or Cortex Sense 83% [E-5.5, E-5.17]. Re-test them on your own set.

### 5.4 Gates

| Stage | Required evidence | Approvals |
|---|---|---|
| `project_intake` (recommended; not implemented in the reference) | Decision record, baseline, power pre-check (power ≥ 0.8 reachable within ≤3 months), positive unit economics at the theoretical optimum, randomization authority | Business owner + DS/statistics owner |
| `answer_publish` | SQL static check, re-execution match, reconciliation to the governed metric; **the claim must match the question** (metric, months, filters, breakdown), otherwise human review | — |
| `pipeline_merge` | Clone build with tests (including reconciliation); regression diff identical on the served snapshot **and** on every table the governed pipeline can still build on the new export; **oracle independence**; detection of weakened tests; locked reference data | CODEOWNER, enforced by branch protection outside the harness. A gate ALLOW means "CI evidence complete", not "permission to merge" |
| `feature_accept` | Time-travel check + static temporal check | — |
| `model_shadow` | Sealed evaluation + candidate-level leakage evidence | — |
| `model_experiment` (G5) | Applies to a **policy**: its models are cleared (`policy.models`) and an offline Qini and value validation on the historical randomized program passes (`policy.offline_qini`). Pre-registration and power are done in the design step | Business owner |
| `model_scale` (G6) | As above + the policy's own experiment readout PASS (after-cost LCB95 > 0) + SRM PASS | Business owner + risk |
| `retrain_promote` | Sealed evaluation + candidate leakage evidence + champion/challenger comparison | Risk |

**Verdict semantics** (reference `evidence/gate.py`, 64 tests):
- Only records that match the subject digest, belong to a required check and come from an authorized producer count. Anything else is **ignored**, never a REJECT. The requester's own evidence is ignored too.
- A PASS on retry supersedes an ERROR. A FAIL sticks until it is explicitly superseded, so you cannot retry until green. A changed artifact is a new subject.
- Missing, stale, ERROR or INCONCLUSIVE gives HOLD, together with the evidence that would unblock it.
- An unknown stage or a malformed policy gives REJECT.
- Self-approval is refused, and a person counts once across roles.
- A healthy HOLD rate is 5–15%. Alert above 20%: 47% of US executives in one survey admitted bypassing governance for urgent deployments [E-2.24].

**In production:**
- Evidence becomes in-toto Statements signed by the CI OIDC identity (GitHub `actions/attest`, or cosign + KMS on GitLab).
- Local unsigned evidence (`UNSIGNED-LOCAL`) counts as HOLD in production stages.
- A sample workflow ships as `.github/workflows/dsh-gate.example.yml`.

**Global holdout.** After G6, keep 5–10% of the eligible population as a long-term control for at least two quarters. It measures decay and feedback loops: retraining on treated data biases the model.

### 5.4.1 Evidence for non-interventional models

This covers demand forecasting, anomaly detection, and scores used for staffing or replenishment. Most of these models cannot be randomized at the individual level, and G6's rule would leave them on HOLD forever.

| Step | Requirement |
|---|---|
| Evaluation | Rolling-origin backtests, with the last ≥2 full cycles sealed. Report the lift over a seasonal-naive baseline (e.g. MASE < 1), sliced by SKU, store and region |
| Value | Decision-cost replay: feed old and new forecasts into the same replenishment or staffing rule and cost the stockouts, overstock and overtime against actual demand. Label it "replay estimate", take the 95% lower bound across windows × κ = 0.3, and book it as channel (3b), never merged with experiment-proven (3) |
| Causality | Where feasible, switchback (week-on/week-off) or cluster randomization by store or region; only then count it in (3) |
| Gates | `model_shadow` becomes ≥4 weeks of shadow running plus delayed-truth back-scoring (O5). `retrain_promote` uses a paired champion/challenger comparison on the same backtest windows |
| Caveat | Replay assumes demand does not respond to the decision. Censored demand or price changes break that assumption; record it as a caveat |

### 5.5 Human review: sample, tier, review evidence packs rather than transcripts

**Review 100%:** external numbers, production writes, model promotion, new verified queries, contract or definition changes, backfills, high-risk use cases.

**Sample:** certified answers at a random 5%. Low-confidence and novel-domain answers get full review. Reviewing every answer recreates the bottleneck, as Ramp found [E-3.13].

**Review canaries.**
- Inject 2–5% known defects into review queues.
- Below 80% detection, suspend the reviewer's approval rights and retrain them.
- Approvals faster than 10 seconds count as rubber stamps [assumption; the anti-rubber-stamp requirement is from E-2.22].

**Named ownership.** Every artifact has a named owner. A one-person team does not claim two-person review.

### 5.6 Statistics checklist (built into the A/B readout skill and the G5/G6 gates)

- Pre-register the randomization unit, population, primary metric, MDE, power, duration and stopping rule.
- SRM: a χ² test with p < 0.001 invalidates the experiment.
- Analyze by ITT, with cluster-robust standard errors at the randomization unit.
- Correct slices for multiplicity.
- HTE is pre-specified or labeled exploratory.
- Underpowered → INCONCLUSIVE, plus the MDE and whether adding sample is worthwhile.

**In the synthetic world [measured]:**
- With the population split between two policies, a perfect uplift list has only about 113–141 customers per arm. Its after-cost value test passes with probability about 0.18–0.24; the "churn went down" ITT test passes about 0.53–0.64 of the time.
- Even with all 500 slots on a perfect list (250 per arm), value power is only about 0.37, against about 0.87 for ITT.

The system must report INCONCLUSIVE, not dress up "a few fewer churners" as significant.

---

## 6. Delivery, organization and adoption

### 6.1 Three units of delivery

| Deliverable | Carrier | Gate | Rollback |
|---|---|---|---|
| Answers | Slack/Teams thread or BI, with an **answer certificate** | `answer_publish` | Recalled when a definition changes |
| Pipelines, semantics, context, skills, the gates themselves | **Pull request** | `pipeline_merge` | git revert and rebuild from the previous digest; tables via time travel or snapshots |
| Backfills | Plan PR: scope, cost, dry-run diff | Two-person approval | Snapshot restore |
| Models | Candidate + sealed-evaluation evidence | `model_shadow` → G5 → G6 | Roll back the whole release manifest (model, preprocessing, threshold, policy, image); rule-based fallback if the schema is incompatible |
| Decisions | Decision record (question, options, evidence tier, owner, expected effect, review date) + experiment | G6 | Stop the experiment, revert the policy |
| Harness changes (model, harness, skill, semantics, prompt) | PR | Full regression eval | Revert the pin |

**The answer certificate** contains:
- the question and how its terms were resolved (net or gross, test accounts excluded);
- the metric ID and digest, and the SQL and its digest;
- the build snapshot, check and reconciliation status, tier and caveats;
- the model and harness versions, and the cost.

The schema is in `docs/answer_certificate.schema.json`. Certificates leaving the company are labeled AI-generated (EU AI Act Art. 50 [E-2.2]).

**Reports and dashboards.**
- A report (weekly memo, analysis, board deck) = narrative + certificate list. Every number in the narrative carries a certificate ID. CI rejects uncited numbers; any uncited number downgrades the whole report to T3.
- A report takes the *lowest* label among its numbers, never an average.
- Causal wording ("drove", "caused", "led to") without V5 evidence is rewritten as correlational wording and gets a caveat.
- Every dashboard tile binds to a runtime-set verified query and inherits lineage-driven invalidation (§4.4).
- Business-facing formats are in Appendix A.

### 6.2 CI, environments and identity

**Four kinds of identity:**

| Identity | Permissions |
|---|---|
| Agent | Dev writes only; may open PRs |
| CI / gate | Runs checks, signs, deploys |
| Sealed scorer | Its own separate identity |
| Production credentials | Released only inside protected environments. GitHub allows up to six required reviewers and can prevent self-review [E-1.10] |

**CI order:**

sqlglot parse and lint → clone build → contracts and tests → reconciliation → data-diff → lineage impact and oracle-independence assertion → eval regression → signed attestation → gate → protected environment

**PRs that touch agent configuration** (`.mcp.json`, hooks, AGENTS.md, skills) need owner approval.

**SOX.** For SOX-scoped figures (anything feeding ICFR or SEC filings), the T0 sign-off, evidence retention and segregation of duties above should map onto your existing ICFR controls. Agent-produced numbers are not exempt [general knowledge — confirm the control design with your auditors].

### 6.3 Roles

| Role | Responsibility | Load [assumption] |
|---|---|---|
| Business data-product owner | Defines value; signs baseline, KPIs and kill criteria; owns experiment budget and decision sign-off | 1–2 h/week |
| Metric steward (business-funded) | Semantics, certified tables, verified queries; correction PRs within 2 business days | 0.25–0.5 FTE per domain |
| **Metric-definition council** | Resolves definition conflicts within 10 business days. Named coexistence is allowed (`net_revenue_finance` vs `net_revenue_ops`); one name with two meanings is not | Finance + business owners, monthly |
| Analytics engineers (CODEOWNERs) | Review agent PRs, maintain tests | 10–15% of their time |
| Platform / harness engineering | MCP, gates, sandboxes, identity, routing, FinOps, SLOs | 1–1.5 FTE |
| Eval owner | Golden sets, grader validation, regression, review canaries | 0.5 FTE |
| DS / statistics owner | Experiment design, causal sign-off | 0.5–1 FTE |
| Risk, compliance, privacy, security | Use-case tiering, routing matrix, DPIA, vendor risk, red team | As needed |
| Finance BP | Confirms κ and cash events; monthly NV | 0.1 FTE |

### 6.4 Adoption and change management

- **Embed in existing channels.** Put the agent in Slack, Teams and BI. LinkedIn saw 5–10× adoption after embedding its SQL Bot in DARWIN [E-3.7]. Replace the #help-data channel with "agent first, human escalation SLA", as Ramp did [E-3.13].
- **Measure with a stepped-wedge rollout.** Roll out to 3–4 cohorts in random order and estimate deflection and cycle-time effects with a mixed model with time fixed effects. This replaces self-reported "hours saved".
- **Weekly and monthly rhythm.**
  - A 30-minute "corrections → verified queries" session each week.
  - A monthly scorecard: self-serve rate (paired with audited correctness), pass^3, audit upper bound, CPVC, HOLD rate, incidents.
- **Two hours of training:** how to read labels and certificates, and when not to trust them. Outside the frontier, consultant accuracy fell from 84.5% to 60–70.6% with AI [E-8.12].
- **Position the program as augmentation, not headcount reduction.**
  - Gartner's survey of 350 executives found that about 80% of organizations piloting autonomous capabilities cut staff without a matching ROI gain [E-8.7].
  - US payroll data shows employment of 22–25-year-olds in AI-exposed occupations is 19% below trend, driven by reduced hiring rather than layoffs [E-2.23]. Build a path for junior analysts into steward, eval and review roles.
- **Works councils.** In the EU, inform workers' representatives *before* deploying a high-risk system at work [E-2.5]. In practice, involve them early for anything that monitors or evaluates employees.

---

## 7. Compliance and social constraints (as of 2026-10-19; have counsel review before rollout)

### 7.1 The landscape

| Jurisdiction | Status | Basis |
|---|---|---|
| **US federal** | No comprehensive AI statute and no preemption statute. EO 14365 created a DOJ AI Litigation Task Force; the March 2026 framework asks Congress to preempt "unduly burdensome" state laws, and the June 2026 draft stalled. NIST AI RMF is still v1.0 plus the GenAI Profile. CAISI launched an AI Agent Standards Initiative (Feb 2026). ISO/IEC 42001 is certifiable | [E-2.18, E-2.21] |
| **US banking** | **SR 26-2 / OCC Bulletin 2026-13 replaced SR 11-7** on 2026-04-17. They are risk-based, narrow the definition of "model", add vendor-model guidance, and explicitly *exclude* generative and agentic AI pending an RFI. Predictive models produced by the factory (W6) may still be "models" under SR 26-2 | [E-2.19] |
| **US credit and insurance** | The CFPB's Reg B amendments (effective 2026-07-21, subject to litigation) remove disparate-impact liability, but **adverse-action notices must still give specific, accurate principal reasons**, including for ML models. About 25 states plus DC have adopted the NAIC AI Model Bulletin, which requires an AI governance program that covers third-party models | [E-2.20] |
| **US states** | **Colorado**: the AI Act was repealed and replaced by SB26-189 (ADMT), mostly effective 2027-01-01. It requires notice, a plain-language explanation within 30 days of an adverse outcome, correction and human review rights, developer documentation and 3-year records; the Attorney General enforces, with a cure period. **California**: CCPA ADMT regulations, compliance by 2027-01-01. **Texas** TRAIGA (2026-01-01). **Illinois** HB 3773 (employment, zip-code proxies) | [E-2.17, E-2.18] |
| **US healthcare** | PHI requires HIPAA-compliant handling and a BAA with every vendor that touches it. Claude Managed Agents are not BAA-eligible | [E-6.10]; HIPAA itself is general knowledge |
| **EU** | The AI Act Digital Omnibus (Reg. (EU) 2026/1744) moved **Annex III high-risk obligations to 2027-12-02** and Annex I to 2028-08-02. **Art. 50** transparency and **GPAI enforcement** have applied since 2026-08. Under Art. 26, deployers must provide human oversight, keep logs ≥6 months, inform workers' representatives and run a DPIA. **CJEU C-203/22**: people subject to automated credit scoring are owed a meaningful explanation; counterfactuals may be appropriate. The GDPR part of the Omnibus is **not** adopted. The DPF is upheld, but an appeal (C-703/25 P) is pending | [E-2.1–2.8] |
| **Other** | Singapore IMDA's agentic AI framework (significant checkpoints, guarding against rubber stamps). Korea's AI Basic Act (2026-01-22) | [E-2.22] |
| **China (multinationals only)** | PIPL. Personal data in a prompt to an offshore API counts as an export, with thresholds at 100k / 1M people / 10k sensitive. Content leaving the company must be labeled (2025-09-01). Internal tools generally need no GenAI filing. See the Chinese edition | [E-2.9–2.16] |

### 7.2 Design requirements that follow

1. **Mandatory use-case risk tiering at intake.** HIGH-RISK covers:
   - credit and creditworthiness;
   - insurance pricing and underwriting;
   - employment (hiring, promotion, task allocation, monitoring);
   - education access;
   - essential services;
   - anything that "materially influences a consequential decision" under Colorado or California ADMT.

   HIGH-RISK triggers the heavy controls: interpretable or constrained models (scorecards, monotonic GBMs, GLMs), per-decision reason codes (supporting Reg B adverse-action reasons, C-203/22 explanations and the Colorado 30-day explanation), human final decision, bias testing, and documented human review and correction channels.
2. **Data minimization.** Prompts carry metadata and aggregates; row-level personal data is handled only by code in the sandbox (§4.5).
3. **Route by data class and jurisdiction.**
   - EU personal data → EU-region endpoints + ZDR + DPIA, with SCCs as a backstop to the DPF.
   - PHI → only BAA-covered services.
   - China-resident personal data → in-country.
4. **Tamper-evident audit logs.**
   - Record prompts and responses, tool calls, generated code, tables and columns accessed, approvers, and decision outputs.
   - Retain at least 6 months for EU high-risk deployers and 3 years for Colorado ADMT records, longer where sector rules require it.
   - Make logs exportable for regulators and auditors.
5. **Approval checkpoints in the orchestration layer, not the prompt:**
   - before sensitive-data access;
   - before cross-border transfer;
   - before production writes;
   - before promotion;
   - before outputs feed a consequential decision.

   Monitor override rates to catch rubber stamps, and keep a kill switch with a manual fallback.
6. **Compliance documents are pipeline artifacts.** Generate model cards, datasheets, DPIA drafts, NAIC-bulletin inventory entries and ISO 42001 / NIST AI RMF evidence from the same evidence store.
7. **Label AI output that leaves the company** (EU Art. 50).
8. **Agent security** (NIST CAISI agent work):
   - per-agent least-privilege identities;
   - tool allowlists, read-only by default;
   - per-tenant memory isolation;
   - data treated as untrusted;
   - egress allowlists;
   - an inventory of open-source and model components.
9. **Reconcile deletion rights with reproducibility** (GDPR Art. 17, CCPA deletion):
   - Evidence, certificates and audit logs store digests and aggregates, not row-level personal data.
   - Snapshots containing personal data have a retention limit (≤13 months by default [assumption]); after that, keep de-identified versions only.
   - On a deletion request: tombstone at the source → rebuild the affected snapshots → run the regression diff against an "expected differences" list instead of requiring identity.
   - Recompute holdout sample sizes after deletion. If they fall below the pre-registered minimum, downgrade the affected conclusions to INCONCLUSIVE.
   - Pseudonymize the audit-log fields you must keep, store the keys separately, and destroy the keys at expiry (crypto-shredding).

---

## 8. Roadmap

### 8.0 First, which starting point are you?

| Situation | First 2–4 weeks | Not yet | Enter the standard 0–90-day plan when |
|---|---|---|---|
| A. No warehouse; data lives in SaaS apps (Salesforce, NetSuite, Stripe) and spreadsheets | One person lands one reconcilable domain (orders or revenue) with Fivetran or dlt into BigQuery, Snowflake or DuckDB; source contracts and control totals; agents write extraction and staging models only as PRs (L2) | Q&A, ML, semantic layer | 4 consecutive weeks of passing reconciliation, and ≥10 recurring metrics |
| B. Warehouse, 1–3 person data team, volume below N* | Buy platform-native Q&A, or use Claude Code / Codex + dbt-mcp. Build only five things: a ≤10-metric semantic YAML, a 30-item golden set in your users' own words, the `analytics-answer` and `pipeline-breakfix` skills, answer reconciliation, one value-ledger table | dsh-mcp data access, W6, panels | Golden-set pass^3 ≥ 75% and a business-funded steward |
| C. Warehouse + dbt, team of 4 or more | Follow the 0–90-day plan below | — | — |

In A and B, one analytics engineer covers the eval-owner and platform roles; the thresholds stay the same and only the scope shrinks. B re-checks N* at week 4. If volume is still below N*, stay with "assets + evals + ledger" and do not build out the tool surface.

### Days 0–90

| Weeks | Deliverables |
|---|---|
| 0–2 | Pick a domain with a financial close: revenue, subscriptions or orders. Measure the baseline with an intake bot or ticket exports: volume, types, handling time, backlog, incidents, MTTR. Use-case risk tiering and routing matrix v1. Sign the thresholds. Counsel confirms data-residency, BAA and DPF/SCC positions |
| 2–6 | ctx v1: ≤30 certified metrics, 50–100 certified tables, 100–150 runtime verified queries, a 150-item eval set. dsh-mcp v0, or native MCP on a single platform. Six skills, hooks, the certificate schema, CI gates with attestation, the ledger |
| 4–8 | W1: put one dbt project behind `pipeline_merge`; replay 20 historical incidents as the DE eval; run the fault-injection pack on a staging clone; W1 at L2 in dev |
| 6–12 | W2 for one persona (10–50 champions); weekly scorecard; first cross-harness comparison (two executors); **run the same tasks on a de-identified slice of your real data** |

**Day-90 exit criteria:**
- **W2:**
  - held-out certified-scope pass^3 ≥ 85% (n ≥ 200);
  - ≥95% on questions covered by verified queries;
  - out-of-scope refusal ≥ 90%;
  - audited silent-error upper bound ≤ 3%;
  - CPVC ≤ 20% of the human unit cost;
  - WAU ≥ 30%;
  - self-serve resolution up 10 points.
- **W1:**
  - at least 20 PRs, with ≥40% merged without rework;
  - zero reconciliation escapes;
  - MTTR down 30%.
- **General:**
  - 100% of agent tasks in the ledger;
  - zero policy violations;
  - runs on at least two harnesses.

### Months 3–6

**Scope:**
- Up to 3 domains, each with a named steward.
- W3 moves to L2; W4 ships; low-risk DE change classes reach L3 in non-production.
- Policy packs for US, EU and other jurisdictions, enforced at the gateway.
- Platform-substitution test.
- First W6 experiment pre-registered with power ≥ 0.8.
- Compliance pack v1: audit export, model cards, DPIA drafts, NAIC inventory.

**Exit criteria:**
- Self-serve resolution up 15–20 points over baseline; mechanical tickets down 10 points.
- No pass^3 regression across two model or harness upgrades.
- Canary detection ≥ 80%; HOLD rate 5–15%.
- Conservative NV positive for two consecutive months.
- A stepped-wedge estimate in hand.

### Months 6–12

**Scope:**
- Up to 5 domains.
- W6 through G5 → G6, uplift-first; establish the global holdout.
- Cross-platform recomputation via OSI.
- A harness switch drill.
- Align with Colorado and California ADMT (2027-01-01) and EU Annex III (2027-12-02).

**Exit criteria:**
- At least one decision with V5 evidence and LCB95 > 0, or spend stopped by an honest null result.
- Annualized conservative NV > 0.
- Zero agent-caused Sev-1 incidents.
- A model or harness switch completed within two weeks, with ≤3 points of regression.

---

## 9. The reference implementation (dsharness) and what it measured

### 9.1 Purpose and structure

dsharness is the design's **measuring instrument**, not a production platform. It simulates a subscription company over 21 months:
- about 7,600 customers;
- a known value for every number, kept in a custody zone that agents cannot reach;
- 9 planted defect classes from real life (D1–D9);
- a randomized retention program in March–June 2026.

The company bills in CNY. Monetary results below are in CNY (¥), with USD at ¥7.1/$ in parentheses; the mechanisms do not depend on currency.

It implements the parts of this design that you would build:

| Design component | Reference implementation | Production replacement |
|---|---|---|
| Source contracts and governed pipeline (§3.2) | Pandera contracts; dbt-style SQL models and data tests; exact reconciliation to the financial close; incident-scoped corrections; price-book validity by date; builds run with file access off | dbt, SQLMesh |
| Semantics and context (§4.4) | YAML semantic model (11 metrics, including program ITT metrics); catalog with PII and temporal tags; verified queries (14 runtime + 6 eval, disjoint) | MetricFlow, Cube, platform semantic views |
| dsh-mcp and skills (§4.2–4.3) | 3 roles with 7/10/6 tools; audit and budget; MCP 2.x stdio server; 4 SKILL.md files; AGENTS.md; `.mcp.json` | Remote MCP + EMA behind your gateway |
| Verification library (§5.1) | SQL static analysis with per-scope resolution; answer reconciliation and claim-vs-question checks; time-travel and static leakage checks; bootstrap, SRM, calibration | Same, wired into your CI |
| Gates and evidence (§5.4) | Three-verdict gate (draft bugs fixed); approvals that refuse self-approval; in-toto Statement export (UNSIGNED-LOCAL) | CI OIDC signing + protected environments |
| ML factory | PIT features; declarative training; sealed evaluation (3 looks per task); uplift learning (ITT only) with offline Qini | Your ML platform, MLflow |
| Decision loop | Policy registry; pre-registered experiment design; one-shot oracle (one experiment per customer per month); per-policy readouts; channel economics | Your experimentation platform |
| Evals (§5.3) | Scripted benchmark with truth only from the evaluator side; real-agent runner (pass@k, pass^k, cost per correct answer) | Inspect AI, MLflow eval |

**Size:** `src/` is about 12k lines of Python, of which the world simulator, benchmark and demo are about a quarter. Tests are about 5.5k lines, 444 tests in all.

### 9.2 Running it

```bash
cd dsharness && uv sync
.venv/bin/dsh world && .venv/bin/dsh build --all   # drift/monitor builds fail by design (D8)
.venv/bin/dsh demo                                 # end-to-end scenario, 1–2 minutes
.venv/bin/dsh bench                                # scripted benchmark, ~2–3 minutes
.venv/bin/dsh roi --profile us                     # value calculator
.venv/bin/python -m pytest -q -p no:cacheprovider  # all tests, ~10 minutes
```

Open Claude Code, or any CLI that supports MCP, Skills and AGENTS.md, in the repository root. It then works through the governed `dsh-analyst`, `dsh-ds` and `dsh-de` MCP servers. For a graded real-agent run, use `dsh live`; it needs a logged-in `claude`, or `--command-template` for another CLI.

### 9.3 End-to-end demo (`dsh demo`, full-size world) [measured]

| Step | What happened | Gate |
|---|---|---|
| 1 Q&A | Careful agent: governed metric with a declared `metric_call`; matches the truth | VERIFIED / **ALLOW** |
| | Naive agent: raw SQL over the uncleaned export reports east-region March revenue of ¥60,247 against a true ¥59,333, because duplicates and test accounts were kept | FLAGGED / **REJECT** |
| 2 DE break-fix | The billing export switched to schema v2. The drift build fails loudly instead of computing wrong numbers | FAILED |
| | Naive fix (amount ÷ 100): CI build and regression diff FAIL | **REJECT** |
| | Correct fix (`COLUMNS('^amount_(cents\|minor)$')`, backward compatible): all three checks PASS; drift and monitor builds return to READY | **ALLOW** |
| 3 Features | `bug_tickets_90d`: time travel and static checks PASS | ALLOW |
| | `has_cancel_reason` (a post-outcome column) and `minutes_last_month_naive` (ignores late-arriving data): time travel FAILs | REJECT |
| | `current_price_fen`: time travel *passes*, because prices had not changed yet; **only the static check catches it** | REJECT |
| 4–5 Train and sealed evaluation | Dev CV AUC 0.675; sealed June AUC **0.712** [0.679, 0.747], n = 3,589, 1 of 3 looks used | `model_shadow` ALLOW |
| 8 Decision, offline | Uplift learned from 2,256 randomized enrollments in March–May (June labels immature and excluded), ITT only. Evaluator check: Spearman correlation with the true uplift +0.36; its top 30% holds 54% of what the oracle's top 30% holds (random: 28%) | `model_experiment` needs business-owner approval; self-approval refused |
| 8 Decision, July experiment | One oracle call, stratified by policy. Risk policy: readout +¥2.92 per contact, CI [−7.14, +12.98]; truth **−¥1.69 (−$0.24)**. Uplift policy, contacting only the 209 customers with positive expected value: readout +¥9.76, CI [−11.08, +30.60]; truth **+¥0.78 (+$0.11)**. Both CIs cover the truth | Both **INCONCLUSIVE** |
| | The phone channel loses money for every policy (risk policy ≈ −¥21.39 per contact). Even a perfect model finds only 46 of 3,590 customers worth a call | Not scaled |
| | Naive strategist claims +¥19.84 per contact (risk × retained value); truth −¥1.69 | HOLD (no readout) |
| 9 Scale | No policy has a PASSing readout, so no decision value is counted (ROI decision channel = 0) | `model_scale` **HOLD** |
| 10 Drift | August–September: `last_charge_fen` PSI 6.2 (the plus-plan price rise, D9) and `channel` 0.23 (a new channel); model-score PSI only 0.01 | Alert, investigate |
| 11 Retrain | A challenger is proposed on the monitor build (dev AUC 0.690). Sealed evaluation, champion/challenger comparison and risk approval are missing | `retrain_promote` **HOLD**, not auto-promoted |

### 9.4 Scripted benchmark (`dsh bench`, full-size world, 38 items) [measured]

Careful agents follow the right procedure. Each naive variant plants one realistic mistake and always claims success. Truth comes only from the evaluator side: custody tables, sealed labels and the oracle.

| Scope | Items | Correct | Wrong | **Silent failures without the harness** | **Silent failures with the harness** | Caught (REJECT/FLAGGED · HOLD) | Correct but not auto-delivered |
|---|---|---|---|---|---|---|---|
| All | 38 | 10 | 28 | **28** | **0** | 28 (27 · 1) | 3 (2 to human review, 1 held by design) |
| Q&A | 25 | 6 | 19 | 19 | 0 | 19 | 1 (CSAT has no governed metric → T3, review) |
| DE | 5 | 1 | 4 | 4 | 0 | 4 | 0 |
| ML | 4 | 1 | 3 | 3 | 0 | 3 | 0 |
| Decision | 4 | 2 | 2 | 2 | 0 | 2 | 2 |

**Which check caught each fault class:**

| Fault | Caught by |
|---|---|
| Raw SQL, look-alike dedup, test accounts not excluded, refunds ignored, mislabeled claim | `answer.reconcile` (reconciliation to the governed metric) |
| v2-only fix | `pipeline.regression_diff`: the served snapshot breaks |
| ÷100 fix | `pipeline.ci_build` (price-book test) + regression diff |
| Calibrating revenue to the finance table | `pipeline.oracle_independence` + regression diff. A planted-defect probe (5 east-region July charges deleted) still built READY under that pipeline |
| Deleting the reconciliation | CI build (tests removed) + oracle independence |
| Three leaky features (self-reported dev AUC 0.948 vs sealed ≈ 0.67) | `model.leakage` |
| "Risk × LTV" value claim | No readout → HOLD |
| Scaling the phone channel | `policy.offline_qini` FAIL → REJECT |

**One naive item was right by chance.** The per-protocol analysis claimed −12 fen per contact against a truth of +109, which falls within the 300-fen tolerance. The harness still held it, because the method is unsound and there was no readout. The benchmark lists such cases separately and never counts them as harness failures.

**What these numbers mean.**
- They demonstrate **mechanism coverage**: every planted fault class is stopped by the intended check. The price is that some correct but unverifiable answers go to human review.
- They are *not* LLM error rates. Scripted agents are not a random sample, and comparing a silent-error rate of 10% against 5% needs about 430 independent items per arm.
- **Real-agent numbers have not been measured yet.** The nested `claude -p` on the build machine was not logged in. Run `dsh live` once it is.

### 9.5 Four economic lessons from the decision loop [measured]

1. **Whether uplift is learnable depends on data collection.**
   - In world v2a, price sensitivity left only weak traces in tickets and add-on purchases. The learned uplift had a Spearman correlation of about −0.01 with the truth, the offline check FAILed, and the policy was REJECTed.
   - From 2026-01 the company tracks pricing-page views (v2b). The same procedure then reaches +0.36, and its top 30% holds 54% of the oracle's uplift.
   - **What you collect often matters more than which model you use.** That is why agents belong in collection, proposing tracking-plan and contract PRs (§3.2).
2. **Value = risk × persuadability.**
   - Targeting on price sensitivity alone picks people who were never going to leave. A single-factor rule on pricing-page views is worth −¥182 per month for the top 500.
   - Model the effect on the logit scale with effect modifiers, so that risk and persuadability multiply.
3. **A one-month experiment cannot resolve differences of cents per contact.**
   - The truth is +¥0.78 per contact for the uplift policy and −¥1.69 for the risk policy.
   - A one-month test (about 250 per arm for risk, about 105 for uplift) has a 95% CI half-width of about ±¥10–21 per contact. Even a perfect model passes in one month only about 18–37% of the time.
   - So both policies are INCONCLUSIVE: keep a bounded experiment (G5), do not scale (G6), and report the MDE and whether more sample is worthwhile.
   - **That honesty is the value.** The naive claim was +¥19.84 per contact. Scaling the risk policy on that claim would actually lose ¥1.69 per contact.
4. **Scale decides feasibility.**
   - An exploratory 4× world (about 14,300 active customers; configuration in `research/scale_4x.md`) yields a learned uplift policy that is **positive in truth**: about +¥1,500–3,400 per month depending on the targeting rule, or 13–30% of the oracle at that scale. Ranking by true risk, which assumes perfect churn prediction, does better still.
   - Per-contact value stays at a few yuan, so a decisive result still needs multi-month or larger tests.
   - This matches §2.4: **for small and mid-size companies, uplift modeling often does not pay.** It is usually better to run a simple hypothesis-driven policy with a properly powered long experiment, or to spend the budget on not doing value-destroying things.

### 9.6 Adversarial review: what it found and what was fixed

The design says verifiers must themselves be verified, and the reference implementation was put through exactly that. The review had five independent strands: two red teams (Q&A; and pipelines, models and decisions), a line-by-line code review, a document fact-check and a requirements-completeness review. Every exploit was reproduced by a separate agent before it was fixed.

| ID | Severity | Problem | Fix |
|---|---|---|---|
| RT-AN-01 | Critical | **Claim substitution.** An agent's declared `metric_call` could disagree with the *question*: February asked, March answered; east asked, north answered; "by region" asked, a total given. The numbers matched the declared claim, so the answer got VERIFIED / T0 / ALLOW. The old check only emitted warnings, which never reached the evidence | `claim_contradictions` parses the *question* only, never the agent's claims, and checks the metric, month containment, filter values and breakdown. On a mismatch, reconciliation is INCONCLUSIVE, the answer drops to T3 / HOLD, and it goes to human review. All 15 reviewed question/claim pairs are unaffected |
| DSH-CORR-001 | Critical | **Custody exfiltration.** A data-engineer agent's pipeline SQL used "a CTE named like a file path" to get past the static screen, read ground truth and arbitrary local files during the CI build, and returned them through failing-test samples | Defense in depth: (1) builds turn off external access and lock configuration as soon as the raw tables are loaded, and the runner refuses unlocked connections; (2) the static screens resolve CTEs per scope and fail closed; (3) system catalogs are refused |
| DSH-CORR-002 | Medium | The same CTE-scope hole in the Q&A SQL screen allowed system-catalog reads (table and column names, file paths) | The same shared resolver |
| DE-2 | High | The regression diff compared only one served snapshot. A fix that altered usage or ticket data only for *later* months could still get ALLOW | On the new export, every silver table the governed pipeline can still build must also be identical |
| ML-1 | Medium | Sealed-evaluation error messages carried counts derived from the holdout, and no budget was spent | Coverage is checked against the build's own population first. Any return after reading custody costs budget, and error messages carry no numbers |

**Lessons:**
- Both critical holes came from **the harness's own edge conditions**, not from a model that was not smart enough.
- Static screens need a second line of defense that does not depend on them; here that is execution-layer file lockdown.
- Whether the claim matches the question is itself something to verify.

This bears out §5.1: every layer assumes the previous one leaks.

The fact-check corrected 38 citation, number and wording issues in the Chinese edition; this edition incorporates them. The completeness review added 10 sections, and their counterparts appear here in §2.4.1, §3.2, §4.1, §4.4, §5.4, §5.4.1, §6.1, §7.2, §8.0 and Appendix A.

**Not covered:** two red-team strands were cut short by a safety classifier before trying every planned vector. Re-run red-team tasks at every major release.

### 9.7 Limitations and next steps

**Not implemented:** sandboxed candidate builds (they run in-process, but with file access off), signed evidence, real identities, MetricFlow (the semantic layer here is a YAML subset), and SQL dialects beyond DuckDB.

**Not measured:** real LLM agents' pass^k and cost per correct answer, behavior on real enterprise data, and human review time.

**Migrating the reference to your own domain** (about 2–3 weeks for one analytics engineer [assumption]):

| Replace | Where in the reference | With |
|---|---|---|
| Pipeline and tests | `lake/pipeline/` | Your dbt project; keep the regression diff and the oracle-independence check |
| Metrics, catalog, verified queries | `context/*.yaml` | Your metric definitions, with business synonyms per metric |
| Independent-record mapping | `INDEPENDENT_RECORDS` and `finance_close_check` in `tools/analytics.py` | An `independent_record` field in the semantic YAML |
| Term and time parsing | `GOVERNED_TERMS`, `months_from_text` (fixed English vocabulary) | Generated from the semantic YAML ("last month", "Q1", "YoY", fiscal calendars) |
| Trusted features | `ml/features.py` | Your own PIT feature SQL, each passing `feature_accept` |
| Snapshot dates | `CUTOFFS` in `workspace.py` | Your close calendar |

**Next steps** (aligned with §8):
1. Measure 2–3 harnesses with `dsh live`, k = 3, including one platform-native agent tested as a black box.
2. Swap the synthetic world for one real domain.
3. Wire `dsh gate` and `dsh evidence export` into CI with OIDC signing.

---

## 10. Risks and falsification

### 10.1 Main risks

| Risk | Early signal | Mitigation |
|---|---|---|
| **Nobody curates** (the most likely failure) | Correction PRs backlogged > 2 weeks; verified queries stop growing | Business-funded stewards as a precondition for onboarding; freeze the domain after a 60-day vacancy |
| Verification costs more than it saves | CPVC rising; review queues | Sampling, tiering, evidence packs, canaries |
| The definition itself is wrong | Gates only prove "consistent with the definition" | O1 reconciliation, definition council, owner sign-off |
| Platforms absorb this layer (Cortex AI Gateway, Databricks buying Quotient, dbt Agents Schema) | Native capabilities catch up on a single platform | Keep the layer thin and the assets in git; run the substitution test |
| Eval leakage or Goodhart effects | High offline scores alongside high audited error | Separate R and E sets, hidden seeds, quarterly rotation, self-serve rate paired with correctness |
| Rubber-stamping or governance bypass | Approvals < 10 s, override rate near 0, HOLD > 20% | Review canaries; make the governed path the fastest path |
| Price, rate-limit or availability shocks | Repricing alerts | Two or more providers, a self-hostable fallback, off-peak batch work |
| Prompt injection from data, privilege abuse | Injected instructions executed > 0 times | Tool-layer authorization, sandboxes, egress allowlists, no token pass-through |
| Product renames and deprecations | Databricks Knowledge Assistant went from GA to legacy in about 9 months [E-5.8] | Integrate only at MCP, SQL and OSI level |
| Overfitting to the synthetic world | Benchmark looks good, real slice is poor | Real slice within 90 days; calibrate defect distributions on real incidents |
| Runaway warehouse spend | Agent-SQL share of credits rising | Dry-run estimates, quotas, partition lint |

### 10.2 What would prove this design wrong

All of these are measured in the pilot:
1. **Semantics-first does not hold.** On your golden set, "semantic layer + MCP" beats "raw SQL" by less than 8 points → buy native conversational Q&A only.
2. **Verification is not worth its cost.** In real audits, checks and gates cut the silent-error rate by less than 20% → keep context assets only.
3. **The labels do not discriminate.** T1 error rates are not lower than T3's in random audits → redesign the labels.
4. **The tool surface is not worth building.** Native agents with their own evals land within 3 points at lower cost → do not build dsh-mcp; keep assets, evals and the ledger.
5. **The hours-saved case fails.** Measured κ < 0.2, and no business owner will randomize within 6 months → value rests on displaced spend only, and the model factory stays offline.
6. **"Single writer, no panels" is too conservative.** Panels or voting cut silent errors by ≥ 30% at ≤ 1.5× cost and replicate on real data → revise the stance.
7. **The SMB profile does not hold.** Steady-state curation needs > 2 FTE per domain.

---

## 11. Architecture decision records

| ID | Decision | Alternatives | Rationale |
|---|---|---|---|
| ADR-01 | A thin verifiable delivery layer: assets + verification + value | A home-built DS agent; a multi-role AI team; buy a platform only | Generation is commoditized [E-5.22]; no single vendor covers cross-platform independent verification and the decision loop (our assessment) |
| ADR-02 | W1 (break-fix) and W2 (Q&A) first, in one domain | Q&A only; DE only | W1 does not depend on adoption and has a cheap oracle; W2 has the most value but is bounded by N* |
| ADR-03 | One ladder V0–V5, projected to T0–T3 for users | Several ladders | One vocabulary; reconciliation is strictly stronger than dual-path |
| ADR-04 | Certified answers to the asker are L1; shared dashboards must earn L3; external and SOX numbers need T0 + a named sign-off | L3 from day one; cap at L1 | Reconciles "autonomy is earned" with "Q&A must be useful" |
| ADR-05 | No production L4 within 12 months except idempotent re-runs within quota; backfills, definition changes and promotions are never automatic | Allow L4 backfills | Backfills rewrite history irreversibly |
| ADR-06 | Build dsh-mcp for multi-platform and SMB; on a single platform, use native MCP first and decide with the substitution test | Always build; never build | Cross-platform verification is a gap; on one platform it would duplicate the vendor |
| ADR-07 | Evidence as in-toto Statements signed with CI OIDC; unsigned local evidence counts as HOLD in production | A custom HMAC ledger | Commodity tools exist; with symmetric keys, whoever runs the gate can forge evidence [E-1.10, E-1.11] |
| ADR-08 | Default executors: platform-native where the data lives, plus a contract-permitted self-run harness (Claude Code, Codex, Gemini CLI); swappability lives in the asset layer; closed agents get black-box evals | Standardize on one vendor's agent | Vendor concentration risk; data residency; hooks cannot be enforced inside closed agents |
| ADR-09 | Routing policy as YAML, enforced by your gateway | Build a routing gateway | Keep the layer thin |
| ADR-10 | A panel or vote is on by default only if: silent errors fall ≥ 30% or accuracy rises 5 points; CPVC ≤ 1.5×; a paired test is significant; it replicates on real data | On by default | Debate ≈ voting [E-4.16]; multi-agent ≈ 15× chat tokens, ≈ 4× single agent [E-6.15] |
| ADR-11 | Economics: κ = 0.3, baseline demand only, ε at the audit upper bound, decision value at LCB95, platform engineering included in N* | κ = 0.5; count all hours | Prevent inflated value; Gartner's cancellation causes are cost, unclear value and weak risk controls [E-8.6] |
| ADR-12 | No new orchestrator | Mandate Dagster (draft) | Reuse what exists [E-1.12] |
| ADR-13 | Answers must declare a claim (`metric_call`), the claim must match the question, and the harness reconciles it; undeclared answers get T3 at best | An LLM judge decides correctness | Judges are unreliable [E-4.14]; reconciliation is deterministic; claim substitution was a real hole (§9.6) |
| ADR-14 | The first ML vertical is experiment- and uplift-first; underpowered one-month tests must say INCONCLUSIVE | Risk ranking + AUC | Risk ≠ value [E-1.23]; measured in §9 |
| ADR-15 | DE fixes must be backward compatible, regress on both the served and the new export, keep oracle independence, and leave reference data locked | Validate on the new export only | v2-only fixes break history; calibrating to the close voids reconciliation |
| ADR-16 | The reference keeps its tiny SQL runner (dbt-compatible SQL) and adds no dbt or MetricFlow dependency | Port to dbt-duckdb + MetricFlow now | The reference exists to measure mechanisms; production uses dbt, MetricFlow or platform semantics |
| ADR-17 | Pipeline builds run with external file access off, independent of static screening | Trust the static screen | The screen leaked (§9.6); defense in depth |

---

## 12. Key numbers and sources

| Number | Value | Source | Confidence |
|---|---|---|---|
| Silent-error share | 69.8% of a baseline's zero-score runs were normal-looking wrong answers | [E-4.13] | Medium-high (one paper) |
| Semantic-layer gain | +8 to +25 points; 90.0 → 98.2 and 84.1 → 100 on a modeled schema | [E-4.11] | Medium (11 questions × 20 runs) |
| Raw enterprise warehouses | BEAVER 10.8% (30.1% with oracle hints); BIRD-Interact 8.67% / 17.00% | [E-4.4] | High |
| DABstep | Context ablation 13.9 → 55.1% (single-author preprint); KGMON 89.95 vs Claude Code + Opus 66.93 | [E-3.18, E-4.5] | Low-medium / medium |
| ELT | Extract and load 96%; building transformations 22.66 → 32.51%; pass^3 8–16 points lower | [E-4.8] | Medium |
| MLE-bench | 61–64% medal rate | [E-4.6] | Medium |
| LLM judges | Best ≈ 82%; up to 14% swing from answer order | [E-4.14] | High |
| Tool count | OpenAI cut ≈ 40 → 13; selection degrades past 30–50 | [E-3.3, E-6.14] | Medium |
| Multi-agent cost | ≈ 15× chat tokens (≈ 4× a single agent) | [E-6.15] | Medium |
| Cases | Grab: mechanical tickets 44 → 30%, SQL self-serve 50 → 81%; Ramp: 22× questions; Meta: 88% of queries use tables from the last 90 days; LinkedIn: 5–10× adoption when embedded, 53% on its expert benchmark | [E-3.7, E-3.10, E-3.12, E-3.13] | Medium |
| Adoption and ROI | McKinsey 2026: 37% report any EBIT impact, flat year on year; Gartner: > 40% of agentic projects cancelled by 2027; Wharton: 31% vs 16% | [E-8.4, E-8.6, E-8.8] | Medium / low-medium |
| Human frontier | Outside the frontier, accuracy 84.5 → 60–70.6% | [E-8.12] | High |
| Model prices | Sonnet 5.5 $2/$10; Opus 5.5 $4/$20; Haiku 5.5 $0.10/$0.50; Fable 5.1 $10/$50 per M tokens | [E-8.16] | High |
| Platform pricing | Snowflake $2.00 / $2.20 regional per AI Credit; Genie free for users through 2027-01-31, but service principals billed; Google ≈ $3/$20 per M data tokens; Hex $36–$75 per editor | [E-5.3, E-5.7, E-5.12, E-5.18] | High / medium |
| US labor | BLS median $120,230 → ≈ $172k fully loaded | [E-8.19] | Medium-high (loaded cost derived) |
| US regulatory dates | SR 26-2 2026-04-17; Reg B amendments 2026-07-21; Colorado and California ADMT 2027-01-01 | [E-2.17–2.20] | Medium-high |
| EU dates | Annex III 2027-12-02; Art. 50 2026-08-02 | [E-2.1, E-2.2] | High / medium |
| N*, κ, FTE loads, HOLD band | §2.4, §5.4 | [assumption] | Low |
| Synthetic-world results | §9 | [measured] | High (synthetic only) |

---

## 13. Questions only your company can answer

1. **Baseline.** What are monthly request volume, mix, handling time, backlog and SLA? Do requests arrive as tickets or only in Slack/Teams? What share could 30 certified metrics cover?
2. **Loaded cost and cash events.** What is the real loaded rate? Are there open requisitions, contractor spend or SI contracts that agents could displace?
3. **Independent records.** Which metrics tie to the GL close, processor settlements or source control totals? How are adjustments recorded?
4. **SOX scope.** Which agent-touched numbers flow into ICFR-relevant reports, and what control design will your auditors accept?
5. **Vendor terms.** Which model and agent vendors are approved? ZDR and no-training terms? BAAs for PHI? EU-region processing?
6. **Data classes and jurisdictions.** Do you handle PHI, consumer credit (FCRA/ECOA) data, children's data, EU personal data, or China-resident personal data?
7. **Consequential decisions.** Which models influence credit, insurance, employment, housing, education or essential services, and therefore Colorado and California ADMT, Reg B or NAIC bulletin obligations?
8. **Platform specifics.** Snowflake, Databricks, BigQuery or Fabric? Which semantic layer is the system of record? Does your IdP support MCP EMA?
9. **Randomization authority.** Can you randomize customer-facing actions and keep a long-term global holdout?
10. **Cost of an escaped error (L).** Are data incidents and their costs logged?
11. **Definition conflicts.** Which metrics are contested, and who arbitrates?
12. **Steward funding.** Will business units fund 0.25–0.5 FTE per domain?
13. **Employee activity data.** What is the notice and retention basis for using query logs for personalization and golden-set mining? Does it need works-council review in the EU?
14. **A real data slice.** How will a de-identified evaluation slice be approved within 90 days?

---

## Appendix A: Deliverables as business readers see them

**A.1 Answer certificate (Slack/Teams card)**

> **Net revenue by region, March 2026**: East ¥59,332.70 · North ¥53,371.50 · South ¥39,263.70 · West ¥30,243.80
> Label **T0 reconciled** (external and board use allowed with a named sign-off). Definition: net of refunds, internal test accounts excluded, South March unit incident corrected per the registered incident.
> Snapshot 2026-05-31 · metric `net_revenue_fen@<digest>` · matches the financial close to the cent · certificate `ac-…` · AI-generated
> Machine format: `docs/answer_certificate.schema.json`. The executor fills in the harness, model and cost blocks.

**A.2 Decision record**

Question | options | evidence tier | owner | expected effect (with MDE) | power pre-check | review date | outcome (an honest null included)

**A.3 Experiment pre-registration**

Randomization unit | population | primary metric (after-cost value) | MDE | per-arm n and power | duration | stopping rule | SRM threshold | slices and multiplicity correction | pre-specified HTE

**A.4 Value-ledger row**

task_id | workflow | baseline demand? | minutes saved and how measured | κ | audit result | token / compute / review cost | decision value (G6 LCB95 only)

---

*Evidence: [REFERENCES.en.md](REFERENCES.en.md). Design-panel record: `research/`. Builder interface contract: [IMPLEMENTATION_SPEC.md](IMPLEMENTATION_SPEC.md). Chinese edition: [DESIGN.zh-CN.md](DESIGN.zh-CN.md).*
