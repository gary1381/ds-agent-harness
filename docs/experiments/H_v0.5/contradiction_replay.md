# Contradiction replay: v0.4 vs v0.5

Every recorded harness-arm submission with its question, metric_call and derive, checked by both trees' claim and shape checks (no agent re-run). A right final answer is one the census graded correct.

| run | submissions | declared | new contradiction | new on a right final answer | contradiction removed | removed on a right final answer | skipped (v0.5) |
|---|---|---|---|---|---|---|---|
| E1 | 377 | 303 | 0 | 0 | 0 | 0 | 0 |
| E1b | 816 | 656 | 0 | 0 | 0 | 0 | 9 |
| E3a | 545 | 464 | 12 | 3 | 10 | 4 | 14 |

## New in v0.5

- E3a claude H R-H07 r1 #1 (right final): the step combines every month of metric_call.months 2024-12..2025-11, but the question names 2025-01..2025-11; declare exactly those months
- E3a claude H R-H07 r2 #1 (earlier): the step combines every month of metric_call.months 2024-12..2025-11, but the question names 2025-01..2025-11; declare exactly those months
- E3a claude H R-H07 r3 #1 (earlier): the step combines every month of metric_call.months 2024-12..2025-11, but the question names 2025-01..2025-11; declare exactly those months
- E3a claude H R-L18 r1 #1 (earlier): the step combines every month of metric_call.months 2025-04..2025-09, but the question names 2025-07..2025-09; declare exactly those months
- E3a claude H R-L18 r2 #2 (earlier): the step combines every month of metric_call.months 2025-04..2025-09, but the question names 2025-07..2025-09; declare exactly those months
- E3a claude H R-L18 r3 #1 (earlier): the step combines every month of metric_call.months 2025-04..2025-09, but the question names 2025-07..2025-09; declare exactly those months
- E3a codex H R-H07 r1 #1 (earlier): the step combines every month of metric_call.months 2024-12..2025-11, but the question names 2025-01..2025-11; declare exactly those months
- E3a codex H R-H07 r2 #1 (earlier): the step combines every month of metric_call.months 2024-12..2025-11, but the question names 2025-01..2025-11; declare exactly those months
- E3a codex H R-H07 r3 #1 (earlier): the step combines every month of metric_call.months 2024-12..2025-11, but the question names 2025-01..2025-11; declare exactly those months
- E3a codex H R-L18 r1 #1 (right final): the step combines every month of metric_call.months 2025-04..2025-09, but the question names 2025-07..2025-09; declare exactly those months
- E3a codex H R-L18 r2 #1 (earlier): the step combines every month of metric_call.months 2025-04..2025-09, but the question names 2025-07..2025-09; declare exactly those months
- E3a codex H R-L18 r3 #1 (right final): the step combines every month of metric_call.months 2025-04..2025-09, but the question names 2025-07..2025-09; declare exactly those months

## Removed in v0.5

- E3a claude H R-H07 r1 #1 (right final): the question names months 2025-01..2025-12 but metric_call declares 2024-12..2025-11
- E3a claude H R-H07 r2 #1 (earlier): the question names months 2025-01..2025-12 but metric_call declares 2024-12..2025-11
- E3a claude H R-H07 r2 #2 (right final): the question names months 2025-01..2025-12 but metric_call declares 2025-01..2025-11
- E3a claude H R-H07 r3 #1 (earlier): the question names months 2025-01..2025-12 but metric_call declares 2024-12..2025-11
- E3a claude H R-H07 r3 #2 (right final): the question names months 2025-01..2025-12 but metric_call declares 2025-01..2025-11
- E3a codex H R-H07 r1 #1 (earlier): the question names months 2025-01..2025-12 but metric_call declares 2024-12..2025-11
- E3a codex H R-H07 r1 #2 (earlier): the question names months 2025-01..2025-12 but metric_call declares 2024-12..2025-11
- E3a codex H R-H07 r2 #1 (earlier): the question names months 2025-01..2025-12 but metric_call declares 2024-12..2025-11
- E3a codex H R-H07 r3 #1 (earlier): the question names months 2025-01..2025-12 but metric_call declares 2024-12..2025-11
- E3a codex H R-H07 r3 #2 (right final): the question names months 2025-01..2025-12 but metric_call declares 2024-12..2025-11
