#!/usr/bin/env python3
"""Exact regression gate for MaxPF dominance pruning."""

from __future__ import annotations

import random
from typing import Dict, Iterable, List, Sequence, Tuple

from alternate_history_maxpf import EMPTY, best_lineup_points, clear_best_lineup_cache
from run_fsffl_counterfactual_replay import eligible


def reference_best_lineup_points(
    roster_players: Iterable[str],
    slots: Sequence[str],
    positions: Dict[str, str],
    realized_points: Dict[str, float],
) -> Tuple[float, List[str]]:
    players = tuple(sorted(
        str(pid) for pid in roster_players
        if str(pid) not in {EMPTY, "None", ""}
        and str(pid) in realized_points
    ))
    slot_tuple = tuple(str(x) for x in slots)
    empty_lineup = tuple(EMPTY for _ in slot_tuple)
    dp: Dict[int, Tuple[float, Tuple[str, ...]]] = {0: (0.0, empty_lineup)}

    def better(candidate_score, candidate_lineup, incumbent):
        if incumbent is None:
            return True
        incumbent_score, incumbent_lineup = incumbent
        if candidate_score > incumbent_score + 1e-9:
            return True
        if abs(candidate_score - incumbent_score) <= 1e-9 and candidate_lineup < incumbent_lineup:
            return True
        return False

    for pid in players:
        position = positions.get(pid, "")
        points = float(realized_points.get(pid) or 0.0)
        eligible_slots = [idx for idx, slot in enumerate(slot_tuple) if eligible(position, slot)]
        if not eligible_slots:
            continue
        next_dp = dict(dp)
        for mask, (score, lineup) in dp.items():
            for slot_idx in eligible_slots:
                bit = 1 << slot_idx
                if mask & bit:
                    continue
                new_mask = mask | bit
                new_lineup_list = list(lineup)
                new_lineup_list[slot_idx] = pid
                new_lineup = tuple(new_lineup_list)
                candidate_score = score + points
                incumbent = next_dp.get(new_mask)
                if better(candidate_score, new_lineup, incumbent):
                    next_dp[new_mask] = (candidate_score, new_lineup)
        dp = next_dp

    best_score = -1.0
    best_lineup = empty_lineup
    for score, lineup in dp.values():
        if score > best_score + 1e-9 or (
            abs(score - best_score) <= 1e-9 and lineup < best_lineup
        ):
            best_score, best_lineup = score, lineup
    return round(max(best_score, 0.0), 2), list(best_lineup)


def main() -> None:
    rng = random.Random(20260825)
    slots = ("QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "SUPER_FLEX")
    position_pool = ("QB", "RB", "WR", "TE")

    # Include randomized normal rosters plus deliberately tie-heavy and
    # position-deficient cases to exercise exact lexical tie behavior.
    for case in range(250):
        n = rng.randint(6, 24)
        roster = [f"p{i:02d}" for i in range(n)]
        positions = {pid: rng.choice(position_pool) for pid in roster}
        if case % 5 == 0:
            points = {pid: float(rng.choice((0, 5, 10, 15))) for pid in roster}
        else:
            points = {pid: round(rng.uniform(-3, 35), 3) for pid in roster}
        if case % 11 == 0:
            for pid in roster:
                positions[pid] = "WR"
        if case % 17 == 0:
            for pid in roster:
                points[pid] = 10.0

        expected = reference_best_lineup_points(roster, slots, positions, points)
        clear_best_lineup_cache()
        actual = best_lineup_points(roster, slots, positions, points)
        if actual != expected:
            raise AssertionError(
                f"MaxPF mismatch in case {case}: expected={expected!r}, actual={actual!r}"
            )

    print("PASS: MaxPF dominance pruning is exact-equivalent")


if __name__ == "__main__":
    main()
