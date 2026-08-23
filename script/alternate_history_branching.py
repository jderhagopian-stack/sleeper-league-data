#!/usr/bin/env python3
"""Probability-conserving branch manager for Fantasy Alternate History 0.7.

The policy layers can create many plausible downstream decisions. This module
provides the generic mechanics needed to propagate those choices efficiently:
- weighted branch expansion;
- exact-state merging before pruning;
- deterministic beam pruning;
- explicit accounting for retained and pruned probability mass;
- compact trace retention for later narrative reporting.

It does not contain FSFFL rules or decision policy. Callers supply state payloads
and transition functions, keeping the branching core league-agnostic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Tuple

import alternate_history_engine as ah


@dataclass
class WeightedBranch:
    branch_id: str
    probability: float
    state: Dict[str, Any]
    traces: List[List[Dict[str, Any]]] = field(default_factory=list)

    def signature(self) -> str:
        return ah.stable_hash(self.state)


@dataclass
class BranchBatch:
    branches: List[WeightedBranch]
    retained_mass: float
    pruned_mass: float
    input_mass: float
    merged_count: int = 0
    expanded_count: int = 0


TransitionFn = Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]]


def _normalized_outcomes(outcomes: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = [dict(x) for x in outcomes]
    if not rows:
        raise ah.AlternateHistoryError("Branch expansion requires at least one outcome")
    total = sum(max(0.0, float(x.get("probability") or 0.0)) for x in rows)
    if total <= 0.0:
        raise ah.AlternateHistoryError("Branch expansion outcome probabilities sum to zero")
    for row in rows:
        row["probability"] = max(0.0, float(row.get("probability") or 0.0)) / total
    return rows


def merge_equivalent(branches: Iterable[WeightedBranch], max_traces: int = 3) -> Tuple[List[WeightedBranch], int]:
    """Merge branches with exactly equivalent state while retaining trace examples."""
    grouped: Dict[str, WeightedBranch] = {}
    count = 0
    for branch in branches:
        sig = branch.signature()
        if sig not in grouped:
            grouped[sig] = WeightedBranch(
                branch_id=branch.branch_id,
                probability=float(branch.probability),
                state=branch.state,
                traces=list(branch.traces[:max_traces]),
            )
            continue
        count += 1
        target = grouped[sig]
        target.probability += float(branch.probability)
        for trace in branch.traces:
            if len(target.traces) >= max_traces:
                break
            if trace not in target.traces:
                target.traces.append(trace)
    rows = sorted(grouped.values(), key=lambda x: (-x.probability, x.branch_id))
    return rows, count


def prune_branches(
    branches: Iterable[WeightedBranch],
    *,
    max_branches: int = 256,
    min_probability: float = 1e-6,
) -> BranchBatch:
    rows = list(branches)
    input_mass = sum(float(x.probability) for x in rows)
    eligible = [x for x in rows if float(x.probability) >= float(min_probability)]
    eligible.sort(key=lambda x: (-x.probability, x.branch_id))
    kept = eligible[: max(1, int(max_branches))]
    retained = sum(float(x.probability) for x in kept)
    pruned = max(0.0, input_mass - retained)
    return BranchBatch(
        branches=kept,
        retained_mass=retained,
        pruned_mass=pruned,
        input_mass=input_mass,
    )


def expand_branches(
    branches: Iterable[WeightedBranch],
    *,
    event_key: str,
    outcomes: Iterable[Dict[str, Any]],
    transition: TransitionFn,
    max_branches: int = 256,
    min_probability: float = 1e-6,
    max_traces: int = 3,
) -> BranchBatch:
    """Expand, merge identical states, then prune while accounting for all mass."""
    source = list(branches)
    input_mass = sum(float(x.probability) for x in source)
    normalized = _normalized_outcomes(outcomes)
    expanded: List[WeightedBranch] = []

    for parent in source:
        parent_traces = parent.traces or [[]]
        for idx, outcome in enumerate(normalized):
            p = float(parent.probability) * float(outcome["probability"])
            if p <= 0.0:
                continue
            state = transition(parent.state, outcome)
            trace_step = {
                "event_key": str(event_key),
                "outcome": outcome.get("outcome") or outcome.get("name") or f"outcome_{idx}",
                "conditional_probability": float(outcome["probability"]),
            }
            traces = [(list(t) + [trace_step]) for t in parent_traces[:max_traces]]
            expanded.append(WeightedBranch(
                branch_id=f"{parent.branch_id}/{event_key}:{idx}",
                probability=p,
                state=state,
                traces=traces,
            ))

    merged, merged_count = merge_equivalent(expanded, max_traces=max_traces)
    batch = prune_branches(merged, max_branches=max_branches, min_probability=min_probability)
    batch.input_mass = input_mass
    batch.expanded_count = len(expanded)
    batch.merged_count = merged_count
    # Expansion itself must conserve the incoming mass; pruning is the only
    # permitted source of retained-mass loss.
    expanded_mass = sum(float(x.probability) for x in merged)
    if abs(expanded_mass - input_mass) > 1e-8:
        raise ah.AlternateHistoryError(
            f"Branch probability mass was not conserved: input={input_mass} expanded={expanded_mass}"
        )
    batch.pruned_mass = max(0.0, input_mass - batch.retained_mass)
    return batch


def root_branch(state: Dict[str, Any], branch_id: str = "root") -> WeightedBranch:
    return WeightedBranch(branch_id=branch_id, probability=1.0, state=state, traces=[[]])
