# Report presentation terminology

User-facing GM 3.0 PDF reports use common fantasy-football language while preserving the underlying technical JSON field names for auditability.

Preferred presentation terms:
- state-aware utility -> Overall Team Fit
- market dynasty / dynasty value -> Long-Term Trade Value
- franchise value / strategic value -> Value to This Team
- title equity -> Championship Odds
- liquidity -> Trade Flexibility
- break-glass value -> Resale Safety
- roster resolution -> Roster Impact
- state-aware winner -> Overall Winner
- one-sided state-aware -> Clear Winner

The model and JSON schemas are unchanged; this is a presentation-layer policy only.


## Narrative standard

Plain English is not only a terminology substitution. Every analytical report must explain the football meaning of the output.

Required presentation order:
1. **Bottom line** — what the model concludes.
2. **Why** — the main factors that drive the conclusion.
3. **Number context** — material numbers must include a baseline, before/after comparison, league rank, relative size, or plain-language magnitude.
4. **Implication / next step** — what the manager should do with the information.

Decision reports add:
- why the recommended choice makes sense;
- why the rejected choice is weaker;
- why a better alternative is better, when one exists;
- what new information or changed terms could alter the recommendation.

Examples:
- Avoid: `Championship odds: +0.031`
- Prefer: `Championship odds rise from 11.4% to 14.5% (+3.1 percentage points).`
- Avoid: `Long-Term Trade Value: +1,200`
- Prefer: `Long-Term Trade Value improves by 1,200, a meaningful gain relative to the value being surrendered.`
- Avoid: `Contender score: 0.71`
- Prefer: `This roster grades as a contender: it is strong enough to compete now, although it is not the league's clear favorite.`

The renderer may interpret magnitude, direction, rank and trade-offs already present in model output. It may not create a new score, silently reweight model factors, or override the model's recommendation.

Page count is not a hard constraint. Use the fewest pages that preserve clarity; a second page is preferable to stripping out the explanation.
