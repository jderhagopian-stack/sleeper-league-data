#!/usr/bin/env python3
"""Branch-local behavioral persistence for historical trades.

This opt-in Alternate History layer repairs a specific counterfactual failure mode:
an accepted historical trade should not collapse to no-action merely because one
outgoing asset is absent in a divergent branch when the same managerial intent,
competitive posture, target opportunity, and comparable branch-owned capital
still exist.

Historical-safety rules:
- only completed information strictly available at the transaction timestamp;
- no current GM 3.0 values or current market ranks;
- player substitutes are same-position assets owned by the historical sender in
  the live branch and ranked only by completed-prior-week fantasy production;
- pick substitutes must be the same season and round and already be controlled
  by the same historical sender in the live branch;
- the opposite side's historical target legs must remain intact: if missing
  outgoing assets span multiple senders, no synthetic equivalent is created;
- exact legal historical trades and all non-trade outcomes are left untouched.
"""
from __future__ import annotations

import copy
import itertools
import math
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import alternate_history_engine as ah
import run_fsffl_multiseason_branch_replay as branch_v1
import run_fsffl_multiseason_particle_replay_v3 as season_v3
from alternate_history_branch_scoring import update_records_from_week
from run_fsffl_alternate_history import FSFFLHistoricalAdapter
from run_fsffl_downstream_dependencies import event_legality, load
from run_fsffl_historical_trade_policy import player_positions
from run_fsffl_historical_usage_policy import HistoricalPoints

DATA = Path("data")
MAX_EQUIVALENTS = 6
MAX_PLAYER_CANDIDATES = 3
_ORIGINAL = branch_v1.branch_specific_outcomes
_POSITIONS: Optional[Dict[str, str]] = None
_POINTS: Optional[HistoricalPoints] = None
_ADAPTER: Optional[FSFFLHistoricalAdapter] = None
_ACTUAL_STATE_CACHE: Dict[int, ah.LeagueState] = {}


def _positions() -> Dict[str, str]:
    global _POSITIONS
    if _POSITIONS is None:
        _POSITIONS = player_positions()
    return _POSITIONS


def _points() -> HistoricalPoints:
    global _POINTS
    if _POINTS is None:
        _POINTS = HistoricalPoints()
    return _POINTS


def _adapter() -> FSFFLHistoricalAdapter:
    global _ADAPTER
    if _ADAPTER is None:
        _ADAPTER = FSFFLHistoricalAdapter()
    return _ADAPTER


def _actual_pre(timestamp_ms: int) -> ah.LeagueState:
    if timestamp_ms not in _ACTUAL_STATE_CACHE:
        _ACTUAL_STATE_CACHE[timestamp_ms] = ah.reconstruct_state(_adapter(), timestamp_ms)
    return _ACTUAL_STATE_CACHE[timestamp_ms]


def _event_season_week(event: Dict[str, Any]) -> Tuple[Optional[str], Optional[int]]:
    meta = event.get("metadata") or {}
    season = event.get("source_season") or event.get("season") or meta.get("source_season") or meta.get("season")
    week = event.get("leg") or event.get("week") or meta.get("leg") or meta.get("week")
    try:
        parsed = int(week) if week is not None else None
    except (TypeError, ValueError):
        parsed = None
    return (str(season) if season is not None else None, parsed)


def _win_pct(record: Dict[str, Any]) -> Optional[float]:
    w = float(record.get("wins") or 0.0)
    l = float(record.get("losses") or 0.0)
    t = float(record.get("ties") or 0.0)
    games = w + l + t
    return None if games <= 0 else (w + 0.5 * t) / games


@lru_cache(maxsize=32)
def _actual_records_before_week(season: str, week: int) -> Dict[str, Dict[str, Any]]:
    matchups = load(DATA / "stats" / "fsffl" / str(season) / "league_matchups_raw.json") or {}
    records: Dict[str, Dict[str, Any]] = {}
    for w in range(1, max(1, int(week))):
        rows = matchups.get(str(w), []) or []
        scores = {str(row.get("roster_id")): float(row.get("points") or 0.0) for row in rows}
        update_records_from_week(records, rows, scores)
    return records


def _competitive_similarity(state_payload: Dict[str, Any], event: Dict[str, Any]) -> float:
    season, week = _event_season_week(event)
    if not season or not week or week <= 1:
        return 0.75
    ledger = state_payload.get(season_v3.LEDGER_KEY) or {}
    branch_records = ((ledger.get(str(season)) or {}).get("records") or {})
    actual_records = _actual_records_before_week(str(season), int(week))
    participants = [str(x) for x in (event.get("roster_ids") or [])]
    vals: List[float] = []
    for rid in participants:
        a = _win_pct(actual_records.get(rid) or {})
        b = _win_pct(branch_records.get(rid) or {})
        if a is None or b is None:
            continue
        diff = abs(a - b)
        score = max(0.0, 1.0 - diff / 0.35)
        # Preserve broad contender/rebuilder posture even with small record noise.
        same_contender = a >= 0.55 and b >= 0.55
        same_rebuilder = a <= 0.40 and b <= 0.40
        if same_contender or same_rebuilder:
            score = max(score, 0.90)
        vals.append(score)
    return sum(vals) / len(vals) if vals else 0.75


def _position_counts(players: Iterable[str]) -> Dict[str, int]:
    pos = _positions()
    out: Dict[str, int] = {}
    for pid in players:
        p = pos.get(str(pid), "")
        if p:
            out[p] = out.get(p, 0) + 1
    return out


def _need_similarity(state_payload: Dict[str, Any], event: Dict[str, Any]) -> float:
    """Compare historical acquisition need with the branch at the same timestamp.

    Only net-positive incoming positions are treated as explicit need signals.
    Position-neutral swaps are treated as opportunity/quality trades and receive
    a neutral-high prior rather than being falsely labeled no-need.
    """
    created = int(event.get("created") or 0)
    actual = _actual_pre(created)
    branch_players = state_payload.get("roster_players") or {}
    adds = {str(pid): str(rid) for pid, rid in (event.get("adds") or {}).items()}
    drops = {str(pid): str(rid) for pid, rid in (event.get("drops") or {}).items()}
    pos = _positions()
    participants = {str(x) for x in (event.get("roster_ids") or [])}
    vals: List[float] = []
    for rid in participants:
        net: Dict[str, int] = {}
        for pid, receiver in adds.items():
            if receiver == rid and pos.get(pid):
                net[pos[pid]] = net.get(pos[pid], 0) + 1
        for pid, sender in drops.items():
            if sender == rid and pos.get(pid):
                net[pos[pid]] = net.get(pos[pid], 0) - 1
        needs = [p for p, delta in net.items() if delta > 0]
        if not needs:
            continue
        actual_counts = _position_counts(actual.roster_players.get(rid, set()))
        branch_counts = _position_counts(branch_players.get(rid, []) or [])
        for p in needs:
            extra_depth = branch_counts.get(p, 0) - actual_counts.get(p, 0)
            if extra_depth <= 0:
                vals.append(1.0)
            elif extra_depth == 1:
                vals.append(0.80)
            elif extra_depth == 2:
                vals.append(0.45)
            else:
                vals.append(0.15)
    return sum(vals) / len(vals) if vals else 0.78


def _player_signal(season: Optional[str], week: Optional[int], pid: str) -> Dict[str, Any]:
    if season is None:
        return {"score": None, "observations": 0}
    return _points().trailing(str(season), week, str(pid))


def _player_candidates(
    state: ah.LeagueState,
    sender: str,
    historical_pid: str,
    excluded: set[str],
    season: Optional[str],
    week: Optional[int],
) -> List[Tuple[str, float, Optional[float]]]:
    pos = _positions()
    target_pos = pos.get(str(historical_pid), "")
    if not target_pos:
        return []
    target = _player_signal(season, week, historical_pid)
    target_score = target.get("score")
    rows = []
    for pid in sorted(state.roster_players.get(str(sender), set())):
        pid = str(pid)
        if pid in excluded or pid == str(historical_pid) or pos.get(pid, "") != target_pos:
            continue
        sig = _player_signal(season, week, pid)
        score = sig.get("score")
        delta: Optional[float] = None
        if target_score is not None and score is not None:
            delta = float(score) - float(target_score)
            weight = math.exp(-min(5.0, abs(delta) / 4.0))
        else:
            weight = 0.22
        if int(sig.get("observations") or 0) >= 2:
            weight *= 1.10
        rows.append((pid, weight, None if delta is None else round(delta, 4)))
    rows.sort(key=lambda x: (-x[1], x[0]))
    return rows[:MAX_PLAYER_CANDIDATES]


def _pick_candidate_keys(state: ah.LeagueState, sender: str, historical_key: str) -> List[str]:
    parts = str(historical_key).split(":")
    if len(parts) < 4:
        return []
    prefix = ":".join(parts[:3]) + ":orig"
    return sorted(
        key for key, owner in state.pick_owners.items()
        if str(owner) == str(sender) and str(key).startswith(prefix) and str(key) != str(historical_key)
    )


def _replace_player_leg(event: Dict[str, Any], historical: str, replacement: str, sender: str) -> Optional[Dict[str, Any]]:
    out = copy.deepcopy(event)
    drops = {str(k): str(v) for k, v in (out.get("drops") or {}).items()}
    adds = {str(k): str(v) for k, v in (out.get("adds") or {}).items()}
    if drops.get(str(historical)) != str(sender):
        return None
    receiver = adds.get(str(historical))
    drops.pop(str(historical), None)
    adds.pop(str(historical), None)
    drops[str(replacement)] = str(sender)
    if receiver is not None:
        adds[str(replacement)] = str(receiver)
    out["drops"] = drops
    out["adds"] = adds
    return out


def _replace_pick_leg(event: Dict[str, Any], historical_key: str, replacement_key: str, sender: str) -> Optional[Dict[str, Any]]:
    out = copy.deepcopy(event)
    rows = list(out.get("draft_picks") or [])
    target_idx = None
    for idx, row in enumerate(rows):
        if ah.pick_key(row) == historical_key:
            target_idx = idx
            break
    if target_idx is None:
        return None
    orig = str(replacement_key).split(":orig", 1)[-1]
    row = dict(rows[target_idx])
    row["roster_id"] = orig
    row["previous_owner_id"] = str(sender)
    rows[target_idx] = row
    out["draft_picks"] = rows
    return out


def _equivalent_events(state_payload: Dict[str, Any], event: Dict[str, Any]) -> List[Dict[str, Any]]:
    state = branch_v1.to_state(state_payload)
    legal, reasons = event_legality(state, event)
    if legal or str(event.get("type") or "") != "trade":
        return []
    relevant = [r for r in reasons if r.get("kind") in {"missing_outgoing_player", "missing_outgoing_pick"}]
    if not relevant or len(relevant) != len(reasons):
        return []
    senders = {str(r.get("required_owner") or "") for r in relevant}
    senders.discard("")
    # Do not invent a new bilateral bargain when both sides' historical target
    # assets disappeared. Equivalent persistence repairs one altered payment side.
    if len(senders) != 1:
        return []
    sender = next(iter(senders))
    season, week = _event_season_week(event)
    excluded = {str(x) for x in (event.get("adds") or {}).keys()} | {str(x) for x in (event.get("drops") or {}).keys()}
    choices: List[List[Tuple[str, str, float, Optional[float]]]] = []
    for reason in relevant:
        if reason.get("kind") == "missing_outgoing_player":
            historical = str(reason.get("player_id") or "")
            cands = _player_candidates(state, sender, historical, excluded, season, week)
            if not cands:
                return []
            choices.append([("player", pid, weight, delta) for pid, weight, delta in cands])
        else:
            historical_key = str(reason.get("pick_key") or "")
            cands = _pick_candidate_keys(state, sender, historical_key)
            if not cands:
                return []
            choices.append([("pick", key, 1.0, None) for key in cands[:MAX_PLAYER_CANDIDATES]])

    out_rows: List[Dict[str, Any]] = []
    for combo in itertools.product(*choices):
        candidate = copy.deepcopy(event)
        weight = 1.0
        replacements = []
        ok = True
        for reason, choice in zip(relevant, combo):
            kind, value, w, delta = choice
            weight *= float(w)
            if kind == "player":
                historical = str(reason.get("player_id") or "")
                candidate = _replace_player_leg(candidate, historical, value, sender)
                replacements.append({"kind": "player", "historical": historical, "replacement": value, "sender": sender, "trailing_score_delta": delta})
            else:
                historical_key = str(reason.get("pick_key") or "")
                candidate = _replace_pick_leg(candidate, historical_key, value, sender)
                replacements.append({"kind": "pick", "historical": historical_key, "replacement": value, "sender": sender})
            if candidate is None:
                ok = False
                break
        if not ok or candidate is None:
            continue
        legal_candidate, _ = event_legality(state, candidate)
        if legal_candidate:
            out_rows.append({"event": candidate, "weight": weight, "replacements": replacements})
    out_rows.sort(key=lambda r: (-float(r["weight"]), repr(r["replacements"])))
    return out_rows[:MAX_EQUIVALENTS]


def _is_trade_proposal(event: Dict[str, Any], proposed: List[Dict[str, Any]]) -> bool:
    if str(event.get("type") or "") != "trade":
        return False
    outcomes = {str(x.get("outcome") or "") for x in proposed}
    return bool(outcomes & {"preserve_historical_trade", "modified_trade", "no_trade"})


def branch_specific_outcomes_v2(
    state_payload: Dict[str, Any],
    event: Dict[str, Any],
    proposed: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    base = _ORIGINAL(state_payload, event, proposed)
    if not _is_trade_proposal(event, proposed):
        return base

    state = branch_v1.to_state(state_payload)
    exact_legal, _ = event_legality(state, event)
    if exact_legal:
        # Critical regression invariant: do not perturb a still-legal historical trade.
        return base

    equivalents = _equivalent_events(state_payload, event)
    if not equivalents:
        return base

    original_no = sum(float(x.get("probability") or 0.0) for x in proposed if x.get("mode") == "no_action")
    original_intent = max(0.0, min(1.0, 1.0 - original_no))
    legal_trade_mass = sum(float(x.get("probability") or 0.0) for x in base if x.get("mode") != "no_action")
    need = _need_similarity(state_payload, event)
    competitive = _competitive_similarity(state_payload, event)
    context = max(0.0, min(1.0, 0.55 * need + 0.45 * competitive))
    # Accepted historical action is a strong revealed-action prior. When need
    # and competitive posture survive, 80-90% of the original intent mass should
    # persist through an economically similar legal package rather than vanish.
    persistence_fraction = 0.55 + 0.35 * context
    desired_trade_mass = max(legal_trade_mass, original_intent * persistence_fraction)
    recovery = max(0.0, min(1.0 - legal_trade_mass, desired_trade_mass - legal_trade_mass))
    if recovery <= 1e-12:
        return base

    rows = [dict(x) for x in base]
    no_rows = [x for x in rows if x.get("mode") == "no_action"]
    if not no_rows:
        return base
    no_row = no_rows[0]
    available_no = float(no_row.get("probability") or 0.0)
    recovery = min(recovery, available_no)
    if recovery <= 1e-12:
        return base
    no_row["probability"] = available_no - recovery

    denom = sum(max(0.0, float(x.get("weight") or 0.0)) for x in equivalents) or 1.0
    tid = str(event.get("transaction_id") or "")
    for idx, eq in enumerate(equivalents, 1):
        p = recovery * max(0.0, float(eq.get("weight") or 0.0)) / denom
        if p <= 0.0:
            continue
        rows.append({
            "outcome": "behaviorally_persistent_equivalent_trade",
            "probability": p,
            "mode": "event",
            "event": eq["event"],
            "package_id": f"{tid}:persistent-equivalent:{idx}",
            "equivalent_trade": True,
            "persistence_context": {
                "need_similarity": round(need, 4),
                "competitive_state_similarity": round(competitive, 4),
                "combined_context": round(context, 4),
                "persistence_fraction": round(persistence_fraction, 4),
                "replacements": eq.get("replacements") or [],
            },
        })
    return branch_v1.normalize(rows)


def install() -> None:
    if branch_v1.branch_specific_outcomes is branch_specific_outcomes_v2:
        return
    branch_v1.branch_specific_outcomes = branch_specific_outcomes_v2
