#!/usr/bin/env python3
"""FSFFL Alternate History 0.9a: weighted present-day particle handoff.

Completes the historical counterfactual chain through the active 2026 league
state. The stage consumes end-2025 weighted states, replays the branch-specific
2026 rookie draft, then replays completed 2026 fantasy transactions only. It
does NOT simulate 2026 NFL games; current/future football belongs to Simulator
1.0 after this boundary.
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

import alternate_history_dynamic_policy as dynamic_policy
import alternate_history_engine as ah
import run_fsffl_multiseason_particle_replay as particle_v1
import run_fsffl_multiseason_particle_replay_v3 as season_v3
import run_fsffl_second_season_particles as season_runner
import run_fsffl_third_season_particles as third_season
from run_fsffl_alternate_history import FSFFLHistoricalAdapter
from run_fsffl_counterfactual_replay import player_positions
from run_fsffl_historical_usage_policy import HistoricalPoints
from run_fsffl_next_draft_handoff import replay_rookie_draft_groups

DEFAULT_PARTICLES = 5000
DEFAULT_SEED = 20260824
MAX_TRACES_PER_GROUP = 3
SeasonParticleGroup = season_v3.SeasonParticleGroup


def build_end_2025_groups(scenario_path: Path, *, particles: int, seed: int):
    """Reproduce the validated chain through the complete 2025 season."""
    _, pre_2025_draft_groups, handoff = season_runner.run(
        scenario_path, particles=particles, seed=seed, return_handoff=True
    )
    scenario = handoff["scenario"]
    completed_season = str(handoff["completed_season"])
    draft_season = str(handoff["next_draft_season"])
    groups, _ = replay_rookie_draft_groups(
        pre_2025_draft_groups,
        completed_season=completed_season,
        draft_season=draft_season,
        particles=particles,
        seed=seed,
    )
    after_timestamp_ms = third_season.draft_boundary_timestamp(draft_season)
    groups, season_meta = third_season.propagate_2025(
        groups,
        particles=particles,
        seed=seed,
        after_timestamp_ms=after_timestamp_ms,
    )
    return scenario, groups, season_meta


def current_season_events(adapter: FSFFLHistoricalAdapter, after_timestamp_ms: int) -> List[Dict[str, Any]]:
    rows = []
    for event in adapter.completed_events():
        created = int(event.get("created") or 0)
        season, _ = dynamic_policy.event_season_week(event, "2026")
        if season == "2026" and created > int(after_timestamp_ms):
            rows.append(event)
    rows.sort(key=lambda event: (int(event.get("created") or 0), str(event.get("transaction_id") or "")))
    return rows


def replay_current_transactions(
    groups: List[SeasonParticleGroup],
    *,
    particles: int,
    seed: int,
    after_timestamp_ms: int,
) -> Tuple[List[SeasonParticleGroup], Dict[str, Any]]:
    adapter = FSFFLHistoricalAdapter()
    positions = player_positions()
    points = HistoricalPoints()
    events = current_season_events(adapter, after_timestamp_ms)
    rng = random.Random(seed ^ 0x2026E)

    actual_state_cache: Dict[str, ah.LeagueState] = {}
    original_actual_pre_state = dynamic_policy.actual_pre_state

    def current_actual_pre_state(adapter_arg, season_arg, event_arg):
        tid = str(event_arg.get("transaction_id") or event_arg.get("created") or "")
        if tid not in actual_state_cache:
            # Active-season historical decisions occur after the 2026 rookie draft,
            # so reverse-reconstructing from the canonical current roster is safe:
            # drafted rookies legitimately already exist at every timestamp here.
            actual_state_cache[tid] = ah.reconstruct_state(
                adapter_arg, int(event_arg.get("created") or 0)
            )
        return actual_state_cache[tid]

    event_audit = []
    policy_particle_counts: Counter = Counter()
    max_unique_states = len(groups)

    try:
        dynamic_policy.actual_pre_state = current_actual_pre_state
        for event in events:
            tid = str(event.get("transaction_id") or "")
            next_groups: List[SeasonParticleGroup] = []
            policy_counts: Counter = Counter()
            outcome_counts: Counter = Counter()

            for group in groups:
                classification, outcomes = dynamic_policy.outcomes_for_branch(
                    adapter,
                    group.state,
                    event,
                    season="2026",
                    positions=positions,
                    points=points,
                )
                policy = str(classification.get("policy") or "UNKNOWN")
                policy_counts[policy] += group.count
                policy_particle_counts[policy] += group.count
                counts = particle_v1.multinomial_counts(
                    group.count,
                    [float(row.get("probability") or 0.0) for row in outcomes],
                    rng,
                )
                if sum(counts) != group.count:
                    raise ah.AlternateHistoryError(f"0.9a particle conservation failed at {tid}")

                for outcome, count in zip(outcomes, counts):
                    if count <= 0:
                        continue
                    outcome_counts[str(outcome.get("outcome") or "unknown")] += count
                    state = season_v3.apply_preserving_ledger(group.state, event, outcome)
                    step = {
                        "transaction_id": tid,
                        "timestamp_ms": int(event.get("created") or 0),
                        "kind": "dynamic_current_season_decision",
                        "season": "2026",
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
                raise ah.AlternateHistoryError(f"0.9a global conservation failed at {tid}")
            groups, merged = season_v3.merge_groups(next_groups)
            max_unique_states = max(max_unique_states, len(groups))
            event_audit.append({
                "transaction_id": tid,
                "timestamp_ms": int(event.get("created") or 0),
                "type": str(event.get("type") or "unknown"),
                "policy_particle_counts": dict(policy_counts),
                "outcome_particle_counts": dict(outcome_counts),
                "unique_states_after_event": len(groups),
                "particles_in_merged_duplicates": merged,
            })
    finally:
        dynamic_policy.actual_pre_state = original_actual_pre_state

    groups, final_merged = season_v3.merge_groups(groups)
    if sum(group.count for group in groups) != particles:
        raise ah.AlternateHistoryError("0.9a final particle conservation failed")

    latest_timestamp = max([after_timestamp_ms] + [int(event.get("created") or 0) for event in events])
    return groups, {
        "season": "2026",
        "events_processed": len(events),
        "latest_completed_event_timestamp_ms": latest_timestamp,
        "actual_pre_event_states_reconstructed": len(actual_state_cache),
        "final_particles": particles,
        "final_probability_mass": 1.0,
        "final_unique_states": len(groups),
        "max_unique_states": max_unique_states,
        "final_particles_merged": final_merged,
        "dynamic_policy_particle_counts": dict(policy_particle_counts),
        "event_audit": event_audit,
    }


def aggregate_present_day(groups: List[SeasonParticleGroup], *, focus: str, particles: int) -> Dict[str, Any]:
    player_owner_counts: Dict[str, Counter] = defaultdict(Counter)
    pick_owner_counts: Dict[str, Counter] = defaultdict(Counter)
    focus_rosters: Counter = Counter()
    focus_picks: Counter = Counter()

    for group in groups:
        for rid, players in (group.state.get("roster_players") or {}).items():
            for pid in players or []:
                player_owner_counts[str(pid)][str(rid)] += group.count
        for key, rid in (group.state.get("pick_owners") or {}).items():
            pick_owner_counts[str(key)][str(rid)] += group.count
        roster_sig = "|".join(sorted(str(x) for x in ((group.state.get("roster_players") or {}).get(focus) or [])))
        focus_rosters[roster_sig] += group.count
        fp = tuple(sorted(
            str(key) for key, rid in (group.state.get("pick_owners") or {}).items()
            if str(rid) == focus and not str(key).startswith("pick:2026:")
        ))
        focus_picks["|".join(fp)] += group.count

    player_rows = []
    for pid, owners in player_owner_counts.items():
        for rid, count in owners.items():
            player_rows.append({
                "player_id": pid,
                "roster_id": rid,
                "particles": count,
                "probability": round(count / particles, 8),
            })
    player_rows.sort(key=lambda row: (-row["probability"], row["player_id"], row["roster_id"]))

    pick_rows = []
    for key, owners in pick_owner_counts.items():
        for rid, count in owners.items():
            pick_rows.append({
                "pick_key": key,
                "roster_id": rid,
                "particles": count,
                "probability": round(count / particles, 8),
            })
    pick_rows.sort(key=lambda row: (row["pick_key"], -row["probability"], row["roster_id"]))

    return {
        "player_ownership_distribution": player_rows,
        "pick_ownership_distribution": pick_rows,
        "focus_roster_distribution": [
            {"player_ids": sig.split("|") if sig else [], "particles": count, "probability": round(count / particles, 8)}
            for sig, count in focus_rosters.most_common(50)
        ],
        "focus_future_pick_distribution": [
            {"pick_keys": sig.split("|") if sig else [], "particles": count, "probability": round(count / particles, 8)}
            for sig, count in focus_picks.most_common(50)
        ],
    }


def run(scenario_path: Path, *, particles: int = DEFAULT_PARTICLES, seed: int = DEFAULT_SEED) -> Path:
    if particles <= 0:
        raise ah.AlternateHistoryError("0.9a particles must be positive")

    scenario, end_2025_groups, season_meta = build_end_2025_groups(
        scenario_path, particles=particles, seed=seed
    )
    groups, draft_meta = replay_rookie_draft_groups(
        end_2025_groups,
        completed_season="2025",
        draft_season="2026",
        particles=particles,
        seed=seed,
    )
    after_timestamp_ms = third_season.draft_boundary_timestamp("2026")
    groups, current_meta = replay_current_transactions(
        groups,
        particles=particles,
        seed=seed,
        after_timestamp_ms=after_timestamp_ms,
    )

    focus = str(scenario.focus_roster_id)
    aggregate = aggregate_present_day(groups, focus=focus, particles=particles)
    report = {
        "model_version": "Fantasy-Alternate-History-0.9a-present-day-particles",
        "scenario_id": scenario.scenario_id,
        "configuration": {"particles": particles, "seed": seed},
        "design_invariants": {
            "completed_nfl_history_is_immutable": True,
            "2026_nfl_games_simulated_here": False,
            "current_gm3_numeric_values_used_for_historical_decisions": False,
            "future_nfl_outcomes_used_for_historical_decisions": False,
            "particle_probability_mass_pruned": False,
            "branch_specific_2026_draft_used": True,
            "branch_specific_2026_transactions_used": True,
            "simulator_1_0_boundary_reached": True,
        },
        "summary": {
            "final_particles": particles,
            "final_probability_mass": 1.0,
            "final_unique_states": current_meta["final_unique_states"],
            "2026_draft_picks_simulated": draft_meta["draft_picks_simulated"],
            "2026_transactions_processed": current_meta["events_processed"],
            "latest_completed_event_timestamp_ms": current_meta["latest_completed_event_timestamp_ms"],
            "2025_end_unique_states": season_meta["final_unique_states"],
        },
        "current_season_meta": current_meta,
        **aggregate,
        "representative_present_day_states": [
            {
                "particles": group.count,
                "probability": round(group.count / particles, 8),
                "focus_roster_players": sorted((group.state.get("roster_players") or {}).get(focus, [])),
                "focus_future_picks": sorted(
                    key for key, rid in (group.state.get("pick_owners") or {}).items()
                    if str(rid) == focus and not str(key).startswith("pick:2026:")
                ),
                "trace": (group.traces or [[]])[0],
            }
            for group in sorted(groups, key=lambda value: value.count, reverse=True)[:20]
        ],
    }

    out = ah.write_isolated_json(
        f"results/{scenario.scenario_id}/present_day_particles_0_9a.json", report
    )
    print(out)
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Run weighted alternate history through present-day 2026 state")
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--particles", type=int, default=DEFAULT_PARTICLES)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    run(args.scenario, particles=args.particles, seed=args.seed)


if __name__ == "__main__":
    main()
