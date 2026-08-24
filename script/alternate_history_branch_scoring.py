#!/usr/bin/env python3
"""No-hindsight weekly lineup and standings utilities for alternate history.

Completed player scoring is immutable. Lineup choices are reconstructed from the
actual historical starter baseline plus only information available before the
week being scored. Alternate ownership can force removals/replacements and can
make historically-unowned players plausible starters, but current-week realized
points never influence the lineup decision itself.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List, Sequence, Set, Tuple

import alternate_history_engine as ah
from run_fsffl_counterfactual_replay import (
    EMPTY,
    ENTRY_MARGIN,
    decision_score,
    eligible,
)


def choose_branch_lineup(
    actual_row: Dict[str, Any],
    roster_players: Iterable[str],
    *,
    week: int,
    slots: Sequence[str],
    positions: Dict[str, str],
    weekly_points: Dict[int, Dict[str, float]],
    previous_alt_starters: Set[str],
) -> Tuple[List[str], List[Dict[str, Any]]]:
    """Choose a plausible branch lineup without current-week hindsight."""
    roster = {str(x) for x in roster_players}
    actual_starters = [str(x) for x in (actual_row.get("starters") or [])]
    actual_players = {str(x) for x in (actual_row.get("players") or [])}
    if len(actual_starters) != len(slots):
        raise ah.AlternateHistoryError(
            f"Branch lineup starter count {len(actual_starters)} != slots {len(slots)} "
            f"for roster {actual_row.get('roster_id')} week {week}"
        )

    lineup = list(actual_starters)
    changes: List[Dict[str, Any]] = []
    used: Set[str] = set()

    # Historical starters remain the revealed-choice baseline when still owned.
    for idx, pid in enumerate(list(lineup)):
        if pid in {EMPTY, "None", ""}:
            lineup[idx] = EMPTY
            continue
        if pid not in roster:
            changes.append({
                "type": "forced_removal",
                "slot": str(slots[idx]),
                "player_id": pid,
                "reason": "not_owned_in_branch",
            })
            lineup[idx] = EMPTY
        else:
            used.add(pid)

    def candidates(slot: str) -> List[str]:
        rows = [
            pid for pid in roster
            if pid not in used and eligible(positions.get(pid, ""), str(slot))
        ]
        rows.sort(
            key=lambda pid: (
                decision_score(pid, week, weekly_points, previous_alt_starters),
                pid,
            ),
            reverse=True,
        )
        return rows

    # Ownership divergence can create a forced hole. Fill it using prior-week
    # evidence only, exactly as the direct-replay model does.
    for idx, pid in enumerate(list(lineup)):
        if pid != EMPTY:
            continue
        cands = candidates(str(slots[idx]))
        if not cands:
            continue
        replacement = cands[0]
        lineup[idx] = replacement
        used.add(replacement)
        changes.append({
            "type": "forced_replacement",
            "slot": str(slots[idx]),
            "player_id": replacement,
            "pre_week_score": decision_score(
                replacement, week, weekly_points, previous_alt_starters
            ),
        })

    # Players who are on the branch roster but were not on this manager's actual
    # weekly roster are the genuine alternate candidates. Let the strongest
    # pre-week candidates challenge the weakest eligible incumbent one at a time.
    alternate_only = [pid for pid in roster if pid not in actual_players and pid not in used]
    alternate_only.sort(
        key=lambda pid: (
            decision_score(pid, week, weekly_points, previous_alt_starters),
            pid,
        ),
        reverse=True,
    )
    for candidate in alternate_only:
        cand_score = decision_score(candidate, week, weekly_points, previous_alt_starters)
        eligible_incumbents: List[Tuple[float, int, str]] = []
        for idx, incumbent in enumerate(lineup):
            if incumbent in {EMPTY, "None", ""}:
                continue
            if not eligible(positions.get(candidate, ""), str(slots[idx])):
                continue
            incumbent_score = decision_score(
                incumbent, week, weekly_points, previous_alt_starters
            )
            eligible_incumbents.append((incumbent_score, idx, incumbent))
        if not eligible_incumbents:
            continue
        incumbent_score, idx, incumbent = min(
            eligible_incumbents,
            key=lambda row: (row[0], row[2]),
        )
        if cand_score < incumbent_score + ENTRY_MARGIN:
            continue
        lineup[idx] = candidate
        used.discard(incumbent)
        used.add(candidate)
        changes.append({
            "type": "evidence_based_entry",
            "slot": str(slots[idx]),
            "player_id": candidate,
            "replaced_player_id": incumbent,
            "candidate_pre_week_score": cand_score,
            "incumbent_pre_week_score": incumbent_score,
            "entry_margin": ENTRY_MARGIN,
        })

    return lineup, changes


def realized_lineup_points(
    lineup: Sequence[str],
    *,
    week: int,
    weekly_points: Dict[int, Dict[str, float]],
) -> Tuple[float, List[str]]:
    missing: List[str] = []
    total = 0.0
    realized = weekly_points.get(int(week), {})
    for pid in lineup:
        if pid in {EMPTY, "None", ""}:
            continue
        if str(pid) not in realized:
            missing.append(str(pid))
            continue
        total += float(realized[str(pid)])
    return round(total, 2), missing


def update_records_from_week(
    records: Dict[str, Dict[str, float]],
    matchup_rows: List[Dict[str, Any]],
    scores: Dict[str, float],
) -> None:
    by_matchup: Dict[str, List[str]] = defaultdict(list)
    for row in matchup_rows:
        mid = row.get("matchup_id")
        rid = str(row.get("roster_id"))
        if mid is None:
            continue
        by_matchup[str(mid)].append(rid)

    for rid, score in scores.items():
        rec = records.setdefault(str(rid), {
            "wins": 0.0,
            "losses": 0.0,
            "ties": 0.0,
            "points_for": 0.0,
            "points_against": 0.0,
        })
        rec["points_for"] += float(score)

    for teams in by_matchup.values():
        if len(teams) != 2:
            continue
        a, b = teams
        sa, sb = float(scores.get(a, 0.0)), float(scores.get(b, 0.0))
        records.setdefault(a, {"wins": 0.0, "losses": 0.0, "ties": 0.0, "points_for": 0.0, "points_against": 0.0})
        records.setdefault(b, {"wins": 0.0, "losses": 0.0, "ties": 0.0, "points_for": 0.0, "points_against": 0.0})
        records[a]["points_against"] += sb
        records[b]["points_against"] += sa
        if abs(sa - sb) <= 0.0001:
            records[a]["ties"] += 1.0
            records[b]["ties"] += 1.0
        elif sa > sb:
            records[a]["wins"] += 1.0
            records[b]["losses"] += 1.0
        else:
            records[b]["wins"] += 1.0
            records[a]["losses"] += 1.0


def seeded_standings(records: Dict[str, Dict[str, float]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for rid, rec in records.items():
        rows.append({
            "roster_id": str(rid),
            "wins": int(rec.get("wins") or 0),
            "losses": int(rec.get("losses") or 0),
            "ties": int(rec.get("ties") or 0),
            "points_for": round(float(rec.get("points_for") or 0.0), 2),
            "points_against": round(float(rec.get("points_against") or 0.0), 2),
        })
    rows.sort(
        key=lambda x: (
            x["wins"] + 0.5 * x["ties"],
            x["points_for"],
            -int(x["roster_id"]),
        ),
        reverse=True,
    )
    for seed, row in enumerate(rows, 1):
        row["seed"] = seed
    return rows
