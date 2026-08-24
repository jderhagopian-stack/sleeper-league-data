#!/usr/bin/env python3
"""Historical-safe rookie draft policy for state-aware alternate-history drafts.

Generalizes the validated 0.6b reference model so roster need and pick control
come from each alternate particle state at draft time. It uses only information
available by that draft:
- actual same-draft order as contemporaneous market evidence;
- that manager's actual selections in the round as revealed preference;
- manager positional tendencies from drafts strictly before the target draft;
- branch-specific roster composition at draft time as the need proxy.

No future NFL outcomes or current GM 3.0 values are used.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any, Dict, Iterable, List, Set, Tuple


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
    local = [
        player for player in available_players
        if abs(int(player.get("pick_no") or 0) - int(current_pick_no)) <= int(market_radius)
    ]
    for player in available_players:
        if str(player.get("player_id")) in revealed_player_ids and player not in local:
            local.append(player)
    if not local:
        local = sorted(available_players, key=lambda p: int(p.get("pick_no") or 9999))[:5]

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
