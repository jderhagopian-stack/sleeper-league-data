#!/usr/bin/env python3
"""User-facing 0.6b draft-policy report with per-controlled-pick probabilities.

The underlying coupled simulation is authoritative. This report avoids
aggregating multiple picks controlled by the same manager in one round, which
can make marginal probabilities sum above 1 when an owner controls multiple
traded picks.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

import alternate_history_engine as ah
from run_fsffl_alternate_draft_candidates import user_to_roster_for_season
from run_fsffl_alternate_draft_policy import run as run_policy
from run_fsffl_downstream_dependencies import load


def run(scenario_path: Path, n_sims: int = 5000) -> Path:
    policy_path = run_policy(scenario_path, n_sims=n_sims)
    policy = load(policy_path)
    draft_season = str(policy.get("draft_season"))
    focus_rid = str(policy.get("focus_roster_id"))
    user_to_roster = user_to_roster_for_season(draft_season)

    controlled: List[Dict[str, Any]] = []
    own_slot: List[Dict[str, Any]] = []
    for row in policy.get("slot_selection_distributions") or []:
        uid = str(row.get("controller_user_id") or "")
        controller_rid = user_to_roster.get(uid)
        if controller_rid != focus_rid:
            continue
        probs = [float(x.get("probability") or 0.0) for x in row.get("top_candidates") or []]
        captured = round(sum(probs), 4)
        controlled.append({
            "round": int(row.get("round") or 0),
            "alternate_slot": int(row.get("alternate_slot") or 0),
            "source_actual_slot": int(row.get("source_actual_slot") or 0),
            "controller_user_id": uid,
            "top_candidates": row.get("top_candidates") or [],
            "top_candidate_probability_mass": captured,
        })
        if int(row.get("source_actual_slot") or 0) == 9:
            own_slot.append(controlled[-1])

    report = {
        "model_version": "Fantasy-Alternate-History-0.6b-focus-report",
        "scenario_id": policy.get("scenario_id"),
        "draft_season": draft_season,
        "n_sims": int(policy.get("n_sims") or n_sims),
        "focus_roster_id": focus_rid,
        "design_note": (
            "Each row is one distinct pick-control opportunity. Probabilities are not combined across multiple traded picks in the same round."
        ),
        "focus_controlled_pick_distributions": controlled,
        "focus_original_slot_distributions": own_slot,
        "confidence": policy.get("confidence"),
    }
    out = ah.write_isolated_json(
        f"results/{policy.get('scenario_id')}/draft_policy_0_6b_focus_report.json", report
    )
    print(out)
    print(json.dumps({
        "focus_original_slot_distributions": own_slot,
        "controlled_pick_count": len(controlled),
    }, indent=2, sort_keys=True))
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--sims", type=int, default=5000)
    args = parser.parse_args()
    run(args.scenario, n_sims=max(250, int(args.sims)))


if __name__ == "__main__":
    main()
