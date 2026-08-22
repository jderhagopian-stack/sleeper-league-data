#!/usr/bin/env python3
"""
FSFFL weekly projection builder - Step 18.

Builds:
data/simulator/<season>/inputs/player_weekly_projections.json
data/simulator/<season>/outputs/weekly_projection_audit.json

Inputs:
- data/league.json
- data/simulator/<season>/sources/preseason_fsffl_points.json
- data/simulator/<season>/sources/selected_preseason_prior.json
- historical NFL weekly normalized files, when available

Method:
- Season projection PPG supplies the weekly scoring mean.
- Bye weeks are set to zero availability.
- Weekly volatility is calibrated from actual historical weekly fantasy scoring
  under FSFFL scoring, using player-specific history when sufficient and
  position-level history otherwise.
- No injuries/role/news adjustments are invented here; those come in the
  dynamic in-season layer.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

DATA = Path("data")
SIM_ROOT = DATA / "simulator"

HISTORY_SEASONS = 3
MIN_PLAYER_GAMES = 8
MIN_POSITION_GAMES = 80

# Conservative distribution floors so low projections do not become
# unrealistically deterministic.
POSITION_SD_FLOOR = {
    "QB": 4.0,
    "RB": 3.5,
    "WR": 3.8,
    "TE": 3.0,
}

# Fallback CVs are used only if historical normalized data cannot calibrate
# a position. The audit reports every fallback.
POSITION_CV_FALLBACK = {
    "QB": 0.34,
    "RB": 0.62,
    "WR": 0.68,
    "TE": 0.72,
}


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")


def as_float(value: Any) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def first(row: Dict[str, Any], *keys: str):
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def normalize_name(value: Optional[str]) -> str:
    import re
    import unicodedata

    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower().replace("’", "'")
    value = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", value)
    return re.sub(r"[^a-z0-9]+", "", value)


def rows_from_payload(payload: Any) -> List[Dict[str, Any]]:
    """
    Accepts a list of rows or common wrappers around a list of rows.
    """
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]

    if isinstance(payload, dict):
        for key in (
            "players", "rows", "data", "weekly", "stats",
            "player_weekly", "records",
        ):
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]

        # Some normalized files are keyed objects.
        if payload and all(isinstance(v, dict) for v in payload.values()):
            return list(payload.values())

    return []


def historical_position(row: Dict[str, Any]) -> Optional[str]:
    value = first(row, "position", "pos", "player_position")
    if not value:
        return None
    value = str(value).upper()
    if value in {"QB", "RB", "WR", "TE"}:
        return value
    return None


def historical_name(row: Dict[str, Any]) -> Optional[str]:
    value = first(
        row,
        "player_name",
        "name",
        "full_name",
        "player_display_name",
    )
    return str(value) if value else None


def historical_player_id(row: Dict[str, Any]) -> Optional[str]:
    value = first(
        row,
        "sleeper_id",
        "player_id",
        "gsis_id",
        "pfr_id",
        "espn_id",
    )
    return str(value) if value else None


def historical_week(row: Dict[str, Any]) -> Optional[int]:
    value = first(row, "week", "week_number")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def score_history_row(row: Dict[str, Any], scoring: Dict[str, Any]) -> float:
    """
    Recalculate offensive fantasy points from common nflverse/nflfastR-style
    stat names using the league's scoring settings.
    """
    stat_aliases = {
        "pass_yd": ("passing_yards", "pass_yd", "pass_yards"),
        "pass_td": ("passing_tds", "pass_td", "passing_touchdowns"),
        "pass_int": ("interceptions", "passing_interceptions", "pass_int"),
        "rush_yd": ("rushing_yards", "rush_yd", "rush_yards"),
        "rush_td": ("rushing_tds", "rush_td", "rushing_touchdowns"),
        "rec": ("receptions", "rec"),
        "rec_yd": ("receiving_yards", "rec_yd", "receiving_yds"),
        "rec_td": ("receiving_tds", "rec_td", "receiving_touchdowns"),
        "fum_lost": ("rushing_fumbles_lost", "receiving_fumbles_lost",
                     "passing_fumbles_lost", "fumbles_lost", "fum_lost"),
        "pass_2pt": ("passing_2pt_conversions", "pass_2pt"),
        "rush_2pt": ("rushing_2pt_conversions", "rush_2pt"),
        "rec_2pt": ("receiving_2pt_conversions", "rec_2pt"),
    }

    total = 0.0
    for fsffl_key, aliases in stat_aliases.items():
        weight = as_float(scoring.get(fsffl_key))
        if not weight:
            continue

        if fsffl_key == "fum_lost":
            # Some feeds split fumbles lost by play type. Sum all available
            # split fields, but prefer an explicit total when present.
            explicit = first(row, "fumbles_lost", "fum_lost")
            if explicit is not None:
                value = as_float(explicit)
            else:
                value = sum(
                    as_float(row.get(k))
                    for k in (
                        "rushing_fumbles_lost",
                        "receiving_fumbles_lost",
                        "passing_fumbles_lost",
                    )
                )
        else:
            value = 0.0
            for alias in aliases:
                if alias in row and row[alias] not in (None, ""):
                    value = as_float(row[alias])
                    break

        total += weight * value

    return total


def robust_cv(values: List[float]) -> Optional[float]:
    """
    Estimate weekly coefficient of variation from games with positive scoring.
    Winsorize at 5th/95th percentiles when sample is large enough.
    """
    vals = [float(x) for x in values if x is not None and x >= 0]
    if len(vals) < 2:
        return None

    vals.sort()
    if len(vals) >= 20:
        lo_i = max(0, int(len(vals) * 0.05))
        hi_i = min(len(vals) - 1, int(len(vals) * 0.95))
        lo = vals[lo_i]
        hi = vals[hi_i]
        vals = [min(max(x, lo), hi) for x in vals]

    mean = statistics.fmean(vals)
    if mean <= 0.25:
        return None

    sd = statistics.stdev(vals)
    cv = sd / mean
    return max(0.15, min(1.35, cv))


def percentile_normal(mean: float, sd: float, z: float) -> float:
    return round(max(0.0, mean + z * sd), 3)


def load_history(
    active_season: int,
    scoring: Dict[str, Any],
):
    """
    Load up to the prior three completed seasons. This keeps development runs
    reasonably fast while still calibrating volatility from a large sample.
    """
    player_scores_by_name = defaultdict(list)
    position_scores = defaultdict(list)
    files_used = []
    files_missing = []
    row_count = 0

    start = active_season - HISTORY_SEASONS
    for season in range(start, active_season):
        path = DATA / "stats" / "nfl" / str(season) / "player_weekly_normalized.json"
        if not path.exists():
            files_missing.append(str(path))
            continue

        payload = load_json(path)
        rows = rows_from_payload(payload)
        files_used.append(str(path))

        for row in rows:
            week = historical_week(row)
            pos = historical_position(row)
            name = historical_name(row)

            if week is None or week < 1 or week > 18:
                continue
            if pos not in {"QB", "RB", "WR", "TE"}:
                continue

            points = score_history_row(row, scoring)

            # Ignore rows with no measurable offensive activity. This prevents
            # inactive roster entries from artificially inflating volatility.
            activity = sum(
                as_float(first(row, *keys))
                for keys in (
                    ("attempts", "passing_attempts", "pass_att"),
                    ("carries", "rushing_attempts", "rush_att"),
                    ("targets",),
                    ("receptions", "rec"),
                )
            )
            if activity <= 0 and points == 0:
                continue

            row_count += 1
            position_scores[pos].append(points)
            if name:
                player_scores_by_name[(normalize_name(name), pos)].append(points)

    return player_scores_by_name, position_scores, {
        "files_used": files_used,
        "files_missing": files_missing,
        "scored_history_rows": row_count,
    }


def main():
    league = load_json(DATA / "league.json")
    if not league:
        raise RuntimeError("Missing data/league.json")

    season = str(league.get("season") or "").strip()
    if not season:
        raise RuntimeError("Missing active season in data/league.json")
    season_int = int(season)

    sim_dir = SIM_ROOT / season
    sources_dir = sim_dir / "sources"
    inputs_dir = sim_dir / "inputs"
    outputs_dir = sim_dir / "outputs"

    baseline = load_json(sources_dir / "preseason_fsffl_points.json")
    prior = load_json(sources_dir / "selected_preseason_prior.json")

    if not baseline or not baseline.get("players"):
        raise RuntimeError("Missing preseason_fsffl_points.json")
    if not prior or not prior.get("players"):
        raise RuntimeError("Missing selected_preseason_prior.json")

    baseline_players = {
        str(k): v for k, v in baseline["players"].items()
    }
    prior_players = {
        str(k): v for k, v in prior["players"].items()
    }

    scoring = league.get("scoring_settings") or {}
    history_by_player, history_by_position, history_audit = load_history(
        season_int,
        scoring,
    )

    position_cv = {}
    position_calibration_source = {}
    for pos in ("QB", "RB", "WR", "TE"):
        values = history_by_position.get(pos, [])
        cv = robust_cv(values) if len(values) >= MIN_POSITION_GAMES else None
        if cv is None:
            cv = POSITION_CV_FALLBACK[pos]
            position_calibration_source[pos] = "fallback"
        else:
            position_calibration_source[pos] = "historical"
        position_cv[pos] = round(cv, 5)

    # Simulator needs playoff weeks too.
    playoff_start = int((league.get("settings") or {}).get("playoff_week_start") or 15)
    last_week = max(17, playoff_start + 2)
    weeks = range(1, last_week + 1)

    out_players = {}
    player_specific_count = 0
    position_fallback_count = 0

    for sid, p in baseline_players.items():
        pos = str(p.get("position") or "").upper()
        if pos not in {"QB", "RB", "WR", "TE"}:
            continue

        name = p.get("player_name") or prior_players.get(sid, {}).get("player_name") or sid
        ppg = as_float(p.get("fsffl_projected_ppg"))
        if ppg <= 0:
            games = max(1.0, as_float(p.get("games_projected")) or 17.0)
            ppg = as_float(p.get("fsffl_projected_points")) / games

        hist = history_by_player.get((normalize_name(name), pos), [])
        player_cv = robust_cv(hist) if len(hist) >= MIN_PLAYER_GAMES else None

        if player_cv is not None:
            # Shrink individual volatility toward the position to reduce
            # overfitting small samples.
            n = len(hist)
            weight = min(0.75, n / 32.0)
            cv = weight * player_cv + (1.0 - weight) * position_cv[pos]
            volatility_source = "player_history_shrunk_to_position"
            player_specific_count += 1
        else:
            cv = position_cv[pos]
            volatility_source = "position_history"
            position_fallback_count += 1

        sd = max(POSITION_SD_FLOOR[pos], ppg * cv)

        bye_week = prior_players.get(sid, {}).get("bye_week")
        try:
            bye_week = int(bye_week) if bye_week is not None else None
        except (TypeError, ValueError):
            bye_week = None

        week_rows = {}
        for week in weeks:
            is_bye = bye_week == week
            mean = 0.0 if is_bye else ppg
            week_sd = 0.1 if is_bye else sd
            active_probability = 0.0 if is_bye else 1.0

            # Median is slightly below mean for positive-skew fantasy scoring.
            median = 0.0 if is_bye else max(0.0, mean - 0.08 * week_sd)

            week_rows[str(week)] = {
                "mean": round(mean, 3),
                "median": round(median, 3),
                "sd": round(week_sd, 3),
                "p25": percentile_normal(mean, week_sd, -0.67448975),
                "p75": percentile_normal(mean, week_sd, 0.67448975),
                "active_probability": active_probability,
                "is_bye": is_bye,
            }

        out_players[sid] = {
            "name": name,
            "position": pos,
            "team": p.get("team"),
            "season_baseline_ppg": round(ppg, 3),
            "bye_week": bye_week,
            "volatility_cv": round(cv, 5),
            "volatility_source": volatility_source,
            "historical_games_for_player_volatility": len(hist),
            "weeks": week_rows,
        }

    baseline_ids = set(baseline_players)
    generated_ids = set(out_players)
    coverage = len(generated_ids & baseline_ids) / max(1, len(baseline_ids))

    write_json(
        inputs_dir / "player_weekly_projections.json",
        {
            "season": season,
            "source": (
                "Razzball season stat projection scored under FSFFL rules; "
                "weekly volatility calibrated from recent NFL weekly history; "
                "bye weeks from selected preseason prior"
            ),
            "model_stage": "preseason_weekly_baseline_v1",
            "players": out_players,
        },
    )

    write_json(
        outputs_dir / "weekly_projection_audit.json",
        {
            "season": season,
            "generated_players": len(out_players),
            "baseline_players": len(baseline_players),
            "baseline_coverage": round(coverage, 5),
            "player_specific_volatility_players": player_specific_count,
            "position_volatility_players": position_fallback_count,
            "position_cv": position_cv,
            "position_calibration_source": position_calibration_source,
            "history": history_audit,
            "weeks_generated": [1, last_week],
            "quality_gate": {
                "minimum_baseline_coverage": 0.95,
                "passed": coverage >= 0.95,
            },
            "important_limitations": [
                "No injury/availability adjustment yet beyond bye weeks.",
                "No opponent-specific weekly matchup adjustment yet.",
                "No in-season usage/performance updating yet.",
                "No same-game/team correlation layer yet.",
            ],
        },
    )

    print(
        f"Weekly projections built: {len(out_players)}/{len(baseline_players)} "
        f"baseline players ({coverage:.1%})."
    )
    print(
        "Volatility calibration: "
        f"{player_specific_count} player-specific, "
        f"{position_fallback_count} position-level."
    )

    if coverage < 0.95:
        raise RuntimeError(
            "Weekly projection coverage below 95%; quality gate failed."
        )


if __name__ == "__main__":
    main()
