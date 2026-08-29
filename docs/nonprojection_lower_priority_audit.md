# FSFFL non-projection audit closure

This document closes the lower-priority non-projection audit that began after PR #79. Projection means, projection uncertainty, source weighting and position-specific projection formulas remain outside this workstream.

## Market momentum

FantasyCalc current dynasty value already reflects the market's present view. Reapplying its same-source 30-day trend as another valuation bonus was therefore structurally suspect. The governed runtime now preserves the trend and the prior proposed adjustment as diagnostics but gives momentum zero incremental weight.

The Stats Guy historical challenger test now provides out-of-sample support for that choice. Across four sequential monthly windows, momentum remained slightly negative after controlling for current player value. In a strict final-window holdout, adding momentum slightly worsened both MAE and RMSE versus a current-value-only baseline. Momentum remains available for future retesting but is not a production value adder.

## External player-market source

FantasyCalc remains a useful research benchmark, but its public terms do not authorize it as a required commercial dependency without separate permission. Stats Guy is the primary replacement candidate because it provides a broad dynasty market, historical snapshots, Sleeper IDs and documented commercial API use subject to its terms and attribution requirements.

Current cross-provider agreement is very high: 376 matched players, about 94.5% coverage of the frozen FantasyCalc board, approximately 0.974 Spearman rank agreement, 96% Top-25 overlap, 98% Top-50 overlap and 99% Top-100 overlap.

A first downstream player-market bake-off using a single median scale conversion was rejected because the two providers have differently shaped raw value curves; the conversion produced obviously inflated elite values and therefore confounded provider disagreement with a scale artifact. The final bake-off instead uses nonparametric quantile mapping: Stats Guy determines relative ordering while the existing FSFFL cross-sectional player-value distribution is preserved. This introduces no fitted economic coefficient and isolates the provider-ranking signal. FantasyCalc is a regression reference, not an answer key: different downstream decisions are not automatically failures.

Because the repository does not contain contemporaneous historical FantasyCalc boards, no historical FantasyCalc-vs-Stats Guy winner claim is made. A pseudo-backtest using today's FantasyCalc values against old outcomes is explicitly prohibited.

## Package economics

The package-value curve has material upstream leverage. An exhaustive 12-team current-state sensitivity changed the top target for 8 teams and the top package for 9 teams; worst-case Top-10 target overlap fell to 50% and package overlap to 0%.

There is not enough contemporaneous transaction evidence to fit an authoritative replacement consolidation curve. The governed solution is therefore robust multi-curve candidate discovery: steep, shallow and neutral package shapes are all searched, and candidates are prioritized by cross-curve presence and worst-case rank before GM3 simulation. No single provisional curve is allowed to hide a viable candidate. The generic roster-slot percentage penalty remains removed because actual roster legalization and forced cuts already price that burden directly.

## GM3 governance

GM3's operating-season adapter was capable of reintroducing positive future-pick uncertainty value and an own-pick control bonus after those assumptions had already been removed upstream. The canonical governed GM3 entry point now applies dynamic season patching first, then non-projection governance, then robust package discovery, and only afterward installs counterfactual simulation. The production runner uses this governed entry point.

Forecast uncertainty remains uncertainty rather than an automatic positive value increment. Own-pick control remains diagnostic rather than an intrinsic-value bonus. Existing future-pick market anchoring from Stats Guy is preserved.

## Roster interaction and behavioral factors

The tested roster-interaction overlay did not change recommendation order, acceptance bands or the focal action in the validated case. Behavioral acceptance information remains a bounded secondary signal; it does not receive a second independent ranking premium, and the former hard eligibility gate is removed. The remaining behavioral coefficients are provisional, but their observed decision leverage is not high enough to justify an extended calibration project without better accepted/rejected-offer evidence.

## Current-season scoring overlap

Championship probability, playoff probability, expected wins and expected points are correlated outputs from the same season simulation. Their exchange rates remain provisional and should not be presented as empirically calibrated independent causal effects.

However, the available retained-finalist sensitivity did not change the leading trade family when the current-season objective was reduced to title-only, title-plus-playoff, when points were removed, or when title probability was removed. This does not prove the coefficients are correct, but it shows low observed ranking leverage in the tested decision set. Without historical or out-of-sample evidence identifying a superior replacement, no additional coefficient surgery is justified now. The overlap risk is documented and deferred to the final refinement layer rather than blocking use of the model.

## Closure standard

The lower-priority audit is considered closed when the clean final branch passes its non-projection governance, package-curve, market-momentum, Stats Guy market-source and governed GM3 regression checks on the exact merge head. Remaining provisional low-leverage coefficients stay documented and become candidates for future recalibration only when new historical/out-of-sample evidence can improve decisions rather than merely produce different numbers.
