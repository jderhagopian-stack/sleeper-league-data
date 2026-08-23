#!/usr/bin/env python3
"""Infer historical FSFFL rookie draft-order components.

The league may use different ordering rules for playoff and non-playoff teams.
Do not reject a valid playoff mapping merely because the consolation/non-playoff
component differs. This script backvalidates each component separately from raw
Sleeper draft order.

For playoff teams, candidate rule is final playoff finish reversed into rookie
slots 7-12: champion=12, runner-up=11, ... sixth=7. It is used for an alternate
playoff team's exact slot only if all six actual playoff teams match.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

import alternate_history_engine as ah
from run_fsffl_downstream_dependencies import load
from run_fsffl_postseason_consequences_v3 import run as run_postseason


def run(scenario_path: Path) -> Path:
    post = load(run_postseason(scenario_path))
    observed = post.get("actual", {}).get("following_draft_order_observed") or {}
    actual_finish = post.get("actual", {}).get("playoffs", {}).get("finish_by_roster") or {}
    alternate_finish = post.get("alternate", {}).get("playoffs", {}).get("finish_by_roster") or {}

    playoff_checks: Dict[str, Dict[str, Any]] = {}
    playoff_valid = len(actual_finish) == 6 and bool(observed)
    for rid, finish in sorted(actual_finish.items(), key=lambda kv: int(kv[1])):
        expected_slot = 13 - int(finish)
        observed_slot = observed.get(str(rid))
        ok = observed_slot == expected_slot
        playoff_checks[str(rid)] = {
            "finish": int(finish),
            "expected_draft_slot": expected_slot,
            "observed_draft_slot": observed_slot,
            "match": ok,
        }
        playoff_valid = playoff_valid and ok

    playoff_ids = set(actual_finish)
    nonplay_observed = {rid: slot for rid, slot in observed.items() if rid not in playoff_ids}
    nonplay_resolved = len(nonplay_observed) == 6

    focus = str(post.get("focus_roster_id"))
    focus_alt_finish = alternate_finish.get(focus)
    focus_alt_slot = None
    if playoff_valid and focus_alt_finish is not None:
        focus_alt_slot = 13 - int(focus_alt_finish)

    report = {
        "model_version": "Fantasy-Alternate-History-draft-order-inference-0.4.1",
        "scenario_id": post.get("scenario_id"),
        "season": post.get("season"),
        "following_draft_season": str(int(post.get("season")) + 1),
        "component_validation": {
            "playoff_component": {
                "rule": "rookie_slot = 13 - final_playoff_finish",
                "validated": playoff_valid,
                "checks": playoff_checks,
            },
            "nonplayoff_component": {
                "raw_slots_available": nonplay_resolved,
                "observed_slots": dict(sorted(nonplay_observed.items(), key=lambda kv: int(kv[1]))),
                "rule": None,
                "validated": False,
                "note": "Non-playoff ordering is intentionally left unresolved until its actual consolation/standings rule is separately inferred.",
            },
        },
        "focus": {
            "roster_id": focus,
            "actual_finish": post.get("actual", {}).get("focus_finish"),
            "actual_draft_slot": observed.get(focus),
            "alternate_finish": focus_alt_finish,
            "alternate_draft_slot": focus_alt_slot,
            "exact_alternate_slot_supported": focus_alt_slot is not None,
        },
    }
    return ah.write_isolated_json(
        f"results/{post.get('scenario_id')}/draft_order_inference_0_4_1.json", report
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", type=Path)
    args = parser.parse_args()
    out = run(args.scenario)
    report = load(out)
    print(out)
    print(json.dumps({
        "playoff_rule_validated": report["component_validation"]["playoff_component"]["validated"],
        "focus": report["focus"],
        "playoff_checks": report["component_validation"]["playoff_component"]["checks"],
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
