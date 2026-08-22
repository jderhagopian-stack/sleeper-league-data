#!/usr/bin/env python3
"""
Build active-season NFL schedule + matchup adjustments for FSFFL Simulator 1.0.

Schedule source:
  nflverse/nfldata games.csv

Outputs:
  data/simulator/<season>/sources/nfl_schedule.json
  data/simulator/<season>/sources/opponent_adjustments.json
  data/simulator/<season>/outputs/opponent_adjustment_audit.json

Method:
- Pull the known NFL schedule for the active league season.
- Map each offense team to its weekly opponent/home-away status.
- Estimate defense-vs-position strength from exact-FSFFL historical weekly
  fantasy scoring already stored in the repo.
- Recency weight prior seasons and incorporate current-season completed games
  automatically when available.
- Shrink/cap matchup multipliers to avoid overfitting.
"""

from __future__ import annotations

import csv
import io
import json
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from build_fsffl_weekly_projections import (
    extract_weekly_rows,
    historical_position,
    historical_week,
    score_history_row,
    has_activity,
)

DATA = Path("data")
SIM_ROOT = DATA / "simulator"

GAMES_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
POSITIONS = ("QB", "RB", "WR", "TE")

TEAM_ALIASES = {
    "JAC": "JAX",
    "LA": "LAR",
    "STL": "LAR",
    "SD": "LAC",
    "OAK": "LV",
}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")


def norm_team(team: Any) -> Optional[str]:
    if team in (None, ""):
        return None
    t = str(team).upper().strip()
    return TEAM_ALIASES.get(t, t)


def as_int(value: Any) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def fetch_games() -> List[Dict[str, str]]:
    req = urllib.request.Request(
        GAMES_URL,
        headers={"User-Agent": "FSFFL-Season-Simulator/1.0"},
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        text = response.read().decode("utf-8")
    return list(csv.DictReader(io.StringIO(text)))


def game_type_regular(row: Dict[str, Any]) -> bool:
    value = str(row.get("game_type") or row.get("season_type") or "").upper()
    return value in {"REG", "REGULAR"}


def build_schedule_maps(rows: List[Dict[str, str]], season: int):
    games = []
    lookup = {}

    for row in rows:
        if as_int(row.get("season")) != season:
            continue
        if not game_type_regular(row):
            continue

        week = as_int(row.get("week"))
        home = norm_team(row.get("home_team"))
        away = norm_team(row.get("away_team"))
        if week is None or not home or not away:
            continue

        game = {
            "game_id": row.get("game_id"),
            "week": week,
            "home_team": home,
            "away_team": away,
            "gameday": row.get("gameday"),
            "weekday": row.get("weekday"),
            "gametime": row.get("gametime"),
            "stadium": row.get("stadium"),
            "roof": row.get("roof"),
            "surface": row.get("surface"),
        }
        games.append(game)

        lookup[(season, week, home)] = {
            "opponent": away,
            "home": True,
        }
        lookup[(season, week, away)] = {
            "opponent": home,
            "home": False,
        }

    return games, lookup


def all_schedule_lookup(rows: List[Dict[str, str]]):
    lookup = {}
    for row in rows:
        if not game_type_regular(row):
            continue
        season = as_int(row.get("season"))
        week = as_int(row.get("week"))
        home = norm_team(row.get("home_team"))
        away = norm_team(row.get("away_team"))
        if season is None or week is None or not home or not away:
            continue
        lookup[(season, week, home)] = away
        lookup[(season, week, away)] = home
    return lookup


def season_weight(active_season: int, season: int) -> float:
    delta = active_season - season
    if delta <= 0:
        return 1.50
    if delta == 1:
        return 1.00
    if delta == 2:
        return 0.65
    if delta == 3:
        return 0.40
    return 0.0


def extract_nfl_team(row: Dict[str, Any]) -> Optional[str]:
    return norm_team(
        row.get("nfl_team")
        or row.get("team")
        or row.get("recent_team")
    )


def completed_current_week_limit(nfl_state: Dict[str, Any], active_season: int) -> int:
    if int(nfl_state.get("season") or 0) != active_season:
        return 0
    season_type = str(nfl_state.get("season_type") or "").lower()
    leg = int(nfl_state.get("leg") or 0)
    week = int(nfl_state.get("week") or 1)
    if season_type not in {"regular", "post"} or leg <= 0:
        return 0
    return max(0, week - 1)


def build_defense_strength(
    active_season: int,
    scoring: Dict[str, Any],
    schedule_lookup: Dict[Tuple[int, int, str], str],
    nfl_state: Dict[str, Any],
):
    # Aggregate team fantasy points by offense team/position/game first.
    allowed_samples = defaultdict(list)
    league_samples = defaultdict(list)
    files_used = []
    scored_rows = 0

    current_completed_limit = completed_current_week_limit(
        nfl_state, active_season
    )

    seasons = [active_season - 3, active_season - 2, active_season - 1]
    if current_completed_limit > 0:
        seasons.append(active_season)

    for season in seasons:
        weight = season_weight(active_season, season)
        if weight <= 0:
            continue

        path = DATA / "stats" / "nfl" / str(season) / "player_weekly_normalized.json"
        if not path.exists():
            continue

        payload = load_json(path)
        rows, _ = extract_weekly_rows(payload)
        files_used.append(str(path))

        offense_game_points = defaultdict(float)

        for row in rows:
            week = historical_week(row)
            pos = historical_position(row)
            team = extract_nfl_team(row)

            if week is None or pos not in POSITIONS or not team:
                continue
            if season == active_season and week > current_completed_limit:
                continue

            points, method = score_history_row(row, scoring)
            if method == "unscorable" or not has_activity(row, points):
                continue

            offense_game_points[(week, team, pos)] += float(points)
            scored_rows += 1

        for (week, offense_team, pos), points in offense_game_points.items():
            opponent = schedule_lookup.get((season, week, offense_team))
            if not opponent:
                continue
            allowed_samples[(opponent, pos)].append((points, weight))
            league_samples[pos].append((points, weight))

    league_means = {}
    for pos in POSITIONS:
        samples = league_samples[pos]
        denom = sum(w for _, w in samples)
        league_means[pos] = (
            sum(v * w for v, w in samples) / denom if denom else None
        )

    defense = {}
    for team in sorted({team for team, _ in allowed_samples}):
        defense[team] = {}
        for pos in POSITIONS:
            samples = allowed_samples.get((team, pos), [])
            denom = sum(w for _, w in samples)
            mean_allowed = (
                sum(v * w for v, w in samples) / denom if denom else None
            )
            league_mean = league_means.get(pos)

            if not mean_allowed or not league_mean:
                multiplier = 1.0
                raw_ratio = 1.0
            else:
                raw_ratio = mean_allowed / league_mean

                # Effective sample shrinkage toward neutral.
                effective_n = denom
                shrink = effective_n / (effective_n + 12.0)
                multiplier = 1.0 + (raw_ratio - 1.0) * shrink

            multiplier = max(0.88, min(1.12, multiplier))

            defense[team][pos] = {
                "multiplier": round(multiplier, 4),
                "raw_ratio": round(raw_ratio, 4),
                "weighted_sample": round(denom, 2),
                "mean_points_allowed": (
                    round(mean_allowed, 3) if mean_allowed is not None else None
                ),
                "league_mean": (
                    round(league_mean, 3) if league_mean is not None else None
                ),
            }

    return defense, {
        "history_files_used": files_used,
        "scored_player_rows": scored_rows,
        "current_season_completed_week_limit": current_completed_limit,
        "league_position_team_week_means": {
            pos: round(v, 3) if v is not None else None
            for pos, v in league_means.items()
        },
    }


def main():
    league = load_json(DATA / "league.json")
    nfl_state = load_json(DATA / "nfl_state.json", {})
    if not league:
        raise RuntimeError("Missing data/league.json")

    season = int(league.get("season"))
    season_dir = SIM_ROOT / str(season)
    sources = season_dir / "sources"
    outputs = season_dir / "outputs"

    rows = fetch_games()
    current_games, current_lookup = build_schedule_maps(rows, season)
    if len(current_games) < 270:
        raise RuntimeError(
            f"NFL schedule quality gate failed: only {len(current_games)} "
            f"regular-season games found for {season}."
        )

    schedule_lookup = all_schedule_lookup(rows)
    defense, defense_audit = build_defense_strength(
        active_season=season,
        scoring=league.get("scoring_settings") or {},
        schedule_lookup=schedule_lookup,
        nfl_state=nfl_state,
    )

    teams_on_schedule = sorted(
        {
            game["home_team"]
            for game in current_games
        }
        | {
            game["away_team"]
            for game in current_games
        }
    )

    week_team = {}
    for game in current_games:
        week = str(game["week"])
        week_team.setdefault(week, {})
        week_team[week][game["home_team"]] = {
            "opponent": game["away_team"],
            "home": True,
        }
        week_team[week][game["away_team"]] = {
            "opponent": game["home_team"],
            "home": False,
        }

    # Pre-production engine expects each offense team to already have the
    # multiplier associated with its opponent for that week.
    adjustment_weeks = {}
    mapped_matchups = 0

    for week, teams in week_team.items():
        adjustment_weeks[week] = {}
        for offense_team, info in teams.items():
            opponent = info["opponent"]
            d = defense.get(opponent, {})
            adjustment_weeks[week][offense_team] = {
                pos: round(
                    float((d.get(pos) or {}).get("multiplier", 1.0)),
                    4,
                )
                for pos in POSITIONS
            }
            mapped_matchups += 1

    schedule_payload = {
        "generated_at_utc": now_utc(),
        "season": season,
        "source": GAMES_URL,
        "regular_season_game_count": len(current_games),
        "teams": teams_on_schedule,
        "games": sorted(
            current_games,
            key=lambda x: (x["week"], x["gameday"] or "", x["game_id"] or ""),
        ),
        "team_week_lookup": week_team,
    }

    adjustments_payload = {
        "generated_at_utc": now_utc(),
        "season": season,
        "method": (
            "Opponent defense-vs-position multiplier from exact-FSFFL historical "
            "team-week fantasy points allowed; recency weighted, shrunk to neutral, "
            "and capped to [0.88, 1.12]. Current-season completed games are included "
            "automatically once available."
        ),
        "weeks": adjustment_weeks,
        "defense_strength": defense,
    }

    checks = [
        {
            "code": "NFL_REGULAR_SEASON_GAMES",
            "passed": len(current_games) >= 270,
            "value": len(current_games),
            "minimum": 270,
        },
        {
            "code": "NFL_TEAMS",
            "passed": len(teams_on_schedule) == 32,
            "value": len(teams_on_schedule),
            "target": 32,
        },
        {
            "code": "MATCHUP_TEAM_WEEKS",
            "passed": mapped_matchups >= 540,
            "value": mapped_matchups,
            "minimum": 540,
        },
        {
            "code": "DEFENSE_POSITION_CALIBRATION",
            "passed": all(
                any(
                    (defense.get(team, {}).get(pos) or {}).get("weighted_sample", 0) > 0
                    for team in defense
                )
                for pos in POSITIONS
            ),
            "positions": list(POSITIONS),
        },
    ]

    audit = {
        "generated_at_utc": now_utc(),
        "season": season,
        "source": GAMES_URL,
        "passed": all(c["passed"] for c in checks),
        "checks": checks,
        **defense_audit,
    }

    write_json(sources / "nfl_schedule.json", schedule_payload)
    write_json(sources / "opponent_adjustments.json", adjustments_payload)
    write_json(outputs / "opponent_adjustment_audit.json", audit)

    if not audit["passed"]:
        raise SystemExit(
            "NFL schedule/opponent adjustment validation failed. "
            "See opponent_adjustment_audit.json."
        )

    print(
        f"NFL schedule/matchup layer ready for {season}: "
        f"{len(current_games)} games, {len(teams_on_schedule)} teams, "
        f"{mapped_matchups} team-weeks."
    )


if __name__ == "__main__":
    main()
