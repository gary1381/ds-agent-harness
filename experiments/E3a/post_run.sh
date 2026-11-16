#!/usr/bin/env bash
# E3a after the census: the pre-registered analysis, then the exploratory ones, in the order the report
# uses them. Run from the repository root on 2 cores:  taskset -c 4,5 bash experiments/E3a/post_run.sh <step>
# Steps are separate because labelling needs the designer between them.
set -euo pipefail
PY=.venv/bin/python
R=experiments/E3a/runs
D=docs/experiments/E3a
AF=experiments/E3a/questions/answer_form.json
case "${1:-}" in
  analyze)      # pre-registered: M1-M4, R1-R3, DIRECT vs DERIVED, byte vs S2 delivery, N vs N-orig
    $PY experiments/E1/analyze.py --runs-root $R --run main --rules e3a --control-run control \
        --answer-form $AF --prefix e3a --figdir $D ;;
  sheets)       # one sheet per wrong attempt for the designer, and the blind 30% sample
    $PY experiments/E1/label_sheet.py --runs-root $R --run main --questions experiments/E3a/questions/bank.yaml
    $PY experiments/E1/label_sheet.py --runs-root $R --run main --questions experiments/E3a/questions/bank.yaml --blind ;;
  blind)        # Haiku labels the blind sample with the retail meanings (DEVIATIONS 04:58)
    $PY experiments/E1/run_blind_labels.py --runs-root $R --run main --world e3a ;;
  holds)        # exploratory: why arm H held correct answers and passed wrong ones
    $PY experiments/E1b/hold_reasons.py --runs-root $R --run main --answer-form $AF --out $D/hold_reasons.md
    $PY experiments/E1b/delivery_match.py --runs-root $R --run main --out $D/delivery_match.md ;;
  agreement)    # exploratory (DEVIATIONS 03:40, 04:57): second-opinion rules; t2x/t2n need the v0.4 source
    DSH_SRC="${DSH_SRC:-}" $PY experiments/H_v04/agreement_probe.py --runs E3a \
        --out docs/experiments/H_v0.4/agreement_E3a.md ;;
  *) echo "usage: $0 analyze|sheets|blind|holds|agreement" >&2; exit 2 ;;
esac
