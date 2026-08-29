# FSFFL lower-priority non-projection audit

This branch continues the non-projection audit after the high-leverage cleanup merged in PR #79.

## Market momentum

FantasyCalc current dynasty value is the canonical external market anchor. The same FantasyCalc row also exposes a 30-day trend. The prior runtime converted that trend into as much as a +/-6% valuation overlay and reapplied it to the current market anchor.

No archived time-ordered FantasyCalc snapshots are currently available in the repository to demonstrate that recent market movement predicts future FSFFL-relevant value after conditioning on today's market price. Because this is same-source evidence reuse rather than an independently validated signal, the governed runtime now:

- preserves the raw 30-day trend;
- preserves the former proposed adjustment as a diagnostic/counterfactual;
- assigns zero incremental valuation weight to market momentum;
- introduces no replacement coefficient;
- requires temporal held-out residual improvement before any future reintroduction.

This does not remove independent football-information overlays such as current injury, usage/snap information or explicitly sourced manual news. Those remain provisional and are scheduled for separate family-level ablation review.
