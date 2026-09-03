#!/usr/bin/env python3
"""Trade-report context enrichment.

Adds presentation-ready explanations that are derived from existing governed
model outputs without changing trade valuation:
- offer origin / observed willingness context;
- explicit long-term value vs incremental asset-liquidity decomposition;
- future-pick outlook using the canonical Simulator competitive ordering before
  and after the trade.

Future-pick tiers are directional ranges, not exact rookie-draft slot forecasts.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

MODEL_VERSION = "FSFFL-Trade-Report-Context-1.0"
DATA = Path("data")


def sf(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def load_json(path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def _latest_standings():
    candidates = []
    root = DATA / "simulator"
    if root.exists():
        for p in root.glob("*/outputs/standings_projection.json"):
            try:
                season = int(p.parts[-3])
            except Exception:
                continue
            candidates.append((season, p))
    if not candidates:
        return None, []
    season, path = max(candidates)
    payload = load_json(path, {}) or {}
    return season, list(payload.get("teams") or [])


def _asset_map():
    payload = load_json(DATA / "fsffl_asset_values.json", {}) or {}
    return {
        str(x.get("asset_id")): x
        for x in (payload.get("picks") or [])
        if x.get("asset_id")
    }


def _order_key(row):
    return (
        sf(row.get("championship_probability")),
        sf(row.get("bye_probability")),
        sf(row.get("playoff_probability")),
        sf(row.get("expected_wins")),
    )


def _replace_rows(baseline, replacements):
    out = []
    for row in baseline:
        uid = str(row.get("user_id") or "")
        out.append(dict(replacements.get(uid) or row))
    return out


def _percentiles(rows):
    ordered = sorted(rows, key=_order_key)
    n = len(ordered)
    return {
        str(row.get("user_id")): (i / (n - 1) if n > 1 else 0.5)
        for i, row in enumerate(ordered)
        if row.get("user_id") is not None
    }


def _tier(p):
    if p < 1.0 / 3.0:
        return "early"
    if p > 2.0 / 3.0:
        return "late"
    return "mid"


def _slot_range(round_no, tier, team_count):
    width = max(1, math.ceil(team_count / 3))
    if tier == "early":
        lo, hi = 1, min(team_count, width)
    elif tier == "mid":
        lo, hi = min(team_count, width + 1), min(team_count, 2 * width)
    else:
        lo, hi = min(team_count, 2 * width + 1), team_count
    return f"{round_no}.{lo:02d}-{round_no}.{hi:02d}"


def _expected_seed(row):
    probs = row.get("seed_probabilities") or {}
    total = sum(sf(v) for v in probs.values())
    if total <= 0:
        return None
    return sum(int(k) * sf(v) for k, v in probs.items()) / total


def _liquidity_contribution(row):
    base = sf(row.get("base_franchise_value") or row.get("market_dynasty"))
    if row.get("asset_type") == "pick":
        pp = row.get("pick_profile") or {}
        if pp.get("liquidity_incremental_value_authorized") is False:
            return 0.0, "PICK_LIQUIDITY_ALREADY_EMBEDDED_IN_MARKET_VALUE"
    return base * sf(row.get("liquidity_score")), "INCREMENTAL_ASSET_LIQUIDITY"


def liquidity_context(report):
    cur = report.get("current_offer_evaluation") or {}
    strategic = ((cur.get("simulation") or {}).get("strategic") or {})
    sent, received = [], []
    for side, target in (("sent", sent), ("received", received)):
        for row in strategic.get(side) or []:
            contribution, basis = _liquidity_contribution(row)
            target.append({
                "asset_id": row.get("asset_id"),
                "name": row.get("name"),
                "asset_type": row.get("asset_type"),
                "base_franchise_value": round(sf(row.get("base_franchise_value")), 2),
                "liquidity_score": round(sf(row.get("liquidity_score")), 4),
                "incremental_liquidity_contribution": round(contribution, 2),
                "basis": basis,
            })
    return {
        "metric_label": "Incremental Asset Liquidity",
        "delta": round(sf(strategic.get("liquidity_value_delta")), 2),
        "definition": (
            "Additional moveability value not already represented by league-wide dynasty market value."
        ),
        "pick_treatment": (
            "Future-pick liquidity is not added a second time when governance marks it as already embedded in the pick's market value."
        ),
        "sent_components": sent,
        "received_components": received,
    }


def future_pick_outlook(report):
    season, baseline = _latest_standings()
    if not baseline:
        return []
    cur = report.get("current_offer_evaluation") or {}
    sim = cur.get("simulation") or {}
    focus_uid = str(report.get("focus_user_id") or "")
    partner_uid = str(report.get("current_offer_partner_user_id") or "")
    replacements = {}
    if focus_uid and sim.get("focus_after"):
        replacements[focus_uid] = sim.get("focus_after")
    if partner_uid and sim.get("buyer_after"):
        replacements[partner_uid] = sim.get("buyer_after")
    post_rows = _replace_rows(baseline, replacements)
    pre_pct = _percentiles(baseline)
    post_pct = _percentiles(post_rows)
    pre_by_uid = {str(x.get("user_id")): x for x in baseline}
    post_by_uid = {str(x.get("user_id")): x for x in post_rows}
    meta = _asset_map()
    strategic = sim.get("strategic") or {}
    picks = [
        (side, x)
        for side in ("sent", "received")
        for x in (strategic.get(side) or [])
        if x.get("asset_type") == "pick"
    ]
    out = []
    for side, row in picks:
        aid = str(row.get("asset_id") or "")
        m = meta.get(aid) or {}
        uid = str(m.get("original_owner_user_id") or "")
        if not uid or uid not in pre_pct or uid not in post_pct:
            continue
        rnd = int(m.get("round") or ((row.get("pick_profile") or {}).get("round")) or 0)
        pre_tier = _tier(pre_pct[uid])
        post_tier = _tier(post_pct[uid])
        pre_row, post_row = pre_by_uid.get(uid) or {}, post_by_uid.get(uid) or {}
        changed = any(
            abs(sf(post_row.get(k)) - sf(pre_row.get(k))) > 1e-9
            for k in ("expected_wins", "playoff_probability", "bye_probability", "championship_probability")
        )
        out.append({
            "asset_id": aid,
            "trade_side": side,
            "name": row.get("name") or m.get("name") or aid,
            "season": m.get("season"),
            "round": rnd,
            "original_owner_user_id": uid,
            "original_owner_team": m.get("original_owner_team"),
            "current_market_tier": m.get("projected_pick_tier"),
            "pre_trade_projected_tier": pre_tier,
            "post_trade_projected_tier": post_tier,
            "pre_trade_competitive_percentile": round(pre_pct[uid], 4),
            "post_trade_competitive_percentile": round(post_pct[uid], 4),
            "post_trade_tier_slot_range": _slot_range(rnd, post_tier, len(baseline)) if rnd else None,
            "pre_trade_expected_wins": round(sf(pre_row.get("expected_wins")), 3),
            "post_trade_expected_wins": round(sf(post_row.get("expected_wins")), 3),
            "pre_trade_expected_seed": (
                None if _expected_seed(pre_row) is None else round(_expected_seed(pre_row), 2)
            ),
            "post_trade_expected_seed": (
                None if _expected_seed(post_row) is None else round(_expected_seed(post_row), 2)
            ),
            "trade_changes_original_owner_projection": changed,
            "projection_basis": (
                f"{season} canonical Simulator competitive ordering used as the current point estimate for future-pick quality; tier range is directional, not an exact rookie-draft slot forecast."
            ),
            "confidence": (row.get("pick_profile") or {}).get("confidence"),
        })
    return out




def _decision_channel(row, channel):
    attr=(row.get("decision_attribution") or {})
    for item in attr.get("channels") or []:
        if str(item.get("channel") or "")==str(channel):
            return item
    return {}


def _package_prior_profile(row):
    attr=row.get("decision_attribution") or {}
    scores=attr.get("package_concentration_prior_scores") or {}
    robustness=attr.get("package_concentration_prior_range_decision_robustness")
    return {
        "mild_score": scores.get("mild"),
        "center_score": scores.get("center"),
        "strong_score": scores.get("strong"),
        "robustness": robustness,
        "sensitive_to_prior_range": robustness=="SENSITIVE_TO_PRIOR_RANGE",
    }


def recommendation_profile(report):
    action = str(report.get("recommended_next_action") or "")
    cur = report.get("current_offer_evaluation") or {}
    sim = cur.get("simulation") or {}
    d = sim.get("focus_delta") or {}
    attr = cur.get("decision_attribution") or {}
    future = sf((_decision_channel(cur, "future") or {}).get("primitive_value"))
    overall = sf(attr.get("final_shared_decision_utility"), sf(cur.get("shared_decision_utility_score")))
    package = _package_prior_profile(cur)
    competitive_up = (
        sf(d.get("expected_wins")) >= 0
        and sf(d.get("playoff_probability")) >= 0
        and sf(d.get("championship_probability")) >= 0
    )
    future_up = future >= 0
    overall_up = overall >= 0

    if package["sensitive_to_prior_range"]:
        sensitivity_note = (
            " The overall recommendation changes somewhere across the governed package-concentration "
            "prior range, so treat it as lower-confidence until that prior is better calibrated."
        )
    elif package.get("robustness") == "ROBUST_POSITIVE_ACROSS_PRIOR_RANGE":
        sensitivity_note = " The overall value stays positive across the governed package-concentration range."
    elif package.get("robustness") == "ROBUST_NEGATIVE_ACROSS_PRIOR_RANGE":
        sensitivity_note = " The overall value stays negative across the governed package-concentration range."
    else:
        sensitivity_note = ""

    if action == "ACCEPT_NOW":
        if competitive_up and future_up and overall_up:
            return {"label": "BROAD-BASED ACCEPT", "basis": "current and future-authority outputs point in the same direction." + sensitivity_note, "package_prior": package}
        if competitive_up and not future_up:
            return {"label": "WIN-NOW ACCEPT / FUTURE-VALUE TRADE-OFF", "basis": "competitive gains outweigh a package-adjusted future-asset-value cost." + sensitivity_note, "package_prior": package}
        return {"label": "ACCEPT WITH MIXED TRADE-OFFS", "basis": "the governed utility is positive despite conflicting component directions." + sensitivity_note, "package_prior": package}
    if action == "COUNTER_CURRENT_OFFEROR":
        return {"label": "COUNTER FOR BETTER TERMS", "basis": "a better same-partner structure survives the final comparison." + sensitivity_note, "package_prior": package}
    if action == "SHOP_BEFORE_ACCEPTING":
        return {"label": "SHOP BEFORE ACCEPTING", "basis": "a better outside structure survives the final comparison." + sensitivity_note, "package_prior": package}
    if action == "DECLINE":
        return {"label": "DECLINE", "basis": "the current offer is not beneficial enough for the focal franchise." + sensitivity_note, "package_prior": package}
    return {"label": action.replace("_", " "), "basis": "review required." + sensitivity_note, "package_prior": package}


def offer_context(report, scenario):
    focus = str(report.get("focus_user_id") or "")
    partner = str(report.get("current_offer_partner_user_id") or "")
    initiator = str(
        scenario.get("offer_initiator_user_id")
        or report.get("offer_initiator_user_id")
        or ""
    )
    direction = (
        "INCOMING_OFFER" if initiator and initiator == partner
        else "FOCAL_INITIATED" if initiator and initiator == focus
        else str(report.get("offer_direction") or "UNKNOWN")
    )
    return {
        "offer_initiator_user_id": initiator or None,
        "direction": direction,
        "current_terms_willingness_observed": direction == "INCOMING_OFFER",
        "generated_counter_willingness_observed": False,
        "incoming_offer_concession_search_relevant": direction == "INCOMING_OFFER",
        "interpretation": (
            "The counterparty made the current offer, so willingness at the current terms is observed. A counter that asks for a concession remains unobserved and is evaluated as a local negotiation step around that anchor."
            if direction == "INCOMING_OFFER"
            else "Offer origin was not observed as counterparty-initiated, so generated counters rely on modeled feasibility rather than an observed willingness anchor."
        ),
    }


def apply_to_report(report, scenario):
    report["offer_context"] = offer_context(report, scenario or {})
    report["future_pick_outlook"] = future_pick_outlook(report)
    cur=report.get("current_offer_evaluation") or {}
    attr=cur.get("decision_attribution") or {}
    future_channel=_decision_channel(cur, "future")
    pkg=((attr.get("diagnostics") or {}).get("package_concentration") or {})
    report["value_metric_context"] = {
        "future_asset_value": {
            "metric": "decision_attribution.channels.future.primitive_value",
            "value": future_channel.get("primitive_value"),
            "definition": (
                "Authoritative future-asset value used by Shared Decision Utility. For explicit multi-asset trades, "
                "this replaces raw package additivity with the governed package-concentration prior while preserving "
                "non-trade future effects exactly once."
            ),
        },
        "raw_additive_dynasty_market_value": {
            "metric": "simulation.strategic.market_dynasty_delta",
            "value": ((cur.get("simulation") or {}).get("strategic") or {}).get("market_dynasty_delta"),
            "definition": "Raw additive league-wide dynasty market-value delta; reference/diagnostic only once package concentration is active.",
        },
        "package_concentration": {
            "applied": pkg.get("package_transform_applied"),
            "raw_trade_package_future_value": pkg.get("raw_trade_package_future_value"),
            "package_effective_trade_future_value": pkg.get("package_effective_trade_future_value"),
            "non_trade_future_value_preserved": pkg.get("non_trade_future_value_preserved"),
            "prior": _package_prior_profile(cur),
        },
        "incremental_asset_liquidity": liquidity_context(report),
        "why_they_can_move_opposite": (
            "Raw dynasty market value is the additive market reference. Future Asset Value is the governed decision input "
            "after any package-concentration transform. Incremental asset liquidity is a separate residual channel when authorized."
        ),
    }
    report["recommendation_profile"] = recommendation_profile(report)
    report.setdefault("policy", {}).update({
        "trade_report_context_model_version": MODEL_VERSION,
        "future_pick_outlook_uses_post_trade_simulator_ordering": True,
        "future_pick_tier_is_directional_not_exact_draft_slot_probability": True,
        "long_term_trade_value_and_incremental_liquidity_defined_separately": True,
        "offer_origin_context_exposed": True,
    })
    return report
