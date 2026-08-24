#!/usr/bin/env python3
"""FSFFL Alternate History 0.5c v4: corrected historical season/week resolution.

Raw Sleeper historical events are normalized by FSFFLHistoricalAdapter with
`source_season` at the event top level. Earlier usage-policy evaluation checked
metadata/source season but omitted the normalized top-level field, causing valid
historical weekly scoring evidence to be treated as missing.

v4 fixes that adapter-contract mismatch while preserving the historical
information firewall and all v3 queue-contract checks.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import alternate_history_engine as ah
import run_fsffl_historical_usage_policy_v2 as usage_v2
import run_fsffl_historical_usage_policy_v3 as usage_v3
from run_fsffl_downstream_dependencies import load


def corrected_event_season_week(event: Dict[str, Any]) -> Tuple[Optional[str], Optional[int]]:
    meta = event.get("metadata") or {}
    season = (
        event.get("source_season")
        or event.get("season")
        or meta.get("source_season")
        or meta.get("season")
    )
    week = event.get("leg") or event.get("week") or meta.get("leg") or meta.get("week")
    try:
        parsed_week = int(week) if week is not None else None
    except (TypeError, ValueError):
        parsed_week = None
    return (str(season) if season is not None else None, parsed_week)


def run(scenario_path: Path) -> Path:
    # evaluate_event imported by v3 retains the globals of the v2 module, so
    # replacing the resolver here repairs the adapter contract without copying
    # the policy implementation.
    usage_v2.event_season_week = corrected_event_season_week
    out = usage_v3.run(scenario_path)
    report = load(out)
    report["model_version"] = "Fantasy-Alternate-History-0.5c-v4-historical-usage"
    report.setdefault("design_invariants", {})["normalized_top_level_source_season_used"] = True
    report["metadata_contract_fix"] = {
        "issue": "raw historical source_season is normalized at event top level",
        "prior_behavior": "season could resolve null, suppressing available trailing weekly evidence",
        "corrected_behavior": "top-level source_season/leg are preferred before metadata fallbacks",
    }
    fixed = ah.write_isolated_json(
        f"results/{report.get('scenario_id')}/historical_usage_policy_0_5c.json", report
    )
    print(json.dumps({
        "model_version": report["model_version"],
        "queued_usage_events": report.get("queued_usage_events"),
        "confidence_counts": report.get("confidence_counts"),
        "expected_decision_counts": report.get("expected_decision_counts"),
        "historical_points_sources": report.get("historical_points_sources"),
    }, indent=2, sort_keys=True))
    return fixed


def main() -> None:
    parser = argparse.ArgumentParser(description="Run corrected Alternate History 0.5c historical usage policy")
    parser.add_argument("scenario", type=Path)
    args = parser.parse_args()
    run(args.scenario)


if __name__ == "__main__":
    main()
