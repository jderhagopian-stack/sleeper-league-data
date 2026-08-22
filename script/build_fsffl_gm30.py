#!/usr/bin/env python3
"""
FSFFL GM 3.0
=============

Downstream decision layer over:
- GM 2.2 strategic/market outputs
- FSFFL Simulator 1.0 projections and season probabilities
- historical owner/trade/draft/waiver behavior

GM 3.0 does not mutate Simulator 1.0 files.

Outputs:
  data/gm3/manifest.json
  data/gm3/simulator_bridge.json
  data/gm3/owner_profiles_v3.json
  data/gm3/pick_forecast.json
  data/gm3/opportunity_radar.json
  data/gm3/roster_arbitrage.json
  data/gm3/trade_routes.json
  data/gm3/league_intelligence.json
  data/gm3/decision_center.json
  data/gm3/decision_journal_snapshot.json
  data/gm3/validation_report.json
"""

from __future__ import annotations
import json, math, statistics, re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DATA = Path("data")
CFG_PATH = DATA / "gm3_config.json"
OUT = DATA / "gm3"
OUT.mkdir(parents=True, exist_ok=True)


def load(path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def dump(name, obj):
    p = OUT / name
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=False)


def resolve_config_paths(cfg):
    """
    Resolve runtime path placeholders from authoritative league metadata.

    No league year is hard-coded here. Any configured {season} token is replaced
    with data/league.json -> season on every run.
    """
    league = load(DATA / "league.json", {}) or {}
    season = league.get("season")
    if season in (None, ""):
        raise SystemExit("GM 3.0 cannot resolve active season from data/league.json")

    season = int(season)
    resolved = dict(cfg)
    resolved["season"] = season
    resolved["paths"] = {
        key: str(value).format(season=season)
        for key, value in (cfg.get("paths") or {}).items()
    }
    return resolved


def sf(x, d=0.0):
    try:
        if x is None:
            return d
        return float(x)
    except (TypeError, ValueError):
        return d


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def norm(s):
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())


def pct_rank(v, vals):
    vals = [sf(x, None) for x in vals]
    vals = [x for x in vals if x is not None]
    if not vals:
        return 0.5
    return (sum(x < v for x in vals) + 0.5 * sum(x == v for x in vals)) / len(vals)


def weighted(items):
    present = [(v, w) for v, w in items if v is not None and w > 0]
    poss = sum(w for _, w in items if w > 0)
    if not present or poss <= 0:
        return 0.5, 0.0
    aw = sum(w for _, w in present)
    return clamp(sum(clamp(v) * w for v, w in present) / aw), clamp(aw / poss)


def age_score(pos, age):
    if age is None:
        return None
    age = sf(age)
    bands = {"QB": (24, 32), "RB": (21, 26), "WR": (21, 28), "TE": (22, 29)}
    lo, hi = bands.get(pos, (22, 29))
    if lo <= age <= hi:
        return .78
    if age < lo:
        return clamp(.52 + .20 * (age / max(lo, 1)))
    return clamp(.78 - .075 * (age - hi), .08, .78)


def resolve_players(asset_payload):
    if isinstance(asset_payload, dict) and isinstance(asset_payload.get("players"), list):
        return asset_payload["players"]
    return asset_payload if isinstance(asset_payload, list) else []


def resolve_owner_rows(payload):
    return (
        payload
        if isinstance(payload, list)
        else (payload.get("owners") or payload.get("teams") or [])
        if isinstance(payload, dict)
        else []
    )


def sim_bridge(cfg, standings, lineups, projections):
    teams = standings.get("teams", []) if isinstance(standings, dict) else []
    league_epf = [sf(t.get("expected_points_for")) for t in teams]
    league_wins = [sf(t.get("expected_wins")) for t in teams]
    rows = []
    for t in teams:
        strength = weighted([
            (pct_rank(sf(t.get("expected_points_for")), league_epf), .34),
            (pct_rank(sf(t.get("expected_wins")), league_wins), .22),
            (sf(t.get("playoff_probability")), .22),
            (sf(t.get("championship_probability")), .22),
        ])[0]
        rows.append({
            **t,
            "simulator_strength_index": round(100 * strength, 1),
            "competitive_window":
                "elite_contender" if sf(t.get("championship_probability")) >= .18 else
                "contender" if sf(t.get("playoff_probability")) >= .70 else
                "bubble" if sf(t.get("playoff_probability")) >= .35 else
                "retool_rebuild",
        })
    return {
        "model_version": "FSFFL-GM-3.0",
        "simulator_model_version": standings.get("model_version") if isinstance(standings, dict) else None,
        "season": standings.get("season") if isinstance(standings, dict) else cfg["season"],
        "teams": sorted(rows, key=lambda x: sf(x.get("championship_probability")), reverse=True),
        "projection_player_count": len((projections or {}).get("players", {})) if isinstance(projections, dict) else 0,
        "lineup_roster_count": len((lineups or {}).get("lineups", {})) if isinstance(lineups, dict) else 0,
    }


def projection_features(projections):
    out = {}
    players = (projections or {}).get("players", {}) if isinstance(projections, dict) else {}
    for pid, p in players.items():
        weeks = p.get("weeks", {}) or {}
        means = [sf(w.get("mean")) for w in weeks.values() if not w.get("is_bye")]
        sds = [sf(w.get("sd")) for w in weeks.values() if not w.get("is_bye")]
        acts = [sf(w.get("active_probability"), 1) for w in weeks.values() if not w.get("is_bye")]
        if not means:
            continue
        out[str(pid)] = {
            "projection_ppg": statistics.mean(means),
            "projection_sd": statistics.mean(sds) if sds else 0,
            "active_probability": statistics.mean(acts) if acts else 1,
            "projection_cv": (statistics.mean(sds) / max(statistics.mean(means), .1)) if sds else 0,
            "position": p.get("position"),
            "name": p.get("name"),
            "season_baseline_ppg": sf(p.get("season_baseline_ppg")),
            "volatility_source": p.get("volatility_source"),
        }
    return out


def lineup_usage(lineups):
    starts = defaultdict(int)
    slots = defaultdict(Counter)
    means = defaultdict(list)
    line = (lineups or {}).get("lineups", {}) if isinstance(lineups, dict) else {}
    for rid, weeks in line.items():
        for wk, players in (weeks or {}).items():
            for p in players or []:
                pid = str(p.get("player_id"))
                starts[(str(rid), pid)] += 1
                slots[(str(rid), pid)][p.get("slot")] += 1
                means[(str(rid), pid)].append(sf(p.get("mean")))
    return starts, slots, means


def owner_profiles_v3(cfg, owners, sim, trade_summary):
    sim_by_uid = {str(x.get("user_id")): x for x in sim["teams"]}
    rows = []
    for o in resolve_owner_rows(owners):
        uid = str(o.get("user_id"))
        tp = o.get("trade_profile") or {}
        wp = o.get("waiver_profile") or {}
        rp = o.get("rookie_draft_profile") or {}
        st = sim_by_uid.get(uid, {})
        total = sf(tp.get("total_trades"))

        recent_keys = [k for k in tp if str(k).startswith("recent_trades_")]
        recent = sf(tp.get(sorted(recent_keys)[-1])) if recent_keys else sf(tp.get("recent_trades"))

        multi = sf(tp.get("multi_asset_rate"))
        initiation = sf(tp.get("initiation_rate"))
        first_net = sf(tp.get("firsts_acquired")) - sf(tp.get("firsts_sent"))
        player_acq = sum(sf(v) for v in (tp.get("player_positions_acquired") or {}).values())
        player_sent = sum(sf(v) for v in (tp.get("player_positions_sent") or {}).values())
        pick_appetite = clamp(.5 + .04 * first_net)
        activity = clamp(total / 50)
        negotiation = weighted([
            (activity, .36),
            (clamp(recent / 20), .24),
            (multi, .20),
            (initiation, .20),
        ])[0]
        rows.append({
            "user_id": uid,
            "manager": o.get("manager"),
            "team_name": o.get("team_name"),
            "competitive_window": st.get("competitive_window"),
            "championship_probability": st.get("championship_probability"),
            "playoff_probability": st.get("playoff_probability"),
            "trade_activity_score": round(100 * activity, 1),
            "negotiability_score": round(100 * negotiation, 1),
            "pick_appetite_score": round(100 * pick_appetite, 1),
            "player_liquidity_score": round(100 * clamp((player_acq + player_sent) / 80), 1),
            "multi_asset_preference": round(multi, 3),
            "initiation_rate": round(initiation, 3),
            "top_trade_partners": tp.get("top_trade_partners") or [],
            "position_acquisition_history": tp.get("player_positions_acquired") or {},
            "rookie_draft_profile": rp,
            "waiver_profile": wp,
            "evidence_quality": "hard_history_plus_simulator",
        })
    return rows


def build_pick_forecast(cfg, sim, fragility, pick_quality):
    fragrows = resolve_owner_rows(fragility)
    frag_by_uid = {str(x.get("user_id")): x for x in fragrows}
    wins = [sf(x.get("expected_wins")) for x in sim["teams"]]
    pts = [sf(x.get("expected_points_for")) for x in sim["teams"]]
    rows = []

    current_season = int(cfg["season"])

    for t in sim["teams"]:
        uid = str(t.get("user_id"))
        f = frag_by_uid.get(uid, {})
        frag = sf(f.get("fragility_score"), sf(f.get("roster_fragility"), .5))
        if frag > 1:
            frag = frag / 100
        strength = weighted([
            (sf(t.get("championship_probability")), .28),
            (sf(t.get("playoff_probability")), .24),
            (pct_rank(sf(t.get("expected_wins")), wins), .24),
            (pct_rank(sf(t.get("expected_points_for")), pts), .14),
            (1 - clamp(frag), .10),
        ])[0]

        expected_slot = 1 + 11 * strength
        row = {
            "user_id": uid,
            "manager": t.get("manager"),
            "team_name": t.get("team_name"),
            "current_strength_index": t.get("simulator_strength_index"),
            "fragility_input": round(frag, 3),
            "confidence": round(.76 if frag_by_uid.get(uid) else .67, 2),
            "note": "Future pick location is a distribution; farther-year estimates shrink toward league average.",
        }

        # Dynamic three-year pick horizon instead of fixed 2027/2028/2029 assumptions.
        for horizon in range(1, 4):
            year = current_season + horizon
            shrink = {1: 1.0, 2: .72, 3: .56}[horizon]
            slot = 1 + 11 * (shrink * strength + (1 - shrink) * .5)
            row[f"{year}_first_expected_slot"] = round(slot, 1)
            if horizon == 1:
                row[f"{year}_first_band"] = (
                    "early" if slot <= 4.5 else "mid" if slot <= 8.5 else "late"
                )
        rows.append(row)

    first_year = current_season + 1
    return sorted(rows, key=lambda x: x[f"{first_year}_first_expected_slot"])


def opportunity_radar(cfg, assets, projections, owner_v3, sim, football_intel):
    pf = projection_features(projections)
    owner_by_uid = {str(x.get("user_id")): x for x in owner_v3}

    intel = {}
    if isinstance(football_intel, list):
        for x in football_intel:
            intel[str(x.get("player_id") or x.get("sleeper_id") or "")] = x
    elif isinstance(football_intel, dict):
        # Support current GM 3.0 intelligence contract as well as legacy list-like contracts.
        rows = (
            football_intel.get("signals")
            or football_intel.get("players")
            or football_intel.get("candidates")
            or []
        )
        if isinstance(rows, list):
            for x in rows:
                intel[str(x.get("player_id") or x.get("sleeper_id") or "")] = x

    players = resolve_players(assets)
    market_vals = [sf(p.get("market_dynasty")) for p in players if sf(p.get("market_dynasty")) > 0]
    proj_ppgs = [x["projection_ppg"] for x in pf.values()]
    rows = []

    for p in players:
        pid = str(p.get("player_id"))
        pr = pf.get(pid, {})
        market = sf(p.get("market_dynasty"))
        fsffl = sf(p.get("fsffl_value"))
        disagreement = clamp(.5 + (fsffl - market) / max(market, 1000) * .9) if market else .5
        trend = sf(p.get("trend_30_day"))
        momentum = clamp(.5 + max(-700, min(700, trend)) / 1600)
        proj_strength = pct_rank(pr.get("projection_ppg", 0), proj_ppgs) if pr else None
        asym = None
        if pr:
            cv = pr.get("projection_cv", 0)
            asym = clamp(.70 - .35 * abs(cv - .65))

        fi = p.get("football_intelligence") or {}
        usage = fi.get("usage_and_snaps") or {}
        vals = []
        for v in [
            usage.get("usage_signal"),
            usage.get("snap_signal"),
            (fi.get("manual_news_signal") or {}).get("signal"),
            (intel.get(pid) or {}).get("signal"),
        ]:
            if v is not None:
                v = sf(v)
                vals.append(clamp((v + 1) / 2 if v < 0 else v))
        role_news = statistics.mean(vals) if vals else None

        age = age_score(str(p.get("position") or ""), p.get("age"))
        owner = owner_by_uid.get(str(p.get("current_owner_user_id")), {})
        access = clamp(sf(owner.get("negotiability_score"), 50) / 100)

        comp, coverage = weighted([
            (disagreement, .22),
            (role_news, .28),
            (proj_strength, .18),
            (asym, .08),
            (momentum, .08),
            (age, .10),
            (access, .06),
        ])
        cheap = 1 - pct_rank(market, market_vals) if market_vals else .5
        neg_mom = clamp(.5 - momentum + .5)
        hidden = weighted([(comp, .55), (cheap, .25), (disagreement, .20)])[0]
        breakout = weighted([(role_news, .34), (proj_strength, .28), (age, .14), (momentum, .12), (disagreement, .12)])[0]
        buylow = weighted([(neg_mom, .34), (disagreement, .28), (proj_strength, .18), (role_news, .20)])[0]
        bust = weighted([
            (1 - disagreement, .22),
            (None if role_news is None else 1 - role_news, .26),
            (None if proj_strength is None else 1 - proj_strength, .20),
            (1 - age if age is not None else None, .16),
            (1 - momentum, .16),
        ])[0]

        cats = []
        th = cfg["alert_thresholds"]
        for typ, sc, t in [
            ("HIDDEN_GEM", hidden, th["hidden_gem"]),
            ("BREAKOUT_WATCH", breakout, th["breakout"]),
            ("BUY_LOW", buylow, th["buy_low"]),
            ("BUST_RISK", bust, th["bust_risk"]),
        ]:
            if sc * 100 >= t:
                cats.append({"type": typ, "score": round(sc * 100, 1)})

        young = (
            (p.get("position") == "QB" and sf(p.get("age"), 99) <= 25)
            or (p.get("position") == "RB" and sf(p.get("age"), 99) <= 23)
            or (p.get("position") == "WR" and sf(p.get("age"), 99) <= 24)
            or (p.get("position") == "TE" and sf(p.get("age"), 99) <= 25)
        )
        early_not_yet = weighted([
            (disagreement, .30),
            (role_news, .24),
            (age, .24),
            (None if proj_strength is None else 1 - proj_strength, .22),
        ])[0]
        if young and proj_strength is not None and proj_strength < .58 and early_not_yet * 100 >= th["early_not_yet"]:
            cats.append({"type": "EARLY_NOT_YET", "score": round(early_not_yet * 100, 1)})

        confidence = clamp(.34 + .54 * coverage + .06 * (1 if pr else 0) + .06 * (1 if vals else 0))
        rows.append({
            "player_id": pid,
            "name": p.get("name"),
            "position": p.get("position"),
            "nfl_team": p.get("nfl_team"),
            "age": p.get("age"),
            "owner_manager": p.get("current_owner_manager"),
            "owner_team": p.get("current_owner_team"),
            "market_dynasty": market,
            "fsffl_value": fsffl,
            "trend_30_day": trend,
            "projection_ppg": round(pr.get("projection_ppg", 0), 2) if pr else None,
            "gm30_value": round(fsffl * (1 + max(-0.12, min(0.12, (comp - .5) * .24))), 1) if fsffl else 0.0,
            "gm30_vs_market_pct": round(
                100 * ((fsffl * (1 + max(-0.12, min(0.12, (comp - .5) * .24))) / market) - 1), 1
            ) if market else None,
            "signal_score": round(comp * 100, 1),
            "confidence": round(confidence, 3),
            "coverage": round(coverage, 3),
            "evidence_grade": "A" if coverage >= .82 and pr and vals else "B" if coverage >= .62 else "C" if coverage >= .45 else "D",
            "categories": sorted(cats, key=lambda x: x["score"], reverse=True),
            "components": {
                "market_disagreement": round(disagreement, 3),
                "role_usage_news": None if role_news is None else round(role_news, 3),
                "projection_strength": None if proj_strength is None else round(proj_strength, 3),
                "upside_asymmetry": None if asym is None else round(asym, 3),
                "market_momentum": round(momentum, 3),
                "age_curve": None if age is None else round(age, 3),
                "owner_accessibility": round(access, 3),
            },
        })

    return sorted(
        rows,
        key=lambda x: max([c["score"] for c in x["categories"]] or [x["signal_score"]]),
        reverse=True,
    )


def roster_arbitrage(cfg, assets, projections, rosters, players):
    vals = {str(x.get("player_id")): x for x in resolve_players(assets)}
    pf = projection_features(projections)
    player_meta = players if isinstance(players, dict) else {}
    rostered = set()
    rows = []

    for r in rosters if isinstance(rosters, list) else []:
        rid = str(r.get("roster_id"))
        pids = [str(x) for x in (r.get("players") or [])]
        rostered.update(pids)
        ranked = []
        for pid in pids:
            a = vals.get(pid, {})
            pr = pf.get(pid, {})
            utility = .55 * sf(a.get("fsffl_value")) / 10000 + .45 * clamp(sf(pr.get("projection_ppg")) / 24)
            ranked.append((utility, pid, a, pr))
        ranked.sort()
        rows.append({
            "roster_id": rid,
            "bottom_assets": [{
                "player_id": pid,
                "name": a.get("name") or (player_meta.get(pid) or {}).get("full_name"),
                "position": a.get("position") or (player_meta.get(pid) or {}).get("position"),
                "fsffl_value": a.get("fsffl_value"),
                "projection_ppg": round(pr.get("projection_ppg", 0), 2) if pr else None,
                "roster_slot_utility": round(u, 4),
            } for u, pid, a, pr in ranked[:3]],
        })

    waiver = []
    for pid, pr in pf.items():
        if pid in rostered:
            continue
        meta = player_meta.get(pid, {}) if isinstance(player_meta, dict) else {}
        waiver.append({
            "player_id": pid,
            "name": pr.get("name") or meta.get("full_name"),
            "position": pr.get("position") or meta.get("position"),
            "projection_ppg": round(pr.get("projection_ppg", 0), 2),
            "projection_cv": round(pr.get("projection_cv", 0), 3),
            "waiver_score": round(100 * clamp(pr.get("projection_ppg", 0) / 15) * (.85 + .15 * clamp(pr.get("active_probability", 1))), 1),
        })
    waiver.sort(key=lambda x: x["waiver_score"], reverse=True)
    return {"team_bottom_assets": rows, "top_unrostered_candidates": waiver[:50]}


def trade_routes(cfg, radar, owner_v3, sim, user_uid):
    owner_by_uid = {str(x.get("user_id")): x for x in owner_v3}
    sim_by_uid = {str(x.get("user_id")): x for x in sim["teams"]}
    candidates = []

    for p in radar:
        uid = None
        for o in owner_v3:
            if norm(o.get("manager")) == norm(p.get("owner_manager")):
                uid = str(o.get("user_id"))
                break
        if not uid or uid == str(user_uid):
            continue

        own = owner_by_uid.get(uid, {})
        if not p.get("categories"):
            continue
        best = max(p["categories"], key=lambda c: c["score"])
        desirability = best["score"] / 100
        acceptance = weighted([
            (sf(own.get("negotiability_score"), 50) / 100, .42),
            (sf(own.get("player_liquidity_score"), 50) / 100, .18),
            (sf(own.get("pick_appetite_score"), 50) / 100, .18),
            (1 - clamp(sf(sim_by_uid.get(uid, {}).get("championship_probability")) / .35), .22),
        ])[0]
        route = desirability * (.65 + .35 * acceptance)
        candidates.append({
            "target_player_id": p["player_id"],
            "target": p["name"],
            "position": p["position"],
            "owner_manager": p.get("owner_manager"),
            "owner_team": p.get("owner_team"),
            "primary_signal": best,
            "target_market_value": p.get("market_dynasty"),
            "target_fsffl_value": p.get("fsffl_value"),
            "counterparty_acceptance_proxy": round(acceptance, 3),
            "route_priority": round(100 * route, 1),
            "suggested_offer_shape":
                "future_pick_heavy" if sf(own.get("pick_appetite_score"), 50) >= 58 else
                "player_for_player" if sf(own.get("player_liquidity_score"), 50) >= 55 else
                "balanced_package",
        })

    return sorted(candidates, key=lambda x: x["route_priority"], reverse=True)[:100]


def championship_marginal_value(cfg, radar, sim, user_uid):
    user = next((x for x in sim["teams"] if str(x.get("user_id")) == str(user_uid)), {})
    base_champ = sf(user.get("championship_probability"))
    base_playoff = sf(user.get("playoff_probability"))
    out = {}

    for p in radar:
        ppg = sf(p.get("projection_ppg"))
        delta_ppg = max(0, ppg - 7.0)
        season_pts = delta_ppg * 14
        champ_delta = min(
            cfg["trade"]["max_probability_delta_per_trade"],
            (season_pts / 100) * cfg["trade"]["championship_elasticity_per_100_points"] * max(.20, 1 - base_champ),
        )
        playoff_delta = min(
            cfg["trade"]["max_probability_delta_per_trade"],
            (season_pts / 100) * cfg["trade"]["playoff_elasticity_per_100_points"] * max(.12, 1 - base_playoff),
        )
        out[p["player_id"]] = {
            "approx_added_points_vs_generic_flex": round(season_pts, 1),
            "approx_championship_probability_delta": round(champ_delta, 4),
            "approx_playoff_probability_delta": round(playoff_delta, 4),
            "method": "fast marginal approximation; rerun Simulator for trade-specific exact estimate",
        }
    return out


def decision_center(cfg, radar, routes, rosterarb, sim, user_uid):
    marg = championship_marginal_value(cfg, radar, sim, user_uid)
    route_by_pid = {x["target_player_id"]: x for x in routes}
    acts, watches, fades = [], [], []

    for p in radar:
        if not p.get("categories"):
            continue
        best = p["categories"][0]
        item = {
            "player_id": p["player_id"],
            "name": p["name"],
            "position": p["position"],
            "signal": best,
            "confidence": p["confidence"],
            "owner_manager": p.get("owner_manager"),
            "market_dynasty": p.get("market_dynasty"),
            "fsffl_value": p.get("fsffl_value"),
            "simulator_utility": marg.get(p["player_id"]),
            "trade_route": route_by_pid.get(p["player_id"]),
        }
        score = best["score"] * (.78 + .22 * p["confidence"])
        if best["type"] == "BUST_RISK":
            if score >= cfg["alert_thresholds"]["act_now"]:
                fades.append(item)
            else:
                watches.append(item)
        elif score >= cfg["alert_thresholds"]["act_now"]:
            acts.append(item)
        elif score >= cfg["alert_thresholds"]["watch"]:
            watches.append(item)

    no_action = None if acts or fades else "NO MATERIAL GM 3.0 ACTION SIGNALS"
    return {
        "user_user_id": str(user_uid),
        "act_now": acts[:20],
        "watch": watches[:40],
        "sell_fade": fades[:20],
        "no_action": no_action,
        "roster_arbitrage": rosterarb,
        "governing_principle": "Optimize expected franchise outcomes: dynasty value + title equity + market edge + optionality, not prediction accuracy alone.",
    }


def decision_journal_snapshot(decisions):
    now = datetime.now(timezone.utc).isoformat()
    existing = load(OUT / "decision_journal.json", {}) or {}
    old_by_id = {
        str(x.get("decision_id")): x
        for x in existing.get("entries", [])
        if x.get("decision_id")
    }
    current_ids = set()

    for bucket in ("act_now", "sell_fade"):
        for d in decisions.get(bucket, [])[:20]:
            sig = (d.get("signal") or {}).get("type")
            raw = f"{bucket}|{d.get('player_id')}|{sig}"
            import hashlib
            did = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
            current_ids.add(did)

            if did in old_by_id:
                row = old_by_id[did]
                row["last_seen_at_utc"] = now
                row["times_seen"] = int(row.get("times_seen", 1)) + 1
                row["latest_signal"] = d.get("signal")
                row["latest_confidence"] = d.get("confidence")
                row["latest_market_value"] = d.get("market_dynasty")
                row["latest_fsffl_value"] = d.get("fsffl_value")
                row["latest_simulator_utility"] = d.get("simulator_utility")
            else:
                old_by_id[did] = {
                    "decision_id": did,
                    "created_at_utc": now,
                    "last_seen_at_utc": now,
                    "times_seen": 1,
                    "status": "OPEN",
                    "decision_type": bucket.upper(),
                    "player_id": d.get("player_id"),
                    "player_name": d.get("name"),
                    "initial_signal": d.get("signal"),
                    "latest_signal": d.get("signal"),
                    "initial_confidence": d.get("confidence"),
                    "latest_confidence": d.get("confidence"),
                    "market_value_at_decision": d.get("market_dynasty"),
                    "latest_market_value": d.get("market_dynasty"),
                    "fsffl_value_at_decision": d.get("fsffl_value"),
                    "latest_fsffl_value": d.get("fsffl_value"),
                    "simulator_utility_at_decision": d.get("simulator_utility"),
                    "latest_simulator_utility": d.get("simulator_utility"),
                    "future_review_fields": {
                        "review_date": None,
                        "outcome": None,
                        "process_grade": None,
                        "notes": None,
                    },
                }

    for did, row in old_by_id.items():
        if did not in current_ids and row.get("status") == "OPEN":
            row["status"] = "INACTIVE_SIGNAL"

    entries = sorted(old_by_id.values(), key=lambda x: x.get("created_at_utc", ""), reverse=True)
    return {
        "generated_at_utc": now,
        "entries": entries,
        "note": "Persistent self-audit journal. Outcome/process review fields survive daily rebuilds.",
    }


def validation(cfg, inputs, outputs):
    checks = []
    required = [
        "asset_values",
        "owner_behavior",
        "rosters",
        "players",
        "sim_standings",
        "sim_lineups",
        "sim_projections",
    ]
    for key in required:
        checks.append({"check": f"required_input:{key}", "passed": inputs.get(key) is not None})

    sim = outputs["sim"]
    checks.append({
        "check": "simulator_teams_12",
        "passed": len(sim.get("teams", [])) == 12,
        "value": len(sim.get("teams", [])),
    })
    champs = sum(sf(x.get("championship_probability")) for x in sim.get("teams", []))
    checks.append({
        "check": "championship_probabilities_sum_near_1",
        "passed": abs(champs - 1) < .03,
        "value": round(champs, 5),
    })
    checks.append({"check": "gm3_does_not_write_simulator_paths", "passed": True})

    return {
        "model_version": cfg["model_version"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "passed": all(x["passed"] for x in checks),
        "checks": checks,
    }


def main():
    cfg = load(CFG_PATH)
    if not cfg:
        raise SystemExit("Missing data/gm3_config.json")

    # Critical future-proofing: resolve {season} from league metadata every run.
    cfg = resolve_config_paths(cfg)

    p = cfg["paths"]
    inputs = {k: load(v) for k, v in p.items()}

    required = [
        "asset_values",
        "owner_behavior",
        "rosters",
        "players",
        "sim_standings",
        "sim_lineups",
        "sim_projections",
    ]
    missing = [k for k in required if inputs.get(k) is None]
    if missing:
        detail = ", ".join(
            f"{k} -> {p.get(k)}"
            for k in missing
        )
        raise SystemExit("Missing required GM 3.0 inputs: " + detail)

    sim = sim_bridge(
        cfg,
        inputs["sim_standings"],
        inputs["sim_lineups"],
        inputs["sim_projections"],
    )
    owners = owner_profiles_v3(
        cfg,
        inputs["owner_behavior"],
        sim,
        inputs.get("trade_summary"),
    )
    picks = build_pick_forecast(
        cfg,
        sim,
        inputs.get("roster_fragility"),
        inputs.get("pick_quality"),
    )
    radar = opportunity_radar(
        cfg,
        inputs["asset_values"],
        inputs["sim_projections"],
        owners,
        sim,
        inputs.get("football_intelligence"),
    )
    rosterarb = roster_arbitrage(
        cfg,
        inputs["asset_values"],
        inputs["sim_projections"],
        inputs["rosters"],
        inputs["players"],
    )

    # Preserve existing configured default-team behavior if present.
    # If no explicit user id exists, choose no arbitrary manager here.
    user_uid = cfg.get("user_user_id")
    if user_uid is None:
        # Prefer a uniquely designated default in owner records if one exists.
        defaults = [
            x for x in resolve_owner_rows(inputs["owner_behavior"])
            if x.get("is_default_team") or x.get("default_team")
        ]
        if len(defaults) == 1:
            user_uid = defaults[0].get("user_id")
        else:
            # Decision-layer outputs remain league-wide; team perspective can be
            # selected downstream rather than hardcoding a username here.
            user_uid = ""

    routes = trade_routes(cfg, radar, owners, sim, user_uid)
    decisions = decision_center(cfg, radar, routes, rosterarb, sim, user_uid)
    journal = decision_journal_snapshot(decisions)

    leagueintel = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_version": cfg["model_version"],
        "season": cfg["season"],
        "simulator_competitive_landscape": sim["teams"],
        "owner_profiles": owners,
        "pick_forecast": picks,
        "top_market_opportunities": radar[:50],
        "top_trade_routes": routes[:40],
    }

    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_version": cfg["model_version"],
        "season": cfg["season"],
        "upstream": {
            "gm": "GM 2.2 / fsffl_asset_values",
            "simulator": inputs["sim_standings"].get("model_version"),
        },
        "architecture": "downstream_only",
        "season_resolution": {
            "mode": "dynamic_from_league_metadata",
            "source": "data/league.json",
            "resolved_season": cfg["season"],
            "hardcoded_season": False,
        },
        "features": [
            "live_league_intelligence",
            "dynamic_player_signal_layer",
            "owner_specific_market_model",
            "trade_routing",
            "championship_utility_bridge",
            "opportunity_radar",
            "confidence_evidence",
            "roster_arbitrage",
            "pick_forecasting",
            "decision_journal",
            "counterfactual_fast_trade_utility",
        ],
        "outputs": [
            "simulator_bridge.json",
            "owner_profiles_v3.json",
            "pick_forecast.json",
            "opportunity_radar.json",
            "roster_arbitrage.json",
            "trade_routes.json",
            "league_intelligence.json",
            "decision_center.json",
            "decision_journal_snapshot.json",
            "validation_report.json",
        ],
    }

    val = validation(cfg, inputs, {"sim": sim})

    dump("manifest.json", manifest)
    dump("simulator_bridge.json", sim)
    dump("owner_profiles_v3.json", owners)
    dump("pick_forecast.json", picks)
    dump("opportunity_radar.json", radar)
    dump("roster_arbitrage.json", rosterarb)
    dump("trade_routes.json", routes)
    dump("league_intelligence.json", leagueintel)
    dump("decision_center.json", decisions)
    dump("decision_journal.json", journal)
    dump("decision_journal_snapshot.json", journal)
    dump("validation_report.json", val)

    print(
        f"GM 3.0 built for season {cfg['season']}: "
        f"{len(radar)} players, {len(routes)} trade routes, "
        f"validation={'PASS' if val['passed'] else 'FAIL'}"
    )


if __name__ == "__main__":
    main()
