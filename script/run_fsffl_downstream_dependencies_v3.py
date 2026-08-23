#!/usr/bin/env python3
"""FSFFL Alternate History 0.5a v3: causal replay with archive-gap handling.

Historical completed transactions are authoritative observations. Reconstructed
pre-event ownership is derived and can be incomplete for older Sleeper history.
When the control reconstruction disagrees with a recorded completed event, this
runner records a historical_reconstruction_gap and re-synchronizes from the
observed event instead of falsely labeling the historical event impossible.

A reconstruction gap only enters the counterfactual branch set if the disputed
asset/roster is already different between actual and alternate states.
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
from run_fsffl_downstream_dependencies_v2 import asset_diff, divergent_rosters


def reason_assets(reasons: List[Dict[str, Any]]) -> Set[str]:
    out: Set[str] = set()
    for row in reasons:
        if row.get("player_id") is not None:
            out.add(f"player:{row['player_id']}")
        if row.get("pick_key"):
            out.add(str(row["pick_key"]))
    return out


def event_assets(event: Dict[str, Any]) -> Set[str]:
    out: Set[str] = set()
    for pid in (event.get("adds") or {}).keys():
        out.add(f"player:{pid}")
    for pid in (event.get("drops") or {}).keys():
        out.add(f"player:{pid}")
    for pick in event.get("draft_picks") or []:
        key = ah.pick_key(pick)
        if key:
            out.add(key)
    return out


def current_asset_differences(actual: ah.LeagueState, alternate: ah.LeagueState, event: Dict[str, Any]) -> Set[str]:
    return {str(x["asset"]) for x in asset_diff(actual, alternate, event)}


def run(scenario_path: Path) -> Path:
    payload = load(scenario_path)
    adapter = FSFFLHistoricalAdapter()
    scenario = ah.scenario_from_json(adapter, payload)

    actual = ah.reconstruct_state(adapter, scenario.fork_timestamp_ms)
    alternate = ah.apply_fork(actual, scenario)
    actual = copy.deepcopy(actual)

    detail: List[Dict[str, Any]] = []
    archive_gaps: List[Dict[str, Any]] = []
    counts = {
        "invariant": 0,
        "behavioral_review": 0,
        "forced_invalid": 0,
        "historical_gap_invariant": 0,
        "historical_gap_causal_uncertain": 0,
    }

    for event in adapter.completed_events():
        created = int(event.get("created") or 0)
        if created < scenario.fork_timestamp_ms:
            continue

        txid = str(event.get("transaction_id"))
        event_rosters = {str(x) for x in (event.get("roster_ids") or [])}
        pre_roster_diff = divergent_rosters(actual, alternate)
        pre_asset_diff = current_asset_differences(actual, alternate, event)

        actual_legal, actual_reasons = event_legality(actual, event)
        alternate_legal, alternate_reasons = event_legality(alternate, event)

        if not actual_legal:
            gap_assets = reason_assets(actual_reasons)
            gap_is_causal = bool(
                gap_assets & pre_asset_diff
                or event_rosters & pre_roster_diff
            )
            gap_class = (
                "historical_gap_causal_uncertain"
                if gap_is_causal
                else "historical_gap_invariant"
            )
            counts[gap_class] += 1
            gap_row = {
                "transaction_id": txid,
                "created": created,
                "type": str(event.get("type") or "unknown"),
                "source": str(event.get("source") or "unknown"),
                "classification": gap_class,
                "actual_reconstruction_reasons": actual_reasons,
                "alternate_legality_reasons": alternate_reasons,
                "pre_event_divergent_rosters": sorted(pre_roster_diff),
                "pre_event_asset_differences": sorted(pre_asset_diff),
                "disputed_assets": sorted(gap_assets),
            }
            archive_gaps.append(gap_row)

            # The recorded completed historical event is authoritative for the
            # control. For an unrelated gap it is also the best synchronization
            # point for the alternate state. For a causal gap we still advance
            # provisionally but retain it in the unresolved branch set.
            apply_forward_event(actual, event)
            apply_forward_event(alternate, event)
            if gap_is_causal:
                detail.append(gap_row)
            continue

        # Control state is coherent here, so alternate legality has causal meaning.
        if not alternate_legal:
            classification = "forced_invalid"
            counts[classification] += 1
            apply_forward_event(actual, event)
            applied_alt = False
        elif event_rosters & pre_roster_diff or pre_asset_diff:
            classification = "behavioral_review"
            counts[classification] += 1
            apply_forward_event(actual, event)
            apply_forward_event(alternate, event)
            applied_alt = True
        else:
            classification = "invariant"
            counts[classification] += 1
            apply_forward_event(actual, event)
            apply_forward_event(alternate, event)
            applied_alt = True

        if classification != "invariant":
            detail.append(
                {
                    "transaction_id": txid,
                    "created": created,
                    "type": str(event.get("type") or "unknown"),
                    "source": str(event.get("source") or "unknown"),
                    "classification": classification,
                    "mechanically_applied_to_alternate": applied_alt,
                    "roster_ids": sorted(event_rosters),
                    "assets": sorted(event_assets(event)),
                    "pre_event_divergent_rosters": sorted(pre_roster_diff),
                    "pre_event_asset_differences": sorted(pre_asset_diff),
                    "alternate_legality_reasons": alternate_reasons,
                    "post_event_divergent_rosters": sorted(divergent_rosters(actual, alternate)),
                }
            )

    final_divergent = sorted(divergent_rosters(actual, alternate))
    branch_classes = {"behavioral_review", "forced_invalid", "historical_gap_causal_uncertain"}
    branch_rows = [x for x in detail if x.get("classification") in branch_classes]

    gap_rate = len(archive_gaps) / max(1, sum(counts.values()))
    if counts["historical_gap_causal_uncertain"]:
        reconstruction_confidence = "LOW"
    elif gap_rate > 0.10:
        reconstruction_confidence = "MEDIUM"
    else:
        reconstruction_confidence = "HIGH"

    report = {
        "model_version": "Fantasy-Alternate-History-0.5a-state-diff-gap-aware",
        "scenario_id": scenario.scenario_id,
        "design_invariants": {
            "completed_historical_transactions_are_authoritative": True,
            "completed_nfl_history_is_immutable": True,
            "archive_gaps_do_not_create_counterfactual_branches_unless_causally_relevant": True,
            "no_gm_judgment_in_0_5a": True,
        },
        "summary": {
            "events_after_fork": sum(counts.values()),
            **counts,
            "archive_gap_events": len(archive_gaps),
            "branch_candidate_events": len(branch_rows),
            "final_divergent_rosters": final_divergent,
            "reconstruction_confidence": reconstruction_confidence,
            "archive_gap_rate": round(gap_rate, 4),
        },
        "branch_candidates": branch_rows,
        "historical_reconstruction_gaps": archive_gaps,
        "actual_final_state_hash": ah.stable_hash(actual.serializable()),
        "alternate_provisional_final_state_hash": ah.stable_hash(alternate.serializable()),
        "scope_note": (
            "0.5a v3 is mechanical pruning only. Unrelated archival gaps are synchronized to the completed historical event and excluded from the branch set. "
            "Only true state differences, counterfactual illegality, and causally relevant archive gaps proceed to 0.5b."
        ),
    }
    return ah.write_isolated_json(
        f"results/{scenario.scenario_id}/downstream_0_5a_v3.json", report
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run gap-aware Alternate History 0.5a")
    parser.add_argument("scenario", type=Path)
    args = parser.parse_args()
    out = run(args.scenario)
    report = load(out)
    print(out)
    print(json.dumps(report["summary"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
