#!/usr/bin/env bash
# E4 after the census: the pre-registered analysis, then the exploratory ones, in the order the report uses
# them. Run from the repository root on 2 cores:  taskset -c 4,5 bash experiments/E4/post_run.sh <step>
# Steps are separate because labelling needs the designer between them.
set -euo pipefail
PY=.venv/bin/python
R=experiments/E4/runs
D=docs/experiments/E4
AF=experiments/E4/questions/answer_form.json
case "${1:-}" in
  analyze)      # pre-registered: M1-M4, R1-R4, by form, S2 delivery, second-opinion rules (t2n vs pair)
    $PY experiments/E1/analyze.py --runs-root $R --run main --rules e4 --answer-form $AF \
        --semantic experiments/E4/context/semantic_model.yaml --prefix e4 --figdir $D ;;
  sensitivity)  # pre-registered: R1-R4 without the nested pairs and without the two questions closest to E3a
    $PY experiments/E4/sensitivity.py --out $D/sensitivity.md ;;
  sheets)       # one sheet per wrong attempt for the designer, and the blind 30% sample
    $PY experiments/E1/label_sheet.py --runs-root $R --run main --questions experiments/E4/questions/bank.yaml
    $PY experiments/E1/label_sheet.py --runs-root $R --run main --questions experiments/E4/questions/bank.yaml --blind ;;
  blind)        # Haiku labels the blind sample with the retail meanings (same world as E3a)
    $PY experiments/E1/run_blind_labels.py --runs-root $R --run main --world e3a ;;
  holds)        # why arm H held correct answers and passed wrong ones
    $PY experiments/E1b/hold_reasons.py --runs-root $R --run main --answer-form $AF --out $D/hold_reasons.md
    $PY experiments/E1b/delivery_match.py --runs-root $R --run main --out $D/delivery_match.md ;;
  agreement)    # exploratory: the grader's (alias-normalised) reading of agreement beside R4's raw one
    $PY experiments/H_v04/agreement_probe.py --runs E4 --out $D/agreement_E4.md ;;
  *) echo "usage: $0 analyze|sensitivity|sheets|blind|holds|agreement" >&2; exit 2 ;;
esac
