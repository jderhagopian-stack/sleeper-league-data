#!/usr/bin/env python3
"""FSFFL Alternate History 0.7c v2: Max PF draft-order backvalidation.

Corrects the regular-season-record tiebreak audit to read the validated seeded
standings emitted by postseason 0.4 v3. Max PF remains the primary non-playoff
ordering key; worse regular-season record is used only for an exact Max PF tie.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

import alternate_history_engine as ah
from run_fsffl_downstream_dependencies import load
from run_fsffl_maxpf_draft_order import actual_weekly_maxpf, player_positions
from run_fsffl_postseason_consequences_v3 import run as run_postseason


def run(scenario_path: Path) -> Path:
    post = load(run_postseason(scenario_path))
    season = str(post.get("season"))
    maxpf = actual_weekly_maxpf(season, player_positions())

    actual = post.get("actual") or {}
    observed = {str(k): int(v) for k, v in (actual.get("following_draft_order_observed") or {}).items()}
    playoff = {str(k) for k in ((actual.get("playoffs") or {}).get("finish_by_roster") or {}).keys()}
    nonplay_ids = sorted((rid for rid in observed if rid not in playoff), key=lambda rid: observed[rid])

    standing_rows = actual.get("standings") or []
    standings = {str(row.get("roster_id")): row for row in standing_rows if row.get("roster_id") is not None}

    def record_value(rid: str) -> float:
        row = standings.get(str(rid)) or {}
        return float(row.get("wins") or 0) + 0.5 * float(row.get("ties") or 0)

    # Lower Max PF drafts earlier. On an exact Max PF tie, worse regular-season
    # record drafts earlier. Roster ID is deterministic last-resort only.
    reconstructed = sorted(
        nonplay_ids,
        key=lambda rid: (
            float(maxpf["totals"].get(rid, float("inf"))),
            record_value(rid),
            rid,
        ),
    )

    checks = []
    valid = len(nonplay_ids) == 6 and not maxpf["missing_regular_season_weeks"]
    for slot, rid in enumerate(reconstructed, 1):
        row = standings.get(rid) or {}
        observed_slot = observed.get(rid)
        ok = observed_slot == slot
        checks.append({
            "roster_id": rid,
            "max_pf": maxpf["totals"].get(rid),
            "regular_season_wins": int(row.get("wins") or 0),
            "regular_season_losses": int(row.get("losses") or 0),
            "regular_season_ties": int(row.get("ties") or 0),
            "record_value": record_value(rid),
            "reconstructed_slot": slot,
            "observed_slot": observed_slot,
            "match": ok,
        })
        valid = valid and ok

    report: Dict[str, Any] = {
        "model_version": "Fantasy-Alternate-History-0.7c-v2-maxpf-draft-order",
        "scenario_id": post.get("scenario_id"),
        "season": season,
        "following_draft_season": str(int(season) + 1),
        "nonplayoff_rule": {
            "primary": "Max PF ascending",
            "exact_maxpf_tiebreak": "worse regular-season record earlier",
            "final_deterministic_tiebreak": "roster_id",
            "user_confirmed": True,
            "historically_backvalidated": bool(valid),
        },
        "nonplayoff_backvalidation": {
            "validated": bool(valid),
            "checks": checks,
            "observed_nonplayoff_rosters_in_slot_order": nonplay_ids,
            "reconstructed_nonplayoff_rosters_in_slot_order": reconstructed,
            "missing_regular_season_weeks": maxpf["missing_regular_season_weeks"],
        },
        "actual_max_pf_by_roster": maxpf["totals"],
    }
    out = ah.write_isolated_json(
        f"results/{post.get('scenario_id')}/maxpf_draft_order_0_7c_v2.json",
        report,
    )
    print(out)
    print(json.dumps({"validated": valid, "checks": checks}, indent=2, sort_keys=True))
    if not valid:
        raise ah.AlternateHistoryError("0.7c v2 Max PF reconstruction did not reproduce observed nonplayoff draft order")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Backvalidate FSFFL Max PF draft order with record tiebreak")
    parser.add_argument("scenario", type=Path)
    args = parser.parse_args()
    run(args.scenario)


if __name__ == "__main__":
    main()
