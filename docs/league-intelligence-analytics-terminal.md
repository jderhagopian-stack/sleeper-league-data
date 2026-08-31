# FSFFL League Intelligence / Analytics Terminal

## Purpose

League Intelligence answers a different question from Opportunity Engine:

> Show me the league, the market, and each franchise's situation so I can investigate and make my own decisions.

It is a **read/expose-oriented application and reporting layer**. It composes governed upstream intelligence into transparent views. It does not create a new valuation model, recommendation model, trade score, contender score, or simulation model.

The product spectrum is:

**raw intelligence -> investigation -> opportunity search -> recommendation**

League Intelligence owns the first two stages. Opportunity Engine owns opportunity search/orchestration. Existing decision applications retain recommendation authority.

## Architectural invariant

**Engines calculate -> League Intelligence exposes -> Opportunity Engine searches/recommends.**

No report or terminal view may introduce a hidden weighted blend, rescore governed upstream outputs, or become a second source of truth.

## Authority map

- **Shared valuation / player-value sources** own canonical model values and market-value inputs.
- **GM3** owns team-specific franchise utility, roster construction context, team needs, break-glass values, and cross-channel improvement utility.
- **Simulator** owns competitive outcome simulation and season-strength outcomes.
- **Trade Decision** owns generated-trade review, negotiation policy, and counterparty-feasibility interpretation.
- **Behavioral Intelligence** owns observed behavioral evidence and market tendencies; it does not create acceptance probability.
- **Draft Intelligence / Breakout-Sleeper Intelligence** own their specialist evidence.
- **League Intelligence** owns view composition, filtering, sorting of like-for-like governed fields, normalization for display when semantics are preserved, explanations, provenance, and report/terminal presentation.
- **Opportunity Engine** may consume League Intelligence views for discovery, but may not treat presentation-only derived fields as new decision authority.

## First vertical slice

### 1. Player Value & Rankings View

Expose, overall and by position:

- canonical FSFFL model value/rank;
- canonical external/market value/rank when available;
- model-versus-market delta as a transparent subtraction of two governed values;
- team-specific value or retention context sourced from GM3 rather than recomputed by League Intelligence;
- current owner and league availability state;
- provenance for every displayed field.

Team-specific rankings must be derived from an owning model's team-aware output. League Intelligence must not manufacture a team-fit coefficient or apply ad hoc positional multipliers.

### 2. Team Strength / Weakness Heat Map

Rows are franchises; columns initially include QB, RB, WR, TE, future draft capital, and optional total roster/depth summaries.

Candidate display dimensions may include:

- current-season relative strength;
- long-term relative strength;
- starter strength;
- depth/replacement quality;
- roster need/deficit;
- draft-capital strength.

The heat map may convert an upstream numeric field into league percentile, rank, or z-score **only for display**, provided:

1. all teams are compared on the same governed field;
2. the raw value remains available;
3. the transformation is monotonic and documented;
4. the transformed display value is never fed back into a decision model as authority.

### 3. Value-Disagreement / Trade-Partner Map

Expose transparent disagreement patterns such as:

- FSFFL model value > market value;
- market value > FSFFL model value;
- current owner's GM3 retention context is low relative to another franchise's team-aware value/context;
- a position of surplus on one roster aligns with a position of need on another.

This view may identify **where to investigate**. It does not assert a fair trade, acceptance probability, or execute recommendation. Any generated trade proposal remains subject to Trade Decision and any cross-channel recommendation remains subject to the owning decision application.

## View contract

Every League Intelligence view must declare:

- `view_id` and version;
- purpose and audience;
- upstream authorities used;
- raw governed fields consumed;
- any presentation-only transforms;
- allowed operations;
- forbidden operations;
- provenance timestamp/source revision where available;
- whether the view is safe for downstream discovery only or safe for direct user presentation.

### Allowed operations

- filtering;
- grouping;
- sorting on a single governed field;
- monotonic display transforms such as percentile/rank/z-score;
- arithmetic deltas between semantically comparable governed values, clearly labeled;
- joins across governed sources using canonical player/team/asset identifiers;
- descriptive explanation and provenance.

### Forbidden operations

- weighted blends of unrelated units;
- new coefficients, thresholds, or hidden heuristics that imply decision quality;
- reranking by presentation-only composites;
- independently estimating trade acceptance probability;
- independently estimating team improvement, contender status, player value, or simulation outcomes;
- presenting a discovery signal as an execution recommendation.

## Data model

League Intelligence should publish view records with three layers:

1. **Identity** — canonical player/team/asset identifiers and human-readable names.
2. **Governed facts** — raw fields from owning modules, each with source authority and source path/model version.
3. **Presentation metadata** — ranks, percentiles, deltas, labels, explanations, and display grouping.

Presentation metadata is disposable and reproducible. Governed facts are the audit trail.

## Initial report family

The architecture should support, without adding new model authority:

- overall and positional player rankings;
- model-vs-market rankings;
- team-specific value/rank views;
- league positional strength heat map;
- roster power rankings sourced from Simulator/GM3 governed outputs;
- trade-partner maps;
- draft-capital maps;
- contender-window views;
- roster age/value curves;
- surplus/deficit maps;
- most-tradable-asset views using existing liquidity/market-test evidence.

## Relationship to Reports module

The standardized Reports layer remains presentation infrastructure. League Intelligence provides governed view payloads; Reports renders them into PDFs/other surfaces. Reports must not calculate new league intelligence or valuation authority.

## Relationship to Opportunity Engine

Opportunity Engine may use League Intelligence to broaden candidate discovery or explain why an opportunity is interesting. When it does:

- the original owning authority must remain attached to each field;
- presentation-only ranks/percentiles cannot become utility coefficients;
- final cross-channel ranking remains GM3 Team Improvement;
- trades still pass through Trade Decision;
- competitive outcomes remain Simulator-owned.

## Shared Core rule

Do not promote League Intelligence-specific view schemas or transforms into Shared Core until they have a genuine second application consumer and are domain-generic. Canonical identity/provenance primitives may qualify if already shared elsewhere.

## Implementation sequence

1. Establish enforceable view contracts and authority declarations.
2. Build the Player Value & Rankings payload from existing governed sources.
3. Build the Team Strength / Weakness Heat Map payload.
4. Build the Value-Disagreement / Trade-Partner payload.
5. Add standardized report renderers and terminal surfaces.
6. Add prospective validation only for claims the layer actually makes (for example data freshness or stability), not for decision outcomes owned elsewhere.

## Success criteria

The first release is successful when a manager can inspect the league, compare model versus market, understand each team's positional shape, and identify plausible counterparties while every displayed conclusion can be traced back to an existing owning model or an explicitly documented presentation-only transformation.