#!/usr/bin/env python3
"""FSFFL Alternate History 0.5a v2: state-diff downstream dependency replay.

Unlike the first dependency pass, this version does not spread causal dirtiness
merely because an affected owner transacts with someone. It maintains actual
and alternate states in parallel and classifies each subsequent event from the
real pre-event state difference.

That keeps the later GM/Monte-Carlo branch set small and evidence-driven.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import alternate_history_engine as ah
from run_fsffl_alternate_history import FSFFLHistoricalAdapter
from run_fsffl_downstream_dependencies import (
    apply_forward_event,
    assets_touched,
    event_legality,
    load,
    player_owner,
)


def divergent_rosters(actual: ah.LeagueState, alternate: ah.LeagueState) -> Set[str]:
    ids = set(actual.roster_players) | set(alternate.roster_players)
    out: Set[str] = set()
    for rid in ids:
        if actual.roster_players.get(rid, set()) != alternate.roster_players.get(rid, set()):
            out.add(str(rid))
    return out


def asset_diff(
    actual: ah.LeagueState,
    alternate: ah.LeagueState,
    event: Dict[str, Any],
) -> List[Dict[str, Any]]:
    diffs: List[Dict[str, Any]] = []
    pids = set(str(x) for x in (event.get("adds") or {}).keys())
    pids |= set(str(x) for x in (event.get("drops") or {}).keys())
    for pid in sorted(pids):
        ao = player_owner(actual, pid)
        bo = player_owner(alternate, pid)
        if ao != bo:
            diffs.append({"asset": f"player:{pid}", "actual_owner": ao, "alternate_owner": bo})
    for pick in event.get("draft_picks") or []:
        key = ah.pick_key(pick)
        if not key:
            continue
        ao = actual.pick_owners.get(key)
        bo = alternate.pick_owners.get(key)
        if ao != bo:
            diffs.append({"asset": key, "actual_owner": ao, "alternate_owner": bo})
    return diffs


def run(scenario_path: Path) -> Path:
    payload = load(scenario_path)
    adapter = FSFFLHistoricalAdapter()
    scenario = ah.scenario_from_json(adapter, payload)

    actual = ah.reconstruct_state(adapter, scenario.fork_timestamp_ms)
    alternate = ah.apply_fork(actual, scenario)
    actual = copy.deepcopy(actual)

    rows: List[Dict[str, Any]] = []
    counts = {"invariant": 0, "behavioral_review": 0, "forced_invalid": 0}

    for event in adapter.completed_events():
        created = int(event.get("created") or 0)
        if created < scenario.fork_timestamp_ms:
            continue

        event_rosters = {str(x) for x in (event.get("roster_ids") or [])}
        pre_divergent_rosters = divergent_rosters(actual, alternate)
        pre_asset_diff = asset_diff(actual, alternate, event)

        actual_legal, actual_reasons = event_legality(actual, event)
        if not actual_legal:
            raise ah.AlternateHistoryError(
                f"Historical event {event.get('transaction_id')} is illegal in reconstructed actual state: {actual_reasons}"
            )

        alternate_legal, alternate_reasons = event_legality(alternate, event)
        if not alternate_legal:
            classification = "forced_invalid"
            counts[classification] += 1
            applied_alt = False
        elif event_rosters & pre_divergent_rosters or pre_asset_diff:
            classification = "behavioral_review"
            counts[classification] += 1
            apply_forward_event(alternate, event)
            applied_alt = True
        else:
            classification = "invariant"
            counts[classification] += 1
            apply_forward_event(alternate, event)
            applied_alt = True

        # Actual history always advances through the recorded event.
        apply_forward_event(actual, event)

        if classification != "invariant":
            post_divergent = divergent_rosters(actual, alternate)
            rows.append(
                {
                    "transaction_id": str(event.get("transaction_id")),
                    "created": created,
                    "type": str(event.get("type") or "unknown"),
                    "source": str(event.get("source") or "unknown"),
                    "classification": classification,
                    "mechanically_applied_to_alternate": applied_alt,
                    "roster_ids": sorted(event_rosters),
                    "assets": sorted(assets_touched(event)),
                    "pre_event_divergent_rosters": sorted(pre_divergent_rosters),
                    "pre_event_asset_differences": pre_asset_diff,
                    "alternate_legality_reasons": alternate_reasons,
                    "post_event_divergent_rosters": sorted(post_divergent),
                }
            )

    first_invalid = next((x for x in rows if x["classification"] == "forced_invalid"), None)
    first_behavioral = next((x for x in rows if x["classification"] == "behavioral_review"), None)
    final_divergent = divergent_rosters(actual, alternate)

    report = {
        "model_version": "Fantasy-Alternate-History-0.5a-state-diff",
        "scenario_id": scenario.scenario_id,
        "design_invariants": {
            "completed_nfl_history_is_immutable": True,
            "actual_and_alternate_states_advanced_in_parallel": True,
            "causal_scope_based_on_real_state_difference": True,
            "no_counterparty_contagion_without_state_difference": True,
            "no_gm_judgment_in_0_5a": True,
        },
        "summary": {
            "events_after_fork": sum(counts.values()),
            "invariant_events": counts["invariant"],
            "behavioral_review_events": counts["behavioral_review"],
            "forced_invalid_events": counts["forced_invalid"],
            "non_invariant_events": len(rows),
            "first_forced_invalid_transaction": first_invalid.get("transaction_id") if first_invalid else None,
            "first_behavioral_review_transaction": first_behavioral.get("transaction_id") if first_behavioral else None,
            "final_divergent_rosters": sorted(final_divergent),
        },
        "non_invariant_events_detail": rows,
        "actual_final_state_hash": ah.stable_hash(actual.serializable()),
        "alternate_provisional_final_state_hash": ah.stable_hash(alternate.serializable()),
        "scope_note": (
            "0.5a v2 identifies the minimal mechanically causal transaction set. "
            "Only behavioral_review events proceed to GM policy. forced_invalid events require an alternate action or no-action branch."
        ),
    }
    return ah.write_isolated_json(
        f"results/{scenario.scenario_id}/downstream_0_5a_v2.json", report
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Alternate History 0.5a state-diff dependency replay")
    parser.add_argument("scenario", type=Path)
    args = parser.parse_args()
    out = run(args.scenario)
    report = load(out)
    print(out)
    print(json.dumps(report["summary"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
