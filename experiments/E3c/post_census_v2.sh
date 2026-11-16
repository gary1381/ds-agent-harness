#!/bin/sh
# E3c v2 after the census (PREREGISTRATION.md, DEVIATIONS 11-07 23:41 and 23:49): waits for dsh-e3c-v2 to end, then
# writes the blind sheets, labeller 1 (Luna), labeller 2 (Haiku) and the meaning-check replay (Haiku, Luna). The
# adjudication (a fresh Claude subagent), `label_v2.py final`, the frozen analysis and the scoring follow by hand.
#   systemd-run --user --unit dsh-e3c-post --property=CPUAffinity=6,7 --working-directory=$PWD \
#       sh experiments/E3c/post_census_v2.sh
cd "$(dirname "$0")/../.." || exit 1
while systemctl --user is-active -q dsh-e3c-v2; do sleep 60; done
RUN=experiments/E3c/runs/v2
date '+post-census starts %F %H:%M'
.venv/bin/python experiments/E3c/label_v2.py sheets $RUN || exit 1
.venv/bin/python experiments/E3c/luna_label_v2.py $RUN &
LUNA=$!
.venv/bin/python experiments/E3c/label_v2.py haiku $RUN
H=$?
wait $LUNA
L=$?
date "+labels done %F %H:%M (haiku exit $H, luna exit $L)"
.venv/bin/python experiments/E3c/meaning_check_v2.py run $RUN --model haiku &
.venv/bin/python experiments/E3c/meaning_check_v2.py run $RUN --model luna
wait
date '+meaning-check replay done %F %H:%M'
