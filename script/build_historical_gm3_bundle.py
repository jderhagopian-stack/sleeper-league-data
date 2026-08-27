#!/usr/bin/env python3
"""Build a time-frozen GM 3.0 input bundle for a historical FSFFL trade.

The builder reconstructs only information knowable at the transaction timestamp.
For the April 10, 2023 Josh Allen calibration case it uses:
- exact pre-trade roster/pick state from HistoricalStateProvider;
- historical competitive state reconstructed by the existing pre-trade state engine;
- prior-completed-season FSFFL production to rebuild the football/projection universe;
- only manager behavior observed before the transaction;
- the prior season's fantasy schedule as a neutral schedule proxy, avoiding the
  not-yet-known 2023 schedule;
- contemporaneous PFF/FantasyPros values only as market ANCHORS and validation
  evidence, never as the final team-specific GM value.

The output is an input bundle, not a trade score. Historical Trade Analysis then
passes it through the same GM 3.0 / Decision Lab / What-If logic used by current
analysis. The final value is produced by GM 3.0 from the reconstructed world.
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import math
import statistics
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict

from fsffl_historical_state_provider import HistoricalStateProvider, completed_transactions, roster_to_user
from build_behavioral_action_context import player_index, owner_directory

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SCRIPT = ROOT / "script"
MODEL_VERSION = "FSFFL-Historical-GM3-Bundle-Builder-1.0"
POSITIONS = ("QB", "RB", "WR", "TE")
DEFAULT_SOURCE = DATA / "historical_gm3" / "sources" / "2023-04-10-josh-allen.json"


def loadj(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to import {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def historical_rosters(state, data):
    latest = {str(r.get("roster_id")): r for r in (data.get("rosters") or [])}
    r2u = roster_to_user(data)
    rows = []
    for rid, pids in sorted(state.roster_players.items(), key=lambda kv: int(kv[0])):
        base = copy.deepcopy(latest.get(str(rid)) or {})
        base["roster_id"] = int(rid)
        base["owner_id"] = r2u.get(str(rid)) or base.get("owner_id")
        base["players"] = sorted(str(x) for x in pids)
        base["taxi"] = sorted(str(x) for x in state.roster_taxi.get(str(rid), set()))
        base["reserve"] = sorted(str(x) for x in state.roster_reserve.get(str(rid), set()))
        rows.append(base)
    return rows


def prior_stats(season: int, players):
    rows = loadj(DATA / "stats" / "fsffl" / str(season - 1) / "player_season_fsffl.json", [])
    out = {}
    pos_ppg = defaultdict(list)
    for r in rows:
        pid = str(r.get("player_id") or "")
        pos = str(r.get("position") or (players.get(pid) or {}).get("position") or "")
        if pos not in POSITIONS:
            continue
        try:
            games = int(r.get("games_with_stats") or 0)
            ppg = float(r.get("fsffl_ppg") or 0.0)
        except Exception:
            continue
        if games >= 4 and ppg >= 0:
            out[pid] = {"ppg": ppg, "games": games, "position": pos}
            pos_ppg[pos].append(ppg)
    baselines = {
        pos: (statistics.median(vals) if vals else {"QB": 12, "RB": 5, "WR": 5, "TE": 4}[pos])
        for pos, vals in pos_ppg.items()
    }
    for pos in POSITIONS:
        baselines.setdefault(pos, {"QB": 12, "RB": 5, "WR": 5, "TE": 4}[pos])
    return out, baselines


def age_at(meta, season: int):
    bd = meta.get("birth_date")
    if bd:
        try:
            d = date.fromisoformat(str(bd)[:10])
            return season - d.year - ((9, 1) < (d.month, d.day))
        except Exception:
            pass
    cur = meta.get("age")
    try:
        return max(18, float(cur) - max(0, datetime.now(timezone.utc).year - season))
    except Exception:
        return 26.0


def name_alias(name):
    return str(name or "").replace("Joshua Palmer", "Josh Palmer").strip()


def rank_proxy_value(ppg: float, pos: str, by_pos):
    peers = sorted(by_pos.get(pos) or [ppg], reverse=True)
    if not peers:
        return 1000.0
    better = sum(1 for x in peers if x > ppg)
    pct = 1.0 - better / max(1, len(peers))
    # Only a fallback for players absent from the dated market source. This is
    # intentionally compressed so exact dated source values dominate.
    return 600.0 + 5900.0 * (pct ** 1.35)


def build_player_values(rosters, players, prior, baselines, source):
    pff = {
        name_alias(k): float(v)
        for k, v in (((source.get("player_source") or {}).get("values")) or {}).items()
    }
    by_pos = defaultdict(list)
    for row in prior.values():
        by_pos[row["position"]].append(float(row["ppg"]))

    roster_pids = sorted({str(pid) for r in rosters for pid in (r.get("players") or [])})
    values = {}
    source_exact = 0
    for pid in roster_pids:
        meta = players.get(pid) or {}
        pos = str(meta.get("position") or "")
        if pos not in POSITIONS:
            continue
        name = name_alias(meta.get("full_name") or meta.get("name") or pid)
        ppg = float((prior.get(pid) or {}).get("ppg") or baselines[pos] * 0.72)
        redraft = max(250.0, ppg * (350.0 if pos == "QB" else 430.0))
        exact = pff.get(name)
        if exact is not None:
            dynasty = exact * 100.0
            source_exact += 1
        else:
            dynasty = rank_proxy_value(ppg, pos, by_pos)
        values[pid] = {
            "player_id": pid,
            "name": name,
            "position": pos,
            "age": round(age_at(meta, 2023), 1),
            "years_exp": max(0.0, float(meta.get("years_exp") or 0) - 3.0),
            "draft_round": meta.get("draft_round"),
            "injury_status": "PUP" if name == "Kyler Murray" else None,
            "trend_30_day": 0.0,
            "market_dynasty": round(dynasty, 1),
            "market_redraft": round(redraft, 1),
            "source": "dated_pff_2qb" if exact is not None else "prior_completed_season_proxy",
            "prior_season_fsffl_ppg": round(ppg, 3),
        }
    ranked = sorted(values.items(), key=lambda kv: kv[1]["market_dynasty"], reverse=True)
    for i, (_, row) in enumerate(ranked, 1):
        row["market_rank"] = i
    return values, source_exact


def projection_bundle(values, prior, baselines):
    players = {}
    cv = {"QB": 0.28, "RB": 0.52, "WR": 0.48, "TE": 0.50}
    for pid, a in values.items():
        pos = a["position"]
        mean = float((prior.get(pid) or {}).get("ppg") or baselines[pos] * 0.72)
        weeks = {}
        for week in range(1, 18):
            active = 1.0
            if a["name"] == "Kyler Murray":
                # At the trade date his December ACL tear was known; his actual
                # 2023 return date is deliberately NOT used. A flat availability
                # haircut represents recovery uncertainty without learning from
                # same-season results.
                active = 0.62
            sd = max(1.0, mean * cv[pos])
            weeks[str(week)] = {
                "active_probability": active,
                "is_bye": False,
                "mean": round(mean, 3),
                "median": round(mean * 0.96, 3),
                "p25": round(max(0.0, mean - 0.67 * sd), 3),
                "p75": round(mean + 0.67 * sd, 3),
                "sd": round(sd, 3),
            }
        players[pid] = {
            "name": a["name"],
            "position": pos,
            "season_baseline_ppg": round(mean, 3),
            "historical_games_for_player_volatility": int((prior.get(pid) or {}).get("games") or 0),
            "volatility_cv": cv[pos],
            "volatility_source": "prior_completed_season_position_proxy",
            "weeks": weeks,
        }
    return {
        "model_stage": "historical_preseason_asof_proxy",
        "players": players,
        "policy": {
            "same_season_results_used": False,
            "future_nfl_schedule_used": False,
            "known_injury_information_only": True,
        },
    }


def build_historical_lineup_cache(rosters, league, players, projections):
    """Pre-optimize the frozen baseline once, matching Decision Lab cache semantics.

    Historical preseason proxies are often stationary across weeks. Avoid
    repeating the exact DFS lineup optimization 17 times by memoizing on the
    roster's projection signature. If a future historical bundle has true
    week-specific projections, each distinct signature is still optimized
    separately, preserving normal Decision Lab semantics.
    """
    simmod = load_module(SCRIPT / "build_fsffl_season_simulator.py", "historical_bundle_simulator")
    reg_weeks = simmod.regular_season_weeks(league)
    playoff_start = int((league.get("settings") or {}).get("playoff_week_start") or 15)
    weeks = sorted(set(reg_weeks + [playoff_start, playoff_start + 1, playoff_start + 2]))
    pproj = (projections or {}).get("players") or {}

    def signature(roster, week):
        rows = []
        taxi = set(str(x) for x in (roster.get("taxi") or []))
        for pid in sorted(str(x) for x in (roster.get("players") or [])):
            if pid in taxi:
                continue
            w = ((pproj.get(pid) or {}).get("weeks") or {}).get(str(week))
            if not w:
                rows.append((pid, None, None))
            else:
                rows.append((
                    pid,
                    float(w.get("mean", w.get("median", 0.0)) or 0.0),
                    float(w.get("active_probability", 1.0) or 0.0),
                ))
        return tuple(rows)

    lineups = {}
    unique_optimizations = 0
    for roster in rosters:
        rid = int(roster.get("roster_id"))
        lineups[str(rid)] = {}
        memo = {}
        for week in weeks:
            sig = signature(roster, week)
            if sig not in memo:
                memo[sig] = simmod.optimize_weekly_lineup(
                    roster, week, league, players, projections
                )
                unique_optimizations += 1
            lineups[str(rid)][str(week)] = copy.deepcopy(memo[sig])
    return {
        "simulator_model_version": simmod.MODEL_VERSION,
        "execution_path": "preoptimized_frozen_baseline_signature_memoized",
        "weeks": weeks,
        "lineups": lineups,
        "unique_lineup_optimizations": unique_optimizations,
        "nominal_roster_week_optimizations": len(rosters) * len(weeks),
    }


def historical_behavior_profiles(provider, season: int, ts: int, players):
    counts = defaultdict(lambda: {
        "trade_acq": defaultdict(int), "draft": defaultdict(int), "waiver": defaultdict(int),
        "firsts_acquired": 0, "seconds_acquired": 0, "thirds_acquired": 0,
        "firsts_sent": 0, "seconds_sent": 0, "thirds_sent": 0,
        "draft_volume": 0,
    })
    for sy in provider.seasons():
        syi = int(sy)
        if syi > season:
            continue
        data = provider.data(sy)
        r2u = roster_to_user(data)
        cutoff = ts if syi == season else 10**18
        for tx in completed_transactions(data):
            created = int(tx.get("created") or 0)
            if created >= cutoff:
                continue
            typ = str(tx.get("type") or "")
            adds = tx.get("adds") or {}
            if typ == "trade":
                for pid, rid in adds.items():
                    uid = r2u.get(str(rid))
                    pos = str((players.get(str(pid)) or {}).get("position") or "")
                    if uid and pos in POSITIONS:
                        counts[uid]["trade_acq"][pos] += 1
                for p in tx.get("draft_picks") or []:
                    rnd = int(p.get("round") or 0)
                    new = r2u.get(str(p.get("owner_id")))
                    old = r2u.get(str(p.get("previous_owner_id")))
                    keya = {1:"firsts_acquired",2:"seconds_acquired",3:"thirds_acquired"}.get(rnd)
                    keys = {1:"firsts_sent",2:"seconds_sent",3:"thirds_sent"}.get(rnd)
                    if new and keya: counts[new][keya] += 1
                    if old and keys: counts[old][keys] += 1
            elif typ in {"waiver", "free_agent"}:
                for pid, rid in adds.items():
                    uid = r2u.get(str(rid))
                    pos = str((players.get(str(pid)) or {}).get("position") or "")
                    if uid and pos in POSITIONS:
                        counts[uid]["waiver"][pos] += 1

        for entry in data.get("drafts") or []:
            d = entry.get("draft") or {}
            start = int(d.get("start_time") or d.get("created") or 0)
            if start <= 0 or start >= cutoff:
                continue
            for p in entry.get("picks") or []:
                uid = str(p.get("picked_by") or "")
                pid = str(p.get("player_id") or (p.get("metadata") or {}).get("player_id") or "")
                pos = str((players.get(pid) or {}).get("position") or "")
                if uid and pos in POSITIONS:
                    counts[uid]["draft"][pos] += 1
                    counts[uid]["draft_volume"] += 1

    profiles = []
    all_uids = {
        str(u.get("user_id")) for sy in provider.seasons() for u in (provider.data(sy).get("users") or [])
        if u.get("user_id") is not None
    }
    for uid in all_uids:
        c = counts[uid]
        profiles.append({
            "user_id": uid,
            "trade_profile": {
                "player_positions_acquired": dict(c["trade_acq"]),
                "firsts_acquired": c["firsts_acquired"], "seconds_acquired": c["seconds_acquired"], "thirds_acquired": c["thirds_acquired"],
                "firsts_sent": c["firsts_sent"], "seconds_sent": c["seconds_sent"], "thirds_sent": c["thirds_sent"],
            },
            "rookie_draft_profile": {
                "positions": dict(c["draft"]),
                "rookie_picks_made_2023_plus": c["draft_volume"],
            },
            "waiver_profile": {"positions_added": dict(c["waiver"])},
        })
    return profiles


def draft_slot_map(data):
    """Map original roster id -> rookie draft slot without reading draft results.

    The previous implementation inferred the mapping from completed pick rows.
    That is both semantically unsafe (roster_id on a pick can reflect the drafter
    rather than the original pick owner) and unnecessarily exposes post-trade
    draft results. Use Sleeper's draft_order metadata instead, which represents
    the league's draft-order assignment rather than who/what was selected.
    """
    out = {}
    u2r = {str(r.get("owner_id")): str(r.get("roster_id"))
           for r in (data.get("rosters") or [])
           if r.get("owner_id") is not None and r.get("roster_id") is not None}
    for entry in data.get("drafts") or []:
        d = entry.get("draft") or {}
        order = d.get("draft_order") or {}
        for uid, slot in order.items():
            rid = u2r.get(str(uid))
            try:
                slot_i = int(slot)
            except Exception:
                continue
            if rid and slot_i > 0:
                out[str(rid)] = slot_i
        if out:
            break
    return out


def parse_pick(aid):
    _, year, rnd, orig = str(aid).split(":", 3)
    return int(year), int(rnd.lstrip("R")), str(orig).replace("orig", "")


def future_pick_source_value(year, rnd, orig, team_profiles, slot_map, source):
    pff = ((source.get("pick_sources") or [{}])[0].get("values") or {})
    fp = ((source.get("pick_sources") or [{},{}])[1].get("values") or {})
    if year == 2023 and rnd in {1,2,3}:
        slot = slot_map.get(str(orig))
        if slot:
            key = f"{rnd}.{slot:02d}"
            return float(pff.get(key) or 0.0), f"PFF exact {key}", 1.0

    uid_by_orig = None
    # team_profiles are keyed by uid, while orig is roster id; the caller adds
    # original_uid in pick construction and can overwrite this fallback.
    if rnd == 1:
        base = float(fp.get("2024_top6_R1") or 45)
    elif rnd == 2:
        base = (float(fp.get("2024_early_R2") or 25) + float(fp.get("2024_late_R2") or 17)) / 2
    else:
        base = (float(fp.get("2024_early_R3") or 13) + float(fp.get("2024_late_R3") or 8)) / 2
    discount = 0.88 ** max(0, year - 2024)
    return base * discount, "FantasyPros future-pick proxy", 0.72


def neutral_schedule():
    p = DATA / "stats" / "fsffl" / "2022" / "league_matchups_raw.json"
    raw = loadj(p, {})
    return {str(k): v for k, v in raw.items() if int(k) <= 14}


def build(season: str, transaction_id: str, source_path: Path):
    provider = HistoricalStateProvider()
    data = provider.data(str(season))
    tx = next(t for t in completed_transactions(data) if str(t.get("transaction_id")) == str(transaction_id))
    ts = int(tx.get("created") or 0)
    state = provider.pre_transaction_state(str(season), str(transaction_id))
    rosters = historical_rosters(state, data)
    players = player_index()
    prior, baselines = prior_stats(int(season), players)
    source = loadj(source_path, {})
    values, exact_count = build_player_values(rosters, players, prior, baselines, source)

    gm = load_module(SCRIPT / "build_fsffl_gm_engine.py", "historical_bundle_gm")
    hist_state_mod = load_module(SCRIPT / "historical_state_behavior.py", "historical_bundle_state")
    profile_by_uid = {}
    owners = owner_directory(data)
    for uid, row in owners.items():
        profile_by_uid[str(uid)] = {
            "user_id": str(uid),
            "manager": row.get("manager"),
            "team_name": row.get("team_name"),
            "username": row.get("manager"),
        }

    team_profiles = gm.optimized_team_strengths(rosters, values, profile_by_uid)
    behavior_profiles = historical_behavior_profiles(provider, int(season), ts, players)
    prefs = gm.build_behavior_preferences(behavior_profiles)
    starters = gm.optimized_starter_sets(rosters)

    owner_by_player = {}
    for r in rosters:
        uid = str(r.get("owner_id"))
        for pid in r.get("players") or []:
            owner_by_player[str(pid)] = uid

    r2u = roster_to_user(data)
    slots = draft_slot_map(data)
    draft_slot_source = "sleeper_draft_order_metadata_no_pick_results" if slots else "unresolved_no_posttrade_pick_result_fallback"
    action_pick_ids = set()
    for p in tx.get("draft_picks") or []:
        action_pick_ids.add(f"pick:{p.get('season')}:R{p.get('round')}:orig{p.get('roster_id')}")

    pick_assets = {}
    pick_quality = {}
    for aid in sorted(action_pick_ids):
        year, rnd, orig = parse_pick(aid)
        source_val, basis, conf = future_pick_source_value(year, rnd, orig, team_profiles, slots, source)
        original_uid = r2u.get(str(orig))
        contender = float((team_profiles.get(str(original_uid)) or {}).get("contender_score") or 0.5)
        if contender <= 0.33:
            q, tier, ew, lw = 0.78, "early", 0.58, 0.14
        elif contender >= 0.67:
            q, tier, ew, lw = 0.28, "late", 0.14, 0.58
        else:
            q, tier, ew, lw = 0.52, "mid", 0.30, 0.30
        owner_rid = state.pick_owners.get(aid, str(orig))
        owner_uid = r2u.get(str(owner_rid))
        pick_assets[aid] = {
            "asset_id": aid, "asset_type": "pick", "name": aid,
            "market_dynasty": round(source_val * 100.0, 1),
            "current_owner_user_id": owner_uid,
            "original_owner_user_id": original_uid,
            "round": rnd, "season": year,
            "source_basis": basis, "source_confidence": conf,
        }
        pick_quality[aid] = {
            "asset_id": aid, "round": rnd, "season": year,
            "original_owner_user_id": original_uid,
            "quality_signal": q, "most_likely_tier": tier,
            "early_scenario_weight": ew, "late_scenario_weight": lw,
            "confidence": "high" if conf >= .95 else "medium",
        }

    owner_vals = {}
    all_uids = sorted(team_profiles)
    for uid in all_uids:
        owner_vals[uid] = {}
        for pid, a in values.items():
            if owner_by_player.get(pid) == uid:
                v, _ = gm.owner_player_hold_value(uid, a, team_profiles, prefs, starters)
            else:
                v, _ = gm.owner_player_buy_value(uid, a, team_profiles, prefs)
            owner_vals[uid][f"player:{pid}"] = round(v, 1)
        for aid, p in pick_assets.items():
            hold = str(p.get("current_owner_user_id")) == uid
            v, _ = gm.owner_pick_value(uid, p, team_profiles, prefs, hold)
            owner_vals[uid][aid] = round(v, 1)

    holdings = {}
    roster_by_uid = {}
    for r in rosters:
        uid = str(r.get("owner_id"))
        roster_by_uid[uid] = [str(x) for x in (r.get("players") or [])]
        holdings[uid] = [f"player:{x}" for x in roster_by_uid[uid]]
    for aid, p in pick_assets.items():
        uid = str(p.get("current_owner_user_id") or "")
        if uid:
            holdings.setdefault(uid, []).append(aid)

    player_meta = {f"player:{pid}": dict(a) for pid, a in values.items()}
    asset_meta = {**player_meta, **pick_assets}
    ctx = {
        "owners": {uid: owners.get(uid, {}) for uid in all_uids},
        "teams": team_profiles,
        "assets_payload": {},
        "player_meta": player_meta,
        "asset_meta": asset_meta,
        "owner_vals": owner_vals,
        "holdings": holdings,
        "profiles": behavior_profiles,
        "profile_by_uid": profile_by_uid,
        "rosters": rosters,
        "roster_by_uid": roster_by_uid,
        "roster_id_by_uid": {str(r.get("owner_id")): int(r.get("roster_id")) for r in rosters},
        "fragility": {},
        "pick_quality": pick_quality,
        "market_regime": {},
        "_profile_cache": {},
        "_depth_cache": {},
    }

    gm_asset_maps = {}
    derived_team_states = {}
    strategic_profiles = {}
    for uid in all_uids:
        payload = gm.build_strategic_asset_profiles_for_team(uid, ctx)
        strategic_profiles[uid] = payload
        derived_team_states[uid] = payload.get("team_state")
        gm_asset_maps[uid] = {str(a.get("asset_id")): a for a in (payload.get("assets") or [])}

    # Canonical historical competitive-state layer: use the already-built
    # pre-trade reconstruction rather than inferring contender/rebuild status
    # from today's framework or from the external market anchors.
    historical_state_index = hist_state_mod.build_index()
    historical_side_rows = [
        row for row in (historical_state_index.get("sides") or [])
        if str(row.get("transaction_id")) == str(transaction_id)
    ]
    historical_state_by_uid = {
        str(row.get("user_id")): str(row.get("historical_state") or "unknown")
        for row in historical_side_rows
    }
    historical_state_confidence = {
        str(row.get("user_id")): float(row.get("historical_state_confidence") or 0.0)
        for row in historical_side_rows
    }
    historical_state_details = {
        str(row.get("user_id")): {
            "state": str(row.get("historical_state") or "unknown"),
            "score": row.get("historical_state_score"),
            "confidence": row.get("historical_state_confidence"),
            "phase": row.get("phase"),
            "reconstruction_mode": row.get("reconstruction_mode"),
            "performance_source_season": row.get("performance_source_season"),
            "performance_through_week": row.get("performance_through_week"),
            "performance_evidence": row.get("performance_evidence"),
            "approx_pretrade_average_age": row.get("approx_pretrade_average_age"),
        }
        for row in historical_side_rows
    }
    team_states = {
        uid: historical_state_by_uid.get(uid) or derived_team_states.get(uid) or "unknown"
        for uid in all_uids
    }

    market_player_values = {
        f"player:{pid}": {
            "name": a["name"], "dynasty": a["market_dynasty"],
            "redraft": a["market_redraft"], "fsffl": a["market_dynasty"],
        }
        for pid, a in values.items()
    }
    market_pick_values = {
        aid: {
            "name": p["name"], "dynasty": p["market_dynasty"],
            "redraft": 0.0, "fsffl": p["market_dynasty"],
        }
        for aid, p in pick_assets.items()
    }

    projection = projection_bundle(values, prior, baselines)
    league = copy.deepcopy(data.get("league") or {})
    users = copy.deepcopy(data.get("users") or [])
    schedule = neutral_schedule()
    optimized_lineup_cache = build_historical_lineup_cache(
        rosters, league, players, projection
    )

    return {
        "model_version": MODEL_VERSION,
        "as_of_transaction_id": str(transaction_id),
        "as_of_utc": datetime.fromtimestamp(ts/1000, tz=timezone.utc).isoformat().replace("+00:00","Z"),
        "league": league,
        "users": users,
        "historical_rosters": rosters,
        "historical_transaction": tx,
        "historical_state_snapshot": {
            "season": int(season),
            "timestamp_ms": ts,
            "roster_players": {str(k): sorted(str(x) for x in v) for k, v in state.roster_players.items()},
            "roster_taxi": {str(k): sorted(str(x) for x in v) for k, v in state.roster_taxi.items()},
            "roster_reserve": {str(k): sorted(str(x) for x in v) for k, v in state.roster_reserve.items()},
            "pick_owners": {str(k): str(v) for k, v in state.pick_owners.items()},
            "faab_used": {str(k): v for k, v in state.faab_used.items()},
            "reconstruction": state.reconstruction,
        },
        "players": players,
        "projections": projection,
        "schedule": schedule,
        "optimized_lineup_cache": optimized_lineup_cache,
        "gm_asset_maps": gm_asset_maps,
        "market_player_values": market_player_values,
        "market_pick_values": market_pick_values,
        "team_states": team_states,
        "historical_state_details": historical_state_details,
        "team_profiles": team_profiles,
        "historical_behavior_preferences": prefs,
        "strategic_profiles": strategic_profiles,
        "provenance": {
            "historical_state": state.reconstruction,
            "market_source_file": str(source_path.relative_to(ROOT)),
            "market_source_role": "anchor_and_validation_only_not_final_gm_value",
            "dated_exact_trade_player_market_anchors": exact_count,
            "historical_team_state_source": historical_state_index.get("model_version"),
            "historical_team_state_confidence": historical_state_confidence,
            "projection_basis": f"{int(season)-1} completed-season FSFFL PPG",
            "schedule_basis": f"{int(season)-1} FSFFL schedule reused as neutral known proxy",
            "behavior_basis": "only actions with timestamp strictly before trade",
            "historical_lineup_cache": "preoptimized once in frozen bundle; reused by GM3 trade analysis",
            "draft_slot_source": draft_slot_source,
            "completed_draft_pick_rows_used_for_slot_resolution": False,
            "current_market_values_used": False,
            "same_season_results_used": False,
            "future_schedule_used": False,
            "external_market_anchor_is_final_value": False,
            "final_team_specific_value_source": "GM3_owner_value_plus_strategic_profile_plus_trade_impact",
        },
        "confidence": {
            "historical_roster_state": state.reconstruction.get("confidence"),
            "traded_player_market_anchors": "high",
            "historical_competitive_state": "explicit per-team confidence in provenance",
            "2023_pick_market_anchor": "high when exact slot resolved",
            "future_pick_market_values": "medium",
            "full_roster_market_values": "medium-low proxy",
            "season_projection": "medium-low prior-season proxy",
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", required=True)
    ap.add_argument("--transaction-id", required=True)
    ap.add_argument("--source", default=str(DEFAULT_SOURCE))
    ap.add_argument("--output", required=True)
    a = ap.parse_args()
    result = build(a.season, a.transaction_id, Path(a.source))
    out = Path(a.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "model_version": result["model_version"],
        "as_of_utc": result["as_of_utc"],
        "team_states": result["team_states"],
        "provenance": result["provenance"],
        "confidence": result["confidence"],
        "output": str(out),
    }, indent=2))


if __name__ == "__main__":
    main()
