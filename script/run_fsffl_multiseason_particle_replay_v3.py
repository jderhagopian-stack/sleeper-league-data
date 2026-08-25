#!/usr/bin/env python3
"""FSFFL Alternate History 0.7d: fork-season standings + Max PF particles.

Production-candidate fork-season feedback layer. Extends grouped equal-weight
particles with a causal season ledger containing:
- no-hindsight alternate weekly lineups and scores;
- regular-season records / seeding;
- exact best-ball Max PF;
- branch-specific playoff field;
- deterministic non-playoff rookie slots 1.01-1.06 via validated Max PF rule.

Completed NFL/fantasy player scoring is immutable. Current-week realized points
are used only after a lineup is chosen from pre-week evidence.
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
import alternate_history_roster_compliance as roster_compliance
from alternate_history_branch_scoring import (
    choose_branch_lineup,
    realized_lineup_points,
    seeded_standings,
    update_records_from_week,
)
from alternate_history_maxpf import best_lineup_points
import run_fsffl_multiseason_branch_replay as branch_v1
import run_fsffl_multiseason_particle_replay as particle_v1
from run_fsffl_alternate_history import FSFFLHistoricalAdapter
from run_fsffl_counterfactual_replay import player_positions, starter_slots
from run_fsffl_downstream_dependencies import load
from run_fsffl_historical_usage_policy import HistoricalPoints

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


def finalize_regular_season(season_row: Dict[str, Any], playoff_teams: int) -> None:
    standings = seeded_standings(season_row.get("records") or {})
    season_row["standings"] = standings
    playoff_field = [str(row["roster_id"]) for row in standings[:playoff_teams]]
    season_row["playoff_field"] = playoff_field
    nonplay = [str(row["roster_id"]) for row in standings if str(row["roster_id"]) not in set(playoff_field)]
    maxpf = season_row.get("season_max_pf") or {}
    record_map = {str(row["roster_id"]): row for row in standings}

    def record_value(rid: str) -> float:
        row = record_map.get(str(rid)) or {}
        return float(row.get("wins") or 0) + 0.5 * float(row.get("ties") or 0)

    nonplay_order = sorted(
        nonplay,
        key=lambda rid: (
            float(maxpf.get(str(rid), float("inf"))),
            record_value(rid),
            str(rid),
        ),
    )
    season_row["nonplayoff_draft_slots"] = {
        rid: idx for idx, rid in enumerate(nonplay_order, 1)
    }
    season_row["nonplayoff_draft_order"] = nonplay_order


def score_regular_week(
    groups: List[SeasonParticleGroup],
    *,
    season: str,
    week: int,
    matchup_rows: List[Dict[str, Any]],
    slots: List[str],
    positions: Dict[str, str],
    weekly_points: Dict[int, Dict[str, float]],
) -> Dict[str, Any]:
    compliance_audit = None
    if int(week) == 1:
        compliance_audit = roster_compliance.enforce_week1_roster_envelope(
            groups,
            season=str(season),
        )

    missing_point_particles = 0
    lineup_change_particles = 0
    rows_by_roster = {str(row.get("roster_id")): row for row in matchup_rows}
    realized = weekly_points.get(int(week), {})

    for group in groups:
        ledger = copy.deepcopy(group.state.get(LEDGER_KEY) or {})
        season_row = ledger.setdefault(str(season), {
            "weekly_lineups": {},
            "weekly_scores": {},
            "weekly_max_pf": {},
            "season_max_pf": {},
            "records": {},
            "previous_alt_starters": {},
            "data_gaps": [],
        })
        weekly_lineups = season_row.setdefault("weekly_lineups", {})
        weekly_scores = season_row.setdefault("weekly_scores", {})
        weekly_max = season_row.setdefault("weekly_max_pf", {})
        season_max = season_row.setdefault("season_max_pf", {})
        previous = season_row.setdefault("previous_alt_starters", {})
        records = season_row.setdefault("records", {})
        scores: Dict[str, float] = {}

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
            max_pf, max_lineup = best_lineup_points(
                roster_players,
                slots,
                positions,
                realized,
            )
            scores[str(rid)] = score
            weekly_lineups.setdefault(str(rid), {})[str(week)] = {
                "starters": lineup,
                "changes": changes,
            }
            weekly_scores.setdefault(str(rid), {})[str(week)] = score
            weekly_max.setdefault(str(rid), {})[str(week)] = {
                "max_pf": max_pf,
                "lineup": max_lineup,
            }
            season_max[str(rid)] = round(float(season_max.get(str(rid)) or 0.0) + float(max_pf), 2)
            previous[str(rid)] = [pid for pid in lineup if pid not in {"0", "None", ""}]
            if changes:
                lineup_change_particles += group.count
            if missing:
                missing_point_particles += group.count
                season_row.setdefault("data_gaps", []).append({
                    "week": week,
                    "roster_id": str(rid),
                    "missing_player_ids": missing,
                })

        update_records_from_week(records, matchup_rows, scores)
        group.state[LEDGER_KEY] = ledger

    result = {
        "missing_point_particle_roster_instances": missing_point_particles,
        "lineup_change_particle_roster_instances": lineup_change_particles,
    }
    if compliance_audit is not None:
        result["roster_compliance"] = compliance_audit
    return result


def run(
    scenario_path: Path,
    *,
    particles: int = DEFAULT_PARTICLES,
    seed: int = DEFAULT_SEED,
) -> Path:
    if particles <= 0:
        raise ah.AlternateHistoryError("0.7d particles must be positive")

    payload = load(scenario_path)
    adapter = FSFFLHistoricalAdapter()
    scenario = ah.scenario_from_json(adapter, payload)
    fork_season = str(payload.get("fork_season") or "")
    fork_week = int(payload.get("fork_week") or 1)
    if not fork_season:
        raise ah.AlternateHistoryError("0.7d requires fork_season in scenario")

    settings = historical_settings(adapter, fork_season)
    playoff_start = int(settings.get("playoff_week_start") or 15)
    playoff_teams = int(settings.get("playoff_teams") or 6)
    if playoff_teams <= 0:
        raise ah.AlternateHistoryError("0.7d invalid playoff_teams")

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
    audits: List[Dict[str, Any]] = []
    week_audits: List[Dict[str, Any]] = []
    next_score_week = fork_week
    invariant_fast_path_events = 0
    max_unique_states = 1

    for event in adapter.completed_events():
        created = int(event.get("created") or 0)
        if created < scenario.fork_timestamp_ms:
            continue

        season, week = event_season_week(event)
        if season == fork_season and week is not None:
            while next_score_week < min(int(week), playoff_start):
                audit = score_regular_week(
                    groups,
                    season=fork_season,
                    week=next_score_week,
                    matchup_rows=matchups.get(str(next_score_week), []),
                    slots=slots,
                    positions=positions,
                    weekly_points=weekly_points,
                )
                groups, merged = merge_groups(groups)
                week_audits.append({
                    "week": next_score_week,
                    "unique_states_after_scoring": len(groups),
                    "particles_merged_after_scoring": merged,
                    **audit,
                })
                max_unique_states = max(max_unique_states, len(groups))
                next_score_week += 1

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
                raise ah.AlternateHistoryError(f"0.7d particle conservation failed at {tid}")
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
                    list(t) + [step]
                    for t in (group.traces or [[]])[:MAX_TRACES_PER_GROUP]
                ]
                next_groups.append(SeasonParticleGroup(count, state, traces))

        if sum(x.count for x in next_groups) != particles:
            raise ah.AlternateHistoryError(f"0.7d particle conservation failed before merge at {tid}")

        if kind == "invariant" and not actual_branching and not legality_changed:
            groups = next_groups
            invariant_fast_path_events += 1
            continue

        groups, merged_particles = merge_groups(next_groups)
        max_unique_states = max(max_unique_states, len(groups))
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
        audit = score_regular_week(
            groups,
            season=fork_season,
            week=next_score_week,
            matchup_rows=matchups.get(str(next_score_week), []),
            slots=slots,
            positions=positions,
            weekly_points=weekly_points,
        )
        groups, merged = merge_groups(groups)
        week_audits.append({
            "week": next_score_week,
            "unique_states_after_scoring": len(groups),
            "particles_merged_after_scoring": merged,
            **audit,
        })
        max_unique_states = max(max_unique_states, len(groups))
        next_score_week += 1

    for group in groups:
        ledger = copy.deepcopy(group.state.get(LEDGER_KEY) or {})
        season_row = ledger.setdefault(fork_season, {})
        finalize_regular_season(season_row, playoff_teams)
        group.state[LEDGER_KEY] = ledger
    groups, final_merged = merge_groups(groups)

    final_particles = sum(group.count for group in groups)
    if final_particles != particles:
        raise ah.AlternateHistoryError(
            f"0.7d final particle conservation failed: {final_particles} != {particles}"
        )

    playoff_field_counts: Dict[Tuple[str, ...], int] = {}
    nonplay_slot_counts: Dict[str, Dict[int, int]] = {}
    focus_seed_counts: Dict[int, int] = {}
    focus = str(scenario.focus_roster_id)
    any_data_gap_particles = 0
    for group in groups:
        season_row = ((group.state.get(LEDGER_KEY) or {}).get(fork_season) or {})
        field = tuple(str(x) for x in (season_row.get("playoff_field") or []))
        playoff_field_counts[field] = playoff_field_counts.get(field, 0) + group.count
        for rid, slot in (season_row.get("nonplayoff_draft_slots") or {}).items():
            nonplay_slot_counts.setdefault(str(rid), {}).setdefault(int(slot), 0)
            nonplay_slot_counts[str(rid)][int(slot)] += group.count
        for row in season_row.get("standings") or []:
            if str(row.get("roster_id")) == focus:
                seed = int(row.get("seed"))
                focus_seed_counts[seed] = focus_seed_counts.get(seed, 0) + group.count
        if season_row.get("data_gaps"):
            any_data_gap_particles += group.count

    report = {
        "model_version": "Fantasy-Alternate-History-0.7d-particle-season-feedback",
        "scenario_id": scenario.scenario_id,
        "fork_season": fork_season,
        "playoff_week_start": playoff_start,
        "configuration": {"particles": particles, "seed": seed},
        "design_invariants": {
            "completed_nfl_fantasy_points_are_immutable": True,
            "current_week_points_never_choose_current_week_lineup": True,
            "historical_starters_are_revealed_choice_baseline": True,
            "branch_specific_roster_eligibility": True,
            "broad_historical_player_week_points_used": True,
            "max_pf_exact_best_ball": True,
            "nonplayoff_slots_use_validated_maxpf_ascending_rule": True,
            "maxpf_exact_tie_uses_worse_regular_season_record": True,
            "particle_probability_mass_pruned": False,
            "season_feedback_part_of_state_identity": True,
            "historical_week1_roster_envelope_enforced": True,
        },
        "historical_points_sources": historical_points.sources,
        "summary": {
            "final_particles": final_particles,
            "final_probability_mass": round(final_particles / particles, 10),
            "final_unique_states_with_season_feedback": len(groups),
            "max_unique_states": max_unique_states,
            "particles_with_any_scoring_data_gap": any_data_gap_particles,
            "probability_with_any_scoring_data_gap": round(any_data_gap_particles / particles, 8),
            "final_particles_merged": final_merged,
            "invariant_fast_path_events": invariant_fast_path_events,
        },
        "focus_seed_distribution": [
            {"seed": seed_no, "particles": count, "probability": round(count / particles, 8)}
            for seed_no, count in sorted(focus_seed_counts.items())
        ],
        "playoff_field_distribution": [
            {"playoff_field": list(field), "particles": count, "probability": round(count / particles, 8)}
            for field, count in sorted(playoff_field_counts.items(), key=lambda x: (-x[1], x[0]))
        ],
        "nonplayoff_draft_slot_probabilities": {
            rid: [
                {"slot": slot, "particles": count, "probability": round(count / particles, 8)}
                for slot, count in sorted(slots_map.items())
            ]
            for rid, slots_map in sorted(nonplay_slot_counts.items())
        },
        "week_audit": week_audits,
        "event_audit": audits,
        "representative_state_groups": [
            {
                "particles": group.count,
                "probability": round(group.count / particles, 8),
                "standings": (((group.state.get(LEDGER_KEY) or {}).get(fork_season) or {}).get("standings") or []),
                "season_max_pf": (((group.state.get(LEDGER_KEY) or {}).get(fork_season) or {}).get("season_max_pf") or {}),
                "nonplayoff_draft_slots": (((group.state.get(LEDGER_KEY) or {}).get(fork_season) or {}).get("nonplayoff_draft_slots") or {}),
                "trace": (group.traces or [[]])[0],
            }
            for group in sorted(groups, key=lambda x: x.count, reverse=True)[:20]
        ],
    }

    out = ah.write_isolated_json(
        f"results/{scenario.scenario_id}/multiseason_particle_replay_0_7d.json",
        report,
    )
    print(out)
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Run 0.7d particle season-feedback replay")
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--particles", type=int, default=DEFAULT_PARTICLES)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    run(args.scenario, particles=args.particles, seed=args.seed)


if __name__ == "__main__":
    main()