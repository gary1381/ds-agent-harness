# E3d results (144 attempts)

## held out tasks

| arm | n | P1 silent error | P2 false hold | sound outcome | P5 sound report (hold tasks) | P6 files outside the paths | $ per sound (Claude) | tokens per sound (Codex) | median min |
|---|---|---|---|---|---|---|---|---|---|
| N | 36 | 25.0% [2.8, 58.3] | 10.0% [0.0, 25.0] | 69.4% [36.1, 94.4] | 88.9% [83.3, 100.0] | 22.2% | $0.13 | 445k | 1.5 |
| H | 36 | 2.8% [0.0, 8.3] | 5.9% [0.0, 16.7] | 77.8% [63.9, 88.9] | 66.7% [50.0, 83.3] | 2.8% | $0.09 | 528k | 2.2 |
| NI | 36 | 16.7% [0.0, 50.0] | 0.0% [0.0, 0.0] | 83.3% [50.0, 100.0] | 100.0% [100.0, 100.0] | 16.7% | $0.12 | 434k | 1.5 |

P1(H) − P1(N), paired over tasks: -22.2% [-47.2, -2.8]
P1(H) − P1(NI), paired over the tasks NI ran: -13.9% [-41.7, 0.0]
On the serve-truth tasks only: P1 N 44.4%, H 5.6%; difference -38.9% [-83.3, 0.0]

| task, arm | sound-delivery | silent-error | sound-hold | hold-wrong-reason | false-hold | failed | unlabelled | defensible-hold |
|---|---|---|---|---|---|---|---|---|
| y2009 N | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| y2009 H | 5 | 0 | 0 | 0 | 1 | 0 | 0 | 0 |
| y2009 NI | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| y2010 N | 3 | 2 | 0 | 0 | 1 | 0 | 0 | 0 |
| y2010 H | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| y2010 NI | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| y2211 N | 0 | 1 | 5 | 0 | 0 | 0 | 0 | 0 |
| y2211 H | 0 | 0 | 3 | 3 | 0 | 0 | 0 | 0 |
| y2211 NI | 0 | 0 | 6 | 0 | 0 | 0 | 0 | 0 |
| y2212 N | 0 | 0 | 6 | 0 | 0 | 0 | 0 | 0 |
| y2212 H | 0 | 0 | 4 | 2 | 0 | 0 | 0 | 0 |
| y2212 NI | 0 | 0 | 6 | 0 | 0 | 0 | 0 | 0 |
| y2302 N | 0 | 6 | 0 | 0 | 0 | 0 | 0 | 0 |
| y2302 H | 5 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| y2302 NI | 0 | 6 | 0 | 0 | 0 | 0 | 0 | 0 |
| y2309 N | 0 | 0 | 5 | 1 | 0 | 0 | 0 | 0 |
| y2309 H | 0 | 0 | 5 | 1 | 0 | 0 | 0 | 0 |
| y2309 NI | 0 | 0 | 6 | 0 | 0 | 0 | 0 | 0 |

## seen tasks

| arm | n | P1 silent error | P2 false hold | sound outcome | P5 sound report (hold tasks) | P6 files outside the paths | $ per sound (Claude) | tokens per sound (Codex) | median min |
|---|---|---|---|---|---|---|---|---|---|
| N | 18 | 27.8% [0.0, 50.0] | 10.0% [0.0, 25.0] | 66.7% [50.0, 100.0] | 50.0% [50.0, 50.0] | 22.2% | $0.17 | 1028k | 2.0 |
| H | 18 | 11.1% [0.0, 16.7] | 0.0% [0.0, 0.0] | 88.9% [83.3, 100.0] | 100.0% [100.0, 100.0] | 5.6% | $0.14 | 818k | 2.8 |

P1(H) − P1(N), paired over tasks: -16.7% [-50.0, 16.7]
On the serve-truth tasks only: P1 N 16.7%, H 16.7%; difference 0.0% [-16.7, 16.7]

| task, arm | sound-delivery | silent-error | sound-hold | hold-wrong-reason | false-hold | failed | unlabelled | defensible-hold |
|---|---|---|---|---|---|---|---|---|
| jan N | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| jan H | 5 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| janx N | 3 | 2 | 0 | 0 | 1 | 0 | 0 | 0 |
| janx H | 5 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| sep N | 0 | 3 | 3 | 0 | 0 | 0 | 0 | 0 |
| sep H | 0 | 0 | 6 | 0 | 0 | 0 | 0 | 0 |

## Decision rules (held-out tasks)

| rule | met |
|---|---|
| R1 | **yes** |
| R2 | no |
| R3 | no |

## Sensitivity (held-out tasks)

| reading | P1 N | P1 H | P1 H − N [95%] | P2 H | R1 | R2 | R3 |
|---|---|---|---|---|---|---|---|
| without y2212 | 30.0% | 3.3% | -26.7% [-56.7, -3.3] | 5.9% | yes | no | no |
| y2010 defensible hold | 25.0% | 2.8% | -22.2% [-47.2, -2.8] | 5.9% | yes | no | no |
| harmless ci not unsound | 2.8% | 2.8% | 0.0% [-8.3, 8.3] | 5.9% | no | no | no |
| luna unaudited | 25.0% | 2.8% | -22.2% [-47.2, -2.8] | 5.9% | yes | no | no |

Designer vs Haiku (blind) on whether a hold names the problem: 95% of 62, kappa 0.80 (by agent: claude 100% of 33, kappa 1.00; codex 90% of 29, kappa 0.75); disagreements: codex/y2309-H-r2, codex/y2309-NI-r1, codex/y2309-H-r1

Designer vs Luna (blind) on whether a hold names the problem: 94% of 62, kappa 0.74 (by agent: claude 100% of 33, kappa 1.00; codex 86% of 29, kappa 0.68); disagreements: codex/y2309-H-r2, codex/sep-H-r2, codex/y2309-NI-r1, codex/y2309-H-r1
