#!/usr/bin/env python3
"""FSFFL Alternate History 0.8d: third-season (2025) particle propagation.

Consumes the complete weighted post-2025-rookie-draft state and propagates the
2025 fantasy season using the same branch-state-aware historical decision
policy used for 2024. Real NFL/fantasy outcomes remain immutable; only fantasy
ownership, lineup choices, transactions, standings, and downstream league
consequences may differ.
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List

import alternate_history_dynamic_policy as dynamic_policy
import alternate_history_engine as ah
import run_fsffl_multiseason_particle_replay as particle_v1
import run_fsffl_multiseason_particle_replay_v3 as season_v3
import run_fsffl_season_boundary_particles as boundary_core
import run_fsffl_second_season_particles as season_runner
from alternate_history_postseason import full_draft_slots, resolve_six_team_playoffs
from run_fsffl_alternate_draft_candidates import raw_draft
from run_fsffl_alternate_history import FSFFLHistoricalAdapter
from run_fsffl_counterfactual_replay import player_positions, starter_slots
from run_fsffl_downstream_dependencies import load
from run_fsffl_historical_usage_policy import HistoricalPoints
from run_fsffl_next_draft_handoff import replay_rookie_draft_groups

DATA = Path("data")
DEFAULT_PARTICLES = 5000
DEFAULT_SEED = 20260824
MAX_TRACES_PER_GROUP = 3
LEDGER_KEY = season_v3.LEDGER_KEY
SeasonParticleGroup = season_v3.SeasonParticleGroup


def draft_boundary_timestamp(draft_season: str) -> int:
    """Return the latest contemporaneous timestamp exposed by the raw draft."""
    draft = (raw_draft(draft_season).get("draft") or {})
    values = []
    for key in ("last_picked", "start_time", "created"):
        value = draft.get(key)
        try:
            value = int(value or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 10**11:
            values.append(value)
    if not values:
        raise ah.AlternateHistoryError(f"0.8d missing usable {draft_season} draft timestamp")
    return max(values)


def propagate_2025(
    groups: List[SeasonParticleGroup],
    *,
    particles: int,
    seed: int,
    after_timestamp_ms: int,
) -> tuple[List[SeasonParticleGroup], Dict[str, Any]]:
    season = "2025"
    if sum(group.count for group in groups) != particles:
        raise ah.AlternateHistoryError("0.8d input particle mass does not match configuration")

    adapter = FSFFLHistoricalAdapter()
    settings = season_v3.historical_settings(adapter, season)
    playoff_start = int(settings.get("playoff_week_start") or 15)
    playoff_teams = int(settings.get("playoff_teams") or 6)
    final_playoff_week = playoff_start + 2
    matchups = load(DATA / "stats" / "fsffl" / season / "league_matchups_raw.json")
    points = HistoricalPoints()
    weekly_points = points.season(season)
    positions = player_positions()
    slots = starter_slots(adapter.league)
    events = season_runner.target_season_events(adapter, season, after_timestamp_ms)

    actual_state_cache: Dict[str, ah.LeagueState] = {}
    original_actual_pre_state = dynamic_policy.actual_pre_state

    def cached_actual_pre_state(adapter_arg, season_arg, event_arg):
        tid = str(event_arg.get("transaction_id") or event_arg.get("created") or "")
        if tid not in actual_state_cache:
            actual_state_cache[tid] = original_actual_pre_state(adapter_arg, season_arg, event_arg)
        return actual_state_cache[tid]

    rng = random.Random(seed ^ 0x2025D)
    event_audit = []
    week_audit = []
    policy_particle_counts: Counter = Counter()
    events_by_dynamic_policy: Counter = Counter()
    next_score_week = 1
    regular_finalized = False
    max_unique_states = len(groups)

    def score_through(target_week_exclusive: int) -> None:
        nonlocal groups, next_score_week, regular_finalized, max_unique_states
        while next_score_week < target_week_exclusive and next_score_week <= final_playoff_week:
            if next_score_week < playoff_start:
                audit = season_v3.score_regular_week(
                    groups,
                    season=season,
                    week=next_score_week,
                    matchup_rows=matchups.get(str(next_score_week), []),
                    slots=slots,
                    positions=positions,
                    weekly_points=weekly_points,
                )
            else:
                if not regular_finalized:
                    season_runner.finalize_regular(groups, season, playoff_teams)
                    groups, _ = season_v3.merge_groups(groups)
                    regular_finalized = True
                audit = boundary_core.score_postseason_week(
                    groups,
                    season=season,
                    week=next_score_week,
                    matchup_rows=matchups.get(str(next_score_week), []),
                    slots=slots,
                    positions=positions,
                    weekly_points=weekly_points,
                )
            groups, merged = season_v3.merge_groups(groups)
            max_unique_states = max(max_unique_states, len(groups))
            week_audit.append({
                "week": next_score_week,
                "unique_states_after_scoring": len(groups),
                "particles_merged_after_scoring": merged,
                **audit,
            })
            next_score_week += 1

    try:
        dynamic_policy.actual_pre_state = cached_actual_pre_state
        for event in events:
            _, event_week = dynamic_policy.event_season_week(event, season)
            if event_week is not None:
                score_through(int(event_week))

            tid = str(event.get("transaction_id") or "")
            next_groups: List[SeasonParticleGroup] = []
            event_policy_particles: Counter = Counter()
            event_outcome_particles: Counter = Counter()

            for group in groups:
                classification, outcomes = dynamic_policy.outcomes_for_branch(
                    adapter,
                    group.state,
                    event,
                    season=season,
                    positions=positions,
                    points=points,
                )
                policy = str(classification.get("policy") or "UNKNOWN")
                event_policy_particles[policy] += group.count
                policy_particle_counts[policy] += group.count
                counts = particle_v1.multinomial_counts(
                    group.count,
                    [float(row.get("probability") or 0.0) for row in outcomes],
                    rng,
                )
                if sum(counts) != group.count:
                    raise ah.AlternateHistoryError(f"0.8d particle conservation failed at {tid}")

                for outcome, count in zip(outcomes, counts):
                    if count <= 0:
                        continue
                    event_outcome_particles[str(outcome.get("outcome") or "unknown")] += count
                    state = season_v3.apply_preserving_ledger(group.state, event, outcome)
                    step = {
                        "transaction_id": tid,
                        "timestamp_ms": int(event.get("created") or 0),
                        "kind": "dynamic_third_season_decision",
                        "season": season,
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
                    f"0.8d global particle conservation failed at {tid}"
                )
            groups, merged = season_v3.merge_groups(next_groups)
            max_unique_states = max(max_unique_states, len(groups))
            for policy in event_policy_particles:
                events_by_dynamic_policy[policy] += 1
            event_audit.append({
                "transaction_id": tid,
                "timestamp_ms": int(event.get("created") or 0),
                "type": str(event.get("type") or "unknown"),
                "policy_particle_counts": dict(event_policy_particles),
                "outcome_particle_counts": dict(event_outcome_particles),
                "unique_states_after_event": len(groups),
                "particles_in_merged_duplicates": merged,
            })
    finally:
        dynamic_policy.actual_pre_state = original_actual_pre_state

    score_through(final_playoff_week + 1)
    if not regular_finalized:
        season_runner.finalize_regular(groups, season, playoff_teams)
    season_runner.finalize_postseason(groups, season, playoff_start)
    groups, final_merged = season_v3.merge_groups(groups)

    final_particles = sum(group.count for group in groups)
    if final_particles != particles:
        raise ah.AlternateHistoryError(
            f"0.8d final particle conservation failed: {final_particles} != {particles}"
        )

    return groups, {
        "season": season,
        "following_draft_season": "2026",
        "postdraft_events_processed": len(events),
        "actual_pre_event_states_reconstructed": len(actual_state_cache),
        "final_particles": final_particles,
        "final_probability_mass": 1.0,
        "final_unique_states": len(groups),
        "max_unique_states": max_unique_states,
        "final_particles_merged": final_merged,
        "dynamic_policy_event_counts": dict(events_by_dynamic_policy),
        "dynamic_policy_particle_counts": dict(policy_particle_counts),
        "historical_points_sources": points.sources,
        "week_audit": week_audit,
        "event_audit": event_audit,
    }


def run(
    scenario_path: Path,
    *,
    particles: int = DEFAULT_PARTICLES,
    seed: int = DEFAULT_SEED,
) -> Path:
    if particles <= 0:
        raise ah.AlternateHistoryError("0.8d particles must be positive")

    _, pre_draft_groups, handoff = season_runner.run(
        scenario_path, particles=particles, seed=seed, return_handoff=True
    )
    scenario = handoff["scenario"]
    completed_season = str(handoff["completed_season"])
    draft_season = str(handoff["next_draft_season"])
    groups, draft_meta = replay_rookie_draft_groups(
        pre_draft_groups,
        completed_season=completed_season,
        draft_season=draft_season,
        particles=particles,
        seed=seed,
    )
    after_timestamp_ms = draft_boundary_timestamp(draft_season)
    groups, season_meta = propagate_2025(
        groups,
        particles=particles,
        seed=seed,
        after_timestamp_ms=after_timestamp_ms,
    )

    focus = str(scenario.focus_roster_id)
    slot_counts = defaultdict(int)
    champion_counts = defaultdict(int)
    roster_counts = defaultdict(int)
    scoring_gap_particles = 0
    for group in groups:
        row = ((group.state.get(LEDGER_KEY) or {}).get("2025") or {})
        slot = (row.get("full_following_draft_slots") or {}).get(focus)
        if slot is not None:
            slot_counts[int(slot)] += group.count
        champion = str((((row.get("postseason") or {}).get("championship") or {}).get("winner")) or "")
        if champion:
            champion_counts[champion] += group.count
        if row.get("data_gaps"):
            scoring_gap_particles += group.count
        players = tuple(sorted(str(x) for x in ((group.state.get("roster_players") or {}).get(focus) or [])))
        roster_counts["|".join(players)] += group.count

    summary = {
        **{k: v for k, v in season_meta.items() if k not in ("week_audit", "event_audit", "historical_points_sources", "dynamic_policy_particle_counts")},
        "probability_with_scoring_data_gap": round(scoring_gap_particles / particles, 8),
        "input_postdraft_unique_states": draft_meta["final_unique_states"],
    }
    report = {
        "model_version": "Fantasy-Alternate-History-0.8d-third-season-dynamic-particles",
        "scenario_id": scenario.scenario_id,
        "season": "2025",
        "following_draft_season": "2026",
        "configuration": {"particles": particles, "seed": seed},
        "design_invariants": {
            "completed_nfl_fantasy_points_are_immutable": True,
            "current_week_points_never_choose_current_week_lineup": True,
            "postdraft_events_reclassified_from_branch_state": True,
            "actual_historical_pre_event_state_cached_once_per_transaction": True,
            "current_gm3_numeric_values_used": False,
            "future_nfl_outcomes_used_for_historical_decisions": False,
            "particle_probability_mass_pruned": False,
            "season_feedback_part_of_state_identity": True,
            "stateful_2025_rookie_draft_is_input": True,
        },
        "summary": summary,
        "dynamic_policy_particle_counts": season_meta["dynamic_policy_particle_counts"],
        "historical_points_sources": season_meta["historical_points_sources"],
        "week_audit": season_meta["week_audit"],
        "event_audit": season_meta["event_audit"],
        "focus_2026_draft_slot_distribution": [
            {"slot": slot, "particles": count, "probability": round(count / particles, 8)}
            for slot, count in sorted(slot_counts.items())
        ],
        "champion_distribution": [
            {"roster_id": rid, "particles": count, "probability": round(count / particles, 8)}
            for rid, count in sorted(champion_counts.items(), key=lambda row: (-row[1], row[0]))
        ],
        "focus_end_2025_roster_distribution": [
            {"player_ids": sig.split("|") if sig else [], "particles": count, "probability": round(count / particles, 8)}
            for sig, count in sorted(roster_counts.items(), key=lambda row: (-row[1], row[0]))[:50]
        ],
        "representative_end_2025_states": [
            {
                "particles": group.count,
                "probability": round(group.count / particles, 8),
                "focus_roster_players": sorted((group.state.get("roster_players") or {}).get(focus, [])),
                "full_following_draft_slots": (((group.state.get(LEDGER_KEY) or {}).get("2025") or {}).get("full_following_draft_slots") or {}),
                "trace": (group.traces or [[]])[0],
            }
            for group in sorted(groups, key=lambda value: value.count, reverse=True)[:20]
        ],
    }
    out = ah.write_isolated_json(
        f"results/{scenario.scenario_id}/third_season_particles_0_8d.json", report
    )
    print(out)
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Run 0.8d dynamic 2025 particle replay")
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--particles", type=int, default=DEFAULT_PARTICLES)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    run(args.scenario, particles=args.particles, seed=args.seed)


if __name__ == "__main__":
    main()
