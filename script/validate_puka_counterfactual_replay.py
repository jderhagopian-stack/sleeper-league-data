#!/usr/bin/env python3
"""Run and validate the first Alternate History counterfactual fixture."""

from __future__ import annotations

import json
from pathlib import Path

from run_fsffl_counterfactual_replay import run

SCENARIO = Path("data/alternate_history/scenarios/puka_vs_van_2023.json")
EXPECTED = Path("data/alternate_history/fixtures/puka_direct_replay_expected.json")


def main() -> None:
    out = run(SCENARIO)
    report = json.loads(out.read_text(encoding="utf-8"))
    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
    focus = report.get("focus_regular_season") or {}
    actual = focus.get("actual") or {}
    alternate = focus.get("alternate") or {}

    summary = {
        "scenario_id": report.get("scenario_id"),
        "season": report.get("season"),
        "focus_roster_id": report.get("focus_roster_id"),
        "actual": actual,
        "alternate": alternate,
        "win_delta": focus.get("win_delta"),
        "points_for_delta": focus.get("points_for_delta"),
        "changed_matchups": len(report.get("changed_matchups") or []),
        "weeks_with_lineup_changes": len(report.get("weekly_lineup_changes") or []),
    }

    invariant_checks = {
        "immutable_nfl_history": bool(
            (report.get("design_invariants") or {}).get("completed_nfl_history_is_immutable")
        ),
        "no_current_week_hindsight": bool(
            (report.get("design_invariants") or {}).get(
                "current_week_realized_points_not_used_for_lineup_decision"
            )
        ),
        "has_lineup_audit": isinstance(report.get("weekly_lineup_changes"), list),
        "has_changed_matchup_audit": isinstance(report.get("changed_matchups"), list),
    }
    if not all(invariant_checks.values()):
        raise SystemExit(f"Puka replay invariant failure: {invariant_checks}")

    expected_summary = {
        key: expected.get(key)
        for key in (
            "scenario_id",
            "season",
            "focus_roster_id",
            "actual",
            "alternate",
            "win_delta",
            "points_for_delta",
            "changed_matchups",
            "weeks_with_lineup_changes",
        )
    }
    if summary != expected_summary:
        raise SystemExit(
            "Puka direct replay changed unexpectedly. "
            + json.dumps({"expected": expected_summary, "actual": summary}, sort_keys=True)
        )

    print(
        json.dumps(
            {"status": "PASS", **summary, "checks": invariant_checks},
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
