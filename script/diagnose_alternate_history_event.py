#!/usr/bin/env python3
"""Diagnose historical event/ownership inconsistencies for Alternate History.

Read-only diagnostic. It reconstructs the actual state at the Puka fork, then
walks the merged historical event stream forward while auditing one player and
one target transaction. The recorded historical event remains authoritative;
this script only explains why a strict state reconstruction disagrees.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import alternate_history_engine as ah
from run_fsffl_alternate_history import FSFFLHistoricalAdapter
from run_fsffl_downstream_dependencies import apply_forward_event, event_legality, load, player_owner


def event_mentions_player(event: Dict[str, Any], pid: str) -> bool:
    return pid in {str(x) for x in (event.get("adds") or {}).keys()} or pid in {
        str(x) for x in (event.get("drops") or {}).keys()
    }


def summarize_event(event: Dict[str, Any], pid: str, owner_before: Optional[str], owner_after: Optional[str]) -> Dict[str, Any]:
    return {
        "transaction_id": str(event.get("transaction_id")),
        "created": int(event.get("created") or 0),
        "type": str(event.get("type") or "unknown"),
        "source": str(event.get("source") or "unknown"),
        "roster_ids": [str(x) for x in (event.get("roster_ids") or [])],
        "player_id": pid,
        "adds": {str(k): str(v) for k, v in (event.get("adds") or {}).items() if str(k) == pid},
        "drops": {str(k): str(v) for k, v in (event.get("drops") or {}).items() if str(k) == pid},
        "owner_before": owner_before,
        "owner_after": owner_after,
    }


def run(scenario_path: Path, player_id: str, target_transaction: str) -> Path:
    payload = load(scenario_path)
    adapter = FSFFLHistoricalAdapter()
    scenario = ah.scenario_from_json(adapter, payload)
    state = ah.reconstruct_state(adapter, scenario.fork_timestamp_ms)

    events = [
        e for e in adapter.completed_events()
        if int(e.get("created") or 0) >= scenario.fork_timestamp_ms
    ]
    audit: List[Dict[str, Any]] = []
    target_detail: Optional[Dict[str, Any]] = None
    actual_legality_gaps: List[Dict[str, Any]] = []

    for event in events:
        before = player_owner(state, player_id)
        legal, reasons = event_legality(state, event)
        if not legal:
            actual_legality_gaps.append(
                {
                    "transaction_id": str(event.get("transaction_id")),
                    "created": int(event.get("created") or 0),
                    "type": str(event.get("type") or "unknown"),
                    "source": str(event.get("source") or "unknown"),
                    "reasons": reasons,
                }
            )
        apply_forward_event(state, event)
        after = player_owner(state, player_id)
        if event_mentions_player(event, player_id):
            audit.append(summarize_event(event, player_id, before, after))
        if str(event.get("transaction_id")) == str(target_transaction):
            target_detail = {
                "event": summarize_event(event, player_id, before, after),
                "strict_legality": legal,
                "strict_legality_reasons": reasons,
                "prior_player_events": audit[-8:],
            }

    source_counts: Dict[str, int] = {}
    for event in events:
        source = str(event.get("source") or "unknown")
        source_counts[source] = source_counts.get(source, 0) + 1

    report = {
        "model_version": "Fantasy-Alternate-History-diagnostic-0.5a",
        "scenario_id": scenario.scenario_id,
        "player_id": str(player_id),
        "target_transaction": str(target_transaction),
        "events_after_fork": len(events),
        "event_source_counts": source_counts,
        "player_event_audit": audit,
        "target_detail": target_detail,
        "actual_legality_gap_count": len(actual_legality_gaps),
        "first_actual_legality_gaps": actual_legality_gaps[:25],
        "interpretation_rule": (
            "If the target event is historically recorded complete but strict pre-event ownership conflicts, "
            "the merged archive is incomplete or insufficient for exact event-by-event ownership replay at that point."
        ),
    }
    return ah.write_isolated_json("validation/event_1008731352486727680_diagnostic.json", report)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--player-id", default="1339")
    parser.add_argument("--transaction-id", default="1008731352486727680")
    args = parser.parse_args()
    out = run(args.scenario, args.player_id, args.transaction_id)
    report = load(out)
    print(out)
    print(json.dumps({
        "target_detail": report["target_detail"],
        "player_event_audit": report["player_event_audit"],
        "actual_legality_gap_count": report["actual_legality_gap_count"],
        "first_actual_legality_gaps": report["first_actual_legality_gaps"][:5],
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
