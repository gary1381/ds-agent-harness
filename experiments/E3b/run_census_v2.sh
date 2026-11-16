#!/bin/sh
# E3b v2 census (docs/experiments/E3b/PREREGISTRATION.md): checks the frozen digests, then runs every slot once and
# retries failed slots once (the runner's rule). k comes from the pre-registration's "**k = N**" line.
#   systemd-run --user --unit dsh-e3b-v2 --property=CPUAffinity=4,5 --working-directory=$PWD \
#       sh experiments/E3b/run_census_v2.sh
cd "$(dirname "$0")/../.." || exit 1
date '+E3b v2 census starts %F %H:%M'
K=$(.venv/bin/python - <<'PY'
import hashlib, re, sys
from pathlib import Path
pre = Path("docs/experiments/E3b/PREREGISTRATION.md").read_text()
rows = re.findall(r"^\| `([^`]+)` \| `([0-9a-f]{64})` \|$", pre, re.M)
bad = [f for f, h in rows if hashlib.sha256(Path(f).read_bytes()).hexdigest() != h]
k = re.search(r"^\*\*k = ([23])\*\*", pre, re.M)
print(f"{len(rows)} frozen inputs, {len(bad)} changed: {bad}", file=sys.stderr)
if bad or len(rows) < 15 or not k:
    sys.exit(1)
print(k.group(1))
PY
) || exit 1
.venv/bin/python experiments/E3b/run_v2.py --out experiments/E3b/runs/v2 --reps "$K"
.venv/bin/python experiments/E3b/run_v2.py --out experiments/E3b/runs/v2 --reps "$K"
date '+E3b v2 census done %F %H:%M'
