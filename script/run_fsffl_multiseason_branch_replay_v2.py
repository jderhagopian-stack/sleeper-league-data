#!/usr/bin/env python3
"""FSFFL Alternate History 0.7a v2: numerically stable chronological branch replay.

Fixes the first 0.7a integration finding: multiplying global path probabilities
through dozens of decisions caused every individual branch to fall below an
absolute probability floor. v2 keeps branch weights normalized *within the
retained beam* and separately tracks the fraction of original probability mass
covered by that beam.

This preserves three distinct quantities:
1. conditional branch weights among retained universes (sum to 1);
2. per-prune retained fraction;
3. cumulative global probability coverage retained from the original universe.

No pruned mass is silently renormalized away: normalization is explicitly
conditional on survival and global coverage remains visible in the report.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

import alternate_history_engine as ah
import alternate_history_branching as br
import run_fsffl_multiseason_branch_replay as v1
from run_fsffl_alternate_history import FSFFLHistoricalAdapter
from run_fsffl_downstream_dependencies import load
from run_fsffl_historical_policy_triage import run as run_triage
from run_fsffl_historical_usage_policy_v3 import run as run_usage_policy
from run_fsffl_historical_trade_policy_v2 import run as run_trade_policy
from run_fsffl_historical_trade_policy_v3 import run as run_trade_expansion

DEFAULT_MAX_BRANCHES = 256


def stable_compact_prune(
    branches: List[br.WeightedBranch],
    max_branches: int,
) -> Tuple[List[br.WeightedBranch], Dict[str, Any]]:
    """Merge + beam prune, then normalize retained conditional weights.

    Absolute min-probability pruning is intentionally not used because repeated
    sequential branching makes absolute path probabilities arbitrarily small.
    """
    merged, merged_count = br.merge_equivalent(branches)
    input_mass = sum(float(x.probability) for x in merged)
    batch = br.prune_branches(merged, max_branches=max_branches, min_probability=0.0)
    retained_mass = float(batch.retained_mass)
    retained_fraction = (retained_mass / input_mass) if input_mass > 0.0 else 0.0
    if retained_mass <= 0.0 or not batch.branches:
        raise ah.AlternateHistoryError(
            f"0.7a v2 branch beam extinct: input_mass={input_mass} retained={retained_mass}"
        )
    for branch in batch.branches:
        branch.probability = float(branch.probability) / retained_mass
    normalized = sum(float(x.probability) for x in batch.branches)
    if abs(normalized - 1.0) > 1e-8:
        raise ah.AlternateHistoryError(f"0.7a v2 conditional beam failed normalization: {normalized}")
    return batch.branches, {
        "expanded": len(branches),
        "merged_equivalent": merged_count,
        "retained": len(batch.branches),
        "pre_prune_conditional_mass": input_mass,
        "retained_pre_normalization_mass": retained_mass,
        "retained_fraction_of_incoming_beam": retained_fraction,
        "pruned_fraction_of_incoming_beam": max(0.0, 1.0 - retained_fraction),
        "post_normalization_conditional_mass": normalized,
    }


def run(scenario_path: Path, *, max_branches: int = DEFAULT_MAX_BRANCHES) -> Path:
    adapter = FSFFLHistoricalAdapter()
    scenario = ah.scenario_from_json(adapter, load(scenario_path))
    triage = load(run_triage(scenario_path))
    usage = load(run_usage_policy(scenario_path))
    trade = load(run_trade_policy(scenario_path))
    expansion = load(run_trade_expansion(scenario_path))

    usage_by_id = {str(x.get("transaction_id")): x for x in (usage.get("decisions") or [])}
    trade_by_id = {str(x.get("transaction_id")): x for x in (trade.get("decisions") or [])}
    expansion_by_id = {str(x.get("transaction_id")): x for x in (expansion.get("expansions") or [])}
    queues = triage.get("queues") or {}
    required = {str(x) for x in queues.get("required_branch_transaction_ids") or []}
    usage_ids = {str(x) for x in queues.get("historical_usage_policy_transaction_ids") or []}
    trade_ids = {str(x) for x in queues.get("historical_gm_required_transaction_ids") or []}
    stable = {str(x) for x in queues.get("structurally_stable_transaction_ids") or []}

    root_state = ah.apply_fork(ah.reconstruct_state(adapter, scenario.fork_timestamp_ms), scenario)
    branches = [br.root_branch(v1.serial(root_state))]
    global_coverage = 1.0
    branch_events: List[Dict[str, Any]] = []
    invariant_fast_path_events = 0
    max_observed_branches = 1

    for event in adapter.completed_events():
        created = int(event.get("created") or 0)
        if created < scenario.fork_timestamp_ms:
            continue
        tid = str(event.get("transaction_id") or "")

        if tid in usage_ids:
            policy_row = usage_by_id.get(tid)
            proposed = v1.usage_outcomes(event, policy_row or {}) if policy_row else [
                {"outcome": "preserve_exact", "probability": 1.0, "mode": "exact"}
            ]
            kind = "historical_usage_policy"
        elif tid in trade_ids:
            decision = trade_by_id.get(tid)
            proposed = v1.trade_outcomes(event, decision or {}, expansion_by_id.get(tid)) if decision else [
                {"outcome": "preserve_exact", "probability": 1.0, "mode": "exact"}
            ]
            kind = "historical_trade_policy"
        elif tid in required:
            proposed = [
                {"outcome": "preserve_if_legal", "probability": 1.0, "mode": "exact"},
                {"outcome": "forced_no_action", "probability": 0.0, "mode": "no_action"},
            ]
            kind = "required_branch"
        else:
            proposed = [
                {"outcome": "preserve_historical", "probability": 1.0, "mode": "exact"},
                {"outcome": "legality_forced_no_action", "probability": 0.0, "mode": "no_action"},
            ]
            kind = "structurally_stable" if tid in stable else "invariant"

        expanded: List[br.WeightedBranch] = []
        branch_legality_changed = False
        genuinely_branched = False
        for parent in branches:
            outcomes = v1.branch_specific_outcomes(parent.state, event, proposed)
            if len(outcomes) > 1:
                genuinely_branched = True
            if len(outcomes) != 1 or outcomes[0].get("mode") != "exact":
                branch_legality_changed = True
            parent_traces = parent.traces or [[]]
            for idx, outcome in enumerate(outcomes):
                p = float(parent.probability) * float(outcome.get("probability") or 0.0)
                if p <= 0.0:
                    continue
                state = v1.apply_outcome(parent.state, event, outcome)
                step = {
                    "transaction_id": tid,
                    "timestamp_ms": created,
                    "kind": kind,
                    "outcome": outcome.get("outcome"),
                    "conditional_probability": round(float(outcome.get("probability") or 0.0), 8),
                }
                if outcome.get("package_id"):
                    step["package_id"] = outcome.get("package_id")
                traces = [(list(t) + [step]) for t in parent_traces[:3]]
                expanded.append(br.WeightedBranch(
                    branch_id=f"{parent.branch_id}/{tid}:{idx}",
                    probability=p,
                    state=state,
                    traces=traces,
                ))

        if not expanded:
            raise ah.AlternateHistoryError(f"0.7a v2 produced zero branches at transaction {tid}")

        # Deterministic invariant events do not need hashing/beam pruning. The
        # mapping is one-to-one and cannot increase branch count. We defer exact
        # state merging until the next meaningful branch/legality boundary.
        if kind == "invariant" and not genuinely_branched and not branch_legality_changed:
            branches = expanded
            invariant_fast_path_events += 1
            continue

        branches, audit = stable_compact_prune(expanded, max_branches)
        retained_fraction = float(audit["retained_fraction_of_incoming_beam"])
        global_coverage *= retained_fraction
        max_observed_branches = max(max_observed_branches, len(branches))
        branch_events.append({
            "transaction_id": tid,
            "timestamp_ms": created,
            "kind": kind,
            "genuinely_branched": genuinely_branched,
            "branch_legality_changed": branch_legality_changed,
            "global_probability_coverage_after_event": global_coverage,
            **audit,
        })

    # One final merge/prune makes the final distribution canonical even if the
    # last events used the deterministic fast path.
    branches, final_audit = stable_compact_prune(branches, max_branches)
    global_coverage *= float(final_audit["retained_fraction_of_incoming_beam"])
    final_conditional_mass = sum(float(x.probability) for x in branches)
    if not branches or abs(final_conditional_mass - 1.0) > 1e-8:
        raise ah.AlternateHistoryError(
            f"0.7a v2 invalid final beam: branches={len(branches)} mass={final_conditional_mass}"
        )

    focus = str(scenario.focus_roster_id)
    player_owner_mass: Dict[str, Dict[str, float]] = {}
    pick_owner_mass: Dict[str, Dict[str, float]] = {}
    for branch in branches:
        w = float(branch.probability)
        for rid, players in (branch.state.get("roster_players") or {}).items():
            for pid in players or []:
                player_owner_mass.setdefault(str(pid), {}).setdefault(str(rid), 0.0)
                player_owner_mass[str(pid)][str(rid)] += w
        for key, rid in (branch.state.get("pick_owners") or {}).items():
            pick_owner_mass.setdefault(str(key), {}).setdefault(str(rid), 0.0)
            pick_owner_mass[str(key)][str(rid)] += w

    focus_player_probabilities = [
        {"player_id": pid, "conditional_probability_on_focus_roster": round(float(owners.get(focus) or 0.0), 6)}
        for pid, owners in player_owner_mass.items()
        if float(owners.get(focus) or 0.0) > 0.0
    ]
    focus_player_probabilities.sort(key=lambda x: (-x["conditional_probability_on_focus_roster"], x["player_id"]))

    report = {
        "model_version": "Fantasy-Alternate-History-0.7a-v2-stable-chronological-branch-replay",
        "scenario_id": scenario.scenario_id,
        "design_invariants": {
            "completed_nfl_history_is_immutable": True,
            "branch_specific_accumulated_state_legality": True,
            "current_gm3_numeric_values_used": False,
            "current_market_values_used": False,
            "future_nfl_outcomes_used": False,
            "absolute_path_probability_floor_used": False,
            "retained_beam_weights_conditionally_normalized": True,
            "global_probability_coverage_explicit": True,
            "equivalent_states_merged_before_meaningful_pruning": True,
        },
        "scope_note": "Transaction-state propagation only; season feedback / alternate Max PF / draft-order / alternate draft insertion follow in 0.7b+.",
        "configuration": {"max_branches": max_branches},
        "summary": {
            "final_retained_branches": len(branches),
            "final_conditional_probability_mass": round(final_conditional_mass, 10),
            "global_probability_coverage_retained": round(global_coverage, 10),
            "global_probability_mass_pruned": round(max(0.0, 1.0 - global_coverage), 10),
            "audited_branch_events": len(branch_events),
            "invariant_fast_path_events": invariant_fast_path_events,
            "max_observed_retained_branches": max_observed_branches,
        },
        "branch_event_audit": branch_events,
        "focus_roster_player_conditional_probabilities": focus_player_probabilities,
        "pick_owner_conditional_probabilities": pick_owner_mass,
        "representative_branches": [
            {
                "branch_id": x.branch_id,
                "conditional_probability": round(float(x.probability), 10),
                "unconditional_probability_approx": round(float(x.probability) * global_coverage, 10),
                "focus_roster_players": sorted((x.state.get("roster_players") or {}).get(focus, [])),
                "pick_owners": x.state.get("pick_owners") or {},
                "trace": (x.traces or [[]])[0],
            }
            for x in branches[:20]
        ],
    }
    out = ah.write_isolated_json(f"results/{scenario.scenario_id}/multiseason_branch_replay_0_7a_v2.json", report)
    print(out)
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Alternate History 0.7a v2 stable chronological branch replay")
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--max-branches", type=int, default=DEFAULT_MAX_BRANCHES)
    args = parser.parse_args()
    run(args.scenario, max_branches=args.max_branches)


if __name__ == "__main__":
    main()
