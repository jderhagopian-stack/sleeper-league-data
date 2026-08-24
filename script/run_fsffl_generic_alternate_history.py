#!/usr/bin/env python3
"""Generic FSFFL Alternate History orchestrator.

Runs an arbitrary historical fork from its season through the active season.
Calendar years are inputs, not hard-coded stages.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

import alternate_history_dynamic_policy as dynamic_policy
import alternate_history_engine as ah
import alternate_history_season_cycle as cycle
import run_fsffl_predraft_particles as predraft
import run_fsffl_multiseason_particle_replay_v3 as season_v3
from alternate_history_historical_state import cached_completed_season_pre_event_state
from run_fsffl_alternate_draft_candidates import raw_draft
from run_fsffl_downstream_dependencies import load
from run_fsffl_next_draft_handoff import replay_rookie_draft_groups

DEFAULT_PARTICLES = 5000
DEFAULT_SEED = 20260824
SeasonParticleGroup = season_v3.SeasonParticleGroup


def _active_actual_pre_state_factory(current: int, original):
    """Build a current-season reference-state resolver with no rookie leakage."""

    def resolve(adapter, season, event):
        if int(season) != int(current):
            return original(adapter, season, event)
        timestamp_ms = int(event.get("created") or 0)
        state = ah.reconstruct_state(adapter, timestamp_ms)
        if timestamp_ms < cycle.draft_start_ms(str(current)):
            entry = raw_draft(str(current))
            rookie_ids = {
                str(pick.get("player_id") or (pick.get("metadata") or {}).get("player_id") or "")
                for pick in (entry.get("picks") or [])
            }
            rookie_ids.discard("")
            for players in state.roster_players.values():
                players.difference_update(rookie_ids)
            for players in state.roster_taxi.values():
                players.difference_update(rookie_ids)
            for players in state.roster_reserve.values():
                players.difference_update(rookie_ids)
            state.reconstruction = dict(state.reconstruction)
            state.reconstruction.update({
                "active_season_predraft_rookies_removed": len(rookie_ids),
                "future_season_rookie_leakage_prevented": True,
            })
        return state

    return resolve


def run_generic(
    scenario_path: Path,
    *,
    particles: int = DEFAULT_PARTICLES,
    seed: int = DEFAULT_SEED,
    return_groups: bool = False,
) -> Any:
    if particles <= 0:
        raise ah.AlternateHistoryError("generic alternate-history particles must be positive")

    payload = load(scenario_path) or {}
    fork_season = int(payload.get("fork_season") or 0)
    if fork_season <= 0:
        raise ah.AlternateHistoryError("generic alternate-history scenario requires fork_season")
    current = cycle.active_season()
    if fork_season > current:
        raise ah.AlternateHistoryError("fork season cannot be after the active season")

    groups, boundary_meta = predraft.anchored_boundary_simulate(
        scenario_path,
        particles=particles,
        seed=seed,
    )
    scenario = boundary_meta["scenario"]
    phases: List[Dict[str, Any]] = [{
        "phase": "fork_season_boundary",
        "season": str(fork_season),
        "unique_states": len(groups),
        "events_processed": int(boundary_meta.get("events_processed_before_next_season") or 0),
    }]

    if fork_season == current:
        raise ah.AlternateHistoryError(
            "active-season fork orchestration is a separate live what-if command surface"
        )

    original_actual_pre = dynamic_policy.actual_pre_state

    def cached_completed_actual_pre(adapter, season, event):
        return cached_completed_season_pre_event_state(adapter, str(season), event)

    active_actual_pre = _active_actual_pre_state_factory(current, original_actual_pre)
    dynamic_policy.actual_pre_state = cached_completed_actual_pre

    try:
        for draft_year in range(fork_season + 1, current + 1):
            draft_season = str(draft_year)
            completed_season = str(draft_year - 1)
            use_active_reference = draft_year == current
            if use_active_reference:
                dynamic_policy.actual_pre_state = active_actual_pre
            try:
                groups, offseason_meta = cycle.replay_predraft_offseason(
                    groups,
                    season=draft_season,
                    particles=particles,
                    seed=seed,
                )
                phases.append({
                    "phase": "predraft_offseason",
                    "season": draft_season,
                    "events_processed": offseason_meta["events_processed"],
                    "unique_states": offseason_meta["final_unique_states"],
                })

                groups, draft_meta = replay_rookie_draft_groups(
                    groups,
                    completed_season=completed_season,
                    draft_season=draft_season,
                    particles=particles,
                    seed=seed,
                )
                phases.append({
                    "phase": "rookie_draft",
                    "season": draft_season,
                    "picks_simulated": draft_meta["draft_picks_simulated"],
                    "unique_states": draft_meta["final_unique_states"],
                })

                after_draft = cycle.draft_end_ms(draft_season)
                if draft_year < current:
                    groups, season_meta = cycle.propagate_completed_season(
                        groups,
                        season=draft_season,
                        particles=particles,
                        seed=seed,
                        after_timestamp_ms=after_draft,
                    )
                    phases.append({
                        "phase": "completed_season",
                        "season": draft_season,
                        "events_processed": season_meta["events_processed"],
                        "unique_states": season_meta["final_unique_states"],
                    })
                else:
                    groups, active_meta = cycle.replay_active_season_to_now(
                        groups,
                        season=draft_season,
                        particles=particles,
                        seed=seed,
                        after_timestamp_ms=after_draft,
                    )
                    phases.append({
                        "phase": "active_season_to_now",
                        "season": draft_season,
                        "events_processed": active_meta["events_processed"],
                        "unique_states": active_meta["final_unique_states"],
                    })
            finally:
                if use_active_reference:
                    dynamic_policy.actual_pre_state = cached_completed_actual_pre
    finally:
        dynamic_policy.actual_pre_state = original_actual_pre

    if sum(group.count for group in groups) != particles:
        raise ah.AlternateHistoryError("generic orchestrator lost particle mass")

    focus = str(scenario.focus_roster_id)
    roster_counts: Dict[str, int] = {}
    for group in groups:
        sig = "|".join(sorted(str(x) for x in ((group.state.get("roster_players") or {}).get(focus) or [])))
        roster_counts[sig] = roster_counts.get(sig, 0) + group.count

    report = {
        "model_version": "Fantasy-Alternate-History-1.0-generic-season-cycle",
        "scenario_id": scenario.scenario_id,
        "fork_season": str(fork_season),
        "active_season": str(current),
        "configuration": {"particles": particles, "seed": seed},
        "design_invariants": {
            "calendar_years_are_data_not_code": True,
            "completed_nfl_history_is_immutable": True,
            "active_season_nfl_games_simulated_here": False,
            "full_probability_mass_conserved": True,
            "archived_completed_season_root_anchor": True,
            "active_season_predraft_rookie_leakage_prevented": True,
            "predraft_offseason_is_explicit_phase": True,
            "branch_specific_rookie_drafts": True,
            "branch_specific_downstream_transactions": True,
            "current_gm3_numeric_values_used_for_historical_decisions": False,
            "completed_season_pre_event_states_incrementally_cached": True,
        },
        "summary": {
            "final_particles": particles,
            "final_probability_mass": 1.0,
            "final_unique_states": len(groups),
            "seasons_traversed": current - fork_season + 1,
            "rookie_drafts_simulated": current - fork_season,
        },
        "phase_audit": phases,
        "focus_present_day_roster_distribution": [
            {
                "player_ids": sig.split("|") if sig else [],
                "particles": count,
                "probability": round(count / particles, 8),
            }
            for sig, count in sorted(roster_counts.items(), key=lambda row: (-row[1], row[0]))[:50]
        ],
        "representative_present_day_states": [
            {
                "particles": group.count,
                "probability": round(group.count / particles, 8),
                "focus_roster_players": sorted((group.state.get("roster_players") or {}).get(focus, [])),
                "focus_future_picks": sorted(
                    key for key, rid in (group.state.get("pick_owners") or {}).items()
                    if str(rid) == focus and not str(key).startswith(f"pick:{current}:")
                ),
                "trace": (group.traces or [[]])[0],
            }
            for group in sorted(groups, key=lambda value: value.count, reverse=True)[:20]
        ],
    }
    out = ah.write_isolated_json(
        f"results/{scenario.scenario_id}/generic_season_cycle_1_0.json",
        report,
    )
    print(out)
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    if return_groups:
        return out, groups, report
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Run arbitrary-year FSFFL alternate history")
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--particles", type=int, default=DEFAULT_PARTICLES)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    run_generic(args.scenario, particles=args.particles, seed=args.seed)


if __name__ == "__main__":
    main()
