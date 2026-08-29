#!/usr/bin/env python3
"""Robust candidate discovery across provisional package-value curves.

The exact GM-2.2 consolidation curve is not empirically calibrated and has
material leverage over which packages reach GM-3 counterfactual simulation.
Rather than replacing it with another unsupported coefficient set, this module
runs bounded candidate discovery under three plausible curve shapes and ranks
candidates by cross-curve robustness.

This is a SEARCH/RECALL layer. It does not claim any curve is economically
correct and it does not alter projection behavior.
"""
from __future__ import annotations

import copy
from collections import defaultdict

MODEL_VERSION = "FSFFL-Package-Curve-Robust-Discovery-1.0"
CURVES = {
    "production_steep": [1.0, 0.78, 0.62, 0.50, 0.42],
    "shallow": [1.0, 0.92, 0.84, 0.78, 0.72],
    "neutral": [1.0, 1.0, 1.0, 1.0, 1.0],
}


def _target_key(row):
    return str(row.get("target_asset_id") or "")


def _package_key(pkg):
    return tuple(sorted(map(str, pkg.get("focal_outgoing_asset_ids") or [])))


def _rank_vector(rank_by_curve, curve_names, missing_rank):
    """Minimax-first, nonparametric robustness ordering.

    Presence across more curves wins first. Among equally present candidates,
    prefer the candidate with the best worst-case rank, then best total rank,
    then best single-curve rank. These are rank statistics, not economic
    coefficients.
    """
    ranks = [int(rank_by_curve.get(name, missing_rank)) for name in curve_names]
    present = sum(name in rank_by_curve for name in curve_names)
    return (-present, max(ranks), sum(ranks), min(ranks))


def _run_curve(core, original, uid, ctx, profile_by_uid, weights):
    prior = list(core.GM22["package_weights"])
    try:
        core.GM22["package_weights"] = list(weights)
        return original(uid, ctx=ctx, profile_by_uid=profile_by_uid)
    finally:
        core.GM22["package_weights"] = prior


def install(core):
    """Patch GM-2.2 opportunity generation with robust multi-curve discovery."""
    original = core.build_universal_trade_opportunities
    curve_names = tuple(CURVES)

    def robust_trade_opportunities(uid, ctx=None, profile_by_uid=None):
        runs = {
            name: _run_curve(core, original, uid, ctx, profile_by_uid, weights)
            for name, weights in CURVES.items()
        }
        if any((payload or {}).get("error") for payload in runs.values()):
            return next(payload for payload in runs.values() if (payload or {}).get("error"))

        production = copy.deepcopy(runs["production_steep"])
        by_target = defaultdict(dict)
        target_ranks = defaultdict(dict)

        # Capture target and package rank under every curve.
        for curve, payload in runs.items():
            for trank, opp in enumerate(payload.get("opportunities") or [], 1):
                tid = _target_key(opp)
                if not tid:
                    continue
                target_ranks[tid][curve] = trank
                slot = by_target[tid]
                slot.setdefault("templates", {})[curve] = opp
                pranks = slot.setdefault("package_ranks", defaultdict(dict))
                ptemplates = slot.setdefault("package_templates", defaultdict(dict))
                for prank, pkg in enumerate(opp.get("best_candidate_packages") or [], 1):
                    pkey = _package_key(pkg)
                    if not pkey:
                        continue
                    pranks[pkey][curve] = prank
                    ptemplates[pkey][curve] = pkg

        target_rows = []
        target_missing = max(
            [len(p.get("opportunities") or []) for p in runs.values()] or [30]
        ) + 1
        for tid, slot in by_target.items():
            templates = slot["templates"]
            template = copy.deepcopy(
                templates.get("production_steep")
                or templates.get("shallow")
                or templates.get("neutral")
                or {}
            )
            tranks = target_ranks[tid]
            template["package_curve_discovery"] = {
                "model_version": MODEL_VERSION,
                "curve_ranks": dict(tranks),
                "curve_presence_count": len(tranks),
                "curve_count": len(curve_names),
                "single_curve_authoritative": False,
            }

            merged_packages = []
            all_pkeys = set(slot.get("package_ranks", {}))
            for pkey in all_pkeys:
                ranks = slot["package_ranks"][pkey]
                templates_p = slot["package_templates"][pkey]
                pkg = copy.deepcopy(
                    templates_p.get("production_steep")
                    or templates_p.get("shallow")
                    or templates_p.get("neutral")
                    or {}
                )
                pkg["package_curve_discovery"] = {
                    "model_version": MODEL_VERSION,
                    "curve_ranks": dict(ranks),
                    "curve_presence_count": len(ranks),
                    "curve_count": len(curve_names),
                    "single_curve_authoritative": False,
                    "production_curve_decision_score_retained_as_provisional_diagnostic": (
                        templates_p.get("production_steep") or {}
                    ).get("decision_score"),
                }
                merged_packages.append((pkey, pkg, ranks))

            package_missing = max(
                [len((x or {}).get("best_candidate_packages") or []) for x in templates.values()] or [10]
            ) + 1
            merged_packages.sort(
                key=lambda x: (
                    _rank_vector(x[2], curve_names, package_missing),
                    str(x[0]),
                )
            )
            template["best_candidate_packages"] = [x[1] for x in merged_packages[:10]]
            if template["best_candidate_packages"]:
                best = template["best_candidate_packages"][0]
                template["best_package_recommendation_band"] = best.get("recommendation_band")
                template["best_package_decision_score"] = best.get("decision_score")
            target_rows.append((tid, template, tranks))

        target_rows.sort(
            key=lambda x: (
                _rank_vector(x[2], curve_names, target_missing),
                str(x[0]),
            )
        )
        production["opportunities"] = [x[1] for x in target_rows]
        production["package_curve_governance"] = {
            "model_version": MODEL_VERSION,
            "candidate_discovery_curves": copy.deepcopy(CURVES),
            "single_package_curve_authoritative": False,
            "curve_disagreement_treated_as_model_uncertainty": True,
            "robust_ranking_basis": "presence_then_minimax_rank_then_rank_sum",
            "economic_coefficients_fitted": False,
            "projection_behavior_changed": False,
            "final_trade_judgment_requires_downstream_counterfactual_or_trade_report_validation": True,
        }
        production["methodology_note"] = (
            str(production.get("methodology_note") or "")
            + " Candidate discovery is robust across multiple provisional package curves; "
              "no single consolidation curve is treated as authoritative."
        ).strip()
        return production

    core.build_universal_trade_opportunities = robust_trade_opportunities
    core.PACKAGE_CURVE_ROBUSTNESS = {
        "installed": True,
        "model_version": MODEL_VERSION,
        "curves": copy.deepcopy(CURVES),
        "single_curve_authoritative": False,
    }
    return core
