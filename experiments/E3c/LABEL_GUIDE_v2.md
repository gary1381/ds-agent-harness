# E3c v2 labelling guide

Written 2026-11-07, before any v2 attempt. Who labels and what the sheets show were changed the same day, after
Luna's blind review of the plan, also before any v2 attempt. Every attempt that left an `answer.md` gets one label.
The label is for the reply as written, against the owner's key (`keys_v2.yaml`, not distributed in the public copy; for the six seen requests,
`tasks.py`). It does not depend on whether the reply was delivered.

## The label

`supported: true` when all of these hold:
- the reply answers what was asked, including every breakdown the request names;
- each number it states matches the reference. The tolerance is 0.5% relative, or half the last stated digit.
  Intervals by any standard method are accepted within 10% of the reference width. Rounding the way a person
  would ("about $0.77", "roughly half") is fine;
- it makes none of the unsupported claims in the key, nor any error under the key's `general` rules.

`supported: false` takes one or more codes from the key: E-select, E-fish, E-pool, E-scale, E-extrapolate,
E-nodata, E-overclaim, E-calc, E-read, E-other. Each comes with a one-sentence `why` that quotes the reply's
words.

Not errors, recorded in `why` only:
- a conservative slip, such as calling an effect unclear that the interval shows, unless the request asks
  exactly that question;
- extra correct detail;
- a missing interval where the request does not ask for certainty;
- style.

Judge the words a reader would act on. A caveat buried after a headline claim does not cure the headline. For
example, "a typical customer gains $0.60 (this is an average)" is still E-scale.

## The sheets are blind

Each sheet (`labels/sheets/S###.md`, `label_v2.py sheets`) shows the request, its key and the reply, nothing else:
no agent, arm, delivery, gate verdict, claims or numbers file. The sheets are in a shuffled order (seed 20261107).
The map from sheet to attempt (`label_index.json`) sits outside the sheet folder, and no labeller reads it. Label
the reply's text only. If a reply mentions a gate or a check, ignore that and judge its words like any other
(the analysis counts such replies by arm).

## Output

Labeller 1 writes `labels/designer_<batch>.json`; labeller 2 (Haiku) writes `labels/haiku.json`. Each holds a
JSON array, one object per sheet:

    {"sheet": "S001", "supported": false, "codes": ["E-scale"], "why": "<one sentence>"}

`label_v2.py` checks every file: each sheet labelled once, and codes present exactly when `supported` is false.

## Who labels (changed 2026-11-07 after Luna's blind review of the plan, before any v2 attempt)

1. **Labeller 1:** designer subagents label every sheet in batches, following this guide and the key.
2. **Labeller 2:** Haiku labels every sheet, with the same guide and key, in batches of six.
3. **Adjudication:** where the two disagree on `supported`, `label_v2.py adjudicate` writes `D###.md`: the blind
   sheet with both labels, unattributed and in random order. A fresh designer subagent decides each one in
   `labels/adjudicate/adjudications.json` (same format, sheet `D###`). It labelled no sheet and sees only the
   `D###.md` files, this guide and the key (Luna's second review: the designer could recognise its own labels).
4. **Final:** `label_v2.py final` writes `labels.json`. The label is the shared one where the two agree (codes from
   labeller 1), and the adjudicated one otherwise. Both original labels stay beside it.

The report gives agreement and κ on `supported`, overall and by arm. Arms are unmasked only for that count, after
the final labels are written.
