# Which model does what (from 2026-11-09)

Decided on 2026-11-08 and 2026-11-09. It applies to experiments pre-registered after E3b v2; earlier
experiments keep the transports recorded in their own `DEVIATIONS.md`.

| role | model | transport |
|---|---|---|
| Claude agent under test | Claude Haiku 5.5 (`claude-haiku-5-5`) | Claude Code CLI on an API key (`census.AGENTS`, `sandbox.find_cli(api_key_file=...)`) |
| Codex agent under test | GPT-6 Luna, reasoning high | Codex CLI |
| helpers: labels, meaning checks, probes, reviews | GPT-6 Luna (default); Haiku 5.5 when a Claude-family check is wanted | `dsharness.bench.llm` (`ask`, `ask_files`) |
| adjudicator of labeller disagreements | GPT-6.1 Terra (`gpt-6.1-terra`) | `openai_api` |

- **Spend ledger:** every API call is recorded in an append-only ledger outside the repository (`budget.py`,
  `DSH_SPEND_LEDGER`), with a configurable daily cap (`DSH_DAILY_USD`). A model with no price in `budget.PRICES` is
  refused. The Claude agent is checked before each attempt and charged its `total_cost_usd`.
- **Labels:** two independent labellers, normally two different prompts to Luna, so independence is weaker than a
  cross-family pair; Terra adjudicates, blind. Report κ separately and name the labellers in the report.
  A cross-family check (Haiku) is added when the result depends on a few labels.
- **No Sol.** Decision (2026-11-16): judges, labellers and reviewers use GPT-6 Luna or Haiku 5.5; Sol is too expensive.
  `gpt-6-sol` has no entry in `budget.PRICES`, so `budget.price` refuses it until someone adds one on purpose.
- **API key for the Claude agent:** read by `claude_api.api_key()`; written to a 0600 file under `$XDG_RUNTIME_DIR`,
  bound read-only into the sandbox and read by `apiKeyHelper`. It is never on a command line, in a run directory or in
  the repository. After each attempt the run directory is scanned and any copy of the key is blanked.
- **Never switch transport inside a running census.** CLI and API are not neutral. `DSH_CLAUDE_TRANSPORT=cli`
  restores the old logged-in CLI run of `sonnet` for a re-run of an earlier experiment.
- **Pilot first.** A new bank gets a short Haiku pilot (cost per attempt, share of attempts that finish and deliver)
  before its census is frozen.

Cost for one E3b-sized census (168 Claude attempts), measured in the 2026-11-10 pilot on six of E3b's requests (12
Haiku attempts, `experiments/E5/runs/pilot_haiku`): Haiku 5.5 averaged $0.040 per attempt in arm N and $0.079 in arm H
(Sonnet in the E3b census: $0.138 and $0.139). Haiku takes about 24 turns against Sonnet's 9 and about ten times the
tokens, so it is only 2 to 3 times cheaper per attempt, not 22 times. A census of this size costs **about $10 for the
Claude agent alone**. Helpers and adjudication add
$0.5 to 1.5. Codex runs are not included in these costs.
