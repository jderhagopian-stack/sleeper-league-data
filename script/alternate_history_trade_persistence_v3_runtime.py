#!/usr/bin/env python3
"""Bug-fixed behavioral trade-persistence wrapper.

The v2 persistence layer delegates legality filtering to the historical branch
filter. That filter intentionally mutates the no-action probability row when it
redirects illegal trade mass. V2 then read historical intent from that mutated
row, which could erase all recoverable trade intent before an otherwise legal
near-equivalent package was considered.

This wrapper snapshots the proposal probabilities before legality filtering and
otherwise preserves the v2 policy, historical-data firewall, candidate search,
and exact-legal-trade behavior.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List

import alternate_history_trade_persistence_runtime as tp
import run_fsffl_multiseason_branch_replay as branch_v1
from run_fsffl_downstream_dependencies import event_legality


def branch_specific_outcomes_v3(
    state_payload: Dict[str, Any],
    event: Dict[str, Any],
    proposed: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    # Preserve the contemporaneous policy probabilities before the legacy
    # legality filter redirects illegal exact-trade mass into no-action.
    original_proposed = copy.deepcopy(proposed)
    base = tp._ORIGINAL(state_payload, event, copy.deepcopy(proposed))
    if not tp._is_trade_proposal(event, original_proposed):
        return base

    state = branch_v1.to_state(state_payload)
    exact_legal, _ = event_legality(state, event)
    if exact_legal:
        # Exact legal historical trades remain governed bit-for-bit by the old
        # branch filter. Persistence only repairs an unavailable payment leg.
        return base

    equivalents = tp._equivalent_events(state_payload, event)
    if not equivalents:
        return base

    original_no = sum(
        float(x.get("probability") or 0.0)
        for x in original_proposed
        if x.get("mode") == "no_action"
    )
    original_intent = max(0.0, min(1.0, 1.0 - original_no))
    legal_trade_mass = sum(
        float(x.get("probability") or 0.0)
        for x in base
        if x.get("mode") != "no_action"
    )
    need = tp._need_similarity(state_payload, event)
    competitive = tp._competitive_similarity(state_payload, event)
    context = max(0.0, min(1.0, 0.55 * need + 0.45 * competitive))
    persistence_fraction = 0.55 + 0.35 * context
    desired_trade_mass = max(
        legal_trade_mass,
        original_intent * persistence_fraction,
    )
    recovery = max(
        0.0,
        min(1.0 - legal_trade_mass, desired_trade_mass - legal_trade_mass),
    )
    if recovery <= 1e-12:
        return base

    rows = [dict(x) for x in base]
    no_rows = [x for x in rows if x.get("mode") == "no_action"]
    if not no_rows:
        return base
    no_row = no_rows[0]
    available_no = float(no_row.get("probability") or 0.0)
    recovery = min(recovery, available_no)
    if recovery <= 1e-12:
        return base
    no_row["probability"] = available_no - recovery

    denom = sum(
        max(0.0, float(x.get("weight") or 0.0)) for x in equivalents
    ) or 1.0
    tid = str(event.get("transaction_id") or "")
    for idx, eq in enumerate(equivalents, 1):
        p = recovery * max(0.0, float(eq.get("weight") or 0.0)) / denom
        if p <= 0.0:
            continue
        rows.append({
            "outcome": "behaviorally_persistent_equivalent_trade",
            "probability": p,
            "mode": "event",
            "event": eq["event"],
            "package_id": f"{tid}:persistent-equivalent:{idx}",
            "equivalent_trade": True,
            "persistence_context": {
                "need_similarity": round(need, 4),
                "competitive_state_similarity": round(competitive, 4),
                "combined_context": round(context, 4),
                "persistence_fraction": round(persistence_fraction, 4),
                "replacements": eq.get("replacements") or [],
            },
        })
    return branch_v1.normalize(rows)


def install() -> None:
    if branch_v1.branch_specific_outcomes is branch_specific_outcomes_v3:
        return
    branch_v1.branch_specific_outcomes = branch_specific_outcomes_v3
