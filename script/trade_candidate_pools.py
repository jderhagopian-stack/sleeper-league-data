#!/usr/bin/env python3
"""Canonical trade candidate-pool organization.

Extracted from historical Counter Market Sweep v1.21. This component does not
manufacture or simulate candidates. It takes the retained candidate frontier
produced upstream and organizes it into:
- suggested_counteroffers: same current partner only, max 2, distinct families;
- market_sweep_alternatives: other owners only, max 5, distinct families.

Eligibility semantics are intentionally preserved from v1.21: positive
continuous post-simulation score is required, and contenders retain the hard
championship-equity constraint. Descriptive state labels do not create a
future-value cliff.
"""
from __future__ import annotations

MODEL_VERSION = "FSFFL-Trade-Candidate-Pools-1.2"


def sf(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def key(row):
    return (
        str(row.get("buyer_user_id") or ""),
        tuple(sorted(map(str, row.get("outgoing_assets") or []))),
        tuple(sorted(map(str, row.get("return_assets") or []))),
    )


def _economic_asset_signature(asset):
    aid = str(asset.get("asset_id") or "")
    if asset.get("asset_type") != "pick":
        return ("asset", aid)
    pp = asset.get("pick_profile") or {}
    return (
        "pick",
        int(pp.get("season") or 0),
        int(pp.get("round") or 0),
        round(sf(asset.get("market_dynasty")), 2),
        str(pp.get("most_likely_tier") or ""),
    )


def _economic_side_signature(row, side):
    strategic = ((row.get("simulation") or {}).get("strategic") or {})
    assets = strategic.get(side) or []
    if assets:
        return tuple(sorted(_economic_asset_signature(x) for x in assets))
    return tuple(sorted(map(str, row.get("outgoing_assets" if side == "sent" else "return_assets") or [])))


def family(row):
    return (
        str(row.get("buyer_user_id") or ""),
        _economic_side_signature(row, "sent"),
        _economic_side_signature(row, "received"),
    )


def focal_ok(row):
    if sf(row.get("post_sim_score")) <= 0:
        return False
    state = str(
        (((row.get("simulation") or {}).get("strategic") or {}).get("objective_state"))
        or row.get("focal_current_state")
        or ""
    )
    if state in {"contender", "elite_contender"} and row.get("championship_equity_constraint") == "FAIL":
        return False
    return True


def enrich_counter(row):
    out = dict(row)
    br = out.get("buyer_rationality") or {}
    acceptance = br.get("heuristic_acceptance_fit") or out.get("acceptance_likelihood")
    plausibility = str(out.get("plausibility") or "UNRATED")
    out["counter_validation_status"] = (
        "VALIDATED_ACCEPTANCE"
        if acceptance in {"HIGH", "MEDIUM"}
        else "STRUCTURALLY_PLAUSIBLE_ACCEPTANCE_UNVALIDATED"
    )
    out["acceptance_likelihood"] = acceptance
    out["counter_confidence_note"] = (
        f"{acceptance} acceptance fit"
        if acceptance
        else f"{plausibility} structural plausibility; buyer acceptance not fully validated"
    )
    out["report_role"] = "SUGGESTED_COUNTEROFFER"
    return out


def apply_to_report(report):
    """Organize an existing candidate frontier exactly as v1.21 did."""
    current = report.get("current_offer_evaluation") or {}
    partner = str(
        report.get("current_offer_partner_user_id")
        or current.get("buyer_user_id")
        or ""
    )
    current_key = key(current)

    counter_pool = []
    for row in (
        (report.get("same_partner_counteroffers") or [])
        + [report.get("best_same_partner") or {}]
        + (report.get("realistic_counter_alternatives") or [])
        + (report.get("top_5_alternatives") or [])
    ):
        if (
            not row
            or str(row.get("buyer_user_id") or "") != partner
            or key(row) == current_key
            or not focal_ok(row)
        ):
            continue
        counter_pool.append(row)

    incoming_offer = str(report.get("offer_direction") or "") == "INCOMING_OFFER"
    counter_pool.sort(
        key=lambda x: (
            1 if (
                incoming_offer
                and x.get("counter_strategy") == "OFFEROR_ANCHORED_TARGET_PRESERVING_CONCESSION"
            ) else 0,
            1 if ((x.get("buyer_rationality") or {}).get("heuristic_acceptance_fit") in {"HIGH", "MEDIUM"}) else 0,
            sf((x.get("negotiation_ranking") or {}).get("score")),
            sf(x.get("post_sim_score")),
        ),
        reverse=True,
    )

    counters = []
    seen = set()
    for row in counter_pool:
        fam = family(row)
        if fam in seen:
            continue
        seen.add(fam)
        counters.append(enrich_counter(row))
        if len(counters) == 2:
            break

    market_pool = []
    for row in (
        (report.get("top_5_alternatives") or [])
        + (report.get("realistic_counter_alternatives") or [])
    ):
        if (
            not row
            or str(row.get("buyer_user_id") or "") == partner
            or not focal_ok(row)
        ):
            continue
        market_pool.append(row)

    market_pool.sort(
        key=lambda x: (
            sf((x.get("negotiation_ranking") or {}).get("score")),
            sf(x.get("post_sim_score")),
        ),
        reverse=True,
    )

    market = []
    seen = set()
    for row in market_pool:
        fam = family(row)
        if fam in seen:
            continue
        seen.add(fam)
        market.append(row)
        if len(market) == 5:
            break

    report["suggested_counteroffers"] = counters
    report["market_sweep_alternatives"] = market
    report["counteroffer_count"] = len(counters)
    report["market_sweep_alternative_count"] = len(market)
    report.setdefault("candidate_counts", {}).update({
        "suggested_counteroffers": len(counters),
        "market_sweep_alternatives": len(market),
    })
    report.setdefault("policy", {}).update({
        "trade_candidate_pool_model_version": MODEL_VERSION,
        "suggested_counteroffers_max": 2,
        "suggested_counteroffers_same_partner_only": True,
        "suggested_counteroffers_never_padded": True,
        "economically_identical_pick_packages_deduplicated": True,
        "pick_deduplication_requires_same_season_round_market_value_and_tier": True,
        "market_sweep_max": 5,
        "market_sweep_excludes_current_partner": True,
        "market_sweep_never_padded": True,
        "counter_and_market_pools_separate": True,
        "descriptive_state_labels_create_candidate_eligibility_cliffs": False,
        "continuous_state_aware_score_controls_focal_option_eligibility": True,
        "offer_origin_aware_counter_prioritization": True,
        "incoming_offer_target_preserving_concessions_prioritized": incoming_offer,
        "observed_current_offer_willingness_not_treated_as_counter_acceptance_probability": True,
    })
    report.setdefault("simulation", {})["execution_path"] = (
        str((report.get("simulation") or {}).get("execution_path") or "")
        + "_plus_counter_market_pool_split"
    )
    return {"counteroffer_count": len(counters), "market_sweep_alternative_count": len(market)}
