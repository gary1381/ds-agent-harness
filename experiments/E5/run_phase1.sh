#!/bin/sh
# E5 phase 1 in order (docs/experiments/E5/PREREGISTRATION.md); resumable, every call cached.
#   systemd-run --user --unit dsh-e5-phase1 --property=CPUAffinity=4,5 --working-directory=$PWD sh experiments/E5/run_phase1.sh
cd "$(dirname "$0")/../.." || exit 1
PY=.venv/bin/python
$PY experiments/E5/coverage.py requests || exit 1
for m in luna haiku; do $PY experiments/E5/coverage.py extract --model $m || exit 1; done
date '+extract done %F %H:%M'
$PY experiments/E5/coverage.py cover --model luna & L=$!
$PY experiments/E5/coverage.py cover --model haiku; H=$?
wait $L; LE=$?
[ $H -eq 0 ] && [ $LE -eq 0 ] || exit 1
date '+cover done %F %H:%M'
$PY experiments/E5/coverage.py score || exit 1
date '+score done %F %H:%M'
