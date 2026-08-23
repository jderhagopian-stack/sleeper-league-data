#!/usr/bin/env python3
"""Run and sanity-check the first Alternate History counterfactual fixture."""

from __future__ import annotations

import json
from pathlib import Path

from run_fsffl_counterfactual_replay import run

SCENARIO = Path("data/alternate_history/scenarios/puka_vs_van_2023.json")


def main() -> None:
    out = run(SCENARIO)
    report = json.loads(out.read_text(encoding="utf-8"))
    focus = report.get("focus_regular_season") or {}
    actual = focus.get("actual") or {}
    alternate = focus.get("alternate") or {}

    checks = {
        "immutable_nfl_history": bool(
            (report.get("design_invariants") or {}).get("completed_nfl_history_is_immutable")
        ),
        "no_current_week_hindsight": bool(
            (report.get("design_invariants") or {}).get(
                "current_week_realized_points_not_used_for_lineup_decision"
            )
        ),
        "has_actual_record": "wins" in actual and "points_for" in actual,
        "has_alternate_record": "wins" in alternate and "points_for" in alternate,
        "has_lineup_audit": isinstance(report.get("weekly_lineup_changes"), list),
        "has_changed_matchup_audit": isinstance(report.get("changed_matchups"), list),
    }
    if not all(checks.values()):
        raise SystemExit(f"Puka replay fixture failed: {checks}")

    summary = {
        "status": "PASS",
        "scenario_id": report.get("scenario_id"),
        "season": report.get("season"),
        "focus_roster_id": report.get("focus_roster_id"),
        "actual": actual,
        "alternate": alternate,
        "win_delta": focus.get("win_delta"),
        "points_for_delta": focus.get("points_for_delta"),
        "changed_matchups": len(report.get("changed_matchups") or []),
        "weeks_with_lineup_changes": len(report.get("weekly_lineup_changes") or []),
        "checks": checks,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
