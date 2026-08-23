#!/usr/bin/env python3
"""FSFFL Alternate History 0.3: direct historical counterfactual replay.

This stage changes fantasy ownership while keeping completed NFL outcomes fixed.
It intentionally preserves actual historical starter choices except where the
fork forces a replacement or a newly-owned player has enough PRE-WEEK evidence
to plausibly enter the lineup. The current week's realized fantasy points are
never used to decide that week's lineup.

0.3 scope:
- one completed season;
- player_swap forks;
- actual historical schedule remains fixed;
- regular-season results are replayed for every affected matchup;
- postseason re-seeding and downstream transaction branching are later stages.
"""

from __future__ import annotations

import argparse
import copy
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import alternate_history_engine as ah
from run_fsffl_alternate_history import FSFFLHistoricalAdapter

DATA = Path("data")
ENTRY_MARGIN = 1.0
STARTER_STICKINESS = 1.0
EMPTY = "0"


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def player_positions() -> Dict[str, str]:
    players = load(DATA / "players.json")
    return {str(pid): str(row.get("position") or "") for pid, row in players.items()}


def starter_slots(league: Dict[str, Any]) -> List[str]:
    return [str(x) for x in (league.get("roster_positions") or []) if str(x) != "BN"]


def eligible(position: str, slot: str) -> bool:
    pos = str(position or "").upper()
    slot = str(slot or "").upper()
    if slot in {"QB", "RB", "WR", "TE", "K", "DEF", "DL", "LB", "DB"}:
        return pos == slot
    if slot in {"FLEX", "REC_FLEX"}:
        return pos in {"RB", "WR", "TE"}
    if slot in {"SUPER_FLEX", "SUPERFLEX"}:
        return pos in {"QB", "RB", "WR", "TE"}
    if slot == "WRRB_FLEX":
        return pos in {"RB", "WR"}
    return False


def weekly_points_index(matchups: Dict[str, List[Dict[str, Any]]]) -> Dict[int, Dict[str, float]]:
    out: Dict[int, Dict[str, float]] = defaultdict(dict)
    for week_key, rows in matchups.items():
        week = int(week_key)
        for row in rows:
            for pid, pts in (row.get("players_points") or {}).items():
                pid = str(pid)
                value = float(pts or 0.0)
                prior = out[week].get(pid)
                if prior is not None and abs(prior - value) > 0.011:
                    raise ah.AlternateHistoryError(
                        f"Conflicting historical points for player {pid} week {week}: {prior} vs {value}"
                    )
                out[week][pid] = value
    return out


def decision_score(
    pid: str,
    week: int,
    weekly_points: Dict[int, Dict[str, float]],
    previous_alt_starters: Set[str],
) -> float:
    """No-hindsight lineup signal using only weeks strictly before `week`."""
    history: List[float] = []
    for w in range(max(1, week - 3), week):
        if pid in weekly_points.get(w, {}):
            history.append(float(weekly_points[w][pid]))
    if not history:
        base = 0.0
    else:
        # Recency-weighted; all inputs are prior weeks.
        weights = [0.15, 0.30, 0.55][-len(history):]
        denom = sum(weights)
        base = sum(v * wt for v, wt in zip(history, weights)) / denom
    if pid in previous_alt_starters:
        base += STARTER_STICKINESS
    return round(base, 6)


def row_by_roster(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {str(row.get("roster_id")): row for row in rows}


def player_actual_owner(rows: List[Dict[str, Any]], pid: str) -> Optional[str]:
    for row in rows:
        if pid in {str(x) for x in (row.get("players") or [])}:
            return str(row.get("roster_id"))
    return None


def historical_player_points(
    pid: str,
    week: int,
    weekly_points: Dict[int, Dict[str, float]],
) -> float:
    # Immutable historical NFL/fantasy outcome. Missing means the local matchup
    # source did not observe the player; 0.3 reports that as unsupported rather
    # than fabricating a result.
    if pid not in weekly_points.get(week, {}):
        raise ah.AlternateHistoryError(
            f"Historical points unavailable for counterfactual player {pid} in week {week}"
        )
    return float(weekly_points[week][pid])


def adjust_lineup(
    row: Dict[str, Any],
    roster_players: Set[str],
    added_pid: Optional[str],
    week: int,
    slots: List[str],
    positions: Dict[str, str],
    weekly_points: Dict[int, Dict[str, float]],
    previous_alt_starters: Set[str],
) -> Tuple[List[str], List[Dict[str, Any]]]:
    actual_starters = [str(x) for x in (row.get("starters") or [])]
    if len(actual_starters) != len(slots):
        raise ah.AlternateHistoryError(
            f"Roster {row.get('roster_id')} week {week}: starter count {len(actual_starters)} != slots {len(slots)}"
        )

    lineup = list(actual_starters)
    changes: List[Dict[str, Any]] = []
    used = {pid for pid in lineup if pid not in {EMPTY, "None", ""} and pid in roster_players}

    # Remove actual starters no longer owned in the counterfactual.
    for idx, pid in enumerate(list(lineup)):
        if pid in {EMPTY, "None", ""}:
            lineup[idx] = EMPTY
            continue
        if pid not in roster_players:
            lineup[idx] = EMPTY
            used.discard(pid)
            changes.append(
                {
                    "type": "forced_removal",
                    "slot": slots[idx],
                    "player_id": pid,
                    "reason": "player not owned in counterfactual",
                }
            )

    def candidates_for(slot: str) -> List[str]:
        cands = []
        for pid in roster_players:
            if pid in used:
                continue
            if eligible(positions.get(pid, ""), slot):
                cands.append(pid)
        cands.sort(
            key=lambda p: (
                decision_score(p, week, weekly_points, previous_alt_starters),
                p,
            ),
            reverse=True,
        )
        return cands

    # Fill forced empty slots first using only pre-week evidence.
    for idx, pid in enumerate(list(lineup)):
        if pid != EMPTY:
            continue
        cands = candidates_for(slots[idx])
        if not cands:
            continue
        replacement = cands[0]
        lineup[idx] = replacement
        used.add(replacement)
        changes.append(
            {
                "type": "forced_replacement",
                "slot": slots[idx],
                "player_id": replacement,
                "pre_week_score": decision_score(
                    replacement, week, weekly_points, previous_alt_starters
                ),
            }
        )

    # A newly-acquired player can enter only if prior-week evidence beats an
    # eligible incumbent by a meaningful margin. This prevents hindsight starts.
    if added_pid and added_pid in roster_players and added_pid not in used:
        added_score = decision_score(added_pid, week, weekly_points, previous_alt_starters)
        eligible_slots: List[Tuple[float, int, str]] = []
        for idx, incumbent in enumerate(lineup):
            if incumbent in {EMPTY, "None", ""}:
                continue
            if not eligible(positions.get(added_pid, ""), slots[idx]):
                continue
            incumbent_score = decision_score(
                incumbent, week, weekly_points, previous_alt_starters
            )
            eligible_slots.append((incumbent_score, idx, incumbent))
        if eligible_slots:
            incumbent_score, idx, incumbent = min(eligible_slots, key=lambda x: x[0])
            if added_score >= incumbent_score + ENTRY_MARGIN:
                lineup[idx] = added_pid
                used.discard(incumbent)
                used.add(added_pid)
                changes.append(
                    {
                        "type": "evidence_based_entry",
                        "slot": slots[idx],
                        "player_id": added_pid,
                        "replaced_player_id": incumbent,
                        "added_pre_week_score": added_score,
                        "incumbent_pre_week_score": incumbent_score,
                        "entry_margin": ENTRY_MARGIN,
                    }
                )

    return lineup, changes


def lineup_points(
    lineup: List[str], week: int, weekly_points: Dict[int, Dict[str, float]]
) -> float:
    total = 0.0
    for pid in lineup:
        if pid in {EMPTY, "None", ""}:
            continue
        total += historical_player_points(pid, week, weekly_points)
    return round(total, 2)


def derive_records(
    matchups: Dict[str, List[Dict[str, Any]]],
    score_overrides: Dict[Tuple[int, str], float],
    playoff_start: int,
) -> Dict[str, Dict[str, Any]]:
    records: Dict[str, Dict[str, float]] = defaultdict(
        lambda: {"wins": 0, "losses": 0, "ties": 0, "pf": 0.0, "pa": 0.0}
    )
    for week_key, rows in matchups.items():
        week = int(week_key)
        if week >= playoff_start:
            continue
        groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for row in rows:
            mid = row.get("matchup_id")
            if mid is not None:
                groups[str(mid)].append(row)
        for pair in groups.values():
            if len(pair) != 2:
                continue
            a, b = pair
            ra, rb = str(a.get("roster_id")), str(b.get("roster_id"))
            pa = float(score_overrides.get((week, ra), a.get("points") or 0.0))
            pb = float(score_overrides.get((week, rb), b.get("points") or 0.0))
            records[ra]["pf"] += pa
            records[ra]["pa"] += pb
            records[rb]["pf"] += pb
            records[rb]["pa"] += pa
            if abs(pa - pb) <= 0.0001:
                records[ra]["ties"] += 1
                records[rb]["ties"] += 1
            elif pa > pb:
                records[ra]["wins"] += 1
                records[rb]["losses"] += 1
            else:
                records[rb]["wins"] += 1
                records[ra]["losses"] += 1
    return {
        rid: {
            "wins": int(v["wins"]),
            "losses": int(v["losses"]),
            "ties": int(v["ties"]),
            "points_for": round(v["pf"], 2),
            "points_against": round(v["pa"], 2),
        }
        for rid, v in records.items()
    }


def run(scenario_path: Path) -> Path:
    payload = load(scenario_path)
    season = str(payload.get("fork_season") or "")
    fork_week = int(payload.get("fork_week") or 1)
    if not season:
        raise ah.AlternateHistoryError("Scenario requires fork_season for historical replay")

    adapter = FSFFLHistoricalAdapter()
    scenario = ah.scenario_from_json(adapter, payload)
    if len(scenario.actions) != 1 or scenario.actions[0].action_type != "player_swap":
        raise ah.AlternateHistoryError("0.3 supports one player_swap action")
    action = scenario.actions[0]

    league = load(DATA / "league.json")
    playoff_start = int((league.get("settings") or {}).get("playoff_week_start") or 15)
    slots = starter_slots(league)
    positions = player_positions()
    matchups = load(DATA / "stats" / "fsffl" / season / "league_matchups_raw.json")
    weekly_points = weekly_points_index(matchups)

    focus = str(scenario.focus_roster_id)
    added = action.add_player_id
    dropped = action.drop_player_id
    score_overrides: Dict[Tuple[int, str], float] = {}
    week_details: List[Dict[str, Any]] = []
    previous_alt_starters: Dict[str, Set[str]] = defaultdict(set)
    affected_rosters: Set[str] = {focus}

    for week_key, rows in sorted(matchups.items(), key=lambda kv: int(kv[0])):
        week = int(week_key)
        if week < fork_week:
            continue
        by_roster = row_by_roster(rows)
        actual_added_owner = player_actual_owner(rows, str(added)) if added else None
        if actual_added_owner:
            affected_rosters.add(actual_added_owner)

        weekly_roster_players: Dict[str, Set[str]] = {
            rid: {str(x) for x in (row.get("players") or [])}
            for rid, row in by_roster.items()
        }
        # Persistent direct counterfactual ownership for this stage.
        if added:
            for rid in weekly_roster_players:
                weekly_roster_players[rid].discard(str(added))
            weekly_roster_players.setdefault(focus, set()).add(str(added))
        if dropped:
            weekly_roster_players.setdefault(focus, set()).discard(str(dropped))

        changes_this_week = []
        for rid in sorted(affected_rosters):
            row = by_roster.get(rid)
            if not row:
                continue
            roster_added = str(added) if rid == focus and added else None
            lineup, changes = adjust_lineup(
                row=row,
                roster_players=weekly_roster_players.get(rid, set()),
                added_pid=roster_added,
                week=week,
                slots=slots,
                positions=positions,
                weekly_points=weekly_points,
                previous_alt_starters=previous_alt_starters.get(rid, set()),
            )
            alt_points = lineup_points(lineup, week, weekly_points)
            actual_points = round(float(row.get("points") or 0.0), 2)
            score_overrides[(week, rid)] = alt_points
            previous_alt_starters[rid] = {p for p in lineup if p not in {EMPTY, "None", ""}}
            if changes or abs(alt_points - actual_points) > 0.011:
                changes_this_week.append(
                    {
                        "roster_id": rid,
                        "actual_points": actual_points,
                        "alternate_points": alt_points,
                        "point_delta": round(alt_points - actual_points, 2),
                        "actual_starters": [str(x) for x in (row.get("starters") or [])],
                        "alternate_starters": lineup,
                        "lineup_changes": changes,
                    }
                )

        if changes_this_week:
            week_details.append({"week": week, "affected_rosters": changes_this_week})

    actual_records = derive_records(matchups, {}, playoff_start)
    alternate_records = derive_records(matchups, score_overrides, playoff_start)
    focus_actual = actual_records.get(focus, {})
    focus_alt = alternate_records.get(focus, {})

    changed_matchups = []
    for week_key, rows in matchups.items():
        week = int(week_key)
        if week >= playoff_start or week < fork_week:
            continue
        groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for row in rows:
            if row.get("matchup_id") is not None:
                groups[str(row.get("matchup_id"))].append(row)
        for mid, pair in groups.items():
            if len(pair) != 2:
                continue
            a, b = pair
            ra, rb = str(a.get("roster_id")), str(b.get("roster_id"))
            aa, ab = float(a.get("points") or 0.0), float(b.get("points") or 0.0)
            ca = float(score_overrides.get((week, ra), aa))
            cb = float(score_overrides.get((week, rb), ab))
            actual_winner = ra if aa > ab else rb if ab > aa else "tie"
            alternate_winner = ra if ca > cb else rb if cb > ca else "tie"
            if actual_winner != alternate_winner:
                changed_matchups.append(
                    {
                        "week": week,
                        "matchup_id": mid,
                        "rosters": [ra, rb],
                        "actual_score": [round(aa, 2), round(ab, 2)],
                        "alternate_score": [round(ca, 2), round(cb, 2)],
                        "actual_winner": actual_winner,
                        "alternate_winner": alternate_winner,
                    }
                )

    report = {
        "model_version": "Fantasy-Alternate-History-0.3-direct-replay",
        "scenario_id": scenario.scenario_id,
        "title": scenario.title,
        "season": season,
        "fork_week": fork_week,
        "focus_roster_id": focus,
        "design_invariants": {
            "completed_nfl_history_is_immutable": True,
            "current_week_realized_points_not_used_for_lineup_decision": True,
            "actual_historical_lineup_is_baseline": True,
            "postseason_bracket_not_yet_reseeded": True,
            "downstream_transactions_not_yet_behaviorally_resimulated": True,
        },
        "lineup_policy": {
            "prior_weeks_used": 3,
            "recency_weights": [0.15, 0.30, 0.55],
            "starter_stickiness": STARTER_STICKINESS,
            "new_player_entry_margin": ENTRY_MARGIN,
        },
        "focus_regular_season": {
            "actual": focus_actual,
            "alternate": focus_alt,
            "win_delta": int(focus_alt.get("wins", 0)) - int(focus_actual.get("wins", 0)),
            "points_for_delta": round(
                float(focus_alt.get("points_for", 0.0)) - float(focus_actual.get("points_for", 0.0)), 2
            ),
        },
        "league_regular_season_actual": actual_records,
        "league_regular_season_alternate": alternate_records,
        "changed_matchups": changed_matchups,
        "weekly_lineup_changes": week_details,
        "dependency_events_after_fork": [
            str(x.get("transaction_id")) for x in ah.dependency_events(adapter, scenario)
        ],
        "scope_note": (
            "0.3 is a direct historical replay. It measures the immediate season effect of changed ownership "
            "using immutable realized NFL scoring and a pre-week lineup policy. Playoff re-seeding, altered "
            "draft order, downstream transactions, multi-season branching, and present-day roster outcomes "
            "are intentionally deferred to later stages."
        ),
    }
    return ah.write_isolated_json(
        f"results/{scenario.scenario_id}/historical_replay_{season}.json", report
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run FSFFL 0.3 direct counterfactual historical replay")
    parser.add_argument("scenario", type=Path)
    args = parser.parse_args()
    path = run(args.scenario)
    print(path)


if __name__ == "__main__":
    main()
