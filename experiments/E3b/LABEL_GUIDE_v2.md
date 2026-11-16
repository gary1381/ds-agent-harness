# E3b v2 labelling guide

Written 2026-11-08, before any held-out v2 attempt and before the sealed months were read. Every attempt that left
an `answer.md` gets one label for the reply as written, against the owner's key (`keys_v2.yaml`). The label does
not depend on whether the reply was delivered.

## Two parts: meaning by labellers, accuracy by program

Labellers judge **meaning only**: did the reply answer what was asked, on a sound definition, without claiming what
the data cannot show? Whether a forecast came true is decided by the truth script (`truth_v2.py`, rules in the key's
`program` section), never by a labeller. The sheets therefore show no realised value from September to November
2025. They do show reference figures about the past (before 2025-09-01) where the key has them, so that a wrong past
figure can be caught. Most requests have no reference figure: the truth script then checks every typed past figure
against the warehouse (program E-calc), so a labeller codes E-calc only against a shown reference figure or for
arithmetic that is wrong on its face (parts that do not add up to the stated total).

A reply's final codes are the union of its label codes and its program codes (E-gross, typed E-overclaim, E-calc on
a typed past figure).

## The label

`supported: true` when all of these hold:
- the reply answers what was asked, including every part and grain the request names (a range where asked, a ranked
  list, per-item or per-week figures, a yes or no, a comparison);
- its definitions are sound (key, `general`), or stated and reasonable where the request leaves room;
- every past figure it states matches the reference, where one is shown, within 0.5% relative, or half the last
  stated digit;
- it makes none of the unsupported claims in the key, nor any error under `general`.

`supported: false` takes one or more codes: E-causal, E-nodata, E-read, E-define, E-extrapolate, E-overclaim,
E-calc, E-other (definitions at the top of the key). Each comes with a one-sentence `why` that quotes the reply's
words.

Not errors, recorded in `why` only:
- a forecast that may turn out wrong: accuracy is the program's job;
- extra correct detail, or a stand-in clearly labelled as one;
- a missing range where the request does not ask for certainty;
- style.

Judge the words a reader would act on. A caveat buried after a headline claim does not cure the headline: "the
discount will bring 300 extra orders (this assumes past patterns hold)" is still E-causal.

## The sheets are blind

Each sheet (`labels/sheets/S###.md`) shows the request, its key, the past reference figures and the reply, nothing
else: no agent, arm, delivery, gate verdict, claims file or realised outcome. Files the reply delivers are shown as
their name, columns and first 10 rows. The sheets are in a shuffled order (seed 20261108); the map from sheet to
attempt sits outside the sheet folder and no labeller reads it. If a reply mentions a gate or a check, ignore that
and judge its words like any other.

## Output

Labeller 1 (Luna) writes `labels/designer_luna_<batch>.json`; labeller 2 (Haiku) writes `labels/haiku.json`. Each
holds a JSON array, one object per sheet:

    {"sheet": "S001", "supported": false, "codes": ["E-causal"], "why": "<one sentence>"}

Every sheet is labelled once, and codes are present exactly when `supported` is false.

## Who labels

1. **Labeller 1:** GPT-6 Luna, every sheet, in batches (Gary, 11-07: Luna wherever possible).
2. **Labeller 2:** Haiku 5.5, every sheet, in batches of six.
3. **Adjudication:** where the two disagree on `supported`, GPT-6.1 Terra decides (Gary, 11-08; neither labeller's
   family), through the OpenAI API, one blind sheet at a time with both labels unattributed and in random order,
   this guide and the key (`adjudicate_terra.py`).
4. **Final:** the shared label where the two agree (codes from labeller 1), the adjudicated one otherwise.

Luna is also the Codex agent in the census, so agreement is reported by agent (as in E3d and E3c).

## Untyped numbers (program support, not a label)

A number in a reply that no claim in its `claims.json` covers is typed after the census by Luna, blind to agent,
arm and outcome: from the reply's text and CLAIMS.md alone, Luna writes the claim the sentence states, with the
sentence quoted in `says`. A program checks that the quote is verbatim in the reply and that the claim realises
without error; a claim that fails either check is reported as "not typed" and gets no program code. Typed this way,
the number is graded by the same program rules as the reply's own claims. The analysis reports how many numbers each
arm left untyped.
