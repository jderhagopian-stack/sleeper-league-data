# FSFFL Architecture

## Governing rule

Shared Core owns reusable concepts. Applications own application-specific reasoning.

A component can be canonical within one application without becoming a platform-wide shared tool. Historical implementations may remain for reproducibility, but they may not silently retain current decision authority.

## Shared Core

Reusable facts, estimates, and mechanics:

- projections and uncertainty
- valuation and future-pick economics
- league rules
- lineup optimization
- season simulation mechanics
- Behavioral Intelligence
- historical fact reconstruction
- roster/team-state primitives
- roster-interaction primitives
- shared decision-utility primitives used by Trade Decision and GM3 Team Improvement
- shared data access and standardized metrics

Promotion to Shared Core requires either genuinely domain-generic behavior or a real second application consumer.

Shared Core ownership is conceptual, not a requirement that every concept already live in a perfectly isolated file. During migration, a coarse retained mechanics host may physically contain reusable Core mechanics without becoming a current application authority. In particular, `build_fsffl_gm_engine.py` remains the audited physical host for valuation and future-pick economics while GM3 treats it only as a legacy mechanics provider. This is intentional transitional hosting, not a reason to split the file function-by-function.

## Applications

### Trade Decision
Owns trade search, trade-specific BI interpretation, bilateral feasibility, negotiation ranking, candidate organization, option comparison, and final trade recommendation policy.

### GM3
Owns franchise-management reasoning. GM2.2 may remain as an internal legacy mechanics provider, but GM3 governance and application orchestration are current authority.

GM3 consumes Draft Intelligence and Breakout / Sleeper Intelligence outputs rather than owning those models.

### Draft Intelligence
Owns prospect inputs, prospect feature enrichment, prospect scoring, market-vs-model comparison, and the prospect board.

### Breakout / Sleeper Intelligence
Owns emerging-value detection for post-rookie and exceptional veteran cases. Rookie prospect evaluation belongs to Draft Intelligence.

### What-If / Alternate History
One Counterfactual application family with two modes:
- Forward What-If starts from current state and simulates changed decisions forward.
- Historical Alternate History reconstructs point-in-time state and applies strict historical information firewalls before branching/replay.

### Opportunity Engine
Owns proactive opportunity-search orchestration and governed cross-channel opportunity-board composition.

The Opportunity Engine is an Application-layer orchestrator, not a new valuation engine. It may search, prune, route, and compose, but it may not create competing player values, pick values, competitive-state rules, trade recommendation policy, acceptance probabilities, or cross-channel utility weights.

The first production version consumes GM3 Team Improvement for single-step trade/waiver/HOLD discovery and preserves its governed ordering. It may also enumerate structurally compatible two-move portfolios, but those bundles are simulated and scored by the stable GM3 Team Improvement API using the same shared decision utility; Opportunity Engine does not own a portfolio score. Leading generated trade proposals are routed through the stable Trade Decision facade before execution advice, with counterparty willingness kept separate from trade valuation. Draft Intelligence and Breakout / Sleeper Intelligence may add specialist context to candidates, but Opportunity Engine does not rescore those signals or take ownership of their models. New code is promoted to Shared Core only when it is genuinely domain-generic or has a real second application consumer.

### Simulator
Owns season-forecast orchestration and published simulator outputs while consuming shared simulation mechanics.

## GM3 application areas

These remain inside GM3 rather than becoming separate engines unless they later develop a genuinely distinct workflow and decision policy:

- Team Improvement — consumes the shared decision-utility primitive rather than owning a separate categorical ranking-weight system
- Portfolio / Asset Management

## Analytics / Derived Products

These consume authoritative facts or application outputs and do not create competing models:

- Record Book
- Contender / Competitive Landscape analysis
- Historical Trade Analysis
- validation and benchmarking products

## Reports / Publications

The Reports / Publications layer includes a shared **FSFFL Reporting module** used across workflows. It owns presentation intelligence, not model intelligence:

- standard user-facing terminology
- contextual narrative built only from authoritative outputs
- number context and comparison language
- reporter-style explanations of roster construction, competitive window, lineup access, strengths/weaknesses, alternatives and implications
- shared visual primitives and evidence-based chart selection
- layout and presentation standards

The stable facade is `script/reporting/__init__.py`. Individual report renderers should consume this shared module rather than independently inventing language, context rules or chart logic.

The Reporting module has no authority to rescore, re-rank, change a recommendation, or create hidden analytical coefficients. It explains and visualizes conclusions owned by Core, Applications, or Analytics.

Reports compose outputs from Core, Applications, and Analytics. They should not invent a separate model merely to produce a document.

Examples:
- Preseason Report
- Record Book report
- Draft Recap
- Competitive Landscape / Power report
- GM Franchise report
- Simulator season outlook
- Trade Decision report
- Alternate History publication

## Migration rule

1. Preserve latest audited production behavior.
2. Move reusable concepts to Shared Core only when justified.
3. Keep application-specific reasoning inside the owning application.
4. Treat wrapper/composition files used only for migration as temporary scaffolding.
5. Preserve historical versions for reproducibility, not authority.
6. Expected differences are allowed. Unexplained differences are not.
