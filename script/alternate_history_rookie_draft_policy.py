#!/usr/bin/env python3
"""Historical-safe rookie draft policy for state-aware alternate-history drafts.

Generalizes the validated reference model so roster need and pick control come
from each alternate particle state at draft time. It uses only information
available by that draft:
- actual same-draft order as contemporaneous market evidence;
- that manager's actual selections as revealed preference;
- manager positional tendencies from drafts strictly before the target draft;
- branch-specific roster composition at draft time as the need proxy.

Important V2 rule: every pick is scored against the complete remaining rookie
pool from that historical draft. Historical round is never an eligibility wall.
The actual same-draft pick number remains the market anchor: reaching well ahead
of market is strongly penalized, while a player who has already slid past his
historical market slot remains live and becomes progressively better value. This
prevents elite rookies from disappearing outside a local window and resurfacing
absurdly late. Selected rookies remain unavailable later in the same particle,
preserving a coherent sequential draft.

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
    current = int(current_pick_no)

    # Same-draft market evidence is intentionally asymmetric. Reaching several
    # picks ahead of the historical market should be uncommon. A player who has
    # already slid, however, should not become *less* selectable the farther he
    # falls; he becomes value. The capped overdue bonus keeps the policy stable
    # without using any future performance information.
    if actual_pick_no > current:
        market = -0.70 * float(actual_pick_no - current)
    else:
        overdue = max(0, current - actual_pick_no)
        market = 0.28 * float(min(overdue, 5))

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
    """Return all still-undrafted players from the same historical draft."""
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

    # Every still-undrafted rookie from this same historical draft is scored on
    # every pick. `market_radius` remains in the signature for compatibility
    # with older callers, but market plausibility is now expressed continuously
    # in candidate_logit rather than by dropping players from consideration.
    market_players = expanded_available_market(available_players, state=state)
    if not market_players:
        return []

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
        for player in market_players
    ]
    return normalize_logits(scored)
