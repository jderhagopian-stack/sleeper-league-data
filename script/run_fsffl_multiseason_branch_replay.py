#!/usr/bin/env python3
"""FSFFL Alternate History 0.7a: chronological weighted branch-state replay.

This is the first end-to-end state propagator. It carries distinct alternate
league states forward transaction by transaction from the scenario fork and
branches only where the historical policy layers say a decision is causally
sensitive.

Key guarantees:
- each branch checks transaction legality against its OWN accumulated state;
- invariant/legal historical events are applied without branching;
- usage decisions branch across preserve exact / changed drop / no action;
- strategic trades branch across preserve / timestamp-safe modified package /
  no trade;
- modified-trade probability with no defensible package is conservatively
  redirected to no trade rather than inventing terms;
- equivalent states are merged before beam pruning;
- all pruned probability mass is reported explicitly;
- no current GM3 values, current market ranks, or future NFL outcomes are used.

0.7a intentionally propagates transaction state only. Season-result feedback,
alternate Max PF, draft-order recomputation, alternate rookie-draft insertion,
and subsequent-season feedback are layered on top in 0.7b+.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import alternate_history_engine as ah
import alternate_history_branching as br
from run_fsffl_alternate_history import FSFFLHistoricalAdapter
from run_fsffl_downstream_dependencies import apply_forward_event, event_legality, load
from run_fsffl_historical_policy_triage import run as run_triage
from run_fsffl_historical_usage_policy_v3 import run as run_usage_policy
from run_fsffl_historical_trade_policy_v2 import run as run_trade_policy
from run_fsffl_historical_trade_policy_v3 import run as run_trade_expansion

DEFAULT_MAX_BRANCHES = 256
DEFAULT_MIN_PROBABILITY = 1e-7


def to_state(payload: Dict[str, Any]) -> ah.LeagueState:
    return ah.LeagueState(
        league_key=str(payload.get("league_key") or ""),
        timestamp_ms=int(payload.get("timestamp_ms") or 0),
        roster_players={str(k): {str(x) for x in (v or [])} for k, v in (payload.get("roster_players") or {}).items()},
        roster_taxi={str(k): {str(x) for x in (v or [])} for k, v in (payload.get("roster_taxi") or {}).items()},
        roster_reserve={str(k): {str(x) for x in (v or [])} for k, v in (payload.get("roster_reserve") or {}).items()},
        pick_owners={str(k): str(v) for k, v in (payload.get("pick_owners") or {}).items()},
        faab={str(k): float(v or 0.0) for k, v in (payload.get("faab") or {}).items()},
        reconstruction=dict(payload.get("reconstruction") or {}),
    )


def serial(state: ah.LeagueState) -> Dict[str, Any]:
    return state.serializable()


def exact_transition(state_payload: Dict[str, Any], event: Dict[str, Any]) -> Tuple[Dict[str, Any], bool, List[Dict[str, Any]]]:
    state = to_state(state_payload)
    legal, reasons = event_legality(state, event)
    if legal:
        apply_forward_event(state, event)
    state.timestamp_ms = int(event.get("created") or state.timestamp_ms)
    return serial(state), bool(legal), reasons


def no_action_transition(state_payload: Dict[str, Any], event: Dict[str, Any]) -> Dict[str, Any]:
    state = to_state(state_payload)
    state.timestamp_ms = int(event.get("created") or state.timestamp_ms)
    return serial(state)


def changed_drop_event(event: Dict[str, Any], decision: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    suggestion = decision.get("suggested_alternate_drop") or {}
    alt_drop = suggestion.get("player_id")
    rid = decision.get("roster_id")
    if not alt_drop or rid is None:
        return None
    out = copy.deepcopy(event)
    drops = {str(k): str(v) for k, v in (out.get("drops") or {}).items()}
    # Replace drops for this roster while preserving any other roster legs.
    drops = {pid: owner for pid, owner in drops.items() if str(owner) != str(rid)}
    drops[str(alt_drop)] = str(rid)
    out["drops"] = drops
    return out


def modified_trade_event(event: Dict[str, Any], package: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    out = copy.deepcopy(event)
    drops = {str(k): str(v) for k, v in (out.get("drops") or {}).items()}
    adds = {str(k): str(v) for k, v in (out.get("adds") or {}).items()}
    for repl in package.get("replacements") or []:
        historical = str(repl.get("outgoing_historical_player_id") or "")
        replacement = str(repl.get("replacement_player_id") or "")
        sender = str(repl.get("sender_roster_id") or "")
        if not historical or not replacement or historical not in drops:
            return None
        receiver = adds.get(historical)
        drops.pop(historical, None)
        adds.pop(historical, None)
        drops[replacement] = sender
        if receiver is not None:
            adds[replacement] = str(receiver)
    out["drops"] = drops
    out["adds"] = adds
    return out


def normalize(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    vals = [dict(x) for x in rows if float(x.get("probability") or 0.0) > 0.0]
    total = sum(float(x["probability"]) for x in vals)
    if total <= 0.0:
        return [{"outcome": "no_action", "probability": 1.0, "mode": "no_action"}]
    for row in vals:
        row["probability"] = float(row["probability"]) / total
    return vals


def usage_outcomes(event: Dict[str, Any], row: Dict[str, Any]) -> List[Dict[str, Any]]:
    decisions = row.get("decisions") or []
    # Current FSFFL queue has one roster decision per transaction. Keep the
    # representation explicit so multi-roster usage events can be generalized.
    if len(decisions) != 1:
        return [{"outcome": "preserve_exact", "probability": 1.0, "mode": "exact"}]
    d = decisions[0]
    probs = d.get("probabilities") or {}
    changed = changed_drop_event(event, d)
    rows = [
        {"outcome": "preserve_exact", "probability": float(probs.get("preserve_exact") or 0.0), "mode": "exact"},
        {"outcome": "no_action", "probability": float(probs.get("no_action") or 0.0), "mode": "no_action"},
    ]
    changed_p = float(probs.get("preserve_add_change_drop") or 0.0)
    if changed is not None:
        rows.append({"outcome": "preserve_add_change_drop", "probability": changed_p, "mode": "event", "event": changed})
    else:
        rows[1]["probability"] += changed_p
    return normalize(rows)


def trade_outcomes(event: Dict[str, Any], decision: Dict[str, Any], expansion: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    probs = decision.get("probabilities") or {}
    preserve = float(probs.get("preserve_historical_trade") or 0.0)
    modified = float(probs.get("modified_trade_branch") or 0.0)
    no_trade = float(probs.get("no_trade") or 0.0)
    rows: List[Dict[str, Any]] = [
        {"outcome": "preserve_historical_trade", "probability": preserve, "mode": "exact"},
        {"outcome": "no_trade", "probability": no_trade, "mode": "no_action"},
    ]
    packages = (expansion or {}).get("packages") or []
    concrete = 0.0
    for package in packages:
        alt_event = modified_trade_event(event, package)
        if alt_event is None:
            continue
        cp = float(package.get("conditional_probability_given_modified") or 0.0)
        if cp <= 0.0:
            continue
        concrete += cp
        rows.append({
            "outcome": "modified_trade",
            "probability": modified * cp,
            "mode": "event",
            "event": alt_event,
            "package_id": package.get("package_id"),
        })
    # Missing/unsupported package mass becomes no trade. This is deliberately
    # conservative and prevents fabricated replacement assets.
    rows[1]["probability"] += modified * max(0.0, 1.0 - concrete)
    return normalize(rows)


def branch_specific_outcomes(
    state_payload: Dict[str, Any],
    event: Dict[str, Any],
    proposed: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Filter/redirect outcomes according to legality in one accumulated branch."""
    state = to_state(state_payload)
    legal_rows: List[Dict[str, Any]] = []
    redirected = 0.0
    for row in proposed:
        p = float(row.get("probability") or 0.0)
        mode = row.get("mode")
        if mode == "no_action":
            legal_rows.append(row)
            continue
        candidate_event = event if mode == "exact" else row.get("event")
        if not isinstance(candidate_event, dict):
            redirected += p
            continue
        legal, _ = event_legality(state, candidate_event)
        if legal:
            legal_rows.append(row)
        else:
            redirected += p
    no_action = next((x for x in legal_rows if x.get("mode") == "no_action"), None)
    if redirected > 0.0:
        if no_action is None:
            legal_rows.append({"outcome": "legality_forced_no_action", "probability": redirected, "mode": "no_action"})
        else:
            no_action["probability"] = float(no_action.get("probability") or 0.0) + redirected
    return normalize(legal_rows)


def apply_outcome(state_payload: Dict[str, Any], historical_event: Dict[str, Any], outcome: Dict[str, Any]) -> Dict[str, Any]:
    mode = outcome.get("mode")
    if mode == "no_action":
        return no_action_transition(state_payload, historical_event)
    event = historical_event if mode == "exact" else outcome.get("event")
    new_state, legal, _ = exact_transition(state_payload, event)
    if not legal:
        # branch_specific_outcomes should prevent this; keep a hard guardrail.
        raise ah.AlternateHistoryError("0.7a attempted illegal branch transition")
    return new_state


def compact_prune(branches: List[br.WeightedBranch], max_branches: int, min_probability: float) -> Tuple[List[br.WeightedBranch], Dict[str, Any]]:
    merged, merged_count = br.merge_equivalent(branches)
    batch = br.prune_branches(merged, max_branches=max_branches, min_probability=min_probability)
    return batch.branches, {
        "expanded": len(branches),
        "merged_equivalent": merged_count,
        "retained": len(batch.branches),
        "retained_mass": batch.retained_mass,
        "pruned_mass": batch.pruned_mass,
    }


def run(scenario_path: Path, *, max_branches: int = DEFAULT_MAX_BRANCHES, min_probability: float = DEFAULT_MIN_PROBABILITY) -> Path:
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
    sensitive = required | usage_ids | trade_ids | stable

    root_state = ah.apply_fork(ah.reconstruct_state(adapter, scenario.fork_timestamp_ms), scenario)
    branches = [br.root_branch(serial(root_state))]
    cumulative_pruned_mass = 0.0
    branch_events = []
    invariant_events = 0

    for event in adapter.completed_events():
        created = int(event.get("created") or 0)
        if created < scenario.fork_timestamp_ms:
            continue
        tid = str(event.get("transaction_id") or "")

        if tid in usage_ids:
            policy_row = usage_by_id.get(tid)
            proposed = usage_outcomes(event, policy_row or {}) if policy_row else [{"outcome": "preserve_exact", "probability": 1.0, "mode": "exact"}]
            kind = "historical_usage_policy"
        elif tid in trade_ids:
            decision = trade_by_id.get(tid)
            proposed = trade_outcomes(event, decision or {}, expansion_by_id.get(tid)) if decision else [{"outcome": "preserve_exact", "probability": 1.0, "mode": "exact"}]
            kind = "historical_trade_policy"
        elif tid in required:
            # Direct/mechanical conflicts are resolved by per-branch legality:
            # apply exact if still legal in a branch, otherwise force no action.
            proposed = [
                {"outcome": "preserve_if_legal", "probability": 1.0, "mode": "exact"},
                {"outcome": "forced_no_action", "probability": 0.0, "mode": "no_action"},
            ]
            kind = "required_branch"
        else:
            # Stable and invariant events are historical facts unless accumulated
            # branch state makes them mechanically impossible.
            proposed = [
                {"outcome": "preserve_historical", "probability": 1.0, "mode": "exact"},
                {"outcome": "legality_forced_no_action", "probability": 0.0, "mode": "no_action"},
            ]
            kind = "structurally_stable" if tid in stable else "invariant"

        expanded: List[br.WeightedBranch] = []
        changed = False
        for parent in branches:
            outcomes = branch_specific_outcomes(parent.state, event, proposed)
            if len(outcomes) > 1 or outcomes[0].get("mode") != "exact":
                changed = True
            parent_traces = parent.traces or [[]]
            for idx, outcome in enumerate(outcomes):
                p = float(parent.probability) * float(outcome.get("probability") or 0.0)
                if p <= 0.0:
                    continue
                state = apply_outcome(parent.state, event, outcome)
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

        branches, audit = compact_prune(expanded, max_branches, min_probability)
        cumulative_pruned_mass += float(audit["pruned_mass"])
        if kind != "invariant" or changed or audit["merged_equivalent"] or audit["pruned_mass"] > 0.0:
            branch_events.append({
                "transaction_id": tid,
                "timestamp_ms": created,
                "kind": kind,
                "branching_or_legality_change": bool(changed),
                **audit,
            })
        else:
            invariant_events += 1

    total_retained = sum(float(x.probability) for x in branches)
    focus = str(scenario.focus_roster_id)
    player_owner_mass: Dict[str, Dict[str, float]] = {}
    for branch in branches:
        for rid, players in (branch.state.get("roster_players") or {}).items():
            for pid in players or []:
                player_owner_mass.setdefault(str(pid), {}).setdefault(str(rid), 0.0)
                player_owner_mass[str(pid)][str(rid)] += float(branch.probability)

    focus_player_probabilities = []
    for pid, owners in player_owner_mass.items():
        mass = float(owners.get(focus) or 0.0)
        if mass > 0.0:
            focus_player_probabilities.append({"player_id": pid, "probability_mass_on_focus_roster": round(mass, 6)})
    focus_player_probabilities.sort(key=lambda x: (-x["probability_mass_on_focus_roster"], x["player_id"]))

    report = {
        "model_version": "Fantasy-Alternate-History-0.7a-chronological-branch-state-replay",
        "scenario_id": scenario.scenario_id,
        "design_invariants": {
            "completed_nfl_history_is_immutable": True,
            "branch_specific_accumulated_state_legality": True,
            "current_gm3_numeric_values_used": False,
            "current_market_values_used": False,
            "future_nfl_outcomes_used": False,
            "equivalent_states_merged_before_pruning": True,
            "pruned_probability_mass_explicit": True,
        },
        "scope_note": "Transaction-state propagation only; season feedback / alternate Max PF / draft-order / alternate draft insertion follow in 0.7b+.",
        "configuration": {"max_branches": max_branches, "min_probability": min_probability},
        "summary": {
            "final_retained_branches": len(branches),
            "final_retained_probability_mass": round(total_retained, 8),
            "cumulative_pruned_probability_mass": round(cumulative_pruned_mass, 8),
            "audited_branch_events": len(branch_events),
            "silent_invariant_events": invariant_events,
        },
        "branch_event_audit": branch_events,
        "focus_roster_player_probability_mass": focus_player_probabilities,
        "representative_branches": [
            {
                "branch_id": x.branch_id,
                "probability": round(float(x.probability), 8),
                "focus_roster_players": sorted((x.state.get("roster_players") or {}).get(focus, [])),
                "pick_owners": x.state.get("pick_owners") or {},
                "trace": (x.traces or [[]])[0],
            }
            for x in branches[:20]
        ],
    }
    out = ah.write_isolated_json(f"results/{scenario.scenario_id}/multiseason_branch_replay_0_7a.json", report)
    print(out)
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Alternate History 0.7a chronological branch replay")
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--max-branches", type=int, default=DEFAULT_MAX_BRANCHES)
    parser.add_argument("--min-probability", type=float, default=DEFAULT_MIN_PROBABILITY)
    args = parser.parse_args()
    run(args.scenario, max_branches=args.max_branches, min_probability=args.min_probability)


if __name__ == "__main__":
    main()
