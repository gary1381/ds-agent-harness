# E4 deviations from the pre-registration

Every change made after [PREREGISTRATION.md](PREREGISTRATION.md) became final, with its time (CST, from
`date`) and reason. Nothing here changes a decision rule after any result was seen.

| time | change | reason |
|---|---|---|
| 2026-11-07 09:15 | added `experiments/E4/sensitivity.py` (sha256 `e2328a20f58187af…`): runs the frozen `analyze.py --rules e4` unchanged on copies of the main run without F-L39, F-H39, F-H30 and without F-H36, F-H37 | the pre-registered sensitivity analyses had no code in the frozen inputs; written and tested on a scratch copy of the first 53 records with output discarded, before any E4 result was read; no rule or input changed |
