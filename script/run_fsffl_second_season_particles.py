#!/usr/bin/env python3
"""FSFFL Alternate History 0.8b: second-season dynamic particle propagation.

Starts from the state-aware alternate rookie draft and propagates the entire
following fantasy season using branch-state-aware decision policy. The original
fork is no longer the only relevance signal: each 2024 event is reclassified
against that particle group's actual divergent state at the event timestamp.

The stage ends at the 2024 season boundary with a complete 2025 rookie-draft
slot map for every retained particle group.
"""

from __future__ import annotations

import argparse
import copy
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

import alternate_history_engine as ah
import alternate_history_dynamic_policy as dynamic_policy
from alternate_history_postseason import full_draft_slots, resolve_six_team_playoffs
from alternate_history_postdraft_state import simulate_postdraft_groups
import run_fsffl_multiseason_branch_replay as branch_v1
import run_fsffl_multiseason_particle_replay as particle_v1
import run_fsffl_multiseason_particle_replay_v3 as season_v3
import run_fsffl_season_boundary_particles as boundary_core
from run_fsffl_alternate_history import FSFFLHistoricalAdapter
from run_fsffl_counterfactual_replay import player_positions, starter_slots
from run_fsffl_downstream_dependencies import load
from run_fsffl_historical_usage_policy import HistoricalPoints

DATA = Path("data")
DEFAULT_PARTICLES = 5000
DEFAULT_SEED = 20260824
MAX_TRACES_PER_GROUP = 3
SeasonParticleGroup = season_v3.SeasonParticleGroup
LEDGER_KEY = season_v3.LEDGER_KEY


def target_season_events(
    adapter: FSFFLHistoricalAdapter,
    season: str,
    after_timestamp_ms: int,
) -> List[Dict[str, Any]]:
    rows = []
    for event in adapter.completed_events():
        source = event.get("source_season") or (event.get("metadata") or {}).get("source_season")
        if str(source or "") != str(season):
            continue
        if int(event.get("created") or 0) < int(after_timestamp_ms):
            continue
        rows.append(event)
    return sorted(rows, key=lambda row: int(row.get("created") or 0))


def finalize_regular(groups: List[SeasonParticleGroup], season: str, playoff_teams: int) -> None:
    for group in groups:
        ledger = copy.deepcopy(group.state.get(LEDGER_KEY) or {})
        season_row = ledger.setdefault(str(season), {})
        season_v3.finalize_regular_season(season_row, playoff_teams)
        group.state[LEDGER_KEY] = ledger


def finalize_postseason(groups: List[SeasonParticleGroup], season: str, playoff_start: int) -> None:
    for group in groups:
        ledger = copy.deepcopy(group.state.get(LEDGER_KEY) or {})
        season_row = ledger.setdefault(str(season), {})
        postseason = resolve_six_team_playoffs(
            season_row.get("standings") or [],
            season_row.get("weekly_scores") or {},
            playoff_start,
        )
        season_row["postseason"] = postseason
        season_row["full_following_draft_slots"] = full_draft_slots(
            season_row.get("nonplayoff_draft_slots") or {},
            postseason.get("playoff_draft_slots") or {},
        )
        group.state[LEDGER_KEY] = ledger


def run(
    scenario_path: Path,
    *,
    particles: int = DEFAULT_PARTICLES,
    seed: int = DEFAULT_SEED,
) -> Path:
    if particles <= 0:
        raise ah.AlternateHistoryError("0.8b particles must be positive")

    groups, postdraft_meta = simulate_postdraft_groups(
        scenario_path,
        particles=particles,
        seed=seed,
    )
    scenario = postdraft_meta["scenario"]
    season = str(postdraft_meta["draft_season"])
    draft_start_ms = int(postdraft_meta.get("draft_end_state_timestamp_ms") or 0)

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
    events = target_season_events(adapter, season, draft_start_ms)

    # Actual historical pre-event state does not depend on the alternate branch.
    # Cache once per transaction instead of reconstructing it for every group.
    actual_state_cache: Dict[str, ah.LeagueState] = {}
    original_actual_pre_state = dynamic_policy.actual_pre_state

    def cached_actual_pre_state(
        adapter_arg: FSFFLHistoricalAdapter,
        season_arg: str,
        event_arg: Dict[str, Any],
    ) -> ah.LeagueState:
        tid = str(event_arg.get("transaction_id") or event_arg.get("created") or "")
        if tid not in actual_state_cache:
            actual_state_cache[tid] = original_actual_pre_state(
                adapter_arg, season_arg, event_arg
            )
        return actual_state_cache[tid]

    rng = random.Random(seed ^ 0x2024B)
    event_audit: List[Dict[str, Any]] = []
    week_audit: List[Dict[str, Any]] = []
    next_score_week = 1
    regular_finalized = False
    max_unique_states = len(groups)
    policy_particle_counts: Counter = Counter()
    events_by_dynamic_policy: Counter = Counter()

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
                    finalize_regular(groups, season, playoff_teams)
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
                    raise ah.AlternateHistoryError(
                        f"0.8b particle conservation failed at {tid}"
                    )

                for outcome, count in zip(outcomes, counts):
                    if count <= 0:
                        continue
                    event_outcome_particles[str(outcome.get("outcome") or "unknown")] += count
                    state = season_v3.apply_preserving_ledger(
                        group.state, event, outcome
                    )
                    step = {
                        "transaction_id": tid,
                        "timestamp_ms": int(event.get("created") or 0),
                        "kind": "dynamic_second_season_decision",
                        "policy": policy,
                        "outcome": outcome.get("outcome"),
                        "conditional_probability": round(
                            float(outcome.get("probability") or 0.0), 8
                        ),
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
                    f"0.8b global particle conservation failed at {tid}"
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
        finalize_regular(groups, season, playoff_teams)
        regular_finalized = True
    finalize_postseason(groups, season, playoff_start)
    groups, final_merged = season_v3.merge_groups(groups)

    final_particles = sum(group.count for group in groups)
    if final_particles != particles:
        raise ah.AlternateHistoryError(
            f"0.8b final particle conservation failed: {final_particles} != {particles}"
        )

    focus = str(scenario.focus_roster_id)
    focus_slot_counts: Dict[int, int] = defaultdict(int)
    champion_counts: Dict[str, int] = defaultdict(int)
    focus_seed_counts: Dict[int, int] = defaultdict(int)
    scoring_gap_particles = 0
    roster_signature_counts: Dict[str, int] = defaultdict(int)

    for group in groups:
        season_row = ((group.state.get(LEDGER_KEY) or {}).get(season) or {})
        slots_map = season_row.get("full_following_draft_slots") or {}
        if focus in slots_map:
            focus_slot_counts[int(slots_map[focus])] += group.count
        standings = season_row.get("standings") or []
        for row in standings:
            if str(row.get("roster_id")) == focus:
                focus_seed_counts[int(row.get("seed"))] += group.count
                break
        champion = str(
            (((season_row.get("postseason") or {}).get("championship") or {}).get("winner"))
            or ""
        )
        if champion:
            champion_counts[champion] += group.count
        if season_row.get("data_gaps"):
            scoring_gap_particles += group.count
        focus_players = tuple(
            sorted(str(x) for x in ((group.state.get("roster_players") or {}).get(focus) or []))
        )
        roster_signature_counts["|".join(focus_players)] += group.count

    report = {
        "model_version": "Fantasy-Alternate-History-0.8b-second-season-dynamic-particles",
        "scenario_id": scenario.scenario_id,
        "season": season,
        "following_draft_season": str(int(season) + 1),
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
        },
        "summary": {
            "postdraft_events_processed": len(events),
            "actual_pre_event_states_reconstructed": len(actual_state_cache),
            "final_particles": final_particles,
            "final_probability_mass": 1.0,
            "final_unique_second_season_states": len(groups),
            "max_unique_states": max_unique_states,
            "final_particles_merged": final_merged,
            "probability_with_scoring_data_gap": round(scoring_gap_particles / particles, 8),
            "dynamic_policy_event_counts": dict(events_by_dynamic_policy),
        },
        "dynamic_policy_particle_counts": dict(policy_particle_counts),
        "focus_seed_distribution": [
            {"seed": value, "particles": count, "probability": round(count / particles, 8)}
            for value, count in sorted(focus_seed_counts.items())
        ],
        "focus_2025_draft_slot_distribution": [
            {"slot": value, "particles": count, "probability": round(count / particles, 8)}
            for value, count in sorted(focus_slot_counts.items())
        ],
        "champion_distribution": [
            {"roster_id": rid, "particles": count, "probability": round(count / particles, 8)}
            for rid, count in sorted(champion_counts.items(), key=lambda row: (-row[1], row[0]))
        ],
        "focus_roster_state_distribution": [
            {"player_ids": sig.split("|") if sig else [], "particles": count, "probability": round(count / particles, 8)}
            for sig, count in sorted(roster_signature_counts.items(), key=lambda row: (-row[1], row[0]))[:50]
        ],
        "historical_points_sources": points.sources,
        "week_audit": week_audit,
        "event_audit": event_audit,
        "representative_second_season_states": [
            {
                "particles": group.count,
                "probability": round(group.count / particles, 8),
                "focus_roster_players": sorted(
                    (group.state.get("roster_players") or {}).get(focus, [])
                ),
                "standings": (((group.state.get(LEDGER_KEY) or {}).get(season) or {}).get("standings") or []),
                "full_following_draft_slots": (((group.state.get(LEDGER_KEY) or {}).get(season) or {}).get("full_following_draft_slots") or {}),
                "trace": (group.traces or [[]])[0],
            }
            for group in sorted(groups, key=lambda value: value.count, reverse=True)[:20]
        ],
    }
    out = ah.write_isolated_json(
        f"results/{scenario.scenario_id}/second_season_particles_0_8b.json",
        report,
    )
    print(out)
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Run 0.8b dynamic second-season particle replay")
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--particles", type=int, default=DEFAULT_PARTICLES)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    run(args.scenario, particles=args.particles, seed=args.seed)


if __name__ == "__main__":
    main()
