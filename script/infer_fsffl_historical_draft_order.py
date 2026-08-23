#!/usr/bin/env python3
"""Infer and backvalidate historical FSFFL rookie draft-order components.

Playoff teams:
- final playoff finish reversed into rookie slots 7-12
- champion=12, runner-up=11, ... sixth=7

Non-playoff teams:
- rookie slots 1-6 are Max Points For ascending
- fewest Max PF = slot 1; most Max PF among non-playoff teams = slot 6

Both components must be historically backvalidated before they are used as
validated league rules in counterfactual season propagation.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

import alternate_history_engine as ah
from run_fsffl_downstream_dependencies import load
from run_fsffl_postseason_consequences_v3 import run as run_postseason
from run_fsffl_maxpf_draft_order import run as run_maxpf


def run(scenario_path: Path) -> Path:
    post = load(run_postseason(scenario_path))
    maxpf = load(run_maxpf(scenario_path))
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

    nonplay = maxpf.get("nonplayoff_backvalidation") or {}
    nonplay_valid = bool(nonplay.get("validated"))
    nonplay_checks = nonplay.get("checks") or []

    focus = str(post.get("focus_roster_id"))
    focus_alt_finish = alternate_finish.get(focus)
    focus_alt_slot = None
    focus_alt_slot_basis = None
    if playoff_valid and focus_alt_finish is not None:
        focus_alt_slot = 13 - int(focus_alt_finish)
        focus_alt_slot_basis = "validated_playoff_finish_rule"
    # A non-playoff alternate slot cannot be emitted from actual Max PF alone.
    # It requires branch-specific alternate weekly Max PF, supplied by 0.7d+.

    report = {
        "model_version": "Fantasy-Alternate-History-draft-order-inference-0.4.3",
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
                "rule": "sort non-playoff teams by Max Points For ascending; lowest Max PF = rookie slot 1, highest = rookie slot 6",
                "validated": nonplay_valid,
                "checks": nonplay_checks,
                "actual_max_pf_by_roster": maxpf.get("actual_max_pf_by_roster") or {},
                "observed_slots": {
                    str(row.get("roster_id")): row.get("observed_slot")
                    for row in nonplay_checks
                },
                "note": "Historical rule is now backvalidated. Counterfactual non-playoff slots require alternate weekly roster states and recomputed Max PF; historical Max PF is never reused for a divergent branch.",
            },
        },
        "league_rule_status": {
            "playoff_draft_order": "VALIDATED",
            "nonplayoff_maxpf_draft_order": "VALIDATED" if nonplay_valid else "KNOWN_NOT_VALIDATED",
            "full_rule_validated": bool(playoff_valid and nonplay_valid),
        },
        "focus": {
            "roster_id": focus,
            "actual_finish": post.get("actual", {}).get("focus_finish"),
            "actual_draft_slot": observed.get(focus),
            "alternate_finish": focus_alt_finish,
            "alternate_draft_slot": focus_alt_slot,
            "alternate_draft_slot_basis": focus_alt_slot_basis,
            "exact_alternate_slot_supported": focus_alt_slot is not None,
        },
    }
    return ah.write_isolated_json(
        f"results/{post.get('scenario_id')}/draft_order_inference_0_4_3.json", report
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
        "nonplayoff_rule_validated": report["component_validation"]["nonplayoff_component"]["validated"],
        "full_rule_validated": report["league_rule_status"]["full_rule_validated"],
        "focus": report["focus"],
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
