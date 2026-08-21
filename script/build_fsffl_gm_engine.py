#!/usr/bin/env python3
"""
FSFFL GM Engine v1.1.3 — STANDALONE

Single-file full GM model. Includes the original GM-1.0 market/data foundation plus:
1) independently optimized legal FSFFL starting lineups;
2) trade-package ranking prioritized by HSG surplus and optimal-lineup improvement;
3) sell-leverage board across opponent-specific valuations.

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

DATA = Path("data")
DATA.mkdir(parents=True, exist_ok=True)

FANTASYCALC_URL = "https://api.fantasycalc.com/values/current"
FC_PARAMS = {
    "isDynasty": "true",
    "numQbs": 2,
    "numTeams": 12,
    "ppr": 0.5,
}
FC_REDRAFT_PARAMS = {
    "isDynasty": "false",
    "numQbs": 2,
    "numTeams": 12,
    "ppr": 0.5,
}


NFLVERSE_PLAYER_STATS_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "player_stats/player_stats.csv.gz"
)
NFLVERSE_SNAP_COUNTS_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "snap_counts/snap_counts_2026.csv"
)

# Optional file for qualitative/camp/news intelligence created by a human/agent.
# The valuation engine remains fully functional if it does not exist.
FOOTBALL_INTELLIGENCE_OVERRIDES = DATA / "football_intelligence_overrides.json"


USER_MANAGER = "jimmygoodjob"
USER_TEAM = "Hurts So Good"

# Assets we do not want the offer generator to use automatically.
# They still receive valuations and can be evaluated manually.
PROTECTED_HSG_PLAYERS = {
    "Lamar Jackson",
    "Dak Prescott",
    "CeeDee Lamb",
    "Drake London",
    "Tee Higgins",
    "DeVonta Smith",
    "Zay Flowers",
    "Quinshon Judkins",
    "Kyle Pitts",
}

FUTURE_PICK_YEARS = [2027, 2028, 2029]
ROUNDS = [1, 2, 3]
POSITIONS = ("QB", "RB", "WR", "TE")

CONFIG = {
    "model_version": "GM-1.0",
    "market_source": "FantasyCalc current values",
    "market_settings": {
        "dynasty": True,
        "num_qbs": 2,
        "num_teams": 12,
        "ppr": 0.5,
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
        m_year = re.search(r"(202[6-9]|2030)", n)
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
    year_discount = {2027: 1.0, 2028: 0.88, 2029: 0.77}.get(year, 0.70)
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


def load_recent_performance(active_season=2026):
    """Load current-season FSFFL weekly production when available."""
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


def fetch_nflverse_usage(active_season: int = 2026) -> Dict[str, Dict[str, Any]]:
    """
    Fetch weekly player stats and derive role/usage signals:
      - carries
      - targets
      - touches
      - team opportunity shares
      - short-window trend

    Uses player name + team as the bridge when Sleeper IDs are not present.
    """
    cache = DATA / "nflverse_usage_2026.json"
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


def fetch_nflverse_snaps(active_season: int = 2026) -> Dict[str, Dict[str, Any]]:
    """
    Fetch game-level offensive snap counts. nflverse snap counts are polled
    multiple times per day during the season.
    """
    cache = DATA / "nflverse_snap_counts_2026.json"
    fetched_at = datetime.now(timezone.utc).isoformat()
    try:
        rows = _read_csv_url(NFLVERSE_SNAP_COUNTS_URL, gzipped=False)
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
    market_idx = build_market_indexes(market)
    detected_pick_values = infer_fc_pick_values(market)

    player_values = build_player_values(rosters, players, market_idx)
    owner_by_player = current_owner_by_player(rosters)
    starters = starter_sets(rosters)

    performance = load_recent_performance(active_season=2026)
    performance_baselines = build_performance_baselines(performance, player_values)
    usage = fetch_nflverse_usage(active_season=2026)
    snaps = fetch_nflverse_snaps(active_season=2026)
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
CONFIG["model_version"] = "GM-1.1.3"
CONFIG["notes"] = list(CONFIG.get("notes") or []) + [
    "GM-1.1 independently optimizes legal starting lineups; Sleeper's current starters are not treated as authoritative.",
    "GM-1.1 ranks trade packages by HSG surplus and optimal-lineup gain before acceptance fit.",
    "GM-1.1 creates a sell-leverage board across every opponent valuation.",
    "GM-1.1.1 fixes runtime dispatch so optimized-lineup and trade-ranking functions are actually used by base_main.",
]

FALLBACK_LINEUP_SLOTS = [
    "QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "SUPER_FLEX"
]


def lineup_slots() -> List[str]:
    league = load_json(DATA / "league.json", {}) or {}
    raw = league.get("roster_positions") or FALLBACK_LINEUP_SLOTS
    slots = [str(x).upper() for x in raw]
    # Sleeper roster_positions contains bench/IR/taxi entries too.
    legal = {"QB", "RB", "WR", "TE", "FLEX", "SUPER_FLEX", "SUPERFLEX"}
    filtered = ["SUPER_FLEX" if x == "SUPERFLEX" else x for x in slots if x in legal]
    return filtered or list(FALLBACK_LINEUP_SLOTS)


LINEUP_SLOTS = lineup_slots()


def eligible(position: str, slot: str) -> bool:
    pos = (position or "").upper()
    slot = slot.upper()
    if slot in {"QB", "RB", "WR", "TE"}:
        return pos == slot
    if slot == "FLEX":
        return pos in {"RB", "WR", "TE"}
    if slot == "SUPER_FLEX":
        return pos in {"QB", "RB", "WR", "TE"}
    return False


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
        pos = a.get("position")
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
        elif slot == "FLEX":
            flex_slots.append(("FLEX", ("RB", "WR", "TE")))
        elif slot == "SUPER_FLEX":
            flex_slots.append(("SUPER_FLEX", ("QB", "RB", "WR", "TE")))

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
            eligible_positions = (
                (slot,) if slot in POSITIONS
                else ("RB", "WR", "TE") if slot == "FLEX"
                else ("QB", "RB", "WR", "TE") if slot == "SUPER_FLEX"
                else ()
            )
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
        elif slot == "FLEX":
            pos = next(flex_iter)
        elif slot == "SUPER_FLEX":
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
        "model_version": "GM-1.1.3",
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
        "model_version": "GM-1.1.3",
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


def write_optimal_lineup_index():
    payload = load_json(DATA / "team_contender_profiles.json", {}) or {}
    teams = payload.get("teams") or []
    out = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_version": "GM-1.1.3",
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
    # Patch the v1.0 decision layer in module globals so base_main() actually
    # resolves the GM-1.1 implementations at runtime.
    globals()["build_team_strengths"] = optimized_team_strengths
    globals()["starter_sets"] = optimized_starter_sets
    globals()["current_starting_lineup_value"] = optimized_current_starting_lineup_value
    globals()["hsg_trade_championship_utility"] = lineup_after_trade_utility
    globals()["build_hsg_trade_opportunities"] = build_hsg_trade_opportunities_v11

    base_main()

    write_json(DATA / "sell_leverage_board.json", build_sell_leverage_board())
    write_optimal_lineup_index()

    print("FSFFL GM Engine v1.1 overlay complete.")
    print("Wrote data/sell_leverage_board.json")
    print("Wrote data/optimal_lineups.json")


if __name__ == "__main__":
    main()
