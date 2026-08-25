#!/usr/bin/env python3
"""FSFFL Alternate History 0.5a: deterministic downstream dependency replay.

Purpose:
- start from the counterfactual fork state;
- walk subsequent historical transactions in chronological order;
- distinguish events that remain mechanically possible from events that become
  impossible because the required player/pick ownership changed;
- identify strategically affected but still mechanically legal events for
  later GM 3.0 behavioral evaluation.

This stage contains no Monte Carlo and no subjective GM choice model.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import alternate_history_engine as ah
from run_fsffl_alternate_history import FSFFLHistoricalAdapter

DATA = Path("data")


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def player_owner(state: ah.LeagueState, pid: str) -> Optional[str]:
    for rid, players in state.roster_players.items():
        if str(pid) in players:
            return str(rid)
    return None


def _clear_nonactive_membership(state: ah.LeagueState, pid: str) -> None:
    """Remove stale IR/taxi placement whenever ownership changes."""
    pid = str(pid)
    for players in state.roster_taxi.values():
        players.discard(pid)
    for players in state.roster_reserve.values():
        players.discard(pid)


def apply_forward_event(state: ah.LeagueState, event: Dict[str, Any]) -> None:
    # Player moves. Slot placement is not transferable with ownership; a player
    # who is traded/dropped must leave the old roster's taxi/IR subset too.
    for pid, rid in (event.get("drops") or {}).items():
        _clear_nonactive_membership(state, str(pid))
        state.roster_players.setdefault(str(rid), set()).discard(str(pid))
    for pid, rid in (event.get("adds") or {}).items():
        _clear_nonactive_membership(state, str(pid))
        # Defensive uniqueness: remove from any prior roster first.
        for players in state.roster_players.values():
            players.discard(str(pid))
        state.roster_players.setdefault(str(rid), set()).add(str(pid))

    # Pick moves.
    for pick in event.get("draft_picks") or []:
        key = ah.pick_key(pick)
        owner = pick.get("owner_id")
        if key and owner is not None:
            state.pick_owners[key] = str(owner)

    # FAAB transfers. The state stores the same abstract balance dimension used
    # by the 0.1 replay; exact platform semantics are not needed for legality.
    for row in event.get("waiver_budget") or []:
        amount = float(row.get("amount") or 0.0)
        sender, receiver = str(row.get("sender")), str(row.get("receiver"))
        state.faab[sender] = float(state.faab.get(sender, 0.0)) - amount
        state.faab[receiver] = float(state.faab.get(receiver, 0.0)) + amount


def event_legality(state: ah.LeagueState, event: Dict[str, Any]) -> Tuple[bool, List[Dict[str, Any]]]:
    reasons: List[Dict[str, Any]] = []
    event_type = str(event.get("type") or "")
    drops = {str(k): str(v) for k, v in (event.get("drops") or {}).items()}
    adds = {str(k): str(v) for k, v in (event.get("adds") or {}).items()}

    # Any player explicitly leaving a roster must actually be owned there.
    for pid, sender in drops.items():
        owner = player_owner(state, pid)
        if owner != sender:
            reasons.append(
                {
                    "kind": "missing_outgoing_player",
                    "player_id": pid,
                    "required_owner": sender,
                    "alternate_owner": owner,
                }
            )

    # For waiver/free-agent acquisitions, the acquired player must not already
    # be owned by another team. Trades are governed by outgoing ownership above.
    if event_type in {"waiver", "free_agent"}:
        for pid, receiver in adds.items():
            owner = player_owner(state, pid)
            if owner is not None and owner != receiver:
                reasons.append(
                    {
                        "kind": "free_agent_not_available",
                        "player_id": pid,
                        "requested_receiver": receiver,
                        "alternate_owner": owner,
                    }
                )

    # Traded picks must be controlled by their recorded prior owner when that
    # ownership is represented in the reconstructed state.
    for pick in event.get("draft_picks") or []:
        key = ah.pick_key(pick)
        previous = pick.get("previous_owner_id")
        if key and previous is not None and key in state.pick_owners:
            current = state.pick_owners.get(key)
            if current != str(previous):
                reasons.append(
                    {
                        "kind": "missing_outgoing_pick",
                        "pick_key": key,
                        "required_owner": str(previous),
                        "alternate_owner": current,
                    }
                )

    return (len(reasons) == 0), reasons


def assets_touched(event: Dict[str, Any]) -> Set[str]:
    assets: Set[str] = set()
    for pid in (event.get("adds") or {}).keys():
        assets.add(f"player:{pid}")
    for pid in (event.get("drops") or {}).keys():
        assets.add(f"player:{pid}")
    for pick in event.get("draft_picks") or []:
        key = ah.pick_key(pick)
        if key:
            assets.add(key)
    return assets


def run(scenario_path: Path) -> Path:
    payload = load(scenario_path)
    adapter = FSFFLHistoricalAdapter()
    scenario = ah.scenario_from_json(adapter, payload)

    historical = ah.reconstruct_state(adapter, scenario.fork_timestamp_ms)
    alternate = ah.apply_fork(historical, scenario)

    dirty_rosters: Set[str] = {str(scenario.focus_roster_id)}
    dirty_assets: Set[str] = set()
    for action in scenario.actions:
        dirty_rosters.add(str(action.roster_id))
        if action.add_player_id:
            dirty_assets.add(f"player:{action.add_player_id}")
            owner_before = player_owner(historical, action.add_player_id)
            if owner_before:
                dirty_rosters.add(owner_before)
        if action.drop_player_id:
            dirty_assets.add(f"player:{action.drop_player_id}")

    classifications: List[Dict[str, Any]] = []
    invalid_count = 0
    behavioral_count = 0
    invariant_count = 0
    mechanically_preserved_count = 0

    for event in adapter.completed_events():
        created = int(event.get("created") or 0)
        if created < scenario.fork_timestamp_ms:
            continue

        event_rosters = {str(x) for x in (event.get("roster_ids") or [])}
        event_assets = assets_touched(event)
        legal, reasons = event_legality(alternate, event)

        touches_dirty = bool(event_rosters & dirty_rosters or event_assets & dirty_assets)
        if not legal:
            classification = "forced_invalid"
            invalid_count += 1
            dirty_rosters |= event_rosters
            dirty_assets |= event_assets
            applied = False
        elif touches_dirty:
            classification = "behavioral_review"
            behavioral_count += 1
            apply_forward_event(alternate, event)
            dirty_rosters |= event_rosters
            dirty_assets |= event_assets
            applied = True
            mechanically_preserved_count += 1
        else:
            classification = "invariant"
            invariant_count += 1
            apply_forward_event(alternate, event)
            applied = True
            mechanically_preserved_count += 1

        if classification != "invariant":
            classifications.append(
                {
                    "transaction_id": str(event.get("transaction_id")),
                    "created": created,
                    "type": str(event.get("type") or "unknown"),
                    "source": str(event.get("source") or "unknown"),
                    "classification": classification,
                    "mechanically_applied": applied,
                    "roster_ids": sorted(event_rosters),
                    "assets": sorted(event_assets),
                    "legality_reasons": reasons,
                }
            )

    first_invalid = next(
        (x for x in classifications if x["classification"] == "forced_invalid"), None
    )
    first_behavioral = next(
        (x for x in classifications if x["classification"] == "behavioral_review"), None
    )

    report = {
        "model_version": "Fantasy-Alternate-History-0.5a-dependency-replay",
        "scenario_id": scenario.scenario_id,
        "design_invariants": {
            "completed_nfl_history_is_immutable": True,
            "historical_events_processed_chronologically": True,
            "transaction_legality_uses_branch_state": True,
            "taxi_reserve_membership_cleared_on_ownership_change": True,
            "no_gm_judgment_in_0_5a": True,
            "mechanical_legality_checked_before_behavioral_model": True,
            "canonical_data_is_read_only": True,
        },
        "summary": {
            "events_after_fork": invariant_count + behavioral_count + invalid_count,
            "invariant_events": invariant_count,
            "behavioral_review_events": behavioral_count,
            "forced_invalid_events": invalid_count,
            "mechanically_preserved_events": mechanically_preserved_count,
            "first_forced_invalid_transaction": (
                first_invalid.get("transaction_id") if first_invalid else None
            ),
            "first_behavioral_review_transaction": (
                first_behavioral.get("transaction_id") if first_behavioral else None
            ),
        },
        "non_invariant_events": classifications,
        "final_provisional_state_hash": ah.stable_hash(alternate.serializable()),
        "scope_note": (
            "0.5a only determines mechanical causality. Legal events involving causally affected owners/assets "
            "are provisionally replayed and labeled behavioral_review. 0.5b decides whether those events would "
            "still occur using GM policy; invalid events are never applied."
        ),
    }
    return ah.write_isolated_json(
        f"results/{scenario.scenario_id}/downstream_0_5a.json", report
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Alternate History 0.5a dependency replay")
    parser.add_argument("scenario", type=Path)
    args = parser.parse_args()
    out = run(args.scenario)
    report = load(out)
    print(out)
    print(json.dumps(report["summary"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
