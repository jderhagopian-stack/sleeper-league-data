#!/usr/bin/env python3
"""FSFFL Alternate History 0.5c v2: adapter-contract historical usage policy.

Same historical-safe policy as 0.5c, implemented against the normalized dict
contract returned by LeagueAdapter.completed_events().
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import alternate_history_engine as ah
from run_fsffl_alternate_history import FSFFLHistoricalAdapter
from run_fsffl_historical_policy_triage import run as run_triage
from run_fsffl_historical_usage_policy import HistoricalPoints, positions_index, score_or_zero, softmax
from run_fsffl_downstream_dependencies import load


def owner_of(state: ah.LeagueState, pid: str) -> Optional[str]:
    pid = str(pid)
    for rid, players in state.roster_players.items():
        if pid in {str(x) for x in players}:
            return str(rid)
    return None


def roster_players(state: ah.LeagueState, rid: str) -> List[str]:
    return sorted(str(x) for x in state.roster_players.get(str(rid), set()))


def event_season_week(event: Dict[str, Any]) -> Tuple[Optional[str], Optional[int]]:
    meta = event.get("metadata") or {}
    season = meta.get("source_season") or meta.get("season") or event.get("season")
    week = meta.get("leg") or meta.get("week") or event.get("leg") or event.get("week")
    try:
        parsed_week = int(week) if week is not None else None
    except (TypeError, ValueError):
        parsed_week = None
    return (str(season) if season is not None else None, parsed_week)


def evaluate_event(
    adapter: FSFFLHistoricalAdapter,
    scenario: ah.Scenario,
    event: Dict[str, Any],
    positions: Dict[str, str],
    points: HistoricalPoints,
) -> Dict[str, Any]:
    timestamp_ms = int(event.get("created") or 0)
    season, week = event_season_week(event)
    actual_pre = ah.reconstruct_state(adapter, timestamp_ms)
    alt_pre = ah.apply_fork(actual_pre, scenario)

    adds_by_roster: Dict[str, List[str]] = defaultdict(list)
    drops_by_roster: Dict[str, List[str]] = defaultdict(list)
    for pid, rid in (event.get("adds") or {}).items():
        adds_by_roster[str(rid)].append(str(pid))
    for pid, rid in (event.get("drops") or {}).items():
        drops_by_roster[str(rid)].append(str(pid))

    decisions = []
    target_rosters = sorted(set(adds_by_roster) | set(drops_by_roster))
    for rid in target_rosters:
        added = adds_by_roster.get(rid, [])
        dropped = drops_by_roster.get(rid, [])
        roster = roster_players(alt_pre, rid)

        added_rows = []
        all_add_available = True
        for pid in added:
            alt_owner = owner_of(alt_pre, pid)
            available = alt_owner is None or str(alt_owner) == rid
            all_add_available = all_add_available and available
            sig = points.trailing(season, week, pid) if season else {
                "score": None, "observations": 0, "reason": "missing_season"
            }
            added_rows.append({
                "player_id": pid,
                "position": positions.get(pid, ""),
                "alternate_pre_owner": alt_owner,
                "available_to_recorded_roster": available,
                "trailing_signal": sig,
            })

        drop_rows = []
        actual_drop_still_owned = True
        for pid in dropped:
            owned = owner_of(alt_pre, pid) == rid
            actual_drop_still_owned = actual_drop_still_owned and owned
            sig = points.trailing(season, week, pid) if season else {
                "score": None, "observations": 0, "reason": "missing_season"
            }
            drop_rows.append({
                "player_id": pid,
                "position": positions.get(pid, ""),
                "still_owned_in_alternate_pre_state": owned,
                "trailing_signal": sig,
            })

        add_positions = {positions.get(pid, "") for pid in added if positions.get(pid, "")}
        incumbent_rows = []
        for pid in roster:
            pos = positions.get(pid, "")
            if add_positions and pos not in add_positions:
                continue
            sig = points.trailing(season, week, pid) if season else {
                "score": None, "observations": 0, "reason": "missing_season"
            }
            incumbent_rows.append({"player_id": pid, "position": pos, "trailing_signal": sig})
        incumbent_rows.sort(key=lambda x: (score_or_zero(x["trailing_signal"]), x["player_id"]))

        alternate_drop = None
        for row in incumbent_rows:
            if row["player_id"] not in set(added):
                alternate_drop = row
                break

        add_scores = [
            score_or_zero(x["trailing_signal"])
            for x in added_rows if x["trailing_signal"].get("score") is not None
        ]
        add_obs = sum(int(x["trailing_signal"].get("observations") or 0) for x in added_rows)
        weakest_score = None
        if alternate_drop and alternate_drop["trailing_signal"].get("score") is not None:
            weakest_score = score_or_zero(alternate_drop["trailing_signal"])
        improvement = None
        if add_scores and weakest_score is not None:
            improvement = (sum(add_scores) / len(add_scores)) - weakest_score

        preserve_acquisition_logit = 1.65
        if improvement is not None:
            preserve_acquisition_logit += max(-1.4, min(1.4, improvement / 8.0))
        elif add_obs == 0:
            preserve_acquisition_logit -= 0.35

        if not all_add_available:
            probs = {"preserve_exact": 0.0, "preserve_add_change_drop": 0.0, "no_action": 1.0}
            reason = "recorded_add_not_available_in_counterfactual_pre_state"
        else:
            exact_logit = preserve_acquisition_logit + (0.8 if actual_drop_still_owned else -math.inf)
            change_logit = preserve_acquisition_logit - 0.35 + (1.1 if not actual_drop_still_owned else -0.5)
            probs = softmax({
                "preserve_exact": exact_logit,
                "preserve_add_change_drop": change_logit,
                "no_action": 0.0,
            })
            reason = "historical_revealed_action_prior_adjusted_by_preweek_usage_and_alt_roster"

        obs = add_obs + sum(
            int(x["trailing_signal"].get("observations") or 0) for x in incumbent_rows[:3]
        )
        if week is None or season is None:
            confidence = "LOW"
        elif obs >= 6:
            confidence = "MEDIUM_HIGH"
        elif obs >= 2:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        decisions.append({
            "roster_id": rid,
            "season": season,
            "week": week,
            "added": added_rows,
            "actual_dropped": drop_rows,
            "alternate_same_position_incumbents": incumbent_rows[:8],
            "suggested_alternate_drop": alternate_drop,
            "add_vs_weakest_incumbent_trailing_delta": round(improvement, 4) if improvement is not None else None,
            "probabilities": probs,
            "confidence": confidence,
            "reason": reason,
        })

    return {
        "transaction_id": str(event.get("transaction_id") or ""),
        "timestamp_ms": timestamp_ms,
        "event_type": str(event.get("type") or "unknown"),
        "source": (event.get("metadata") or {}).get("source"),
        "decisions": decisions,
    }


def run(scenario_path: Path) -> Path:
    adapter = FSFFLHistoricalAdapter()
    payload = load(scenario_path)
    scenario = ah.scenario_from_json(adapter, payload)
    triage = load(run_triage(scenario_path))
    events = adapter.completed_events()
    event_by_id = {str(e.get("transaction_id")): e for e in events}
    positions = positions_index()
    points = HistoricalPoints()

    queue = [
        row for row in (triage.get("decision_queue") or [])
        if row.get("classification") == "HISTORICAL_USAGE_POLICY"
    ]
    results = []
    missing = []
    for row in queue:
        tid = str(row.get("transaction_id"))
        event = event_by_id.get(tid)
        if event is None:
            missing.append(tid)
            continue
        results.append(evaluate_event(adapter, scenario, event, positions, points))

    flattened = [d for row in results for d in row.get("decisions") or []]
    expected = {
        "preserve_exact": round(sum(float(d["probabilities"].get("preserve_exact") or 0.0) for d in flattened), 3),
        "preserve_add_change_drop": round(sum(float(d["probabilities"].get("preserve_add_change_drop") or 0.0) for d in flattened), 3),
        "no_action": round(sum(float(d["probabilities"].get("no_action") or 0.0) for d in flattened), 3),
    }
    conf: Dict[str, int] = defaultdict(int)
    for d in flattened:
        conf[str(d.get("confidence"))] += 1

    report = {
        "model_version": "Fantasy-Alternate-History-0.5c-v2-historical-usage",
        "scenario_id": scenario.scenario_id,
        "design_invariants": {
            "future_nfl_outcomes_used": False,
            "current_week_realized_points_used": False,
            "current_gm3_numeric_values_used": False,
            "completed_prior_week_scoring_only": True,
            "historical_completed_transaction_is_revealed_action_prior": True,
            "local_reference_state_only": True,
        },
        "policy_parameters": {
            "trailing_weeks": 3,
            "recency_weights": [0.15, 0.30, 0.55],
            "historical_action_base_logit": 1.65,
            "usage_delta_scale_points": 8.0,
            "note": "Transparent heuristic, not claimed as empirically calibrated. 0.7 reruns against accumulated branch state.",
        },
        "queued_usage_events": len(queue),
        "evaluated_transactions": len(results),
        "evaluated_roster_decisions": len(flattened),
        "missing_transaction_ids": missing,
        "expected_decision_counts": expected,
        "confidence_counts": dict(conf),
        "historical_points_sources": points.sources,
        "decisions": results,
    }
    out = ah.write_isolated_json(
        f"results/{scenario.scenario_id}/historical_usage_policy_0_5c.json", report
    )
    print(out)
    print(json.dumps({
        "queued_usage_events": len(queue),
        "evaluated_roster_decisions": len(flattened),
        "expected_decision_counts": expected,
        "confidence_counts": dict(conf),
        "missing_transaction_ids": missing,
    }, indent=2, sort_keys=True))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Alternate History 0.5c v2 historical usage policy")
    parser.add_argument("scenario", type=Path)
    args = parser.parse_args()
    run(args.scenario)


if __name__ == "__main__":
    main()
