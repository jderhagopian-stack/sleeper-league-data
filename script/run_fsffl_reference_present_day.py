#!/usr/bin/env python3
"""Alternate History present-day reference path.

This is NOT the final expected alternate roster. It is a cheap deterministic
reference path used for debugging and later branch aggregation:
- apply the counterfactual fork;
- preserve every subsequent completed historical event when it remains legal;
- where archival control reconstruction is inconsistent, trust the completed
  historical event and synchronize both paths;
- where the alternate path makes a historical event mechanically impossible,
  preserve the divergence and skip that event on the alternate path.

Behavioral 0.5b/Monte-Carlo branches will later produce the expected/modal
present-day distribution. This reference path supplies a lower-cost baseline.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Set

import alternate_history_engine as ah
from run_fsffl_alternate_history import FSFFLHistoricalAdapter
from run_fsffl_downstream_dependencies import apply_forward_event, event_legality, load, player_owner


def owner_index(state: ah.LeagueState) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for rid, players in state.roster_players.items():
        for pid in players:
            out[str(pid)] = str(rid)
    return out


def player_name_index(adapter: FSFFLHistoricalAdapter) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for pid, row in adapter.players.items():
        out[str(pid)] = str(row.get("full_name") or f"{row.get('first_name') or ''} {row.get('last_name') or ''}".strip() or pid)
    return out


def run(scenario_path: Path) -> Path:
    payload = load(scenario_path)
    adapter = FSFFLHistoricalAdapter()
    scenario = ah.scenario_from_json(adapter, payload)

    actual = ah.reconstruct_state(adapter, scenario.fork_timestamp_ms)
    alternate = ah.apply_fork(actual, scenario)
    actual = copy.deepcopy(actual)

    skipped_alt: List[Dict[str, Any]] = []
    archive_resyncs = 0

    for event in adapter.completed_events():
        if int(event.get("created") or 0) < scenario.fork_timestamp_ms:
            continue
        actual_legal, actual_reasons = event_legality(actual, event)
        alternate_legal, alternate_reasons = event_legality(alternate, event)

        if not actual_legal:
            # Completed historical observation outranks incomplete reconstructed
            # pre-state. Synchronize both as the deterministic reference path.
            apply_forward_event(actual, event)
            apply_forward_event(alternate, event)
            archive_resyncs += 1
            continue

        apply_forward_event(actual, event)
        if alternate_legal:
            apply_forward_event(alternate, event)
        else:
            skipped_alt.append(
                {
                    "transaction_id": str(event.get("transaction_id")),
                    "created": int(event.get("created") or 0),
                    "type": str(event.get("type") or "unknown"),
                    "reasons": alternate_reasons,
                }
            )

    names = player_name_index(adapter)
    actual_owner = owner_index(actual)
    alternate_owner = owner_index(alternate)
    all_players = set(actual_owner) | set(alternate_owner)
    player_diffs = []
    for pid in sorted(all_players):
        a, b = actual_owner.get(pid), alternate_owner.get(pid)
        if a != b:
            player_diffs.append(
                {
                    "player_id": pid,
                    "name": names.get(pid, pid),
                    "actual_roster_id": a,
                    "alternate_roster_id": b,
                }
            )

    all_picks = set(actual.pick_owners) | set(alternate.pick_owners)
    pick_diffs = []
    for key in sorted(all_picks):
        a, b = actual.pick_owners.get(key), alternate.pick_owners.get(key)
        if a != b:
            pick_diffs.append(
                {
                    "pick_key": key,
                    "actual_roster_id": a,
                    "alternate_roster_id": b,
                }
            )

    focus = str(scenario.focus_roster_id)
    report = {
        "model_version": "Fantasy-Alternate-History-reference-present-day-0.5",
        "scenario_id": scenario.scenario_id,
        "status": "REFERENCE_PATH_NOT_EXPECTED_DISTRIBUTION",
        "design_invariants": {
            "completed_nfl_history_is_immutable": True,
            "all_legal_historical_fantasy_events_provisionally_preserved": True,
            "mechanically_impossible_alternate_events_skipped": True,
            "behavioral_branching_not_yet_applied": True,
        },
        "summary": {
            "archive_resynchronizations": archive_resyncs,
            "mechanically_skipped_alternate_events": len(skipped_alt),
            "player_ownership_differences_today": len(player_diffs),
            "pick_ownership_differences_today": len(pick_diffs),
        },
        "focus_actual_players": [
            {"player_id": pid, "name": names.get(pid, pid)}
            for pid in sorted(actual.roster_players.get(focus, set()), key=lambda x: names.get(x, x))
        ],
        "focus_reference_alternate_players": [
            {"player_id": pid, "name": names.get(pid, pid)}
            for pid in sorted(alternate.roster_players.get(focus, set()), key=lambda x: names.get(x, x))
        ],
        "player_ownership_differences": player_diffs,
        "pick_ownership_differences": pick_diffs,
        "mechanically_skipped_alternate_events": skipped_alt,
        "warning": (
            "This is a deterministic preserved-history reference path, not the expected present-day alternate roster. "
            "0.5b behavioral branching, alternate drafts, and multi-season branch aggregation must run before user-facing expected roster probabilities are produced."
        ),
    }
    return ah.write_isolated_json(
        f"results/{scenario.scenario_id}/present_day_reference.json", report
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Alternate History present-day reference path")
    parser.add_argument("scenario", type=Path)
    args = parser.parse_args()
    out = run(args.scenario)
    report = load(out)
    print(out)
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    print(json.dumps({"player_differences": report["player_ownership_differences"][:20]}, indent=2))


if __name__ == "__main__":
    main()
