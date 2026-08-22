#!/usr/bin/env python3
"""
FSFFL Simulator - dynamic in-season projection updater.

This layer sits AFTER build_fsffl_weekly_projections.py and BEFORE the season
simulator. It is season-agnostic and intentionally does nothing to preseason
means when the NFL state says preseason.

Inputs:
- data/nfl_state.json
- data/players.json
- data/league.json
- data/stats/nfl/<season>/player_weekly_normalized.json (when regular season data exists)
- data/simulator/<season>/inputs/player_weekly_projections.json

Updates future weeks using:
- actual current-season fantasy scoring under exact FSFFL rules
- increasing in-season weight as sample size grows
- Sleeper injury/status availability
- actual completed weeks are not projected forward as if unknown

Outputs:
- overwrites player_weekly_projections.json with dynamic future-week means
- data/simulator/<season>/outputs/dynamic_projection_audit.json
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

DATA = Path("data")
SIM_ROOT = DATA / "simulator"

NAME_KEYS = ("player_name", "name", "full_name", "player_display_name", "player")
POSITION_KEYS = ("position", "pos", "player_position")
WEEK_KEYS = ("week", "week_number", "game_week")
ID_KEYS = ("sleeper_id", "player_id", "gsis_id")

STAT_ALIASES = {
    "pass_yd": ("passing_yards", "pass_yd", "pass_yards", "pass_yds"),
    "pass_td": ("passing_tds", "passing_td", "pass_td", "passing_touchdowns"),
    "pass_int": ("interceptions", "passing_interceptions", "pass_int", "passing_ints"),
    "rush_yd": ("rushing_yards", "rush_yd", "rush_yards", "rush_yds"),
    "rush_td": ("rushing_tds", "rushing_td", "rush_td", "rushing_touchdowns"),
    "rec": ("receptions", "rec", "receiving_receptions"),
    "rec_yd": ("receiving_yards", "rec_yd", "receiving_yds", "rec_yards"),
    "rec_td": ("receiving_tds", "receiving_td", "rec_td", "receiving_touchdowns"),
    "pass_2pt": ("passing_2pt_conversions", "pass_2pt"),
    "rush_2pt": ("rushing_2pt_conversions", "rush_2pt"),
    "rec_2pt": ("receiving_2pt_conversions", "rec_2pt"),
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
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def first(row: Dict[str, Any], keys: Iterable[str]):
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def norm_name(value: Optional[str]) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower().replace("’", "'")
    value = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", value)
    return re.sub(r"[^a-z0-9]+", "", value)


def maybe_week(value: Any) -> Optional[int]:
    try:
        w = int(value)
        return w if 1 <= w <= 18 else None
    except (TypeError, ValueError):
        return None


def normalize_position(value: Any) -> Optional[str]:
    text = str(value or "").upper().strip()
    return text if text in {"QB", "RB", "WR", "TE"} else None


def flatten_stats(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    for key in ("stats", "statistics", "player_stats"):
        nested = row.get(key)
        if isinstance(nested, dict):
            for k, v in nested.items():
                out.setdefault(k, v)
    return out


def row_has_stats(row: Dict[str, Any]) -> bool:
    keys = set(row)
    return any(alias in keys for aliases in STAT_ALIASES.values() for alias in aliases)


def extract_weekly_rows(payload: Any) -> List[Dict[str, Any]]:
    rows = []

    def walk(node: Any, ctx: Dict[str, Any], parent_key: Optional[str] = None):
        if isinstance(node, list):
            for item in node:
                walk(item, dict(ctx), parent_key)
            return
        if not isinstance(node, dict):
            return

        local = dict(ctx)

        if parent_key:
            wk = maybe_week(parent_key)
            if wk is not None:
                local["week"] = wk

        for keys, target in (
            (NAME_KEYS, "player_name"),
            (POSITION_KEYS, "position"),
            (ID_KEYS, "player_id"),
            (WEEK_KEYS, "week"),
        ):
            value = first(node, keys)
            if value is not None:
                local[target] = value

        flat = flatten_stats(node)
        if row_has_stats(flat):
            candidate = dict(local)
            candidate.update(flat)
            wk = maybe_week(candidate.get("week"))
            pos = normalize_position(candidate.get("position"))
            if wk is not None and pos:
                candidate["week"] = wk
                candidate["position"] = pos
                rows.append(candidate)

        for key, value in node.items():
            if isinstance(value, (dict, list)):
                walk(value, dict(local), str(key))

    walk(payload, {})

    deduped = []
    seen = set()
    for row in rows:
        sig = (
            str(row.get("player_id") or ""),
            norm_name(str(row.get("player_name") or "")),
            row.get("position"),
            row.get("week"),
        )
        if sig in seen:
            continue
        seen.add(sig)
        deduped.append(row)
    return deduped


def alias_value(row: Dict[str, Any], aliases: Iterable[str]) -> float:
    for key in aliases:
        if key in row and row[key] not in (None, ""):
            return as_float(row[key])
    return 0.0


def score_row(row: Dict[str, Any], scoring: Dict[str, Any]) -> float:
    total = 0.0
    for fs_key, aliases in STAT_ALIASES.items():
        total += as_float(scoring.get(fs_key)) * alias_value(row, aliases)

    fum_weight = as_float(scoring.get("fum_lost"))
    if fum_weight:
        if row.get("fumbles_lost") not in (None, ""):
            lost = as_float(row.get("fumbles_lost"))
        elif row.get("fum_lost") not in (None, ""):
            lost = as_float(row.get("fum_lost"))
        else:
            lost = sum(
                as_float(row.get(k))
                for k in (
                    "rushing_fumbles_lost",
                    "receiving_fumbles_lost",
                    "passing_fumbles_lost",
                )
            )
        total += fum_weight * lost
    return total


def active_probability(player: Dict[str, Any]) -> Tuple[float, str]:
    status = str(
        player.get("injury_status")
        or player.get("status")
        or ""
    ).strip().lower()

    if status in {"out", "ir", "pup", "nfi", "suspended", "inactive"}:
        return 0.0, status or "out"
    if status in {"doubtful"}:
        return 0.25, status
    if status in {"questionable"}:
        return 0.72, status
    if status in {"probable"}:
        return 0.95, status
    return 1.0, status or "no_injury_flag"


def current_season_weight(completed_games: int) -> float:
    # Preseason prior dominates early; actual season becomes dominant later.
    if completed_games <= 0:
        return 0.0
    return min(0.80, 0.10 + 0.06 * completed_games)


def main():
    league = load_json(DATA / "league.json")
    nfl_state = load_json(DATA / "nfl_state.json", {})
    players_meta = load_json(DATA / "players.json", {})
    if not league:
        raise RuntimeError("Missing data/league.json")

    season = str(league.get("season"))
    sim_dir = SIM_ROOT / season
    input_path = sim_dir / "inputs" / "player_weekly_projections.json"
    audit_path = sim_dir / "outputs" / "dynamic_projection_audit.json"
    projections = load_json(input_path)
    if not projections or not projections.get("players"):
        raise RuntimeError("Missing weekly projection input")

    season_type = str(nfl_state.get("season_type") or "").lower()
    current_week = maybe_week(nfl_state.get("week")) or 1
    leg = int(nfl_state.get("leg") or 0)
    regular_active = season_type in {"regular", "post"} and leg >= 1

    scoring = league.get("scoring_settings") or {}
    current_path = DATA / "stats" / "nfl" / season / "player_weekly_normalized.json"

    by_name_pos = defaultdict(list)
    current_rows = []
    if regular_active and current_path.exists():
        current_rows = extract_weekly_rows(load_json(current_path))
        for row in current_rows:
            wk = maybe_week(row.get("week"))
            if wk is None or wk >= current_week:
                continue
            name = norm_name(str(row.get("player_name") or ""))
            pos = normalize_position(row.get("position"))
            if not name or not pos:
                continue
            by_name_pos[(name, pos)].append(score_row(row, scoring))

    changed_means = 0
    injury_adjusted = 0
    actual_players = 0
    future_week_rows = 0
    player_audit = {}

    for sid, player_projection in projections["players"].items():
        name = str(player_projection.get("name") or "")
        pos = normalize_position(player_projection.get("position"))
        if not pos:
            continue

        meta = players_meta.get(str(sid), {}) if isinstance(players_meta, dict) else {}
        availability, injury_label = active_probability(meta)

        scores = by_name_pos.get((norm_name(name), pos), [])
        actual_ppg = sum(scores) / len(scores) if scores else None
        weight = current_season_weight(len(scores)) if regular_active else 0.0
        if scores:
            actual_players += 1

        base_ppg = as_float(player_projection.get("season_baseline_ppg"))
        updated_ppg = base_ppg
        if actual_ppg is not None and weight > 0:
            updated_ppg = (1.0 - weight) * base_ppg + weight * actual_ppg

        player_changed = False
        for week_str, row in (player_projection.get("weeks") or {}).items():
            week = maybe_week(week_str)
            if week is None:
                continue

            # Past/current weeks are not future forecasts. Leave their means
            # untouched here; the simulator's midseason state layer will later
            # lock actual completed FSFFL matchup results.
            if regular_active and week < current_week:
                continue

            if row.get("is_bye"):
                row["active_probability"] = 0.0
                continue

            future_week_rows += 1
            old_mean = as_float(row.get("mean"))
            old_sd = max(0.1, as_float(row.get("sd")))

            if weight > 0 and abs(updated_ppg - old_mean) > 1e-9:
                ratio = updated_ppg / max(0.25, old_mean)
                row["mean"] = round(updated_ppg, 3)
                row["median"] = round(max(0.0, as_float(row.get("median")) * ratio), 3)
                row["sd"] = round(max(0.1, old_sd * max(0.70, min(1.30, ratio))), 3)
                row["p25"] = round(max(0.0, row["mean"] - 0.67448975 * row["sd"]), 3)
                row["p75"] = round(max(0.0, row["mean"] + 0.67448975 * row["sd"]), 3)
                player_changed = True

            # Injury availability is applied even in preseason.
            if availability < as_float(row.get("active_probability", 1.0)):
                row["active_probability"] = availability
                injury_adjusted += 1

        if player_changed:
            changed_means += 1

        player_projection["dynamic_update"] = {
            "regular_season_update_active": regular_active,
            "completed_games_sample": len(scores),
            "actual_fsffl_ppg": round(actual_ppg, 3) if actual_ppg is not None else None,
            "actual_weight": round(weight, 3),
            "updated_future_ppg": round(updated_ppg, 3),
            "availability_probability": availability,
            "injury_label": injury_label,
        }

        player_audit[sid] = player_projection["dynamic_update"]

    projections["dynamic_update_stage"] = {
        "season_type": season_type,
        "current_week": current_week,
        "leg": leg,
        "regular_season_update_active": regular_active,
    }
    projections["source"] = (
        str(projections.get("source") or "")
        + "; dynamic current-season/injury update layer"
    )

    write_json(input_path, projections)
    write_json(
        audit_path,
        {
            "season": season,
            "season_type": season_type,
            "current_week": current_week,
            "leg": leg,
            "regular_season_update_active": regular_active,
            "current_stats_file": str(current_path),
            "current_weekly_rows_parsed": len(current_rows),
            "players_with_actual_samples": actual_players,
            "players_with_future_mean_changes": changed_means,
            "future_week_rows_checked": future_week_rows,
            "injury_adjusted_week_rows": injury_adjusted,
            "preseason_behavior": (
                "Current-season performance weighting disabled; injury flags only."
                if not regular_active else None
            ),
            "player_updates": player_audit,
        },
    )

    print(
        f"Dynamic projection layer complete for {season}. "
        f"regular_active={regular_active}; "
        f"actual_players={actual_players}; changed_means={changed_means}; "
        f"injury_rows={injury_adjusted}"
    )


if __name__ == "__main__":
    main()
