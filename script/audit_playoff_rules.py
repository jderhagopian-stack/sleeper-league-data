#!/usr/bin/env python3
"""Rule-level regression tests for playoff qualification, seeding, and byes."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIM_PATH = ROOT / "script" / "build_fsffl_season_simulator.py"


def load_sim():
    spec = importlib.util.spec_from_file_location("fsffl_sim_playoff_audit", SIM_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to import simulator")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    sim = load_sim()
    league = {"settings": {"playoff_teams": 6, "divisions": 4}}
    roster_dir = {
        1: {"division": 1}, 2: {"division": 1}, 3: {"division": 1},
        4: {"division": 2}, 5: {"division": 2}, 6: {"division": 2},
        7: {"division": 3}, 8: {"division": 3}, 9: {"division": 3},
        10: {"division": 4}, 11: {"division": 4}, 12: {"division": 4},
    }
    # Division winners: 1,4,7,10. Team 2 has a better record than winner 10,
    # but Sleeper division rules still give 10 an automatic playoff berth and
    # a top-four seed in a four-division league.
    wins = {1: 11,2: 10,3: 4,4: 9,5: 8,6: 3,7: 8,8: 7,9: 2,10: 6,11: 5,12: 1}
    pf = {rid: 1000 + rid for rid in roster_dir}
    pa = {rid: 900 + rid for rid in roster_dir}
    order = sim.seed_playoff_field(league, roster_dir, wins, pf, pa)

    assert set(order[:4]) == {1,4,7,10}, order
    assert order.index(10) < order.index(2), order
    assert order[4] == 2, order
    assert sim.playoff_bye_count(4) == 0
    assert sim.playoff_bye_count(6) == 2
    assert sim.playoff_bye_count(8) == 0
    assert sim.standard_playoff_round_count(4) == 2
    assert sim.standard_playoff_round_count(6) == 3
    assert sim.standard_playoff_round_count(8) == 3
    assert sim.configured_playoff_weeks({"settings": {"playoff_teams": 4, "playoff_week_start": 15}}) == [15, 16]
    assert sim.configured_playoff_weeks({"settings": {"playoff_teams": 6, "playoff_week_start": 15}}) == [15, 16, 17]

    # Sleeper default standings tiebreak: record, PF, then higher PA.
    tied_wins = {1: 8, 2: 8}
    tied_pf = {1: 1200, 2: 1200}
    tied_pa = {1: 1100, 2: 1150}
    assert sim.seed_teams(tied_wins, tied_pf, tied_pa) == [2, 1]

    payload = {
        "status": "PASS",
        "evidence_class": "RULE_DEFINED",
        "fsffl_division_count": 4,
        "fsffl_playoff_teams": 6,
        "division_winners_seeded_first": True,
        "standings_tiebreak": ["record", "points_for", "higher_points_against"],
        "bye_count_by_supported_bracket": {"4": 0, "6": 2, "8": 0},
        "round_count_by_supported_bracket": {"4": 2, "6": 3, "8": 3},
        "projection_behavior_changed": False,
        "remaining_provisional_item": (
            "Raw Sleeper playoff reseeding-setting encoding is not authoritatively mapped here; "
            "the simulator preserves its existing reseeding behavior while qualification/seeding is corrected."
        ),
    }
    out = ROOT / "data" / "audit" / "playoff_rule_audit.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
