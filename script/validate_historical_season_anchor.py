#!/usr/bin/env python3
"""Regression validation for completed-season historical state anchoring."""

from __future__ import annotations

import json
from pathlib import Path

import alternate_history_engine as ah
from alternate_history_historical_state import reconstruct_completed_season_state
from backfill_alternate_history_sleeper import run as backfill
from run_fsffl_alternate_history import FSFFLHistoricalAdapter
from run_fsffl_downstream_dependencies import load

SCENARIO = Path("data/alternate_history/scenarios/puka_vs_van_2023.json")


def owner_of(state: ah.LeagueState, pid: str):
    for rid, players in state.roster_players.items():
        if str(pid) in {str(x) for x in players}:
            return str(rid)
    return None


def future_draftees(adapter: FSFFLHistoricalAdapter, seasons: set[str]) -> set[str]:
    out: set[str] = set()
    for row in adapter.raw_history_seasons():
        season = str((row.get("league") or {}).get("season") or "")
        if season not in seasons:
            continue
        for entry in row.get("drafts") or []:
            for pick in entry.get("picks") or []:
                pid = pick.get("player_id") or (pick.get("metadata") or {}).get("player_id")
                if pid is not None:
                    out.add(str(pid))
    return out


def main() -> None:
    backfill()
    adapter = FSFFLHistoricalAdapter()
    payload = load(SCENARIO)
    scenario = ah.scenario_from_json(adapter, payload)
    state = reconstruct_completed_season_state(
        adapter,
        str(payload.get("fork_season") or "2023"),
        scenario.fork_timestamp_ms,
    )

    puka = adapter.player_id("Puka Nacua")
    van = adapter.player_id("Van Jefferson")
    future = future_draftees(adapter, {"2024", "2025"})
    rostered = {str(pid) for players in state.roster_players.values() for pid in players}
    leaked = sorted(future & rostered)

    checks = {
        "puka_unowned_before_historical_waiver": owner_of(state, puka) is None,
        "van_on_focus_roster": owner_of(state, van) == str(scenario.focus_roster_id),
        "no_2024_or_2025_draftees_in_2023_state": not leaked,
        "archived_season_anchor_used": state.reconstruction.get("source") == "archived_completed_season_snapshot_reverse_replay",
    }
    report = {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "scenario_id": scenario.scenario_id,
        "focus_roster_id": scenario.focus_roster_id,
        "puka_owner": owner_of(state, puka),
        "van_owner": owner_of(state, van),
        "future_draftee_leaks": leaked,
        "reconstruction": state.reconstruction,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    if report["status"] != "PASS":
        raise ah.AlternateHistoryError(f"Historical season anchor validation failed: {report}")


if __name__ == "__main__":
    main()
