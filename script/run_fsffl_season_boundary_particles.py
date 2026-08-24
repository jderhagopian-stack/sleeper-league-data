#!/usr/bin/env python3
"""FSFFL Alternate History 0.7e: weighted season-boundary particle states.

This is the production causal boundary between one completed fantasy season and
the following rookie draft. It deliberately STOPS before next-season fantasy
transactions so an alternate rookie draft can be inserted before downstream
2024 decisions are evaluated.

Outputs per-particle-group consequences through the fork season:
- branch-specific regular-season lineups, scores, standings and playoff field;
- branch-specific regular-season Max PF;
- validated non-playoff slots 1.01-1.06;
- branch-specific postseason lineups/scores and six-team bracket finish;
- playoff slots 1.07-1.12;
- complete following rookie-draft slot map.

Completed NFL/fantasy scoring remains immutable throughout.
"""

from __future__ import annotations

import argparse
import copy
import json
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import alternate_history_engine as ah
from alternate_history_branch_scoring import choose_branch_lineup, realized_lineup_points
from alternate_history_postseason import full_draft_slots, resolve_six_team_playoffs
import run_fsffl_multiseason_branch_replay as branch_v1
import run_fsffl_multiseason_particle_replay as particle_v1
import run_fsffl_multiseason_particle_replay_v3 as season_v3
from run_fsffl_alternate_history import FSFFLHistoricalAdapter
from run_fsffl_counterfactual_replay import player_positions, starter_slots
from run_fsffl_downstream_dependencies import load
from run_fsffl_historical_usage_policy import HistoricalPoints

DATA = Path("data")
DEFAULT_PARTICLES = 5000
DEFAULT_SEED = 20260824
MAX_TRACES_PER_GROUP = 3
LEDGER_KEY = season_v3.LEDGER_KEY
SeasonParticleGroup = season_v3.SeasonParticleGroup


def score_postseason_week(
    groups: List[SeasonParticleGroup],
    *,
    season: str,
    week: int,
    matchup_rows: List[Dict[str, Any]],
    slots: List[str],
    positions: Dict[str, str],
    weekly_points: Dict[int, Dict[str, float]],
) -> Dict[str, int]:
    rows_by_roster = {str(row.get("roster_id")): row for row in matchup_rows}
    missing_point_particles = 0
    lineup_change_particles = 0

    for group in groups:
        ledger = copy.deepcopy(group.state.get(LEDGER_KEY) or {})
        season_row = ledger.setdefault(str(season), {})
        weekly_lineups = season_row.setdefault("weekly_lineups", {})
        weekly_scores = season_row.setdefault("weekly_scores", {})
        previous = season_row.setdefault("previous_alt_starters", {})
        season_row.setdefault("data_gaps", [])

        for rid, actual_row in rows_by_roster.items():
            roster_players = (group.state.get("roster_players") or {}).get(str(rid), [])
            prev = {str(x) for x in (previous.get(str(rid)) or [])}
            lineup, changes = choose_branch_lineup(
                actual_row,
                roster_players,
                week=week,
                slots=slots,
                positions=positions,
                weekly_points=weekly_points,
                previous_alt_starters=prev,
            )
            score, missing = realized_lineup_points(
                lineup,
                week=week,
                weekly_points=weekly_points,
            )
            weekly_lineups.setdefault(str(rid), {})[str(week)] = {
                "starters": lineup,
                "changes": changes,
            }
            weekly_scores.setdefault(str(rid), {})[str(week)] = score
            previous[str(rid)] = [pid for pid in lineup if pid not in {"0", "None", ""}]
            if changes:
                lineup_change_particles += group.count
            if missing:
                missing_point_particles += group.count
                season_row["data_gaps"].append({
                    "week": week,
                    "roster_id": str(rid),
                    "missing_player_ids": missing,
                })

        group.state[LEDGER_KEY] = ledger

    return {
        "missing_point_particle_roster_instances": missing_point_particles,
        "lineup_change_particle_roster_instances": lineup_change_particles,
    }


def finalize_regular(groups: List[SeasonParticleGroup], season: str, playoff_teams: int) -> None:
    for group in groups:
        ledger = copy.deepcopy(group.state.get(LEDGER_KEY) or {})
        season_row = ledger.setdefault(str(season), {})
        if not season_row.get("standings"):
            season_v3.finalize_regular_season(season_row, playoff_teams)
        group.state[LEDGER_KEY] = ledger


def finalize_postseason(groups: List[SeasonParticleGroup], season: str, playoff_start: int) -> None:
    for group in groups:
        ledger = copy.deepcopy(group.state.get(LEDGER_KEY) or {})
        season_row = ledger.setdefault(str(season), {})
        standings = season_row.get("standings") or []
        weekly_scores = season_row.get("weekly_scores") or {}
        postseason = resolve_six_team_playoffs(standings, weekly_scores, playoff_start)
        season_row["postseason"] = postseason
        season_row["full_following_draft_slots"] = full_draft_slots(
            season_row.get("nonplayoff_draft_slots") or {},
            postseason.get("playoff_draft_slots") or {},
        )
        group.state[LEDGER_KEY] = ledger


def season_event_cutoff(events: List[Dict[str, Any]], fork_season: str) -> Optional[int]:
    target = int(fork_season)
    candidates = []
    for event in events:
        season, _ = season_v3.event_season_week(event)
        if season is None:
            continue
        try:
            value = int(season)
        except ValueError:
            continue
        if value > target:
            candidates.append(int(event.get("created") or 0))
    return min(candidates) if candidates else None


def simulate(
    scenario_path: Path,
    *,
    particles: int = DEFAULT_PARTICLES,
    seed: int = DEFAULT_SEED,
) -> Tuple[List[SeasonParticleGroup], Dict[str, Any]]:
    if particles <= 0:
        raise ah.AlternateHistoryError("0.7e particles must be positive")

    payload = load(scenario_path)
    adapter = FSFFLHistoricalAdapter()
    scenario = ah.scenario_from_json(adapter, payload)
    fork_season = str(payload.get("fork_season") or "")
    fork_week = int(payload.get("fork_week") or 1)
    if not fork_season:
        raise ah.AlternateHistoryError("0.7e requires fork_season in scenario")

    settings = season_v3.historical_settings(adapter, fork_season)
    playoff_start = int(settings.get("playoff_week_start") or 15)
    playoff_teams = int(settings.get("playoff_teams") or 6)
    final_playoff_week = playoff_start + 2

    matchups = load(DATA / "stats" / "fsffl" / fork_season / "league_matchups_raw.json")
    historical_points = HistoricalPoints()
    weekly_points = historical_points.season(fork_season)
    positions = player_positions()
    slots = starter_slots(adapter.league)

    policies = particle_v1.policy_inputs(adapter, scenario, scenario_path)
    triage = policies["triage"]
    usage = policies["usage"]
    trade = policies["trade"]
    expansion = policies["expansion"]
    usage_by_id = {str(x.get("transaction_id")): x for x in (usage.get("decisions") or [])}
    trade_by_id = {str(x.get("transaction_id")): x for x in (trade.get("decisions") or [])}
    expansion_by_id = {str(x.get("transaction_id")): x for x in (expansion.get("expansions") or [])}
    queues = triage.get("queues") or {}
    required = {str(x) for x in queues.get("required_branch_transaction_ids") or []}
    usage_ids = {str(x) for x in queues.get("historical_usage_policy_transaction_ids") or []}
    trade_ids = {str(x) for x in queues.get("historical_gm_required_transaction_ids") or []}
    stable = {str(x) for x in queues.get("structurally_stable_transaction_ids") or []}

    root = ah.apply_fork(ah.reconstruct_state(adapter, scenario.fork_timestamp_ms), scenario)
    root_payload = branch_v1.serial(root)
    root_payload[LEDGER_KEY] = {}
    groups = [SeasonParticleGroup(particles, root_payload, [[]])]
    rng = random.Random(seed)

    all_events = [
        event for event in adapter.completed_events()
        if int(event.get("created") or 0) >= scenario.fork_timestamp_ms
    ]
    cutoff = season_event_cutoff(all_events, fork_season)
    events = [event for event in all_events if cutoff is None or int(event.get("created") or 0) < cutoff]

    next_score_week = fork_week
    regular_finalized = False
    week_audits: List[Dict[str, Any]] = []
    event_audits: List[Dict[str, Any]] = []
    max_unique_states = 1
    invariant_fast_path_events = 0

    def score_through(target_week_exclusive: int) -> None:
        nonlocal next_score_week, groups, regular_finalized, max_unique_states
        while next_score_week < target_week_exclusive and next_score_week <= final_playoff_week:
            if next_score_week < playoff_start:
                audit = season_v3.score_regular_week(
                    groups,
                    season=fork_season,
                    week=next_score_week,
                    matchup_rows=matchups.get(str(next_score_week), []),
                    slots=slots,
                    positions=positions,
                    weekly_points=weekly_points,
                )
            else:
                if not regular_finalized:
                    finalize_regular(groups, fork_season, playoff_teams)
                    groups, _ = season_v3.merge_groups(groups)
                    regular_finalized = True
                audit = score_postseason_week(
                    groups,
                    season=fork_season,
                    week=next_score_week,
                    matchup_rows=matchups.get(str(next_score_week), []),
                    slots=slots,
                    positions=positions,
                    weekly_points=weekly_points,
                )
            groups, merged = season_v3.merge_groups(groups)
            week_audits.append({
                "week": next_score_week,
                "unique_states_after_scoring": len(groups),
                "particles_merged_after_scoring": merged,
                **audit,
            })
            max_unique_states = max(max_unique_states, len(groups))
            next_score_week += 1

    for event in events:
        season, week = season_v3.event_season_week(event)
        if season == fork_season and week is not None:
            score_through(int(week))

        tid = str(event.get("transaction_id") or "")
        kind, proposed = particle_v1.proposed_outcomes(
            event,
            tid,
            usage_ids,
            trade_ids,
            required,
            stable,
            usage_by_id,
            trade_by_id,
            expansion_by_id,
        )

        next_groups: List[SeasonParticleGroup] = []
        actual_branching = False
        legality_changed = False
        for group in groups:
            outcomes = branch_v1.branch_specific_outcomes(group.state, event, proposed)
            if len(outcomes) > 1:
                actual_branching = True
            if len(outcomes) != 1 or outcomes[0].get("mode") != "exact":
                legality_changed = True
            counts = particle_v1.multinomial_counts(
                group.count,
                [float(row.get("probability") or 0.0) for row in outcomes],
                rng,
            )
            if sum(counts) != group.count:
                raise ah.AlternateHistoryError(f"0.7e particle conservation failed at {tid}")
            for idx, (outcome, count) in enumerate(zip(outcomes, counts)):
                if count <= 0:
                    continue
                state = season_v3.apply_preserving_ledger(group.state, event, outcome)
                step = {
                    "transaction_id": tid,
                    "timestamp_ms": int(event.get("created") or 0),
                    "kind": kind,
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
            raise ah.AlternateHistoryError(f"0.7e global particle conservation failed at {tid}")

        if kind == "invariant" and not actual_branching and not legality_changed:
            groups = next_groups
            invariant_fast_path_events += 1
            continue

        groups, merged = season_v3.merge_groups(next_groups)
        max_unique_states = max(max_unique_states, len(groups))
        event_audits.append({
            "transaction_id": tid,
            "kind": kind,
            "actual_branching": actual_branching,
            "legality_changed": legality_changed,
            "unique_states_after_event": len(groups),
            "particles_in_merged_duplicates": merged,
        })

    score_through(final_playoff_week + 1)
    if not regular_finalized:
        finalize_regular(groups, fork_season, playoff_teams)
        regular_finalized = True
    finalize_postseason(groups, fork_season, playoff_start)
    groups, final_merged = season_v3.merge_groups(groups)

    final_particles = sum(group.count for group in groups)
    if final_particles != particles:
        raise ah.AlternateHistoryError(
            f"0.7e final particle conservation failed: {final_particles} != {particles}"
        )

    meta = {
        "scenario": scenario,
        "fork_season": fork_season,
        "playoff_start": playoff_start,
        "particles": particles,
        "seed": seed,
        "cutoff_timestamp_ms": cutoff,
        "events_processed_before_next_season": len(events),
        "week_audits": week_audits,
        "event_audits": event_audits,
        "max_unique_states": max_unique_states,
        "final_merged_particles": final_merged,
        "invariant_fast_path_events": invariant_fast_path_events,
        "historical_points_sources": historical_points.sources,
    }
    return groups, meta


def run(
    scenario_path: Path,
    *,
    particles: int = DEFAULT_PARTICLES,
    seed: int = DEFAULT_SEED,
) -> Path:
    groups, meta = simulate(scenario_path, particles=particles, seed=seed)
    scenario = meta["scenario"]
    fork_season = str(meta["fork_season"])
    focus = str(scenario.focus_roster_id)

    full_slot_counts: Dict[str, Dict[int, int]] = {}
    focus_slot_counts: Dict[int, int] = {}
    champion_counts: Dict[str, int] = {}
    focus_finish_counts: Dict[int, int] = {}
    data_gap_particles = 0

    for group in groups:
        season_row = ((group.state.get(LEDGER_KEY) or {}).get(fork_season) or {})
        full_slots = season_row.get("full_following_draft_slots") or {}
        for rid, slot in full_slots.items():
            full_slot_counts.setdefault(str(rid), {}).setdefault(int(slot), 0)
            full_slot_counts[str(rid)][int(slot)] += group.count
        if focus in full_slots:
            slot = int(full_slots[focus])
            focus_slot_counts[slot] = focus_slot_counts.get(slot, 0) + group.count
        post = season_row.get("postseason") or {}
        champ = str(((post.get("championship") or {}).get("winner")) or "")
        if champ:
            champion_counts[champ] = champion_counts.get(champ, 0) + group.count
        finish = (post.get("finish_by_roster") or {}).get(focus)
        if finish is not None:
            focus_finish_counts[int(finish)] = focus_finish_counts.get(int(finish), 0) + group.count
        if season_row.get("data_gaps"):
            data_gap_particles += group.count

    report = {
        "model_version": "Fantasy-Alternate-History-0.7e-season-boundary-particles",
        "scenario_id": scenario.scenario_id,
        "fork_season": fork_season,
        "following_draft_season": str(int(fork_season) + 1),
        "configuration": {"particles": particles, "seed": seed},
        "design_invariants": {
            "completed_nfl_fantasy_points_are_immutable": True,
            "current_week_points_never_choose_current_week_lineup": True,
            "particle_probability_mass_pruned": False,
            "season_boundary_precedes_next_season_transaction_replay": True,
            "nonplayoff_slots_use_historically_validated_maxpf_rule": True,
            "playoff_slots_use_historically_validated_finish_rule": True,
            "full_draft_order_completed_before_rookie_draft_policy": True,
        },
        "summary": {
            "final_particles": sum(group.count for group in groups),
            "final_probability_mass": 1.0,
            "final_unique_season_boundary_states": len(groups),
            "max_unique_states": meta["max_unique_states"],
            "events_processed_before_next_season": meta["events_processed_before_next_season"],
            "probability_with_scoring_data_gap": round(data_gap_particles / particles, 8),
            "next_season_cutoff_timestamp_ms": meta["cutoff_timestamp_ms"],
        },
        "focus_following_draft_slot_distribution": [
            {"slot": slot, "particles": count, "probability": round(count / particles, 8)}
            for slot, count in sorted(focus_slot_counts.items())
        ],
        "focus_playoff_finish_distribution": [
            {"finish": finish, "particles": count, "probability": round(count / particles, 8)}
            for finish, count in sorted(focus_finish_counts.items())
        ],
        "champion_distribution": [
            {"roster_id": rid, "particles": count, "probability": round(count / particles, 8)}
            for rid, count in sorted(champion_counts.items(), key=lambda row: (-row[1], row[0]))
        ],
        "draft_slot_probabilities_by_original_roster": {
            rid: [
                {"slot": slot, "particles": count, "probability": round(count / particles, 8)}
                for slot, count in sorted(rows.items())
            ]
            for rid, rows in sorted(full_slot_counts.items())
        },
        "historical_points_sources": meta["historical_points_sources"],
        "week_audit": meta["week_audits"],
        "event_audit": meta["event_audits"],
        "representative_season_boundary_states": [
            {
                "particles": group.count,
                "probability": round(group.count / particles, 8),
                "focus_roster_players": sorted((group.state.get("roster_players") or {}).get(focus, [])),
                "full_following_draft_slots": (((group.state.get(LEDGER_KEY) or {}).get(fork_season) or {}).get("full_following_draft_slots") or {}),
                "standings": (((group.state.get(LEDGER_KEY) or {}).get(fork_season) or {}).get("standings") or []),
                "postseason": (((group.state.get(LEDGER_KEY) or {}).get(fork_season) or {}).get("postseason") or {}),
                "trace": (group.traces or [[]])[0],
            }
            for group in sorted(groups, key=lambda x: x.count, reverse=True)[:20]
        ],
    }

    out = ah.write_isolated_json(
        f"results/{scenario.scenario_id}/season_boundary_particles_0_7e.json",
        report,
    )
    print(out)
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Run 0.7e season-boundary particle replay")
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--particles", type=int, default=DEFAULT_PARTICLES)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    run(args.scenario, particles=args.particles, seed=args.seed)


if __name__ == "__main__":
    main()
