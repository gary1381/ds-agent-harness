# E5 design: a request-coverage check for the part of the request an agent leaves out

Chosen by the designer on 2026-11-10. Not yet pre-registered.

## Why this one

E-read, "an asked part left out", is the most common error in both forecasting and experiment read-outs, and the
program gates make it worse or leave it alone:

| experiment | arm N | arm H | gate sees it? |
|---|---|---|---|
| E3c v2 (Hillstrom e-mail test) | 18 replies | 38 replies | no |
| E3b v2 (gift wholesaler forecast) | 40 replies | 49 replies | no |

In E3b it is 89 of the 168 held-out silent errors; in E3c most of the visible-to-blind shift. The E3c meaning-check
replay already caught about half of such replies with its "unanswered" question, but with 17–23% false flags on
supported replies, so it could not hold. The idea written down after E3c: the request's parts are a list a model can
extract once, from the request alone, and a program can check once the model has pointed at the sentence that covers
each part ("semantic → model, exact → program").

## The check

1. **Extract** (model, sees only the request): the numbered parts a complete reply must cover, each as a short
   phrase with a type (a figure, a breakdown, a horizon, a population, a decision, a caveat the requester asked for).
   One extraction per request, cached, never shown to the agent in arm N.
2. **Cover** (model, sees the reply and the numbered parts): for each part `covered: yes/no` and, for yes, the
   sentence or table row of the reply that covers it, quoted verbatim.
3. **Verify** (program): a `yes` whose quote is not in the reply (after normalising spaces) counts as `no`. A part
   marked `no` is a coverage gap; the check reports it, with the part's own words, as the reason.

## Phase 1: offline qualification (cheap, no agents)

Replay the check on the finished replies we already have labels for: E3b v2 (336) and E3c v2 (552). The ground truth
is each reply's final `E-read` label. **Rule, fixed before any result is looked at** (same as the E3c meaning check):
the check may be used to hold only if, with one model, it flags at most 5% of supported replies and catches at least
half of the E-read errors, against the final labels and against the other family's labels, on both experiments.
Otherwise it runs advisory (reasons shown, never holds), as the E3c meaning check does.

- Models: Luna extracts and covers; Haiku does the same independently, each with its own part lists (no
  adjudication of the lists: see PREREGISTRATION.md). Cost estimate: about 900 replies × 2 models, a few US dollars.
- Reported: per experiment and per agent; false flags on supported replies; catches on E-read; how many gaps are
  quote-failures; κ of Luna's and Haiku's part lists.

## Phase 2: live census (only if phase 1 does not rule it out)

- New blind bank (the E3b bank is spent): about 40 requests, written blind by Luna and Haiku, on a data set new to
  the agents. Held-out, as before.
- Arms: N (agent alone); H (E3b's forecast gate or E3c's read-out gate, whichever the data set needs); C (H plus the
  coverage check). Agents: Claude Code on Haiku 5.5 (API key) and Codex on GPT-6 Luna; k = 2.
- Primary rule: R1(C) < R1(H) on silent errors, paired over requests, interval below 0, without false holds above
  15%. Secondary: E-read rate by arm; harm_v2 (first answer right, final wrong) by arm.
- Cost: Claude agent about $2–4 for the census plus helpers.

## Before phase 2

- A Haiku pilot on the new bank's own requests (done for E3b's requests on 2026-11-10, see the pilot note).
- Pre-registration frozen with file digests, as for E3b and E3c, before any agent runs.
- Luna's blind review of the design and of the analysis code, as before.

## Risks

- Parts extracted from the request can be too fine (every adjective a part) or too coarse; phase 1 reports both
  kinds, and the qualification rule above decides, not the designer.
- A coverage check can push the agent to pad the reply; the primary rule counts silent errors of any kind, so padding
  that introduces other errors shows up.
