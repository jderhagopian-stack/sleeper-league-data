# FSFFL lower-priority non-projection audit

This branch continues the non-projection audit after the high-leverage cleanup merged in PR #79.

## Market momentum

FantasyCalc current dynasty value is the canonical external market anchor for the current private/research runtime. The same FantasyCalc row also exposes a 30-day trend. The prior runtime converted that trend into as much as a +/-6% valuation overlay and reapplied it to the current market anchor.

No archived time-ordered FantasyCalc snapshots are currently available in the repository to demonstrate that recent market movement predicts future FSFFL-relevant value after conditioning on today's market price. Because this is same-source evidence reuse rather than an independently validated signal, the governed runtime now:

- preserves the raw 30-day trend;
- preserves the former proposed adjustment as a diagnostic/counterfactual;
- assigns zero incremental valuation weight to market momentum;
- introduces no replacement coefficient;
- requires temporal held-out residual improvement before any future reintroduction.

This does not remove independent football-information overlays such as current injury, usage/snap information or explicitly sourced manual news. Those remain provisional and are scheduled for separate family-level ablation review.

## Commercial-use boundary for external market data

FantasyCalc is useful as a research/private-development market reference, but it must not be treated as an irreplaceable commercial production dependency. Its current terms restrict commercial use of FantasyCalc data without express written permission. Accordingly, FSFFL governance treats FantasyCalc-derived market values and trend fields as replaceable external-source inputs rather than model-owned facts.

Commercial-readiness policy:

- no FantasyCalc-derived field is authorized as a required commercial production dependency without an appropriate commercial permission/license;
- market-source provenance must remain explicit so FantasyCalc-derived outputs can be identified and replaced;
- market momentum remains diagnostic-only for model-quality reasons independent of licensing;
- a commercial deployment must use a source whose license permits that use, obtain express permission from FantasyCalc, or substitute an internally constructed/permissibly sourced market estimate;
- source replacement must not require changing the underlying FSFFL decision architecture.

This is a source-governance restriction, not a claim that FantasyCalc data is inaccurate. The architecture should preserve the ability to use FantasyCalc in permitted research contexts while keeping commercial deployment source-portable.
