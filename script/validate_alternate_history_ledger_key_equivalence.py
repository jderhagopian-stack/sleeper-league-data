#!/usr/bin/env python3
"""Exact equality-class validation for per-season ledger fingerprinting."""

from __future__ import annotations

import copy

import alternate_history_performance_runtime as perf
import alternate_history_ledger_key_runtime as ledger_key
import run_fsffl_multiseason_particle_replay_v3 as season_v3


def reference_key(state):
    return perf._state_key_with_memo(state)


def main() -> None:
    base = {
        "roster_players": {"1": ["1", "2"], "2": ["3"]},
        "roster_taxi": {"1": []},
        "roster_reserve": {"2": []},
        "pick_owners": {"pick:2027:R1:orig1": "2"},
        "faab": {"1": 50.0, "2": 100.0},
        season_v3.DRAFT_KEY: {"selected_player_ids": ["9"], "picks": [{"player_id": "9"}]},
        season_v3.LEDGER_KEY: {
            "2024": {"season_max_pf": {"1": 1900.25}, "records": {"1": {"wins": 9}}},
            "2025": {"season_max_pf": {"1": 2010.75}, "records": {"1": {"wins": 10}}},
        },
    }
    equivalent = copy.deepcopy(base)
    equivalent[season_v3.LEDGER_KEY] = {
        "2025": copy.deepcopy(base[season_v3.LEDGER_KEY]["2025"]),
        "2024": copy.deepcopy(base[season_v3.LEDGER_KEY]["2024"]),
    }
    changed_row = copy.deepcopy(base)
    changed_row[season_v3.LEDGER_KEY]["2025"]["records"]["1"]["wins"] = 11
    changed_season = copy.deepcopy(base)
    changed_season[season_v3.LEDGER_KEY]["2026"] = {"season_max_pf": {"1": 0.0}}

    states = [base, equivalent, changed_row, changed_season]
    reference = [[reference_key(a) == reference_key(b) for b in states] for a in states]

    ledger_key.install()
    optimized = [[perf._state_key_with_memo(a) == perf._state_key_with_memo(b) for b in states] for a in states]
    if optimized != reference:
        raise AssertionError(f"ledger fingerprint changed state equality classes: ref={reference} opt={optimized}")

    shared_2024 = base[season_v3.LEDGER_KEY]["2024"]
    next_ledger = dict(base[season_v3.LEDGER_KEY])
    next_ledger["2025"] = copy.deepcopy(base[season_v3.LEDGER_KEY]["2025"])
    if next_ledger["2024"] is not shared_2024:
        raise AssertionError("test fixture failed to preserve shared completed-season row")
    first = ledger_key.ledger_fingerprint(base[season_v3.LEDGER_KEY])
    second = ledger_key.ledger_fingerprint(next_ledger)
    if first != second:
        raise AssertionError("equal ledger content produced different season-row fingerprints")

    print("PASS: per-season ledger fingerprint preserves exact state equality classes")


if __name__ == "__main__":
    main()
