#!/usr/bin/env python3
"""Six-team Sleeper postseason utilities for alternate-history season feedback.

The routing matches the FSFFL structure already backvalidated against historical
Sleeper playoff matchups. Inputs are branch-specific standings and immutable
realized fantasy scores for playoff weeks.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import alternate_history_engine as ah


def winner(
    a: str,
    b: str,
    scores: Dict[str, float],
    seed_by_roster: Dict[str, int],
) -> Tuple[str, str, str | None]:
    sa = float(scores.get(str(a), 0.0))
    sb = float(scores.get(str(b), 0.0))
    if abs(sa - sb) <= 0.0001:
        win = str(a) if int(seed_by_roster[str(a)]) < int(seed_by_roster[str(b)]) else str(b)
        tiebreak = "higher_seed"
    else:
        win = str(a) if sa > sb else str(b)
        tiebreak = None
    lose = str(b) if win == str(a) else str(a)
    return win, lose, tiebreak


def resolve_six_team_playoffs(
    standings: List[Dict[str, Any]],
    weekly_scores: Dict[str, Dict[str, float]],
    playoff_start: int,
) -> Dict[str, Any]:
    if len(standings) < 6:
        raise ah.AlternateHistoryError("Six-team playoff resolution requires at least six standings rows")
    top6 = standings[:6]
    seed_to_roster = {int(row["seed"]): str(row["roster_id"]) for row in top6}
    seed_by_roster = {rid: seed for seed, rid in seed_to_roster.items()}

    w15 = {rid: float((weekly_scores.get(rid) or {}).get(str(playoff_start), 0.0)) for rid in seed_by_roster}
    qf36w, qf36l, qf36tb = winner(seed_to_roster[3], seed_to_roster[6], w15, seed_by_roster)
    qf45w, qf45l, qf45tb = winner(seed_to_roster[4], seed_to_roster[5], w15, seed_by_roster)

    w16 = {rid: float((weekly_scores.get(rid) or {}).get(str(playoff_start + 1), 0.0)) for rid in seed_by_roster}
    sf1w, sf1l, sf1tb = winner(seed_to_roster[1], qf45w, w16, seed_by_roster)
    sf2w, sf2l, sf2tb = winner(seed_to_roster[2], qf36w, w16, seed_by_roster)

    # FSFFL historical validation uses Week 16 score to order the two
    # quarterfinal losers into fifth/sixth.
    fifth, sixth, fifth_tb = winner(qf36l, qf45l, w16, seed_by_roster)

    w17 = {rid: float((weekly_scores.get(rid) or {}).get(str(playoff_start + 2), 0.0)) for rid in seed_by_roster}
    champ, runner_up, final_tb = winner(sf1w, sf2w, w17, seed_by_roster)
    third, fourth, third_tb = winner(sf1l, sf2l, w17, seed_by_roster)

    finish = {
        champ: 1,
        runner_up: 2,
        third: 3,
        fourth: 4,
        fifth: 5,
        sixth: 6,
    }
    draft_slots = {rid: 13 - int(place) for rid, place in finish.items()}

    return {
        "playoff_field": [{"seed": seed, "roster_id": seed_to_roster[seed]} for seed in range(1, 7)],
        "quarterfinals": [
            {"team_a": seed_to_roster[3], "team_b": seed_to_roster[6], "winner": qf36w, "loser": qf36l, "tiebreak": qf36tb},
            {"team_a": seed_to_roster[4], "team_b": seed_to_roster[5], "winner": qf45w, "loser": qf45l, "tiebreak": qf45tb},
        ],
        "semifinals": [
            {"team_a": seed_to_roster[1], "team_b": qf45w, "winner": sf1w, "loser": sf1l, "tiebreak": sf1tb},
            {"team_a": seed_to_roster[2], "team_b": qf36w, "winner": sf2w, "loser": sf2l, "tiebreak": sf2tb},
        ],
        "championship": {"team_a": sf1w, "team_b": sf2w, "winner": champ, "loser": runner_up, "tiebreak": final_tb},
        "third_place": {"team_a": sf1l, "team_b": sf2l, "winner": third, "loser": fourth, "tiebreak": third_tb},
        "fifth_sixth": {"team_a": qf36l, "team_b": qf45l, "winner": fifth, "loser": sixth, "tiebreak": fifth_tb},
        "finish_by_roster": finish,
        "playoff_draft_slots": draft_slots,
    }


def full_draft_slots(
    nonplayoff_slots: Dict[str, int],
    playoff_slots: Dict[str, int],
) -> Dict[str, int]:
    out = {str(rid): int(slot) for rid, slot in nonplayoff_slots.items()}
    out.update({str(rid): int(slot) for rid, slot in playoff_slots.items()})
    if len(out) != 12 or sorted(out.values()) != list(range(1, 13)):
        raise ah.AlternateHistoryError(
            f"Invalid full draft-slot map: teams={len(out)} slots={sorted(out.values())}"
        )
    return out
