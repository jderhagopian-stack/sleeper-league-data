#!/usr/bin/env python3
"""Self-checks for the league-agnostic Alternate History 0.7 branch manager."""

from __future__ import annotations

import json

from alternate_history_branching import expand_branches, root_branch


def main() -> None:
    root = root_branch({"counter": 0})

    # Two distinct decision labels intentionally map to the same resulting
    # state. They must merge before pruning and conserve probability mass.
    first = expand_branches(
        [root],
        event_key="merge_test",
        outcomes=[
            {"outcome": "a", "probability": 0.4, "delta": 1},
            {"outcome": "b", "probability": 0.6, "delta": 1},
        ],
        transition=lambda state, outcome: {"counter": state["counter"] + outcome["delta"]},
        max_branches=16,
    )
    assert len(first.branches) == 1, first
    assert abs(first.branches[0].probability - 1.0) < 1e-9, first
    assert first.merged_count == 1, first
    assert abs(first.pruned_mass) < 1e-9, first

    # Create a wider decision and force beam pruning. The dropped probability
    # must be reported rather than silently renormalized away.
    second = expand_branches(
        first.branches,
        event_key="prune_test",
        outcomes=[
            {"outcome": "x", "probability": 0.50, "delta": 1},
            {"outcome": "y", "probability": 0.30, "delta": 2},
            {"outcome": "z", "probability": 0.20, "delta": 3},
        ],
        transition=lambda state, outcome: {"counter": state["counter"] + outcome["delta"]},
        max_branches=2,
    )
    assert len(second.branches) == 2, second
    assert abs(second.retained_mass - 0.80) < 1e-9, second
    assert abs(second.pruned_mass - 0.20) < 1e-9, second

    print(json.dumps({
        "status": "PASS",
        "merge_test_probability": first.branches[0].probability,
        "merged_count": first.merged_count,
        "prune_test_retained_mass": second.retained_mass,
        "prune_test_pruned_mass": second.pruned_mass,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
