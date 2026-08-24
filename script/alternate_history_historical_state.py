#!/usr/bin/env python3
"""Completed-season historical state anchors for Alternate History.

The generic 0.1 engine originally rewound from the active current roster. That
is sufficient for transaction dependency experiments but is not safe for true
multi-season ownership replay because rookie draft acquisitions are not Sleeper
transactions and can otherwise leak backward across seasons.

Production historical reconstruction for a completed season therefore anchors
to that season's archived Sleeper roster/traded-pick snapshot and reverses only
same-season transactions after the requested timestamp. Drafted players are also
removed when reconstructing to a timestamp before that season's rookie draft.

Performance note: historical policy evaluation needs the pre-event state for
hundreds of transactions. Reconstructing each one independently repeats almost
the same reverse replay and becomes quadratic in transaction count. The
season-level cache below walks history backward once, snapshots the state at each
transaction timestamp, and serves the exact same timestamp-safe state thereafter.
"""

from __future__ import annotations

import copy
from collections import defaultdict
from typing import Any, Dict, List

import alternate_history_engine as ah
from run_fsffl_alternate_history import FSFFLHistoricalAdapter


def season_data(adapter: FSFFLHistoricalAdapter, season: str) -> Dict[str, Any]:
    for row in adapter.raw_history_seasons():
        if str((row.get("league") or {}).get("season") or "") == str(season):
            return row
    raise ah.AlternateHistoryError(f"Archived completed-season Sleeper data unavailable for {season}")


def user_to_roster(data: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for roster in data.get("rosters") or []:
        uid = roster.get("owner_id")
        rid = roster.get("roster_id")
        if uid is not None and rid is not None:
            out[str(uid)] = str(rid)
    return out


def season_end_state(adapter: FSFFLHistoricalAdapter, season: str) -> ah.LeagueState:
    data = season_data(adapter, season)
    league = data.get("league") or {}
    roster_players: Dict[str, set[str]] = {}
    roster_taxi: Dict[str, set[str]] = {}
    roster_reserve: Dict[str, set[str]] = {}
    faab: Dict[str, float] = {}

    for roster in data.get("rosters") or []:
        rid = str(roster.get("roster_id"))
        roster_players[rid] = {str(x) for x in (roster.get("players") or [])}
        roster_taxi[rid] = {str(x) for x in (roster.get("taxi") or [])}
        roster_reserve[rid] = {str(x) for x in (roster.get("reserve") or [])}
        settings = roster.get("settings") or {}
        faab[rid] = float(settings.get("waiver_budget_used") or 0.0)

    pick_owners: Dict[str, str] = {}
    for pick in data.get("traded_picks") or []:
        key = ah.pick_key(pick)
        owner = pick.get("owner_id")
        if key and owner is not None:
            pick_owners[key] = str(owner)

    txs = data.get("transactions") or []
    latest = max((int(tx.get("created") or 0) for tx in txs), default=0)
    return ah.LeagueState(
        league_key=str(league.get("league_id") or f"fsffl-{season}"),
        timestamp_ms=latest + 1,
        roster_players=roster_players,
        roster_taxi=roster_taxi,
        roster_reserve=roster_reserve,
        pick_owners=pick_owners,
        faab=faab,
        reconstruction={
            "source": "archived_completed_season_end_snapshot",
            "season": str(season),
            "future_season_rookie_leakage_prevented": True,
        },
    )


def normalized_same_season_events(adapter: FSFFLHistoricalAdapter, season: str) -> List[Dict[str, Any]]:
    rows = []
    for event in adapter.completed_events():
        source = event.get("source_season") or (event.get("metadata") or {}).get("source_season")
        if str(source or "") == str(season):
            rows.append(event)
    return sorted(rows, key=lambda x: int(x.get("created") or 0))


def reverse_future_draft_acquisitions(
    state: ah.LeagueState,
    data: Dict[str, Any],
    timestamp_ms: int,
) -> Dict[str, int]:
    """Remove same-season draftees if reconstructing before their rookie draft."""
    u2r = user_to_roster(data)
    drafts_reversed = 0
    players_removed = 0
    for entry in data.get("drafts") or []:
        draft = entry.get("draft") or {}
        start = int(draft.get("start_time") or draft.get("created") or 0)
        if start <= 0 or int(timestamp_ms) >= start:
            continue
        drafts_reversed += 1
        for pick in entry.get("picks") or []:
            pid = pick.get("player_id") or (pick.get("metadata") or {}).get("player_id")
            uid = pick.get("picked_by")
            rid = u2r.get(str(uid)) if uid is not None else None
            if pid is None:
                continue
            for players in state.roster_players.values():
                if str(pid) in players:
                    players.discard(str(pid))
                    players_removed += 1
            if rid is not None:
                state.roster_taxi.setdefault(rid, set()).discard(str(pid))
                state.roster_reserve.setdefault(rid, set()).discard(str(pid))
    return {"drafts_reversed": drafts_reversed, "draft_players_removed": players_removed}


def _event_cache_key(event: Dict[str, Any]) -> str:
    return f"{str(event.get('transaction_id') or '')}|{int(event.get('created') or 0)}"


def completed_season_pre_event_cache(
    adapter: FSFFLHistoricalAdapter,
    season: str,
) -> Dict[str, ah.LeagueState]:
    """Build/read exact pre-event historical states with one reverse pass.

    Events sharing a timestamp receive the same pre-timestamp state, matching
    reconstruct_completed_season_state(), which reverses every event whose
    created timestamp is >= the requested timestamp.
    """
    season = str(season)
    cache_root = getattr(adapter, "_alternate_history_pre_event_state_cache", None)
    if cache_root is None:
        cache_root = {}
        setattr(adapter, "_alternate_history_pre_event_state_cache", cache_root)
    if season in cache_root:
        return cache_root[season]

    data = season_data(adapter, season)
    state = copy.deepcopy(season_end_state(adapter, season))
    events = normalized_same_season_events(adapter, season)
    by_timestamp: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_timestamp[int(event.get("created") or 0)].append(event)

    result: Dict[str, ah.LeagueState] = {}
    reversed_events = 0
    player_moves = pick_moves = faab_moves = 0
    for created in sorted(by_timestamp, reverse=True):
        same_time = by_timestamp[created]
        for event in reversed(same_time):
            counts = ah.reverse_event(state, event)
            reversed_events += 1
            player_moves += int(counts.get("player_moves") or 0)
            pick_moves += int(counts.get("pick_moves") or 0)
            faab_moves += int(counts.get("faab_moves") or 0)

        snapshot = copy.deepcopy(state)
        draft_counts = reverse_future_draft_acquisitions(snapshot, data, created)
        snapshot.timestamp_ms = created
        snapshot.reconstruction = {
            "source": "archived_completed_season_incremental_reverse_cache",
            "season": season,
            "reversed_same_season_transactions": reversed_events,
            "player_moves_reversed": player_moves,
            "pick_moves_reversed": pick_moves,
            "faab_moves_reversed": faab_moves,
            **draft_counts,
            "future_season_rookie_leakage_prevented": True,
            "confidence": "high",
        }
        for event in same_time:
            result[_event_cache_key(event)] = snapshot

    cache_root[season] = result
    return result


def cached_completed_season_pre_event_state(
    adapter: FSFFLHistoricalAdapter,
    season: str,
    event: Dict[str, Any],
) -> ah.LeagueState:
    cache = completed_season_pre_event_cache(adapter, str(season))
    key = _event_cache_key(event)
    state = cache.get(key)
    if state is None:
        return reconstruct_completed_season_state(
            adapter,
            str(season),
            int(event.get("created") or 0),
        )
    return state


def reconstruct_completed_season_state(
    adapter: FSFFLHistoricalAdapter,
    season: str,
    timestamp_ms: int,
) -> ah.LeagueState:
    state = copy.deepcopy(season_end_state(adapter, season))
    data = season_data(adapter, season)
    reversed_events = 0
    player_moves = pick_moves = faab_moves = 0

    for event in reversed(normalized_same_season_events(adapter, season)):
        created = int(event.get("created") or 0)
        if created < int(timestamp_ms):
            break
        counts = ah.reverse_event(state, event)
        reversed_events += 1
        player_moves += int(counts.get("player_moves") or 0)
        pick_moves += int(counts.get("pick_moves") or 0)
        faab_moves += int(counts.get("faab_moves") or 0)

    draft_counts = reverse_future_draft_acquisitions(state, data, int(timestamp_ms))
    state.timestamp_ms = int(timestamp_ms)
    state.reconstruction = {
        "source": "archived_completed_season_snapshot_reverse_replay",
        "season": str(season),
        "reversed_same_season_transactions": reversed_events,
        "player_moves_reversed": player_moves,
        "pick_moves_reversed": pick_moves,
        "faab_moves_reversed": faab_moves,
        **draft_counts,
        "future_season_rookie_leakage_prevented": True,
        "confidence": "high",
    }
    return state
