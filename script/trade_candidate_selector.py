#!/usr/bin/env python3
"""Canonical trade candidate selector.

Mechanical extraction of the current v23/v21 selector semantics. This component
operates on an already-simulated viable candidate set and does not generate or
simulate trades.

Responsibilities:
- apply current state-policy preparation/ranking;
- keep only candidates beneficial under the continuous focal objective;
- deduplicate negotiation families;
- limit normal options per buyer;
- preserve a distinct swing candidate using the inherited v21 swing rule.

Production is not switched to this component until equivalence is proven.
"""
from __future__ import annotations

from collections import Counter

MODEL_VERSION = "FSFFL-Trade-Candidate-Selector-1.0"


def select_normal_four(viable, swing, family_key, state_policy, ranker, max_per_buyer=2):
    prepared = [
        row for row in state_policy.prepare_rows(list(viable or []), ranker)
        if state_policy.focal_state_beneficial(row)
    ]
    selected = []
    counts = Counter()
    used_families = set()
    swing_family = family_key(swing) if swing else None

    for row in prepared:
        fam = family_key(row)
        if swing_family and fam == swing_family:
            continue
        if fam in used_families:
            continue
        uid = str(row.get("buyer_user_id") or "")
        if counts[uid] >= max_per_buyer:
            continue
        selected.append(row)
        used_families.add(fam)
        counts[uid] += 1
        if len(selected) == 4:
            break
    return selected


def select_swing(viable, inherited_swing_selector, state_policy, ranker):
    prepared = state_policy.prepare_rows(list(viable or []), ranker)
    return inherited_swing_selector(prepared)
