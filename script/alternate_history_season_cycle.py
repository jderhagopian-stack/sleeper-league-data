#!/usr/bin/env python3
"""Generic completed-season/offseason/draft cycle for Fantasy Alternate History.

This module removes calendar-year orchestration from the multi-season engine.
A season number is data, not code. Given an already-divergent weighted state it
can:

1. replay offseason transactions before that season's rookie draft;
2. replay the branch-specific rookie draft;
3. replay a completed fantasy season with immutable realized NFL/fantasy points;
4. resolve standings, playoffs, Max PF, and the following draft order;
5. repeat until the active season;
6. replay active-season fantasy transactions only and stop at Simulator 1.0.

Historical decisions never use current GM 3.0 numeric values or future NFL
outcomes. Equal-weight grouped particles conserve the full probability mass.
"""

from __future__ import annotations

import copy
import random
from collections import Counter
from typing import Any, Dict, List, Tuple

import alternate_history_dynamic_policy as dynamic_policy
import alternate_history_engine as ah
import run_fsffl_multiseason_particle_replay as particle_v1
import run_fsffl_multiseason_particle_replay_v3 as season_v3
import run_fsffl_second_season_particles as legacy_season
import run_fsffl_season_boundary_particles as boundary_core
from alternate_history_postseason import full_draft_slots, resolve_six_team_playoffs
from run_fsffl_alternate_draft_candidates import raw_draft
from run_fsffl_alternate_history import FSFFLHistoricalAdapter
from run_fsffl_counterfactual_replay import player_positions, starter_slots
from run_fsffl_downstream_dependencies import load
from run_fsffl_historical_usage_policy import HistoricalPoints

DATA = ah.DATA
SeasonParticleGroup = season_v3.SeasonParticleGroup
LEDGER_KEY = season_v3.LEDGER_KEY
MAX_TRACES_PER_GROUP = 3


def active_season() -> int:
    league = load(DATA / "league.json") or {}
    value = int(league.get("season") or 0)
    if value <= 0:
        raise ah.AlternateHistoryError("Unable to determine active FSFFL season")
    return value


def draft_start_ms(season: str) -> int:
    draft = (raw_draft(str(season)).get("draft") or {})
    value = int(draft.get("start_time") or draft.get("created") or 0)
    if value <= 0:
        raise ah.AlternateHistoryError(f"Draft start unavailable for {season}")
    return value


def draft_end_ms(season: str) -> int:
    draft = (raw_draft(str(season)).get("draft") or {})
    values = []
    for key in ("last_picked", "start_time", "created"):
        try:
            value = int(draft.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            values.append(value)
    if not values:
        raise ah.AlternateHistoryError(f"Draft timestamp unavailable for {season}")
    return max(values)


def season_events(
    adapter: FSFFLHistoricalAdapter,
    season: str,
    *,
    after_ms: int | None = None,
    before_ms: int | None = None,
) -> List[Dict[str, Any]]:
    rows = []
    for event in adapter.completed_events():
        source = event.get("source_season") or (event.get("metadata") or {}).get("source_season")
        if str(source or "") != str(season):
            continue
        created = int(event.get("created") or 0)
        if after_ms is not None and created <= int(after_ms):
            continue
        if before_ms is not None and created >= int(before_ms):
            continue
        rows.append(event)
    return sorted(rows, key=lambda row: (int(row.get("created") or 0), str(row.get("transaction_id") or "")))


def _dynamic_replay_events(
    groups: List[SeasonParticleGroup],
    events: List[Dict[str, Any]],
    *,
    season: str,
    particles: int,
    seed: int,
    phase: str,
) -> Tuple[List[SeasonParticleGroup], Dict[str, Any]]:
    """Replay a timestamp-ordered event slice against each branch's live state."""
    adapter = FSFFLHistoricalAdapter()
    positions = player_positions()
    points = HistoricalPoints()
    rng = random.Random(int(seed))
    actual_cache: Dict[str, ah.LeagueState] = {}
    original_actual_pre = dynamic_policy.actual_pre_state
    policy_particles: Counter = Counter()
    audits: List[Dict[str, Any]] = []
    max_unique = len(groups)

    def cached_actual(adapter_arg, season_arg, event_arg):
        tid = str(event_arg.get("transaction_id") or event_arg.get("created") or "")
        if tid not in actual_cache:
            actual_cache[tid] = original_actual_pre(adapter_arg, season_arg, event_arg)
        return actual_cache[tid]

    try:
        dynamic_policy.actual_pre_state = cached_actual
        for event in events:
            tid = str(event.get("transaction_id") or "")
            next_groups: List[SeasonParticleGroup] = []
            per_policy: Counter = Counter()
            per_outcome: Counter = Counter()
            for group in groups:
                classification, outcomes = dynamic_policy.outcomes_for_branch(
                    adapter,
                    group.state,
                    event,
                    season=str(season),
                    positions=positions,
                    points=points,
                )
                policy = str(classification.get("policy") or "UNKNOWN")
                per_policy[policy] += group.count
                policy_particles[policy] += group.count
                counts = particle_v1.multinomial_counts(
                    group.count,
                    [float(row.get("probability") or 0.0) for row in outcomes],
                    rng,
                )
                if sum(counts) != group.count:
                    raise ah.AlternateHistoryError(
                        f"generic {phase} particle conservation failed at {tid}"
                    )
                for outcome, count in zip(outcomes, counts):
                    if count <= 0:
                        continue
                    per_outcome[str(outcome.get("outcome") or "unknown")] += count
                    state = season_v3.apply_preserving_ledger(group.state, event, outcome)
                    step = {
                        "transaction_id": tid,
                        "timestamp_ms": int(event.get("created") or 0),
                        "kind": f"generic_{phase}_decision",
                        "season": str(season),
                        "policy": policy,
                        "outcome": outcome.get("outcome"),
                        "conditional_probability": round(float(outcome.get("probability") or 0.0), 8),
                        "particles": count,
                    }
                    if outcome.get("package_id"):
                        step["package_id"] = outcome.get("package_id")
                    traces = [
                        list(trace) + [step]
                        for trace in (group.traces or [[]])[:MAX_TRACES_PER_GROUP]
                    ]
                    next_groups.append(SeasonParticleGroup(count, state, traces))
            if sum(group.count for group in next_groups) != particles:
                raise ah.AlternateHistoryError(
                    f"generic {phase} global conservation failed at {tid}"
                )
            groups, merged = season_v3.merge_groups(next_groups)
            max_unique = max(max_unique, len(groups))
            audits.append({
                "transaction_id": tid,
                "timestamp_ms": int(event.get("created") or 0),
                "policy_particle_counts": dict(per_policy),
                "outcome_particle_counts": dict(per_outcome),
                "unique_states_after_event": len(groups),
                "particles_in_merged_duplicates": merged,
            })
    finally:
        dynamic_policy.actual_pre_state = original_actual_pre

    groups, merged = season_v3.merge_groups(groups)
    if sum(group.count for group in groups) != particles:
        raise ah.AlternateHistoryError(f"generic {phase} final particle conservation failed")
    return groups, {
        "season": str(season),
        "phase": phase,
        "events_processed": len(events),
        "actual_pre_event_states_reconstructed": len(actual_cache),
        "final_unique_states": len(groups),
        "max_unique_states": max_unique,
        "final_particles_merged": merged,
        "dynamic_policy_particle_counts": dict(policy_particles),
        "event_audit": audits,
    }


def replay_predraft_offseason(
    groups: List[SeasonParticleGroup],
    *,
    season: str,
    particles: int,
    seed: int,
) -> Tuple[List[SeasonParticleGroup], Dict[str, Any]]:
    adapter = FSFFLHistoricalAdapter()
    start = draft_start_ms(season)
    events = season_events(adapter, season, before_ms=start)
    groups, meta = _dynamic_replay_events(
        groups,
        events,
        season=season,
        particles=particles,
        seed=seed ^ (int(season) * 0x91),
        phase="predraft_offseason",
    )
    meta["draft_start_timestamp_ms"] = start
    return groups, meta


def propagate_completed_season(
    groups: List[SeasonParticleGroup],
    *,
    season: str,
    particles: int,
    seed: int,
    after_timestamp_ms: int,
) -> Tuple[List[SeasonParticleGroup], Dict[str, Any]]:
    """Replay any completed post-draft season through its following draft order."""
    if int(season) >= active_season():
        raise ah.AlternateHistoryError(f"{season} is not a completed season")
    if sum(group.count for group in groups) != particles:
        raise ah.AlternateHistoryError("generic completed-season input particle mismatch")

    adapter = FSFFLHistoricalAdapter()
    settings = season_v3.historical_settings(adapter, str(season))
    playoff_start = int(settings.get("playoff_week_start") or 15)
    playoff_teams = int(settings.get("playoff_teams") or 6)
    final_playoff_week = playoff_start + 2
    matchups = load(DATA / "stats" / "fsffl" / str(season) / "league_matchups_raw.json") or {}
    points = HistoricalPoints()
    weekly_points = points.season(str(season))
    positions = player_positions()
    slots = starter_slots(adapter.league)
    events = season_events(adapter, str(season), after_ms=after_timestamp_ms)

    actual_cache: Dict[str, ah.LeagueState] = {}
    original_actual_pre = dynamic_policy.actual_pre_state
    rng = random.Random(seed ^ (int(season) * 0xB1))
    policy_particles: Counter = Counter()
    event_audit: List[Dict[str, Any]] = []
    week_audit: List[Dict[str, Any]] = []
    next_week = 1
    regular_finalized = False
    max_unique = len(groups)

    def cached_actual(adapter_arg, season_arg, event_arg):
        tid = str(event_arg.get("transaction_id") or event_arg.get("created") or "")
        if tid not in actual_cache:
            actual_cache[tid] = original_actual_pre(adapter_arg, season_arg, event_arg)
        return actual_cache[tid]

    def score_through(target_exclusive: int) -> None:
        nonlocal groups, next_week, regular_finalized, max_unique
        while next_week < target_exclusive and next_week <= final_playoff_week:
            if next_week < playoff_start:
                audit = season_v3.score_regular_week(
                    groups,
                    season=str(season),
                    week=next_week,
                    matchup_rows=matchups.get(str(next_week), []),
                    slots=slots,
                    positions=positions,
                    weekly_points=weekly_points,
                )
            else:
                if not regular_finalized:
                    legacy_season.finalize_regular(groups, str(season), playoff_teams)
                    groups, _ = season_v3.merge_groups(groups)
                    regular_finalized = True
                audit = boundary_core.score_postseason_week(
                    groups,
                    season=str(season),
                    week=next_week,
                    matchup_rows=matchups.get(str(next_week), []),
                    slots=slots,
                    positions=positions,
                    weekly_points=weekly_points,
                )
            groups, merged = season_v3.merge_groups(groups)
            max_unique = max(max_unique, len(groups))
            week_audit.append({
                "week": next_week,
                "unique_states_after_scoring": len(groups),
                "particles_merged_after_scoring": merged,
                **audit,
            })
            next_week += 1

    try:
        dynamic_policy.actual_pre_state = cached_actual
        for event in events:
            _, week = dynamic_policy.event_season_week(event, str(season))
            if week is not None:
                score_through(int(week))
            tid = str(event.get("transaction_id") or "")
            next_groups: List[SeasonParticleGroup] = []
            per_policy: Counter = Counter()
            per_outcome: Counter = Counter()
            for group in groups:
                classification, outcomes = dynamic_policy.outcomes_for_branch(
                    adapter,
                    group.state,
                    event,
                    season=str(season),
                    positions=positions,
                    points=points,
                )
                policy = str(classification.get("policy") or "UNKNOWN")
                per_policy[policy] += group.count
                policy_particles[policy] += group.count
                counts = particle_v1.multinomial_counts(
                    group.count,
                    [float(row.get("probability") or 0.0) for row in outcomes],
                    rng,
                )
                for outcome, count in zip(outcomes, counts):
                    if count <= 0:
                        continue
                    per_outcome[str(outcome.get("outcome") or "unknown")] += count
                    state = season_v3.apply_preserving_ledger(group.state, event, outcome)
                    step = {
                        "transaction_id": tid,
                        "timestamp_ms": int(event.get("created") or 0),
                        "kind": "generic_completed_season_decision",
                        "season": str(season),
                        "policy": policy,
                        "outcome": outcome.get("outcome"),
                        "conditional_probability": round(float(outcome.get("probability") or 0.0), 8),
                        "particles": count,
                    }
                    traces = [
                        list(trace) + [step]
                        for trace in (group.traces or [[]])[:MAX_TRACES_PER_GROUP]
                    ]
                    next_groups.append(SeasonParticleGroup(count, state, traces))
            if sum(group.count for group in next_groups) != particles:
                raise ah.AlternateHistoryError(f"generic season conservation failed at {tid}")
            groups, merged = season_v3.merge_groups(next_groups)
            max_unique = max(max_unique, len(groups))
            event_audit.append({
                "transaction_id": tid,
                "timestamp_ms": int(event.get("created") or 0),
                "policy_particle_counts": dict(per_policy),
                "outcome_particle_counts": dict(per_outcome),
                "unique_states_after_event": len(groups),
                "particles_in_merged_duplicates": merged,
            })
    finally:
        dynamic_policy.actual_pre_state = original_actual_pre

    score_through(final_playoff_week + 1)
    if not regular_finalized:
        legacy_season.finalize_regular(groups, str(season), playoff_teams)
    for group in groups:
        ledger = copy.deepcopy(group.state.get(LEDGER_KEY) or {})
        row = ledger.setdefault(str(season), {})
        postseason = resolve_six_team_playoffs(
            row.get("standings") or [], row.get("weekly_scores") or {}, playoff_start
        )
        row["postseason"] = postseason
        row["full_following_draft_slots"] = full_draft_slots(
            row.get("nonplayoff_draft_slots") or {},
            postseason.get("playoff_draft_slots") or {},
        )
        group.state[LEDGER_KEY] = ledger
    groups, final_merged = season_v3.merge_groups(groups)
    if sum(group.count for group in groups) != particles:
        raise ah.AlternateHistoryError("generic completed-season final particle conservation failed")

    return groups, {
        "season": str(season),
        "following_draft_season": str(int(season) + 1),
        "events_processed": len(events),
        "final_particles": particles,
        "final_probability_mass": 1.0,
        "final_unique_states": len(groups),
        "max_unique_states": max_unique,
        "final_particles_merged": final_merged,
        "actual_pre_event_states_reconstructed": len(actual_cache),
        "dynamic_policy_particle_counts": dict(policy_particles),
        "historical_points_sources": points.sources,
        "week_audit": week_audit,
        "event_audit": event_audit,
    }


def replay_active_season_to_now(
    groups: List[SeasonParticleGroup],
    *,
    season: str,
    particles: int,
    seed: int,
    after_timestamp_ms: int,
) -> Tuple[List[SeasonParticleGroup], Dict[str, Any]]:
    """Replay fantasy transactions in the active season; never simulate NFL games."""
    if int(season) != active_season():
        raise ah.AlternateHistoryError(f"{season} is not the active season")
    adapter = FSFFLHistoricalAdapter()
    events = season_events(adapter, str(season), after_ms=after_timestamp_ms)

    # The active season is deliberately handled at the transaction layer only.
    # Current/future game outcomes are Simulator 1.0's responsibility.
    groups, meta = _dynamic_replay_events(
        groups,
        events,
        season=str(season),
        particles=particles,
        seed=seed ^ (int(season) * 0xC1),
        phase="active_season",
    )
    meta["nfl_games_simulated"] = False
    meta["latest_completed_event_timestamp_ms"] = max(
        [after_timestamp_ms] + [int(event.get("created") or 0) for event in events]
    )
    return groups, meta
