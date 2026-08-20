#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = DATA / "record_book"
SEASONS = ("2022", "2023", "2024", "2025")
OUT.mkdir(parents=True, exist_ok=True)


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def dump(name: str, obj):
    path = OUT / name
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    print(f"Wrote {path.relative_to(ROOT)}")


def season_num(entry):
    league = entry.get("league") or {}
    return str(league.get("season") or entry.get("season") or "")


def user_name(user):
    meta = user.get("metadata") or {}
    return (
        meta.get("team_name")
        or user.get("display_name")
        or user.get("username")
        or str(user.get("user_id"))
    )


def build_identity_maps(history):
    entries = {season_num(e): e for e in history if season_num(e)}
    canonical = {}

    # Latest available name becomes the franchise's display label.
    for season in sorted(entries):
        for user in entries[season].get("users", []):
            uid = str(user.get("user_id") or "")
            if uid:
                canonical[uid] = user_name(user).strip()

    roster_maps = {}
    playoff_starts = {}

    for season, entry in entries.items():
        users = {
            str(u.get("user_id")): u
            for u in entry.get("users", [])
            if u.get("user_id")
        }
        roster_to_uid = {}

        for roster in entry.get("rosters", []):
            rid = str(roster.get("roster_id") or "")
            uid = str(roster.get("owner_id") or "")
            if rid and uid:
                roster_to_uid[rid] = uid
                if uid not in canonical:
                    canonical[uid] = user_name(users.get(uid, {})).strip() or uid

        roster_maps[season] = roster_to_uid
        settings = (entry.get("league") or {}).get("settings") or {}
        playoff_starts[season] = int(settings.get("playoff_week_start") or 15)

    return canonical, roster_maps, playoff_starts


def pair_key(a, b):
    return tuple(sorted((a, b)))


def main():
    history = load_json(DATA / "league_history.json")
    canonical, roster_maps, playoff_starts = build_identity_maps(history)

    games = []
    warnings = []

    for season in SEASONS:
        raw = load_json(DATA / "stats" / "fsffl" / season / "league_matchups_raw.json")
        roster_map = roster_maps.get(season, {})
        playoff_start = playoff_starts.get(season, 15)

        for week_s, rows in raw.items():
            week = int(week_s)
            grouped = defaultdict(list)

            for row in rows:
                matchup_id = row.get("matchup_id")
                if matchup_id is not None:
                    grouped[str(matchup_id)].append(row)

            for matchup_id, pair in grouped.items():
                if len(pair) != 2:
                    warnings.append({
                        "season": season,
                        "week": week,
                        "matchup_id": matchup_id,
                        "rows_found": len(pair),
                    })
                    continue

                a, b = pair
                arid, brid = str(a.get("roster_id")), str(b.get("roster_id"))
                auid, buid = roster_map.get(arid), roster_map.get(brid)

                if not auid or not buid:
                    warnings.append({
                        "season": season,
                        "week": week,
                        "matchup_id": matchup_id,
                        "missing_roster_identity": [arid, brid],
                    })
                    continue

                ap = float(a.get("points") or 0.0)
                bp = float(b.get("points") or 0.0)
                phase = "regular" if week < playoff_start else "postseason"

                if ap > bp:
                    winner, loser, tie = auid, buid, False
                elif bp > ap:
                    winner, loser, tie = buid, auid, False
                else:
                    winner = loser = None
                    tie = True

                games.append({
                    "season": season,
                    "week": week,
                    "phase": phase,
                    "matchup_id": matchup_id,
                    "a_user_id": auid,
                    "a_team": canonical.get(auid, auid),
                    "a_points": round(ap, 2),
                    "b_user_id": buid,
                    "b_team": canonical.get(buid, buid),
                    "b_points": round(bp, 2),
                    "winner_user_id": winner,
                    "loser_user_id": loser,
                    "tie": tie,
                    "margin": round(abs(ap - bp), 2),
                    "combined_points": round(ap + bp, 2),
                })

    games.sort(key=lambda g: (int(g["season"]), g["week"], g["matchup_id"]))
    regular_games = [g for g in games if g["phase"] == "regular"]

    # All-time franchise regular-season records.
    franchise = defaultdict(lambda: {
        "wins": 0, "losses": 0, "ties": 0, "pf": 0.0, "pa": 0.0,
        "games": 0, "weekly_highs": 0, "seasons": set()
    })

    h2h = defaultdict(lambda: {
        "wins": defaultdict(int),
        "ties": 0,
        "games": 0,
        "pf": defaultdict(float),
    })

    by_week = defaultdict(list)

    for g in regular_games:
        by_week[(g["season"], g["week"])].append(g)
        a, b = g["a_user_id"], g["b_user_id"]

        for uid, pf, pa in (
            (a, g["a_points"], g["b_points"]),
            (b, g["b_points"], g["a_points"]),
        ):
            f = franchise[uid]
            f["pf"] += pf
            f["pa"] += pa
            f["games"] += 1
            f["seasons"].add(g["season"])

        key = pair_key(a, b)
        h = h2h[key]
        h["games"] += 1
        h["pf"][a] += g["a_points"]
        h["pf"][b] += g["b_points"]

        if g["tie"]:
            franchise[a]["ties"] += 1
            franchise[b]["ties"] += 1
            h["ties"] += 1
        else:
            franchise[g["winner_user_id"]]["wins"] += 1
            franchise[g["loser_user_id"]]["losses"] += 1
            h["wins"][g["winner_user_id"]] += 1

    # Weekly high-score finishes.
    for _, week_games in by_week.items():
        scores = {}
        for g in week_games:
            scores[g["a_user_id"]] = g["a_points"]
            scores[g["b_user_id"]] = g["b_points"]
        if scores:
            best = max(scores.values())
            for uid, score in scores.items():
                if abs(score - best) < 0.001:
                    franchise[uid]["weekly_highs"] += 1

    franchise_rows = []
    for uid, x in franchise.items():
        gp = x["games"]
        adjusted_wins = x["wins"] + 0.5 * x["ties"]
        franchise_rows.append({
            "user_id": uid,
            "team_name": canonical.get(uid, uid),
            "games": gp,
            "wins": x["wins"],
            "losses": x["losses"],
            "ties": x["ties"],
            "win_pct": round(adjusted_wins / gp, 4) if gp else 0,
            "points_for": round(x["pf"], 2),
            "points_against": round(x["pa"], 2),
            "point_diff": round(x["pf"] - x["pa"], 2),
            "avg_points": round(x["pf"] / gp, 2) if gp else 0,
            "weekly_high_score_finishes": x["weekly_highs"],
            "seasons": sorted(x["seasons"]),
        })

    franchise_rows.sort(
        key=lambda r: (-r["wins"], -r["win_pct"], -r["point_diff"])
    )

    # Head-to-head.
    h2h_rows = []
    for (a, b), h in h2h.items():
        h2h_rows.append({
            "team_a": canonical.get(a, a),
            "team_a_user_id": a,
            "team_a_wins": h["wins"].get(a, 0),
            "team_b": canonical.get(b, b),
            "team_b_user_id": b,
            "team_b_wins": h["wins"].get(b, 0),
            "ties": h["ties"],
            "games": h["games"],
            "team_a_points": round(h["pf"].get(a, 0), 2),
            "team_b_points": round(h["pf"].get(b, 0), 2),
            "point_margin": round(abs(h["pf"].get(a, 0) - h["pf"].get(b, 0)), 2),
        })

    h2h_rows.sort(
        key=lambda r: (
            -r["games"],
            abs(r["team_a_wins"] - r["team_b_wins"]),
            r["point_margin"],
        )
    )

    # Longest W/L streaks across regular seasons.
    sequences = defaultdict(list)
    for g in regular_games:
        for uid in (g["a_user_id"], g["b_user_id"]):
            if g["tie"]:
                result = "T"
            elif uid == g["winner_user_id"]:
                result = "W"
            else:
                result = "L"
            sequences[uid].append((int(g["season"]), g["week"], result))

    streak_rows = []
    for uid, seq in sequences.items():
        seq.sort(key=lambda x: (x[0], x[1]))
        for target, label in (("W", "winning"), ("L", "losing")):
            best = []
            cur = []
            for item in seq:
                if item[2] == target:
                    cur.append(item)
                else:
                    if len(cur) > len(best):
                        best = cur[:]
                    cur = []
            if len(cur) > len(best):
                best = cur[:]

            if best:
                streak_rows.append({
                    "type": label,
                    "team_name": canonical.get(uid, uid),
                    "user_id": uid,
                    "length": len(best),
                    "start_season": str(best[0][0]),
                    "start_week": best[0][1],
                    "end_season": str(best[-1][0]),
                    "end_week": best[-1][1],
                })

    winning_streaks = sorted(
        [x for x in streak_rows if x["type"] == "winning"],
        key=lambda x: (-x["length"], x["team_name"]),
    )
    losing_streaks = sorted(
        [x for x in streak_rows if x["type"] == "losing"],
        key=lambda x: (-x["length"], x["team_name"]),
    )

    # Team-game form for single-game records.
    team_games = []
    for g in regular_games:
        team_games.extend([
            {
                "season": g["season"], "week": g["week"],
                "team_name": g["a_team"], "opponent": g["b_team"],
                "points": g["a_points"], "opp_points": g["b_points"],
                "won": g["winner_user_id"] == g["a_user_id"],
            },
            {
                "season": g["season"], "week": g["week"],
                "team_name": g["b_team"], "opponent": g["a_team"],
                "points": g["b_points"], "opp_points": g["a_points"],
                "won": g["winner_user_id"] == g["b_user_id"],
            },
        ])

    single_game_records = {
        "highest_team_scores": sorted(team_games, key=lambda x: -x["points"])[:25],
        "lowest_team_scores": sorted(team_games, key=lambda x: x["points"])[:25],
        "largest_blows": sorted(regular_games, key=lambda x: -x["margin"])[:25],
        "closest_games": sorted(regular_games, key=lambda x: x["margin"])[:25],
        "highest_combined_scores": sorted(
            regular_games, key=lambda x: -x["combined_points"]
        )[:25],
        "lowest_combined_scores": sorted(
            regular_games, key=lambda x: x["combined_points"]
        )[:25],
        "highest_scoring_losses": sorted(
            [
                x for x in team_games
                if not x["won"] and x["points"] != x["opp_points"]
            ],
            key=lambda x: -x["points"],
        )[:25],
        "lowest_scoring_wins": sorted(
            [x for x in team_games if x["won"]],
            key=lambda x: x["points"],
        )[:25],
    }

    # Single-season franchise records.
    season_team = defaultdict(lambda: {
        "wins": 0, "losses": 0, "ties": 0, "pf": 0.0, "pa": 0.0, "games": 0
    })

    for g in regular_games:
        for uid, pf, pa in (
            (g["a_user_id"], g["a_points"], g["b_points"]),
            (g["b_user_id"], g["b_points"], g["a_points"]),
        ):
            s = season_team[(g["season"], uid)]
            s["pf"] += pf
            s["pa"] += pa
            s["games"] += 1

        if g["tie"]:
            season_team[(g["season"], g["a_user_id"])]["ties"] += 1
            season_team[(g["season"], g["b_user_id"])]["ties"] += 1
        else:
            season_team[(g["season"], g["winner_user_id"])]["wins"] += 1
            season_team[(g["season"], g["loser_user_id"])]["losses"] += 1

    season_rows = []
    for (season, uid), x in season_team.items():
        gp = x["games"]
        season_rows.append({
            "season": season,
            "team_name": canonical.get(uid, uid),
            "user_id": uid,
            "wins": x["wins"],
            "losses": x["losses"],
            "ties": x["ties"],
            "win_pct": round((x["wins"] + 0.5 * x["ties"]) / gp, 4) if gp else 0,
            "points_for": round(x["pf"], 2),
            "points_against": round(x["pa"], 2),
            "point_diff": round(x["pf"] - x["pa"], 2),
        })

    # Luck / efficiency: actual wins vs all-play wins in each regular-season week.
    all_play = defaultdict(lambda: {"actual_wins": 0, "all_play_wins": 0, "all_play_games": 0})
    for (season, week), week_games in by_week.items():
        scores = {}
        actual = {}
        for g in week_games:
            scores[g["a_user_id"]] = g["a_points"]
            scores[g["b_user_id"]] = g["b_points"]
            actual[g["a_user_id"]] = 1 if g["winner_user_id"] == g["a_user_id"] else 0
            actual[g["b_user_id"]] = 1 if g["winner_user_id"] == g["b_user_id"] else 0

        for uid, score in scores.items():
            others = [s for oid, s in scores.items() if oid != uid]
            all_play[(season, uid)]["actual_wins"] += actual.get(uid, 0)
            all_play[(season, uid)]["all_play_wins"] += sum(score > s for s in others)
            all_play[(season, uid)]["all_play_games"] += len(others)

    luck_rows = []
    for (season, uid), x in all_play.items():
        ap_pct = (
            x["all_play_wins"] / x["all_play_games"]
            if x["all_play_games"]
            else 0
        )
        actual_row = next(
            (r for r in season_rows if r["season"] == season and r["user_id"] == uid),
            None,
        )
        actual_pct = actual_row["win_pct"] if actual_row else 0
        luck_rows.append({
            "season": season,
            "team_name": canonical.get(uid, uid),
            "user_id": uid,
            "actual_win_pct": round(actual_pct, 4),
            "all_play_win_pct": round(ap_pct, 4),
            "luck_delta": round(actual_pct - ap_pct, 4),
        })

    summary = {
        "methodology": {
            "seasons": list(SEASONS),
            "head_to_head_and_streaks": "regular season only",
            "postseason_games_retained_separately": True,
            "franchise_identity": (
                "Sleeper owner/user ID; latest available team name used as display label"
            ),
        },
        "counts": {
            "regular_season_games": len(regular_games),
            "postseason_schedule_games": len(
                [g for g in games if g["phase"] == "postseason"]
            ),
            "warnings": len(warnings),
        },
        "franchise_regular_season": franchise_rows,
        "head_to_head_regular_season": h2h_rows,
        "longest_winning_streaks": winning_streaks,
        "longest_losing_streaks": losing_streaks,
        "single_game_records": single_game_records,
        "season_records": season_rows,
        "luck_records": sorted(luck_rows, key=lambda x: -x["luck_delta"]),
        "postseason_schedule_games": [
            g for g in games if g["phase"] == "postseason"
        ],
        "warnings": warnings,
    }

    dump("competition_records.json", summary)


if __name__ == "__main__":
    main()
