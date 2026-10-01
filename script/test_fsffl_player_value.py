#!/usr/bin/env python3
"""Governance and calculation regressions for native FSFFL player value."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MODULE = ROOT / "script" / "player_value" / "application.py"
spec = importlib.util.spec_from_file_location("fsffl_player_value_application", MODULE)
assert spec is not None and spec.loader is not None
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


league = ROOT / "data" / "league.json"
projections = ROOT / "data" / "simulator" / "2026" / "inputs" / "player_weekly_projections.json"
rosters = ROOT / "data" / "rosters.json"
before = {path: digest(path) for path in (league, projections, rosters)}
payload = app.build_current_season_values(league, projections, rosters)
after = {path: digest(path) for path in (league, projections, rosters)}

assert before == after
assert payload["status"] == "CURRENT_SEASON_GOVERNED"
assert payload["market_inputs_consumed"] is False
assert payload["gm3_inputs_consumed"] is False
assert payload["strategic_posture_consumed"] is False
assert payload["not_a_trade_price"] is True
assert payload["recommendation"] is False
assert payload["long_term_model"]["available"] is False
assert payload["long_term_model"]["status"] == "REQUIRES_TEMPORAL_VALIDATION"

context = payload["league_context"]
assert context["league_size"] == 12
assert context["starter_slots_per_team"] == {
    "QB": 1, "RB": 2, "WR": 3, "TE": 1, "FLEX": 1, "SUPER_FLEX": 1,
}
assert sum(context["optimal_position_allocation"].values()) == 108

players = payload["players"]
assert len(players) == 231
assert sum(bool(row["expected_lineup_contributor"]) for row in players) >= 108
assert all(row["fsffl_current_season_value"] >= 0 for row in players)
assert all(row["weeks_covered"] == 14 for row in players)
assert all(row["actual_roster_contexts_evaluated"] in {11, 12} for row in players)
assert players[0]["fsffl_current_season_rank"] == 1
assert players[0]["fsffl_current_season_value"] >= players[1]["fsffl_current_season_value"]

published = ROOT / "data" / "player_value" / "fsffl_current_season_value.json"
stored = json.loads(published.read_text(encoding="utf-8"))
assert stored["source_manifest"] == payload["source_manifest"]
assert stored["model_version"] == payload["model_version"]

print("FSFFL native current-season player-value regressions passed")
