# Fantasy Alternate History Engine

## Purpose

A league-agnostic counterfactual fantasy-football replay system. For FSFFL conversations it is presented as the **FSFFL Alternate History Simulator**, but the core must work with any league adapter.

## Non-negotiable invariants

1. **Completed NFL history is immutable.** Past NFL games, player stats, injuries, suspensions, transactions and career outcomes do not change. A fantasy counterfactual changes only who receives the fantasy benefit and the downstream fantasy-league consequences.
2. **League-agnostic core.** Scoring, roster, draft, playoff and platform assumptions come from adapters/configuration, never hard-coded FSFFL rules.
3. **Read-only production dependency.** Simulator 1.0, GM 3.0 and canonical Sleeper artifacts are inputs only. Alternate History writes exclusively to `data/alternate_history/`.
4. **Uncertainty is explicit.** Deterministic consequences, high-probability modeled consequences and speculative butterfly effects are labeled separately.
5. **Incremental replay.** Only causally affected events are reconsidered. Unaffected historical events remain fixed.

## Runtime architecture

`League Adapter -> Historical State Reconstruction -> Counterfactual Fork -> Dependency Graph -> Decision Policy -> Historical Replay -> Current/Future Simulator -> Branch Clustering -> Milestone/Butterfly Analysis -> Narrative Report`

### Completed seasons

Use actual historical NFL fantasy points. Do not Monte Carlo alternate NFL performance. Monte Carlo is reserved for fantasy decision uncertainty and branching.

### Current/future seasons

Use the league's season simulation provider. For FSFFL this is Simulator 1.0. Existing Simulator 1.0 code is not modified.

### Decision policy

Use a pluggable decision-policy provider. For FSFFL the intended provider is GM 3.0 plus owner-behavior calibration. Existing GM 3.0 code is not modified.

## Progressive simulation depth

- Screen branches with ~250-500 samples.
- Re-evaluate material branches with ~2,500-5,000 samples.
- Confirm final high-impact timeline families with ~15,000-50,000 samples.
- Cluster materially equivalent states to prevent combinatorial explosion.
- Cache fork states and simulation families by stable scenario hash.

## Report contract

Every mature report should contain:

- fork decision and historical context;
- deterministic changes;
- things that did not change;
- year-by-year expected timeline;
- affected transactions/drafts/waivers;
- butterfly effects with probabilities;
- major milestone changes (playoffs, titles, records, draft slots, major acquisitions);
- expected present-day roster with ownership probabilities;
- expected present-day draft capital with ownership/pick-quality probabilities;
- current-season expected wins, points, playoff, bye and championship probabilities;
- franchise-value and roster-window comparison versus actual history;
- reconstruction/model confidence.

## Version plan

### 0.1 - Historical fork foundation
- league-agnostic core;
- FSFFL historical adapter;
- reverse player/pick/FAAB transaction replay;
- declarative player add/drop fork;
- dependency-event pruning;
- isolated cache/results namespace;
- Puka Nacua vs Van Jefferson validation scenario.

### 0.2 - Historical scoring replay
- ingest historical weekly fantasy scoring;
- optimize legal historical lineups under league rules;
- recompute matchups, standings, playoffs and draft order after a fork;
- no-fork replay must reproduce actual league results.

### 0.3 - Conditional downstream events
- classify later events as invariant, impossible, conditional or decision events;
- build asset/event dependency graph;
- invalidate transactions whose required assets no longer exist.

### 0.4 - GM/owner policy branching
- GM 3.0 decision-policy provider;
- owner behavior probabilities;
- branch screening/pruning/clustering.

### 0.5 - Draft alternate-history engine
- probabilistically replay remaining draft board after changed selections;
- retain actual picks where unaffected;
- owner/team-need/value-aware selections where affected.

### 0.6 - Present-day alternate franchise
- modal/expected current roster;
- player ownership probabilities;
- expected future draft capital and pick quality;
- GM 3.0 roster value and strategic state.

### 0.7 - Current/future simulation
- feed alternate present-day states into Simulator 1.0 through a provider interface;
- expected wins/points/playoff/bye/championship distributions.

### 0.8 - Butterfly and milestone analyzer
- titles, playoff appearances, record-book changes, draft-slot changes, major asset events;
- league-wide causal ripple analysis.

### 0.9 - Narrative report generator
- evidence-backed alternate-history narrative generated only from structured model outputs.

### 1.0 - Production validation
- replay validation suite;
- calibration/backtests;
- runtime benchmarks;
- stable report schema and scenario API.

## First validation scenario

`data/alternate_history/scenarios/puka_vs_van_2023.json`

Fork: immediately before Puka Nacua was acquired on waivers on September 7, 2023. Counterfactual: the focus owner adds Puka and drops Van Jefferson. Puka's real NFL career and weekly production remain unchanged; only FSFFL ownership and downstream fantasy consequences may change.
