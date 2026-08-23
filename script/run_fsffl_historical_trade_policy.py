#!/usr/bin/env python3
"""FSFFL Alternate History 0.5d: historical-safe strategic trade policy.

This layer resolves the HISTORICAL_GM_REQUIRED queue into a compact probability
set for branch replay. It intentionally does NOT use current GM 3.0 values,
current market ranks, or future NFL outcomes.

Historical-safe evidence:
- the accepted historical trade itself as a revealed-action prior;
- exact pre-transaction ownership / mechanical legality;
- which roster/player positions are actually divergent in the counterfactual;
- whether the historical trade directly touches a divergent asset;
- the recorded transaction structure and participants.

0.5d does not invent replacement packages. `modified_trade_branch` means 0.7
must generate a small historically plausible package set at that timestamp.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import alternate_history_engine as ah
from run_fsffl_alternate_history import FSFFLHistoricalAdapter
from run_fsffl_downstream_dependencies import event_legality, load
from run_fsffl_historical_policy_triage import run as run_triage

DATA = Path("data")


def player_positions() -> Dict[str, str]:
    raw = load(DATA / "players.json")
    return {str(pid): str(row.get("position") or "").upper() for pid, row in raw.items()}


def owner_of(state: ah.LeagueState, pid: str) -> Optional[str]:
    pid = str(pid)
    for rid, players in state.roster_players.items():
        if pid in {str(x) for x in players}:
            return str(rid)
    return None


def divergent_players(actual: ah.LeagueState, alternate: ah.LeagueState) -> Set[str]:
    all_ids: Set[str] = set()
    for state in (actual, alternate):
        for players in state.roster_players.values():
            all_ids |= {str(x) for x in players}
    return {pid for pid in all_ids if owner_of(actual, pid) != owner_of(alternate, pid)}


def normalized_probs(preserve: float, modified: float, no_trade: float) -> Dict[str, float]:
    vals = [max(0.0, preserve), max(0.0, modified), max(0.0, no_trade)]
    total = sum(vals) or 1.0
    return {
        "preserve_historical_trade": round(vals[0] / total, 4),
        "modified_trade_branch": round(vals[1] / total, 4),
        "no_trade": round(vals[2] / total, 4),
    }


def evaluate(
    adapter: FSFFLHistoricalAdapter,
    scenario: ah.Scenario,
    event: Dict[str, Any],
    positions: Dict[str, str],
) -> Dict[str, Any]:
    created = int(event.get("created") or 0)
    actual_pre = ah.reconstruct_state(adapter, created)
    alternate_pre = ah.apply_fork(actual_pre, scenario)
    legal, reasons = event_legality(alternate_pre, event)

    div_players = divergent_players(actual_pre, alternate_pre)
    event_players = {str(x) for x in (event.get("adds") or {}).keys()} | {
        str(x) for x in (event.get("drops") or {}).keys()
    }
    direct = sorted(event_players & div_players)
    divergent_positions = {positions.get(pid, "") for pid in div_players if positions.get(pid, "")}
    trade_positions = {positions.get(pid, "") for pid in event_players if positions.get(pid, "")}
    same_position = sorted(divergent_positions & trade_positions)
    participants = sorted(str(x) for x in (event.get("roster_ids") or []))
    divergent_rosters = sorted({
        rid for pid in div_players
        for rid in (owner_of(actual_pre, pid), owner_of(alternate_pre, pid))
        if rid is not None
    })
    divergent_participants = sorted(set(participants) & set(divergent_rosters))

    if not legal:
        probs = normalized_probs(0.0, 0.68, 0.32)
        classification = "EXACT_TERMS_IMPOSSIBLE"
        confidence = "HIGH"
        reason = "counterfactual_pre_state_fails_exact_trade_legality"
    elif direct:
        probs = normalized_probs(0.20, 0.55, 0.25)
        classification = "DIRECT_DIVERGENT_ASSET_TRADE"
        confidence = "MEDIUM"
        reason = "historical_trade_directly_touches_counterfactually_divergent_asset"
    elif same_position and divergent_participants:
        probs = normalized_probs(0.62, 0.23, 0.15)
        classification = "POSITIONALLY_SENSITIVE_TRADE"
        confidence = "MEDIUM"
        reason = "historical_trade_remains_legal_but_alternate_roster_changes_same_position_context"
    else:
        probs = normalized_probs(0.82, 0.10, 0.08)
        classification = "REVEALED_ACTION_PRESERVE_LEAN"
        confidence = "MEDIUM_HIGH"
        reason = "historical_trade_remains_legal_and_counterfactual_does_not_directly_change_terms"

    return {
        "transaction_id": str(event.get("transaction_id") or ""),
        "timestamp_ms": created,
        "source": (event.get("metadata") or {}).get("source"),
        "participants": participants,
        "event_player_ids": sorted(event_players),
        "event_positions": sorted(trade_positions),
        "divergent_player_ids_at_event": sorted(div_players),
        "divergent_positions_at_event": sorted(divergent_positions),
        "divergent_participants": divergent_participants,
        "direct_divergent_assets_in_trade": direct,
        "same_divergent_positions": same_position,
        "exact_terms_legal": bool(legal),
        "exact_terms_legality_reasons": reasons,
        "classification": classification,
        "probabilities": probs,
        "confidence": confidence,
        "reason": reason,
    }


def run(scenario_path: Path) -> Path:
    adapter = FSFFLHistoricalAdapter()
    scenario = ah.scenario_from_json(adapter, load(scenario_path))
    triage = load(run_triage(scenario_path))
    events = {str(e.get("transaction_id")): e for e in adapter.completed_events()}
    positions = player_positions()

    queue = [
        row for row in (triage.get("decision_queue") or [])
        if row.get("classification") == "HISTORICAL_GM_REQUIRED"
    ]
    decisions: List[Dict[str, Any]] = []
    missing: List[str] = []
    for row in queue:
        tid = str(row.get("transaction_id"))
        event = events.get(tid)
        if event is None:
            missing.append(tid)
            continue
        decisions.append(evaluate(adapter, scenario, event, positions))

    expected = {
        key: round(sum(float(d["probabilities"].get(key) or 0.0) for d in decisions), 3)
        for key in ("preserve_historical_trade", "modified_trade_branch", "no_trade")
    }
    class_counts: Dict[str, int] = {}
    conf_counts: Dict[str, int] = {}
    for d in decisions:
        class_counts[d["classification"]] = class_counts.get(d["classification"], 0) + 1
        conf_counts[d["confidence"]] = conf_counts.get(d["confidence"], 0) + 1

    report = {
        "model_version": "Fantasy-Alternate-History-0.5d-historical-trade-policy",
        "scenario_id": scenario.scenario_id,
        "design_invariants": {
            "current_gm3_numeric_values_used": False,
            "current_market_values_used": False,
            "future_nfl_outcomes_used": False,
            "historical_accepted_trade_is_revealed_action_prior": True,
            "replacement_trade_packages_generated_here": False,
            "local_reference_state_only": True,
        },
        "policy_note": (
            "Structural probability layer only. Probabilities are transparent priors, not claimed as point-in-time market-value calibration. Modified branches are expanded later using timestamp-safe evidence."
        ),
        "queued_trade_events": len(queue),
        "evaluated_trade_events": len(decisions),
        "missing_transaction_ids": missing,
        "expected_branch_counts": expected,
        "classification_counts": class_counts,
        "confidence_counts": conf_counts,
        "decisions": decisions,
    }
    out = ah.write_isolated_json(
        f"results/{scenario.scenario_id}/historical_trade_policy_0_5d.json", report
    )
    print(out)
    print(json.dumps({
        "queued_trade_events": len(queue),
        "expected_branch_counts": expected,
        "classification_counts": class_counts,
        "confidence_counts": conf_counts,
        "missing_transaction_ids": missing,
    }, indent=2, sort_keys=True))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Alternate History 0.5d historical trade policy")
    parser.add_argument("scenario", type=Path)
    args = parser.parse_args()
    run(args.scenario)


if __name__ == "__main__":
    main()
