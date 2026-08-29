#!/usr/bin/env python3
"""Regression and portability audit for non-projection league configuration."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from league_rules import load_league_rules, slot_eligible_positions

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
ENGINE = ROOT / "script" / "build_fsffl_gm_engine.py"


def main():
    actual = load_league_rules(DATA / "league.json", DATA / "traded_picks.json")
    # These are regression assertions against the current FSFFL rule file, not
    # generic model assumptions.
    assert actual["team_count"] == 12
    assert actual["roster_size"] == 18
    assert actual["draft_rounds"] == 3
    assert actual["ppr"] == 0.5
    assert actual["superflex"] is True
    assert actual["market_num_qbs"] == 2
    assert actual["lineup_slots"] == [
        "QB","RB","RB","WR","WR","WR","TE","FLEX","SUPER_FLEX"
    ]

    # Prove the resolver changes behavior when league rules change.
    synthetic = {
        "league_id": "synthetic",
        "season": "2031",
        "total_rosters": 10,
        "roster_positions": [
            "QB","RB","RB","WR","WR","TE","FLEX","K","DEF",
            "BN","BN","BN","BN","BN","BN"
        ],
        "scoring_settings": {"rec": 1.0},
        "settings": {
            "num_teams": 10,
            "draft_rounds": 5,
            "playoff_teams": 4,
            "playoff_week_start": 16,
            "reserve_slots": 3,
            "taxi_slots": 4,
            "trade_deadline": 11,
        },
    }
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "league.json"
        p.write_text(json.dumps(synthetic), encoding="utf-8")
        alt = load_league_rules(p, None)

    assert alt["team_count"] == 10
    assert alt["roster_size"] == 15
    assert alt["draft_rounds"] == 5
    assert alt["rounds"] == [1,2,3,4,5]
    assert alt["ppr"] == 1.0
    assert alt["superflex"] is False
    assert alt["market_num_qbs"] == 1
    assert "K" in alt["positions"] and "DEF" in alt["positions"]
    assert slot_eligible_positions("FLEX") == ("RB","WR","TE")
    assert slot_eligible_positions("SUPER_FLEX") == ("QB","RB","WR","TE")
    assert slot_eligible_positions("DST") == ("DEF",)

    src = ENGINE.read_text(encoding="utf-8")
    market = json.loads((DATA / "market_values_fantasycalc.json").read_text(encoding="utf-8"))
    market_positions = {str(x.get("position") or "").upper() for x in (market.get("dynasty") or [])}
    missing_portable_market_positions = sorted({"K", "DEF"} - market_positions)
    assert missing_portable_market_positions == ["DEF", "K"]
    assert "market_position_coverage" in src

    forbidden = [
        '"numQbs": 2',
        '"numTeams": 12',
        '"roster_size": 18',
        'FUTURE_PICK_YEARS = [2027, 2028, 2029]',
        'ROUNDS = [1, 2, 3]',
        'POSITIONS = ("QB", "RB", "WR", "TE")',
    ]
    for marker in forbidden:
        assert marker not in src, marker

    assert 'LEAGUE_RULES["team_count"]' in src
    assert 'LEAGUE_RULES["roster_size"]' in src
    assert 'LEAGUE_RULES["rounds"]' in src
    assert 'LEAGUE_RULES["positions"]' in src

    payload = {
        "status": "PASS",
        "production_projection_behavior_changed": False,
        "fsffl_regression_rules": {
            k: actual[k] for k in (
                "team_count","roster_size","draft_rounds","ppr","superflex",
                "market_num_qbs","lineup_slots","playoff_teams","playoff_week_start"
            )
        },
        "synthetic_portability": {
            k: alt[k] for k in (
                "team_count","roster_size","draft_rounds","ppr","superflex",
                "market_num_qbs","lineup_slots","positions"
            )
        },
        "evidence_class": "RULE_DEFINED",
        "market_coverage": {
            "k_dst_lineup_legality_supported": True,
            "k_dst_external_market_valuation_supported": False,
            "missing_from_current_dynasty_market": missing_portable_market_positions,
            "policy": "Do not silently interpret unsupported market positions as zero-value evidence.",
        },
        "remaining_provisional_scope": actual["provisional_runtime_defaults"],
    }
    out = DATA / "audit" / "nonprojection_league_configuration_audit.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
