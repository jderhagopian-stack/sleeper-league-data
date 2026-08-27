#!/usr/bin/env python3
"""FSFFL roster-interaction and correlated-asset intelligence.

Generic, symmetric portfolio layer used by trade analysis. It evaluates:
- same-NFL-team same-position insurance/coverage value;
- current injury/availability uncertainty;
- depth-chart competition context for diagnostics.

It deliberately does NOT hard-code player names or team-specific exceptions.
The module is bounded so roster interactions cannot overwhelm market value or
lineup simulation. Market value remains league-wide; this layer is explicitly
roster-specific marginal value.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

DATA = Path("data")
MODEL_VERSION = "FSFFL-Roster-Interaction-1.0"
MAX_PAIR_INSURANCE_PCT = 0.12
PAIR_CAPTURE_SCALE = 0.30
MAX_PORTFOLIO_ADJUSTMENT = 600.0
MAX_ACCEPTANCE_FIT_SHIFT = 0.04


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def sf(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def player_map():
    raw = load_json(DATA / "players.json", {}) or {}
    if isinstance(raw, list):
        raw = {str(x.get("player_id")): x for x in raw if isinstance(x, dict) and x.get("player_id") is not None}
    return {str(k): v for k, v in raw.items() if isinstance(v, dict)}


def asset_player_map():
    raw = load_json(DATA / "fsffl_asset_values.json", {}) or {}
    return {str(x.get("player_id")): x for x in (raw.get("players") or []) if x.get("player_id") is not None}


def football_intelligence():
    return load_json(DATA / "football_intelligence_signals.json", {}) or {}


def franchise_index():
    raw = load_json(DATA / "gm" / "franchise_index.json", {}) or {}
    return {str(x.get("user_id")): x for x in (raw.get("teams") or [])}


def gm_assets(uid: str):
    row = franchise_index().get(str(uid)) or {}
    p = ((row.get("paths") or {}).get("strategic_asset_profiles"))
    raw = load_json(Path(p), {}) if p else {}
    return {str(x.get("asset_id")): x for x in (raw.get("assets") or [])}


def roster_maps(rosters):
    return {str(x.get("owner_id")): x for x in rosters}


def normalize_roster(r):
    r = copy.deepcopy(r)
    for k in ("players", "taxi", "reserve"):
        r[k] = [str(x) for x in (r.get(k) or [])]
    return r


def active_ids(roster):
    r = normalize_roster(roster)
    exempt = set(r.get("taxi") or []) | set(r.get("reserve") or [])
    return [str(x) for x in (r.get("players") or []) if str(x) not in exempt]


def apply_actions(rosters, actions):
    out = copy.deepcopy(rosters)
    by_uid = roster_maps(out)
    for uid, r in list(by_uid.items()):
        by_uid[uid] = normalize_roster(r)
    for a in actions or []:
        typ = str(a.get("type") or "").lower().strip()
        if typ == "trade":
            src, dst = str(a.get("from_user_id")), str(a.get("to_user_id"))
            if src not in by_uid or dst not in by_uid:
                continue
            for pid in [str(x) for x in (a.get("players") or [])]:
                for k in ("players", "taxi", "reserve"):
                    by_uid[src][k] = [x for x in by_uid[src].get(k, []) if str(x) != pid]
                if pid not in by_uid[dst]["players"]:
                    by_uid[dst]["players"].append(pid)
        elif typ in {"cut", "drop"}:
            uid = str(a.get("user_id"))
            if uid not in by_uid:
                continue
            for pid in [str(x) for x in (a.get("players") or [])]:
                for k in ("players", "taxi", "reserve"):
                    by_uid[uid][k] = [x for x in by_uid[uid].get(k, []) if str(x) != pid]
        elif typ == "add":
            uid = str(a.get("user_id"))
            if uid not in by_uid:
                continue
            for pid in [str(x) for x in (a.get("players") or [])]:
                if pid not in by_uid[uid]["players"]:
                    by_uid[uid]["players"].append(pid)
    return list(by_uid.values())


def player_context(uid: str, pid: str, pmap=None, amap=None, fint=None):
    pmap = pmap or player_map()
    amap = amap or asset_player_map()
    fint = fint or football_intelligence()
    p = pmap.get(str(pid)) or {}
    a = amap.get(str(pid)) or {}
    gm = gm_assets(str(uid)).get(f"player:{pid}") or {}

    prior = (fint.get("prior_snaps") or {}).get(str(pid)) or {}
    current = (fint.get("snaps") or {}).get(str(pid)) or {}
    pre = (fint.get("preseason_usage") or {}).get(str(pid)) or {}

    market_redraft = sf(gm.get("market_redraft"), sf(a.get("market_redraft")))
    market_dynasty = sf(gm.get("market_dynasty"), sf(a.get("market_dynasty")))
    downside = clamp(sf((gm.get("future_distribution") or {}).get("downside_risk"), 0.25), 0, 1)

    injury_status = str(p.get("injury_status") or a.get("injury_status") or "").lower()
    injury_now = 0.45 if injury_status in {"questionable", "doubtful", "out", "ir"} else 0.18 if injury_status else 0.0

    prior_games = sf(prior.get("games"))
    availability_uncertainty = 0.0
    if prior_games > 0:
        availability_uncertainty = clamp(1.0 - prior_games / 17.0, 0, 0.75)

    role_conf = 0.0
    if current:
        role_conf = clamp(sf(current.get("offense_snap_pct")), 0, 1)
    elif pre:
        role_conf = clamp(max(sf(pre.get("signal_strength")), sf(pre.get("latest_game_role_share"))), 0, 1)
    elif prior:
        role_conf = clamp(sf(prior.get("offense_snap_pct")), 0, 1)

    role_uncertainty = 1.0 - role_conf if role_conf > 0 else 0.35
    uncertainty = clamp(
        0.45 * downside +
        0.30 * max(injury_now, availability_uncertainty) +
        0.25 * role_uncertainty,
        0.05, 0.85
    )

    return {
        "player_id": str(pid),
        "name": p.get("full_name") or a.get("name") or gm.get("name") or f"player:{pid}",
        "team": p.get("team") or a.get("nfl_team"),
        "position": p.get("position") or a.get("position"),
        "market_redraft": round(market_redraft, 2),
        "market_dynasty": round(market_dynasty, 2),
        "downside_risk": round(downside, 4),
        "current_injury_status": p.get("injury_status") or a.get("injury_status"),
        "prior_games": int(prior_games) if prior_games else 0,
        "role_confidence_proxy": round(role_conf, 4),
        "uncertainty": round(uncertainty, 4),
    }


def credible_competitors(pid: str, pmap=None, amap=None, fint=None):
    pmap = pmap or player_map()
    amap = amap or asset_player_map()
    fint = fint or football_intelligence()
    p = pmap.get(str(pid)) or {}
    team, pos = p.get("team"), p.get("position")
    if not team or not pos:
        return []
    prior_all = fint.get("prior_snaps") or {}
    rows = []
    for qid, q in pmap.items():
        if str(qid) == str(pid) or not q.get("active"):
            continue
        if q.get("team") != team or q.get("position") != pos:
            continue
        a = amap.get(str(qid)) or {}
        prior = prior_all.get(str(qid)) or {}
        years = sf(q.get("years_exp"))
        snap = sf(prior.get("offense_snap_pct"))
        redraft = sf(a.get("market_redraft"))
        credible = redraft >= 200 or snap >= 0.12 or years >= 2
        if not credible:
            continue
        strength = clamp(max(redraft / 3500.0, snap, 0.22 if years >= 4 else 0.10 if years >= 2 else 0.0), 0, 1)
        rows.append({
            "player_id": str(qid),
            "name": q.get("full_name") or a.get("name") or f"player:{qid}",
            "market_redraft": round(redraft, 2),
            "prior_snap_pct": round(snap, 4),
            "years_exp": int(years) if years else 0,
            "competition_strength": round(strength, 4),
        })
    rows.sort(key=lambda x: x["competition_strength"], reverse=True)
    return rows[:5]


def pair_insurance(primary, secondary):
    if not primary.get("team") or primary.get("team") != secondary.get("team"):
        return 0.0
    if primary.get("position") != secondary.get("position"):
        return 0.0
    pval = max(sf(primary.get("market_redraft")), 1.0)
    sval = max(sf(secondary.get("market_redraft")), 0.0)
    if sval <= 0:
        return 0.0
    secondary_quality = clamp(sval / pval, 0, 1)
    uncertainty = clamp(sf(primary.get("uncertainty")), 0, 1)
    raw = pval * uncertainty * secondary_quality * PAIR_CAPTURE_SCALE
    return round(min(pval * MAX_PAIR_INSURANCE_PCT, raw), 2)


def portfolio(uid: str, roster: Dict[str, Any], pmap=None, amap=None, fint=None):
    pmap = pmap or player_map()
    amap = amap or asset_player_map()
    fint = fint or football_intelligence()
    contexts = [player_context(uid, pid, pmap, amap, fint) for pid in active_ids(roster)]
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for c in contexts:
        if c.get("team") and c.get("position") in {"QB", "RB", "WR", "TE"}:
            groups.setdefault((str(c["team"]), str(c["position"])), []).append(c)

    pair_rows = []
    total = 0.0
    for (team, pos), rows in groups.items():
        if len(rows) < 2:
            continue
        rows = sorted(rows, key=lambda x: sf(x.get("market_redraft")), reverse=True)[:3]
        primary = rows[0]
        for secondary in rows[1:]:
            value = pair_insurance(primary, secondary)
            if value <= 0:
                continue
            total += value
            pair_rows.append({
                "nfl_team": team,
                "position": pos,
                "primary": primary["name"],
                "secondary": secondary["name"],
                "insurance_value": value,
                "primary_uncertainty": primary["uncertainty"],
                "secondary_quality_ratio": round(clamp(sf(secondary.get("market_redraft")) / max(sf(primary.get("market_redraft")), 1), 0, 1), 4),
            })

    total = round(clamp(total, -MAX_PORTFOLIO_ADJUSTMENT, MAX_PORTFOLIO_ADJUSTMENT), 2)
    return {
        "user_id": str(uid),
        "portfolio_interaction_value": total,
        "same_team_position_pairs": pair_rows,
    }


def trade_adjustments(focus_uid: str, buyer_uid: str, actions: List[Dict[str, Any]]):
    rosters = load_json(DATA / "rosters.json", []) or []
    pmap, amap, fint = player_map(), asset_player_map(), football_intelligence()
    before = roster_maps(rosters)
    after_rosters = apply_actions(rosters, actions)
    after = roster_maps(after_rosters)

    out = {}
    for uid in [str(focus_uid), str(buyer_uid)]:
        b = portfolio(uid, before.get(uid) or {}, pmap, amap, fint)
        a = portfolio(uid, after.get(uid) or {}, pmap, amap, fint)
        delta = round(clamp(sf(a.get("portfolio_interaction_value")) - sf(b.get("portfolio_interaction_value")), -MAX_PORTFOLIO_ADJUSTMENT, MAX_PORTFOLIO_ADJUSTMENT), 2)
        out[uid] = {
            "before": b,
            "after": a,
            "roster_interaction_value_delta": delta,
            "acceptance_fit_shift": round(clamp(math.tanh(delta / 1200.0) * MAX_ACCEPTANCE_FIT_SHIFT, -MAX_ACCEPTANCE_FIT_SHIFT, MAX_ACCEPTANCE_FIT_SHIFT), 4),
        }
    return {
        "model_version": MODEL_VERSION,
        "focus_user_id": str(focus_uid),
        "buyer_user_id": str(buyer_uid),
        "teams": out,
        "policy": {
            "generic_symmetric_rules": True,
            "player_specific_exceptions": False,
            "market_value_remains_league_wide": True,
            "interaction_value_is_roster_specific": True,
            "pair_insurance_bounded": True,
            "acceptance_shift_bounded": True,
            "competition_context_not_double_counted_into_value": True,
        },
    }


def contextual_snapshot(uid: str, player_ids: Iterable[str]):
    pmap, amap, fint = player_map(), asset_player_map(), football_intelligence()
    out = []
    for pid in player_ids:
        c = player_context(uid, str(pid), pmap, amap, fint)
        c["credible_competitors"] = credible_competitors(str(pid), pmap, amap, fint)
        out.append(c)
    return out
