#!/usr/bin/env python3
"""FSFFL Alternate History 0.5a v4: relevance-aware causal pruning.

A roster remaining different does not mean every later click by that owner must
branch. With a one-for-one fork, unrelated housekeeping moves can remain actual
history unless they touch a divergent asset, compete for the same positional
roster role, or are strategic trades whose desirability can change with the
alternate roster.

This stage is still mechanical: it identifies what deserves behavioral review;
it does not decide whether the historical action would occur.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import alternate_history_engine as ah
from run_fsffl_alternate_history import FSFFLHistoricalAdapter
from run_fsffl_downstream_dependencies import apply_forward_event, event_legality, load, player_owner
from run_fsffl_downstream_dependencies_v2 import asset_diff, divergent_rosters
from run_fsffl_downstream_dependencies_v3 import reason_assets

DATA = Path("data")


def player_positions() -> Dict[str, str]:
    raw = load(DATA / "players.json")
    return {str(pid): str(row.get("position") or "") for pid, row in raw.items()}


def all_divergent_player_ids(actual: ah.LeagueState, alternate: ah.LeagueState) -> Set[str]:
    out: Set[str] = set()
    all_players: Set[str] = set()
    for state in (actual, alternate):
        for players in state.roster_players.values():
            all_players |= {str(x) for x in players}
    for pid in all_players:
        if player_owner(actual, pid) != player_owner(alternate, pid):
            out.add(pid)
    return out


def all_divergent_pick_keys(actual: ah.LeagueState, alternate: ah.LeagueState) -> Set[str]:
    keys = set(actual.pick_owners) | set(alternate.pick_owners)
    return {k for k in keys if actual.pick_owners.get(k) != alternate.pick_owners.get(k)}


def event_player_ids(event: Dict[str, Any]) -> Set[str]:
    out = {str(x) for x in (event.get("adds") or {}).keys()}
    out |= {str(x) for x in (event.get("drops") or {}).keys()}
    return out


def event_pick_keys(event: Dict[str, Any]) -> Set[str]:
    out: Set[str] = set()
    for pick in event.get("draft_picks") or []:
        key = ah.pick_key(pick)
        if key:
            out.add(key)
    return out


def event_positions(event: Dict[str, Any], positions: Dict[str, str]) -> Set[str]:
    return {positions.get(pid, "") for pid in event_player_ids(event) if positions.get(pid, "")}


def relevance(
    actual: ah.LeagueState,
    alternate: ah.LeagueState,
    event: Dict[str, Any],
    positions: Dict[str, str],
) -> Dict[str, Any]:
    divergent_players = all_divergent_player_ids(actual, alternate)
    divergent_picks = all_divergent_pick_keys(actual, alternate)
    divergent_roster_ids = divergent_rosters(actual, alternate)
    event_rosters = {str(x) for x in (event.get("roster_ids") or [])}
    ep = event_player_ids(event)
    ek = event_pick_keys(event)
    direct_players = sorted(ep & divergent_players)
    direct_picks = sorted(ek & divergent_picks)
    event_type = str(event.get("type") or "unknown")

    divergent_positions = {
        positions.get(pid, "") for pid in divergent_players if positions.get(pid, "")
    }
    same_position = sorted(event_positions(event, positions) & divergent_positions)
    owner_touched = bool(event_rosters & divergent_roster_ids)

    if direct_players or direct_picks:
        band = "DIRECT_ASSET"
        requires_review = True
    elif event_type == "trade" and owner_touched:
        band = "STRATEGIC_TRADE"
        requires_review = True
    elif event_type in {"waiver", "free_agent"} and owner_touched and same_position:
        band = "SAME_POSITION_ROSTER_DECISION"
        requires_review = True
    elif owner_touched:
        band = "UNRELATED_HOUSEKEEPING"
        requires_review = False
    else:
        band = "INVARIANT"
        requires_review = False

    return {
        "band": band,
        "requires_review": requires_review,
        "direct_divergent_players": direct_players,
        "direct_divergent_picks": direct_picks,
        "same_divergent_positions": same_position,
        "divergent_owner_touched": owner_touched,
        "divergent_player_ids": sorted(divergent_players),
        "divergent_pick_keys": sorted(divergent_picks),
    }


def run(scenario_path: Path) -> Path:
    payload = load(scenario_path)
    adapter = FSFFLHistoricalAdapter()
    scenario = ah.scenario_from_json(adapter, payload)
    positions = player_positions()

    actual = ah.reconstruct_state(adapter, scenario.fork_timestamp_ms)
    alternate = ah.apply_fork(actual, scenario)
    actual = copy.deepcopy(actual)

    branch_candidates: List[Dict[str, Any]] = []
    archive_gaps: List[Dict[str, Any]] = []
    band_counts: Dict[str, int] = {}
    counts = {
        "invariant": 0,
        "review": 0,
        "forced_invalid": 0,
        "historical_gap_invariant": 0,
        "historical_gap_causal_uncertain": 0,
    }

    for event in adapter.completed_events():
        created = int(event.get("created") or 0)
        if created < scenario.fork_timestamp_ms:
            continue
        txid = str(event.get("transaction_id"))
        rel = relevance(actual, alternate, event, positions)
        band_counts[rel["band"]] = band_counts.get(rel["band"], 0) + 1
        pre_asset_diff = {str(x["asset"]) for x in asset_diff(actual, alternate, event)}

        actual_legal, actual_reasons = event_legality(actual, event)
        alternate_legal, alternate_reasons = event_legality(alternate, event)

        if not actual_legal:
            disputed = reason_assets(actual_reasons)
            # A historical reconstruction gap is causal only if the disputed
            # asset itself is currently different. Merely sharing an owner is
            # insufficient: the observed transaction re-synchronizes history.
            causal_gap = bool(disputed & pre_asset_diff)
            gap_class = "historical_gap_causal_uncertain" if causal_gap else "historical_gap_invariant"
            counts[gap_class] += 1
            row = {
                "transaction_id": txid,
                "created": created,
                "type": str(event.get("type") or "unknown"),
                "classification": gap_class,
                "relevance": rel,
                "disputed_assets": sorted(disputed),
                "actual_reconstruction_reasons": actual_reasons,
            }
            archive_gaps.append(row)
            apply_forward_event(actual, event)
            apply_forward_event(alternate, event)
            if causal_gap:
                branch_candidates.append(row)
            continue

        if not alternate_legal:
            counts["forced_invalid"] += 1
            row = {
                "transaction_id": txid,
                "created": created,
                "type": str(event.get("type") or "unknown"),
                "classification": "forced_invalid",
                "relevance": rel,
                "alternate_legality_reasons": alternate_reasons,
            }
            branch_candidates.append(row)
            apply_forward_event(actual, event)
            continue

        if rel["requires_review"]:
            counts["review"] += 1
            row = {
                "transaction_id": txid,
                "created": created,
                "type": str(event.get("type") or "unknown"),
                "classification": "behavioral_review",
                "relevance": rel,
            }
            branch_candidates.append(row)
        else:
            counts["invariant"] += 1

        # Until 0.5b branches, all mechanically legal actions are provisionally
        # replayed in the alternate to preserve a single cheap reference path.
        apply_forward_event(actual, event)
        apply_forward_event(alternate, event)

    final_divergent = sorted(divergent_rosters(actual, alternate))
    gap_rate = len(archive_gaps) / max(1, sum(counts.values()))
    causal_gap_count = counts["historical_gap_causal_uncertain"]
    if causal_gap_count > 0:
        causal_confidence = "MEDIUM"
    elif gap_rate > 0.15:
        causal_confidence = "MEDIUM"
    else:
        causal_confidence = "HIGH"

    report = {
        "model_version": "Fantasy-Alternate-History-0.5a-relevance-pruned",
        "scenario_id": scenario.scenario_id,
        "design_invariants": {
            "completed_historical_transactions_are_authoritative": True,
            "completed_nfl_history_is_immutable": True,
            "owner_identity_alone_does_not_force_a_branch": True,
            "direct_asset_same_position_and_strategic_trade_relevance_are_reviewed": True,
            "no_gm_judgment_in_0_5a": True,
        },
        "summary": {
            "events_after_fork": sum(counts.values()),
            **counts,
            "archive_gap_events": len(archive_gaps),
            "archive_gap_rate": round(gap_rate, 4),
            "branch_candidate_events": len(branch_candidates),
            "relevance_band_counts": dict(sorted(band_counts.items())),
            "final_divergent_rosters": final_divergent,
            "causal_reconstruction_confidence": causal_confidence,
        },
        "branch_candidates": branch_candidates,
        "historical_reconstruction_gaps": archive_gaps,
        "scope_note": (
            "0.5a v4 minimizes downstream behavioral work. Unrelated housekeeping by a divergent owner remains historical fact unless it touches a divergent asset or positional competition. Strategic trades involving a divergent owner remain reviewable."
        ),
    }
    return ah.write_isolated_json(
        f"results/{scenario.scenario_id}/downstream_0_5a_v4.json", report
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run relevance-pruned Alternate History 0.5a")
    parser.add_argument("scenario", type=Path)
    args = parser.parse_args()
    out = run(args.scenario)
    report = load(out)
    print(out)
    print(json.dumps(report["summary"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
