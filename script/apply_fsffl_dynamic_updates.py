#!/usr/bin/env python3
"""
FSFFL Simulator dynamic in-season updater.

Key behavior:
- Preseason: does NOT spread transient Sleeper injury tags across the season.
- Regular season: current injury tags affect only the immediate forecast week
  unless an explicit expected-return week is available.
- Current-season actual performance is blended into future means increasingly
  as sample size grows.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

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

TRANSIENT_AVAILABILITY = {
    "probable": 0.95,
    "questionable": 0.72,
    "doubtful": 0.25,
    "out": 0.0,
    "inactive": 0.0,
}

LONG_TERM_STATUSES = {"ir", "pup", "nfi", "suspended"}


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
        if sig not in seen:
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
            lost = as_float(row["fumbles_lost"])
        elif row.get("fum_lost") not in (None, ""):
            lost = as_float(row["fum_lost"])
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


def actual_weight(games: int) -> float:
    if games <= 0:
        return 0.0
    return min(0.80, 0.10 + 0.06 * games)


def injury_status(meta: Dict[str, Any]) -> str:
    return str(meta.get("injury_status") or meta.get("status") or "").strip().lower()


def explicit_return_week(meta: Dict[str, Any]) -> Optional[int]:
    for key in (
        "expected_return_week",
        "injury_return_week",
        "return_week",
    ):
        wk = maybe_week(meta.get(key))
        if wk is not None:
            return wk
    return None


def apply_availability(
    row: Dict[str, Any],
    week: int,
    current_week: int,
    regular_active: bool,
    status: str,
    return_week: Optional[int],
) -> bool:
    if row.get("is_bye"):
        row["active_probability"] = 0.0
        return False

    # Preseason injury labels are often about preseason participation, not
    # Week 1 availability. Do not contaminate the whole regular-season prior.
    if not regular_active:
        return False

    probability = None

    if status in LONG_TERM_STATUSES:
        if return_week is not None:
            probability = 0.0 if week < return_week else 1.0
        elif week == current_week:
            probability = 0.0
    elif status in TRANSIENT_AVAILABILITY and week == current_week:
        probability = TRANSIENT_AVAILABILITY[status]

    if probability is None:
        return False

    before = as_float(row.get("active_probability", 1.0))
    row["active_probability"] = min(before, probability)
    return row["active_probability"] != before


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
            if name and pos:
                by_name_pos[(name, pos)].append(score_row(row, scoring))

    players_with_actual = 0
    players_with_mean_changes = 0
    availability_rows_changed = 0
    unresolved_long_term = 0
    player_audit = {}

    for sid, pp in projections["players"].items():
        name = str(pp.get("name") or "")
        pos = normalize_position(pp.get("position"))
        if not pos:
            continue

        meta = players_meta.get(str(sid), {}) if isinstance(players_meta, dict) else {}
        status = injury_status(meta)
        return_week = explicit_return_week(meta)

        scores = by_name_pos.get((norm_name(name), pos), [])
        ppg = sum(scores) / len(scores) if scores else None
        weight = actual_weight(len(scores)) if regular_active else 0.0

        if scores:
            players_with_actual += 1

        base_ppg = as_float(pp.get("season_baseline_ppg"))
        updated_ppg = base_ppg
        if ppg is not None and weight > 0:
            updated_ppg = (1.0 - weight) * base_ppg + weight * ppg

        changed = False
        availability_changed_for_player = 0

        for week_str, row in (pp.get("weeks") or {}).items():
            week = maybe_week(week_str)
            if week is None:
                continue

            if regular_active and week < current_week:
                continue

            if not row.get("is_bye") and weight > 0:
                old_mean = as_float(row.get("mean"))
                if abs(updated_ppg - old_mean) > 1e-9:
                    old_sd = max(0.1, as_float(row.get("sd")))
                    ratio = updated_ppg / max(0.25, old_mean)
                    row["mean"] = round(updated_ppg, 3)
                    row["median"] = round(max(0.0, as_float(row.get("median")) * ratio), 3)
                    row["sd"] = round(max(0.1, old_sd * max(0.70, min(1.30, ratio))), 3)
                    row["p25"] = round(max(0.0, row["mean"] - 0.67448975 * row["sd"]), 3)
                    row["p75"] = round(max(0.0, row["mean"] + 0.67448975 * row["sd"]), 3)
                    changed = True

            if apply_availability(
                row, week, current_week, regular_active, status, return_week
            ):
                availability_rows_changed += 1
                availability_changed_for_player += 1

        if changed:
            players_with_mean_changes += 1

        if regular_active and status in LONG_TERM_STATUSES and return_week is None:
            unresolved_long_term += 1

        pp["dynamic_update"] = {
            "regular_season_update_active": regular_active,
            "completed_games_sample": len(scores),
            "actual_fsffl_ppg": round(ppg, 3) if ppg is not None else None,
            "actual_weight": round(weight, 3),
            "updated_future_ppg": round(updated_ppg, 3),
            "injury_status": status or "none",
            "expected_return_week": return_week,
            "availability_rows_changed": availability_changed_for_player,
        }
        player_audit[sid] = pp["dynamic_update"]

    projections["dynamic_update_stage"] = {
        "season_type": season_type,
        "current_week": current_week,
        "leg": leg,
        "regular_season_update_active": regular_active,
        "injury_policy": (
            "Preseason transient tags ignored. In season, transient tags affect "
            "only current week; long-term tags use explicit return week when available."
        ),
    }

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
            "players_with_actual_samples": players_with_actual,
            "players_with_future_mean_changes": players_with_mean_changes,
            "availability_rows_changed": availability_rows_changed,
            "unresolved_long_term_injuries": unresolved_long_term,
            "preseason_transient_injury_tags_ignored": not regular_active,
            "player_updates": player_audit,
        },
    )

    print(
        f"Dynamic projection layer complete: regular_active={regular_active}; "
        f"actual_players={players_with_actual}; mean_changes={players_with_mean_changes}; "
        f"availability_rows_changed={availability_rows_changed}"
    )


if __name__ == "__main__":
    main()
