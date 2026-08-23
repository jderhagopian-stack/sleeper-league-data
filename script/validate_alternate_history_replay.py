#!/usr/bin/env python3
"""Alternate History 0.2 no-fork historical replay validator.

Validation contract:
- Completed NFL/fantasy scoring is immutable historical fact.
- Replaying actual FSFFL weekly results with no counterfactual fork must
  reproduce independently-built Record Book regular-season results.
- This validator reads canonical artifacts and writes nothing outside the
  isolated data/alternate_history namespace.

The validator intentionally uses recorded starters and recorded player points
for the no-fork control. Counterfactual lineup policy is a later stage; first
we prove that the historical baseline can be reproduced exactly.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

import alternate_history_engine as ah

DATA = Path("data")
SEASONS = ("2022", "2023", "2024", "2025")
TOL = 0.011
EMPTY_PLAYER_IDS = {"0", "None", ""}


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def close(a: float, b: float, tol: float = TOL) -> bool:
    return abs(float(a) - float(b)) <= tol


def roster_owner_map() -> Dict[str, str]:
    out: Dict[str, str] = {}
    for row in load(DATA / "rosters.json"):
        rid = str(row.get("roster_id"))
        uid = str(row.get("owner_id"))
        if rid and uid and uid != "None":
            out[rid] = uid
    return out


def replay_season(season: str, playoff_start: int) -> Dict[str, Any]:
    path = DATA / "stats" / "fsffl" / season / "league_matchups_raw.json"
    payload = load(path)

    errors: List[str] = []
    records: Dict[str, Dict[str, float]] = defaultdict(
        lambda: {"games": 0, "wins": 0, "losses": 0, "ties": 0, "pf": 0.0, "pa": 0.0}
    )
    regular_games = 0
    postseason_games = 0
    non_game_rows = 0
    empty_starter_slots = 0
    rows_checked = 0
    lineup_point_checks = 0
    player_point_checks = 0

    for week_key, rows in sorted(payload.items(), key=lambda kv: int(kv[0])):
        week = int(week_key)
        groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

        for row in rows:
            rows_checked += 1
            rid = str(row.get("roster_id"))
            raw_matchup_id = row.get("matchup_id")
            if raw_matchup_id is None:
                # Sleeper retains inactive/consolation rows with no matchup.
                # They are roster snapshots, not played head-to-head games.
                non_game_rows += 1
            else:
                groups[str(raw_matchup_id)].append(row)

            starters = [str(x) for x in (row.get("starters") or [])]
            starter_points = [float(x or 0.0) for x in (row.get("starters_points") or [])]
            players = {str(x) for x in (row.get("players") or [])}
            players_points = {str(k): float(v or 0.0) for k, v in (row.get("players_points") or {}).items()}

            if len(starters) != len(starter_points):
                errors.append(f"{season} W{week} R{rid}: starter/points length mismatch")
            else:
                recomputed = round(sum(starter_points), 2)
                reported = round(float(row.get("points") or 0.0), 2)
                if not close(recomputed, reported):
                    errors.append(
                        f"{season} W{week} R{rid}: lineup sum {recomputed} != reported {reported}"
                    )
                lineup_point_checks += 1

            for pid, pts in zip(starters, starter_points):
                # Sleeper uses player id 0 as an explicitly empty starter slot.
                if pid in EMPTY_PLAYER_IDS:
                    empty_starter_slots += 1
                    if not close(pts, 0.0):
                        errors.append(f"{season} W{week} R{rid}: empty starter slot scored {pts}")
                    continue
                if pid not in players:
                    errors.append(f"{season} W{week} R{rid}: starter {pid} absent from rostered players")
                if pid in players_points and not close(players_points[pid], pts):
                    errors.append(
                        f"{season} W{week} R{rid}: starter {pid} points {pts} != player map {players_points[pid]}"
                    )
                player_point_checks += 1

        for matchup_id, pair in groups.items():
            # Sleeper can expose a one-row postseason bye. It is not a played game.
            if len(pair) == 1:
                continue
            if len(pair) != 2:
                errors.append(f"{season} W{week} M{matchup_id}: expected 2 teams, got {len(pair)}")
                continue

            a, b = pair
            ra, rb = str(a.get("roster_id")), str(b.get("roster_id"))
            pa, pb = float(a.get("points") or 0.0), float(b.get("points") or 0.0)

            if week < playoff_start:
                regular_games += 1
                for rid, pf, against in ((ra, pa, pb), (rb, pb, pa)):
                    rec = records[rid]
                    rec["games"] += 1
                    rec["pf"] += pf
                    rec["pa"] += against
                if close(pa, pb, tol=0.0001):
                    records[ra]["ties"] += 1
                    records[rb]["ties"] += 1
                elif pa > pb:
                    records[ra]["wins"] += 1
                    records[rb]["losses"] += 1
                else:
                    records[rb]["wins"] += 1
                    records[ra]["losses"] += 1
            else:
                postseason_games += 1

    serial_records = {}
    for rid, rec in records.items():
        serial_records[rid] = {
            "games": int(rec["games"]),
            "wins": int(rec["wins"]),
            "losses": int(rec["losses"]),
            "ties": int(rec["ties"]),
            "points_for": round(rec["pf"], 2),
            "points_against": round(rec["pa"], 2),
        }

    return {
        "season": season,
        "regular_season_games": regular_games,
        "postseason_games": postseason_games,
        "non_game_rows": non_game_rows,
        "empty_starter_slots": empty_starter_slots,
        "rows_checked": rows_checked,
        "lineup_point_checks": lineup_point_checks,
        "player_point_checks": player_point_checks,
        "records_by_roster": serial_records,
        "errors": errors,
    }


def main() -> None:
    league = load(DATA / "league.json")
    playoff_start = int((league.get("settings") or {}).get("playoff_week_start") or 15)
    record_book = load(DATA / "record_book" / "competition_records.json")
    owner_by_roster = roster_owner_map()

    season_results = [replay_season(s, playoff_start) for s in SEASONS]
    errors: List[str] = [e for season in season_results for e in season["errors"]]

    aggregate: Dict[str, Dict[str, float]] = defaultdict(
        lambda: {"games": 0, "wins": 0, "losses": 0, "ties": 0, "pf": 0.0, "pa": 0.0}
    )
    for season in season_results:
        for rid, rec in season["records_by_roster"].items():
            uid = owner_by_roster.get(str(rid))
            if not uid:
                errors.append(f"No current owner mapping for roster {rid}")
                continue
            a = aggregate[uid]
            a["games"] += rec["games"]
            a["wins"] += rec["wins"]
            a["losses"] += rec["losses"]
            a["ties"] += rec["ties"]
            a["pf"] += rec["points_for"]
            a["pa"] += rec["points_against"]

    expected_by_user = {
        str(row["user_id"]): row for row in (record_book.get("franchise_regular_season") or [])
    }
    comparisons = []
    for uid, actual in sorted(aggregate.items()):
        expected = expected_by_user.get(uid)
        if expected is None:
            errors.append(f"Replay user {uid} missing from Record Book benchmark")
            continue
        checks = {
            "games": int(actual["games"]) == int(expected.get("games") or 0),
            "wins": int(actual["wins"]) == int(expected.get("wins") or 0),
            "losses": int(actual["losses"]) == int(expected.get("losses") or 0),
            "ties": int(actual["ties"]) == int(expected.get("ties") or 0),
            "points_for": close(round(actual["pf"], 2), float(expected.get("points_for") or 0.0)),
            "points_against": close(round(actual["pa"], 2), float(expected.get("points_against") or 0.0)),
        }
        if not all(checks.values()):
            errors.append(f"Record Book mismatch for user {uid}: {checks}")
        comparisons.append(
            {
                "user_id": uid,
                "team_name": expected.get("team_name"),
                "replay": {
                    "games": int(actual["games"]),
                    "wins": int(actual["wins"]),
                    "losses": int(actual["losses"]),
                    "ties": int(actual["ties"]),
                    "points_for": round(actual["pf"], 2),
                    "points_against": round(actual["pa"], 2),
                },
                "record_book": {
                    "games": int(expected.get("games") or 0),
                    "wins": int(expected.get("wins") or 0),
                    "losses": int(expected.get("losses") or 0),
                    "ties": int(expected.get("ties") or 0),
                    "points_for": float(expected.get("points_for") or 0.0),
                    "points_against": float(expected.get("points_against") or 0.0),
                },
                "checks": checks,
            }
        )

    expected_counts = record_book.get("counts") or {}
    replay_regular = sum(x["regular_season_games"] for x in season_results)
    replay_post = sum(x["postseason_games"] for x in season_results)
    if replay_regular != int(expected_counts.get("regular_season_games") or -1):
        errors.append(
            f"Regular-season game count {replay_regular} != benchmark {expected_counts.get('regular_season_games')}"
        )
    if replay_post != int(expected_counts.get("postseason_schedule_games") or -1):
        errors.append(
            f"Postseason game count {replay_post} != benchmark {expected_counts.get('postseason_schedule_games')}"
        )

    report = {
        "model_version": "Fantasy-Alternate-History-0.2-replay-validation",
        "status": "PASS" if not errors else "FAIL",
        "design_invariants": {
            "completed_nfl_history_is_immutable": True,
            "no_fork_control_uses_recorded_starters": True,
            "canonical_data_is_read_only": True,
        },
        "seasons": list(SEASONS),
        "playoff_week_start": playoff_start,
        "replay_counts": {
            "regular_season_games": replay_regular,
            "postseason_games": replay_post,
            "non_game_rows": sum(x["non_game_rows"] for x in season_results),
            "empty_starter_slots": sum(x["empty_starter_slots"] for x in season_results),
            "rows_checked": sum(x["rows_checked"] for x in season_results),
            "lineup_point_checks": sum(x["lineup_point_checks"] for x in season_results),
            "player_point_checks": sum(x["player_point_checks"] for x in season_results),
        },
        "benchmark_counts": expected_counts,
        "season_results": season_results,
        "franchise_comparisons": comparisons,
        "errors": errors,
    }
    out = ah.write_isolated_json("validation/historical_replay.json", report)
    print(out)
    print(json.dumps({"status": report["status"], "errors": errors[:10]}, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
