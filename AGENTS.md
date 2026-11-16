# AGENTS.md — working in the DS Harness repository

Conventions for any coding agent (Claude Code, Codex, Cursor, ...) that edits this repo or
does data work through it. The harness exists to make agent work *verifiable*; please do not
route around it.

## Setup and tests

- Python 3.12, dependencies pinned in `pyproject.toml` / `uv.lock`. Use the project venv:
  `.venv/bin/python`. Do not add dependencies.
- Run the suite: `.venv/bin/python -m pytest -q -p no:cacheprovider`
  (single file: `... pytest -q -p no:cacheprovider tests/test_tools.py`). Delete any
  `.hypothesis/` directory a run creates.
- Tests build a small synthetic world into temporary directories (session fixtures in
  `tests/conftest.py`: `small_ws`, `build_reports`, `train_build`, `score_build`). Tests must never
  read or write `./workspace`. A test that needs its own registry/ledgers builds a private
  workspace that symlinks the shared lake (see `tests/test_tools.py::private_ws`).
- The binding interfaces are in `docs/IMPLEMENTATION_SPEC.md`; the code is the source of truth
  where it documents a justified deviation.

## Trust zones (`workspace/`)

| Zone | Contents | Who may read / write |
|---|---|---|
| `sources/` | raw source-system exports | ingest/build only |
| `lake/` | governed builds (`warehouse.duckdb` + `build_report.json`) | agents read through tools only |
| `custody/` | ground truth, sealed labels, holdout and experiment ledgers | **evaluator code only — never open, list, copy or summarise it** |
| `registry/` | evidence, approvals, gate decisions, audit log, candidates | append-only, written by the tools layer |
| `runs/` | candidate artefacts, pipeline-change CI builds | scratch, candidate-writable |

- Never read `custody/` from agent code, notebooks, prompts or tests of agent behaviour. If a
  task seems to need it, it is an evaluator task: say so instead of reading it.
- Never edit or delete lines in `registry/*.jsonl`. History is append-only; a correction is a new
  record that supersedes the old one.
- `bronze.*` tables contain personal data. Agents never query them; use `silver.*` (certified)
  or `bronze_safe.*` (PII-free, uncertified).

## How to submit work

Do data work through the governed tools (Python `dsharness.tools` or the MCP servers in
`.mcp.json`: `dsh-analyst`, `dsh-ds`, `dsh-de`). Each server exposes one role's tools only.

- Analytics: answer with governed metrics, then `submit_answer` with a declared
  `metric_call`. Report the returned tier (VERIFIED / CONSISTENT / FLAGGED) truthfully.
  Skill: `.claude/skills/analytics-answer/SKILL.md`.
- Modelling: `propose_feature`, `train_candidate`, `request_final_evaluation`,
  `request_release`. Skill: `.claude/skills/churn-modeling/SKILL.md`.
- Pipelines: `propose_pipeline_change` (only `models/*.sql` and `schema.yml`). CI must pass the
  build, the regression diff and oracle independence. Skill: `.claude/skills/pipeline-breakfix/SKILL.md`.
- A gate verdict (ALLOW / HOLD / REJECT) is the answer. Do not retry with cosmetic changes to
  get a different verdict, do not weaken tests, and never claim success a tier or gate does not
  support.

## Code conventions

- Dataclasses and type hints; short module docstrings that state the trust boundary.
  Comments only where the reason is not obvious.
- Fail loudly: raise (library) or return `{"error", "kind"}` (agent-facing tools). Never turn
  a failure into a success, a missing result into zero, or an immature label into "no churn".
- Check results use `PASS | FAIL | INCONCLUSIVE | ERROR`, never booleans.
- Library checks return results; only `dsharness/tools/` records evidence.
- No network access and no LLM calls inside library code.
- Agent-facing SQL runs on `common.open_readonly` connections (read-only, external access off).
