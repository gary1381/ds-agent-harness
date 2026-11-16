"""Designer adjudication on top of the labelling subagents' parts (E1b), keyed by attempt so it carries
to the final sheet ids.

    python3 experiments/E1b/adjudicate_labels.py experiments/E1b/runs/main/labels
LABELS_DIR holds index_designer.json and designer_parts/*.json; writes designer_labels.json there
(sheet order) and prints what changed.

Rules (each from the subagents' ambiguity reports, applied to every sheet they cover):
  A1 C-H11: the truth counts only tickets of subscribers active on 1 Feb (682/3,497); the wording
     says "total tickets", and every agent in every arm counted all 730 non-test tickets.
     -> E-other, note starts "question?:", harness_induced false (unless the agent's own boundary error,
     E-time, comes first in the chain).
  A2 start-of-month boundary in the active-at-start filter (started_at <= month start; the next month's
     start; as-known-at updated_at views) -> E-time, as in E1 (its 33 Codex boundary errors).
  A3 a full-year answer keyed with month "2025" (values exact) -> note starts "grader?:".
"""
import json
import re
import sys
from pathlib import Path

lab = Path(sys.argv[1])                                     # e.g. experiments/E1b/runs/main/labels
idx = {x["sheet"]: x for x in json.loads((lab / "index_designer.json").read_text())}
labels = {}
for p in sorted((lab / "designer_parts").glob("*.json")):
    for r in json.loads(p.read_text()):
        labels[r["sheet"]] = r
BOUNDARY = re.compile(r"started_at\s*<=|<= ?(the )?(month start|boundary|bom)|00:00 on the 1st|boundary-instant|"
                      r"as[- ]known|updated_at|month_start \+ 1 month|subscribed during the month", re.I)
YEAR_KEY = re.compile(r"month (key )?['\"]2025['\"]")
changed = []
for sid, r in labels.items():
    x, before = idx[sid], dict(r)
    if x["qid"] == "C-H11" and r["label"] != "E-time":      # an own boundary error stays first in the chain
        r.update(label="E-other", harness_induced=False)
        if not r["note"].startswith("question?:"):
            r["note"] = ("question?: the truth counts only tickets of subscribers active on 1 Feb (682/3,497 = "
                         "0.19502); the question says total tickets, and the agent counted all non-test tickets. "
                         + r["note"])
    elif r["label"] == "E-def" and BOUNDARY.search(r["note"]):
        r["label"] = "E-time"
    if r["label"] == "E-fmt" and YEAR_KEY.search(r["note"]):
        if not r["note"].startswith("grader?:"):
            r["note"] = "grader?: " + r["note"]
    if r != before:
        changed.append((sid, x["qid"], before["label"], r["label"], r["note"][:90]))
out = [labels[s] for s in sorted(labels)]
(lab / "designer_labels.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
print(f"{len(out)} labels, {len(changed)} changed by adjudication")
for c in changed:
    print(" ", *c)
