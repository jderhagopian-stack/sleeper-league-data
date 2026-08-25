#!/usr/bin/env python3
"""Roster-aware post-trade legalization for FSFFL decision support.

Trade simulations must represent a legal roster, not an impossible temporary
roster. This module resolves active-roster overflow after a hypothetical move
by selecting the least-damaging cuts using GM 3.0 state-aware asset profiles.
Taxi and reserve players are excluded from the active-roster count because they
occupy their own Sleeper slots. The canonical roster is never mutated.

Callers may protect specific newly acquired players from automatic cut
selection. This is useful for waiver/add evaluation: the question is whether
adding the candidate improves the roster after the best incumbent is dropped,
not whether the optimizer can immediately undo the hypothetical add.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Iterable, List, Tuple

MODEL_VERSION = "FSFFL-Roster-Aware-Trade-Resolution-1.1"


def sf(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def active_roster_limit(league: Dict[str, Any]) -> int:
    """Sleeper roster_positions contains starters + BN, excluding taxi/reserve."""
    return len(league.get("roster_positions") or [])


def normalize(roster: Dict[str, Any]) -> None:
    for key in ("players", "taxi", "reserve"):
        roster[key] = [str(x) for x in (roster.get(key) or [])]


def active_player_ids(roster: Dict[str, Any]) -> List[str]:
    normalize(roster)
    exempt = set(roster.get("taxi") or []) | set(roster.get("reserve") or [])
    return [str(pid) for pid in (roster.get("players") or []) if str(pid) not in exempt]


def player_name(players: Dict[str, Any], pid: str) -> str:
    p = (players or {}).get(str(pid)) or {}
    return str(p.get("full_name") or p.get("name") or f"player:{pid}")


def cut_profile(dl, uid: str, pid: str, players: Dict[str, Any]) -> Dict[str, Any]:
    """Return a state-aware retention cost. Lower cost means safer to cut."""
    gm = dl.gm_asset_map(str(uid)) if hasattr(dl, "gm_asset_map") else {}
    g = gm.get(f"player:{pid}") or gm.get(str(pid)) or {}
    market_dynasty = sf(g.get("market_dynasty"))
    market_redraft = sf(g.get("market_redraft"))
    base = sf(g.get("base_franchise_value"), market_dynasty + .25 * market_redraft)
    break_glass = sf(g.get("break_glass_value"), base)
    depth = sf(g.get("depth_insurance_drop"))
    liquidity = sf(g.get("liquidity_score"))
    starter = bool(g.get("is_current_optimal_starter"))
    core = str(g.get("core_status") or "")

    cost = base + .12 * break_glass + .06 * depth + .04 * market_dynasty * liquidity
    if starter:
        cost *= 1.75
    cost *= {
        "franchise_cornerstone": 2.00,
        "core_high_hold": 1.70,
        "core_pick": 1.35,
        "liquid_asset": 1.12,
    }.get(core, 1.0)
    return {
        "player_id": str(pid),
        "asset_id": f"player:{pid}",
        "name": g.get("name") or player_name(players, str(pid)),
        "position": g.get("position") or ((players or {}).get(str(pid)) or {}).get("position"),
        "retention_cost": round(cost, 2),
        "base_franchise_value": round(base, 2),
        "market_dynasty": round(market_dynasty, 2),
        "market_redraft": round(market_redraft, 2),
        "break_glass_value": round(break_glass, 2),
        "is_current_optimal_starter": starter,
        "core_status": core or None,
    }


def legalize_trade_rosters(dl, canonical_rosters: List[Dict[str, Any]], hypothetical_rosters: List[Dict[str, Any]],
                            touched_uids: Iterable[str], league: Dict[str, Any], players: Dict[str, Any],
                            protected_player_ids_by_uid: Dict[str, Iterable[str]] | None = None
                            ) -> Tuple[List[Dict[str, Any]], Dict[str, Any], List[Dict[str, Any]]]:
    """Return legal hypothetical rosters, resolution metadata, and auto-cut actions.

    ``protected_player_ids_by_uid`` prevents specified players from being chosen
    as an automatic cut when enough unprotected incumbents are available.
    """
    out = copy.deepcopy(hypothetical_rosters)
    before_by_uid, _ = dl.roster_maps(copy.deepcopy(canonical_rosters))
    after_by_uid, _ = dl.roster_maps(out)
    limit = active_roster_limit(league)
    protected_map = {
        str(uid): {str(x) for x in (ids or [])}
        for uid, ids in (protected_player_ids_by_uid or {}).items()
    }
    resolutions: Dict[str, Any] = {}
    cut_actions: List[Dict[str, Any]] = []

    for uid in sorted({str(x) for x in touched_uids}):
        before = before_by_uid.get(uid)
        roster = after_by_uid.get(uid)
        if not roster:
            continue
        active_before = len(active_player_ids(before)) if before else 0
        active_pre_cut_ids = active_player_ids(roster)
        overflow = max(0, len(active_pre_cut_ids) - limit)
        protected = protected_map.get(uid, set())
        selected = []
        if overflow:
            eligible_ids = [pid for pid in active_pre_cut_ids if pid not in protected]
            if len(eligible_ids) < overflow:
                eligible_ids += [pid for pid in active_pre_cut_ids if pid in protected]
            profiles = [cut_profile(dl, uid, pid, players) for pid in eligible_ids]
            profiles.sort(key=lambda x: (sf(x.get("retention_cost")), sf(x.get("base_franchise_value")), str(x.get("player_id"))))
            selected = profiles[:overflow]
            for row in selected:
                dl.remove_player(roster, str(row["player_id"]))
            cut_actions.append({
                "type": "cut", "user_id": uid,
                "players": [str(x["player_id"]) for x in selected],
                "automatic_roster_legalization": True,
            })

        active_after = len(active_player_ids(roster))
        resolutions[uid] = {
            "model_version": MODEL_VERSION,
            "active_roster_limit": limit,
            "active_players_before_trade": active_before,
            "active_players_after_trade_before_cuts": len(active_pre_cut_ids),
            "required_cuts": overflow,
            "selected_cuts": selected,
            "protected_from_automatic_cut": sorted(protected),
            "cut_base_franchise_value": round(sum(sf(x.get("base_franchise_value")) for x in selected), 2),
            "cut_market_dynasty_value": round(sum(sf(x.get("market_dynasty")) for x in selected), 2),
            "legal_active_players_after_resolution": active_after,
            "roster_legal": active_after <= limit,
            "taxi_and_reserve_excluded_from_active_count": True,
            "automatic_taxi_or_reserve_reassignment": False,
        }
        if active_after > limit:
            raise RuntimeError(f"Roster legalization failed for {uid}: {active_after}>{limit}")

    return out, resolutions, cut_actions
