#!/bin/sh
# E3c v2 census (docs/experiments/E3c/PREREGISTRATION.md): waits for the E3d census to finish (one experiment at a
# time on two cores), checks the frozen digests, then runs every slot once and retries failed slots once.
#   systemd-run --user --unit dsh-e3c-v2 --property=CPUAffinity=4,5 --working-directory=$PWD \
#       sh experiments/E3c/run_census_v2.sh
#   --now: start at once, beside the E3d census (Gary 11-07 23:32: both may run, cores 4-5 and 6-7)
cd "$(dirname "$0")/../.." || exit 1
if [ "$1" != "--now" ]; then
    while systemctl --user is-active -q dsh-e3d-census; do sleep 60; done
fi
date '+E3c v2 census starts %F %H:%M'
.venv/bin/python - <<'EOF' || exit 1
import hashlib, re, sys
from pathlib import Path
rows = re.findall(r"^\| `([^`]+)` \| `([0-9a-f]{64})` \|$", Path("docs/experiments/E3c/PREREGISTRATION.md").read_text(), re.M)
bad = [f for f, h in rows if hashlib.sha256(Path(f).read_bytes()).hexdigest() != h]
print(f"{len(rows)} frozen inputs, {len(bad)} changed: {bad}")
sys.exit(1 if bad or len(rows) < 12 else 0)
EOF
.venv/bin/python experiments/E3c/run_v2.py --out experiments/E3c/runs/v2
.venv/bin/python experiments/E3c/run_v2.py --out experiments/E3c/runs/v2
date '+E3c v2 census done %F %H:%M'
