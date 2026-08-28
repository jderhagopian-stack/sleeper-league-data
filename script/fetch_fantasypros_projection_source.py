#!/usr/bin/env python3
"""Fetch and normalize FantasyPros NFL projections for the FSFFL ensemble.

Requires FANTASYPROS_API_KEY. The adapter requests preseason projections for
QB/RB/WR/TE, maps players to the existing Sleeper preseason prior, and
recalculates projected fantasy points under data/league.json scoring rather
than trusting provider fantasy-point totals.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DATA = Path("data")
SIM_ROOT = DATA / "simulator"
BASE_URL = "https://api.fantasypros.com/public/v2/json/nfl/{season}/projections"
POSITIONS = ("QB", "RB", "WR", "TE")
TEAM_ALIASES = {"JAC": "JAX", "NEP": "NE", "KCC": "KC", "SFO": "SF", "GBP": "GB", "NOS": "NO", "TBB": "TB", "LVR": "LV"}


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")


def norm_name(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower().replace("’", "'")
    value = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", value)
    return re.sub(r"[^a-z0-9]+", "", value)


def norm_team(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    value = str(value).upper().strip()
    return TEAM_ALIASES.get(value, value)


def build_prior_index(prior_players: Dict[str, Dict[str, Any]]):
    by_name: Dict[str, List[str]] = {}
    for sid, player in prior_players.items():
        key = norm_name(player.get("player_name") or "")
        if key:
            by_name.setdefault(key, []).append(str(sid))
    return by_name


def choose_sleeper_id(name: str, team: Optional[str], position: str,
                      prior_players: Dict[str, Dict[str, Any]], by_name: Dict[str, List[str]]) -> Tuple[Optional[str], str]:
    candidates = by_name.get(norm_name(name), [])
    if not candidates:
        return None, "unmatched_name"
    if len(candidates) == 1:
        return candidates[0], "normalized_name"
    team = norm_team(team)
    narrowed = [sid for sid in candidates
                if norm_team(prior_players[sid].get("team")) == team
                and str(prior_players[sid].get("position") or "").upper() == position]
    if len(narrowed) == 1:
        return narrowed[0], "name_team_position"
    return None, "ambiguous_name"


def first_stats(player: Dict[str, Any]) -> Dict[str, Any]:
    stats = player.get("stats")
    if isinstance(stats, list) and stats:
        return stats[0] or {}
    if isinstance(stats, dict):
        return stats
    return {}


def num(stats: Dict[str, Any], *keys: str) -> float:
    for key in keys:
        value = stats.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def normalized_stats(stats: Dict[str, Any]) -> Dict[str, float]:
    # FantasyPros field names are normalized to Sleeper/FSFFL scoring keys.
    # Provider fantasy-point totals are intentionally not used.
    return {
        "pass_att": num(stats, "pass_att"),
        "pass_cmp": num(stats, "pass_cmp"),
        "pass_yd": num(stats, "pass_yds", "pass_yd"),
        "pass_td": num(stats, "pass_tds", "pass_td"),
        "pass_int": num(stats, "pass_ints", "pass_int"),
        "rush_att": num(stats, "rush_att"),
        "rush_yd": num(stats, "rush_yds", "rush_yd"),
        "rush_td": num(stats, "rush_tds", "rush_td"),
        "rec": num(stats, "rec", "receptions"),
        "rec_yd": num(stats, "rec_yds", "rec_yd"),
        "rec_td": num(stats, "rec_tds", "rec_td"),
        "fum_lost": num(stats, "fum_lost", "fumbles_lost"),
    }


def score_stats(stats: Dict[str, float], scoring: Dict[str, float]) -> float:
    return round(sum(float(v or 0.0) * float(scoring.get(k, 0.0)) for k, v in stats.items()), 3)


def fetch_position(season: str, position: str, api_key: str) -> Dict[str, Any]:
    query = urllib.parse.urlencode({"position": position, "week": 0})
    url = f"{BASE_URL.format(season=season)}?{query}"
    request = urllib.request.Request(url, headers={"x-api-key": api_key, "Accept": "application/json", "User-Agent": "FSFFL-Projection-Ensemble/1.0"})
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.loads(response.read().decode("utf-8"))


def main():
    api_key = os.environ.get("FANTASYPROS_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("FANTASYPROS_API_KEY is required to fetch FantasyPros projections")

    league = load_json(DATA / "league.json")
    if not league:
        raise RuntimeError("Missing data/league.json")
    season = str(league.get("season") or "").strip()
    scoring = league.get("scoring_settings") or {}
    if not season:
        raise RuntimeError("Active season missing from data/league.json")

    sources_dir = SIM_ROOT / season / "sources"
    prior = load_json(sources_dir / "selected_preseason_prior.json")
    if not prior or not prior.get("players"):
        raise RuntimeError("Missing populated selected_preseason_prior.json for Sleeper player matching")
    prior_players = {str(k): v for k, v in prior["players"].items()}
    by_name = build_prior_index(prior_players)

    players: Dict[str, Dict[str, Any]] = {}
    unmatched = []
    raw_counts = {}

    for position in POSITIONS:
        payload = fetch_position(season, position, api_key)
        raw_players = payload.get("players") or []
        raw_counts[position] = len(raw_players)
        for raw in raw_players:
            name = str(raw.get("name") or "").strip()
            team = norm_team(raw.get("team_id"))
            sid, method = choose_sleeper_id(name, team, position, prior_players, by_name)
            if not sid:
                unmatched.append({"player_name": name, "team": team, "position": position, "reason": method})
                continue
            stats_raw = first_stats(raw)
            stats = normalized_stats(stats_raw)
            points = score_stats(stats, scoring)
            games = num(stats_raw, "games", "g") or 17.0
            players[sid] = {
                "sleeper_id": sid,
                "fantasypros_player_id": raw.get("fpid") or raw.get("player_id"),
                "player_name": prior_players[sid].get("player_name") or name,
                "team": prior_players[sid].get("team") or team,
                "position": position,
                "season": season,
                "games_projected": games,
                "projected_stats": stats,
                "fsffl_projected_points": points,
                "fsffl_projected_ppg": round(points / max(1.0, games), 3),
                "match_method": method,
                "source": "FantasyPros",
            }

    rostered_ids = set(prior_players)
    covered_ids = rostered_ids & set(players)
    coverage = len(covered_ids) / max(1, len(rostered_ids))

    out = {
        "season": season,
        "source_id": "fantasypros",
        "source_name": "FantasyPros",
        "provenance_class": "EXTERNAL_CURRENT_FORECAST",
        "projection_horizon": "preseason_week_0",
        "scoring_source": "data/league.json",
        "players": players,
        "audit": {
            "raw_counts_by_position": raw_counts,
            "mapped_players": len(players),
            "rostered_coverage": round(coverage, 5),
            "unmatched_source_rows": unmatched[:150],
        },
    }
    write_json(sources_dir / "projection_fantasypros.json", out)
    print(f"FantasyPros source normalized: {len(covered_ids)}/{len(rostered_ids)} rostered players ({coverage:.1%}).")
    if coverage < 0.85:
        raise RuntimeError("FantasyPros projection coverage below 85%; source adapter quality gate failed")


if __name__ == "__main__":
    main()
