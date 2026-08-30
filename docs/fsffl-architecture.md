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
- shared data access and standardized metrics

Promotion to Shared Core requires either genuinely domain-generic behavior or a real second application consumer.

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

### Simulator
Owns season-forecast orchestration and published simulator outputs while consuming shared simulation mechanics.

## GM3 application areas

These remain inside GM3 rather than becoming separate engines unless they later develop a genuinely distinct workflow and decision policy:

- Team Improvement
- Portfolio / Asset Management

## Analytics / Derived Products

These consume authoritative facts or application outputs and do not create competing models:

- Record Book
- Contender / Competitive Landscape analysis
- Historical Trade Analysis
- validation and benchmarking products

## Reports / Publications

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
