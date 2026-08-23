#!/usr/bin/env python3
"""FSFFL Alternate History 0.5d v2: queue-contract validated trade policy."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

import alternate_history_engine as ah
from run_fsffl_alternate_history import FSFFLHistoricalAdapter
from run_fsffl_downstream_dependencies import load
from run_fsffl_historical_policy_triage import run as run_triage
from run_fsffl_historical_trade_policy import evaluate, player_positions


def run(scenario_path: Path) -> Path:
    adapter = FSFFLHistoricalAdapter()
    scenario = ah.scenario_from_json(adapter, load(scenario_path))
    triage = load(run_triage(scenario_path))
    expected_ids = [str(x) for x in ((triage.get("queues") or {}).get("historical_gm_required_transaction_ids") or [])]
    expected_count = int(((triage.get("summary") or {}).get("historical_gm_required_trades") or 0))
    if len(expected_ids) != expected_count:
        raise ah.AlternateHistoryError(
            f"0.5d triage queue mismatch: ids={len(expected_ids)} summary={expected_count}"
        )
    if expected_count <= 0:
        raise ah.AlternateHistoryError("0.5d expected a non-empty historical trade queue")

    event_by_id = {str(e.get("transaction_id")): e for e in adapter.completed_events()}
    positions = player_positions()
    decisions = []
    missing = []
    for tid in expected_ids:
        event = event_by_id.get(tid)
        if event is None:
            missing.append(tid)
            continue
        decisions.append(evaluate(adapter, scenario, event, positions))
    if missing:
        raise ah.AlternateHistoryError(f"0.5d missing queued transactions: {missing[:10]}")
    if len(decisions) != expected_count:
        raise ah.AlternateHistoryError(
            f"0.5d evaluated {len(decisions)} trades, expected {expected_count}"
        )

    expected = {
        key: round(sum(float(d["probabilities"].get(key) or 0.0) for d in decisions), 3)
        for key in ("preserve_historical_trade", "modified_trade_branch", "no_trade")
    }
    class_counts: Dict[str, int] = {}
    conf_counts: Dict[str, int] = {}
    for d in decisions:
        class_counts[d["classification"]] = class_counts.get(d["classification"], 0) + 1
        conf_counts[d["confidence"]] = conf_counts.get(d["confidence"], 0) + 1

    report: Dict[str, Any] = {
        "model_version": "Fantasy-Alternate-History-0.5d-v2-historical-trade-policy",
        "scenario_id": scenario.scenario_id,
        "design_invariants": {
            "current_gm3_numeric_values_used": False,
            "current_market_values_used": False,
            "future_nfl_outcomes_used": False,
            "historical_accepted_trade_is_revealed_action_prior": True,
            "replacement_trade_packages_generated_here": False,
            "triage_queue_contract_enforced": True,
            "local_reference_state_only": True,
        },
        "policy_note": (
            "Structural probability layer only. Modified trade branches are expanded later using timestamp-safe evidence."
        ),
        "queued_trade_events": expected_count,
        "evaluated_trade_events": len(decisions),
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
        "queued_trade_events": expected_count,
        "evaluated_trade_events": len(decisions),
        "expected_branch_counts": expected,
        "classification_counts": class_counts,
        "confidence_counts": conf_counts,
    }, indent=2, sort_keys=True))
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", type=Path)
    args = parser.parse_args()
    run(args.scenario)


if __name__ == "__main__":
    main()
