#!/usr/bin/env python3
"""Shared point-in-time FSFFL historical state provider.

This module promotes the validated historical-state reconstruction primitive
originally built for Alternate History into reusable infrastructure.  It owns
historical FACT reconstruction only; it contains no counterfactual policy,
branching, behavioral inference, GM logic, or simulation.

Consumers such as Alternate History and Behavioral Intelligence may share these
facts without sharing model conclusions.

Source priority:
1. Alternate History immutable completed-season cache when present.
2. Existing update_sleeper.build_history() ingestion as a refresh-time fallback.

The provider reconstructs a season from its latest roster/pick/FAAB snapshot and
walks completed transactions backward once, caching the exact pre-timestamp
state for every transaction. Draft state can be requested at the draft start;
callers may then replay picks forward in pick order when per-pick context is
needed.
"""
from __future__ import annotations

import copy
import importlib.util
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
AH_CACHE = DATA / "alternate_history" / "source_history" / "sleeper_history.json"
MODEL_VERSION = "FSFFL-Historical-State-Provider-1.0"


@dataclass
class HistoricalState:
    season: str
    timestamp_ms: int
    roster_players: Dict[str, set[str]] = field(default_factory=dict)
    roster_taxi: Dict[str, set[str]] = field(default_factory=dict)
    roster_reserve: Dict[str, set[str]] = field(default_factory=dict)
    pick_owners: Dict[str, str] = field(default_factory=dict)
    faab_used: Dict[str, float] = field(default_factory=dict)
    reconstruction: Dict[str, Any] = field(default_factory=dict)


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _load_update_sleeper():
    path = ROOT / "script" / "update_sleeper.py"
    spec = importlib.util.spec_from_file_location("fsffl_update_sleeper_for_history", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def load_history() -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Load authoritative season snapshots without creating model coupling."""
    cached = _load_json(AH_CACHE, {})
    cached_history = cached.get("history") if isinstance(cached, dict) else None
    if cached_history:
        # The AH cache intentionally excludes the active season. Add the current
        # season from canonical Sleeper artifacts via the same existing ingestor.
        update = _load_update_sleeper()
        live = update.fetch_league_season(update.STARTING_LEAGUE_ID)
        history = list(cached_history) + [live]
        history.sort(key=lambda x: int((x.get("league") or {}).get("season") or 0))
        return history, {
            "source": "alternate_history_cache_plus_current_sleeper",
            "alternate_history_cache_reused": True,
        }

    update = _load_update_sleeper()
    history = update.build_history()
    history.sort(key=lambda x: int((x.get("league") or {}).get("season") or 0))
    return history, {
        "source": "existing_update_sleeper_build_history",
        "alternate_history_cache_reused": False,
    }


def _pick_key(pick: Dict[str, Any]) -> str | None:
    season = pick.get("season")
    rnd = pick.get("round")
    roster = pick.get("roster_id")
    if season is None or rnd is None or roster is None:
        return None
    return f"pick:{season}:R{rnd}:orig{roster}"


def _season_data(history: Iterable[Dict[str, Any]], season: str) -> Dict[str, Any]:
    for row in history:
        if str((row.get("league") or {}).get("season") or "") == str(season):
            return row
    raise KeyError(f"Historical season {season} unavailable")


def user_to_roster(data: Dict[str, Any]) -> Dict[str, str]:
    out = {}
    for roster in data.get("rosters") or []:
        uid = roster.get("owner_id")
        rid = roster.get("roster_id")
        if uid is not None and rid is not None:
            out[str(uid)] = str(rid)
    return out


def roster_to_user(data: Dict[str, Any]) -> Dict[str, str]:
    return {rid: uid for uid, rid in user_to_roster(data).items()}


def season_end_state(history: Iterable[Dict[str, Any]], season: str) -> HistoricalState:
    data = _season_data(history, season)
    rosters, taxi, reserve, faab = {}, {}, {}, {}
    for r in data.get("rosters") or []:
        rid = str(r.get("roster_id"))
        rosters[rid] = {str(x) for x in (r.get("players") or [])}
        taxi[rid] = {str(x) for x in (r.get("taxi") or [])}
        reserve[rid] = {str(x) for x in (r.get("reserve") or [])}
        faab[rid] = float((r.get("settings") or {}).get("waiver_budget_used") or 0.0)
    picks = {}
    for p in data.get("traded_picks") or []:
        key = _pick_key(p)
        if key and p.get("owner_id") is not None:
            picks[key] = str(p.get("owner_id"))
    latest = max((int(t.get("created") or 0) for t in (data.get("transactions") or [])), default=0)
    return HistoricalState(
        season=str(season), timestamp_ms=latest + 1,
        roster_players=rosters, roster_taxi=taxi, roster_reserve=reserve,
        pick_owners=picks, faab_used=faab,
        reconstruction={"source": "season_snapshot", "confidence": "high"},
    )


def completed_transactions(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = [
        t for t in (data.get("transactions") or [])
        if t.get("status") in {None, "complete", "completed"}
    ]
    return sorted(rows, key=lambda x: (int(x.get("created") or 0), str(x.get("transaction_id") or "")))


def reverse_transaction(state: HistoricalState, tx: Dict[str, Any]) -> Dict[str, int]:
    counts = {"player_moves": 0, "pick_moves": 0, "faab_moves": 0, "anomalies": 0}
    adds = tx.get("adds") or {}
    drops = tx.get("drops") or {}
    for pid, recv in adds.items():
        rid = str(recv)
        state.roster_players.setdefault(rid, set())
        if str(pid) not in state.roster_players[rid]:
            counts["anomalies"] += 1
        state.roster_players[rid].discard(str(pid))
        state.roster_taxi.setdefault(rid, set()).discard(str(pid))
        state.roster_reserve.setdefault(rid, set()).discard(str(pid))
        counts["player_moves"] += 1
    for pid, send in drops.items():
        rid = str(send)
        state.roster_players.setdefault(rid, set()).add(str(pid))
        counts["player_moves"] += 1

    for p in tx.get("draft_picks") or []:
        key = _pick_key(p)
        prev = p.get("previous_owner_id")
        if key and prev is not None:
            state.pick_owners[key] = str(prev)
            counts["pick_moves"] += 1

    for b in tx.get("waiver_budget") or []:
        amount = float(b.get("amount") or 0.0)
        sender, receiver = b.get("sender"), b.get("receiver")
        # Sleeper roster settings track amount USED. Reversing a transfer of
        # budget restores the pre-event used-budget direction symmetrically.
        if sender is not None:
            state.faab_used[str(sender)] = state.faab_used.get(str(sender), 0.0) - amount
        if receiver is not None:
            state.faab_used[str(receiver)] = state.faab_used.get(str(receiver), 0.0) + amount
        if sender is not None or receiver is not None:
            counts["faab_moves"] += 1
    return counts


def draft_start_ms(data: Dict[str, Any], draft_id: str | None = None) -> int:
    for entry in data.get("drafts") or []:
        d = entry.get("draft") or {}
        if draft_id and str(d.get("draft_id") or "") != str(draft_id):
            continue
        ts = int(d.get("start_time") or d.get("created") or 0)
        if ts:
            return ts
    return 0


def remove_future_draftees(state: HistoricalState, data: Dict[str, Any], timestamp_ms: int) -> int:
    removed = 0
    for entry in data.get("drafts") or []:
        d = entry.get("draft") or {}
        start = int(d.get("start_time") or d.get("created") or 0)
        if start <= 0 or timestamp_ms >= start:
            continue
        for pick in entry.get("picks") or []:
            pid = pick.get("player_id") or (pick.get("metadata") or {}).get("player_id")
            if pid is None:
                continue
            for roster in state.roster_players.values():
                if str(pid) in roster:
                    roster.discard(str(pid)); removed += 1
            for roster in state.roster_taxi.values(): roster.discard(str(pid))
            for roster in state.roster_reserve.values(): roster.discard(str(pid))
    return removed


class HistoricalStateProvider:
    def __init__(self, history: List[Dict[str, Any]] | None = None):
        if history is None:
            history, provenance = load_history()
        else:
            provenance = {"source": "caller_supplied_history", "alternate_history_cache_reused": False}
        self.history = history
        self.provenance = provenance
        self._pre_event_cache: Dict[str, Dict[str, HistoricalState]] = {}

    def seasons(self) -> List[str]:
        return [str((x.get("league") or {}).get("season")) for x in self.history]

    def data(self, season: str) -> Dict[str, Any]:
        return _season_data(self.history, season)

    def pre_event_cache(self, season: str) -> Dict[str, HistoricalState]:
        season = str(season)
        if season in self._pre_event_cache:
            return self._pre_event_cache[season]
        data = self.data(season)
        state = copy.deepcopy(season_end_state(self.history, season))
        by_ts: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
        for tx in completed_transactions(data):
            by_ts[int(tx.get("created") or 0)].append(tx)
        result = {}
        totals = defaultdict(int)
        for ts in sorted(by_ts, reverse=True):
            same = by_ts[ts]
            for tx in reversed(same):
                c = reverse_transaction(state, tx)
                for k, v in c.items(): totals[k] += v
            snap = copy.deepcopy(state)
            removed = remove_future_draftees(snap, data, ts)
            snap.timestamp_ms = ts
            snap.reconstruction = {
                "source": "incremental_reverse_transaction_cache",
                "season": season,
                "confidence": "high" if totals["anomalies"] == 0 else "medium",
                "player_moves_reversed": totals["player_moves"],
                "pick_moves_reversed": totals["pick_moves"],
                "faab_moves_reversed": totals["faab_moves"],
                "ownership_anomalies": totals["anomalies"],
                "future_draftees_removed": removed,
            }
            for tx in same:
                tid = str(tx.get("transaction_id") or "")
                if tid:
                    result[tid] = snap
        self._pre_event_cache[season] = result
        return result

    def pre_transaction_state(self, season: str, transaction_id: str) -> HistoricalState:
        state = self.pre_event_cache(str(season)).get(str(transaction_id))
        if state is None:
            raise KeyError(f"Pre-event state missing for {season}/{transaction_id}")
        return state

    def state_at(self, season: str, timestamp_ms: int) -> HistoricalState:
        data = self.data(str(season))
        state = copy.deepcopy(season_end_state(self.history, str(season)))
        totals = defaultdict(int)
        for tx in reversed(completed_transactions(data)):
            if int(tx.get("created") or 0) < int(timestamp_ms):
                break
            c = reverse_transaction(state, tx)
            for k, v in c.items(): totals[k] += v
        removed = remove_future_draftees(state, data, int(timestamp_ms))
        state.timestamp_ms = int(timestamp_ms)
        state.reconstruction = {
            "source": "timestamp_reverse_replay",
            "season": str(season),
            "confidence": "high" if totals["anomalies"] == 0 else "medium",
            "ownership_anomalies": totals["anomalies"],
            "future_draftees_removed": removed,
        }
        return state

    def draft_pre_pick_states(self, season: str, draft_id: str) -> List[Dict[str, Any]]:
        data = self.data(str(season))
        entry = next((x for x in (data.get("drafts") or []) if str((x.get("draft") or {}).get("draft_id") or "") == str(draft_id)), None)
        if entry is None:
            return []
        start = draft_start_ms(data, draft_id)
        state = self.state_at(str(season), start)
        u2r = user_to_roster(data)
        rows = []
        picks = sorted(entry.get("picks") or [], key=lambda p: int(p.get("pick_no") or 0))
        for pick in picks:
            uid = str(pick.get("picked_by") or "")
            rid = u2r.get(uid) or (str(pick.get("roster_id")) if pick.get("roster_id") is not None else None)
            rows.append({"pick": pick, "user_id": uid, "roster_id": rid, "pre_state": copy.deepcopy(state)})
            pid = pick.get("player_id") or (pick.get("metadata") or {}).get("player_id")
            if rid and pid is not None:
                state.roster_players.setdefault(rid, set()).add(str(pid))
        return rows


def provider_audit(provider: HistoricalStateProvider) -> Dict[str, Any]:
    transactions = states = anomalies = 0
    for season in provider.seasons():
        cache = provider.pre_event_cache(season)
        transactions += len(completed_transactions(provider.data(season)))
        states += len(cache)
        for st in cache.values():
            anomalies = max(anomalies, int((st.reconstruction or {}).get("ownership_anomalies") or 0))
    return {
        "model_version": MODEL_VERSION,
        "seasons": provider.seasons(),
        "transaction_count": transactions,
        "cached_pre_transaction_state_count": states,
        "max_cumulative_ownership_anomalies": anomalies,
        "provenance": provider.provenance,
    }
