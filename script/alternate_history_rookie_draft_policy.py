#!/usr/bin/env python3
"""Historical-safe rookie draft policy for state-aware alternate-history drafts.

Generalizes the validated reference model so roster need and pick control come
from each alternate particle state at draft time. It uses only information
available by that draft:
- actual same-draft order as contemporaneous market evidence;
- that manager's actual selections as revealed preference;
- manager positional tendencies from drafts strictly before the target draft;
- branch-specific roster composition at draft time as the need proxy.

Important V2 rule: historical *round* is not a hard eligibility boundary. The
actual same-draft pick number is a market anchor, so a player near a round
boundary may rise or fall across that boundary while still remaining inside a
small contemporaneous market window. Selected rookies remain unavailable later
in the same particle, preserving a coherent sequential draft.

No future NFL outcomes or current GM 3.0 values are used.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any, Dict, Iterable, List, Set, Tuple

_MARKET_POOL_CACHE: Dict[str, List[Dict[str, Any]]] = {}


def branch_roster_counts(
    state: Dict[str, Any],
    positions: Dict[str, str],
) -> Dict[str, Counter]:
    out: Dict[str, Counter] = {}
    for rid, players in (state.get("roster_players") or {}).items():
        counter = Counter()
        for pid in players or []:
            pos = positions.get(str(pid), "")
            if pos:
                counter[pos] += 1
        out[str(rid)] = counter
    return out


def branch_position_medians(counts: Dict[str, Counter]) -> Dict[str, float]:
    positions = {p for c in counts.values() for p in c}
    out: Dict[str, float] = {}
    for pos in positions:
        vals = sorted(float(c.get(pos, 0)) for c in counts.values())
        if not vals:
            continue
        n = len(vals)
        out[pos] = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2.0
    return out


def candidate_logit(
    player: Dict[str, Any],
    *,
    current_pick_no: int,
    controller_roster_id: str,
    controller_user_id: str,
    revealed_player_ids: Set[str],
    tendencies: Dict[str, Counter],
    roster_counts: Dict[str, Counter],
    medians: Dict[str, float],
) -> float:
    pos = str(player.get("position") or "")
    actual_pick_no = int(player.get("pick_no") or 0)
    market = -0.42 * abs(actual_pick_no - int(current_pick_no))
    revealed = 1.55 if str(player.get("player_id")) in revealed_player_ids else 0.0

    hist = tendencies.get(str(controller_user_id)) or Counter()
    total = float(hist.get("__TOTAL__", 0))
    tendency_share = float(hist.get(pos, 0)) / total if total > 0 and pos else 0.0
    tendency = 0.65 * tendency_share

    owner_counts = roster_counts.get(str(controller_roster_id)) or Counter()
    median = float(medians.get(pos, 0.0))
    deficit = max(-2.0, min(3.0, median - float(owner_counts.get(pos, 0)))) if pos else 0.0
    need = 0.28 * deficit
    return market + revealed + tendency + need


def normalize_logits(scored: Iterable[Tuple[float, Dict[str, Any]]]) -> List[Dict[str, Any]]:
    rows = list(scored)
    if not rows:
        return []
    mx = max(score for score, _ in rows)
    exps = [math.exp(max(-20.0, min(20.0, score - mx))) for score, _ in rows]
    denom = sum(exps) or 1.0
    out = []
    for weight, (score, player) in zip(exps, rows):
        out.append({
            "player": player,
            "logit": round(float(score), 6),
            "probability": float(weight / denom),
        })
    out.sort(key=lambda row: (-float(row["probability"]), str((row["player"] or {}).get("player_id") or "")))
    return out


def _selected_ids(state: Dict[str, Any]) -> Set[str]:
    node = state.get("_alternate_history_rookie_draft") or {}
    return {str(x) for x in (node.get("selected_player_ids") or []) if str(x)}


def _draft_season_from_state(state: Dict[str, Any]) -> str:
    node = state.get("_alternate_history_rookie_draft") or {}
    seasons = [str(p.get("draft_season") or "") for p in (node.get("picks") or []) if p.get("draft_season")]
    return seasons[-1] if seasons else ""


def _market_pool_for_season(season: str) -> List[Dict[str, Any]]:
    if season in _MARKET_POOL_CACHE:
        return _MARKET_POOL_CACHE[season]
    # Local imports avoid coupling module initialization order. These helpers
    # read only that season's archived/live Sleeper rookie draft.
    from run_fsffl_alternate_draft_candidates import raw_draft
    from run_fsffl_alternate_draft_policy import normalized_picks

    rows = normalized_picks(raw_draft(str(season)))
    _MARKET_POOL_CACHE[str(season)] = rows
    return rows


def _infer_draft_season(
    available_players: List[Dict[str, Any]],
    state: Dict[str, Any],
) -> str:
    season = _draft_season_from_state(state)
    if season:
        return season
    probes = {
        (str(p.get("player_id") or ""), int(p.get("pick_no") or 0))
        for p in available_players[:4]
        if str(p.get("player_id") or "")
    }
    if not probes:
        return ""
    # The FSFFL history begins well before the scenario fork; probing a compact
    # calendar range is deterministic and uses only same-draft source data.
    for year in range(2020, 2031):
        try:
            rows = _market_pool_for_season(str(year))
        except Exception:
            continue
        keys = {(str(p.get("player_id") or ""), int(p.get("pick_no") or 0)) for p in rows}
        if probes & keys:
            return str(year)
    return ""


def expanded_available_market(
    available_players: List[Dict[str, Any]],
    *,
    state: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Return all still-undrafted players from the same historical draft.

    Callers historically passed only the player's actual round. That made the
    actual round an accidental hard wall. We recover the complete same-draft
    market here, then let `market_radius` impose the intended local uncertainty
    window around the current pick. This permits, for example, an actual 2.01
    player to be considered at 1.12 without allowing a hindsight-driven leap
    from the bottom of the draft to the top.
    """
    season = _infer_draft_season(available_players, state)
    if not season:
        return list(available_players)
    selected = _selected_ids(state)
    return [
        p for p in _market_pool_for_season(season)
        if str(p.get("player_id") or "") not in selected
    ]


def candidate_distribution(
    available_players: List[Dict[str, Any]],
    *,
    current_pick_no: int,
    controller_roster_id: str,
    controller_user_id: str,
    revealed_player_ids: Set[str],
    tendencies: Dict[str, Counter],
    state: Dict[str, Any],
    positions: Dict[str, str],
    market_radius: int = 4,
) -> List[Dict[str, Any]]:
    if not available_players:
        return []

    # V2: expand from the legacy actual-round pool to the complete same-draft
    # market. The radius below, not historical round labels, controls who is a
    # plausible contemporaneous candidate.
    market_players = expanded_available_market(available_players, state=state)
    local = [
        player for player in market_players
        if abs(int(player.get("pick_no") or 0) - int(current_pick_no)) <= int(market_radius)
    ]
    for player in market_players:
        if str(player.get("player_id")) in revealed_player_ids and player not in local:
            local.append(player)
    if not local:
        local = sorted(market_players, key=lambda p: abs(int(p.get("pick_no") or 9999) - int(current_pick_no)))[:5]

    counts = branch_roster_counts(state, positions)
    medians = branch_position_medians(counts)
    scored = [
        (
            candidate_logit(
                player,
                current_pick_no=current_pick_no,
                controller_roster_id=controller_roster_id,
                controller_user_id=controller_user_id,
                revealed_player_ids=revealed_player_ids,
                tendencies=tendencies,
                roster_counts=counts,
                medians=medians,
            ),
            player,
        )
        for player in local
    ]
    return normalize_logits(scored)
