# FSFFL Opportunity Engine

## Purpose

The Opportunity Engine answers a different question from the individual FSFFL
decision modules:

> What are the best actions available to improve this franchise right now?

It is an **Application-layer orchestrator**. It searches, routes, and explains.
It does not create a second valuation model.

## Authority

- **GM3 Team Improvement** owns cross-channel franchise-improvement utility,
  waiver/add-drop evaluation, and portfolio bundle evaluation.
- **Simulator** owns competitive outcome simulation.
- **Trade Decision** owns generated-trade review, negotiation policy, and
  counterparty-feasibility interpretation.
- **Behavioral Intelligence** supplies behavioral evidence; it does not supply an
  acceptance probability.
- **Draft Intelligence** and **Breakout / Sleeper Intelligence** supply specialist
  player context.
- **Opportunity Engine** owns search orchestration, search coverage, specialist
  views, portfolio candidate enumeration, execution precondition composition,
  prospective snapshot metadata, and presentation-ready composition.

## Current production generation

Opportunity Engine 2.0 extends the governed 1.4 composition layer. The 1.4 module
remains reusable and contains no competing valuation authority; the 2.0 runtime
adds search and diagnostics only.

Current capabilities include:

1. League-wide GM3 trade-target scan for a focal franchise.
2. Waiver/free-agent scan with endogenous roster cuts.
3. Explicit HOLD benchmark.
4. Governed cross-channel ranking from GM3 Team Improvement.
5. Filtered buy-low, model-vs-market, current-season, long-term-value,
   negotiation-ready, emerging-value, and draft-intelligence views. These are
   filtered views of the governed upstream order, not independent rankings.
6. GM3 market-test / sell-high candidate view.
7. Broader trade-package discovery with a configurable per-target search budget.
8. Scale-free waiver discovery across independent projection, ECR, dynasty-market,
   and FSFFL-value lanes. No fixed cross-unit waiver pre-screen coefficients are
   active in the current Team Improvement implementation.
9. Adaptive multi-step portfolio search. Two-move combinations are exhaustive
   within the retained candidate pool; 3+ move bundles expand from the leading
   governed frontier.
10. Every portfolio is simulated and scored by GM3 Team Improvement using the
    same shared decision utility; Opportunity Engine does not add a bundle score.
11. Equal-precision comparison of the best portfolio against the best single move.
12. Execution preconditions for every portfolio step, including ownership,
    waiver availability, roster legality, and the requirement to re-check trade
    counterparties immediately before action.
13. Optional independent-seed robustness diagnostics. These diagnostics report
    score stability but do not rerank the board.
14. Trade Decision routing for leading generated trade proposals.
15. Timestamped prospective-validation metadata and an input fingerprint so later
    grading can be performed without backfilling future information.
16. Presentation-only Markdown report and on-demand GitHub Actions workflow.

## Search vs judgment

Search budgets such as trade-screen depth, package depth, portfolio depth, beam
width, and simulation counts control computation. They are not valuation weights
and do not change the meaning of the downstream models.

Opportunity Engine may prune or enumerate candidates, but final scores come from
the owning application. Specialist intelligence may add context but does not
rerank the board.

### Trade candidate discovery

GM3's trade-opportunity intelligence supplies targets and candidate packages.
Opportunity Engine/Team Improvement may broaden how many upstream packages are
considered and how much unique-target coverage is retained, but package quality
continues to use the upstream GM3 decision score. Acceptance fit is descriptive
context and is not an eligibility gate or acceptance probability.

### Waiver candidate discovery

All unowned fantasy-relevant players with a canonical full projection may enter
the discovery universe. The active implementation does not combine projection,
ECR, dynasty market value, and FSFFL value with fixed coefficients. Instead it
round-robins independent ranked lanes so candidates can reach full simulation for
different evidence-based reasons. GM3's full roster-aware simulation remains the
actual decision authority.

## Simulation/search-budget calibration

`.github/workflows/opportunity-engine-phase2-calibration.yml` compares the
production-sized configuration against higher simulation depths, wider trade and
waiver screens, deeper trade-package search, pair-only portfolio search, and a
much deeper same-state reference.

The reference is **not ground truth** and is not coefficient training. The audit
measures best-action agreement, top-10 recall, top-rank overlap, score error,
portfolio stability, and runtime. Production budgets should be changed only when
that evidence shows a worthwhile confidence/recall gain for the runtime cost.

## Trade semantics

A trade generated by Opportunity Engine is treated as a **focal-initiated
proposal**. Counterparty willingness to that proposal is not observed. Trade
Decision therefore reports behavioral feasibility separately from trade quality.

The default production workflow routes the leading generated trade through Trade
Decision at the canonical high-precision confirmation depth.

## Portfolio semantics

Opportunity Engine 2.0 can search structurally compatible portfolios beyond two
moves without brute-forcing the entire combinatorial space. It exhaustively tests
compatible pairs within the retained candidate set, keeps a governed beam of the
leading bundles, and expands that frontier to additional moves. Every expanded
bundle is re-evaluated by GM3 Team Improvement.

This is a **strategy search**, not an assertion that the sequence is immediately
executable. Live ownership, waiver availability, roster legality and counterparty
willingness must be rechecked before execution. Any trade step still requires
Trade Decision review.

## Robustness semantics

Independent-seed robustness diagnostics are optional and are deliberately
separate from the primary ranking. They show whether a leading recommendation's
GM3 score remains directionally stable across repeated Monte Carlo samples. They
do not create another score, confidence weight, or recommendation model.

## Prospective validation

Every 2.0 board includes a timestamp, source revision when available, and SHA-256
fingerprint of the source Team Improvement input. Production artifacts are
retained so recommendations can later be evaluated as genuinely prospective
observations. Future player values or outcomes must not be backfilled into the
original recommendation record.

## Shared Core rule

Opportunity Engine adds no new Shared Core authority. New primitives should move
to Shared Core only if they become genuinely domain-generic or acquire a real
second application consumer.

## Operational entry points

Current production application:

```bash
python script/opportunity_engine/application_v2.py \
  --focus-user-id <SLEEPER_USER_ID> \
  --output /tmp/opportunity-board.json
```

The governed 1.4 composition implementation remains at:

```text
script/opportunity_engine/application.py
```

Production workflow:

```text
.github/workflows/run-opportunity-engine.yml
```

The workflow builds the full projection universe, refreshes Behavioral
Intelligence, runs Opportunity Engine 2.0, routes the leading trade through Trade
Decision, renders a report, and uploads the prospective artifacts.
