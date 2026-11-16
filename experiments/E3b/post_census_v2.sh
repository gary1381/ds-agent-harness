#!/bin/sh
# E3b v2 after the census (PREREGISTRATION.md, DEVIATIONS 11-08): waits for dsh-e3b-v2, then every step in order.
# Model calls by API (spend recorded by budget.py), except the gate replay (the census's CLI path).
#   systemd-run --user --unit dsh-e3b-post --property=CPUAffinity=4,5 --working-directory=$PWD \
#       sh experiments/E3b/post_census_v2.sh
cd "$(dirname "$0")/../.." || exit 1
while systemctl --user is-active -q dsh-e3b-v2; do sleep 60; done
RUN=experiments/E3b/runs/v2
PY=.venv/bin/python
date '+post-census starts %F %H:%M'
$PY experiments/E3b/replay_gate_v2.py --run $RUN || exit 1
date '+gate replay done %F %H:%M'

label() {   # typing, sealed truth, blind labels, adjudication, final labels for one run directory
    $PY experiments/E3b/type_numbers.py --run "$1" || return 1
    $PY experiments/E3b/truth_v2.py --run "$1" --sealed --evaluator-after-census || return 1
    $PY experiments/E3b/label_v2.py sheets "$1" || return 1
    $PY experiments/E3b/luna_label_v2.py "$1" & L=$!
    $PY experiments/E3b/label_v2.py haiku "$1"; H=$?
    wait $L; LE=$?
    [ $H -eq 0 ] && [ $LE -eq 0 ] || return 1
    $PY experiments/E3b/label_v2.py adjudicate "$1" || return 1
    $PY experiments/E3b/adjudicate_terra.py "$1" || return 1
    $PY experiments/E3b/label_v2.py final "$1" || return 1
}

label $RUN || exit 1
date '+labels done %F %H:%M'
$PY experiments/E3b/analyze_v2.py $RUN || exit 1
date '+analysis done %F %H:%M'
$PY experiments/E3b/harm_v2.py build --run $RUN || exit 1
label $RUN/first || exit 1
$PY experiments/E3b/harm_v2.py report --run $RUN || exit 1
date '+harm done %F %H:%M'
