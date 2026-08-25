#!/usr/bin/env python3
"""Regression tests for branch-local historical trade persistence."""
from __future__ import annotations

import copy

import alternate_history_engine as ah
import alternate_history_trade_persistence_runtime as tp
import run_fsffl_multiseason_branch_replay as branch_v1


class StubPoints:
    def trailing(self, season, week, pid):
        scores = {"A": 10.0, "B": 10.5, "X": 12.0}
        value = scores.get(str(pid))
        return {"score": value, "observations": 4 if value is not None else 0}


def state(players, picks=None):
    return branch_v1.serial(ah.LeagueState(
        league_key="test",
        timestamp_ms=1,
        roster_players={str(k): set(v) for k, v in players.items()},
        pick_owners=dict(picks or {}),
        faab={},
    ))


def probs(rows):
    return sum(float(x.get("probability") or 0.0) for x in rows)


def main():
    tp._POSITIONS = {"A": "WR", "B": "WR", "X": "RB"}
    tp._POINTS = StubPoints()
    # Make the behavioral-context test deterministic and independent of fixture history.
    tp._need_similarity = lambda payload, event: 1.0
    tp._competitive_similarity = lambda payload, event: 1.0

    trade = {
        "transaction_id": "t1", "created": 1000, "type": "trade",
        "source_season": "2024", "leg": 8, "roster_ids": ["1", "2"],
        "drops": {"A": "1", "X": "2"}, "adds": {"A": "2", "X": "1"},
        "draft_picks": [],
    }
    proposed = [
        {"outcome": "preserve_historical_trade", "probability": 0.68, "mode": "exact"},
        {"outcome": "no_trade", "probability": 0.32, "mode": "no_action"},
    ]

    # Missing A, but same-position/same-value B is owned by the same sender and
    # the target X still exists. Most historical intent should survive.
    s = state({"1": {"B"}, "2": {"X"}})
    rows = tp.branch_specific_outcomes_v2(s, trade, copy.deepcopy(proposed))
    assert abs(probs(rows) - 1.0) < 1e-9
    equivalent = [x for x in rows if x.get("equivalent_trade")]
    assert equivalent, rows
    eq_mass = sum(float(x["probability"]) for x in equivalent)
    assert eq_mass >= 0.60, eq_mass
    for row in equivalent:
        legal, reasons = branch_v1.event_legality(branch_v1.to_state(s), row["event"])
        assert legal, reasons
        assert row["event"]["drops"].get("B") == "1"
        assert row["event"]["adds"].get("B") == "2"

    # A still-legal historical trade is bit-for-bit governed by the old branch
    # filter; persistence must not perturb it.
    legal_state = state({"1": {"A", "B"}, "2": {"X"}})
    old = tp._ORIGINAL(legal_state, trade, copy.deepcopy(proposed))
    new = tp.branch_specific_outcomes_v2(legal_state, trade, copy.deepcopy(proposed))
    assert new == old

    # If target-side and payment-side historical assets both disappeared, do not
    # fabricate an entirely new bilateral bargain.
    both_missing = state({"1": {"B"}, "2": set()})
    rows = tp.branch_specific_outcomes_v2(both_missing, trade, copy.deepcopy(proposed))
    assert not [x for x in rows if x.get("equivalent_trade")]

    # Same-season/same-round branch-owned draft capital can substitute for an
    # unavailable exact pick while the historical incoming player remains.
    pick_trade = {
        "transaction_id": "t2", "created": 1000, "type": "trade",
        "source_season": "2024", "leg": 8, "roster_ids": ["1", "2"],
        "drops": {"X": "2"}, "adds": {"X": "1"},
        "draft_picks": [{"season": "2027", "round": 1, "roster_id": 1, "previous_owner_id": 1, "owner_id": 2}],
    }
    pick_state = state(
        {"1": {"B"}, "2": {"X"}},
        {"pick:2027:R1:orig1": "4", "pick:2027:R1:orig3": "1"},
    )
    rows = tp.branch_specific_outcomes_v2(pick_state, pick_trade, copy.deepcopy(proposed))
    eq = [x for x in rows if x.get("equivalent_trade")]
    assert eq, rows
    assert any((x["event"].get("draft_picks") or [{}])[0].get("roster_id") == "3" for x in eq)
    assert all(abs(probs(rows) - 1.0) < 1e-9 for _ in [0])

    print("PASS: behavioral trade persistence preserves legal history and repairs comparable illegal payment legs")


if __name__ == "__main__":
    main()
