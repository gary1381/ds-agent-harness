#!/bin/sh
# E3d census (docs/experiments/E3d/PREREGISTRATION.md, "Run plan"). One attempt at a time; run it under
#   systemd-run --user --unit dsh-e3d-census --property=CPUAffinity=4,5 --working-directory=$PWD experiments/E3d/run_census.sh
# Each run_dbt.py call skips slots that already have an OK record or two tries, so the last two calls re-run a failed
# slot once and fill any slot a crash left out. No `set -e`: a crash in one phase must not stop the next.
cd "$(dirname "$0")/../.." || exit 1
OUT=experiments/E3d/runs/census
HELD="y2009 y2010 y2211 y2212 y2302 y2309"
PY=.venv/bin/python
$PY experiments/E3d/run_dbt.py --out $OUT --tasks $HELD --arms N H --reps 3
$PY experiments/E3d/run_dbt.py --out $OUT --tasks jan janx sep --arms N H --reps 3
$PY experiments/E3d/run_dbt.py --out $OUT --tasks $HELD --arms NI --reps 3
$PY experiments/E3d/run_dbt.py --out $OUT --tasks $HELD --arms N H NI --reps 3
$PY experiments/E3d/run_dbt.py --out $OUT --tasks jan janx sep --arms N H --reps 3
date '+census done %F %H:%M'
