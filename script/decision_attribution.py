#!/usr/bin/env python3
"""Authoritative Shared Decision Utility attribution and reconciliation.

This module does not create a second score. It exposes the exact calculation
performed by decision_utility.py in a stable, plain-language structure and
provides guardrails for integration/CI audits.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Dict, Iterable, List

SCRIPT = Path(__file__).resolve().parent
MODEL_VERSION = "FSFFL-Decision-Attribution-1.2"


def _load_utility():
    path = SCRIPT / "decision_utility.py"
    spec = importlib.util.spec_from_file_location("fsffl_decision_utility_attribution", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to import {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _r(v, n=2):
    try:
        return round(float(v), n)
    except (TypeError, ValueError):
        return 0.0


def reconcile(sim: Dict[str, Any]) -> Dict[str, Any]:
    utility = _load_utility()
    scored = utility.score(sim)
    components = dict(scored.get("components") or {})
    primitives = dict(scored.get("primitive_blocks") or {})
    authorization = dict(scored.get("incremental_channel_authorization") or {})
    component_sum = round(sum(float(v or 0.0) for v in components.values()), 2)
    final_score = _r(scored.get("score"))
    tol = 0.03

    channels = []
    labels = {
        "current": "current competitive effect",
        "future": "future franchise-value effect",
        "liquidity": "liquidity effect",
        "resilience": "resilience effect",
    }
    sources = {
        "current": (
            "unweighted median of same-unit current-season evidence: canonical "
            "Simulator league-relative outcomes, transaction market-redraft delta, "
            "and optimized-starter redraft delta when available"
        ),
        "future": (
            "GM3 market-dynasty delta with governed bounded provisional package-concentration "
            "transform applied only to negotiated trade legs; non-trade future effects preserved once"
        ),
        "liquidity": "GM3 residual liquidity delta, only when independently authorized",
        "resilience": "GM3 residual resilience delta, only when independently authorized",
    }
    for key in ("current", "future", "liquidity", "resilience"):
        active = bool(authorization.get(key, True))
        channels.append({
            "channel": key,
            "plain_language": labels[key],
            "authoritative_source": sources[key],
            "authorized_for_final_utility": active,
            "primitive_value": _r(primitives.get(key)),
            "objective_weight": _r((scored.get("objective_weights") or {}).get(key), 6),
            "numeric_contribution": _r(components.get(key)),
            "diagnostic_only": not active,
        })

    return {
        "model_version": MODEL_VERSION,
        "shared_decision_utility_model_version": scored.get("model_version"),
        "final_shared_decision_utility": final_score,
        "channels": channels,
        "component_sum": component_sum,
        "reconciles": abs(component_sum - final_score) <= tol,
        "reconciliation_tolerance": tol,
        "suppressed_unauthorized_objective_weight": scored.get("suppressed_unauthorized_objective_weight") or {},
        "diagnostics": scored.get("diagnostics") or {},
        "calculation_is_explanatory_only": True,
        "creates_independent_score": False,
    }


def audit_batch(attributions: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = list(attributions)
    issues: List[Dict[str, Any]] = []
    for i, row in enumerate(rows):
        if not row.get("reconciles"):
            issues.append({"type": "UTILITY_RECONCILIATION_MISMATCH", "row": i})

    for channel in ("current", "future", "liquidity", "resilience"):
        active_rows = [
            r for r in rows
            if any(
                c.get("channel") == channel and c.get("authorized_for_final_utility")
                for c in (r.get("channels") or [])
            )
        ]
        if len(active_rows) >= 2:
            values = [
                abs(float(next(
                    c.get("primitive_value") or 0.0
                    for c in (r.get("channels") or [])
                    if c.get("channel") == channel
                )))
                for r in active_rows
            ]
            if all(v <= 1e-12 for v in values):
                issues.append({
                    "type": "AUTHORIZED_MAJOR_UTILITY_BLOCK_ALWAYS_ZERO",
                    "channel": channel,
                    "observations": len(active_rows),
                })

    return {
        "model_version": MODEL_VERSION,
        "observations": len(rows),
        "issues": issues,
        "passed": not issues,
    }
