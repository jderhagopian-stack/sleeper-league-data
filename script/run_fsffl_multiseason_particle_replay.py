#!/usr/bin/env python3
"""FSFFL Alternate History 0.7b: grouped particle chronological replay.

Replaces the lossy top-probability beam approximation with deterministic seeded
Monte Carlo particles. Every particle represents equal probability mass, so the
full distribution is retained rather than dropping low-probability paths.

Efficiency comes from grouping particles that share an identical league state:
- invariant events are applied once per unique state group;
- sensitive decisions split group counts with seeded multinomial sampling;
- identical post-event states are merged by summing particle counts;
- only a few representative traces are retained per group.

The particle count is conserved exactly from fork to present. No branch
probability mass is pruned.
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
import run_fsffl_multiseason_branch_replay as v1
from run_fsffl_alternate_history import FSFFLHistoricalAdapter
from run_fsffl_downstream_dependencies import load

DEFAULT_PARTICLES = 5000
DEFAULT_SEED = 20260824
MAX_TRACES_PER_GROUP = 3


@dataclass
class ParticleGroup:
    count: int
    state: Dict[str, Any]
    traces: List[List[Dict[str, Any]]] = field(default_factory=lambda: [[]])


def state_key(state: Dict[str, Any]) -> str:
    canonical = {
        "roster_players": {str(k): sorted(str(x) for x in (v or [])) for k, v in sorted((state.get("roster_players") or {}).items())},
        "pick_owners": dict(sorted((state.get("pick_owners") or {}).items())),
        "faab": {str(k): float(v or 0.0) for k, v in sorted((state.get("faab") or {}).items())},
    }
    return ah.stable_hash(canonical)


def merge_groups(groups: Iterable[ParticleGroup]) -> Tuple[List[ParticleGroup], int]:
    by_key: Dict[str, ParticleGroup] = {}
    merged_particles = 0
    for group in groups:
        if group.count <= 0:
            continue
        key = state_key(group.state)
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = ParticleGroup(group.count, group.state, [list(t) for t in group.traces[:MAX_TRACES_PER_GROUP]])
        else:
            merged_particles += group.count
            existing.count += group.count
            for trace in group.traces:
                if len(existing.traces) >= MAX_TRACES_PER_GROUP:
                    break
                existing.traces.append(list(trace))
    return list(by_key.values()), merged_particles


def multinomial_counts(n: int, probabilities: List[float], rng: random.Random) -> List[int]:
    """Seeded categorical draw with exact count conservation.

    Uses cumulative probabilities and repeated RNG draws. Number of sensitive
    decisions is small enough that this remains cheap while avoiding numpy as a
    core-engine dependency.
    """
    if n <= 0:
        return [0] * len(probabilities)
    total = sum(max(0.0, float(x)) for x in probabilities)
    if total <= 0.0:
        out = [0] * len(probabilities)
        if out:
            out[0] = n
        return out
    probs = [max(0.0, float(x)) / total for x in probabilities]
    cumulative: List[float] = []
    running = 0.0
    for p in probs:
        running += p
        cumulative.append(running)
    cumulative[-1] = 1.0
    counts = [0] * len(probs)
    for _ in range(n):
        u = rng.random()
        for idx, bound in enumerate(cumulative):
            if u <= bound:
                counts[idx] += 1
                break
    return counts


def load_or_run(path: Path, runner, scenario_path: Path) -> Dict[str, Any]:
    if path.exists():
        payload = load(path)
        if payload:
            return payload
    return load(runner(scenario_path))


def policy_inputs(adapter: FSFFLHistoricalAdapter, scenario: ah.Scenario, scenario_path: Path) -> Dict[str, Any]:
    from run_fsffl_historical_policy_triage import run as run_triage
    from run_fsffl_historical_usage_policy_v3 import run as run_usage
    from run_fsffl_historical_trade_policy_v2 import run as run_trade
    from run_fsffl_historical_trade_policy_v3 import run as run_expand

    base = Path("data/alternate_history/results") / scenario.scenario_id
    triage = load_or_run(base / "policy_triage_0_5b.json", run_triage, scenario_path)
    usage = load_or_run(base / "historical_usage_policy_0_5c.json", run_usage, scenario_path)
    trade = load_or_run(base / "historical_trade_policy_0_5d.json", run_trade, scenario_path)
    expansion = load_or_run(base / "historical_trade_expansion_0_5e.json", run_expand, scenario_path)
    return {"triage": triage, "usage": usage, "trade": trade, "expansion": expansion}


def proposed_outcomes(
    event: Dict[str, Any],
    tid: str,
    usage_ids: set[str],
    trade_ids: set[str],
    required: set[str],
    stable: set[str],
    usage_by_id: Dict[str, Any],
    trade_by_id: Dict[str, Any],
    expansion_by_id: Dict[str, Any],
) -> Tuple[str, List[Dict[str, Any]]]:
    if tid in usage_ids:
        row = usage_by_id.get(tid)
        return "historical_usage_policy", v1.usage_outcomes(event, row or {}) if row else [
            {"outcome": "preserve_exact", "probability": 1.0, "mode": "exact"}
        ]
    if tid in trade_ids:
        decision = trade_by_id.get(tid)
        return "historical_trade_policy", v1.trade_outcomes(event, decision or {}, expansion_by_id.get(tid)) if decision else [
            {"outcome": "preserve_exact", "probability": 1.0, "mode": "exact"}
        ]
    if tid in required:
        return "required_branch", [
            {"outcome": "preserve_if_legal", "probability": 1.0, "mode": "exact"},
            {"outcome": "forced_no_action", "probability": 0.0, "mode": "no_action"},
        ]
    return ("structurally_stable" if tid in stable else "invariant"), [
        {"outcome": "preserve_historical", "probability": 1.0, "mode": "exact"},
        {"outcome": "legality_forced_no_action", "probability": 0.0, "mode": "no_action"},
    ]


def run(
    scenario_path: Path,
    *,
    particles: int = DEFAULT_PARTICLES,
    seed: int = DEFAULT_SEED,
) -> Path:
    if particles <= 0:
        raise ah.AlternateHistoryError("0.7b particles must be positive")
    adapter = FSFFLHistoricalAdapter()
    scenario = ah.scenario_from_json(adapter, load(scenario_path))
    policies = policy_inputs(adapter, scenario, scenario_path)
    triage, usage, trade, expansion = (
        policies["triage"], policies["usage"], policies["trade"], policies["expansion"]
    )

    usage_by_id = {str(x.get("transaction_id")): x for x in (usage.get("decisions") or [])}
    trade_by_id = {str(x.get("transaction_id")): x for x in (trade.get("decisions") or [])}
    expansion_by_id = {str(x.get("transaction_id")): x for x in (expansion.get("expansions") or [])}
    queues = triage.get("queues") or {}
    required = {str(x) for x in queues.get("required_branch_transaction_ids") or []}
    usage_ids = {str(x) for x in queues.get("historical_usage_policy_transaction_ids") or []}
    trade_ids = {str(x) for x in queues.get("historical_gm_required_transaction_ids") or []}
    stable = {str(x) for x in queues.get("structurally_stable_transaction_ids") or []}

    root = ah.apply_fork(ah.reconstruct_state(adapter, scenario.fork_timestamp_ms), scenario)
    groups = [ParticleGroup(particles, v1.serial(root), [[]])]
    rng = random.Random(seed)
    audits: List[Dict[str, Any]] = []
    invariant_fast_path_events = 0
    total_merge_events = 0
    max_unique_states = 1
    event_count = 0

    for event in adapter.completed_events():
        created = int(event.get("created") or 0)
        if created < scenario.fork_timestamp_ms:
            continue
        event_count += 1
        tid = str(event.get("transaction_id") or "")
        kind, proposed = proposed_outcomes(
            event, tid, usage_ids, trade_ids, required, stable,
            usage_by_id, trade_by_id, expansion_by_id,
        )

        next_groups: List[ParticleGroup] = []
        actual_branching = False
        legality_changed = False
        for group in groups:
            outcomes = v1.branch_specific_outcomes(group.state, event, proposed)
            if len(outcomes) > 1:
                actual_branching = True
            if len(outcomes) != 1 or outcomes[0].get("mode") != "exact":
                legality_changed = True
            counts = multinomial_counts(
                group.count,
                [float(x.get("probability") or 0.0) for x in outcomes],
                rng,
            )
            if sum(counts) != group.count:
                raise ah.AlternateHistoryError(
                    f"0.7b particle conservation failed at {tid}: {sum(counts)} != {group.count}"
                )
            for idx, (outcome, count) in enumerate(zip(outcomes, counts)):
                if count <= 0:
                    continue
                state = v1.apply_outcome(group.state, event, outcome)
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
                traces = [(list(t) + [step]) for t in (group.traces or [[]])[:MAX_TRACES_PER_GROUP]]
                next_groups.append(ParticleGroup(count, state, traces))

        if not next_groups:
            raise ah.AlternateHistoryError(f"0.7b produced zero particle groups at {tid}")
        if sum(x.count for x in next_groups) != particles:
            raise ah.AlternateHistoryError(
                f"0.7b global particle conservation failed before merge at {tid}"
            )

        # For a truly deterministic invariant mapping there is no distributional
        # benefit to hashing/merging immediately. Defer until a sensitive boundary.
        if kind == "invariant" and not actual_branching and not legality_changed:
            groups = next_groups
            invariant_fast_path_events += 1
            continue

        groups, merged_particles = merge_groups(next_groups)
        total_merge_events += 1 if merged_particles > 0 else 0
        max_unique_states = max(max_unique_states, len(groups))
        if sum(x.count for x in groups) != particles:
            raise ah.AlternateHistoryError(f"0.7b particle conservation failed after merge at {tid}")
        audits.append({
            "transaction_id": tid,
            "timestamp_ms": created,
            "kind": kind,
            "actual_branching": actual_branching,
            "legality_changed": legality_changed,
            "unique_states_after_event": len(groups),
            "particles_in_merged_duplicates": merged_particles,
        })

    groups, merged_particles = merge_groups(groups)
    max_unique_states = max(max_unique_states, len(groups))
    final_particles = sum(x.count for x in groups)
    if final_particles != particles:
        raise ah.AlternateHistoryError(
            f"0.7b final particle conservation failed: {final_particles} != {particles}"
        )

    focus = str(scenario.focus_roster_id)
    player_focus_counts: Dict[str, int] = {}
    pick_owner_counts: Dict[str, Dict[str, int]] = {}
    for group in groups:
        for pid in (group.state.get("roster_players") or {}).get(focus, []) or []:
            player_focus_counts[str(pid)] = player_focus_counts.get(str(pid), 0) + group.count
        for key, rid in (group.state.get("pick_owners") or {}).items():
            pick_owner_counts.setdefault(str(key), {}).setdefault(str(rid), 0)
            pick_owner_counts[str(key)][str(rid)] += group.count

    focus_players = [
        {"player_id": pid, "probability_on_focus_roster": round(count / particles, 6), "particles": count}
        for pid, count in player_focus_counts.items()
    ]
    focus_players.sort(key=lambda x: (-x["probability_on_focus_roster"], x["player_id"]))

    report = {
        "model_version": "Fantasy-Alternate-History-0.7b-grouped-particle-replay",
        "scenario_id": scenario.scenario_id,
        "design_invariants": {
            "completed_nfl_history_is_immutable": True,
            "branch_specific_accumulated_state_legality": True,
            "current_gm3_numeric_values_used": False,
            "current_market_values_used": False,
            "future_nfl_outcomes_used": False,
            "probability_mass_pruned": False,
            "equal_weight_particle_count_conserved": True,
            "identical_states_grouped_for_efficiency": True,
            "seeded_reproducibility": True,
        },
        "configuration": {"particles": particles, "seed": seed},
        "summary": {
            "events_replayed": event_count,
            "final_particles": final_particles,
            "final_probability_mass": round(final_particles / particles, 10),
            "final_unique_states": len(groups),
            "max_unique_states": max_unique_states,
            "audited_sensitive_or_legality_events": len(audits),
            "invariant_fast_path_events": invariant_fast_path_events,
            "merge_boundaries_with_duplicates": total_merge_events,
        },
        "event_audit": audits,
        "focus_roster_player_probabilities": focus_players,
        "pick_owner_probabilities": {
            key: {rid: round(count / particles, 6) for rid, count in owners.items()}
            for key, owners in pick_owner_counts.items()
        },
        "representative_state_groups": [
            {
                "particles": group.count,
                "probability": round(group.count / particles, 8),
                "focus_roster_players": sorted((group.state.get("roster_players") or {}).get(focus, [])),
                "pick_owners": group.state.get("pick_owners") or {},
                "trace": (group.traces or [[]])[0],
            }
            for group in sorted(groups, key=lambda x: x.count, reverse=True)[:20]
        ],
    }
    out = ah.write_isolated_json(
        f"results/{scenario.scenario_id}/multiseason_particle_replay_0_7b.json",
        report,
    )
    print(out)
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Alternate History 0.7b grouped particle replay")
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--particles", type=int, default=DEFAULT_PARTICLES)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    run(args.scenario, particles=args.particles, seed=args.seed)


if __name__ == "__main__":
    main()
