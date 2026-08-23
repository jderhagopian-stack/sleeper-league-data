#!/usr/bin/env python3
"""Regression validation for Fantasy Alternate History Engine.

The validator is intentionally destructive only inside data/alternate_history/.
It verifies that a scenario run cannot mutate canonical Simulator/GM/Sleeper
artifacts and that the historical adapter reaches pre-2026 events.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import alternate_history_engine as ah
from run_fsffl_alternate_history import FSFFLHistoricalAdapter, run

DATA = Path("data")
PROTECTED = [
    DATA / "league.json",
    DATA / "rosters.json",
    DATA / "transactions.json",
    DATA / "trade_ledger.json",
    DATA / "acquisition_ledger.json",
    DATA / "fsffl_asset_values.json",
    DATA / "gm_command_center.json",
    DATA / "simulator" / "2026" / "outputs" / "standings_projection.json",
]
SCENARIO = DATA / "alternate_history" / "scenarios" / "puka_vs_van_2023.json"


def digest(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    before = {str(p): digest(p) for p in PROTECTED}

    adapter = FSFFLHistoricalAdapter()
    events = adapter.completed_events()
    assert events, "No completed events loaded"
    assert min(int(x.get("created") or 0) for x in events) < 1700000000000, (
        "Historical adapter did not reach pre-2024 data"
    )

    payload = json.loads(SCENARIO.read_text(encoding="utf-8"))
    scenario = ah.scenario_from_json(adapter, payload)
    assert scenario.focus_roster_id, "Focus roster did not resolve"
    assert scenario.actions[0].add_player_id == adapter.player_id("Puka Nacua")
    assert scenario.actions[0].drop_player_id == adapter.player_id("Van Jefferson")

    reconstructed = ah.reconstruct_state(adapter, scenario.fork_timestamp_ms)
    alternate = ah.apply_fork(reconstructed, scenario)
    rid = scenario.focus_roster_id
    assert scenario.actions[0].add_player_id in alternate.roster_players[rid]
    assert scenario.actions[0].drop_player_id not in alternate.roster_players[rid]

    out = run(SCENARIO)
    assert out.exists(), "Scenario manifest was not written"
    assert ah.AH_ROOT.resolve() in out.resolve().parents, "Output escaped isolated namespace"

    after = {str(p): digest(p) for p in PROTECTED}
    changed = [p for p in before if before[p] != after[p]]
    assert not changed, f"Canonical artifacts changed: {changed}"

    manifest = json.loads(out.read_text(encoding="utf-8"))
    invariants = manifest.get("design_invariants") or {}
    assert invariants.get("completed_nfl_history_is_immutable") is True
    assert invariants.get("canonical_data_is_read_only") is True
    assert invariants.get("league_agnostic_core") is True

    print(json.dumps({
        "status": "PASS",
        "model_version": ah.MODEL_VERSION,
        "historical_events_loaded": len(events),
        "scenario": scenario.scenario_id,
        "protected_files_changed": changed,
        "output": str(out),
    }, indent=2))


if __name__ == "__main__":
    main()
