"""Carry designer labels made on an early sheet set onto the final sheet ids (matched by attempt key).

    python3 experiments/E1b/carry_labels.py EARLY_LABELS_DIR FINAL_LABELS_DIR
EARLY dir holds index_designer.json + designer_labels.json (else designer_parts/part*.json); FINAL dir holds index_designer.json.
Writes FINAL/designer_parts/carried.json (labels renamed to final sheet ids) and prints the final
sheets that still need labels.
"""
import json
import sys
from pathlib import Path

early, final = Path(sys.argv[1]), Path(sys.argv[2])
key = lambda x: (x["agent"], x["arm"], x["qid"], x["rep"])  # noqa: E731
e_idx = {x["sheet"]: key(x) for x in json.loads((early / "index_designer.json").read_text())}
labels = {}
srcs = ([early / "designer_labels.json"] if (early / "designer_labels.json").exists()      # adjudicated set
        else sorted((early / "designer_parts").glob("part*.json")))
for p in srcs:
    for r in json.loads(p.read_text()):
        labels[e_idx[r["sheet"]]] = r
f_idx = json.loads((final / "index_designer.json").read_text())
carried, todo = [], []
for x in f_idx:
    r = labels.get(key(x))
    if r:
        carried.append({**r, "sheet": x["sheet"]})
    else:
        todo.append(x["sheet"])
lost = set(labels) - {key(x) for x in f_idx}
(final / "designer_parts").mkdir(exist_ok=True)
(final / "designer_parts" / "carried.json").write_text(json.dumps(carried, indent=1, ensure_ascii=False))
print(f"{len(carried)} carried, {len(todo)} to label, {len(lost)} early labels with no final sheet: {sorted(lost)}")
print("todo:", " ".join(todo))
