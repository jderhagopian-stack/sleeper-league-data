#!/usr/bin/env python3
"""FSFFL Alternate History 0.7c: grouped particles with fork-season Max PF.

Extends the probability-mass-conserving 0.7b particle replay by accumulating
weekly branch-specific Max PF during the fork season. The season ledger is part
of particle-group identity, so histories that converge to the same later roster
but produced different season consequences are never incorrectly merged.

NFL/fantasy scoring outcomes are immutable realized history. Only which players
are eligible for a fantasy roster in a given alternate branch may change.
"""

from __future__ import annotations

import argparse
import copy
import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import alternate_history_engine as ah
from alternate_history_maxpf import best_lineup_points
import run_fsffl_multiseason_branch_replay as branch_v1
import run_fsffl_multiseason_particle_replay as particle_v1
from run_fsffl_alternate_history import FSFFLHistoricalAdapter
from run_fsffl_counterfactual_replay import player_positions, starter_slots, weekly_points_index
from run_fsffl_downstream_dependencies import load

DATA = Path("data")
DEFAULT_PARTICLES = 5000
DEFAULT_SEED = 20260824
MAX_TRACES_PER_GROUP = 3
LEDGER_KEY = "_alternate_history_season_ledger"


@dataclass
class SeasonParticleGroup:
    count: int
    state: Dict[str, Any]
    traces: List[List[Dict[str, Any]]] = field(default_factory=lambda: [[]])


def event_season_week(event: Dict[str, Any]) -> Tuple[Optional[str], Optional[int]]:
    meta = event.get("metadata") or {}
    season = event.get("source_season") or event.get("season") or meta.get("source_season") or meta.get("season")
    week = event.get("leg") or event.get("week") or meta.get("leg") or meta.get("week")
    try:
        parsed_week = int(week) if week is not None else None
    except (TypeError, ValueError):
        parsed_week = None
    return (str(season) if season is not None else None, parsed_week)


def historical_settings(adapter: FSFFLHistoricalAdapter, season: str) -> Dict[str, Any]:
    for row in adapter.raw_history_seasons():
        league = row.get("league") or {}
        if str(league.get("season") or "") == str(season):
            return dict(league.get("settings") or {})
    if str((adapter.league or {}).get("season") or "") == str(season):
        return dict((adapter.league or {}).get("settings") or {})
    return {}


def season_state_key(state: Dict[str, Any]) -> str:
    """Identity includes causal season history, not merely terminal assets."""
    canonical = {
        "roster_players": {
            str(k): sorted(str(x) for x in (v or []))
            for k, v in sorted((state.get("roster_players") or {}).items())
        },
        "pick_owners": dict(sorted((state.get("pick_owners") or {}).items())),
        "faab": {
            str(k): float(v or 0.0)
            for k, v in sorted((state.get("faab") or {}).items())
        },
        "season_ledger": state.get(LEDGER_KEY) or {},
    }
    return ah.stable_hash(canonical)


def merge_groups(groups: Iterable[SeasonParticleGroup]) -> Tuple[List[SeasonParticleGroup], int]:
    by_key: Dict[str, SeasonParticleGroup] = {}
    merged_particles = 0
    for group in groups:
        if group.count <= 0:
            continue
        key = season_state_key(group.state)
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = SeasonParticleGroup(
                group.count,
                group.state,
                [list(t) for t in group.traces[:MAX_TRACES_PER_GROUP]],
            )
            continue
        merged_particles += group.count
        existing.count += group.count
        for trace in group.traces:
            if len(existing.traces) >= MAX_TRACES_PER_GROUP:
                break
            if trace not in existing.traces:
                existing.traces.append(list(trace))
    return list(by_key.values()), merged_particles


def apply_preserving_ledger(state_payload: Dict[str, Any], event: Dict[str, Any], outcome: Dict[str, Any]) -> Dict[str, Any]:
    ledger = copy.deepcopy(state_payload.get(LEDGER_KEY) or {})
    new_state = branch_v1.apply_outcome(state_payload, event, outcome)
    new_state[LEDGER_KEY] = ledger
    return new_state


def score_week(
    groups: List[SeasonParticleGroup],
    *,
    season: str,
    week: int,
    slots: List[str],
    positions: Dict[str, str],
    points_by_week: Dict[int, Dict[str, float]],
) -> None:
    realized = points_by_week.get(int(week), {})
    for group in groups:
        ledger = copy.deepcopy(group.state.get(LEDGER_KEY) or {})
        season_row = ledger.setdefault(str(season), {"weekly_max_pf": {}, "season_max_pf": {}})
        weekly = season_row.setdefault("weekly_max_pf", {})
        totals = season_row.setdefault("season_max_pf", {})
        for rid, players in (group.state.get("roster_players") or {}).items():
            score, lineup = best_lineup_points(players or [], slots, positions, realized)
            weekly.setdefault(str(rid), {})[str(week)] = {
                "max_pf": score,
                "lineup": lineup,
            }
            totals[str(rid)] = round(float(totals.get(str(rid)) or 0.0) + float(score), 2)
        group.state[LEDGER_KEY] = ledger


def run(
    scenario_path: Path,
    *,
    particles: int = DEFAULT_PARTICLES,
    seed: int = DEFAULT_SEED,
) -> Path:
    if particles <= 0:
        raise ah.AlternateHistoryError("0.7c particles must be positive")

    payload = load(scenario_path)
    adapter = FSFFLHistoricalAdapter()
    scenario = ah.scenario_from_json(adapter, payload)
    fork_season = str(payload.get("fork_season") or "")
    fork_week = int(payload.get("fork_week") or 1)
    if not fork_season:
        raise ah.AlternateHistoryError("0.7c requires fork_season in scenario")

    settings = historical_settings(adapter, fork_season)
    playoff_start = int(settings.get("playoff_week_start") or 15)
    matchups = load(DATA / "stats" / "fsffl" / fork_season / "league_matchups_raw.json")
    points_by_week = weekly_points_index(matchups)
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
    audits: List[Dict[str, Any]] = []
    scored_weeks: List[int] = []
    next_score_week = fork_week
    invariant_fast_path_events = 0
    max_unique_states = 1
    total_merge_boundaries = 0

    for event in adapter.completed_events():
        created = int(event.get("created") or 0)
        if created < scenario.fork_timestamp_ms:
            continue

        season, week = event_season_week(event)
        if season == fork_season and week is not None:
            while next_score_week < min(int(week), playoff_start):
                score_week(
                    groups,
                    season=fork_season,
                    week=next_score_week,
                    slots=slots,
                    positions=positions,
                    points_by_week=points_by_week,
                )
                scored_weeks.append(next_score_week)
                next_score_week += 1
                groups, _ = merge_groups(groups)

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
                [float(x.get("probability") or 0.0) for x in outcomes],
                rng,
            )
            if sum(counts) != group.count:
                raise ah.AlternateHistoryError(f"0.7c particle conservation failed at {tid}")

            for idx, (outcome, count) in enumerate(zip(outcomes, counts)):
                if count <= 0:
                    continue
                state = apply_preserving_ledger(group.state, event, outcome)
                step = {
                    "transaction_id": tid,
                    "timestamp_ms": created,
                    "kind": kind,
                    "outcome": outcome.get("outcome"),
                    "conditional_probability": round(float(outcome.get("probability") or 0.0), 8),
                    "particles": count,
                }
                if outcome.get("package_id"):
                    step["package_id"] = outcome.get("package_id")
                traces = [
                    (list(t) + [step])
                    for t in (group.traces or [[]])[:MAX_TRACES_PER_GROUP]
                ]
                next_groups.append(SeasonParticleGroup(count, state, traces))

        if sum(x.count for x in next_groups) != particles:
            raise ah.AlternateHistoryError(f"0.7c global particle conservation failed before merge at {tid}")

        if kind == "invariant" and not actual_branching and not legality_changed:
            groups = next_groups
            invariant_fast_path_events += 1
            continue

        groups, merged_particles = merge_groups(next_groups)
        total_merge_boundaries += 1
        max_unique_states = max(max_unique_states, len(groups))
        if sum(x.count for x in groups) != particles:
            raise ah.AlternateHistoryError(f"0.7c particle conservation failed after merge at {tid}")
        audits.append({
            "transaction_id": tid,
            "timestamp_ms": created,
            "kind": kind,
            "actual_branching": actual_branching,
            "legality_changed": legality_changed,
            "unique_states_after_event": len(groups),
            "particles_in_merged_duplicates": merged_particles,
        })

    while next_score_week < playoff_start:
        score_week(
            groups,
            season=fork_season,
            week=next_score_week,
            slots=slots,
            positions=positions,
            points_by_week=points_by_week,
        )
        scored_weeks.append(next_score_week)
        next_score_week += 1
        groups, _ = merge_groups(groups)

    groups, final_merged = merge_groups(groups)
    final_particles = sum(x.count for x in groups)
    if final_particles != particles:
        raise ah.AlternateHistoryError(
            f"0.7c final particle conservation failed: {final_particles} != {particles}"
        )

    maxpf_counts: Dict[str, Dict[str, int]] = {}
    for group in groups:
        totals = (((group.state.get(LEDGER_KEY) or {}).get(fork_season) or {}).get("season_max_pf") or {})
        for rid, value in totals.items():
            key = f"{float(value):.2f}"
            maxpf_counts.setdefault(str(rid), {}).setdefault(key, 0)
            maxpf_counts[str(rid)][key] += int(group.count)

    report = {
        "model_version": "Fantasy-Alternate-History-0.7c-particles-with-fork-season-maxpf",
        "scenario_id": scenario.scenario_id,
        "fork_season": fork_season,
        "playoff_week_start": playoff_start,
        "regular_season_weeks_scored": scored_weeks,
        "configuration": {"particles": particles, "seed": seed},
        "design_invariants": {
            "completed_nfl_history_is_immutable": True,
            "particle_probability_mass_pruned": False,
            "equal_weight_particle_count_conserved": True,
            "max_pf_uses_exact_best_ball_optimizer": True,
            "max_pf_uses_branch_specific_roster_eligibility": True,
            "season_ledger_part_of_state_identity": True,
            "current_gm3_numeric_values_used": False,
            "future_nfl_outcomes_used_for_historical_decisions": False,
        },
        "summary": {
            "final_particles": final_particles,
            "final_probability_mass": round(final_particles / particles, 10),
            "final_unique_states_with_season_history": len(groups),
            "max_unique_states": max_unique_states,
            "merge_boundaries": total_merge_boundaries,
            "final_particles_merged": final_merged,
            "invariant_fast_path_events": invariant_fast_path_events,
        },
        "max_pf_distributions_by_roster": {
            rid: [
                {
                    "max_pf": float(value),
                    "particles": count,
                    "probability": round(count / particles, 8),
                }
                for value, count in sorted(rows.items(), key=lambda x: float(x[0]))
            ]
            for rid, rows in sorted(maxpf_counts.items())
        },
        "event_audit": audits,
        "representative_state_groups": [
            {
                "particles": group.count,
                "probability": round(group.count / particles, 8),
                "season_max_pf": (((group.state.get(LEDGER_KEY) or {}).get(fork_season) or {}).get("season_max_pf") or {}),
                "trace": (group.traces or [[]])[0],
            }
            for group in sorted(groups, key=lambda x: x.count, reverse=True)[:20]
        ],
    }

    out = ah.write_isolated_json(
        f"results/{scenario.scenario_id}/multiseason_particle_replay_0_7c.json",
        report,
    )
    print(out)
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Run 0.7c grouped particle replay with fork-season Max PF")
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--particles", type=int, default=DEFAULT_PARTICLES)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    run(args.scenario, particles=args.particles, seed=args.seed)


if __name__ == "__main__":
    main()
