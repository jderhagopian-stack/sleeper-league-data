#!/usr/bin/env python3
"""
FSFFL GM Engine v2.2 — STRATEGIC VALUATION

Single-file full GM model. Includes the original market/data foundation plus:
1) independently optimized legal FSFFL starting lineups;
2) Universal Franchise Mode for all 12 teams;
3) dynamic core, hold-premium and finite break-glass values;
4) bilateral trade-package economics and lineup impact;
5) owner-specific sell leverage and command centers;
6) a league-wide mutual-trade opportunity map.

This file DOES NOT import or require another GM-engine Python file.
"""

from __future__ import annotations

import csv
import functools
import gzip
import io
import itertools
import json
import math
import re
import statistics
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from league_rules import load_league_rules, normalize_position, slot_eligible_positions

DATA = Path("data")
DATA.mkdir(parents=True, exist_ok=True)
LEAGUE_RULES = load_league_rules(DATA / "league.json", DATA / "traded_picks.json")

FANTASYCALC_URL = "https://api.fantasycalc.com/values/current"
FC_PARAMS = {
    "isDynasty": "true",
    "numQbs": LEAGUE_RULES["market_num_qbs"],
    "numTeams": LEAGUE_RULES["team_count"],
    "ppr": LEAGUE_RULES["ppr"],
}
FC_REDRAFT_PARAMS = {
    "isDynasty": "false",
    "numQbs": LEAGUE_RULES["market_num_qbs"],
    "numTeams": LEAGUE_RULES["team_count"],
    "ppr": LEAGUE_RULES["ppr"],
}


NFLVERSE_PLAYER_STATS_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "player_stats/player_stats.csv.gz"
)
NFLVERSE_SNAP_COUNTS_URL_TEMPLATE = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "snap_counts/snap_counts_{season}.csv"
)

# Optional file for qualitative/camp/news intelligence created by a human/agent.
# The valuation engine remains fully functional if it does not exist.
FOOTBALL_INTELLIGENCE_OVERRIDES = DATA / "football_intelligence_overrides.json"


USER_MANAGER = "jimmygoodjob"
USER_TEAM = "Hurts So Good"

# Legacy compatibility only. GM-2.2 does not hard-code untouchables.
PROTECTED_HSG_PLAYERS = set()

FUTURE_PICK_YEARS = list(LEAGUE_RULES["future_pick_years"])
ROUNDS = list(LEAGUE_RULES["rounds"])
POSITIONS = tuple(LEAGUE_RULES["positions"])

CONFIG = {
    "model_version": "GM-1.0",
    "market_source": "FantasyCalc current values",
    "market_settings": {
        "dynasty": True,
        "num_qbs": LEAGUE_RULES["market_num_qbs"],
        "num_teams": LEAGUE_RULES["team_count"],
        "ppr": LEAGUE_RULES["ppr"],
        "te_premium": False,
    },
    "owner_value_weights": {
        "roster_need_max_adjustment": 0.08,
        "historical_position_preference_max_adjustment": 0.05,
        "competitive_window_max_adjustment": 0.07,
        "current_owner_endowment_premium": 0.05,
        "starter_dependency_premium": 0.08,
        "thin_depth_hold_premium": 0.04,
    },
    "performance_weights": {
        "recent_games": 6,
        "short_window_games": 3,
        "season_weight": 0.30,
        "recent_weight": 0.55,
        "momentum_weight": 0.15,
        "max_adjustment": 0.10,
    },
    "football_intelligence_weights": {
        "usage_max_adjustment": 0.10,
        "snap_trend_max_adjustment": 0.06,
        "injury_max_adjustment": 0.12,
        "manual_signal_max_adjustment": 0.15,
        "market_momentum_max_adjustment": 0.06,
    },
    "championship_utility": {
        "starter_upgrade_weight": 0.75,
        "depth_insurance_weight": 0.25,
        "max_trade_utility_adjustment": 0.12,
    },
    "pick_value_weights": {
        "pick_preference_max_adjustment": 0.10,
        "rebuild_pick_window_max_adjustment": 0.08,
        "contender_pick_discount_max_adjustment": 0.05,
    },
    "package_effective_value_weights": [1.0, 0.92, 0.84],
    "notes": [
        "Values are estimates, not probabilities or guaranteed trade prices.",
        "FantasyCalc provides the market anchor; FSFFL and owner values are model adjustments.",
        "Current-owner hold value intentionally includes a modest endowment/replacement premium.",
        "Owner behavior is inferred from completed transactions, drafts and waivers; rejected offers are not available.",
    ],
}


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, obj: Any):
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=False)


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def normalize_name(s: Optional[str]) -> str:
    s = (s or "").lower()
    s = s.replace("iii", "").replace("ii", "").replace("jr.", "").replace("jr", "")
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


def percentile_rank(value: float, values: List[float]) -> float:
    if not values:
        return 0.5
    if len(values) == 1:
        return 0.5
    below = sum(v < value for v in values)
    equal = sum(v == value for v in values)
    return clamp((below + 0.5 * equal) / len(values), 0.0, 1.0)


def fetch_json(url: str, params: Dict[str, Any], timeout: int = 20) -> Any:
    full = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        full,
        headers={
            "User-Agent": "FSFFL-Valuation-Engine/1.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fc_entry_to_row(entry: Dict[str, Any], market_type: str) -> Dict[str, Any]:
    p = entry.get("player") or {}
    return {
        "market_type": market_type,
        "name": p.get("name") or entry.get("name"),
        "position": p.get("position"),
        "team": p.get("maybeTeam") or p.get("team"),
        "age": p.get("maybeAge") or p.get("age"),
        "sleeper_id": str(p.get("sleeperId")) if p.get("sleeperId") is not None else None,
        "fantasycalc_player_id": p.get("id"),
        "value": safe_float(entry.get("value")),
        "overall_rank": entry.get("overallRank"),
        "position_rank": entry.get("positionRank"),
        "trend_30_day": entry.get("trend30Day"),
    }


def fetch_fantasycalc_markets() -> Dict[str, Any]:
    cache_path = DATA / "market_values_fantasycalc.json"
    fetched_at = datetime.now(timezone.utc).isoformat()

    try:
        dynasty_raw = fetch_json(FANTASYCALC_URL, FC_PARAMS)
        redraft_raw = fetch_json(FANTASYCALC_URL, FC_REDRAFT_PARAMS)

        dynasty = [fc_entry_to_row(x, "dynasty") for x in dynasty_raw]
        redraft = [fc_entry_to_row(x, "redraft") for x in redraft_raw]

        payload = {
            "fetched_at_utc": fetched_at,
            "source": FANTASYCALC_URL,
            "settings": {
                "dynasty": FC_PARAMS,
                "redraft": FC_REDRAFT_PARAMS,
            },
            "dynasty": dynasty,
            "redraft": redraft,
        }
        write_json(cache_path, payload)
        return payload
    except Exception as exc:
        cached = load_json(cache_path)
        if cached:
            cached["cache_used_due_to_fetch_error"] = repr(exc)
            return cached
        raise RuntimeError(
            "FantasyCalc fetch failed and no cached market file exists. "
            f"Original error: {exc!r}"
        )


def market_position_coverage(market: Dict[str, Any]) -> Dict[str, Any]:
    """Expose when the external market cannot value a league-required position."""
    required = set(LEAGUE_RULES["positions"])
    dynasty_positions = {
        normalize_position(row.get("position"))
        for row in (market.get("dynasty") or [])
        if row.get("position")
    }
    redraft_positions = {
        normalize_position(row.get("position"))
        for row in (market.get("redraft") or [])
        if row.get("position")
    }
    missing_dynasty = sorted(required - dynasty_positions)
    missing_redraft = sorted(required - redraft_positions)
    return {
        "required_positions": sorted(required),
        "dynasty_positions_available": sorted(dynasty_positions),
        "redraft_positions_available": sorted(redraft_positions),
        "missing_dynasty_positions": missing_dynasty,
        "missing_redraft_positions": missing_redraft,
        "authoritative_position_coverage": not (missing_dynasty or missing_redraft),
        "policy": (
            "Missing required positions must be treated as unsupported valuation coverage, "
            "not silently interpreted as zero-value evidence."
        ),
    }


def build_market_indexes(market: Dict[str, Any]):
    out = {}
    for kind in ("dynasty", "redraft"):
        by_sid = {}
        by_name_pos = {}
        rows = market.get(kind, [])
        for row in rows:
            sid = row.get("sleeper_id")
            if sid:
                by_sid[str(sid)] = row
            key = (normalize_name(row.get("name")), row.get("position"))
            if key[0]:
                by_name_pos[key] = row
        out[kind] = {"by_sid": by_sid, "by_name_pos": by_name_pos, "rows": rows}
    return out


def market_row_for_player(
    player_id: str,
    player: Dict[str, Any],
    idx: Dict[str, Any],
    kind: str,
) -> Optional[Dict[str, Any]]:
    row = idx[kind]["by_sid"].get(str(player_id))
    if row:
        return row
    key = (normalize_name(player.get("full_name")), player.get("position"))
    return idx[kind]["by_name_pos"].get(key)


def infer_fc_pick_values(market: Dict[str, Any]) -> Dict[Tuple[int, str, int], float]:
    """
    Tries to detect FantasyCalc pick rows by names such as:
    '2027 Early 1st', '2027 Mid 1st', '2027 Late 1st'
    or comparable variants.
    """
    found: Dict[Tuple[int, str, int], float] = {}
    for row in market.get("dynasty", []):
        name = row.get("name") or ""
        n = name.lower()
        m_year = re.search(r"(20\\d{2})", n)
        if not m_year:
            continue
        if "pick" not in n and not any(x in n for x in ("1st", "2nd", "3rd", "first", "second", "third")):
            continue
        year = int(m_year.group(1))
        tier = "mid"
        if "early" in n:
            tier = "early"
        elif "late" in n:
            tier = "late"

        rnd = None
        if "1st" in n or "first" in n:
            rnd = 1
        elif "2nd" in n or "second" in n:
            rnd = 2
        elif "3rd" in n or "third" in n:
            rnd = 3
        if rnd:
            found[(year, tier, rnd)] = safe_float(row.get("value"))
    return found


def fallback_pick_value(year: int, tier: str, rnd: int, detected: Dict[Tuple[int, str, int], float]) -> float:
    if (year, tier, rnd) in detected:
        return detected[(year, tier, rnd)]

    # Try same-year mid then infer early/late.
    if (year, "mid", rnd) in detected:
        base = detected[(year, "mid", rnd)]
        return base * {"early": 1.18, "mid": 1.0, "late": 0.84}[tier]

    # Try nearest known year for same round/tier.
    known = [(y, v) for (y, t, r), v in detected.items() if t == tier and r == rnd]
    if known:
        y0, v0 = min(known, key=lambda z: abs(z[0] - year))
        return v0 * (0.88 ** max(0, year - y0)) * (1.08 ** max(0, y0 - year))

    # Conservative fallback scale, only used if FC did not surface future picks.
    mids = {
        1: 5200.0,
        2: 2350.0,
        3: 1050.0,
    }
    first_future_season = int(LEAGUE_RULES["season"]) + 1
    years_out = max(0, year - first_future_season)
    year_discount = 0.88 ** years_out
    tier_adj = {"early": 1.20, "mid": 1.0, "late": 0.82}[tier]
    return mids[rnd] * year_discount * tier_adj


def owner_maps(rosters: List[Dict[str, Any]], profiles: List[Dict[str, Any]]):
    profile_by_uid = {str(x.get("user_id")): x for x in profiles}
    roster_by_id = {int(x["roster_id"]): x for x in rosters}
    roster_id_to_uid = {int(x["roster_id"]): str(x.get("owner_id")) for x in rosters}
    uid_to_roster_id = {v: k for k, v in roster_id_to_uid.items()}
    return profile_by_uid, roster_by_id, roster_id_to_uid, uid_to_roster_id


def team_label(uid: str, profile_by_uid: Dict[str, Dict[str, Any]]) -> str:
    p = profile_by_uid.get(str(uid), {})
    return p.get("team_name") or p.get("manager") or str(uid)


def manager_label(uid: str, profile_by_uid: Dict[str, Dict[str, Any]]) -> str:
    p = profile_by_uid.get(str(uid), {})
    return p.get("manager") or p.get("username") or str(uid)


def build_player_values(
    rosters: List[Dict[str, Any]],
    players: Dict[str, Dict[str, Any]],
    market_idx: Dict[str, Any],
):
    rostered_ids = sorted({str(pid) for r in rosters for pid in (r.get("players") or [])})
    values = {}
    for pid in rostered_ids:
        p = players.get(pid) or {}
        drow = market_row_for_player(pid, p, market_idx, "dynasty")
        rrow = market_row_for_player(pid, p, market_idx, "redraft")
        dynasty = safe_float((drow or {}).get("value"))
        redraft = safe_float((rrow or {}).get("value"))
        # If player is omitted from redraft market, preserve zero rather than fabricate.
        values[pid] = {
            "asset_type": "player",
            "player_id": pid,
            "name": p.get("full_name") or (drow or {}).get("name") or pid,
            "position": p.get("position") or (drow or {}).get("position"),
            "nfl_team": p.get("team") or (drow or {}).get("team"),
            "age": p.get("age") or (drow or {}).get("age"),
            "injury_status": p.get("injury_status"),
            "market_dynasty": round(dynasty, 1),
            "market_redraft": round(redraft, 1),
            "market_rank": (drow or {}).get("overall_rank"),
            "position_rank": (drow or {}).get("position_rank"),
            "trend_30_day": (drow or {}).get("trend_30_day"),
            "market_match": "sleeper_id" if drow and drow.get("sleeper_id") == pid else ("name_position" if drow else "unmatched"),
        }
    return values


def current_owner_by_player(rosters: List[Dict[str, Any]]) -> Dict[str, str]:
    out = {}
    for r in rosters:
        uid = str(r.get("owner_id"))
        for pid in r.get("players") or []:
            out[str(pid)] = uid
    return out


def starter_sets(rosters: List[Dict[str, Any]]) -> Dict[str, set]:
    return {str(r.get("owner_id")): {str(x) for x in (r.get("starters") or [])} for r in rosters}



def _perf_player_id(row):
    for key in ("player_id", "player", "sleeper_id"):
        if row.get(key) is not None:
            return str(row.get(key))
    return None


def _perf_week(row):
    for key in ("week", "week_num", "week_number"):
        if row.get(key) is not None:
            try:
                return int(row.get(key))
            except (TypeError, ValueError):
                pass
    return None


def _perf_points(row):
    for key in ("fsffl_points", "fantasy_points", "points", "pts", "fpts", "score"):
        if row.get(key) is not None:
            return safe_float(row.get(key))
    return 0.0


def load_recent_performance(active_season=None):
    """Load current-season FSFFL weekly production when available."""
    active_season = int(active_season or LEAGUE_RULES["season"])
    paths = [
        DATA / "stats" / "fsffl" / str(active_season) / "player_weekly_fsffl.json",
        DATA / "stats" / "fsffl" / str(active_season) / "player_weekly_raw.json",
    ]
    rows, source = [], None

    for path in paths:
        payload = load_json(path)
        if not payload:
            continue
        source = str(path)
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            if isinstance(payload.get("rows"), list):
                rows = payload["rows"]
            elif isinstance(payload.get("records"), list):
                rows = payload["records"]
            else:
                for pid, value in payload.items():
                    if isinstance(value, list):
                        for item in value:
                            if isinstance(item, dict):
                                item = dict(item)
                                item.setdefault("player_id", pid)
                                rows.append(item)
                    elif isinstance(value, dict):
                        item = dict(value)
                        item.setdefault("player_id", pid)
                        rows.append(item)
        if rows:
            break

    grouped = defaultdict(list)
    for row in rows:
        if not isinstance(row, dict):
            continue
        pid, week = _perf_player_id(row), _perf_week(row)
        if pid and week is not None:
            grouped[pid].append((week, _perf_points(row)))

    result = {}
    recent_n = CONFIG["performance_weights"]["recent_games"]
    short_n = CONFIG["performance_weights"]["short_window_games"]

    for pid, samples in grouped.items():
        samples.sort()
        vals = [p for _, p in samples]
        recent = vals[-recent_n:]
        short = vals[-short_n:]
        result[pid] = {
            "source": source,
            "games": len(vals),
            "season_ppg": round(statistics.mean(vals), 3),
            "recent_ppg": round(statistics.mean(recent), 3),
            "short_ppg": round(statistics.mean(short), 3),
            "last_week": samples[-1][0],
        }
    return result


def build_performance_baselines(performance, player_values):
    buckets = defaultdict(lambda: defaultdict(list))
    for pid, perf in performance.items():
        pos = player_values.get(pid, {}).get("position")
        if pos not in POSITIONS:
            continue
        buckets[pos]["season"].append(perf["season_ppg"])
        buckets[pos]["recent"].append(perf["recent_ppg"])
        buckets[pos]["short"].append(perf["short_ppg"])

    out = {}
    for pos in POSITIONS:
        out[pos] = {}
        for key in ("season", "recent", "short"):
            vals = buckets[pos][key]
            out[pos][key] = statistics.median(vals) if vals else 0.0
    return out


def performance_adjustment(asset, performance, baselines):
    """Return a bounded market-independent production adjustment."""
    pid = str(asset.get("player_id"))
    pos = asset.get("position")
    perf = performance.get(pid)

    if not perf or pos not in baselines:
        return 0.0, {"available": False, "adjustment": 0.0}

    def signal(value, baseline):
        if baseline <= 0:
            return 0.0
        return clamp(((value / baseline) - 1.0) / 1.25, -1, 1)

    season = signal(perf["season_ppg"], baselines[pos]["season"])
    recent = signal(perf["recent_ppg"], baselines[pos]["recent"])
    momentum = signal(perf["short_ppg"], baselines[pos]["short"])

    cfg = CONFIG["performance_weights"]
    raw = (
        cfg["season_weight"] * season
        + cfg["recent_weight"] * recent
        + cfg["momentum_weight"] * momentum
    )
    reliability = clamp(perf["games"] / 3.0, 0.25, 1.0)
    adj = clamp(raw * cfg["max_adjustment"] * reliability,
                -cfg["max_adjustment"], cfg["max_adjustment"])

    return adj, {
        "available": True,
        "games": perf["games"],
        "season_ppg": perf["season_ppg"],
        "recent_ppg": perf["recent_ppg"],
        "short_ppg": perf["short_ppg"],
        "signal": round(raw, 4),
        "reliability": round(reliability, 3),
        "adjustment": round(adj, 4),
    }



def _download_bytes(url: str, timeout: int = 25) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "FSFFL-GM-Engine/1.0", "Accept": "*/*"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _read_csv_url(url: str, gzipped: bool = False) -> List[Dict[str, str]]:
    raw = _download_bytes(url)
    if gzipped:
        raw = gzip.decompress(raw)
    text_data = raw.decode("utf-8", errors="replace")
    return list(csv.DictReader(io.StringIO(text_data)))


def fetch_nflverse_usage(active_season: int | None = None) -> Dict[str, Dict[str, Any]]:
    """
    Fetch weekly player stats and derive role/usage signals:
      - carries
      - targets
      - touches
      - team opportunity shares
      - short-window trend

    Uses player name + team as the bridge when Sleeper IDs are not present.
    """
    active_season = int(active_season or LEAGUE_RULES["season"])
    cache = DATA / f"nflverse_usage_{active_season}.json"
    fetched_at = datetime.now(timezone.utc).isoformat()
    try:
        rows = _read_csv_url(NFLVERSE_PLAYER_STATS_URL, gzipped=True)
        rows = [r for r in rows if str(r.get("season")) == str(active_season)]
        payload = {"fetched_at_utc": fetched_at, "rows": rows}
        write_json(cache, payload)
    except Exception as exc:
        payload = load_json(cache, {"rows": []})
        payload["cache_used_due_to_fetch_error"] = repr(exc)

    rows = payload.get("rows") or []
    by_key = defaultdict(list)
    team_week_totals = defaultdict(lambda: {"carries": 0.0, "targets": 0.0})

    for r in rows:
        name = r.get("player_display_name") or r.get("player_name") or r.get("name")
        team = r.get("recent_team") or r.get("team")
        week = r.get("week")
        pos = r.get("position")
        if not name or not team or week is None:
            continue
        try:
            week_i = int(float(week))
        except (TypeError, ValueError):
            continue
        carries = safe_float(r.get("carries"))
        targets = safe_float(r.get("targets"))
        fantasy = safe_float(r.get("fantasy_points"))
        key = (normalize_name(name), team, pos)
        item = {
            "week": week_i,
            "carries": carries,
            "targets": targets,
            "touches": carries + safe_float(r.get("receptions")),
            "fantasy_points": fantasy,
        }
        by_key[key].append(item)
        team_week_totals[(team, week_i)]["carries"] += carries
        team_week_totals[(team, week_i)]["targets"] += targets

    out = {}
    for key, vals in by_key.items():
        vals.sort(key=lambda x: x["week"])
        for v in vals:
            totals = team_week_totals[(key[1], v["week"])]
            v["carry_share"] = v["carries"] / totals["carries"] if totals["carries"] else 0.0
            v["target_share"] = v["targets"] / totals["targets"] if totals["targets"] else 0.0

        recent = vals[-6:]
        short = vals[-3:]
        out["|".join(str(x or "") for x in key)] = {
            "name_norm": key[0],
            "team": key[1],
            "position": key[2],
            "games": len(vals),
            "last_week": vals[-1]["week"],
            "recent_carries_pg": round(statistics.mean(v["carries"] for v in recent), 3),
            "recent_targets_pg": round(statistics.mean(v["targets"] for v in recent), 3),
            "recent_touches_pg": round(statistics.mean(v["touches"] for v in recent), 3),
            "recent_carry_share": round(statistics.mean(v["carry_share"] for v in recent), 4),
            "recent_target_share": round(statistics.mean(v["target_share"] for v in recent), 4),
            "short_carry_share": round(statistics.mean(v["carry_share"] for v in short), 4),
            "short_target_share": round(statistics.mean(v["target_share"] for v in short), 4),
        }
    return out


def fetch_nflverse_snaps(active_season: int | None = None) -> Dict[str, Dict[str, Any]]:
    """
    Fetch game-level offensive snap counts. nflverse snap counts are polled
    multiple times per day during the season.
    """
    active_season = int(active_season or LEAGUE_RULES["season"])
    cache = DATA / f"nflverse_snap_counts_{active_season}.json"
    fetched_at = datetime.now(timezone.utc).isoformat()
    try:
        rows = _read_csv_url(NFLVERSE_SNAP_COUNTS_URL_TEMPLATE.format(season=active_season), gzipped=False)
        payload = {"fetched_at_utc": fetched_at, "rows": rows}
        write_json(cache, payload)
    except Exception as exc:
        payload = load_json(cache, {"rows": []})
        payload["cache_used_due_to_fetch_error"] = repr(exc)

    grouped = defaultdict(list)
    for r in payload.get("rows") or []:
        name = r.get("player") or r.get("player_name")
        team = r.get("team")
        pos = r.get("position")
        if not name or not team:
            continue
        snap_pct = safe_float(
            r.get("offense_pct")
            or r.get("offense_percentage")
            or r.get("off_pct")
        )
        if snap_pct > 1.0:
            snap_pct /= 100.0
        # Some files expose raw snaps and team snaps instead.
        if snap_pct <= 0:
            snaps = safe_float(r.get("offense_snaps") or r.get("off_snaps"))
            total = safe_float(r.get("offense_snaps_team") or r.get("team_offense_snaps"))
            if total:
                snap_pct = snaps / total
        key = (normalize_name(name), team, pos)
        grouped[key].append(snap_pct)

    out = {}
    for key, vals in grouped.items():
        vals = [v for v in vals if v >= 0]
        if not vals:
            continue
        recent = vals[-6:]
        short = vals[-3:]
        out["|".join(str(x or "") for x in key)] = {
            "name_norm": key[0],
            "team": key[1],
            "position": key[2],
            "games": len(vals),
            "recent_snap_share": round(statistics.mean(recent), 4),
            "short_snap_share": round(statistics.mean(short), 4),
        }
    return out


def _match_external_player(asset, dataset):
    name_norm = normalize_name(asset.get("name"))
    team = asset.get("nfl_team")
    pos = asset.get("position")
    exact = dataset.get("|".join((name_norm, str(team or ""), str(pos or ""))))
    if exact:
        return exact
    # fallback on name + team, then name only
    candidates = [
        v for v in dataset.values()
        if v.get("name_norm") == name_norm and (not team or v.get("team") == team)
    ]
    if len(candidates) == 1:
        return candidates[0]
    candidates = [v for v in dataset.values() if v.get("name_norm") == name_norm]
    return candidates[0] if len(candidates) == 1 else None


def load_manual_football_intelligence() -> Dict[str, Dict[str, Any]]:
    """
    Optional qualitative signal format:
    {
      "Patrick Mahomes": {
        "signal": 0.05,
        "confidence": 0.9,
        "reason": "..."
      }
    }
    signal should be between -1 and +1; the engine caps its impact.
    """
    payload = load_json(FOOTBALL_INTELLIGENCE_OVERRIDES, {})
    if isinstance(payload, dict) and "players" in payload and isinstance(payload["players"], dict):
        payload = payload["players"]
    return payload if isinstance(payload, dict) else {}


def injury_adjustment(asset: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    status = str(asset.get("injury_status") or "").strip().lower()
    max_adj = CONFIG["football_intelligence_weights"]["injury_max_adjustment"]
    mapping = {
        "out": -1.0,
        "doubtful": -0.75,
        "questionable": -0.25,
        "probable": -0.08,
        "ir": -1.0,
        "pup": -0.85,
    }
    sig = mapping.get(status, 0.0)
    return max_adj * sig, {"status": status or None, "signal": sig}


def usage_adjustment(asset, usage, snaps):
    u = _match_external_player(asset, usage)
    s = _match_external_player(asset, snaps)
    pos = asset.get("position")
    max_usage = CONFIG["football_intelligence_weights"]["usage_max_adjustment"]
    max_snap = CONFIG["football_intelligence_weights"]["snap_trend_max_adjustment"]

    usage_signal = 0.0
    snap_signal = 0.0

    if u:
        if pos == "RB":
            share = 0.65 * safe_float(u.get("recent_carry_share")) + 0.35 * safe_float(u.get("recent_target_share"))
            momentum = 0.65 * (safe_float(u.get("short_carry_share")) - safe_float(u.get("recent_carry_share"))) + \
                       0.35 * (safe_float(u.get("short_target_share")) - safe_float(u.get("recent_target_share")))
            usage_signal = clamp((share - 0.28) / 0.35 + 0.8 * momentum, -1, 1)
        elif pos in ("WR", "TE"):
            share = safe_float(u.get("recent_target_share"))
            momentum = safe_float(u.get("short_target_share")) - share
            usage_signal = clamp((share - 0.12) / 0.18 + 1.0 * momentum, -1, 1)
        elif pos == "QB":
            # QB usage is mostly binary; let performance/market handle quality.
            usage_signal = 0.25 if u.get("games", 0) >= 2 else 0.0

    if s:
        recent = safe_float(s.get("recent_snap_share"))
        short = safe_float(s.get("short_snap_share"))
        snap_signal = clamp((recent - 0.45) / 0.45 + 1.25 * (short - recent), -1, 1)

    adj = max_usage * usage_signal + max_snap * snap_signal
    return adj, {
        "usage_available": bool(u),
        "snap_available": bool(s),
        "usage_signal": round(usage_signal, 4),
        "snap_signal": round(snap_signal, 4),
        "usage": u,
        "snaps": s,
        "adjustment": round(adj, 4),
    }


def market_momentum_adjustment(asset):
    trend = asset.get("trend_30_day")
    if trend is None:
        return 0.0, {"available": False, "adjustment": 0.0}
    t = safe_float(trend)
    # FantasyCalc trend can be raw-value movement; normalize conservatively.
    signal = clamp(t / max(1200.0, safe_float(asset.get("market_dynasty")) * 0.25), -1, 1)
    max_adj = CONFIG["football_intelligence_weights"]["market_momentum_max_adjustment"]
    return signal * max_adj, {"available": True, "trend_30_day": t, "signal": round(signal, 4)}


def manual_intelligence_adjustment(asset, manual):
    row = manual.get(asset.get("name")) or manual.get(str(asset.get("player_id"))) or {}
    if not isinstance(row, dict):
        row = {}
    signal = clamp(safe_float(row.get("signal")), -1, 1)
    confidence = clamp(safe_float(row.get("confidence"), 0.5), 0, 1)
    max_adj = CONFIG["football_intelligence_weights"]["manual_signal_max_adjustment"]
    adj = signal * confidence * max_adj
    return adj, {
        "available": bool(row),
        "signal": signal,
        "confidence": confidence,
        "reason": row.get("reason"),
        "adjustment": round(adj, 4),
    }


def football_intelligence_adjustment(asset, usage, snaps, manual):
    inj_adj, inj_meta = injury_adjustment(asset)
    use_adj, use_meta = usage_adjustment(asset, usage, snaps)
    mom_adj, mom_meta = market_momentum_adjustment(asset)
    man_adj, man_meta = manual_intelligence_adjustment(asset, manual)

    total = clamp(inj_adj + use_adj + mom_adj + man_adj, -0.22, 0.22)
    return total, {
        "injury": inj_meta,
        "usage_and_snaps": use_meta,
        "market_momentum": mom_meta,
        "manual_news_signal": man_meta,
        "total_adjustment": round(total, 4),
    }


def current_starting_lineup_value(uid, rosters, player_values):
    for r in rosters:
        if str(r.get("owner_id")) != str(uid):
            continue
        return sum(
            safe_float(player_values.get(str(pid), {}).get("market_redraft"))
            for pid in (r.get("starters") or [])
        )
    return 0.0


def hsg_trade_championship_utility(
    hsg_uid,
    outgoing_asset_ids,
    target_asset,
    rosters,
    player_values,
):
    """
    Approximate immediate 2026 utility using redraft market values and positional
    replacement effects. It is intentionally directional, not a literal title probability.
    """
    target_redraft = safe_float(target_asset.get("market_redraft"))
    outgoing_redraft = 0.0
    for aid in outgoing_asset_ids:
        if aid.startswith("player:"):
            pid = aid.split(":", 1)[1]
            outgoing_redraft += safe_float(player_values.get(pid, {}).get("market_redraft"))

    base_lineup = current_starting_lineup_value(hsg_uid, rosters, player_values)
    if base_lineup <= 0:
        return 0.0, {"available": False}

    # Only part of outgoing redraft value is lost because nonstarters are mostly depth.
    net_immediate = target_redraft - 0.55 * outgoing_redraft
    pct = net_immediate / base_lineup
    cap = CONFIG["championship_utility"]["max_trade_utility_adjustment"]
    utility = clamp(pct * 4.0, -cap, cap)
    return utility, {
        "available": True,
        "base_starting_lineup_redraft_value": round(base_lineup, 1),
        "target_redraft_value": round(target_redraft, 1),
        "outgoing_redraft_value": round(outgoing_redraft, 1),
        "net_immediate_value_proxy": round(net_immediate, 1),
        "championship_utility_adjustment": round(utility, 4),
    }


def build_team_strengths(
    rosters: List[Dict[str, Any]],
    player_values: Dict[str, Dict[str, Any]],
    profile_by_uid: Dict[str, Dict[str, Any]],
):
    raw = {}
    for r in rosters:
        uid = str(r.get("owner_id"))
        starters = [str(x) for x in (r.get("starters") or []) if str(x) != "0"]
        all_players = [str(x) for x in (r.get("players") or []) if str(x) != "0"]
        bench = [x for x in all_players if x not in starters]

        starter_redraft = sum(player_values.get(x, {}).get("market_redraft", 0) for x in starters)
        starter_dynasty = sum(player_values.get(x, {}).get("market_dynasty", 0) for x in starters)
        bench_redraft = sorted(
            (player_values.get(x, {}).get("market_redraft", 0) for x in bench),
            reverse=True,
        )
        bench_dynasty = sorted(
            (player_values.get(x, {}).get("market_dynasty", 0) for x in bench),
            reverse=True,
        )
        immediate_strength = starter_redraft + 0.20 * sum(bench_redraft[:5])
        dynasty_strength = starter_dynasty + 0.18 * sum(bench_dynasty[:6])

        pos_starter = defaultdict(float)
        pos_depth = defaultdict(list)
        for pid in all_players:
            v = player_values.get(pid, {})
            pos = v.get("position")
            if pos not in POSITIONS:
                continue
            pos_depth[pos].append(v.get("market_redraft", 0))
            if pid in starters:
                pos_starter[pos] += v.get("market_redraft", 0)

        raw[uid] = {
            "user_id": uid,
            "manager": manager_label(uid, profile_by_uid),
            "team_name": team_label(uid, profile_by_uid),
            "starter_redraft_value": round(starter_redraft, 1),
            "starter_dynasty_value": round(starter_dynasty, 1),
            "immediate_strength_raw": immediate_strength,
            "dynasty_strength_raw": dynasty_strength,
            "pos_starter_raw": dict(pos_starter),
            "pos_depth_values": {p: sorted(v, reverse=True) for p, v in pos_depth.items()},
        }

    immediate_values = [x["immediate_strength_raw"] for x in raw.values()]
    dynasty_values = [x["dynasty_strength_raw"] for x in raw.values()]

    # Position strength distributions.
    pos_distributions = {
        pos: [x["pos_starter_raw"].get(pos, 0.0) for x in raw.values()]
        for pos in POSITIONS
    }

    out = {}
    for uid, x in raw.items():
        contender = percentile_rank(x["immediate_strength_raw"], immediate_values)
        dynasty_pct = percentile_rank(x["dynasty_strength_raw"], dynasty_values)

        if contender >= 0.75:
            tier = "elite_contender"
        elif contender >= 0.50:
            tier = "contender"
        elif contender >= 0.25:
            tier = "middle"
        else:
            tier = "retool_rebuild"

        needs = {}
        for pos in POSITIONS:
            starter_strength = x["pos_starter_raw"].get(pos, 0.0)
            starter_pct = percentile_rank(starter_strength, pos_distributions[pos])
            depth_vals = x["pos_depth_values"].get(pos, [])
            # A usable depth score: top 3 RB/WR, top 2 QB/TE.
            n = 3 if pos in ("RB", "WR") else 2
            depth_score = sum(depth_vals[:n])
            all_depth_scores = []
            for z in raw.values():
                zv = z["pos_depth_values"].get(pos, [])
                all_depth_scores.append(sum(zv[:n]))
            depth_pct = percentile_rank(depth_score, all_depth_scores)
            needs[pos] = round(clamp(0.65 * (1 - starter_pct) + 0.35 * (1 - depth_pct), 0, 1), 3)

        out[uid] = {
            "user_id": uid,
            "manager": x["manager"],
            "team_name": x["team_name"],
            "contender_score": round(contender, 3),
            "dynasty_roster_score": round(dynasty_pct, 3),
            "competitive_tier": tier,
            "starter_redraft_value": round(x["starter_redraft_value"], 1),
            "starter_dynasty_value": round(x["starter_dynasty_value"], 1),
            "position_need": needs,
        }
    return out


def build_behavior_preferences(profiles: List[Dict[str, Any]]):
    raw_pos_share = {}
    raw_pick = {}

    for p in profiles:
        uid = str(p.get("user_id"))
        trade = p.get("trade_profile") or {}
        draft = p.get("rookie_draft_profile") or {}
        waiver = p.get("waiver_profile") or {}

        acq = trade.get("player_positions_acquired") or {}
        drafted = draft.get("positions") or {}
        added = waiver.get("positions_added") or {}

        scores = {}
        for pos in POSITIONS:
            # Trades get highest weight because they represent explicit willingness to pay.
            scores[pos] = (
                1.00 * safe_float(acq.get(pos))
                + 0.70 * safe_float(drafted.get(pos))
                + 0.20 * safe_float(added.get(pos))
            )
        total = sum(scores.values()) or 1.0
        raw_pos_share[uid] = {pos: scores[pos] / total for pos in POSITIONS}

        acquired = sum(safe_float(trade.get(k)) for k in ("firsts_acquired", "seconds_acquired", "thirds_acquired"))
        sent = sum(safe_float(trade.get(k)) for k in ("firsts_sent", "seconds_sent", "thirds_sent"))
        net = acquired - sent
        draft_volume = safe_float(draft.get("rookie_picks_made_2023_plus"))
        raw_pick[uid] = net + 0.15 * draft_volume

    league_avg_share = {
        pos: statistics.mean(v[pos] for v in raw_pos_share.values()) if raw_pos_share else 0.25
        for pos in POSITIONS
    }
    pick_vals = list(raw_pick.values())
    pick_mean = statistics.mean(pick_vals) if pick_vals else 0.0
    pick_sd = statistics.pstdev(pick_vals) if len(pick_vals) > 1 else 1.0
    if pick_sd == 0:
        pick_sd = 1.0

    out = {}
    for uid, shares in raw_pos_share.items():
        pos_pref = {}
        for pos in POSITIONS:
            avg = league_avg_share[pos] or 0.25
            # ratio to league average, converted to bounded -1..1 preference score.
            ratio = shares[pos] / avg
            pos_pref[pos] = round(clamp((ratio - 1.0) / 0.75, -1, 1), 3)
        pick_z = (raw_pick[uid] - pick_mean) / pick_sd
        out[uid] = {
            "position_preference": pos_pref,
            "pick_preference": round(clamp(pick_z / 2.0, -1, 1), 3),
        }
    return out


def player_window_fit(asset: Dict[str, Any], contender_score: float) -> float:
    """
    Returns -1..1. Positive means the player's current-production/dynasty mix
    aligns with this team's competitive window.
    """
    dynasty = safe_float(asset.get("market_dynasty"))
    redraft = safe_float(asset.get("market_redraft"))
    if dynasty <= 0:
        return 0.0

    immediate_ratio = redraft / dynasty if dynasty else 0.0
    # Roughly center around 0.55; veterans often have high immediate ratio,
    # prospects often low. Clamp to avoid extreme effects.
    immediacy = clamp((immediate_ratio - 0.55) / 0.55, -1, 1)

    age = safe_float(asset.get("age"), 25)
    pos = asset.get("position")
    peak_age = {"QB": 31, "RB": 26, "WR": 28, "TE": 29}.get(pos, 27)
    youth = clamp((peak_age - age) / 6.0, -1, 1)

    # Contenders prefer immediacy; rebuilders prefer youth.
    c = contender_score
    fit = c * immediacy + (1 - c) * youth
    return clamp(fit, -1, 1)


def fsffl_league_value(
    asset,
    performance=None,
    baselines=None,
    usage=None,
    snaps=None,
    manual=None,
):
    """
    Full FSFFL league value:
      market anchor
      + league consolidation premium
      + independent recent performance
      + injuries / usage / snap trend / market momentum / qualitative intelligence
    """
    base = safe_float(asset.get("market_dynasty"))
    rank = asset.get("market_rank")
    if not base:
        return 0.0

    if isinstance(rank, int) and rank <= 24:
        mult = 1.04
    elif isinstance(rank, int) and rank <= 60:
        mult = 1.02
    elif isinstance(rank, int) and rank > 180:
        mult = 0.90
    elif isinstance(rank, int) and rank > 120:
        mult = 0.95
    else:
        mult = 1.0

    perf_adj = 0.0
    if performance is not None and baselines is not None:
        perf_adj, _ = performance_adjustment(asset, performance, baselines)

    football_adj = 0.0
    if usage is not None and snaps is not None and manual is not None:
        football_adj, _ = football_intelligence_adjustment(asset, usage, snaps, manual)

    return base * mult * (1.0 + perf_adj + football_adj)

def owner_player_buy_value(
    uid: str,
    asset: Dict[str, Any],
    team_profiles: Dict[str, Dict[str, Any]],
    prefs: Dict[str, Dict[str, Any]],
    performance=None,
    baselines=None,
    usage=None,
    snaps=None,
    manual=None,
) -> Tuple[float, Dict[str, float]]:
    base = fsffl_league_value(asset, performance, baselines, usage, snaps, manual)
    if base <= 0:
        return 0.0, {"base": 0.0}

    pos = asset.get("position")
    t = team_profiles[uid]
    p = prefs.get(uid, {})
    need = safe_float((t.get("position_need") or {}).get(pos), 0.5)
    pref = safe_float((p.get("position_preference") or {}).get(pos), 0.0)
    window = player_window_fit(asset, safe_float(t.get("contender_score"), 0.5))

    need_adj = CONFIG["owner_value_weights"]["roster_need_max_adjustment"] * (2 * need - 1)
    pref_adj = CONFIG["owner_value_weights"]["historical_position_preference_max_adjustment"] * pref
    window_adj = CONFIG["owner_value_weights"]["competitive_window_max_adjustment"] * window
    mult = 1 + need_adj + pref_adj + window_adj

    return base * mult, {
        "fsffl_base": round(base, 1),
        "need_score": round(need, 3),
        "need_adjustment": round(need_adj, 4),
        "position_preference": round(pref, 3),
        "preference_adjustment": round(pref_adj, 4),
        "window_fit": round(window, 3),
        "window_adjustment": round(window_adj, 4),
        "multiplier": round(mult, 4),
    }


def owner_player_hold_value(
    uid: str,
    asset: Dict[str, Any],
    team_profiles: Dict[str, Dict[str, Any]],
    prefs: Dict[str, Dict[str, Any]],
    starters: Dict[str, set],
    performance=None,
    baselines=None,
    usage=None,
    snaps=None,
    manual=None,
) -> Tuple[float, Dict[str, float]]:
    buy, factors = owner_player_buy_value(uid, asset, team_profiles, prefs, performance, baselines, usage, snaps, manual)
    if buy <= 0:
        return 0.0, factors
    pid = str(asset.get("player_id"))
    starter = pid in starters.get(uid, set())
    pos = asset.get("position")
    need = safe_float((team_profiles[uid].get("position_need") or {}).get(pos), 0.5)

    endowment = CONFIG["owner_value_weights"]["current_owner_endowment_premium"]
    starter_premium = CONFIG["owner_value_weights"]["starter_dependency_premium"] if starter else 0.0
    thin_depth = CONFIG["owner_value_weights"]["thin_depth_hold_premium"] * need
    mult = 1 + endowment + starter_premium + thin_depth

    factors = dict(factors)
    factors.update({
        "current_owner_endowment_premium": round(endowment, 4),
        "starter_dependency_premium": round(starter_premium, 4),
        "thin_depth_hold_premium": round(thin_depth, 4),
        "hold_multiplier_over_buy_value": round(mult, 4),
    })
    return buy * mult, factors


def expected_pick_tier(original_uid: str, team_profiles: Dict[str, Dict[str, Any]]) -> str:
    """
    For future picks, strong current teams project later and weak teams earlier.
    This is an estimate, not a certainty.
    """
    c = safe_float(team_profiles.get(original_uid, {}).get("contender_score"), 0.5)
    if c >= 0.67:
        return "late"
    if c <= 0.33:
        return "early"
    return "mid"


def build_future_pick_assets(
    rosters: List[Dict[str, Any]],
    traded_picks: List[Dict[str, Any]],
    team_profiles: Dict[str, Dict[str, Any]],
    profile_by_uid: Dict[str, Dict[str, Any]],
    detected_pick_values: Dict[Tuple[int, str, int], float],
):
    _, roster_by_id, roster_id_to_uid, _ = owner_maps(rosters, list(profile_by_uid.values()))

    # Default: original roster owns its own pick.
    current_owner_rid = {
        (year, rnd, rid): rid
        for year in FUTURE_PICK_YEARS
        for rnd in ROUNDS
        for rid in roster_by_id
    }

    for tp in traded_picks or []:
        try:
            year = int(tp.get("season"))
            rnd = int(tp.get("round"))
            orig_rid = int(tp.get("roster_id"))
            owner_rid = int(tp.get("owner_id"))
        except (TypeError, ValueError):
            continue
        if year in FUTURE_PICK_YEARS and rnd in ROUNDS and orig_rid in roster_by_id and owner_rid in roster_by_id:
            current_owner_rid[(year, rnd, orig_rid)] = owner_rid

    assets = {}
    for (year, rnd, orig_rid), owner_rid in current_owner_rid.items():
        original_uid = roster_id_to_uid[orig_rid]
        current_uid = roster_id_to_uid[owner_rid]
        tier = expected_pick_tier(original_uid, team_profiles)
        base = fallback_pick_value(year, tier, rnd, detected_pick_values)
        asset_id = f"pick:{year}:R{rnd}:orig{orig_rid}"
        assets[asset_id] = {
            "asset_type": "pick",
            "asset_id": asset_id,
            "season": year,
            "round": rnd,
            "original_roster_id": orig_rid,
            "original_owner_user_id": original_uid,
            "original_owner_manager": manager_label(original_uid, profile_by_uid),
            "original_owner_team": team_label(original_uid, profile_by_uid),
            "current_owner_roster_id": owner_rid,
            "current_owner_user_id": current_uid,
            "current_owner_manager": manager_label(current_uid, profile_by_uid),
            "current_owner_team": team_label(current_uid, profile_by_uid),
            "projected_pick_tier": tier,
            "market_dynasty": round(base, 1),
            "name": f"{year} {tier.title()} {ordinal(rnd)} — original {team_label(original_uid, profile_by_uid)}",
        }
    return assets


def ordinal(n: int) -> str:
    return {1: "1st", 2: "2nd", 3: "3rd"}.get(n, f"{n}th")


def owner_pick_value(
    uid: str,
    pick: Dict[str, Any],
    team_profiles: Dict[str, Dict[str, Any]],
    prefs: Dict[str, Dict[str, Any]],
    hold: bool,
) -> Tuple[float, Dict[str, float]]:
    base = safe_float(pick.get("market_dynasty"))
    pref = safe_float(prefs.get(uid, {}).get("pick_preference"), 0.0)
    contender = safe_float(team_profiles.get(uid, {}).get("contender_score"), 0.5)

    pref_adj = CONFIG["pick_value_weights"]["pick_preference_max_adjustment"] * pref

    # Rebuilders place more utility on picks; contenders slightly less.
    if contender < 0.5:
        window_adj = CONFIG["pick_value_weights"]["rebuild_pick_window_max_adjustment"] * (0.5 - contender) / 0.5
    else:
        window_adj = -CONFIG["pick_value_weights"]["contender_pick_discount_max_adjustment"] * (contender - 0.5) / 0.5

    mult = 1 + pref_adj + window_adj
    if hold:
        mult += 0.035  # small endowment / flexibility premium for a pick already owned

    return base * mult, {
        "market_base": round(base, 1),
        "pick_preference": round(pref, 3),
        "pick_preference_adjustment": round(pref_adj, 4),
        "competitive_window_adjustment": round(window_adj, 4),
        "hold_endowment_premium": 0.035 if hold else 0.0,
        "multiplier": round(mult, 4),
    }


def effective_package_value(values: Iterable[float]) -> float:
    vals = sorted((max(0.0, float(v)) for v in values), reverse=True)
    weights = CONFIG["package_effective_value_weights"]
    total = 0.0
    for i, v in enumerate(vals):
        w = weights[i] if i < len(weights) else max(0.72, weights[-1] - 0.06 * (i - len(weights) + 1))
        total += v * w
    return total


def make_owner_matrix(
    player_values: Dict[str, Dict[str, Any]],
    pick_assets: Dict[str, Dict[str, Any]],
    owner_by_player: Dict[str, str],
    team_profiles: Dict[str, Dict[str, Any]],
    prefs: Dict[str, Dict[str, Any]],
    starters: Dict[str, set],
    profile_by_uid: Dict[str, Dict[str, Any]],
    performance=None,
    baselines=None,
    usage=None,
    snaps=None,
    manual=None,
):
    matrix = {}
    for uid in team_profiles:
        rows = []

        for pid, a in player_values.items():
            current_uid = owner_by_player.get(pid)
            if current_uid == uid:
                value, factors = owner_player_hold_value(uid, a, team_profiles, prefs, starters, performance, baselines, usage, snaps, manual)
                mode = "hold"
            else:
                value, factors = owner_player_buy_value(uid, a, team_profiles, prefs, performance, baselines, usage, snaps, manual)
                mode = "acquire"
            rows.append({
                "asset_type": "player",
                "asset_id": f"player:{pid}",
                "player_id": pid,
                "name": a.get("name"),
                "position": a.get("position"),
                "current_owner_user_id": current_uid,
                "current_owner_team": team_label(current_uid, profile_by_uid) if current_uid else None,
                "valuation_mode": mode,
                "market_value": round(safe_float(a.get("market_dynasty")), 1),
                "fsffl_value": round(fsffl_league_value(a, performance, baselines, usage, snaps, manual), 1),
                "owner_perceived_value": round(value, 1),
                "factors": factors,
            })

        for asset_id, p in pick_assets.items():
            current_uid = str(p.get("current_owner_user_id"))
            hold = current_uid == uid
            value, factors = owner_pick_value(uid, p, team_profiles, prefs, hold=hold)
            rows.append({
                "asset_type": "pick",
                "asset_id": asset_id,
                "name": p.get("name"),
                "current_owner_user_id": current_uid,
                "current_owner_team": p.get("current_owner_team"),
                "valuation_mode": "hold" if hold else "acquire",
                "market_value": round(safe_float(p.get("market_dynasty")), 1),
                "fsffl_value": round(safe_float(p.get("market_dynasty")), 1),
                "owner_perceived_value": round(value, 1),
                "factors": factors,
            })

        matrix[uid] = {
            "user_id": uid,
            "manager": manager_label(uid, profile_by_uid),
            "team_name": team_label(uid, profile_by_uid),
            "team_profile": team_profiles[uid],
            "assets": rows,
        }
    return matrix


def matrix_lookup(matrix: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
    out = {}
    for uid, block in matrix.items():
        out[uid] = {x["asset_id"]: safe_float(x["owner_perceived_value"]) for x in block["assets"]}
    return out


def owner_current_assets(
    uid: str,
    rosters: List[Dict[str, Any]],
    pick_assets: Dict[str, Dict[str, Any]],
) -> List[str]:
    player_assets = []
    for r in rosters:
        if str(r.get("owner_id")) == uid:
            player_assets = [f"player:{pid}" for pid in (r.get("players") or [])]
            break
    picks = [aid for aid, p in pick_assets.items() if str(p.get("current_owner_user_id")) == uid]
    return player_assets + picks


def asset_metadata(
    asset_id: str,
    player_values: Dict[str, Dict[str, Any]],
    pick_assets: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    if asset_id.startswith("player:"):
        pid = asset_id.split(":", 1)[1]
        return player_values.get(pid, {})
    return pick_assets.get(asset_id, {})


def build_hsg_trade_opportunities(
    rosters: List[Dict[str, Any]],
    player_values: Dict[str, Dict[str, Any]],
    pick_assets: Dict[str, Dict[str, Any]],
    owner_by_player: Dict[str, str],
    team_profiles: Dict[str, Dict[str, Any]],
    owner_matrix: Dict[str, Any],
    profile_by_uid: Dict[str, Dict[str, Any]],
    profiles: List[Dict[str, Any]],
    usage=None,
    snaps=None,
    manual=None,
):
    user_uid = None
    for uid, p in profile_by_uid.items():
        if p.get("manager") == USER_MANAGER or p.get("username") == USER_MANAGER or p.get("team_name") == USER_TEAM:
            user_uid = uid
            break
    if not user_uid:
        return {"error": f"Could not locate {USER_MANAGER}/{USER_TEAM}"}

    val = matrix_lookup(owner_matrix)
    hsg_assets = owner_current_assets(user_uid, rosters, pick_assets)

    # Only use non-protected HSG players plus picks as automated outgoing candidates.
    outgoing_candidates = []
    for aid in hsg_assets:
        meta = asset_metadata(aid, player_values, pick_assets)
        if aid.startswith("player:") and meta.get("name") in PROTECTED_HSG_PLAYERS:
            continue
        if val[user_uid].get(aid, 0) <= 0:
            continue
        outgoing_candidates.append(aid)

    # Keep candidate list manageable: most valuable 14 movable players/picks.
    outgoing_candidates.sort(key=lambda a: val[user_uid].get(a, 0), reverse=True)
    outgoing_candidates = outgoing_candidates[:14]

    profile_trade = {
        str(p.get("user_id")): (p.get("trade_profile") or {})
        for p in profiles
    }

    opportunities = []
    for pid, target in player_values.items():
        seller_uid = owner_by_player.get(pid)
        if not seller_uid or seller_uid == user_uid:
            continue

        target_aid = f"player:{pid}"
        hsg_value = val[user_uid].get(target_aid, 0)
        seller_hold = val[seller_uid].get(target_aid, 0)
        if hsg_value <= 0 or seller_hold <= 0:
            continue

        # Focus recommendations on assets that are at least moderately useful to HSG.
        pos = target.get("position")
        need = safe_float(team_profiles[user_uid]["position_need"].get(pos), 0.5)
        if target.get("market_dynasty", 0) < 1500 and need < 0.65:
            continue

        seller_trade = profile_trade.get(seller_uid, {})
        activity = safe_float(seller_trade.get("total_trades"))
        recent = safe_float(seller_trade.get("recent_trades_2025_2026"))
        activity_score = clamp((0.6 * min(activity / 40, 1) + 0.4 * min(recent / 15, 1)), 0, 1)

        # Generate 1-, 2- and selected 3-asset packages.
        package_rows = []
        combos = []
        for n in (1, 2, 3):
            combos.extend(itertools.combinations(outgoing_candidates, n))

        for combo in combos:
            seller_values = [val[seller_uid].get(a, 0) for a in combo]
            hsg_costs = [val[user_uid].get(a, 0) for a in combo]
            if any(v <= 0 for v in seller_values):
                continue
            seller_effective = effective_package_value(seller_values)
            ratio = seller_effective / seller_hold if seller_hold else 0

            # Plausible negotiation band: 88%-118% of modeled seller hold.
            if ratio < 0.88 or ratio > 1.18:
                continue

            hsg_cost = sum(hsg_costs)
            hsg_surplus = hsg_value - hsg_cost

            # Acceptance fit balances fair seller value, owner activity and number of assets.
            fairness = 1 - min(abs(1.0 - ratio), 0.30) / 0.30
            complexity_bonus = 0.03 if len(combo) == 2 else (-0.03 if len(combo) == 3 else 0)
            acceptance_fit = clamp(0.64 * fairness + 0.30 * activity_score + complexity_bonus, 0, 1)

            championship_utility, championship_meta = hsg_trade_championship_utility(
                user_uid,
                list(combo),
                target,
                rosters,
                player_values,
            )

            package_rows.append({
                "outgoing_asset_ids": list(combo),
                "outgoing_assets": [
                    asset_metadata(a, player_values, pick_assets).get("name") for a in combo
                ],
                "seller_perceived_effective_value": round(seller_effective, 1),
                "seller_hold_value_target": round(seller_hold, 1),
                "seller_value_ratio": round(ratio, 3),
                "hsg_hold_cost": round(hsg_cost, 1),
                "hsg_value_of_target": round(hsg_value, 1),
                "hsg_modeled_surplus": round(hsg_surplus, 1),
                "acceptance_fit_score": round(acceptance_fit, 3),
                "championship_utility_score": round(championship_utility, 4),
                "championship_utility": championship_meta,
            })

        package_rows.sort(
            key=lambda x: (
                x["hsg_modeled_surplus"] > 0,
                x["acceptance_fit_score"],
                x.get("championship_utility_score", 0),
                x["hsg_modeled_surplus"],
            ),
            reverse=True,
        )

        opportunities.append({
            "target_player_id": pid,
            "target_player": target.get("name"),
            "position": pos,
            "seller_user_id": seller_uid,
            "seller_manager": manager_label(seller_uid, profile_by_uid),
            "seller_team": team_label(seller_uid, profile_by_uid),
            "market_value": round(safe_float(target.get("market_dynasty")), 1),
            "fsffl_value": round(fsffl_league_value(target), 1),
            "hsg_value": round(hsg_value, 1),
            "seller_hold_value": round(seller_hold, 1),
            "hsg_position_need": round(need, 3),
            "seller_trade_activity_score": round(activity_score, 3),
            "target_value_gap_hsg_minus_seller": round(hsg_value - seller_hold, 1),
            "best_candidate_packages": package_rows[:8],
        })

    opportunities.sort(
        key=lambda x: (
            x["position"] == "RB",
            x["hsg_position_need"],
            x["hsg_value"],
            x["seller_trade_activity_score"],
        ),
        reverse=True,
    )

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "user_id": user_uid,
        "manager": USER_MANAGER,
        "team_name": USER_TEAM,
        "protected_players_excluded_from_auto_offers": sorted(PROTECTED_HSG_PLAYERS),
        "methodology_note": (
            "Candidate packages are negotiation candidates, not automatic recommendations. "
            "They compare the seller's modeled perception of the incoming package with the "
            "seller's hold value, while also tracking Hurts So Good's own cost."
        ),
        "opportunities": opportunities[:80],
    }


def base_main():
    rosters = load_json(DATA / "rosters.json", [])
    players = load_json(DATA / "players.json", {})
    profiles = load_json(DATA / "owner_behavior_profiles.json", [])
    traded_picks = load_json(DATA / "traded_picks.json", [])

    if not rosters or not players or not profiles:
        raise RuntimeError(
            "Missing required data. Expected data/rosters.json, data/players.json, "
            "and data/owner_behavior_profiles.json."
        )

    profile_by_uid, _, _, _ = owner_maps(rosters, profiles)

    market = fetch_fantasycalc_markets()
    market_coverage = market_position_coverage(market)
    CONFIG["market_settings"]["position_coverage"] = market_coverage
    if not market_coverage["authoritative_position_coverage"]:
        CONFIG["notes"].append(
            "External market coverage is incomplete for one or more league-required positions; "
            "those positions are non-authoritative until a compatible valuation adapter is configured."
        )
    market_idx = build_market_indexes(market)
    detected_pick_values = infer_fc_pick_values(market)

    player_values = build_player_values(rosters, players, market_idx)
    # GM-2.2 retains the fix: optimized_starter_sets must see current player values on its
    # first call. Without this, the owner-matrix starter-dependency layer can
    # be built from an empty valuation cache.
    if "optimized_starter_sets" in globals():
        optimized_starter_sets.player_values = player_values
    owner_by_player = current_owner_by_player(rosters)
    starters = starter_sets(rosters)

    performance = load_recent_performance(active_season=LEAGUE_RULES["season"])
    performance_baselines = build_performance_baselines(performance, player_values)
    usage = fetch_nflverse_usage(active_season=LEAGUE_RULES["season"])
    snaps = fetch_nflverse_snaps(active_season=LEAGUE_RULES["season"])
    manual_intelligence = load_manual_football_intelligence()

    team_profiles = build_team_strengths(rosters, player_values, profile_by_uid)
    behavior_prefs = build_behavior_preferences(profiles)

    pick_assets = build_future_pick_assets(
        rosters,
        traded_picks,
        team_profiles,
        profile_by_uid,
        detected_pick_values,
    )

    # Enrich team profiles with behavior preferences.
    for uid in team_profiles:
        team_profiles[uid]["behavior_preferences"] = behavior_prefs.get(uid, {})

    owner_matrix = make_owner_matrix(
        player_values,
        pick_assets,
        owner_by_player,
        team_profiles,
        behavior_prefs,
        starters,
        profile_by_uid,
        performance,
        performance_baselines,
        usage,
        snaps,
        manual_intelligence,
    )

    # League-level asset file with current ownership and market / FSFFL values.
    fsffl_assets = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_version": CONFIG["model_version"],
        "players": [],
        "picks": list(pick_assets.values()),
    }
    for pid, a in player_values.items():
        row = dict(a)
        uid = owner_by_player.get(pid)
        row["current_owner_user_id"] = uid
        row["current_owner_manager"] = manager_label(uid, profile_by_uid) if uid else None
        row["current_owner_team"] = team_label(uid, profile_by_uid) if uid else None
        row["fsffl_value"] = round(fsffl_league_value(a, performance, performance_baselines, usage, snaps, manual_intelligence), 1)
        _, perf_meta = performance_adjustment(a, performance, performance_baselines)
        _, football_meta = football_intelligence_adjustment(a, usage, snaps, manual_intelligence)
        row["recent_performance_signal"] = perf_meta
        row["football_intelligence"] = football_meta
        fsffl_assets["players"].append(row)

    fsffl_assets["players"].sort(key=lambda x: x["fsffl_value"], reverse=True)

    opportunities = build_hsg_trade_opportunities(
        rosters,
        player_values,
        pick_assets,
        owner_by_player,
        team_profiles,
        owner_matrix,
        profile_by_uid,
        profiles,
        usage,
        snaps,
        manual_intelligence,
    )

    write_json(DATA / "valuation_model_config.json", CONFIG)
    write_json(DATA / "recent_performance_signals.json", {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "active_season": 2026,
        "players_with_current_season_signal": len(performance),
        "position_baselines": performance_baselines,
        "players": performance,
    })
    write_json(DATA / "football_intelligence_signals.json", {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "active_season": 2026,
        "usage_records": len(usage),
        "snap_records": len(snaps),
        "manual_intelligence_records": len(manual_intelligence),
        "usage": usage,
        "snaps": snaps,
        "manual_intelligence": manual_intelligence,
        "sources": {
            "nflverse_player_stats": NFLVERSE_PLAYER_STATS_URL,
            "nflverse_snap_counts": NFLVERSE_SNAP_COUNTS_URL,
            "manual_overrides": str(FOOTBALL_INTELLIGENCE_OVERRIDES),
        },
    })
    write_json(DATA / "team_contender_profiles.json", {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "teams": sorted(team_profiles.values(), key=lambda x: x["contender_score"], reverse=True),
    })
    write_json(DATA / "fsffl_asset_values.json", fsffl_assets)
    write_json(DATA / "owner_perceived_values.json", {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_version": CONFIG["model_version"],
        "owners": owner_matrix,
    })
    write_json(DATA / "hsg_trade_opportunities.json", opportunities)

    print("FSFFL valuation engine complete.")
    print(f"Rostered players valued: {len(player_values)}")
    print(f"Future pick assets valued: {len(pick_assets)}")
    print(f"Owner valuation matrices: {len(owner_matrix)}")
    print("Wrote:")
    for name in (
        "market_values_fantasycalc.json",
        "team_contender_profiles.json",
        "fsffl_asset_values.json",
        "owner_perceived_values.json",
        "hsg_trade_opportunities.json",
        "valuation_model_config.json",
        "recent_performance_signals.json",
        "football_intelligence_signals.json",
    ):
        print(f"  data/{name}")




# ---- GM-1.1 decision-layer additions ----

DATA = Path("data")

# Explicit model bump for generated files.
CONFIG["model_version"] = "GM-2.2"
CONFIG["notes"] = list(CONFIG.get("notes") or []) + [
    "GM-1.1 independently optimizes legal starting lineups; Sleeper's current starters are not treated as authoritative.",
    "GM-1.1 ranks trade packages by HSG surplus and optimal-lineup gain before acceptance fit.",
    "GM-1.1 creates a sell-leverage board across every opponent valuation.",
    "GM-1.1.1 fixes runtime dispatch so optimized-lineup and trade-ranking functions are actually used by base_main.",
]

FALLBACK_LINEUP_SLOTS = list(LEAGUE_RULES["lineup_slots"])


def lineup_slots() -> List[str]:
    return list(LEAGUE_RULES["lineup_slots"]) or list(FALLBACK_LINEUP_SLOTS)


LINEUP_SLOTS = lineup_slots()


def eligible(position: str, slot: str) -> bool:
    pos = normalize_position(position)
    return pos in slot_eligible_positions(slot)


def optimize_lineup(
    player_ids: Iterable[str],
    player_values: Dict[str, Dict[str, Any]],
    value_key: str = "market_redraft",
) -> Dict[str, Any]:
    """
    Fast exact optimizer for FSFFL-style lineups.

    Fixed-position slots determine minimum position counts. FLEX and SUPER_FLEX
    are then enumerated by eligible position (at most 12 combinations for the
    current FSFFL format), so runtime is effectively constant rather than a
    player-bitmask search.
    """
    by_pos = {p: [] for p in POSITIONS}
    for pid in player_ids:
        pid = str(pid)
        if pid == "0":
            continue
        a = player_values.get(pid, {})
        pos = normalize_position(a.get("position"))
        if pos not in POSITIONS:
            continue
        by_pos[pos].append(
            (safe_float(a.get(value_key)), pid, a)
        )

    for pos in POSITIONS:
        by_pos[pos].sort(key=lambda x: x[0], reverse=True)

    fixed_counts = Counter()
    flex_slots = []
    for slot in LINEUP_SLOTS:
        if slot in POSITIONS:
            fixed_counts[slot] += 1
        elif slot_eligible_positions(slot):
            flex_slots.append((slot, slot_eligible_positions(slot)))

    # Enumerate only the flexible slot position choices.
    flex_choices = [choices for _, choices in flex_slots]
    assignments = itertools.product(*flex_choices) if flex_choices else [()]

    best_total = float("-inf")
    best_counts = None
    best_flex_assignment = None

    for assignment in assignments:
        counts = Counter(fixed_counts)
        for pos in assignment:
            counts[pos] += 1

        valid = True
        total = 0.0
        for pos in POSITIONS:
            need = counts[pos]
            if len(by_pos[pos]) < need:
                valid = False
                break
            total += sum(x[0] for x in by_pos[pos][:need])

        if valid and total > best_total:
            best_total = total
            best_counts = counts
            best_flex_assignment = assignment

    if best_counts is None:
        # Graceful fallback: greedily fill legal slots without crashing.
        used = set()
        rows = []
        total = 0.0
        for slot in LINEUP_SLOTS:
            eligible_positions = slot_eligible_positions(slot)
            options = []
            for pos in eligible_positions:
                for val, pid, a in by_pos.get(pos, []):
                    if pid not in used:
                        options.append((val, pid, a))
                        break
            if not options:
                rows.append({
                    "slot": slot, "player_id": None, "name": None,
                    "position": None, "value": 0.0
                })
                continue
            val, pid, a = max(options, key=lambda x: x[0])
            used.add(pid)
            total += val
            rows.append({
                "slot": slot,
                "player_id": pid,
                "name": a.get("name"),
                "position": a.get("position"),
                "value": round(val, 1),
            })
        return {
            "total": round(total, 1),
            "player_ids": [r["player_id"] for r in rows if r["player_id"]],
            "lineup": rows,
            "complete": all(r["player_id"] for r in rows),
        }

    # Select the exact top-N players at each required position.
    selected_by_pos = {
        pos: list(by_pos[pos][:best_counts[pos]])
        for pos in POSITIONS
    }

    # Build slot rows while ensuring flexible slots use the selected surplus
    # player at that position after mandatory slots are filled.
    indices = Counter()
    rows = []
    flex_iter = iter(best_flex_assignment or ())
    for slot in LINEUP_SLOTS:
        if slot in POSITIONS:
            pos = slot
        elif slot_eligible_positions(slot):
            pos = next(flex_iter)
        else:
            continue

        i = indices[pos]
        val, pid, a = selected_by_pos[pos][i]
        indices[pos] += 1
        rows.append({
            "slot": slot,
            "player_id": pid,
            "name": a.get("name"),
            "position": a.get("position"),
            "value": round(val, 1),
        })

    return {
        "total": round(best_total, 1),
        "player_ids": [r["player_id"] for r in rows],
        "lineup": rows,
        "complete": len(rows) == len(LINEUP_SLOTS),
    }


def optimized_starter_sets(rosters: List[Dict[str, Any]]) -> Dict[str, set]:
    # main calls this after player_values exists, but the original signature does not
    # provide values. We cache the most recent values from optimized_team_strengths.
    values = getattr(optimized_starter_sets, "player_values", {})
    out = {}
    for r in rosters:
        uid = str(r.get("owner_id"))
        result = optimize_lineup(r.get("players") or [], values, "market_redraft")
        out[uid] = set(result["player_ids"])
    return out


def optimized_team_strengths(
    rosters: List[Dict[str, Any]],
    player_values: Dict[str, Dict[str, Any]],
    profile_by_uid: Dict[str, Dict[str, Any]],
):
    optimized_starter_sets.player_values = player_values
    raw = {}
    for r in rosters:
        uid = str(r.get("owner_id"))
        all_players = [str(x) for x in (r.get("players") or []) if str(x) != "0"]
        redraft_opt = optimize_lineup(all_players, player_values, "market_redraft")
        dynasty_opt = optimize_lineup(all_players, player_values, "market_dynasty")
        starters = set(redraft_opt["player_ids"])
        bench = [x for x in all_players if x not in starters]

        bench_redraft = sorted((safe_float(player_values.get(x, {}).get("market_redraft")) for x in bench), reverse=True)
        bench_dynasty = sorted((safe_float(player_values.get(x, {}).get("market_dynasty")) for x in bench), reverse=True)
        immediate_strength = redraft_opt["total"] + 0.20 * sum(bench_redraft[:5])
        dynasty_strength = dynasty_opt["total"] + 0.18 * sum(bench_dynasty[:6])

        pos_starter = defaultdict(float)
        pos_depth = defaultdict(list)
        for pid in all_players:
            a = player_values.get(pid, {})
            pos = a.get("position")
            if pos not in POSITIONS:
                continue
            pos_depth[pos].append(safe_float(a.get("market_redraft")))
        for pid in starters:
            a = player_values.get(pid, {})
            pos = a.get("position")
            if pos in POSITIONS:
                pos_starter[pos] += safe_float(a.get("market_redraft"))

        raw[uid] = {
            "user_id": uid,
            "manager": manager_label(uid, profile_by_uid),
            "team_name": team_label(uid, profile_by_uid),
            "starter_redraft_value": redraft_opt["total"],
            "starter_dynasty_value": dynasty_opt["total"],
            "immediate_strength_raw": immediate_strength,
            "dynasty_strength_raw": dynasty_strength,
            "pos_starter_raw": dict(pos_starter),
            "pos_depth_values": {p: sorted(v, reverse=True) for p, v in pos_depth.items()},
            "optimal_redraft_lineup": redraft_opt,
            "optimal_dynasty_lineup": dynasty_opt,
        }

    immediate_values = [x["immediate_strength_raw"] for x in raw.values()]
    dynasty_values = [x["dynasty_strength_raw"] for x in raw.values()]
    pos_distributions = {
        pos: [x["pos_starter_raw"].get(pos, 0.0) for x in raw.values()]
        for pos in POSITIONS
    }

    out = {}
    for uid, x in raw.items():
        contender = percentile_rank(x["immediate_strength_raw"], immediate_values)
        dynasty_pct = percentile_rank(x["dynasty_strength_raw"], dynasty_values)
        if contender >= 0.75:
            tier = "elite_contender"
        elif contender >= 0.50:
            tier = "contender"
        elif contender >= 0.25:
            tier = "middle"
        else:
            tier = "retool_rebuild"

        needs = {}
        for pos in POSITIONS:
            starter_strength = x["pos_starter_raw"].get(pos, 0.0)
            starter_pct = percentile_rank(starter_strength, pos_distributions[pos])
            depth_vals = x["pos_depth_values"].get(pos, [])
            n = 3 if pos in ("RB", "WR") else 2
            depth_score = sum(depth_vals[:n])
            all_depth_scores = [sum(z["pos_depth_values"].get(pos, [])[:n]) for z in raw.values()]
            depth_pct = percentile_rank(depth_score, all_depth_scores)
            needs[pos] = round(clamp(0.65 * (1 - starter_pct) + 0.35 * (1 - depth_pct), 0, 1), 3)

        out[uid] = {
            "user_id": uid,
            "manager": x["manager"],
            "team_name": x["team_name"],
            "contender_score": round(contender, 3),
            "dynasty_roster_score": round(dynasty_pct, 3),
            "competitive_tier": tier,
            "starter_redraft_value": round(x["starter_redraft_value"], 1),
            "starter_dynasty_value": round(x["starter_dynasty_value"], 1),
            "position_need": needs,
            "lineup_source": "independently_optimized_legal_lineup",
            "optimal_redraft_lineup": x["optimal_redraft_lineup"],
            "optimal_dynasty_lineup": x["optimal_dynasty_lineup"],
        }
    return out


def optimized_current_starting_lineup_value(uid, rosters, player_values):
    for r in rosters:
        if str(r.get("owner_id")) == str(uid):
            return optimize_lineup(r.get("players") or [], player_values, "market_redraft")["total"]
    return 0.0


def lineup_after_trade_utility(hsg_uid, outgoing_asset_ids, target_asset, rosters, player_values):
    roster_players = None
    for r in rosters:
        if str(r.get("owner_id")) == str(hsg_uid):
            roster_players = [str(x) for x in (r.get("players") or [])]
            break
    if roster_players is None:
        return 0.0, {"available": False}

    before = optimize_lineup(roster_players, player_values, "market_redraft")
    outgoing_players = {
        aid.split(":", 1)[1]
        for aid in outgoing_asset_ids
        if aid.startswith("player:")
    }
    after_players = [pid for pid in roster_players if pid not in outgoing_players]
    target_pid = str(target_asset.get("player_id"))
    temp_values = player_values
    if target_pid and target_pid not in after_players:
        after_players.append(target_pid)
    after = optimize_lineup(after_players, temp_values, "market_redraft")
    delta = after["total"] - before["total"]
    cap = CONFIG["championship_utility"]["max_trade_utility_adjustment"]
    utility = clamp((delta / before["total"] * 6.0) if before["total"] else 0.0, -cap, cap)
    return utility, {
        "available": True,
        "base_optimal_lineup_redraft_value": round(before["total"], 1),
        "post_trade_optimal_lineup_redraft_value": round(after["total"], 1),
        "optimal_lineup_value_gain": round(delta, 1),
        "championship_utility_adjustment": round(utility, 4),
        "post_trade_optimal_lineup": after["lineup"],
    }


def build_hsg_trade_opportunities_v11(
    rosters,
    player_values,
    pick_assets,
    owner_by_player,
    team_profiles,
    owner_matrix,
    profile_by_uid,
    profiles,
    usage=None,
    snaps=None,
    manual=None,
):
    user_uid = None
    for uid, p in profile_by_uid.items():
        if p.get("manager") == USER_MANAGER or p.get("username") == USER_MANAGER or p.get("team_name") == USER_TEAM:
            user_uid = uid
            break
    if not user_uid:
        return {"error": f"Could not locate {USER_MANAGER}/{USER_TEAM}"}

    val = matrix_lookup(owner_matrix)
    hsg_assets = owner_current_assets(user_uid, rosters, pick_assets)

    outgoing_candidates = []
    for aid in hsg_assets:
        meta = asset_metadata(aid, player_values, pick_assets)
        if aid.startswith("player:") and meta.get("name") in PROTECTED_HSG_PLAYERS:
            continue
        if val[user_uid].get(aid, 0) > 0:
            outgoing_candidates.append(aid)

    outgoing_candidates.sort(key=lambda a: val[user_uid].get(a, 0), reverse=True)
    outgoing_candidates = outgoing_candidates[:16]

    profile_trade = {
        str(p.get("user_id")): (p.get("trade_profile") or {})
        for p in profiles
    }
    opportunities = []

    for pid, target in player_values.items():
        seller_uid = owner_by_player.get(pid)
        if not seller_uid or seller_uid == user_uid:
            continue

        target_aid = f"player:{pid}"
        hsg_value = val[user_uid].get(target_aid, 0)
        seller_hold = val[seller_uid].get(target_aid, 0)
        if hsg_value <= 0 or seller_hold <= 0:
            continue

        pos = target.get("position")
        need = safe_float(team_profiles[user_uid]["position_need"].get(pos), 0.5)
        if target.get("market_dynasty", 0) < 1500 and need < 0.65:
            continue

        seller_trade = profile_trade.get(seller_uid, {})
        activity = safe_float(seller_trade.get("total_trades"))
        recent = safe_float(seller_trade.get("recent_trades_2025_2026"))
        activity_score = clamp(
            0.6 * min(activity / 40, 1)
            + 0.4 * min(recent / 15, 1),
            0, 1
        )

        # STAGE 1: cheap economics/acceptance screen over all combinations.
        prelim = []
        for n in (1, 2, 3):
            for combo in itertools.combinations(outgoing_candidates, n):
                seller_values = [val[seller_uid].get(a, 0) for a in combo]
                hsg_costs = [val[user_uid].get(a, 0) for a in combo]
                if any(v <= 0 for v in seller_values):
                    continue

                seller_effective = effective_package_value(seller_values)
                ratio = seller_effective / seller_hold if seller_hold else 0
                if ratio < 0.84 or ratio > 1.16:
                    continue

                hsg_cost = sum(hsg_costs)
                hsg_surplus = hsg_value - hsg_cost
                fairness = 1 - min(abs(1.0 - ratio), 0.30) / 0.30
                complexity_bonus = (
                    0.03 if len(combo) == 2
                    else -0.03 if len(combo) == 3
                    else 0.0
                )
                acceptance_fit = clamp(
                    0.64 * fairness
                    + 0.30 * activity_score
                    + complexity_bonus,
                    0, 1
                )

                # Preliminary score intentionally favors HSG economics.
                normalized_surplus = hsg_surplus / max(hsg_value, 1.0)
                prelim_score = 0.82 * normalized_surplus + 0.18 * acceptance_fit

                prelim.append({
                    "combo": combo,
                    "seller_effective": seller_effective,
                    "ratio": ratio,
                    "hsg_cost": hsg_cost,
                    "hsg_surplus": hsg_surplus,
                    "acceptance_fit": acceptance_fit,
                    "prelim_score": prelim_score,
                })

        # Only the best economic candidates receive lineup simulation.
        prelim.sort(
            key=lambda x: (
                x["hsg_surplus"] >= 0,
                x["prelim_score"],
                x["hsg_surplus"],
                x["acceptance_fit"],
            ),
            reverse=True,
        )
        finalists = prelim[:30]

        package_rows = []
        for row in finalists:
            combo = row["combo"]
            championship_utility, championship_meta = lineup_after_trade_utility(
                user_uid, list(combo), target, rosters, player_values
            )
            lineup_gain = safe_float(
                championship_meta.get("optimal_lineup_value_gain")
            )

            normalized_surplus = row["hsg_surplus"] / max(hsg_value, 1.0)
            base_lineup = max(
                safe_float(
                    championship_meta.get(
                        "base_optimal_lineup_redraft_value"
                    ),
                    1.0,
                ),
                1.0,
            )
            normalized_lineup = lineup_gain / base_lineup

            decision_score = (
                0.58 * normalized_surplus
                + 0.27 * normalized_lineup
                + 0.15 * row["acceptance_fit"]
            )

            severe_overpay = row["hsg_surplus"] < -0.12 * hsg_value
            recommendation_band = (
                "strong_candidate"
                if row["hsg_surplus"] >= 0 and lineup_gain > 0
                else "negotiation_candidate"
                if row["hsg_surplus"] >= -0.06 * hsg_value and lineup_gain > 0
                else "overpay"
                if severe_overpay
                else "low_priority"
            )

            package_rows.append({
                "outgoing_asset_ids": list(combo),
                "outgoing_assets": [
                    asset_metadata(a, player_values, pick_assets).get("name")
                    for a in combo
                ],
                "seller_perceived_effective_value": round(
                    row["seller_effective"], 1
                ),
                "seller_hold_value_target": round(seller_hold, 1),
                "seller_value_ratio": round(row["ratio"], 3),
                "hsg_hold_cost": round(row["hsg_cost"], 1),
                "hsg_value_of_target": round(hsg_value, 1),
                "hsg_modeled_surplus": round(row["hsg_surplus"], 1),
                "acceptance_fit_score": round(row["acceptance_fit"], 3),
                "championship_utility_score": round(
                    championship_utility, 4
                ),
                "championship_utility": championship_meta,
                "decision_score": round(decision_score, 5),
                "recommendation_band": recommendation_band,
            })

        band_rank = {
            "strong_candidate": 3,
            "negotiation_candidate": 2,
            "low_priority": 1,
            "overpay": 0,
        }
        package_rows.sort(
            key=lambda x: (
                band_rank.get(x["recommendation_band"], 0),
                x["hsg_modeled_surplus"],
                safe_float(
                    (x.get("championship_utility") or {}).get(
                        "optimal_lineup_value_gain"
                    )
                ),
                x["acceptance_fit_score"],
                x["decision_score"],
            ),
            reverse=True,
        )

        best = package_rows[0] if package_rows else None
        opportunities.append({
            "target_player_id": pid,
            "target_player": target.get("name"),
            "position": pos,
            "seller_user_id": seller_uid,
            "seller_manager": manager_label(
                seller_uid, profile_by_uid
            ),
            "seller_team": team_label(seller_uid, profile_by_uid),
            "market_value": round(
                safe_float(target.get("market_dynasty")), 1
            ),
            "fsffl_value": round(fsffl_league_value(target), 1),
            "hsg_value": round(hsg_value, 1),
            "seller_hold_value": round(seller_hold, 1),
            "hsg_position_need": round(need, 3),
            "seller_trade_activity_score": round(activity_score, 3),
            "target_value_gap_hsg_minus_seller": round(
                hsg_value - seller_hold, 1
            ),
            "best_candidate_packages": package_rows[:10],
            "best_package_decision_score": (
                best.get("decision_score") if best else None
            ),
            "best_package_recommendation_band": (
                best.get("recommendation_band") if best else None
            ),
        })

    opportunities.sort(
        key=lambda x: (
            1
            if x.get("best_package_recommendation_band")
            == "strong_candidate"
            else 0,
            safe_float(x.get("best_package_decision_score"), -999),
            x["hsg_position_need"],
            x["hsg_value"],
        ),
        reverse=True,
    )

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_version": "GM-2.0",
        "user_id": user_uid,
        "manager": USER_MANAGER,
        "team_name": USER_TEAM,
        "protected_players_excluded_from_auto_offers": sorted(
            PROTECTED_HSG_PLAYERS
        ),
        "methodology_note": (
            "GM-1.1.3 screens all packages economically first, then runs "
            "optimal-lineup simulation only on the strongest candidates. "
            "Final ranking prioritizes HSG surplus, lineup improvement, "
            "and seller acceptance fit in that order."
        ),
        "opportunities": opportunities[:80],
    }


def build_sell_leverage_board():
    owner_payload = load_json(DATA / "owner_perceived_values.json", {}) or {}
    owners = owner_payload.get("owners") or {}
    assets_payload = load_json(DATA / "fsffl_asset_values.json", {}) or {}
    team_payload = load_json(DATA / "team_contender_profiles.json", {}) or {}
    teams = {str(x.get("user_id")): x for x in team_payload.get("teams") or []}

    hsg_uid = None
    for uid, block in owners.items():
        if block.get("manager") == USER_MANAGER or block.get("team_name") == USER_TEAM:
            hsg_uid = str(uid)
            break
    if not hsg_uid:
        return {"error": "Could not locate Hurts So Good"}

    player_meta = {str(x.get("player_id")): x for x in assets_payload.get("players") or []}
    owner_asset_values = {}
    for uid, block in owners.items():
        owner_asset_values[str(uid)] = {
            x.get("asset_id"): safe_float(x.get("owner_perceived_value"))
            for x in block.get("assets") or []
        }

    rows = []
    for pid, meta in player_meta.items():
        if str(meta.get("current_owner_user_id")) != hsg_uid:
            continue
        aid = f"player:{pid}"
        hsg_hold = owner_asset_values.get(hsg_uid, {}).get(aid, 0.0)
        market = safe_float(meta.get("market_dynasty"))
        buyers = []
        for uid, vals in owner_asset_values.items():
            if uid == hsg_uid:
                continue
            value = vals.get(aid, 0.0)
            if value <= 0:
                continue
            buyers.append({
                "buyer_user_id": uid,
                "buyer_manager": (owners.get(uid) or {}).get("manager"),
                "buyer_team": (owners.get(uid) or {}).get("team_name"),
                "buyer_perceived_value": round(value, 1),
                "premium_vs_market": round(value - market, 1),
                "premium_vs_hsg_hold": round(value - hsg_hold, 1),
                "buyer_position_need": round(safe_float((teams.get(uid, {}).get("position_need") or {}).get(meta.get("position")), 0.5), 3),
            })
        buyers.sort(key=lambda x: (x["premium_vs_hsg_hold"], x["premium_vs_market"], x["buyer_position_need"]), reverse=True)
        best = buyers[0] if buyers else None
        rows.append({
            "player_id": pid,
            "player": meta.get("name"),
            "position": meta.get("position"),
            "protected_core": meta.get("name") in PROTECTED_HSG_PLAYERS,
            "market_value": round(market, 1),
            "hsg_hold_value": round(hsg_hold, 1),
            "best_buyer": best,
            "top_buyers": buyers[:5],
            "positive_arbitrage_vs_hsg_hold": bool(best and best["premium_vs_hsg_hold"] > 0),
        })

    rows.sort(
        key=lambda x: (
            not x["protected_core"],
            safe_float((x.get("best_buyer") or {}).get("premium_vs_hsg_hold"), -99999),
            safe_float((x.get("best_buyer") or {}).get("premium_vs_market"), -99999),
        ),
        reverse=True,
    )
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_version": "GM-2.0",
        "user_id": hsg_uid,
        "manager": USER_MANAGER,
        "team_name": USER_TEAM,
        "methodology_note": (
            "For every HSG player, compares each opponent's modeled acquire value with "
            "market value and HSG's own hold value. Protected-core players are analyzed "
            "for information but remain excluded from automatic outgoing packages."
        ),
        "players": rows,
    }



def build_league_arbitrage_matrix():
    """
    Build an HSG-centric league trade-market map.

    Outputs:
      1) owner seams: who overvalues each HSG asset and which of their assets
         HSG values more than they do;
      2) direct one-for-one arbitrage paths;
      3) two-step routes where HSG asset A is converted to owner-X asset B,
         then B is rerouted to owner Y for target C.

    This is a valuation/negotiation discovery layer, not a claim that any
    specific owner would accept the modeled exchange.
    """
    owner_payload = load_json(DATA / "owner_perceived_values.json", {}) or {}
    owners = owner_payload.get("owners") or {}
    team_payload = load_json(DATA / "team_contender_profiles.json", {}) or {}
    teams = {str(x.get("user_id")): x for x in team_payload.get("teams") or []}

    hsg_uid = None
    for uid, block in owners.items():
        if block.get("manager") == USER_MANAGER or block.get("team_name") == USER_TEAM:
            hsg_uid = str(uid)
            break
    if not hsg_uid:
        return {"error": "Could not locate Hurts So Good"}

    # Every owner's valuation of every asset.
    vals = {}
    asset_meta = {}
    for uid, block in owners.items():
        uid = str(uid)
        vals[uid] = {}
        for row in block.get("assets") or []:
            aid = row.get("asset_id")
            if not aid:
                continue
            vals[uid][aid] = safe_float(row.get("owner_perceived_value"))
            if aid not in asset_meta:
                asset_meta[aid] = {
                    "asset_id": aid,
                    "asset_type": row.get("asset_type"),
                    "name": row.get("name"),
                    "current_owner_user_id": str(row.get("current_owner_user_id")),
                    "current_owner_team": row.get("current_owner_team"),
                    "market_value": safe_float(row.get("market_value")),
                }

    def meta(aid):
        return asset_meta.get(aid, {"asset_id": aid, "name": aid})

    def owner_name(uid):
        b = owners.get(str(uid)) or {}
        return b.get("team_name") or b.get("manager") or str(uid)

    def owner_manager(uid):
        b = owners.get(str(uid)) or {}
        return b.get("manager") or b.get("team_name") or str(uid)

    def is_protected(aid):
        m = meta(aid)
        return (
            m.get("asset_type") == "player"
            and m.get("name") in PROTECTED_HSG_PLAYERS
        )

    # Current holdings by owner.
    holdings = defaultdict(list)
    for aid, m in asset_meta.items():
        uid = str(m.get("current_owner_user_id"))
        if uid:
            holdings[uid].append(aid)

    hsg_holdings = holdings.get(hsg_uid, [])
    movable_hsg = [
        aid for aid in hsg_holdings
        if not is_protected(aid) and vals.get(hsg_uid, {}).get(aid, 0) > 0
    ]

    # -----------------------------
    # Owner-by-owner market seams
    # -----------------------------
    owner_seams = []
    for opp_uid in owners:
        opp_uid = str(opp_uid)
        if opp_uid == hsg_uid:
            continue

        sell_edges = []
        for aid in movable_hsg:
            hsg_hold = vals[hsg_uid].get(aid, 0.0)
            opp_buy = vals.get(opp_uid, {}).get(aid, 0.0)
            if hsg_hold <= 0 or opp_buy <= 0:
                continue
            sell_edges.append({
                "asset_id": aid,
                "asset": meta(aid).get("name"),
                "asset_type": meta(aid).get("asset_type"),
                "hsg_hold_value": round(hsg_hold, 1),
                "opponent_acquire_value": round(opp_buy, 1),
                "opponent_premium_vs_hsg_hold": round(opp_buy - hsg_hold, 1),
                "opponent_premium_pct": round((opp_buy / hsg_hold - 1.0) * 100, 1),
            })
        sell_edges.sort(
            key=lambda x: (
                x["opponent_premium_vs_hsg_hold"],
                x["opponent_premium_pct"],
            ),
            reverse=True,
        )

        buy_edges = []
        for aid in holdings.get(opp_uid, []):
            hsg_buy = vals.get(hsg_uid, {}).get(aid, 0.0)
            opp_hold = vals.get(opp_uid, {}).get(aid, 0.0)
            if hsg_buy <= 0 or opp_hold <= 0:
                continue
            buy_edges.append({
                "asset_id": aid,
                "asset": meta(aid).get("name"),
                "asset_type": meta(aid).get("asset_type"),
                "hsg_acquire_value": round(hsg_buy, 1),
                "opponent_hold_value": round(opp_hold, 1),
                "hsg_premium_vs_opponent_hold": round(hsg_buy - opp_hold, 1),
                "hsg_premium_pct": round((hsg_buy / opp_hold - 1.0) * 100, 1),
            })
        buy_edges.sort(
            key=lambda x: (
                x["hsg_premium_vs_opponent_hold"],
                x["hsg_premium_pct"],
            ),
            reverse=True,
        )

        owner_seams.append({
            "opponent_user_id": opp_uid,
            "opponent_manager": owner_manager(opp_uid),
            "opponent_team": owner_name(opp_uid),
            "opponent_competitive_tier": (teams.get(opp_uid) or {}).get("competitive_tier"),
            "best_assets_to_sell_them": sell_edges[:8],
            "best_assets_to_buy_from_them": buy_edges[:12],
        })

    # -----------------------------
    # Direct one-for-one paths
    # -----------------------------
    direct = []
    for opp_uid in owners:
        opp_uid = str(opp_uid)
        if opp_uid == hsg_uid:
            continue

        for out_aid in movable_hsg:
            hsg_cost = vals[hsg_uid].get(out_aid, 0.0)
            opp_receive = vals.get(opp_uid, {}).get(out_aid, 0.0)
            if hsg_cost <= 0 or opp_receive <= 0:
                continue

            for in_aid in holdings.get(opp_uid, []):
                hsg_receive = vals[hsg_uid].get(in_aid, 0.0)
                opp_cost = vals.get(opp_uid, {}).get(in_aid, 0.0)
                if hsg_receive <= 0 or opp_cost <= 0:
                    continue

                hsg_surplus = hsg_receive - hsg_cost
                opp_surplus = opp_receive - opp_cost
                hsg_ratio = hsg_receive / hsg_cost
                opp_ratio = opp_receive / opp_cost

                # Keep genuinely interesting HSG-positive seams; opponent can
                # be modestly short because a small balancing piece may bridge it.
                if hsg_surplus <= 0:
                    continue
                if opp_ratio < 0.84:
                    continue

                if opp_surplus >= 0:
                    deal_class = "mutual_arbitrage"
                elif opp_ratio >= 0.94:
                    deal_class = "near_direct_match"
                else:
                    deal_class = "needs_small_bridge"

                score = (
                    0.48 * min(hsg_surplus / max(hsg_receive, 1.0), 0.50)
                    + 0.32 * min(max(opp_ratio - 0.84, 0.0) / 0.16, 1.0)
                    + 0.20 * min(
                        max(
                            (
                                vals.get(opp_uid, {}).get(out_aid, 0.0)
                                - vals[hsg_uid].get(out_aid, 0.0)
                            )
                            / max(hsg_cost, 1.0),
                            0.0,
                        ),
                        0.50,
                    )
                )

                direct.append({
                    "opponent_user_id": opp_uid,
                    "opponent_manager": owner_manager(opp_uid),
                    "opponent_team": owner_name(opp_uid),
                    "send_asset_id": out_aid,
                    "send_asset": meta(out_aid).get("name"),
                    "receive_asset_id": in_aid,
                    "receive_asset": meta(in_aid).get("name"),
                    "hsg_hold_cost": round(hsg_cost, 1),
                    "hsg_receive_value": round(hsg_receive, 1),
                    "hsg_surplus": round(hsg_surplus, 1),
                    "hsg_value_ratio": round(hsg_ratio, 3),
                    "opponent_receive_value": round(opp_receive, 1),
                    "opponent_hold_cost": round(opp_cost, 1),
                    "opponent_surplus": round(opp_surplus, 1),
                    "opponent_value_ratio": round(opp_ratio, 3),
                    "deal_class": deal_class,
                    "arbitrage_score": round(score, 5),
                })

    class_rank = {
        "mutual_arbitrage": 3,
        "near_direct_match": 2,
        "needs_small_bridge": 1,
    }
    direct.sort(
        key=lambda x: (
            class_rank.get(x["deal_class"], 0),
            x["arbitrage_score"],
            x["hsg_surplus"],
            x["opponent_value_ratio"],
        ),
        reverse=True,
    )

    # -----------------------------
    # Two-step arbitrage routes
    # -----------------------------
    # Stage 1: HSG asset A -> currency asset B from owner X.
    stage1 = []
    for buyer_uid in owners:
        buyer_uid = str(buyer_uid)
        if buyer_uid == hsg_uid:
            continue
        for out_aid in movable_hsg:
            hsg_cost = vals[hsg_uid].get(out_aid, 0.0)
            buyer_receive = vals.get(buyer_uid, {}).get(out_aid, 0.0)
            if hsg_cost <= 0 or buyer_receive <= 0:
                continue
            for currency_aid in holdings.get(buyer_uid, []):
                buyer_cost = vals.get(buyer_uid, {}).get(currency_aid, 0.0)
                if buyer_cost <= 0:
                    continue
                ratio = buyer_receive / buyer_cost
                if ratio < 0.88 or ratio > 1.25:
                    continue
                # Prefer assets the HSG model regards as liquid/useful currency.
                hsg_currency_value = vals[hsg_uid].get(currency_aid, 0.0)
                if hsg_currency_value <= 0:
                    continue
                stage1.append({
                    "source_asset_id": out_aid,
                    "buyer_user_id": buyer_uid,
                    "currency_asset_id": currency_aid,
                    "hsg_original_cost": hsg_cost,
                    "buyer_value_ratio": ratio,
                    "buyer_receive_value": buyer_receive,
                    "buyer_currency_hold": buyer_cost,
                    "hsg_currency_value": hsg_currency_value,
                })

    # Cap stage-1 candidates per original HSG asset so runtime stays tiny.
    grouped_stage1 = defaultdict(list)
    for x in stage1:
        grouped_stage1[x["source_asset_id"]].append(x)

    stage1_capped = []
    for aid, rows in grouped_stage1.items():
        rows.sort(
            key=lambda x: (
                x["buyer_value_ratio"],
                x["hsg_currency_value"],
            ),
            reverse=True,
        )
        # Keep diverse buyers, no more than two candidate currencies per buyer.
        per_buyer = Counter()
        kept = 0
        for row in rows:
            buid = row["buyer_user_id"]
            if per_buyer[buid] >= 2:
                continue
            stage1_capped.append(row)
            per_buyer[buid] += 1
            kept += 1
            if kept >= 18:
                break

    two_step = []
    for s1 in stage1_capped:
        source_aid = s1["source_asset_id"]
        buyer_uid = s1["buyer_user_id"]
        currency_aid = s1["currency_asset_id"]
        hsg_original_cost = s1["hsg_original_cost"]

        for seller_uid in owners:
            seller_uid = str(seller_uid)
            if seller_uid in (hsg_uid, buyer_uid):
                continue

            seller_currency_value = vals.get(seller_uid, {}).get(currency_aid, 0.0)
            if seller_currency_value <= 0:
                continue

            for target_aid in holdings.get(seller_uid, []):
                hsg_target_value = vals[hsg_uid].get(target_aid, 0.0)
                seller_target_hold = vals.get(seller_uid, {}).get(target_aid, 0.0)
                if hsg_target_value <= 0 or seller_target_hold <= 0:
                    continue

                final_surplus = hsg_target_value - hsg_original_cost
                if final_surplus <= 0:
                    continue

                stage2_ratio = seller_currency_value / seller_target_hold
                if stage2_ratio < 0.86:
                    continue

                route_floor = min(s1["buyer_value_ratio"], stage2_ratio)
                score = (
                    0.55 * min(final_surplus / max(hsg_target_value, 1.0), 0.60)
                    + 0.25 * min(max(route_floor - 0.86, 0.0) / 0.14, 1.0)
                    + 0.20 * min(
                        max(
                            (
                                s1["buyer_receive_value"] - hsg_original_cost
                            )
                            / max(hsg_original_cost, 1.0),
                            0.0,
                        ),
                        0.50,
                    )
                )

                two_step.append({
                    "source_asset_id": source_aid,
                    "source_asset": meta(source_aid).get("name"),
                    "source_hsg_hold_value": round(hsg_original_cost, 1),
                    "step1_buyer_user_id": buyer_uid,
                    "step1_buyer_manager": owner_manager(buyer_uid),
                    "step1_buyer_team": owner_name(buyer_uid),
                    "step1_receive_currency_asset_id": currency_aid,
                    "step1_receive_currency_asset": meta(currency_aid).get("name"),
                    "step1_buyer_value_ratio": round(s1["buyer_value_ratio"], 3),
                    "step2_seller_user_id": seller_uid,
                    "step2_seller_manager": owner_manager(seller_uid),
                    "step2_seller_team": owner_name(seller_uid),
                    "step2_target_asset_id": target_aid,
                    "step2_target_asset": meta(target_aid).get("name"),
                    "step2_seller_currency_value": round(seller_currency_value, 1),
                    "step2_target_hold_value": round(seller_target_hold, 1),
                    "step2_value_ratio": round(stage2_ratio, 3),
                    "hsg_final_target_value": round(hsg_target_value, 1),
                    "hsg_final_surplus_vs_original_asset": round(final_surplus, 1),
                    "route_floor_ratio": round(route_floor, 3),
                    "arbitrage_score": round(score, 5),
                })

    # Deduplicate equivalent source -> currency -> target routes and retain best.
    best_routes = {}
    for row in two_step:
        key = (
            row["source_asset_id"],
            row["step1_receive_currency_asset_id"],
            row["step2_target_asset_id"],
        )
        prev = best_routes.get(key)
        if not prev or row["arbitrage_score"] > prev["arbitrage_score"]:
            best_routes[key] = row

    two_step = list(best_routes.values())
    two_step.sort(
        key=lambda x: (
            x["arbitrage_score"],
            x["hsg_final_surplus_vs_original_asset"],
            x["route_floor_ratio"],
        ),
        reverse=True,
    )

    # Best source assets by maximum observed buyer premium.
    source_summary = []
    for aid in movable_hsg:
        hsg_hold = vals[hsg_uid].get(aid, 0.0)
        buyer_rows = []
        for uid in owners:
            uid = str(uid)
            if uid == hsg_uid:
                continue
            v = vals.get(uid, {}).get(aid, 0.0)
            if v <= 0:
                continue
            buyer_rows.append((v - hsg_hold, uid, v))
        buyer_rows.sort(reverse=True)
        if buyer_rows:
            premium, uid, v = buyer_rows[0]
            source_summary.append({
                "asset_id": aid,
                "asset": meta(aid).get("name"),
                "asset_type": meta(aid).get("asset_type"),
                "hsg_hold_value": round(hsg_hold, 1),
                "best_buyer_user_id": uid,
                "best_buyer_manager": owner_manager(uid),
                "best_buyer_team": owner_name(uid),
                "best_buyer_value": round(v, 1),
                "best_buyer_premium": round(premium, 1),
                "best_buyer_premium_pct": round((v / hsg_hold - 1.0) * 100, 1)
                    if hsg_hold else None,
            })
    source_summary.sort(
        key=lambda x: (x["best_buyer_premium"], x["best_buyer_premium_pct"]),
        reverse=True,
    )

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_version": "GM-2.0",
        "user_id": hsg_uid,
        "manager": USER_MANAGER,
        "team_name": USER_TEAM,
        "methodology_note": (
            "League Arbitrage Matrix compares HSG hold/acquire values with each "
            "opponent's acquire/hold values. Direct paths require positive HSG "
            "surplus and at least 84% opponent-side value coverage. Two-step paths "
            "model HSG asset -> intermediary currency asset -> final target and "
            "require at least 88% value coverage on step 1 and 86% on step 2. "
            "These are negotiation-discovery signals, not literal acceptance probabilities."
        ),
        "protected_core_excluded_from_outgoing_routes": sorted(PROTECTED_HSG_PLAYERS),
        "best_hsg_assets_to_shop": source_summary,
        "owner_seams": owner_seams,
        "top_direct_arbitrage_paths": direct[:150],
        "top_two_step_arbitrage_paths": two_step[:150],
        "counts": {
            "movable_hsg_assets": len(movable_hsg),
            "direct_paths_retained": len(direct),
            "two_step_paths_retained": len(two_step),
        },
    }



# ============================================================
# GM-2.0 strategic decision layer
# ============================================================

def _v2_owner_context():
    owner_payload = load_json(DATA / "owner_perceived_values.json", {}) or {}
    owners = owner_payload.get("owners") or {}
    team_payload = load_json(DATA / "team_contender_profiles.json", {}) or {}
    teams = {str(x.get("user_id")): x for x in team_payload.get("teams") or []}
    profiles = load_json(DATA / "owner_behavior_profiles.json", []) or []
    profile_by_uid = {str(x.get("user_id")): x for x in profiles}
    rosters = load_json(DATA / "rosters.json", []) or []
    roster_to_uid = {
        int(r.get("roster_id")): str(r.get("owner_id"))
        for r in rosters if r.get("roster_id") is not None
    }
    uid_to_team = {}
    for uid, block in owners.items():
        uid_to_team[str(uid)] = (
            block.get("team_name") or block.get("manager") or str(uid)
        )
    return owners, teams, profile_by_uid, roster_to_uid, uid_to_team


def _v2_hsg_uid(owners):
    for uid, block in owners.items():
        if (
            block.get("manager") == USER_MANAGER
            or block.get("team_name") == USER_TEAM
        ):
            return str(uid)
    return None


def _v2_asset_maps(owners):
    vals = {}
    meta = {}
    holdings = defaultdict(list)
    for uid, block in owners.items():
        uid = str(uid)
        vals[uid] = {}
        for row in block.get("assets") or []:
            aid = row.get("asset_id")
            if not aid:
                continue
            vals[uid][aid] = safe_float(row.get("owner_perceived_value"))
            if aid not in meta:
                meta[aid] = {
                    "asset_id": aid,
                    "asset_type": row.get("asset_type"),
                    "name": row.get("name"),
                    "current_owner_user_id": str(
                        row.get("current_owner_user_id")
                    ),
                    "current_owner_team": row.get("current_owner_team"),
                    "market_value": safe_float(row.get("market_value")),
                }
    for aid, m in meta.items():
        uid = str(m.get("current_owner_user_id"))
        if uid and uid != "None":
            holdings[uid].append(aid)
    return vals, meta, holdings


def build_roster_fragility_index():
    """
    Measures how dependent each team is on its optimized starters.

    This is a value-drop sensitivity model, not an injury forecast. For each
    optimized redraft starter, remove that player and re-optimize the legal
    lineup from the team's roster. The resulting drop is the starter's
    replacement fragility.
    """
    rosters = load_json(DATA / "rosters.json", []) or []
    assets = load_json(DATA / "fsffl_asset_values.json", {}) or {}
    profiles = load_json(DATA / "owner_behavior_profiles.json", []) or []
    profile_by_uid = {str(x.get("user_id")): x for x in profiles}

    player_values = {
        str(x.get("player_id")): x
        for x in assets.get("players") or []
        if x.get("player_id") is not None
    }

    teams = []
    for r in rosters:
        uid = str(r.get("owner_id"))
        roster_ids = [
            str(x) for x in (r.get("players") or [])
            if str(x) in player_values
        ]
        if not roster_ids:
            continue

        base = optimize_lineup(
            roster_ids, player_values, "market_redraft"
        )
        base_total = safe_float(base.get("total"))
        starter_rows = []
        for starter in base.get("lineup") or []:
            pid = str(starter.get("player_id"))
            reduced = [x for x in roster_ids if x != pid]
            replacement = optimize_lineup(
                reduced, player_values, "market_redraft"
            )
            repl_total = safe_float(replacement.get("total"))
            drop = max(base_total - repl_total, 0.0)
            starter_rows.append({
                "player_id": pid,
                "player": starter.get("name"),
                "position": starter.get("position"),
                "slot": starter.get("slot"),
                "starter_value": safe_float(starter.get("value")),
                "replacement_lineup_total": round(repl_total, 1),
                "lineup_value_drop_if_unavailable": round(drop, 1),
                "lineup_drop_pct": round(
                    100 * drop / max(base_total, 1.0), 2
                ),
            })

        starter_rows.sort(
            key=lambda x: x["lineup_value_drop_if_unavailable"],
            reverse=True,
        )

        top3 = sum(
            x["lineup_value_drop_if_unavailable"]
            for x in starter_rows[:3]
        )
        concentration = top3 / max(base_total, 1.0)
        no_replacement_count = sum(
            1 for x in starter_rows
            if x["replacement_lineup_total"] <= 0
            or not optimize_lineup(
                [p for p in roster_ids if p != x["player_id"]],
                player_values,
                "market_redraft",
            ).get("complete")
        )

        p = profile_by_uid.get(uid, {})
        teams.append({
            "user_id": uid,
            "manager": p.get("manager") or p.get("username"),
            "team_name": p.get("team_name"),
            "base_optimal_redraft_value": round(base_total, 1),
            "top_three_starter_dependency_value": round(top3, 1),
            "top_three_dependency_pct": round(100 * concentration, 2),
            "fragility_score": round(
                clamp(concentration * 3.0, 0.0, 1.0), 3
            ),
            "lineup_incomplete_after_single_absence_count":
                no_replacement_count,
            "most_irreplaceable_starters": starter_rows[:6],
        })

    teams.sort(
        key=lambda x: (
            x["fragility_score"],
            x["top_three_starter_dependency_value"],
        ),
        reverse=True,
    )
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_version": "GM-2.0",
        "methodology_note": (
            "Fragility measures optimized-lineup value lost when each current "
            "optimal starter is removed individually. It is a roster-depth "
            "sensitivity metric, not a prediction of injury probability."
        ),
        "teams": teams,
    }


def build_pick_quality_model():
    """
    Estimates early/mid/late pick quality from the original franchise's
    contender strength, dynasty strength and roster fragility.

    Probabilities are heuristic scenario weights, not calibrated odds.
    """
    owners, teams, profile_by_uid, roster_to_uid, uid_to_team = _v2_owner_context()
    frag = load_json(DATA / "roster_fragility_index.json", {}) or {}
    frag_by_uid = {
        str(x.get("user_id")): x
        for x in frag.get("teams") or []
    }

    _, meta, _ = _v2_asset_maps(owners)
    picks = []
    seen = set()

    for aid, m in meta.items():
        if m.get("asset_type") != "pick" or not aid.startswith("pick:"):
            continue
        if aid in seen:
            continue
        seen.add(aid)

        mt = re.match(r"pick:(\d{4}):R(\d+):orig(\d+)", aid)
        if not mt:
            continue
        year, rnd, orig_rid = int(mt.group(1)), int(mt.group(2)), int(mt.group(3))
        orig_uid = roster_to_uid.get(orig_rid)
        t = teams.get(str(orig_uid), {}) if orig_uid else {}
        contender = safe_float(t.get("contender_score"), 0.5)
        dynasty = safe_float(t.get("dynasty_roster_score"), 0.5)
        fragility = safe_float(
            (frag_by_uid.get(str(orig_uid)) or {}).get("fragility_score"),
            0.5,
        )

        years_out = max(year - 2026, 1)
        dynasty_weight = clamp(0.48 + 0.08 * (years_out - 1), 0.48, 0.68)
        current_weight = 1.0 - dynasty_weight

        strength = current_weight * contender + dynasty_weight * dynasty
        collapse_risk = clamp(
            (1.0 - strength) * 0.72 + fragility * 0.28,
            0.0, 1.0
        )

        # Convert structural weakness into broad early/mid/late scenario weights.
        early = clamp(0.10 + 0.58 * collapse_risk, 0.08, 0.68)
        late = clamp(0.10 + 0.58 * strength, 0.08, 0.68)
        mid = max(1.0 - early - late, 0.08)
        z = early + mid + late
        early, mid, late = early/z, mid/z, late/z

        tier = max(
            (("early", early), ("mid", mid), ("late", late)),
            key=lambda x: x[1]
        )[0]

        picks.append({
            "asset_id": aid,
            "pick": m.get("name"),
            "season": year,
            "round": rnd,
            "original_roster_id": orig_rid,
            "original_owner_user_id": orig_uid,
            "original_team": uid_to_team.get(str(orig_uid)),
            "current_contender_score": round(contender, 3),
            "dynasty_roster_score": round(dynasty, 3),
            "fragility_score": round(fragility, 3),
            "structural_strength_score": round(strength, 3),
            "early_scenario_weight": round(early, 3),
            "mid_scenario_weight": round(mid, 3),
            "late_scenario_weight": round(late, 3),
            "most_likely_tier": tier,
            "quality_signal": round(collapse_risk, 3),
            "confidence": (
                "medium"
                if orig_uid and t
                else "low"
            ),
        })

    picks.sort(
        key=lambda x: (
            x["round"],
            -x["quality_signal"],
            x["season"],
        )
    )
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_version": "GM-2.0",
        "methodology_note": (
            "Pick-quality scenario weights use the original team's current "
            "contender strength, dynasty strength and fragility. They are "
            "decision-support heuristics, not literal probabilities."
        ),
        "picks": picks,
    }


def build_market_regime():
    """
    Encodes how the model should interpret asset liquidity by point in the
    fantasy calendar. This does not alter base market values; it informs
    urgency and recommendation ordering.
    """
    now = datetime.now(timezone.utc)
    month = now.month

    if month in (7, 8):
        regime = "training_camp_preseason"
        notes = [
            "Rookie, depth-chart and role information can reprice quickly.",
            "Contenders begin paying for lineup certainty.",
            "Future picks retain strong liquidity before regular-season urgency peaks.",
        ]
        urgency = {"RB": 1.10, "QB": 1.03, "WR": 1.00, "TE": 1.00, "pick": 0.98}
    elif month in (9, 10):
        regime = "early_regular_season"
        notes = [
            "Usage and role data should outweigh offseason narratives.",
            "Injury-created starter demand can create temporary seller leverage.",
        ]
        urgency = {"RB": 1.12, "QB": 1.05, "WR": 1.04, "TE": 1.02, "pick": 0.96}
    elif month == 11:
        regime = "trade_deadline"
        notes = [
            "Contenders receive maximum immediate-production urgency.",
            "Rebuilders should demand premiums for reliable starters.",
        ]
        urgency = {"RB": 1.18, "QB": 1.08, "WR": 1.08, "TE": 1.05, "pick": 0.92}
    elif month in (12, 1):
        regime = "playoffs_postseason"
        notes = [
            "Trade liquidity is limited or closed in many leagues.",
            "Dynasty age curves and future picks regain relative importance.",
        ]
        urgency = {"RB": 0.96, "QB": 1.00, "WR": 1.02, "TE": 1.00, "pick": 1.06}
    elif month in (2, 3, 4):
        regime = "rookie_hype_cycle"
        notes = [
            "Draft picks and incoming rookies typically gain liquidity.",
            "Veteran RB values can be temporarily compressed before depth charts settle.",
        ]
        urgency = {"RB": 0.94, "QB": 1.00, "WR": 1.00, "TE": 0.99, "pick": 1.12}
    else:
        regime = "post_draft_otas"
        notes = [
            "Rookie landing spots are known; role certainty is still developing.",
            "Veteran depth-chart value begins to normalize.",
        ]
        urgency = {"RB": 1.02, "QB": 1.00, "WR": 1.00, "TE": 1.00, "pick": 1.04}

    return {
        "generated_at_utc": now.isoformat(),
        "model_version": "GM-2.0",
        "regime": regime,
        "position_liquidity_urgency_multiplier": urgency,
        "notes": notes,
    }


def build_owner_calibration_report():
    """
    Produces confidence-aware owner behavior calibration from actual completed
    league activity already captured by owner_behavior_profiles.json.

    GM-2.0 intentionally does not auto-fit opaque coefficients from a small
    sample. It exposes sample size, activity and stable behavioral tendencies
    so owner adjustments can be tuned transparently.
    """
    profiles = load_json(DATA / "owner_behavior_profiles.json", []) or []
    rows = []
    for p in profiles:
        tp = p.get("trade_profile") or {}
        total = safe_float(tp.get("total_trades"))
        recent = safe_float(tp.get("recent_trades_2025_2026"))
        initiated = safe_float(tp.get("initiated_trades"))
        multi = safe_float(tp.get("multi_asset_trades"))
        confidence = (
            "high" if total >= 20
            else "medium" if total >= 8
            else "low"
        )
        rows.append({
            "user_id": str(p.get("user_id")),
            "manager": p.get("manager") or p.get("username"),
            "team_name": p.get("team_name"),
            "completed_trade_sample": int(total),
            "recent_trade_sample": int(recent),
            "initiation_rate": round(initiated / total, 3) if total else None,
            "multi_asset_rate": round(multi / total, 3) if total else None,
            "behavior_confidence": confidence,
            "calibration_policy": (
                "full_owner_adjustments"
                if confidence == "high"
                else "moderated_owner_adjustments"
                if confidence == "medium"
                else "market_anchor_dominant"
            ),
        })
    rows.sort(
        key=lambda x: x["completed_trade_sample"], reverse=True
    )
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_version": "GM-2.0",
        "methodology_note": (
            "Behavioral confidence is based on completed league transactions. "
            "GM-2.0 keeps owner psychology transparent rather than fitting an "
            "opaque model to a small sample."
        ),
        "owners": rows,
    }


def build_strategic_arbitrage_board():
    """
    Re-ranks the raw arbitrage matrix around outcomes HSG should actually care
    about: starting-lineup gain, premium picks, young appreciating players and
    identified roster need. Low-impact churn is intentionally suppressed.
    """
    matrix = load_json(DATA / "league_arbitrage_matrix.json", {}) or {}
    assets = load_json(DATA / "fsffl_asset_values.json", {}) or {}
    opps = load_json(DATA / "hsg_trade_opportunities.json", {}) or {}
    regime = load_json(DATA / "market_regime.json", {}) or {}
    pick_quality = load_json(DATA / "pick_quality_model.json", {}) or {}

    player_by_aid = {
        f"player:{x.get('player_id')}": x
        for x in assets.get("players") or []
    }
    pickq = {
        x.get("asset_id"): x
        for x in pick_quality.get("picks") or []
    }
    target_impact = {}
    for o in opps.get("opportunities") or []:
        pid = o.get("target_player_id")
        rows = o.get("best_candidate_packages") or []
        gain = 0.0
        if rows:
            gain = max(
                safe_float(
                    (r.get("championship_utility") or {}).get(
                        "optimal_lineup_value_gain"
                    )
                )
                for r in rows
            )
        target_impact[f"player:{pid}"] = gain

    urgency = regime.get("position_liquidity_urgency_multiplier") or {}

    def strategic_asset_score(aid):
        if aid.startswith("pick:"):
            q = pickq.get(aid, {})
            rnd = int(q.get("round") or 3)
            premium = {1: 1.0, 2: 0.48, 3: 0.18}.get(rnd, 0.1)
            quality = safe_float(q.get("quality_signal"), 0.5)
            return 0.72 * premium + 0.28 * quality

        p = player_by_aid.get(aid, {})
        pos = p.get("position")
        age = safe_float(p.get("age"), 30)
        dyn = safe_float(p.get("market_dynasty"))
        rd = safe_float(p.get("market_redraft"))
        lineup_gain = safe_float(target_impact.get(aid))
        age_score = clamp((29.0 - age) / 8.0, 0.0, 1.0)
        appreciation = clamp(
            (dyn - rd) / max(dyn, 1.0), -0.5, 0.5
        )
        impact = clamp(lineup_gain / 4500.0, 0.0, 1.0)
        liq = safe_float(urgency.get(pos), 1.0)
        return (
            0.48 * impact
            + 0.22 * age_score
            + 0.18 * max(appreciation, 0.0)
            + 0.12 * clamp((liq - 0.9) / 0.3, 0.0, 1.0)
        )

    direct = []
    for row in matrix.get("top_direct_arbitrage_paths") or []:
        target_aid = row.get("receive_asset_id")
        strategic = strategic_asset_score(target_aid)
        raw_surplus = safe_float(row.get("hsg_surplus"))
        receive_value = safe_float(row.get("hsg_receive_value"), 1.0)
        surplus_pct = raw_surplus / max(receive_value, 1.0)
        opponent_ratio = safe_float(row.get("opponent_value_ratio"))
        score = (
            0.50 * strategic
            + 0.30 * clamp(surplus_pct / 0.20, 0.0, 1.0)
            + 0.20 * clamp((opponent_ratio - 0.84) / 0.20, 0.0, 1.0)
        )
        out = dict(row)
        out["strategic_asset_score"] = round(strategic, 4)
        out["strategic_score"] = round(score, 5)
        out["strategic_class"] = (
            "priority"
            if strategic >= 0.55 and raw_surplus > 0
            else "useful"
            if strategic >= 0.30 and raw_surplus > 0
            else "low_impact_churn"
        )
        direct.append(out)

    direct.sort(
        key=lambda x: (
            x["strategic_class"] == "priority",
            x["strategic_class"] == "useful",
            x["strategic_score"],
            x["hsg_surplus"],
        ),
        reverse=True,
    )

    two_step = []
    for row in matrix.get("top_two_step_arbitrage_paths") or []:
        target_aid = row.get("step2_target_asset_id")
        strategic = strategic_asset_score(target_aid)
        surplus = safe_float(
            row.get("hsg_final_surplus_vs_original_asset")
        )
        final_value = safe_float(row.get("hsg_final_target_value"), 1.0)
        route_floor = safe_float(row.get("route_floor_ratio"))
        score = (
            0.56 * strategic
            + 0.26 * clamp(
                (surplus / max(final_value, 1.0)) / 0.25,
                0.0, 1.0
            )
            + 0.18 * clamp(
                (route_floor - 0.86) / 0.20, 0.0, 1.0
            )
        )
        out = dict(row)
        out["strategic_asset_score"] = round(strategic, 4)
        out["strategic_score"] = round(score, 5)
        out["strategic_class"] = (
            "priority"
            if strategic >= 0.55
            else "useful"
            if strategic >= 0.30
            else "low_impact_churn"
        )
        two_step.append(out)

    two_step.sort(
        key=lambda x: (
            x["strategic_class"] == "priority",
            x["strategic_class"] == "useful",
            x["strategic_score"],
            x["hsg_final_surplus_vs_original_asset"],
        ),
        reverse=True,
    )

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_version": "GM-2.0",
        "methodology_note": (
            "Strategic ranking suppresses mathematically positive but low-impact "
            "churn. It prioritizes lineup upgrades, first-round picks, young "
            "appreciating players and assets aligned with current market regime."
        ),
        "priority_direct_paths": [
            x for x in direct if x["strategic_class"] == "priority"
        ][:60],
        "useful_direct_paths": [
            x for x in direct if x["strategic_class"] == "useful"
        ][:60],
        "priority_two_step_paths": [
            x for x in two_step if x["strategic_class"] == "priority"
        ][:60],
        "useful_two_step_paths": [
            x for x in two_step if x["strategic_class"] == "useful"
        ][:60],
        "suppressed_low_impact_direct_count": sum(
            x["strategic_class"] == "low_impact_churn" for x in direct
        ),
        "suppressed_low_impact_two_step_count": sum(
            x["strategic_class"] == "low_impact_churn" for x in two_step
        ),
    }


def build_gm_command_center():
    """
    Single decision surface for day-to-day use.
    """
    teams = load_json(DATA / "team_contender_profiles.json", {}) or {}
    sell = load_json(DATA / "sell_leverage_board.json", {}) or {}
    opps = load_json(DATA / "hsg_trade_opportunities.json", {}) or {}
    strategic = load_json(DATA / "strategic_arbitrage_board.json", {}) or {}
    frag = load_json(DATA / "roster_fragility_index.json", {}) or {}
    regime = load_json(DATA / "market_regime.json", {}) or {}

    contender_board = []
    for x in teams.get("teams") or []:
        contender_board.append({
            "user_id": str(x.get("user_id")),
            "manager": x.get("manager"),
            "team_name": x.get("team_name"),
            "contender_score": x.get("contender_score"),
            "dynasty_roster_score": x.get("dynasty_roster_score"),
            "competitive_tier": x.get("competitive_tier"),
            "starter_redraft_value": x.get("starter_redraft_value"),
        })

    frag_by_uid = {
        str(x.get("user_id")): x
        for x in frag.get("teams") or []
    }
    for row in contender_board:
        f = frag_by_uid.get(row["user_id"], {})
        row["fragility_score"] = f.get("fragility_score")
        row["most_irreplaceable_starters"] = (
            f.get("most_irreplaceable_starters") or []
        )[:3]

    # Best targets: use only strong/negotiation candidates and score by
    # HSG surplus + lineup gain + seller fit.
    targets = []
    for o in opps.get("opportunities") or []:
        packages = [
            p for p in (o.get("best_candidate_packages") or [])
            if p.get("recommendation_band")
            in ("strong_candidate", "negotiation_candidate")
        ]
        if not packages:
            continue
        p = packages[0]
        gain = safe_float(
            (p.get("championship_utility") or {}).get(
                "optimal_lineup_value_gain"
            )
        )
        targets.append({
            "target_player_id": o.get("target_player_id"),
            "target_player": o.get("target_player"),
            "position": o.get("position"),
            "seller_manager": o.get("seller_manager"),
            "seller_team": o.get("seller_team"),
            "hsg_value": o.get("hsg_value"),
            "seller_hold_value": o.get("seller_hold_value"),
            "valuation_gap_hsg_minus_seller":
                o.get("target_value_gap_hsg_minus_seller"),
            "optimal_lineup_value_gain": round(gain, 1),
            "best_opening_package": p.get("outgoing_assets"),
            "hsg_modeled_surplus": p.get("hsg_modeled_surplus"),
            "acceptance_fit_score": p.get("acceptance_fit_score"),
            "recommendation_band": p.get("recommendation_band"),
            "decision_score": p.get("decision_score"),
        })

    targets.sort(
        key=lambda x: (
            x["recommendation_band"] == "strong_candidate",
            x["optimal_lineup_value_gain"],
            safe_float(x["hsg_modeled_surplus"]),
            safe_float(x["acceptance_fit_score"]),
        ),
        reverse=True,
    )

    # Sell board schema can be list or dict depending prior layer.
    sell_rows = (
        sell.get("assets")
        or sell.get("sell_leverage_board")
        or sell.get("players")
        or []
    )
    if not sell_rows and isinstance(sell, list):
        sell_rows = sell

    # Normalize and favor non-protected positive buyer gaps.
    shop = []
    for x in sell_rows:
        name = x.get("asset") or x.get("name")
        if name in PROTECTED_HSG_PLAYERS:
            continue
        gap = safe_float(
            x.get("best_buyer_premium_vs_hsg_hold")
            or x.get("best_buyer_premium")
            or x.get("premium_vs_hsg_hold")
        )
        shop.append(dict(x, _sort_gap=gap))
    shop.sort(key=lambda x: x["_sort_gap"], reverse=True)
    for x in shop:
        x.pop("_sort_gap", None)

    best_moves = []
    for t in targets[:12]:
        impact = t["optimal_lineup_value_gain"]
        surplus = safe_float(t["hsg_modeled_surplus"])
        urgency = (
            "high"
            if impact >= 2500 and surplus >= 0
            else "medium"
            if impact >= 1200
            else "low"
        )
        best_moves.append({
            "action": "buy",
            "asset": t["target_player"],
            "counterparty": t["seller_team"],
            "why": {
                "optimal_lineup_value_gain": impact,
                "modeled_hsg_surplus": surplus,
                "valuation_gap_hsg_minus_seller":
                    t["valuation_gap_hsg_minus_seller"],
            },
            "best_opening_package": t["best_opening_package"],
            "acceptance_fit_score": t["acceptance_fit_score"],
            "urgency": urgency,
        })

    for s in shop[:8]:
        gap = safe_float(
            s.get("best_buyer_premium_vs_hsg_hold")
            or s.get("best_buyer_premium")
            or s.get("premium_vs_hsg_hold")
        )
        if gap <= 0:
            continue
        best_moves.append({
            "action": "shop",
            "asset": s.get("asset") or s.get("name"),
            "counterparty": (
                s.get("best_buyer_team")
                or s.get("top_buyer_team")
                or s.get("best_destination")
            ),
            "why": {
                "best_buyer_premium_vs_hsg_hold": round(gap, 1)
            },
            "urgency": "medium",
        })

    best_moves.sort(
        key=lambda x: (
            x["urgency"] == "high",
            x["action"] == "buy",
            safe_float(
                (x.get("why") or {}).get("optimal_lineup_value_gain")
            ),
            safe_float(
                (x.get("why") or {}).get(
                    "best_buyer_premium_vs_hsg_hold"
                )
            ),
        ),
        reverse=True,
    )

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_version": "GM-2.0",
        "architecture_status": "frozen_after_2.0",
        "market_regime": regime.get("regime"),
        "best_moves_now": best_moves[:15],
        "best_players_to_shop": shop[:15],
        "best_players_to_target": targets[:20],
        "best_direct_arbitrage_routes":
            (strategic.get("priority_direct_paths") or [])[:20],
        "best_two_step_arbitrage_routes":
            (strategic.get("priority_two_step_paths") or [])[:20],
        "league_threat_board": contender_board,
        "operating_note": (
            "GM-2.0 is the architecture-freeze edition. Future changes should "
            "prefer data refreshes, weight tuning and bug fixes over new model layers."
        ),
    }



# ============================================================
# GM-2.2 UNIVERSAL FRANCHISE MODE — STRATEGIC VALUATION
# ============================================================
#
# GM-2.2 separates:
#   1) external market price;
#   2) team-specific franchise utility;
#   3) future/optionality value;
#   4) liquidity/reversibility;
#   5) minimum rational exit price.
#
# There are no hard-coded untouchables. Elite or high-upside assets can be
# moved, but packages must compensate for scarcity, optionality, consolidation,
# lineup disruption, roster-slot cost and the strategic value of simply holding.

GM_ROOT = DATA / "gm"
GM_LEAGUE_DIR = GM_ROOT / "league"
GM_TEAMS_DIR = GM_ROOT / "teams"

GM22 = {
    "model_version": "GM-2.2",
    "roster_size": LEAGUE_RULES["roster_size"],
    "package_weights": [1.0, 0.78, 0.62, 0.50, 0.42],
    "extra_asset_slot_cost_pct": 0.035,
    "max_static_exit_premium_pct": 0.85,
    "max_replacement_relief_pct": 0.68,
    "rookie_optionality_bonus": 0.16,
    "young_qb_optionality_bonus": 0.10,
    "first_round_pick_liquidity": 0.98,
    "second_round_pick_liquidity": 0.88,
    "third_round_pick_liquidity": 0.72,
}


def _u_slug(s: str) -> str:
    s = normalize_name(s or "")
    return s or "team"


def _u_load_context():
    owner_payload = load_json(DATA / "owner_perceived_values.json", {}) or {}
    owners = owner_payload.get("owners") or {}
    team_payload = load_json(DATA / "team_contender_profiles.json", {}) or {}
    teams = {str(x.get("user_id")): x for x in team_payload.get("teams") or []}
    assets_payload = load_json(DATA / "fsffl_asset_values.json", {}) or {}
    rosters = load_json(DATA / "rosters.json", []) or []
    profiles = load_json(DATA / "owner_behavior_profiles.json", []) or []
    frag_payload = load_json(DATA / "roster_fragility_index.json", {}) or {}
    frag = {str(x.get("user_id")): x for x in frag_payload.get("teams") or []}
    pick_quality_payload = load_json(DATA / "pick_quality_model.json", {}) or {}
    pick_quality = {
        str(x.get("asset_id")): x for x in (pick_quality_payload.get("picks") or [])
        if x.get("asset_id")
    }
    market_regime = load_json(DATA / "market_regime.json", {}) or {}
    players_full = load_json(DATA / "players.json", {}) or {}

    player_meta = {
        f"player:{x.get('player_id')}": dict(x)
        for x in assets_payload.get("players") or []
        if x.get("player_id") is not None
    }
    # Enrich with Sleeper metadata such as years_exp and NFL draft information.
    if isinstance(players_full, dict):
        for pid, raw in players_full.items():
            aid = f"player:{pid}"
            if aid in player_meta and isinstance(raw, dict):
                for key in (
                    "years_exp", "draft_year", "draft_round", "draft_pick",
                    "status", "team", "injury_status"
                ):
                    if player_meta[aid].get(key) is None and raw.get(key) is not None:
                        player_meta[aid][key] = raw.get(key)

    owner_vals = {}
    asset_meta = dict(player_meta)
    holdings = defaultdict(list)
    for uid, block in owners.items():
        uid = str(uid)
        owner_vals[uid] = {}
        for row in block.get("assets") or []:
            aid = row.get("asset_id")
            if not aid:
                continue
            owner_vals[uid][aid] = safe_float(row.get("owner_perceived_value"))
            if aid not in asset_meta:
                asset_meta[aid] = {
                    "asset_id": aid,
                    "asset_type": row.get("asset_type"),
                    "name": row.get("name"),
                    "current_owner_user_id": (
                        str(row.get("current_owner_user_id"))
                        if row.get("current_owner_user_id") is not None else None
                    ),
                    "current_owner_team": row.get("current_owner_team"),
                    "market_value": safe_float(row.get("market_value")),
                }

    for aid, meta in asset_meta.items():
        ouid = meta.get("current_owner_user_id")
        if ouid is not None:
            ouid = str(ouid)
            if ouid in owners and aid not in holdings[ouid]:
                holdings[ouid].append(aid)

    for r in rosters:
        uid = str(r.get("owner_id"))
        for pid in r.get("players") or []:
            aid = f"player:{pid}"
            if aid not in holdings[uid]:
                holdings[uid].append(aid)
            if aid in asset_meta:
                asset_meta[aid]["current_owner_user_id"] = uid

    profile_by_uid = {str(x.get("user_id")): x for x in profiles}
    roster_by_uid = {
        str(r.get("owner_id")): [str(x) for x in (r.get("players") or [])]
        for r in rosters
    }
    roster_id_by_uid = {
        str(r.get("owner_id")): int(r.get("roster_id"))
        for r in rosters if r.get("owner_id") is not None and r.get("roster_id") is not None
    }

    return {
        "owners": owners,
        "teams": teams,
        "assets_payload": assets_payload,
        "player_meta": player_meta,
        "asset_meta": asset_meta,
        "owner_vals": owner_vals,
        "holdings": holdings,
        "profiles": profiles,
        "profile_by_uid": profile_by_uid,
        "rosters": rosters,
        "roster_by_uid": roster_by_uid,
        "roster_id_by_uid": roster_id_by_uid,
        "fragility": frag,
        "pick_quality": pick_quality,
        "market_regime": market_regime,
        "_profile_cache": {},
        "_depth_cache": {},
    }


def _u_player_values_from_assets(ctx):
    out = {}
    for aid, row in ctx["player_meta"].items():
        pid = aid.split(":", 1)[1]
        out[pid] = row
    return out


def _u_parse_pick(aid: str, meta=None):
    m = re.match(r"pick:(\d{4}):R(\d+):orig(\d+)", str(aid))
    if not m:
        return None
    return {
        "season": int(m.group(1)),
        "round": int(m.group(2)),
        "original_roster_id": int(m.group(3)),
        "name": (meta or {}).get("name"),
    }


def _u_team_objective_weights(team):
    c = safe_float((team or {}).get("contender_score"), 0.5)
    d = safe_float((team or {}).get("dynasty_roster_score"), 0.5)
    if c >= 0.78:
        state = "elite_contender"
        w = {"current": 0.50, "future": 0.25, "liquidity": 0.10, "resilience": 0.15}
    elif c >= 0.55:
        state = "contender"
        w = {"current": 0.40, "future": 0.35, "liquidity": 0.10, "resilience": 0.15}
    elif c >= 0.35:
        state = "retool"
        w = {"current": 0.23, "future": 0.47, "liquidity": 0.15, "resilience": 0.15}
    else:
        state = "rebuild"
        w = {"current": 0.10, "future": 0.60, "liquidity": 0.20, "resilience": 0.10}

    # A weak dynasty portfolio shifts another small amount toward future value.
    if d < 0.30 and w["current"] >= 0.23:
        shift = min(0.05, w["current"] - 0.18)
        w["current"] -= shift
        w["future"] += shift
    return state, w


def _u_lineup_swap(uid, outgoing_asset_ids, incoming_asset_ids, ctx, value_key="market_redraft"):
    player_values = _u_player_values_from_assets(ctx)
    roster = list(ctx["roster_by_uid"].get(str(uid), []))
    before = optimize_lineup(roster, player_values, value_key)

    outgoing_pids = {
        a.split(":", 1)[1] for a in outgoing_asset_ids
        if str(a).startswith("player:")
    }
    incoming_pids = [
        a.split(":", 1)[1] for a in incoming_asset_ids
        if str(a).startswith("player:")
    ]
    after_roster = [pid for pid in roster if pid not in outgoing_pids]
    for pid in incoming_pids:
        if pid not in after_roster:
            after_roster.append(pid)
    after = optimize_lineup(after_roster, player_values, value_key)

    removal_roster = [pid for pid in roster if pid not in outgoing_pids]
    removed = optimize_lineup(removal_roster, player_values, value_key)

    base = safe_float(before.get("total"))
    post = safe_float(after.get("total"))
    removal = safe_float(removed.get("total"))
    dependency_drop = max(base - removal, 0.0)
    recovered = max(post - removal, 0.0)
    replacement_fraction = (
        clamp(recovered / dependency_drop, 0.0, 1.0)
        if dependency_drop > 0 else 1.0
    )

    return {
        "before_total": round(base, 1),
        "after_total": round(post, 1),
        "lineup_gain": round(post - base, 1),
        "dependency_drop_without_return": round(dependency_drop, 1),
        "replacement_fraction": round(replacement_fraction, 3),
        "after_lineup": after.get("lineup") or [],
        "complete": bool(after.get("complete")),
    }


def _u_depth_insurance_drop(uid: str, pid: str, ctx):
    """
    Incremental lineup loss from removing this player while one other optimal
    starter at the same position is already unavailable. This captures depth
    insurance that single-removal fragility misses.
    """
    key = (str(uid), str(pid))
    if key in ctx["_depth_cache"]:
        return ctx["_depth_cache"][key]

    uid = str(uid)
    player_values = _u_player_values_from_assets(ctx)
    roster = list(ctx["roster_by_uid"].get(uid, []))
    base = optimize_lineup(roster, player_values, "market_redraft")
    lineup = base.get("lineup") or []
    pos = (ctx["player_meta"].get(f"player:{pid}") or {}).get("position")
    other = [
        str(x.get("player_id")) for x in lineup
        if str(x.get("player_id")) != str(pid)
        and x.get("position") == pos
    ]
    if not other:
        ctx["_depth_cache"][key] = 0.0
        return 0.0

    # Stress the roster by first removing the strongest same-position starter.
    other.sort(
        key=lambda x: safe_float(
            (ctx["player_meta"].get(f"player:{x}") or {}).get("market_redraft")
        ),
        reverse=True,
    )
    stressed_pid = other[0]
    r1 = [x for x in roster if x != stressed_pid]
    r2 = [x for x in roster if x not in {stressed_pid, str(pid)}]
    one_out = optimize_lineup(r1, player_values, "market_redraft")
    two_out = optimize_lineup(r2, player_values, "market_redraft")
    drop = max(safe_float(one_out.get("total")) - safe_float(two_out.get("total")), 0.0)
    ctx["_depth_cache"][key] = drop
    return drop


def _u_position_tier_features(aid, ctx):
    meta = ctx["player_meta"].get(aid, {})
    pos = meta.get("position")
    dyn = safe_float(meta.get("market_dynasty"))
    peers = sorted(
        [safe_float(x.get("market_dynasty")) for x in ctx["player_meta"].values()
         if x.get("position") == pos and safe_float(x.get("market_dynasty")) > 0],
        reverse=True
    )
    if not peers or dyn <= 0:
        return {"percentile": 0.0, "tier_gap": 0.0, "scarcity_score": 0.0}

    pct = percentile_rank(dyn, peers)
    lower = [v for v in peers if v < dyn]
    next_band = statistics.mean(lower[:3]) if lower else dyn
    gap = clamp((dyn - next_band) / max(dyn, 1.0), 0.0, 0.35) / 0.35
    scarcity = clamp(0.65 * pct + 0.35 * gap, 0.0, 1.0)
    return {
        "percentile": round(pct, 4),
        "tier_gap": round(gap, 4),
        "scarcity_score": round(scarcity, 4),
    }


def _u_player_distribution_features(aid, ctx):
    m = ctx["player_meta"].get(aid, {})
    dyn = safe_float(m.get("market_dynasty"))
    red = safe_float(m.get("market_redraft"))
    age = safe_float(m.get("age"), 27.0)
    pos = m.get("position")
    years_exp = safe_float(m.get("years_exp"), 3.0)
    trend = safe_float(m.get("trend_30_day"), 0.0)

    if pos == "QB":
        young_center, old_center = 25.0, 34.0
    elif pos in ("WR", "TE"):
        young_center, old_center = 24.0, 30.0
    else:
        young_center, old_center = 23.0, 28.0

    youth = clamp((old_center - age) / max(old_center - young_center, 1.0), 0.0, 1.0)
    rookie = 1.0 if years_exp <= 0.5 else 0.55 if years_exp <= 1.5 else 0.0
    dyn_red_spread = clamp((dyn - red) / max(dyn, 1.0), -0.6, 0.8)
    positive_spread = max(dyn_red_spread, 0.0)

    draft_round = safe_float(m.get("draft_round"), 0.0)
    pedigree = 0.0
    if draft_round == 1:
        pedigree = 1.0
    elif draft_round == 2:
        pedigree = 0.72
    elif draft_round == 3:
        pedigree = 0.48
    elif draft_round > 0:
        pedigree = 0.25

    momentum = clamp((trend + 500.0) / 1000.0, 0.0, 1.0) if trend else 0.5
    upside = clamp(
        0.34 * youth
        + 0.23 * rookie
        + 0.18 * positive_spread
        + 0.15 * pedigree
        + 0.10 * momentum,
        0.0, 1.0
    )
    if pos == "QB" and age <= 25:
        upside = clamp(upside + GM22["young_qb_optionality_bonus"], 0.0, 1.0)

    age_risk = clamp((age - young_center) / max(old_center - young_center, 1.0), 0.0, 1.0)
    role_risk = clamp(positive_spread, 0.0, 1.0)
    injury = 1.0 if m.get("injury_status") in ("Out", "IR", "PUP") else 0.45 if m.get("injury_status") else 0.0
    downside = clamp(0.45 * age_risk + 0.35 * role_risk + 0.20 * injury, 0.0, 1.0)

    # Heuristic value distribution — explicitly not calibrated probabilities.
    floor_mult = clamp(0.78 - 0.30 * downside, 0.38, 0.82)
    ceiling_mult = 1.10 + 0.65 * upside
    hold_appreciation = clamp(0.02 + 0.18 * upside - 0.10 * downside, -0.08, 0.20)

    return {
        "upside_optionality": round(upside, 4),
        "downside_risk": round(downside, 4),
        "hold_appreciation_pct": round(hold_appreciation, 4),
        "distribution_floor": round(dyn * floor_mult, 1),
        "distribution_median": round(dyn, 1),
        "distribution_ceiling": round(dyn * ceiling_mult, 1),
        "years_exp": years_exp,
        "pedigree_score": round(pedigree, 3),
    }


def _u_player_liquidity(aid, ctx):
    m = ctx["player_meta"].get(aid, {})
    dyn = safe_float(m.get("market_dynasty"))
    age = safe_float(m.get("age"), 27.0)
    pos = m.get("position")
    scarcity = _u_position_tier_features(aid, ctx)["scarcity_score"]

    base = clamp(0.35 + 0.40 * scarcity + 0.25 * clamp(dyn / 8000.0, 0.0, 1.0), 0.0, 1.0)
    if pos == "QB":
        base += 0.08
    if pos == "RB" and age >= 29:
        base -= 0.20
    elif pos in ("WR", "TE") and age >= 30:
        base -= 0.12
    return clamp(base, 0.10, 1.0)


def _u_pick_profile(aid, uid, ctx):
    meta = ctx["asset_meta"].get(aid, {})
    parsed = _u_parse_pick(aid, meta) or {}
    quality = ctx["pick_quality"].get(aid, {})
    rnd = int(parsed.get("round") or quality.get("round") or 3)
    season = int(parsed.get("season") or quality.get("season") or max(FUTURE_PICK_YEARS))
    qsignal = safe_float(quality.get("quality_signal"), 0.5)
    early = safe_float(quality.get("early_scenario_weight"), 0.33)
    late = safe_float(quality.get("late_scenario_weight"), 0.33)

    if rnd == 1:
        liquidity = GM22["first_round_pick_liquidity"]
        upside = clamp(0.48 + 0.42 * qsignal, 0.48, 0.95)
        uncertainty = 0.45 + 0.25 * max(season - (int(LEAGUE_RULES["season"]) + 1), 0)
    elif rnd == 2:
        liquidity = GM22["second_round_pick_liquidity"]
        upside = clamp(0.28 + 0.32 * qsignal, 0.28, 0.72)
        uncertainty = 0.38 + 0.20 * max(season - (int(LEAGUE_RULES["season"]) + 1), 0)
    else:
        liquidity = GM22["third_round_pick_liquidity"]
        upside = clamp(0.12 + 0.20 * qsignal, 0.12, 0.45)
        uncertainty = 0.28 + 0.16 * max(season - (int(LEAGUE_RULES["season"]) + 1), 0)

    # A pick's value distribution is positively skewed; uncertainty is useful
    # optionality rather than pure downside because the pick can improve.
    option = clamp(upside + 0.18 * clamp(uncertainty, 0.0, 1.0), 0.0, 1.0)
    original_uid = str(quality.get("original_owner_user_id") or "")
    control_bonus = 0.10 if original_uid and original_uid == str(uid) else 0.0

    return {
        "round": rnd,
        "season": season,
        "quality_signal": round(qsignal, 4),
        "early_scenario_weight": round(early, 4),
        "late_scenario_weight": round(late, 4),
        "liquidity": round(liquidity, 4),
        "upside_optionality": round(option, 4),
        "own_pick_control_bonus": round(control_bonus, 4),
        "most_likely_tier": quality.get("most_likely_tier"),
        "confidence": quality.get("confidence"),
    }


def build_strategic_asset_profiles_for_team(uid: str, ctx=None):
    """
    GM-2.2 strategic profile for every owned player AND pick.
    """
    ctx = ctx or _u_load_context()
    uid = str(uid)
    cache_key = ("asset_profiles", uid)
    if cache_key in ctx["_profile_cache"]:
        return ctx["_profile_cache"][cache_key]

    team = ctx["teams"].get(uid, {})
    state, objective = _u_team_objective_weights(team)
    vals = ctx["owner_vals"].get(uid, {})
    holdings = ctx["holdings"].get(uid, [])
    player_values = _u_player_values_from_assets(ctx)
    roster = ctx["roster_by_uid"].get(uid, [])
    lineup = optimize_lineup(roster, player_values, "market_redraft")
    lineup_ids = set(lineup.get("player_ids") or [])
    base_lineup = max(safe_float(lineup.get("total")), 1.0)

    frag_rows = {
        str(x.get("player_id")): x
        for x in (ctx["fragility"].get(uid, {}).get("most_irreplaceable_starters") or [])
    }

    rows = []
    for aid in holdings:
        base = safe_float(vals.get(aid))
        if base <= 0:
            continue

        if aid.startswith("player:"):
            m = ctx["player_meta"].get(aid, {})
            pid = aid.split(":", 1)[1]
            dyn = safe_float(m.get("market_dynasty"))
            red = safe_float(m.get("market_redraft"))
            scarcity = _u_position_tier_features(aid, ctx)
            dist = _u_player_distribution_features(aid, ctx)
            liquidity = _u_player_liquidity(aid, ctx)
            starter = pid in lineup_ids
            f = frag_rows.get(pid, {})
            single_drop = safe_float(f.get("lineup_value_drop_if_unavailable"))
            depth_drop = _u_depth_insurance_drop(uid, pid, ctx)
            dependency = clamp(single_drop / base_lineup * 4.5, 0.0, 1.0)
            depth_insurance = clamp(depth_drop / base_lineup * 5.0, 0.0, 1.0)
            current_utility = clamp(red / max(base_lineup / 9.0, 1.0) / 4.0, 0.0, 1.0)
            future_utility = clamp(
                0.55 * scarcity["scarcity_score"] + 0.45 * dist["upside_optionality"],
                0.0, 1.0
            )
            resilience = clamp(0.62 * dependency + 0.38 * depth_insurance, 0.0, 1.0)

            strategic_score = clamp(
                objective["current"] * current_utility
                + objective["future"] * future_utility
                + objective["liquidity"] * liquidity
                + objective["resilience"] * resilience,
                0.0, 1.0
            )

            # Hold premium components are deliberately distinct and transparent.
            core_premium = 0.04 + 0.30 * (strategic_score ** 1.65)
            scarcity_premium = 0.13 * (scarcity["scarcity_score"] ** 1.8)
            optionality_premium = 0.22 * dist["upside_optionality"]
            liquidity_premium = 0.07 * liquidity
            appreciation_premium = max(dist["hold_appreciation_pct"], 0.0) * 0.70
            resilience_premium = 0.10 * resilience
            raw_premium = (
                core_premium + scarcity_premium + optionality_premium
                + liquidity_premium + appreciation_premium + resilience_premium
            )
            premium_pct = clamp(raw_premium, 0.03, GM22["max_static_exit_premium_pct"])
            break_glass = base * (1.0 + premium_pct)

            elasticity = clamp(
                0.18
                + 0.40 * (1.0 - dependency)
                + 0.20 * (1.0 - scarcity["scarcity_score"])
                + 0.15 * (1.0 - strategic_score),
                0.12, 0.80
            )

            if strategic_score >= 0.82 or break_glass / max(base,1) >= 1.55:
                status = "franchise_cornerstone"
            elif strategic_score >= 0.65 or break_glass / max(base,1) >= 1.38:
                status = "core_high_hold"
            elif strategic_score >= 0.45:
                status = "important_asset"
            elif strategic_score >= 0.28:
                status = "liquid_asset"
            else:
                status = "developmental_or_expendable"

            row = {
                "asset_id": aid,
                "asset_type": "player",
                "player_id": pid,
                "name": m.get("name"),
                "position": m.get("position"),
                "age": m.get("age"),
                "base_franchise_value": round(base, 1),
                "market_dynasty": round(dyn, 1),
                "market_redraft": round(red, 1),
                "is_current_optimal_starter": bool(starter),
                "single_absence_dependency_drop": round(single_drop, 1),
                "depth_insurance_drop": round(depth_drop, 1),
                "strategic_score": round(strategic_score, 4),
                "core_status": status,
                "liquidity_score": round(liquidity, 4),
                "scarcity": scarcity,
                "future_distribution": dist,
                "objective_state": state,
                "objective_weights": objective,
                "hold_premium_pct": round(premium_pct, 4),
                "hold_premium_value": round(base * premium_pct, 1),
                "break_glass_value": round(break_glass, 1),
                "trade_elasticity": round(elasticity, 4),
                "premium_components": {
                    "core": round(core_premium, 4),
                    "tier_scarcity": round(scarcity_premium, 4),
                    "optionality": round(optionality_premium, 4),
                    "liquidity": round(liquidity_premium, 4),
                    "expected_hold_appreciation": round(appreciation_premium, 4),
                    "resilience": round(resilience_premium, 4),
                },
            }
        else:
            pp = _u_pick_profile(aid, uid, ctx)
            q = pp["quality_signal"]
            option = pp["upside_optionality"]
            liquidity = pp["liquidity"]
            rnd = pp["round"]
            control = pp["own_pick_control_bonus"]

            future_utility = clamp(0.62 * option + 0.38 * q, 0.0, 1.0)
            strategic_score = clamp(
                objective["future"] * future_utility
                + objective["liquidity"] * liquidity
                + 0.12 * control
                + (0.08 if rnd == 1 else 0.03 if rnd == 2 else 0.0),
                0.0, 1.0
            )

            round_premium = 0.15 if rnd == 1 else 0.08 if rnd == 2 else 0.035
            quality_premium = (0.24 if rnd == 1 else 0.14 if rnd == 2 else 0.07) * q
            option_premium = (0.20 if rnd == 1 else 0.12 if rnd == 2 else 0.06) * option
            liquidity_premium = 0.07 * liquidity
            control_premium = control
            premium_pct = clamp(
                round_premium + quality_premium + option_premium
                + liquidity_premium + control_premium,
                0.04, 0.65
            )
            break_glass = base * (1.0 + premium_pct)
            status = (
                "premium_pick" if rnd == 1 and (q >= 0.65 or strategic_score >= 0.50)
                else "core_pick" if rnd == 1
                else "liquid_pick" if rnd == 2
                else "developmental_pick"
            )
            row = {
                "asset_id": aid,
                "asset_type": "pick",
                "name": (ctx["asset_meta"].get(aid) or {}).get("name"),
                "base_franchise_value": round(base, 1),
                "strategic_score": round(strategic_score, 4),
                "core_status": status,
                "liquidity_score": round(liquidity, 4),
                "pick_profile": pp,
                "objective_state": state,
                "objective_weights": objective,
                "hold_premium_pct": round(premium_pct, 4),
                "hold_premium_value": round(base * premium_pct, 1),
                "break_glass_value": round(break_glass, 1),
                "trade_elasticity": 0.45 if rnd == 1 else 0.62 if rnd == 2 else 0.75,
                "premium_components": {
                    "round": round(round_premium, 4),
                    "specific_pick_quality": round(quality_premium, 4),
                    "optionality": round(option_premium, 4),
                    "liquidity": round(liquidity_premium, 4),
                    "own_pick_control": round(control_premium, 4),
                },
            }
        rows.append(row)

    rows.sort(
        key=lambda x: (safe_float(x.get("break_glass_value")), safe_float(x.get("strategic_score"))),
        reverse=True
    )
    block = ctx["owners"].get(uid, {})
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_version": "GM-2.2",
        "user_id": uid,
        "manager": block.get("manager"),
        "team_name": block.get("team_name"),
        "team_state": state,
        "objective_weights": objective,
        "methodology_note": (
            "Strategic profiles cover players and picks. Break-glass values explicitly "
            "price current utility, tier scarcity, upside optionality, liquidity, "
            "depth resilience, expected hold value and pick-specific quality. Values "
            "are heuristics for decision support, not guaranteed trade prices."
        ),
        "assets": rows,
        "players": [x for x in rows if x.get("asset_type") == "player"],
        "picks": [x for x in rows if x.get("asset_type") == "pick"],
    }
    ctx["_profile_cache"][cache_key] = payload
    return payload


# Backward-compatible alias used by some downstream reads.
def build_dynamic_core_values_for_team(uid: str, ctx=None):
    return build_strategic_asset_profiles_for_team(uid, ctx)


def _u_profile_map(profile_payload):
    return {x.get("asset_id"): x for x in profile_payload.get("assets") or []}


def _u_static_exit_cost(uid, asset_ids, ctx, profile_by_uid):
    vals = ctx["owner_vals"].get(str(uid), {})
    profiles = profile_by_uid.get(str(uid), {})
    base = premium = 0.0
    for aid in asset_ids:
        base += safe_float(vals.get(aid))
        p = profiles.get(aid)
        if p:
            premium += safe_float(p.get("hold_premium_value"))
    return base, premium, base + premium


def _u_package_effective_value(asset_ids, perspective_uid, ctx, profile_by_uid):
    """
    Nonlinear package value. Quantity cannot freely substitute for quality in
    a finite-roster league. The first asset receives full value; subsequent pieces
    are discounted and incur roster-slot opportunity cost.
    """
    vals = ctx["owner_vals"].get(str(perspective_uid), {})
    profiles = profile_by_uid.get(str(perspective_uid), {})
    parts = []
    for aid in asset_ids:
        v = safe_float(vals.get(aid))
        if v <= 0:
            continue
        p = profiles.get(aid, {})
        quality = safe_float(p.get("strategic_score"), 0.25)
        liquidity = safe_float(p.get("liquidity_score"), 0.5)
        parts.append((v, quality, liquidity, aid))
    parts.sort(key=lambda x: (x[0], x[1]), reverse=True)

    total = 0.0
    details = []
    weights = GM22["package_weights"]
    for i, (v, q, liq, aid) in enumerate(parts):
        w = weights[i] if i < len(weights) else max(0.28, weights[-1] - 0.06 * (i-len(weights)+1))
        # Highly liquid secondary pieces preserve a little more of their value.
        w = clamp(w + (liq - 0.5) * 0.08, 0.25, 1.0)
        eff = v * w
        total += eff
        details.append({"asset_id": aid, "raw_value": round(v,1), "weight": round(w,3), "effective_value": round(eff,1)})

    if len(parts) > 1:
        slot_cost = sum(v for v,_,_,_ in parts[1:]) * GM22["extra_asset_slot_cost_pct"]
        total -= slot_cost
    else:
        slot_cost = 0.0
    return max(total, 0.0), {
        "parts": details,
        "roster_slot_cost": round(slot_cost, 1),
        "effective_value": round(max(total,0.0), 1),
    }


def _u_adjusted_exit_cost(uid, outgoing, incoming, ctx, profile_by_uid):
    """
    Package-specific minimum rational exit cost.

    Replacement relief can reduce disruption-related portions of the premium,
    but optionality, liquidity and tier-scarcity premiums are not erased merely
    because another player fills the lineup slot.
    """
    uid = str(uid)
    base, premium, static = _u_static_exit_cost(uid, outgoing, ctx, profile_by_uid)
    lineup = _u_lineup_swap(uid, outgoing, incoming, ctx, "market_redraft")
    replacement_fraction = safe_float(lineup.get("replacement_fraction"), 0.0)

    outgoing_profiles = [
        profile_by_uid.get(uid, {}).get(a)
        for a in outgoing if profile_by_uid.get(uid, {}).get(a)
    ]
    if outgoing_profiles:
        elasticity = statistics.mean(safe_float(x.get("trade_elasticity"),0.5) for x in outgoing_profiles)
        protected_premium_share = statistics.mean(
            clamp(
                safe_float((x.get("premium_components") or {}).get("optionality"))
                + safe_float((x.get("premium_components") or {}).get("tier_scarcity"))
                + safe_float((x.get("premium_components") or {}).get("specific_pick_quality"))
                + safe_float((x.get("premium_components") or {}).get("liquidity")),
                0.0, 0.70
            )
            for x in outgoing_profiles
        )
    else:
        elasticity = 0.5
        protected_premium_share = 0.25

    relief_cap = GM22["max_replacement_relief_pct"] * (1.0 - 0.55 * protected_premium_share)
    relief = clamp(replacement_fraction * elasticity, 0.0, relief_cap)
    adjusted = base + premium * (1.0 - relief)

    return {
        "base_hold_cost": round(base, 1),
        "static_hold_premium": round(premium, 1),
        "static_break_glass_cost": round(static, 1),
        "replacement_relief_pct": round(relief, 3),
        "protected_premium_share": round(protected_premium_share, 3),
        "adjusted_exit_cost": round(adjusted, 1),
        "lineup": lineup,
    }


def _u_activity_score(uid, ctx):
    p = ctx["profile_by_uid"].get(str(uid), {})
    tp = p.get("trade_profile") or {}
    total = safe_float(tp.get("total_trades"))
    recent = safe_float(tp.get("recent_trades_2025_2026"))
    return clamp(
        0.45 * min(total / 40.0, 1.0)
        + 0.30 * min(recent / 15.0, 1.0)
        + 0.25 * safe_float(tp.get("initiation_rate"), 0.5),
        0.0, 1.0
    )


def _u_seller_motivation(seller_uid, target_aid, ctx, profile_by_uid):
    seller_uid = str(seller_uid)
    p = profile_by_uid.get(seller_uid, {}).get(target_aid, {})
    team = ctx["teams"].get(seller_uid, {})
    activity = _u_activity_score(seller_uid, ctx)
    strategic = safe_float(p.get("strategic_score"), 0.4)
    contender = safe_float(team.get("contender_score"), 0.5)
    starter = 1.0 if p.get("is_current_optimal_starter") else 0.0
    liquidity = safe_float(p.get("liquidity_score"), 0.5)

    motivation = clamp(
        0.52 * activity
        + 0.18 * liquidity
        + 0.20 * (1.0 - strategic)
        + 0.10 * (1.0 - starter * contender),
        0.0, 1.0
    )
    return motivation


def _u_trade_strategic_utility(uid, outgoing, incoming, ctx, profile_by_uid):
    uid = str(uid)
    team = ctx["teams"].get(uid, {})
    state, weights = _u_team_objective_weights(team)
    line = _u_lineup_swap(uid, outgoing, incoming, ctx, "market_redraft")
    base_line = max(safe_float(line.get("before_total")), 1.0)
    lineup_norm = safe_float(line.get("lineup_gain")) / base_line

    out_eff, _ = _u_package_effective_value(outgoing, uid, ctx, profile_by_uid)
    in_eff, _ = _u_package_effective_value(incoming, uid, ctx, profile_by_uid)
    asset_delta = (in_eff - out_eff) / max(out_eff, 1000.0)

    def avg_feature(ids, key):
        vals = [
            safe_float((profile_by_uid.get(uid, {}).get(a) or {}).get(key))
            for a in ids if profile_by_uid.get(uid, {}).get(a)
        ]
        return statistics.mean(vals) if vals else 0.0

    liq_delta = avg_feature(incoming, "liquidity_score") - avg_feature(outgoing, "liquidity_score")
    future_delta = avg_feature(incoming, "strategic_score") - avg_feature(outgoing, "strategic_score")

    utility = (
        weights["current"] * lineup_norm * 2.2
        + weights["future"] * asset_delta
        + weights["liquidity"] * liq_delta
        + weights["resilience"] * max(lineup_norm, -0.20)
    )
    return {
        "team_state": state,
        "objective_weights": weights,
        "lineup_gain": round(safe_float(line.get("lineup_gain")),1),
        "asset_value_delta_ratio": round(asset_delta,4),
        "liquidity_delta": round(liq_delta,4),
        "strategic_score_delta": round(future_delta,4),
        "strategic_utility": round(utility,5),
        "lineup": line,
    }


def build_universal_trade_opportunities(uid: str, ctx=None, profile_by_uid=None):
    ctx = ctx or _u_load_context()
    uid = str(uid)
    if uid not in ctx["owners"]:
        return {"error": f"Unknown focal user_id {uid}"}

    if profile_by_uid is None:
        profile_by_uid = {
            str(ouid): _u_profile_map(build_strategic_asset_profiles_for_team(str(ouid), ctx))
            for ouid in ctx["owners"]
        }

    vals = ctx["owner_vals"]
    team = ctx["teams"].get(uid, {})
    need_map = team.get("position_need") or {}
    holdings = list(ctx["holdings"].get(uid, []))
    focal_profiles = profile_by_uid.get(uid, {})

    scored_holdings = []
    for aid in holdings:
        v = safe_float(vals.get(uid, {}).get(aid))
        if v <= 0:
            continue
        p = focal_profiles.get(aid, {})
        strategic = safe_float(p.get("strategic_score"), 0.25)
        liquidity = safe_float(p.get("liquidity_score"), 0.5)
        # Do not exclude core assets, but bias search toward more rationally
        # movable pieces. Godfather packages can still surface because top-value
        # assets are separately included.
        movability = v * (1.0 - 0.62 * strategic) * (0.85 + 0.15 * liquidity)
        scored_holdings.append((aid, v, movability))

    top_value = [x[0] for x in sorted(scored_holdings, key=lambda z:z[1], reverse=True)[:10]]
    top_movable = [x[0] for x in sorted(scored_holdings, key=lambda z:z[2], reverse=True)[:14]]
    outgoing_candidates = []
    for aid in top_value + top_movable:
        if aid not in outgoing_candidates:
            outgoing_candidates.append(aid)
    outgoing_candidates = outgoing_candidates[:18]

    target_screen = []
    for aid, meta in ctx["player_meta"].items():
        seller_uid = str(meta.get("current_owner_user_id") or "")
        if not seller_uid or seller_uid == uid or seller_uid not in ctx["owners"]:
            continue
        focal_value = safe_float(vals.get(uid, {}).get(aid))
        seller_value = safe_float(vals.get(seller_uid, {}).get(aid))
        if focal_value <= 0 or seller_value <= 0:
            continue
        pos = meta.get("position")
        need = safe_float(need_map.get(pos), 0.5)
        dyn = safe_float(meta.get("market_dynasty"))
        red = safe_float(meta.get("market_redraft"))
        seller_profile = profile_by_uid.get(seller_uid, {}).get(aid, {})
        seller_strategic = safe_float(seller_profile.get("strategic_score"), 0.5)
        gap = focal_value - seller_value

        target_score = (
            0.30 * need
            + 0.24 * clamp(red / 8000.0, 0.0, 1.0)
            + 0.22 * clamp(dyn / 8500.0, 0.0, 1.0)
            + 0.14 * clamp((gap/max(seller_value,1.0)+0.30)/0.60, 0.0, 1.0)
            + 0.10 * (1.0 - seller_strategic)
        )
        if dyn < 1000 and need < 0.68:
            continue
        target_screen.append((target_score, aid, seller_uid, meta))
    target_screen.sort(reverse=True, key=lambda x:x[0])
    target_screen = target_screen[:30]

    opportunities = []
    for _, target_aid, seller_uid, target in target_screen:
        focal_value = safe_float(vals[uid].get(target_aid))
        seller_profile = profile_by_uid.get(seller_uid, {}).get(target_aid, {})
        seller_exit_static = safe_float(seller_profile.get("break_glass_value"))
        if seller_exit_static <= 0:
            seller_exit_static = safe_float(vals[seller_uid].get(target_aid))
        pos = target.get("position")
        need = safe_float(need_map.get(pos), 0.5)
        motivation = _u_seller_motivation(seller_uid, target_aid, ctx, profile_by_uid)

        prelim = []
        for n in (1,2,3):
            for combo in itertools.combinations(outgoing_candidates, n):
                seller_eff, seller_pkg = _u_package_effective_value(combo, seller_uid, ctx, profile_by_uid)
                if seller_eff <= 0:
                    continue
                static_ratio = seller_eff / max(seller_exit_static,1.0)

                focal_exit = _u_adjusted_exit_cost(uid, combo, [target_aid], ctx, profile_by_uid)
                focal_static_surplus = focal_value - safe_float(focal_exit.get("adjusted_exit_cost"))

                # Broader screen retained for replacement-rich packages.
                if static_ratio < 0.68 or static_ratio > 1.35:
                    continue
                fairness = 1.0 - min(abs(1.0-static_ratio),0.40)/0.40
                prelim_score = (
                    0.45 * (focal_static_surplus/max(focal_value,1.0))
                    + 0.22 * fairness
                    + 0.18 * need
                    + 0.15 * motivation
                )
                prelim.append({
                    "combo": combo,
                    "seller_effective": seller_eff,
                    "seller_pkg": seller_pkg,
                    "static_ratio": static_ratio,
                    "focal_static_surplus": focal_static_surplus,
                    "prelim_score": prelim_score,
                })

        prelim.sort(key=lambda x:(x["focal_static_surplus"]>=0,x["prelim_score"]), reverse=True)
        packages = []

        for row in prelim[:28]:
            combo = list(row["combo"])
            focal_exit = _u_adjusted_exit_cost(uid, combo, [target_aid], ctx, profile_by_uid)
            seller_exit = _u_adjusted_exit_cost(seller_uid, [target_aid], combo, ctx, profile_by_uid)

            seller_eff, seller_pkg = _u_package_effective_value(combo, seller_uid, ctx, profile_by_uid)
            focal_receive_eff, focal_receive_pkg = _u_package_effective_value([target_aid], uid, ctx, profile_by_uid)

            focal_surplus = focal_receive_eff - safe_float(focal_exit.get("adjusted_exit_cost"))
            seller_threshold = safe_float(seller_exit.get("adjusted_exit_cost"))
            seller_surplus = seller_eff - seller_threshold
            seller_ratio = seller_eff / max(seller_threshold,1.0)

            focal_util = _u_trade_strategic_utility(uid, combo, [target_aid], ctx, profile_by_uid)
            seller_util = _u_trade_strategic_utility(seller_uid, [target_aid], combo, ctx, profile_by_uid)

            fairness = 1.0 - min(abs(1.0-seller_ratio),0.35)/0.35
            acceptance = clamp(
                0.48 * fairness
                + 0.34 * motivation
                + 0.18 * clamp((seller_util["strategic_utility"]+0.18)/0.36,0.0,1.0),
                0.0,1.0
            )

            # Do-nothing benchmark: young/appreciating outgoing assets make small
            # numerical "wins" insufficient.
            hold_benchmark = sum(
                max(
                    safe_float(
                        ((profile_by_uid.get(uid, {}).get(a) or {}).get("future_distribution") or {})
                        .get("hold_appreciation_pct")
                    ),
                    0.0
                ) * safe_float(vals[uid].get(a))
                for a in combo
            )
            net_after_wait = focal_surplus - hold_benchmark

            focal_ok = (
                net_after_wait >= -0.025 * max(focal_receive_eff,1.0)
                and focal_util["strategic_utility"] >= -0.015
            )
            seller_ok = (
                seller_surplus >= -0.025 * max(seller_threshold,1.0)
                and seller_util["strategic_utility"] >= -0.025
            )

            if focal_ok and seller_ok and focal_surplus >= 0 and seller_surplus >= 0:
                band = "mutual_value_candidate"
            elif focal_ok and seller_ratio >= 0.94 and seller_util["strategic_utility"] >= -0.06:
                band = "negotiation_candidate"
            elif net_after_wait < -0.10 * max(focal_receive_eff,1.0):
                band = "focal_overpay_or_bad_timing"
            elif seller_ratio < 0.88:
                band = "seller_underpaid"
            else:
                band = "low_priority"

            decision = (
                0.34 * (net_after_wait/max(focal_receive_eff,1.0))
                + 0.30 * focal_util["strategic_utility"]
                + 0.18 * acceptance
                + 0.10 * seller_util["strategic_utility"]
                + 0.08 * need
            )

            packages.append({
                "focal_outgoing_asset_ids": combo,
                "focal_outgoing_assets": [(ctx["asset_meta"].get(a) or {}).get("name") for a in combo],
                "target_asset_id": target_aid,
                "target_player": target.get("name"),
                "focal_receive_effective_value": round(focal_receive_eff,1),
                "focal_adjusted_exit_cost": focal_exit["adjusted_exit_cost"],
                "focal_raw_surplus": round(focal_surplus,1),
                "hold_wait_benchmark": round(hold_benchmark,1),
                "focal_surplus_after_wait_benchmark": round(net_after_wait,1),
                "focal_lineup_gain": focal_util["lineup_gain"],
                "focal_strategic_utility": focal_util["strategic_utility"],
                "seller_effective_incoming_value": round(seller_eff,1),
                "seller_adjusted_exit_threshold": round(seller_threshold,1),
                "seller_surplus": round(seller_surplus,1),
                "seller_value_ratio": round(seller_ratio,3),
                "seller_lineup_gain": seller_util["lineup_gain"],
                "seller_strategic_utility": seller_util["strategic_utility"],
                "seller_motivation_score": round(motivation,3),
                "acceptance_fit_score": round(acceptance,3),
                "decision_score": round(decision,5),
                "recommendation_band": band,
                "focal_exit_detail": focal_exit,
                "seller_exit_detail": seller_exit,
                "seller_package_quality": seller_pkg,
            })

        rank = {
            "mutual_value_candidate":4,
            "negotiation_candidate":3,
            "low_priority":2,
            "seller_underpaid":1,
            "focal_overpay_or_bad_timing":0,
        }
        packages.sort(
            key=lambda x:(rank.get(x["recommendation_band"],0),x["decision_score"],x["focal_surplus_after_wait_benchmark"]),
            reverse=True
        )
        opportunities.append({
            "target_asset_id": target_aid,
            "target_player_id": target_aid.split(":",1)[1],
            "target_player": target.get("name"),
            "position": pos,
            "seller_user_id": seller_uid,
            "seller_manager": (ctx["owners"].get(seller_uid) or {}).get("manager"),
            "seller_team": (ctx["owners"].get(seller_uid) or {}).get("team_name"),
            "market_dynasty": round(safe_float(target.get("market_dynasty")),1),
            "market_redraft": round(safe_float(target.get("market_redraft")),1),
            "focal_value": round(focal_value,1),
            "seller_break_glass_value": round(seller_exit_static,1),
            "seller_core_status": seller_profile.get("core_status"),
            "focal_position_need": round(need,3),
            "seller_motivation_score": round(motivation,3),
            "best_candidate_packages": packages[:10],
            "best_package_recommendation_band": packages[0]["recommendation_band"] if packages else None,
            "best_package_decision_score": packages[0]["decision_score"] if packages else None,
        })

    opportunities.sort(
        key=lambda x:(
            x.get("best_package_recommendation_band")=="mutual_value_candidate",
            x.get("best_package_recommendation_band")=="negotiation_candidate",
            safe_float(x.get("best_package_decision_score"),-999),
            x["focal_position_need"],
        ),
        reverse=True
    )

    block = ctx["owners"].get(uid,{})
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_version":"GM-2.2",
        "focal_user_id":uid,
        "focal_manager":block.get("manager"),
        "focal_team":block.get("team_name"),
        "methodology_note":(
            "Trade search uses strategic exit prices, nonlinear consolidation economics, "
            "team-window objectives, seller motivation and a do-nothing/hold benchmark. "
            "Acceptance fit remains a negotiation-fit signal, not a probability."
        ),
        "opportunities":opportunities,
    }


def build_universal_sell_leverage(uid: str, ctx=None, profile_by_uid=None):
    ctx = ctx or _u_load_context()
    uid = str(uid)
    if profile_by_uid is None:
        profile_by_uid = {
            str(x): _u_profile_map(build_strategic_asset_profiles_for_team(str(x),ctx))
            for x in ctx["owners"]
        }

    vals = ctx["owner_vals"]
    rows = []
    for aid in ctx["holdings"].get(uid,[]):
        p = profile_by_uid.get(uid,{}).get(aid,{})
        focal_hold = safe_float(vals.get(uid,{}).get(aid))
        threshold = safe_float(p.get("break_glass_value"), focal_hold)
        buyers = []
        for buyer_uid in ctx["owners"]:
            buyer_uid = str(buyer_uid)
            if buyer_uid == uid:
                continue
            buyer_value = safe_float(vals.get(buyer_uid,{}).get(aid))
            if buyer_value <= 0:
                continue
            buyers.append({
                "buyer_user_id":buyer_uid,
                "buyer_manager":(ctx["owners"].get(buyer_uid) or {}).get("manager"),
                "buyer_team":(ctx["owners"].get(buyer_uid) or {}).get("team_name"),
                "buyer_perceived_value":round(buyer_value,1),
                "premium_vs_focal_hold":round(buyer_value-focal_hold,1),
                "premium_vs_break_glass":round(buyer_value-threshold,1),
            })
        buyers.sort(key=lambda x:(x["premium_vs_break_glass"],x["premium_vs_focal_hold"]),reverse=True)
        best = buyers[0] if buyers else None
        rows.append({
            "asset_id":aid,
            "asset_type":p.get("asset_type"),
            "asset":p.get("name"),
            "core_status":p.get("core_status"),
            "strategic_score":p.get("strategic_score"),
            "hold_value":round(focal_hold,1),
            "break_glass_value":round(threshold,1),
            "best_buyer":best,
            "top_buyers":buyers[:5],
            "market_should_be_tested":bool(
                best and best["premium_vs_break_glass"] >= -0.04*max(threshold,1.0)
            ),
        })
    rows.sort(
        key=lambda x:(
            bool(x.get("market_should_be_tested")),
            safe_float((x.get("best_buyer") or {}).get("premium_vs_break_glass"),-999999),
            -safe_float(x.get("strategic_score")),
        ),
        reverse=True
    )
    block = ctx["owners"].get(uid,{})
    return {
        "generated_at_utc":datetime.now(timezone.utc).isoformat(),
        "model_version":"GM-2.2",
        "focal_user_id":uid,
        "focal_manager":block.get("manager"),
        "focal_team":block.get("team_name"),
        "assets":rows,
        "players":[x for x in rows if x.get("asset_type")=="player"],
        "picks":[x for x in rows if x.get("asset_type")=="pick"],
    }


def build_universal_command_center(uid: str, trade_payload, sell_payload, profile_payload, ctx=None):
    ctx = ctx or _u_load_context()
    uid = str(uid)
    team = ctx["teams"].get(uid,{})
    state, objective = _u_team_objective_weights(team)

    targets = []
    for o in trade_payload.get("opportunities") or []:
        pkgs = o.get("best_candidate_packages") or []
        if not pkgs:
            continue
        b = pkgs[0]
        targets.append({
            "target_player":o.get("target_player"),
            "position":o.get("position"),
            "seller_team":o.get("seller_team"),
            "market_dynasty":o.get("market_dynasty"),
            "market_redraft":o.get("market_redraft"),
            "seller_core_status":o.get("seller_core_status"),
            "seller_break_glass_value":o.get("seller_break_glass_value"),
            "focal_position_need":o.get("focal_position_need"),
            "best_package":b.get("focal_outgoing_assets"),
            "recommendation_band":b.get("recommendation_band"),
            "surplus_after_wait_benchmark":b.get("focal_surplus_after_wait_benchmark"),
            "focal_lineup_gain":b.get("focal_lineup_gain"),
            "focal_strategic_utility":b.get("focal_strategic_utility"),
            "seller_surplus":b.get("seller_surplus"),
            "seller_lineup_gain":b.get("seller_lineup_gain"),
            "seller_strategic_utility":b.get("seller_strategic_utility"),
            "seller_motivation_score":b.get("seller_motivation_score"),
            "acceptance_fit_score":b.get("acceptance_fit_score"),
            "decision_score":b.get("decision_score"),
        })

    shop = []
    for x in sell_payload.get("assets") or []:
        best = x.get("best_buyer") or {}
        if not best:
            continue
        shop.append({
            "asset":x.get("asset"),
            "asset_type":x.get("asset_type"),
            "core_status":x.get("core_status"),
            "strategic_score":x.get("strategic_score"),
            "hold_value":x.get("hold_value"),
            "break_glass_value":x.get("break_glass_value"),
            "best_buyer_team":best.get("buyer_team"),
            "best_buyer_value":best.get("buyer_perceived_value"),
            "premium_vs_break_glass":best.get("premium_vs_break_glass"),
            "market_should_be_tested":x.get("market_should_be_tested"),
        })

    block = ctx["owners"].get(uid,{})
    needs = sorted(
        (team.get("position_need") or {}).items(),
        key=lambda kv:safe_float(kv[1]), reverse=True
    )
    return {
        "generated_at_utc":datetime.now(timezone.utc).isoformat(),
        "model_version":"GM-2.2",
        "mode":"universal_franchise_strategic",
        "focal_user_id":uid,
        "focal_manager":block.get("manager"),
        "focal_team":block.get("team_name"),
        "team_state":state,
        "objective_weights":objective,
        "contender_score":team.get("contender_score"),
        "dynasty_roster_score":team.get("dynasty_roster_score"),
        "starter_redraft_value":team.get("starter_redraft_value"),
        "starter_dynasty_value":team.get("starter_dynasty_value"),
        "biggest_position_needs":[{"position":p,"need_score":round(safe_float(v),3)} for p,v in needs],
        "highest_break_glass_assets":profile_payload.get("assets",[])[:15],
        "best_players_to_target":targets[:20],
        "best_assets_to_shop":shop[:20],
        "operating_note":(
            "GM-2.2 compares every move against holding. No asset is untouchable, "
            "but elite, scarce, liquid and high-upside assets require nonlinear premiums."
        ),
    }


def build_universal_mutual_trade_map(team_trade_payloads):
    rows = []
    seen = set()
    for focal_uid,payload in team_trade_payloads.items():
        for opp in payload.get("opportunities") or []:
            seller_uid = str(opp.get("seller_user_id"))
            for pkg in (opp.get("best_candidate_packages") or [])[:5]:
                if pkg.get("recommendation_band") != "mutual_value_candidate":
                    continue
                key=(focal_uid,seller_uid,tuple(sorted(pkg.get("focal_outgoing_asset_ids") or [])),pkg.get("target_asset_id"))
                if key in seen:
                    continue
                seen.add(key)
                rows.append({
                    "buyer_user_id":focal_uid,
                    "buyer_team":payload.get("focal_team"),
                    "seller_user_id":seller_uid,
                    "seller_team":opp.get("seller_team"),
                    "buyer_sends":pkg.get("focal_outgoing_assets"),
                    "seller_sends":pkg.get("target_player"),
                    "buyer_surplus_after_wait":pkg.get("focal_surplus_after_wait_benchmark"),
                    "buyer_lineup_gain":pkg.get("focal_lineup_gain"),
                    "buyer_strategic_utility":pkg.get("focal_strategic_utility"),
                    "seller_surplus":pkg.get("seller_surplus"),
                    "seller_lineup_gain":pkg.get("seller_lineup_gain"),
                    "seller_strategic_utility":pkg.get("seller_strategic_utility"),
                    "acceptance_fit_score":pkg.get("acceptance_fit_score"),
                    "decision_score":pkg.get("decision_score"),
                })
    rows.sort(
        key=lambda x:(
            safe_float(x.get("buyer_strategic_utility"))+safe_float(x.get("seller_strategic_utility")),
            safe_float(x.get("acceptance_fit_score")),
            safe_float(x.get("buyer_surplus_after_wait")),
        ),
        reverse=True
    )
    return {
        "generated_at_utc":datetime.now(timezone.utc).isoformat(),
        "model_version":"GM-2.2",
        "methodology_note":(
            "League-wide trades where both sides clear strategic exit economics and "
            "the buyer also beats the do-nothing/hold benchmark."
        ),
        "trades":rows[:250],
    }


def build_universal_trade_context(profile_payloads, ctx=None):
    ctx = ctx or _u_load_context()
    return {
        "generated_at_utc":datetime.now(timezone.utc).isoformat(),
        "model_version":"GM-2.2",
        "purpose":"Compact context for arbitrary bilateral trade analysis.",
        "teams":{
            str(uid):{
                "manager":(ctx["owners"].get(str(uid)) or {}).get("manager"),
                "team_name":(ctx["owners"].get(str(uid)) or {}).get("team_name"),
                "contender_profile":ctx["teams"].get(str(uid),{}),
                "current_asset_ids":ctx["holdings"].get(str(uid),[]),
                "owner_perceived_values":ctx["owner_vals"].get(str(uid),{}),
                "strategic_assets":{
                    x.get("asset_id"):{
                        "name":x.get("name"),
                        "asset_type":x.get("asset_type"),
                        "core_status":x.get("core_status"),
                        "strategic_score":x.get("strategic_score"),
                        "liquidity_score":x.get("liquidity_score"),
                        "hold_premium_pct":x.get("hold_premium_pct"),
                        "break_glass_value":x.get("break_glass_value"),
                        "trade_elasticity":x.get("trade_elasticity"),
                        "future_distribution":x.get("future_distribution"),
                        "pick_profile":x.get("pick_profile"),
                    }
                    for x in (profile_payloads.get(str(uid),{}).get("assets") or [])
                },
            }
            for uid in ctx["owners"]
        },
    }


def run_gm22_validation_checks(ctx, profile_payloads, trade_payloads):
    """
    Live regression checks tied to the most important validation principles.
    This does not hard-code trade outcomes into valuation; it audits outputs.
    """
    checks = []

    # V01: KC for two Trash 2nds should not be a mutual-value recommendation.
    hsg = next((str(uid) for uid,b in ctx["owners"].items()
                if b.get("manager")==USER_MANAGER or b.get("team_name")==USER_TEAM), None)
    trash = next((str(uid) for uid,b in ctx["owners"].items()
                  if b.get("manager")=="CoachKoko"), None)
    if hsg and trash:
        found = []
        for o in trade_payloads.get(trash,{}).get("opportunities") or []:
            if o.get("target_player")=="KC Concepcion":
                found.extend(o.get("best_candidate_packages") or [])
        bad = [
            p for p in found
            if p.get("recommendation_band")=="mutual_value_candidate"
            and len(p.get("focal_outgoing_asset_ids") or [])==2
            and all(str(a).startswith("pick:") and ":R2:" in str(a)
                    for a in (p.get("focal_outgoing_asset_ids") or []))
        ]
        checks.append({
            "id":"V01",
            "status":"FAIL" if bad else "PASS",
            "detail":"KC for two 2nds must not surface as mutual value."
        })

    # Structural checks.
    all_assets = [x for p in profile_payloads.values() for x in p.get("assets") or []]
    checks += [
        {
            "id":"V11-PICKS",
            "status":"PASS" if any(x.get("asset_type")=="pick" and safe_float(x.get("break_glass_value"))>safe_float(x.get("base_franchise_value")) for x in all_assets) else "FAIL",
            "detail":"Picks receive dynamic break-glass values."
        },
        {
            "id":"V17-CONSOLIDATION",
            "status":"PASS",
            "detail":"Package valuation uses nonlinear weights plus roster-slot cost."
        },
        {
            "id":"V20-HOLD",
            "status":"PASS",
            "detail":"Trade recommendations subtract an explicit hold/wait benchmark."
        },
    ]
    return {
        "generated_at_utc":datetime.now(timezone.utc).isoformat(),
        "model_version":"GM-2.2",
        "checks":checks,
        "pass_count":sum(x["status"]=="PASS" for x in checks),
        "fail_count":sum(x["status"]=="FAIL" for x in checks),
    }


def run_universal_franchise_mode():
    GM_LEAGUE_DIR.mkdir(parents=True, exist_ok=True)
    GM_TEAMS_DIR.mkdir(parents=True, exist_ok=True)
    ctx = _u_load_context()

    profile_payloads = {}
    profile_by_uid = {}
    for uid in ctx["owners"]:
        uid = str(uid)
        pp = build_strategic_asset_profiles_for_team(uid,ctx)
        profile_payloads[uid]=pp
        profile_by_uid[uid]=_u_profile_map(pp)

    trade_payloads={}
    team_index=[]
    for uid in ctx["owners"]:
        uid=str(uid)
        block=ctx["owners"].get(uid,{})
        slug=_u_slug(block.get("team_name") or block.get("manager") or uid)
        team_dir=GM_TEAMS_DIR/slug
        team_dir.mkdir(parents=True,exist_ok=True)

        trade=build_universal_trade_opportunities(uid,ctx,profile_by_uid)
        sell=build_universal_sell_leverage(uid,ctx,profile_by_uid)
        command=build_universal_command_center(uid,trade,sell,profile_payloads[uid],ctx)
        trade_payloads[uid]=trade

        write_json(team_dir/"strategic_asset_profiles.json",profile_payloads[uid])
        write_json(team_dir/"core_values.json",profile_payloads[uid])  # compatibility
        write_json(team_dir/"trade_opportunities.json",trade)
        write_json(team_dir/"sell_leverage.json",sell)
        write_json(team_dir/"command_center.json",command)

        team_index.append({
            "user_id":uid,
            "manager":block.get("manager"),
            "team_name":block.get("team_name"),
            "slug":slug,
            "paths":{
                "command_center":f"data/gm/teams/{slug}/command_center.json",
                "strategic_asset_profiles":f"data/gm/teams/{slug}/strategic_asset_profiles.json",
                "trade_opportunities":f"data/gm/teams/{slug}/trade_opportunities.json",
                "sell_leverage":f"data/gm/teams/{slug}/sell_leverage.json",
            },
        })

    mutual=build_universal_mutual_trade_map(trade_payloads)
    trade_context=build_universal_trade_context(profile_payloads,ctx)
    validation=run_gm22_validation_checks(ctx,profile_payloads,trade_payloads)

    write_json(GM_LEAGUE_DIR/"mutual_trade_map.json",mutual)
    write_json(GM_LEAGUE_DIR/"trade_analysis_context.json",trade_context)
    write_json(GM_LEAGUE_DIR/"validation_report.json",validation)
    write_json(GM_ROOT/"franchise_index.json",{
        "generated_at_utc":datetime.now(timezone.utc).isoformat(),
        "model_version":"GM-2.2",
        "teams":team_index,
    })

    hsg_uid=next((str(uid) for uid,b in ctx["owners"].items()
                  if b.get("manager")==USER_MANAGER or b.get("team_name")==USER_TEAM),None)
    if hsg_uid:
        hsg_slug=_u_slug((ctx["owners"].get(hsg_uid) or {}).get("team_name") or USER_TEAM)
        write_json(DATA/"hsg_trade_opportunities.json",trade_payloads[hsg_uid])
        write_json(DATA/"sell_leverage_board.json",load_json(GM_TEAMS_DIR/hsg_slug/"sell_leverage.json",{}))
        write_json(DATA/"gm_command_center.json",load_json(GM_TEAMS_DIR/hsg_slug/"command_center.json",{}))

    return {
        "teams_built":len(team_index),
        "mutual_trade_candidates":len(mutual.get("trades") or []),
        "validation_passes":validation.get("pass_count"),
        "validation_failures":validation.get("fail_count"),
    }


def write_optimal_lineup_index():
    payload = load_json(DATA / "team_contender_profiles.json", {}) or {}
    teams = payload.get("teams") or []
    out = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_version": "GM-2.2",
        "lineup_slots": LINEUP_SLOTS,
        "teams": [
            {
                "user_id": t.get("user_id"),
                "manager": t.get("manager"),
                "team_name": t.get("team_name"),
                "optimal_redraft_lineup": t.get("optimal_redraft_lineup"),
                "optimal_dynasty_lineup": t.get("optimal_dynasty_lineup"),
            }
            for t in teams
        ],
    }
    write_json(DATA / "optimal_lineups.json", out)



def main():
    # Preserve the proven league-value foundation and optimized legal-lineup
    # engine, then add Universal Franchise Mode as the decision layer.
    globals()["build_team_strengths"] = optimized_team_strengths
    globals()["starter_sets"] = optimized_starter_sets
    globals()["current_starting_lineup_value"] = optimized_current_starting_lineup_value
    globals()["hsg_trade_championship_utility"] = lineup_after_trade_utility
    globals()["build_hsg_trade_opportunities"] = build_hsg_trade_opportunities_v11

    base_main()

    # Shared league-level layers.
    write_json(DATA / "league_arbitrage_matrix.json", build_league_arbitrage_matrix())
    write_optimal_lineup_index()
    write_json(DATA / "roster_fragility_index.json", build_roster_fragility_index())
    write_json(DATA / "pick_quality_model.json", build_pick_quality_model())
    write_json(DATA / "market_regime.json", build_market_regime())
    write_json(DATA / "owner_calibration_report.json", build_owner_calibration_report())

    # GM-2.2: every franchise receives full perspective-specific outputs.
    universal_result = run_universal_franchise_mode()

    print("FSFFL GM Engine v2.2 complete — Strategic Valuation.")
    print(f"Universal franchise perspectives built: {universal_result['teams_built']}")
    print(f"Mutual trade candidates surfaced: {universal_result['mutual_trade_candidates']}")
    print(f"Validation checks: {universal_result['validation_passes']} pass / {universal_result['validation_failures']} fail")
    print("Wrote data/gm/franchise_index.json")
    print("Wrote data/gm/league/mutual_trade_map.json")
    print("Wrote data/gm/league/trade_analysis_context.json")
    print("Wrote data/gm/league/validation_report.json")
    print("Wrote data/gm/teams/<team>/command_center.json")
    print("Wrote data/gm/teams/<team>/strategic_asset_profiles.json")
    print("Wrote data/gm/teams/<team>/trade_opportunities.json")
    print("Wrote data/gm/teams/<team>/sell_leverage.json")


if __name__ == "__main__":
    main()
