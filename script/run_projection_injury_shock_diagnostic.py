#!/usr/bin/env python3
"""Runner that attaches role features, validates appearances, and throttles FFToday."""
from __future__ import annotations

import re
import time
import urllib.error
from collections import defaultdict

import diagnose_external_benchmark_injury_shocks as diagnostic
import run_native_vs_fftoday_historical_benchmark as fftsrc
from run_native_projection_opening_role_by_position_benchmark import attach

_original_native_predictions = diagnostic.native_predictions
_original_fetch_html = fftsrc.fetch_html
_cache = {}
_last_request_at = [0.0]
PARTICIPATION_URL = "https://github.com/nflverse/nflverse-data/releases/download/pbp_participation/pbp_participation_{season}.csv"


def native_predictions_with_roles(rows, target_season, position):
    key = id(rows)
    if key not in _cache:
        seasons = sorted({int(r["season"]) for r in rows})
        _cache[key] = attach(rows, seasons)[0]
    return _original_native_predictions(_cache[key], target_season, position)


def offensive_game_appearances(season: int):
    """Count distinct games in which a player appeared on an offensive play.

    nflverse participation explicitly lists offense_players by GSIS id on every
    tracked play. This is a better post-hoc appearance measure than requiring a
    box-score stat. It is never used as a preseason feature.
    """
    rows = diagnostic.fetch_csv_url(PARTICIPATION_URL.format(season=season))
    games = defaultdict(set)
    for r in rows:
        game_id = str(r.get("nflverse_game_id") or "").strip()
        if not game_id:
            continue
        # nflverse game id is season_week_away_home. Regular-season weeks are
        # represented numerically; participation files used here are regular +
        # postseason, so restrict the diagnostic to weeks 1-18.
        parts = game_id.split("_")
        if len(parts) < 2:
            continue
        try:
            week = int(parts[1])
        except ValueError:
            continue
        if not (1 <= week <= 18):
            continue
        offense = str(r.get("offense_players") or "")
        for pid in set(re.findall(r"00-\d+", offense)):
            games[pid].add(game_id)
    return {pid: len(gs) for pid, gs in games.items()}


def corrected_classify_player_seasons(rows, seasons):
    result = {}
    for season in seasons:
        roles = diagnostic.opening_map(season)
        injuries = diagnostic.injury_map(season)
        weekly = diagnostic.weekly_opportunity(season)
        participation_games = offensive_game_appearances(season)
        season_rows = [r for r in rows if int(r["season"]) == season]
        actual_by_pid = {str(r["player_id"]): r for r in season_rows}

        higher_by_team_pos = defaultdict(list)
        for (pid, pos), role in roles.items():
            higher_by_team_pos[(role["team"], pos)].append((pid, float(role["rank"])))

        for pid, ar in actual_by_pid.items():
            pos = str(ar.get("position") or "").upper()
            if pos not in diagnostic.POSITIONS:
                continue
            role = roles.get((pid, pos))
            # Primary: actual offensive play participation. Conservative fallback
            # to weekly stat appearances only when participation lacks the player.
            games = float(participation_games.get(pid, len(weekly.get(pid, {}))))
            game_count_source = "play_participation" if pid in participation_games else "weekly_stat_fallback"
            inj = injuries.get(pid, {"out_doubtful_weeks": set(), "injury_report_weeks": set()})
            out_weeks = set(inj["out_doubtful_weeks"])
            self_injury = bool(games <= 14.0 and len(out_weeks) >= 1)

            teammate_shock = False
            higher_injured = []
            injury_opp_weeks = set()
            if role and float(role["rank"]) > 1.0:
                for other_pid, other_rank in higher_by_team_pos.get((role["team"], pos), []):
                    if other_pid == pid or other_rank >= float(role["rank"]):
                        continue
                    weeks = set(injuries.get(other_pid, {}).get("out_doubtful_weeks", set()))
                    if len(weeks) >= 2:
                        higher_injured.append(other_pid)
                        injury_opp_weeks |= weeks
                if injury_opp_weeks:
                    pweek = weekly.get(pid, {})
                    in_vals = [v for w, v in pweek.items() if w in injury_opp_weeks]
                    out_vals = [v for w, v in pweek.items() if w not in injury_opp_weeks]
                    if in_vals:
                        in_avg = sum(in_vals) / len(in_vals)
                        out_avg = sum(out_vals) / len(out_vals) if out_vals else 0.0
                        teammate_shock = bool(
                            in_avg >= max(5.0, out_avg * 1.25)
                            and in_avg - out_avg >= 2.0
                        )

            result[(season, pid)] = {
                "position": pos,
                "games": games,
                "game_count_source": game_count_source,
                "opening_team": role["team"] if role else "",
                "opening_depth_rank": float(role["rank"]) if role else None,
                "self_injury": self_injury,
                "self_out_doubtful_weeks": sorted(out_weeks),
                "teammate_injury_opportunity": teammate_shock,
                "higher_ranked_injured_players": sorted(higher_injured),
                "higher_ranked_out_doubtful_weeks": sorted(injury_opp_weeks),
                "stable_no_shock": not self_injury and not teammate_shock,
            }
    return result


def throttled_fetch_html(url: str) -> str:
    for attempt in range(6):
        elapsed = time.monotonic() - _last_request_at[0]
        if elapsed < 1.25:
            time.sleep(1.25 - elapsed)
        try:
            out = _original_fetch_html(url)
            _last_request_at[0] = time.monotonic()
            return out
        except urllib.error.HTTPError as exc:
            _last_request_at[0] = time.monotonic()
            if exc.code not in {403, 429} or attempt == 5:
                raise
            time.sleep(4.0 * (attempt + 1))
    raise RuntimeError("unreachable FFToday retry state")


fftsrc.fetch_html = throttled_fetch_html
diagnostic.native_predictions = native_predictions_with_roles
diagnostic.classify_player_seasons = corrected_classify_player_seasons

if __name__ == "__main__":
    diagnostic.main()
