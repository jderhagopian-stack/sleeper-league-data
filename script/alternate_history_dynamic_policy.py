#!/usr/bin/env python3
"""Branch-state-aware historical policy for multi-season alternate history.

After an alternate rookie draft, downstream relevance can no longer be inferred
only from the original fork. This module compares each branch against the actual
historical pre-event state and decides whether a transaction is:
- invariant/preserve lean;
- a usage decision needing re-evaluation;
- a strategic trade needing re-evaluation; or
- mechanically impossible in that branch.

All decision signals are timestamp-safe. No current GM 3.0 values, current
market ranks, or future NFL outcomes are used.
"""

from __future__ import annotations

import copy
import itertools
import math
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import alternate_history_engine as ah
from alternate_history_historical_state import reconstruct_completed_season_state
import run_fsffl_multiseason_branch_replay as branch_v1
from run_fsffl_alternate_history import FSFFLHistoricalAdapter
from run_fsffl_downstream_dependencies import event_legality
from run_fsffl_historical_trade_policy import normalized_probs
from run_fsffl_historical_usage_policy import HistoricalPoints, softmax

MAX_TRADE_CANDIDATES = 3
MAX_TRADE_PACKAGES = 6


def payload_owner_index(state: Dict[str, Any]) -> Dict[str, str]:
    """Build one player->roster index for a branch-state classification pass."""
    return {
        str(pid): str(rid)
        for rid, players in (state.get("roster_players") or {}).items()
        for pid in (players or [])
    }


def actual_owner_index(state: ah.LeagueState) -> Dict[str, str]:
    """Build one player->roster index for the timestamp-safe actual state."""
    return {
        str(pid): str(rid)
        for rid, players in state.roster_players.items()
        for pid in players
    }


def owner_from_payload(state: Dict[str, Any], pid: str) -> Optional[str]:
    pid = str(pid)
    for rid, players in (state.get("roster_players") or {}).items():
        if pid in {str(x) for x in (players or [])}:
            return str(rid)
    return None


def owner_from_state(state: ah.LeagueState, pid: str) -> Optional[str]:
    pid = str(pid)
    for rid, players in state.roster_players.items():
        if pid in {str(x) for x in players}:
            return str(rid)
    return None


def actual_pre_state(
    adapter: FSFFLHistoricalAdapter,
    season: str,
    event: Dict[str, Any],
) -> ah.LeagueState:
    return reconstruct_completed_season_state(
        adapter,
        str(season),
        int(event.get("created") or 0),
    )


def divergent_players(
    actual: ah.LeagueState,
    branch_state: Dict[str, Any],
    *,
    actual_owners: Optional[Dict[str, str]] = None,
    branch_owners: Optional[Dict[str, str]] = None,
) -> Set[str]:
    actual_owners = actual_owners if actual_owners is not None else actual_owner_index(actual)
    branch_owners = branch_owners if branch_owners is not None else payload_owner_index(branch_state)
    ids = set(actual_owners) | set(branch_owners)
    return {pid for pid in ids if actual_owners.get(pid) != branch_owners.get(pid)}


def event_players(event: Dict[str, Any]) -> Set[str]:
    return {str(x) for x in (event.get("adds") or {}).keys()} | {
        str(x) for x in (event.get("drops") or {}).keys()
    }


def event_season_week(event: Dict[str, Any], fallback_season: str) -> Tuple[str, Optional[int]]:
    meta = event.get("metadata") or {}
    season = event.get("source_season") or event.get("season") or meta.get("source_season") or meta.get("season") or fallback_season
    week = event.get("leg") or event.get("week") or meta.get("leg") or meta.get("week")
    try:
        parsed = int(week) if week is not None else None
    except (TypeError, ValueError):
        parsed = None
    return str(season), parsed


def pick_controller_payload(state: Dict[str, Any], pick: Dict[str, Any]) -> Optional[str]:
    key = ah.pick_key(pick)
    original = pick.get("roster_id")
    if not key:
        return None
    return str((state.get("pick_owners") or {}).get(key) or original or "") or None


def pick_controller_actual(state: ah.LeagueState, pick: Dict[str, Any]) -> Optional[str]:
    key = ah.pick_key(pick)
    original = pick.get("roster_id")
    if not key:
        return None
    return str(state.pick_owners.get(key) or original or "") or None


def classify(
    adapter: FSFFLHistoricalAdapter,
    branch_state: Dict[str, Any],
    event: Dict[str, Any],
    season: str,
    positions: Dict[str, str],
) -> Dict[str, Any]:
    actual = actual_pre_state(adapter, season, event)
    branch_league_state = branch_v1.to_state(branch_state)
    legal, reasons = event_legality(branch_league_state, event)
    actual_owners = actual_owner_index(actual)
    branch_owners = payload_owner_index(branch_state)
    div = divergent_players(
        actual,
        branch_state,
        actual_owners=actual_owners,
        branch_owners=branch_owners,
    )
    players = event_players(event)
    direct = sorted(players & div)
    participants = {str(x) for x in (event.get("roster_ids") or [])}

    divergent_positions_by_roster: Dict[str, Set[str]] = defaultdict(set)
    for pid in div:
        pos = positions.get(pid, "")
        if not pos:
            continue
        for rid in (actual_owners.get(pid), branch_owners.get(pid)):
            if rid is not None:
                divergent_positions_by_roster[str(rid)].add(pos)
    epositions = {positions.get(pid, "") for pid in players if positions.get(pid, "")}
    positional_participants = sorted(
        rid for rid in participants
        if divergent_positions_by_roster.get(rid, set()) & epositions
    )

    pick_divergence = []
    for pick in event.get("draft_picks") or []:
        actual_owner = pick_controller_actual(actual, pick)
        branch_owner = pick_controller_payload(branch_state, pick)
        if actual_owner != branch_owner:
            pick_divergence.append({
                "pick_key": ah.pick_key(pick),
                "actual_owner": actual_owner,
                "branch_owner": branch_owner,
            })

    etype = str(event.get("type") or "")
    if not legal:
        policy = "MECHANICALLY_IMPOSSIBLE"
    elif etype in {"waiver", "free_agent"} and (direct or positional_participants):
        policy = "DYNAMIC_USAGE"
    elif etype == "trade" and (direct or positional_participants or pick_divergence):
        policy = "DYNAMIC_TRADE"
    else:
        policy = "PRESERVE_HISTORICAL"

    return {
        "policy": policy,
        "exact_terms_legal": bool(legal),
        "legality_reasons": reasons,
        "divergent_player_ids": sorted(div),
        "direct_divergent_assets": direct,
        "positional_divergent_participants": positional_participants,
        "pick_divergence": pick_divergence,
        "event_positions": sorted(epositions),
    }


def trailing(points: HistoricalPoints, season: str, week: Optional[int], pid: str) -> Dict[str, Any]:
    return points.trailing(str(season), week, str(pid))


def signal_score(signal: Dict[str, Any]) -> float:
    return float(signal.get("score") or 0.0)


def dynamic_usage_outcomes(
    branch_state: Dict[str, Any],
    event: Dict[str, Any],
    *,
    season: str,
    week: Optional[int],
    positions: Dict[str, str],
    points: HistoricalPoints,
) -> List[Dict[str, Any]]:
    adds_by_roster: Dict[str, List[str]] = defaultdict(list)
    drops_by_roster: Dict[str, List[str]] = defaultdict(list)
    for pid, rid in (event.get("adds") or {}).items():
        adds_by_roster[str(rid)].append(str(pid))
    for pid, rid in (event.get("drops") or {}).items():
        drops_by_roster[str(rid)].append(str(pid))
    rosters = sorted(set(adds_by_roster) | set(drops_by_roster))
    if len(rosters) != 1:
        return [{"outcome": "preserve_exact", "probability": 1.0, "mode": "exact"}]

    rid = rosters[0]
    added = adds_by_roster.get(rid, [])
    dropped = drops_by_roster.get(rid, [])
    roster = [str(x) for x in ((branch_state.get("roster_players") or {}).get(rid) or [])]

    all_add_available = all(
        owner_from_payload(branch_state, pid) in {None, rid}
        for pid in added
    )
    if not all_add_available:
        return [{"outcome": "no_action", "probability": 1.0, "mode": "no_action"}]

    actual_drop_still_owned = all(owner_from_payload(branch_state, pid) == rid for pid in dropped)
    add_positions = {positions.get(pid, "") for pid in added if positions.get(pid, "")}
    candidates = []
    for pid in roster:
        if pid in set(added):
            continue
        pos = positions.get(pid, "")
        if add_positions and pos not in add_positions:
            continue
        sig = trailing(points, season, week, pid)
        candidates.append({"player_id": pid, "position": pos, "trailing_signal": sig})
    candidates.sort(key=lambda row: (signal_score(row["trailing_signal"]), row["player_id"]))
    alternate_drop = candidates[0] if candidates else None

    add_scores = []
    add_obs = 0
    for pid in added:
        sig = trailing(points, season, week, pid)
        add_obs += int(sig.get("observations") or 0)
        if sig.get("score") is not None:
            add_scores.append(signal_score(sig))
    weakest = None
    if alternate_drop and alternate_drop["trailing_signal"].get("score") is not None:
        weakest = signal_score(alternate_drop["trailing_signal"])
    improvement = None
    if add_scores and weakest is not None:
        improvement = (sum(add_scores) / len(add_scores)) - weakest

    preserve_logit = 1.65
    if improvement is not None:
        preserve_logit += max(-1.4, min(1.4, improvement / 8.0))
    elif add_obs == 0:
        preserve_logit -= 0.35
    exact_logit = preserve_logit + (0.8 if actual_drop_still_owned else -math.inf)
    change_logit = preserve_logit - 0.35 + (1.1 if not actual_drop_still_owned else -0.5)
    probs = softmax({
        "preserve_exact": exact_logit,
        "preserve_add_change_drop": change_logit,
        "no_action": 0.0,
    })
    decision = {
        "roster_id": rid,
        "suggested_alternate_drop": alternate_drop,
        "probabilities": probs,
    }
    return branch_v1.usage_outcomes(event, {"decisions": [decision]})


def trade_candidate_rows(
    branch_state: Dict[str, Any],
    *,
    sender: str,
    target_pid: str,
    event_player_ids: Set[str],
    season: str,
    week: Optional[int],
    positions: Dict[str, str],
    points: HistoricalPoints,
) -> List[Dict[str, Any]]:
    target_pos = positions.get(str(target_pid), "")
    target_signal = trailing(points, season, week, target_pid)
    target_score = target_signal.get("score")
    rows = []
    for pid in sorted(str(x) for x in ((branch_state.get("roster_players") or {}).get(str(sender)) or [])):
        if pid == str(target_pid) or pid in event_player_ids:
            continue
        if target_pos and positions.get(pid, "") != target_pos:
            continue
        sig = trailing(points, season, week, pid)
        score = sig.get("score")
        delta = None if score is None or target_score is None else float(score) - float(target_score)
        logit = -1.8 if delta is None else -min(4.0, abs(delta) / 4.0)
        obs = int(sig.get("observations") or 0) + int(target_signal.get("observations") or 0)
        if obs >= 4:
            logit += 0.35
        elif obs == 0:
            logit -= 0.4
        rows.append({
            "player_id": pid,
            "sender_roster_id": str(sender),
            "target_player_id": str(target_pid),
            "logit": logit,
        })
    if not rows:
        return []
    mx = max(float(row["logit"]) for row in rows)
    exps = [math.exp(max(-20.0, min(20.0, float(row["logit"]) - mx))) for row in rows]
    denom = sum(exps) or 1.0
    for row, weight in zip(rows, exps):
        row["conditional_probability"] = weight / denom
    rows.sort(key=lambda row: (-float(row["conditional_probability"]), row["player_id"]))
    return rows[:MAX_TRADE_CANDIDATES]


def dynamic_trade_outcomes(
    adapter: FSFFLHistoricalAdapter,
    branch_state: Dict[str, Any],
    event: Dict[str, Any],
    classification: Dict[str, Any],
    *,
    season: str,
    week: Optional[int],
    positions: Dict[str, str],
    points: HistoricalPoints,
) -> List[Dict[str, Any]]:
    legal = bool(classification.get("exact_terms_legal"))
    direct = set(classification.get("direct_divergent_assets") or [])
    positional = set(classification.get("positional_divergent_participants") or [])
    pick_div = classification.get("pick_divergence") or []

    if not legal:
        probs = normalized_probs(0.0, 0.68, 0.32)
    elif direct:
        probs = normalized_probs(0.20, 0.55, 0.25)
    elif positional:
        probs = normalized_probs(0.62, 0.23, 0.15)
    elif pick_div:
        probs = normalized_probs(0.55, 0.25, 0.20)
    else:
        probs = normalized_probs(0.82, 0.10, 0.08)

    rows: List[Dict[str, Any]] = [
        {"outcome": "preserve_historical_trade", "probability": float(probs["preserve_historical_trade"]), "mode": "exact"},
        {"outcome": "no_trade", "probability": float(probs["no_trade"]), "mode": "no_action"},
    ]
    modified_mass = float(probs["modified_trade_branch"])
    if modified_mass <= 0.0:
        return branch_v1.normalize(rows)

    drops = {str(pid): str(rid) for pid, rid in (event.get("drops") or {}).items()}
    eplayers = event_players(event)
    targets: List[Tuple[str, str]] = []
    for pid, sender in drops.items():
        unavailable = owner_from_payload(branch_state, pid) != sender
        direct_asset = pid in direct
        same_position_context = sender in positional
        if unavailable or direct_asset or same_position_context:
            targets.append((pid, sender))
    hard = [(pid, sender) for pid, sender in targets if owner_from_payload(branch_state, pid) != sender or pid in direct]
    targets = hard if hard else targets[:1]

    candidate_sets = [
        trade_candidate_rows(
            branch_state,
            sender=sender,
            target_pid=pid,
            event_player_ids=eplayers,
            season=season,
            week=week,
            positions=positions,
            points=points,
        )
        for pid, sender in targets
    ]
    if not targets or any(not rows_ for rows_ in candidate_sets):
        rows[1]["probability"] += modified_mass
        return branch_v1.normalize(rows)

    packages = []
    for combo in itertools.product(*candidate_sets):
        joint = 1.0
        replacements = []
        for cand in combo:
            joint *= float(cand.get("conditional_probability") or 0.0)
            replacements.append({
                "outgoing_historical_player_id": cand["target_player_id"],
                "sender_roster_id": cand["sender_roster_id"],
                "replacement_player_id": cand["player_id"],
            })
        packages.append((joint, replacements))
    packages.sort(key=lambda row: row[0], reverse=True)
    packages = packages[:MAX_TRADE_PACKAGES]
    denom = sum(weight for weight, _ in packages) or 1.0
    concrete = 0.0
    for idx, (weight, replacements) in enumerate(packages, 1):
        cp = weight / denom
        package = {"replacements": replacements}
        alt_event = branch_v1.modified_trade_event(event, package)
        if alt_event is None:
            continue
        concrete += cp
        rows.append({
            "outcome": "modified_trade",
            "probability": modified_mass * cp,
            "mode": "event",
            "event": alt_event,
            "package_id": f"dynamic:{event.get('transaction_id')}:{idx}",
        })
    rows[1]["probability"] += modified_mass * max(0.0, 1.0 - concrete)
    return branch_v1.normalize(rows)


def outcomes_for_branch(
    adapter: FSFFLHistoricalAdapter,
    branch_state: Dict[str, Any],
    event: Dict[str, Any],
    *,
    season: str,
    positions: Dict[str, str],
    points: HistoricalPoints,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    classification = classify(adapter, branch_state, event, season, positions)
    _, week = event_season_week(event, season)
    policy = classification["policy"]
    if policy == "MECHANICALLY_IMPOSSIBLE":
        outcomes = [{"outcome": "no_action", "probability": 1.0, "mode": "no_action"}]
    elif policy == "DYNAMIC_USAGE":
        outcomes = dynamic_usage_outcomes(
            branch_state,
            event,
            season=season,
            week=week,
            positions=positions,
            points=points,
        )
    elif policy == "DYNAMIC_TRADE":
        outcomes = dynamic_trade_outcomes(
            adapter,
            branch_state,
            event,
            classification,
            season=season,
            week=week,
            positions=positions,
            points=points,
        )
    else:
        outcomes = branch_v1.branch_specific_outcomes(
            branch_state,
            event,
            [{"outcome": "preserve_historical", "probability": 1.0, "mode": "exact"}],
        )
    return classification, outcomes
