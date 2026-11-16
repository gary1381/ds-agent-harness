# E3c probe: the Hillstrom e-mail experiment before any design

2026-11-07 (night), designer's first look. Kevin Hillstrom's MineThatData challenge file (2008-03-20;
64,000 customers who bought in the last 12 months, randomly split into thirds: men's e-mail, women's
e-mail, no e-mail; visit / conversion / spend over the following two weeks). **No licence stated by
the author: research use only, never redistributed, never in a commercial benchmark** (Gary,
2026-11-06). The data stays in the local dataset folder; this note carries aggregates only.

## Intent-to-treat effects (difference in means vs no e-mail, normal 95% interval)

| outcome | men's e-mail | women's e-mail |
|---|---|---|
| visit (share) | +0.0766 [+0.0700, +0.0832] | +0.0452 [+0.0389, +0.0516] |
| conversion (share) | +0.0068 [+0.0050, +0.0086] | +0.0031 [+0.0015, +0.0047] |
| spend per customer ($) | +0.77 [+0.49, +1.05] | +0.42 [+0.17, +0.68] |
| spend per buyer ($) | −0.48 [−23.1, +22.2] | +7.89 [−15.8, +31.6] |

Arms are balanced on every pre-treatment column (recency, history, men's / women's buyer, newbie,
web channel: differences in the second or third decimal).

## What an analyst can get wrong here

- **Spend per buyer is not an effect.** Conditioning on conversion selects different customers in
  each arm; the per-buyer comparison says nothing causal (and its interval spans ±$23). The causal
  spend effect is per customer assigned.
- **Spend is extremely sparse**: 578 buyers of 64,000 (0.9%); the 99th percentile of spend is $0,
  the maximum $499. Means are driven by a few hundred orders; medians are all zero; a t-interval is
  fine at this size, a "typical customer" claim is not.
- **Heterogeneity invites subgroup stories.** Visit rate by past purchases (men's-only / women's-only
  / both buyers): the men's e-mail lifts all three (0.166 vs 0.096, 0.169 vs 0.100, 0.315 vs 0.181);
  the women's e-mail lifts women's-only buyers (0.170) but barely men's-only buyers (0.111 vs 0.100).
  With 3 outcomes × 2 treatments × many segments, unadjusted subgroup "wins" are expected by chance.
- **Two treatments, one control**: a pooled "e-mail vs none" effect hides that the men's e-mail is
  about 1.7× stronger on visits; comparing the two e-mails with each other is a different question
  from either vs control.

## What this means for E3c

Truth is the randomisation: ITT differences and intervals computed from the file, plus fixed owner
rules for which outcomes and segments are pre-specified. The harness side is the readout gate
(`dsh readout`, E2) and the uplift tools, both written for the subscription world, so E3c needs the
same kind of world config v0.3 gave the analytics tools. Not started before E3a.
