#!/usr/bin/env python3
"""
FSFFL weekly projection builder - Step 19.

Fixes historical volatility calibration by recursively reading the actual
historical normalized JSON structure instead of assuming a flat row list.

Outputs:
- data/simulator/<season>/inputs/player_weekly_projections.json
- data/simulator/<season>/outputs/weekly_projection_audit.json

Hard gates:
- >=95% baseline projection coverage
- >=1,000 usable historical weekly rows
- historical volatility calibration for QB/RB/WR/TE
"""

from __future__ import annotations

import json
import re
import statistics
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

DATA = Path("data")
SIM_ROOT = DATA / "simulator"

HISTORY_SEASONS = 3
MIN_PLAYER_GAMES = 8
MIN_POSITION_GAMES = 80
MIN_HISTORY_ROWS = 1000

POSITION_SD_FLOOR = {
    "QB": 4.0,
    "RB": 3.5,
    "WR": 3.8,
    "TE": 3.0,
}

# Retained only as an emergency diagnostic fallback. The hard historical gate
# below prevents production success when these are needed for all positions.
POSITION_CV_FALLBACK = {
    "QB": 0.34,
    "RB": 0.62,
    "WR": 0.68,
    "TE": 0.72,
}

NAME_KEYS = (
    "player_name", "name", "full_name", "player_display_name",
    "player", "display_name",
)
POSITION_KEYS = ("position", "pos", "player_position")
WEEK_KEYS = ("week", "week_number", "game_week")
ID_KEYS = (
    "sleeper_id", "player_id", "gsis_id", "pfr_id", "espn_id",
)

STAT_ALIASES = {
    "pass_yd": (
        "passing_yards", "pass_yd", "pass_yards", "pass_yds",
    ),
    "pass_td": (
        "passing_tds", "passing_td", "pass_td", "passing_touchdowns",
    ),
    "pass_int": (
        "interceptions", "passing_interceptions", "pass_int",
        "passing_ints",
    ),
    "rush_yd": (
        "rushing_yards", "rush_yd", "rush_yards", "rush_yds",
    ),
    "rush_td": (
        "rushing_tds", "rushing_td", "rush_td", "rushing_touchdowns",
    ),
    "rec": (
        "receptions", "rec", "receiving_receptions",
    ),
    "rec_yd": (
        "receiving_yards", "rec_yd", "receiving_yds", "rec_yards",
    ),
    "rec_td": (
        "receiving_tds", "receiving_td", "rec_td",
        "receiving_touchdowns",
    ),
    "pass_2pt": (
        "passing_2pt_conversions", "pass_2pt",
    ),
    "rush_2pt": (
        "rushing_2pt_conversions", "rush_2pt",
    ),
    "rec_2pt": (
        "receiving_2pt_conversions", "rec_2pt",
    ),
}

ACTIVITY_ALIASES = (
    "attempts", "passing_attempts", "pass_att",
    "carries", "rushing_attempts", "rush_att",
    "targets", "receptions", "rec",
)

KNOWN_POINT_KEYS = (
    "fantasy_points_half_ppr",
    "fantasy_points_half",
    "half_ppr",
    "half_ppr_points",
    "fantasy_points_ppr_half",
)


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
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower().replace("’", "'")
    value = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", value)
    return re.sub(r"[^a-z0-9]+", "", value)


def normalize_position(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).upper().strip()
    if text in {"QB", "RB", "WR", "TE"}:
        return text
    return None


def maybe_week(value: Any) -> Optional[int]:
    try:
        week = int(value)
        if 1 <= week <= 18:
            return week
    except (TypeError, ValueError):
        pass
    return None


def row_has_stats(row: Dict[str, Any]) -> bool:
    keys = set(row)
    stat_names = set()
    for aliases in STAT_ALIASES.values():
        stat_names.update(aliases)
    stat_names.update(KNOWN_POINT_KEYS)
    return bool(keys & stat_names)


def flatten_embedded_stats(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    for key in ("stats", "statistics", "player_stats"):
        nested = row.get(key)
        if isinstance(nested, dict):
            for k, v in nested.items():
                out.setdefault(k, v)
    return out


def extract_weekly_rows(payload: Any) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Recursively walk arbitrary JSON.

    Handles, among other shapes:
    - flat list of weekly rows
    - {"data": [...]}
    - {"1": [...], "2": [...]} where the week is encoded in the key
    - player-keyed objects with nested "weeks"
    - rows with a nested "stats" dictionary

    Context such as player name/position/id and week is inherited downward.
    """
    rows: List[Dict[str, Any]] = []
    dict_nodes = 0
    list_nodes = 0
    candidate_nodes = 0
    observed_keys = defaultdict(int)

    def walk(node: Any, context: Dict[str, Any], parent_key: Optional[str] = None):
        nonlocal dict_nodes, list_nodes, candidate_nodes

        if isinstance(node, list):
            list_nodes += 1
            for item in node:
                walk(item, dict(context), parent_key)
            return

        if not isinstance(node, dict):
            return

        dict_nodes += 1
        local = dict(context)

        # Numeric dictionary keys frequently encode week.
        if parent_key is not None:
            wk = maybe_week(parent_key)
            if wk is not None and "week" not in local:
                local["week"] = wk

        # Pull metadata from this node into inherited context.
        for key in NAME_KEYS:
            if key in node and node[key] not in (None, ""):
                local["player_name"] = node[key]
                break

        for key in POSITION_KEYS:
            if key in node and node[key] not in (None, ""):
                local["position"] = node[key]
                break

        for key in ID_KEYS:
            if key in node and node[key] not in (None, ""):
                local["player_id"] = node[key]
                break

        for key in WEEK_KEYS:
            if key in node and node[key] not in (None, ""):
                wk = maybe_week(node[key])
                if wk is not None:
                    local["week"] = wk
                    break

        flat = flatten_embedded_stats(node)
        for k in flat.keys():
            observed_keys[k] += 1

        if row_has_stats(flat):
            candidate_nodes += 1
            candidate = dict(local)
            candidate.update(flat)

            if (
                maybe_week(first(candidate, *WEEK_KEYS, "week")) is not None
                and normalize_position(first(candidate, *POSITION_KEYS, "position"))
                is not None
            ):
                rows.append(candidate)

        # Recurse into nested containers while carrying metadata.
        for key, value in node.items():
            if isinstance(value, (dict, list)):
                child_context = dict(local)
                wk = maybe_week(key)
                if wk is not None:
                    child_context["week"] = wk
                walk(value, child_context, str(key))

    walk(payload, {})

    # Deduplicate structurally repeated rows.
    deduped = []
    seen = set()
    for row in rows:
        sig = (
            str(first(row, *ID_KEYS, "player_id") or ""),
            normalize_name(str(first(row, *NAME_KEYS, "player_name") or "")),
            normalize_position(first(row, *POSITION_KEYS, "position")),
            maybe_week(first(row, *WEEK_KEYS, "week")),
            tuple(
                (fs_key, tuple(as_float(row.get(k)) for k in aliases))
                for fs_key, aliases in STAT_ALIASES.items()
            ),
        )
        if sig in seen:
            continue
        seen.add(sig)
        deduped.append(row)

    top_keys = sorted(observed_keys.items(), key=lambda x: x[1], reverse=True)[:40]
    audit = {
        "dict_nodes_seen": dict_nodes,
        "list_nodes_seen": list_nodes,
        "stat_candidate_nodes": candidate_nodes,
        "weekly_rows_extracted": len(deduped),
        "most_common_keys": [{"key": k, "count": v} for k, v in top_keys],
    }
    return deduped, audit


def historical_position(row: Dict[str, Any]) -> Optional[str]:
    return normalize_position(first(row, *POSITION_KEYS, "position"))


def historical_name(row: Dict[str, Any]) -> Optional[str]:
    value = first(row, *NAME_KEYS, "player_name")
    return str(value) if value else None


def historical_week(row: Dict[str, Any]) -> Optional[int]:
    return maybe_week(first(row, *WEEK_KEYS, "week"))


def get_alias_value(row: Dict[str, Any], aliases: Iterable[str]) -> float:
    for alias in aliases:
        if alias in row and row[alias] not in (None, ""):
            return as_float(row[alias])
    return 0.0


def score_history_row(
    row: Dict[str, Any],
    scoring: Dict[str, Any],
) -> Tuple[float, str]:
    """
    Prefer exact stat-by-stat reconstruction under FSFFL scoring.

    If the normalized historical file exposes only an already-computed
    half-PPR score and no raw offensive stats, use that as a fallback and
    identify it in the audit.
    """
    raw_stat_present = any(
        alias in row
        for aliases in STAT_ALIASES.values()
        for alias in aliases
    )

    if raw_stat_present:
        total = 0.0

        for fs_key, aliases in STAT_ALIASES.items():
            weight = as_float(scoring.get(fs_key))
            if weight:
                total += weight * get_alias_value(row, aliases)

        fum_weight = as_float(scoring.get("fum_lost"))
        if fum_weight:
            explicit = first(row, "fumbles_lost", "fum_lost")
            if explicit is not None:
                lost = as_float(explicit)
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

        return total, "raw_stats_fsffl"

    for key in KNOWN_POINT_KEYS:
        if key in row and row[key] not in (None, ""):
            return as_float(row[key]), f"precomputed:{key}"

    return 0.0, "unscorable"


def has_activity(row: Dict[str, Any], points: float) -> bool:
    if points != 0:
        return True
    for key in ACTIVITY_ALIASES:
        if as_float(row.get(key)) > 0:
            return True

    # If a normalized weekly points field explicitly exists, zero is still a
    # valid played-week observation.
    return any(key in row and row[key] not in (None, "") for key in KNOWN_POINT_KEYS)


def robust_cv(values: List[float]) -> Optional[float]:
    vals = [float(x) for x in values if x is not None and x >= 0]
    if len(vals) < 2:
        return None

    vals.sort()
    if len(vals) >= 20:
        lo = vals[max(0, int(len(vals) * 0.05))]
        hi = vals[min(len(vals) - 1, int(len(vals) * 0.95))]
        vals = [min(max(x, lo), hi) for x in vals]

    mean = statistics.fmean(vals)
    if mean <= 0.25:
        return None

    return max(0.15, min(1.35, statistics.stdev(vals) / mean))


def percentile_normal(mean: float, sd: float, z: float) -> float:
    return round(max(0.0, mean + z * sd), 3)


def load_history(active_season: int, scoring: Dict[str, Any]):
    player_scores_by_name = defaultdict(list)
    position_scores = defaultdict(list)

    files_used = []
    files_missing = []
    file_schema_audits = {}
    scoring_method_counts = defaultdict(int)
    scored_rows = 0

    for season in range(active_season - HISTORY_SEASONS, active_season):
        path = (
            DATA / "stats" / "nfl" / str(season)
            / "player_weekly_normalized.json"
        )

        if not path.exists():
            files_missing.append(str(path))
            continue

        payload = load_json(path)
        rows, schema_audit = extract_weekly_rows(payload)

        files_used.append(str(path))
        file_schema_audits[str(path)] = schema_audit

        for row in rows:
            week = historical_week(row)
            pos = historical_position(row)
            name = historical_name(row)

            if week is None or pos not in {"QB", "RB", "WR", "TE"}:
                continue

            points, method = score_history_row(row, scoring)
            if method == "unscorable":
                continue
            if not has_activity(row, points):
                continue

            scored_rows += 1
            scoring_method_counts[method] += 1
            position_scores[pos].append(points)

            if name:
                player_scores_by_name[(normalize_name(name), pos)].append(points)

    audit = {
        "files_used": files_used,
        "files_missing": files_missing,
        "scored_history_rows": scored_rows,
        "scoring_method_counts": dict(scoring_method_counts),
        "schema_by_file": file_schema_audits,
        "position_history_rows": {
            pos: len(position_scores[pos])
            for pos in ("QB", "RB", "WR", "TE")
        },
    }

    return player_scores_by_name, position_scores, audit


def main():
    league = load_json(DATA / "league.json")
    if not league:
        raise RuntimeError("Missing data/league.json")

    season = str(league.get("season") or "").strip()
    if not season:
        raise RuntimeError("Missing active season in data/league.json")

    season_int = int(season)
    scoring = league.get("scoring_settings") or {}

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

    history_by_player, history_by_position, history_audit = load_history(
        season_int, scoring
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

    playoff_start = int(
        (league.get("settings") or {}).get("playoff_week_start") or 15
    )
    last_week = max(17, playoff_start + 2)

    out_players = {}
    player_specific_count = 0
    position_history_count = 0

    for sid, player in baseline_players.items():
        pos = str(player.get("position") or "").upper()
        if pos not in {"QB", "RB", "WR", "TE"}:
            continue

        prior_player = prior_players.get(sid, {})
        name = (
            player.get("player_name")
            or prior_player.get("player_name")
            or sid
        )

        ppg = as_float(player.get("fsffl_projected_ppg"))
        if ppg <= 0:
            games = max(
                1.0,
                as_float(player.get("games_projected")) or 17.0,
            )
            ppg = as_float(player.get("fsffl_projected_points")) / games

        hist = history_by_player.get((normalize_name(name), pos), [])
        individual_cv = (
            robust_cv(hist)
            if len(hist) >= MIN_PLAYER_GAMES
            else None
        )

        if individual_cv is not None:
            n = len(hist)
            weight = min(0.75, n / 32.0)
            cv = (
                weight * individual_cv
                + (1.0 - weight) * position_cv[pos]
            )
            volatility_source = "player_history_shrunk_to_position"
            player_specific_count += 1
        else:
            cv = position_cv[pos]
            volatility_source = "position_history"
            position_history_count += 1

        sd = max(POSITION_SD_FLOOR[pos], ppg * cv)

        bye_week = prior_player.get("bye_week")
        try:
            bye_week = int(bye_week) if bye_week is not None else None
        except (TypeError, ValueError):
            bye_week = None

        weeks = {}
        for week in range(1, last_week + 1):
            is_bye = week == bye_week
            mean = 0.0 if is_bye else ppg
            week_sd = 0.1 if is_bye else sd
            active_probability = 0.0 if is_bye else 1.0

            median = (
                0.0
                if is_bye
                else max(0.0, mean - 0.08 * week_sd)
            )

            weeks[str(week)] = {
                "mean": round(mean, 3),
                "median": round(median, 3),
                "sd": round(week_sd, 3),
                "p25": percentile_normal(
                    mean, week_sd, -0.67448975
                ),
                "p75": percentile_normal(
                    mean, week_sd, 0.67448975
                ),
                "active_probability": active_probability,
                "is_bye": is_bye,
            }

        out_players[sid] = {
            "name": name,
            "position": pos,
            "team": player.get("team"),
            "season_baseline_ppg": round(ppg, 3),
            "bye_week": bye_week,
            "volatility_cv": round(cv, 5),
            "volatility_source": volatility_source,
            "historical_games_for_player_volatility": len(hist),
            "weeks": weeks,
        }

    baseline_ids = set(baseline_players)
    generated_ids = set(out_players)
    coverage = len(generated_ids & baseline_ids) / max(1, len(baseline_ids))

    history_rows_ok = history_audit["scored_history_rows"] >= MIN_HISTORY_ROWS
    positions_ok = all(
        position_calibration_source[pos] == "historical"
        for pos in ("QB", "RB", "WR", "TE")
    )
    coverage_ok = coverage >= 0.95

    write_json(
        inputs_dir / "player_weekly_projections.json",
        {
            "season": season,
            "source": (
                "Razzball season projection scored under FSFFL rules; "
                "weekly distribution width calibrated from 3 seasons of "
                "known NFL weekly outcomes; bye weeks from preseason prior"
            ),
            "model_stage": "preseason_weekly_baseline_v2",
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
            "position_volatility_players": position_history_count,
            "position_cv": position_cv,
            "position_calibration_source": position_calibration_source,
            "history": history_audit,
            "weeks_generated": [1, last_week],
            "quality_gate": {
                "minimum_baseline_coverage": 0.95,
                "minimum_history_rows": MIN_HISTORY_ROWS,
                "coverage_passed": coverage_ok,
                "history_rows_passed": history_rows_ok,
                "all_positions_historical_passed": positions_ok,
                "passed": coverage_ok and history_rows_ok and positions_ok,
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
        f"Historical weekly rows scored: "
        f"{history_audit['scored_history_rows']:,}."
    )
    print(
        "Position volatility sources: "
        + ", ".join(
            f"{pos}={position_calibration_source[pos]}"
            for pos in ("QB", "RB", "WR", "TE")
        )
    )
    print(
        f"Player-specific volatility: {player_specific_count}; "
        f"position-history volatility: {position_history_count}."
    )

    failures = []
    if not coverage_ok:
        failures.append("baseline coverage below 95%")
    if not history_rows_ok:
        failures.append(
            f"historical rows below {MIN_HISTORY_ROWS}"
        )
    if not positions_ok:
        failures.append(
            "one or more positions are still using fallback volatility"
        )

    if failures:
        raise RuntimeError(
            "Weekly projection quality gate failed: "
            + "; ".join(failures)
        )


if __name__ == "__main__":
    main()
