#!/usr/bin/env python3
"""FSFFL Alternate History 0.7f: pre-rookie-draft particle handoff.

Consumes the archived-anchor 2023 season-boundary logic, then replays only the
following season's fantasy transactions that occurred BEFORE the 2024 rookie
draft. The output freezes branch-specific player/pick/FAAB state at draft start
and reports who controls every original-roster pick in each round.

This stage intentionally does not select rookies. It establishes the correct
causal pick-controller topology first.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any, Dict, List, Tuple

import alternate_history_engine as ah
from alternate_history_historical_state import reconstruct_completed_season_state
import run_fsffl_multiseason_branch_replay as branch_v1
import run_fsffl_multiseason_particle_replay as particle_v1
import run_fsffl_multiseason_particle_replay_v3 as season_v3
import run_fsffl_season_boundary_particles as boundary_core
from run_fsffl_alternate_draft_candidates import raw_draft
from run_fsffl_alternate_history import FSFFLHistoricalAdapter
from run_fsffl_downstream_dependencies import load

DEFAULT_PARTICLES = 5000
DEFAULT_SEED = 20260824
MAX_TRACES_PER_GROUP = 3
SeasonParticleGroup = season_v3.SeasonParticleGroup


class AnchoredFSFFLAdapter(FSFFLHistoricalAdapter):
    pass


def anchored_boundary_simulate(
    scenario_path: Path,
    *,
    particles: int,
    seed: int,
):
    payload = load(scenario_path)
    fork_season = str(payload.get("fork_season") or "")
    if not fork_season:
        raise ah.AlternateHistoryError("0.7f requires fork_season")

    original_adapter_cls = boundary_core.FSFFLHistoricalAdapter
    original_reconstruct = ah.reconstruct_state

    def anchored_reconstruct(adapter: Any, timestamp_ms: int):
        if isinstance(adapter, AnchoredFSFFLAdapter):
            return reconstruct_completed_season_state(adapter, fork_season, int(timestamp_ms))
        return original_reconstruct(adapter, int(timestamp_ms))

    try:
        boundary_core.FSFFLHistoricalAdapter = AnchoredFSFFLAdapter
        ah.reconstruct_state = anchored_reconstruct
        return boundary_core.simulate(scenario_path, particles=particles, seed=seed)
    finally:
        ah.reconstruct_state = original_reconstruct
        boundary_core.FSFFLHistoricalAdapter = original_adapter_cls


def draft_start_ms(draft_season: str) -> int:
    entry = raw_draft(str(draft_season))
    draft = entry.get("draft") or {}
    value = int(draft.get("start_time") or draft.get("created") or 0)
    if value <= 0:
        raise ah.AlternateHistoryError(f"Draft start unavailable for {draft_season}")
    return value


def controller_for(state: Dict[str, Any], season: str, round_no: int, original_roster_id: str) -> str:
    key = f"pick:{season}:R{int(round_no)}:orig{original_roster_id}"
    return str((state.get("pick_owners") or {}).get(key) or original_roster_id)


def run(
    scenario_path: Path,
    *,
    particles: int = DEFAULT_PARTICLES,
    seed: int = DEFAULT_SEED,
) -> Path:
    groups, boundary_meta = anchored_boundary_simulate(
        scenario_path,
        particles=particles,
        seed=seed,
    )
    scenario = boundary_meta["scenario"]
    fork_season = str(boundary_meta["fork_season"])
    draft_season = str(int(fork_season) + 1)
    start_ms = draft_start_ms(draft_season)
    boundary_cutoff = int(boundary_meta.get("cutoff_timestamp_ms") or 0)

    adapter = FSFFLHistoricalAdapter()
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

    events = [
        event for event in adapter.completed_events()
        if int(event.get("created") or 0) >= boundary_cutoff
        and int(event.get("created") or 0) < start_ms
    ]
    rng = random.Random(seed ^ 0xD4A47)
    audits: List[Dict[str, Any]] = []
    max_unique_states = len(groups)
    invariant_fast_path_events = 0

    for event in events:
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
                raise ah.AlternateHistoryError(f"0.7f particle conservation failed at {tid}")
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
            raise ah.AlternateHistoryError(f"0.7f global particle conservation failed at {tid}")

        if kind == "invariant" and not actual_branching and not legality_changed:
            groups = next_groups
            invariant_fast_path_events += 1
            continue

        groups, merged = season_v3.merge_groups(next_groups)
        max_unique_states = max(max_unique_states, len(groups))
        audits.append({
            "transaction_id": tid,
            "timestamp_ms": int(event.get("created") or 0),
            "kind": kind,
            "actual_branching": actual_branching,
            "legality_changed": legality_changed,
            "unique_states_after_event": len(groups),
            "particles_in_merged_duplicates": merged,
        })

    groups, final_merged = season_v3.merge_groups(groups)
    if sum(group.count for group in groups) != particles:
        raise ah.AlternateHistoryError("0.7f final particle conservation failed")

    draft_entry = raw_draft(draft_season)
    draft = draft_entry.get("draft") or {}
    rounds = int((draft.get("settings") or {}).get("rounds") or 3)
    teams = int((draft.get("settings") or {}).get("teams") or 12)
    original_rosters = [str(x) for x in range(1, teams + 1)]

    controller_counts: Dict[str, Dict[str, int]] = {}
    for group in groups:
        for rnd in range(1, rounds + 1):
            for original in original_rosters:
                key = f"{draft_season}:R{rnd}:orig{original}"
                controller = controller_for(group.state, draft_season, rnd, original)
                controller_counts.setdefault(key, {}).setdefault(controller, 0)
                controller_counts[key][controller] += group.count

    report = {
        "model_version": "Fantasy-Alternate-History-0.7f-predraft-particles",
        "scenario_id": scenario.scenario_id,
        "draft_season": draft_season,
        "draft_start_timestamp_ms": start_ms,
        "season_boundary_cutoff_timestamp_ms": boundary_cutoff,
        "configuration": {"particles": particles, "seed": seed},
        "design_invariants": {
            "archived_completed_season_root_anchor": True,
            "full_prior_season_draft_order_resolved_before_predraft_replay": True,
            "only_transactions_before_rookie_draft_replayed": True,
            "pick_controller_frozen_at_exact_draft_start": True,
            "original_roster_determines_slot": True,
            "current_pick_controller_makes_selection": True,
            "particle_probability_mass_pruned": False,
        },
        "summary": {
            "predraft_events_replayed": len(events),
            "final_particles": particles,
            "final_probability_mass": 1.0,
            "final_unique_predraft_states": len(groups),
            "max_unique_states": max_unique_states,
            "final_particles_merged": final_merged,
            "invariant_fast_path_events": invariant_fast_path_events,
        },
        "pick_controller_probabilities": {
            key: [
                {
                    "controller_roster_id": rid,
                    "particles": count,
                    "probability": round(count / particles, 8),
                }
                for rid, count in sorted(rows.items(), key=lambda row: (-row[1], row[0]))
            ]
            for key, rows in sorted(controller_counts.items())
        },
        "event_audit": audits,
        "representative_predraft_states": [
            {
                "particles": group.count,
                "probability": round(group.count / particles, 8),
                "pick_owners": group.state.get("pick_owners") or {},
                "trace": (group.traces or [[]])[0],
            }
            for group in sorted(groups, key=lambda x: x.count, reverse=True)[:20]
        ],
    }

    out = ah.write_isolated_json(
        f"results/{scenario.scenario_id}/predraft_particles_0_7f.json",
        report,
    )
    print(out)
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Run 0.7f pre-rookie-draft particle handoff")
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--particles", type=int, default=DEFAULT_PARTICLES)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    run(args.scenario, particles=args.particles, seed=args.seed)


if __name__ == "__main__":
    main()
