#!/usr/bin/env python3
"""Accuracy-neutral runtime optimizations for Alternate History.

This module changes only redundant data movement/hash work. It does not alter
particle counts, branch probabilities, decision policies, historical inputs,
lineup logic, draft logic, or Simulator behavior.

The optimizations are installed explicitly by production/benchmark wrappers so
we can A/B them against the validated engine before folding them into core.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Tuple

import alternate_history_engine as ah
import run_fsffl_multiseason_branch_replay as branch_v1
import run_fsffl_multiseason_particle_replay_v3 as season_v3


def apply_preserving_ledger_cow(
    state_payload: Dict[str, Any],
    event: Dict[str, Any],
    outcome: Dict[str, Any],
) -> Dict[str, Any]:
    """Carry the immutable ledger by reference across transaction transitions.

    branch_v1.apply_outcome serializes only transaction state and never reads or
    mutates the season ledger. All ledger-writing paths in the season engine
    already copy the ledger before mutation, so deep-copying it for every
    transaction outcome is redundant.
    """
    ledger = state_payload.get(season_v3.LEDGER_KEY)
    new_state = branch_v1.apply_outcome(state_payload, event, outcome)
    new_state[season_v3.LEDGER_KEY] = ledger if ledger is not None else {}
    return new_state


def _state_key_with_memo(
    state: Dict[str, Any],
    ledger_hash_by_identity: Dict[int, str],
) -> str:
    ledger = state.get(season_v3.LEDGER_KEY) or {}
    identity = id(ledger)
    ledger_hash = ledger_hash_by_identity.get(identity)
    if ledger_hash is None:
        ledger_hash = ah.stable_hash(ledger)
        ledger_hash_by_identity[identity] = ledger_hash

    core = {
        "roster_players": {
            str(k): sorted(str(x) for x in (v or []))
            for k, v in sorted((state.get("roster_players") or {}).items())
        },
        "pick_owners": dict(sorted((state.get("pick_owners") or {}).items())),
        "faab": {
            str(k): float(v or 0.0)
            for k, v in sorted((state.get("faab") or {}).items())
        },
    }
    return f"{ah.stable_hash(core)}:{ledger_hash}"


def merge_groups_memoized(
    groups: Iterable[season_v3.SeasonParticleGroup],
) -> Tuple[list[season_v3.SeasonParticleGroup], int]:
    """Merge exact-equivalent states while hashing shared ledgers only once.

    Descendants of one branch share the same immutable ledger between scoring
    boundaries. The validated implementation serializes/hashes that identical
    growing ledger separately for every descendant. Identity memoization is
    local to this merge call, so it cannot become stale across a ledger write.
    """
    by_key: Dict[str, season_v3.SeasonParticleGroup] = {}
    ledger_hash_by_identity: Dict[int, str] = {}
    merged_particles = 0

    for group in groups:
        if group.count <= 0:
            continue
        key = _state_key_with_memo(group.state, ledger_hash_by_identity)
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = season_v3.SeasonParticleGroup(
                group.count,
                group.state,
                [list(t) for t in group.traces[: season_v3.MAX_TRACES_PER_GROUP]],
            )
            continue

        merged_particles += group.count
        existing.count += group.count
        for trace in group.traces:
            if len(existing.traces) >= season_v3.MAX_TRACES_PER_GROUP:
                break
            if trace not in existing.traces:
                existing.traces.append(list(trace))

    return list(by_key.values()), merged_particles


def install() -> None:
    """Install the accuracy-neutral runtime replacements for this process."""
    season_v3.apply_preserving_ledger = apply_preserving_ledger_cow
    season_v3.merge_groups = merge_groups_memoized
