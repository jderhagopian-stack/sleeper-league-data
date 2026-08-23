#!/usr/bin/env python3
"""FSFFL Alternate History 0.5c v3: queue-contract validated usage policy."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict

import alternate_history_engine as ah
from run_fsffl_alternate_history import FSFFLHistoricalAdapter
from run_fsffl_downstream_dependencies import load
from run_fsffl_historical_policy_triage import run as run_triage
from run_fsffl_historical_usage_policy import HistoricalPoints, positions_index
from run_fsffl_historical_usage_policy_v2 import evaluate_event


def run(scenario_path: Path) -> Path:
    adapter = FSFFLHistoricalAdapter()
    scenario = ah.scenario_from_json(adapter, load(scenario_path))
    triage = load(run_triage(scenario_path))
    expected_ids = [str(x) for x in ((triage.get("queues") or {}).get("historical_usage_policy_transaction_ids") or [])]
    expected_count = int(((triage.get("summary") or {}).get("historical_usage_policy_events") or 0))
    if len(expected_ids) != expected_count:
        raise ah.AlternateHistoryError(
            f"0.5c triage queue mismatch: ids={len(expected_ids)} summary={expected_count}"
        )
    if expected_count <= 0:
        raise ah.AlternateHistoryError("0.5c expected a non-empty historical usage queue")

    event_by_id = {str(e.get("transaction_id")): e for e in adapter.completed_events()}
    positions = positions_index()
    points = HistoricalPoints()
    results = []
    missing = []
    for tid in expected_ids:
        event = event_by_id.get(tid)
        if event is None:
            missing.append(tid)
            continue
        results.append(evaluate_event(adapter, scenario, event, positions, points))
    if missing:
        raise ah.AlternateHistoryError(f"0.5c missing queued transactions: {missing[:10]}")
    if len(results) != expected_count:
        raise ah.AlternateHistoryError(
            f"0.5c evaluated {len(results)} transactions, expected {expected_count}"
        )

    flattened = [d for row in results for d in row.get("decisions") or []]
    if not flattened:
        raise ah.AlternateHistoryError("0.5c evaluated transactions but produced zero roster decisions")

    expected = {
        key: round(sum(float(d["probabilities"].get(key) or 0.0) for d in flattened), 3)
        for key in ("preserve_exact", "preserve_add_change_drop", "no_action")
    }
    conf: Dict[str, int] = defaultdict(int)
    for d in flattened:
        conf[str(d.get("confidence"))] += 1

    report: Dict[str, Any] = {
        "model_version": "Fantasy-Alternate-History-0.5c-v3-historical-usage",
        "scenario_id": scenario.scenario_id,
        "design_invariants": {
            "future_nfl_outcomes_used": False,
            "current_week_realized_points_used": False,
            "current_gm3_numeric_values_used": False,
            "completed_prior_week_scoring_only": True,
            "historical_completed_transaction_is_revealed_action_prior": True,
            "triage_queue_contract_enforced": True,
            "local_reference_state_only": True,
        },
        "queued_usage_events": expected_count,
        "evaluated_transactions": len(results),
        "evaluated_roster_decisions": len(flattened),
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
        "queued_usage_events": expected_count,
        "evaluated_transactions": len(results),
        "evaluated_roster_decisions": len(flattened),
        "expected_decision_counts": expected,
        "confidence_counts": dict(conf),
    }, indent=2, sort_keys=True))
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", type=Path)
    args = parser.parse_args()
    run(args.scenario)


if __name__ == "__main__":
    main()
