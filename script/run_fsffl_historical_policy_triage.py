#!/usr/bin/env python3
"""FSFFL Alternate History 0.5b: historical decision-policy triage.

This stage enforces a historical information firewall. GM 3.0's current numeric
asset values/projections may NOT be backcast into historical decisions. The
model first separates branch candidates into:
- REQUIRED_BRANCH: direct asset / mechanical conflict;
- HISTORICAL_USAGE_POLICY: same-position waiver/add-drop decisions that can be
  evaluated from information available before the event;
- HISTORICAL_GM_REQUIRED: strategic trades sensitive to the divergent position
  or future-pick economics and therefore requiring point-in-time valuation;
- STRUCTURALLY_STABLE: legal historical trades with no divergent-position or
  draft-pick exposure, provisionally retained without expensive modeling.

The current/future boundary may later call normal GM 3.0 and Simulator 1.0.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Set

import alternate_history_engine as ah
from run_fsffl_alternate_history import FSFFLHistoricalAdapter
from run_fsffl_downstream_dependencies import load
from run_fsffl_downstream_dependencies_v4 import run as run_pruning

DATA = Path("data")


def positions() -> Dict[str, str]:
    raw = load(DATA / "players.json")
    return {str(pid): str(row.get("position") or "") for pid, row in raw.items()}


def event_positions(event: Dict[str, Any], pos: Dict[str, str]) -> Set[str]:
    ids = {str(x) for x in (event.get("adds") or {}).keys()}
    ids |= {str(x) for x in (event.get("drops") or {}).keys()}
    return {pos.get(pid, "") for pid in ids if pos.get(pid, "")}


def run(scenario_path: Path) -> Path:
    payload = load(scenario_path)
    adapter = FSFFLHistoricalAdapter()
    scenario = ah.scenario_from_json(adapter, payload)
    prune_path = run_pruning(scenario_path)
    pruned = load(prune_path)
    event_by_id = {
        str(e.get("transaction_id")): e for e in adapter.completed_events()
    }
    pos = positions()

    divergent_positions: Set[str] = set()
    for action in scenario.actions:
        for pid in (action.add_player_id, action.drop_player_id):
            if pid and pos.get(str(pid)):
                divergent_positions.add(pos[str(pid)])

    decisions: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {}
    gm_required_trade_ids: List[str] = []
    usage_policy_ids: List[str] = []
    stable_trade_ids: List[str] = []
    required_branch_ids: List[str] = []

    for row in pruned.get("branch_candidates") or []:
        txid = str(row.get("transaction_id"))
        event = event_by_id.get(txid) or {}
        rel = row.get("relevance") or {}
        band = str(rel.get("band") or "")
        classification = str(row.get("classification") or "")
        etype = str(event.get("type") or row.get("type") or "unknown")
        epos = event_positions(event, pos)
        has_picks = bool(event.get("draft_picks"))

        if classification == "forced_invalid" or band == "DIRECT_ASSET":
            policy = "REQUIRED_BRANCH"
            reason = "Historical action directly conflicts with alternate asset ownership."
            required_branch_ids.append(txid)
        elif band == "SAME_POSITION_ROSTER_DECISION":
            policy = "HISTORICAL_USAGE_POLICY"
            reason = (
                "Add/drop decision competes with the divergent position and can be evaluated from pre-event historical usage/performance only."
            )
            usage_policy_ids.append(txid)
        elif band == "STRATEGIC_TRADE":
            sensitive_position = bool(epos & divergent_positions)
            if sensitive_position or has_picks:
                policy = "HISTORICAL_GM_REQUIRED"
                reason = (
                    "Trade touches the divergent position or future-pick economics; point-in-time valuation/context is required."
                )
                gm_required_trade_ids.append(txid)
            else:
                policy = "STRUCTURALLY_STABLE"
                reason = (
                    "Trade remains mechanically legal and contains neither the divergent position nor draft picks; retain as the reference path unless later causal evidence contradicts it."
                )
                stable_trade_ids.append(txid)
        else:
            policy = "REQUIRED_BRANCH"
            reason = "Nonstandard causal candidate requires explicit branch handling."
            required_branch_ids.append(txid)

        counts[policy] = counts.get(policy, 0) + 1
        decisions.append(
            {
                "transaction_id": txid,
                "created": int(event.get("created") or row.get("created") or 0),
                "event_type": etype,
                "source": str(event.get("source") or "unknown"),
                "0_5a_classification": classification,
                "relevance_band": band,
                "event_positions": sorted(epos),
                "contains_draft_picks": has_picks,
                "policy": policy,
                "reason": reason,
            }
        )

    report = {
        "model_version": "Fantasy-Alternate-History-0.5b-policy-triage",
        "scenario_id": scenario.scenario_id,
        "historical_information_firewall": {
            "enabled": True,
            "prohibited_for_pre_current_decisions": [
                "current GM 3.0 numeric player values",
                "current redraft projections",
                "future-known player outcomes",
                "current market ranks",
            ],
            "permitted": [
                "information timestamped before the decision",
                "trailing fantasy usage/performance from already-completed weeks",
                "historical standings known at the time",
                "historical transaction terms",
                "point-in-time market/value snapshots when available",
                "GM 3.0 structural logic supplied with historical-safe inputs",
            ],
        },
        "divergent_positions": sorted(divergent_positions),
        "summary": {
            "input_branch_candidates": len(pruned.get("branch_candidates") or []),
            "policy_counts": dict(sorted(counts.items())),
            "required_branch_events": len(required_branch_ids),
            "historical_usage_policy_events": len(usage_policy_ids),
            "historical_gm_required_trades": len(gm_required_trade_ids),
            "structurally_stable_trades": len(stable_trade_ids),
        },
        "decision_triage": decisions,
        "queues": {
            "required_branch_transaction_ids": required_branch_ids,
            "historical_usage_policy_transaction_ids": usage_policy_ids,
            "historical_gm_required_transaction_ids": gm_required_trade_ids,
            "structurally_stable_transaction_ids": stable_trade_ids,
        },
        "next_stage_contract": {
            "usage_policy": "evaluate without current-week/future information",
            "historical_gm": "do not score until point-in-time valuation/context inputs exist",
            "current_future": "normal GM 3.0 and Simulator 1.0 become authoritative only at current/future boundary",
        },
    }
    return ah.write_isolated_json(
        f"results/{scenario.scenario_id}/policy_triage_0_5b.json", report
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Alternate History historical policy triage")
    parser.add_argument("scenario", type=Path)
    args = parser.parse_args()
    out = run(args.scenario)
    report = load(out)
    print(out)
    print(json.dumps(report["summary"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
