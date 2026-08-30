#!/usr/bin/env python3
"""Canonical post-simulation roster-interaction overlay.

Extracts the useful roster-interaction and post-overlay negotiation-ranking
mechanics from historical Counter Market Sweep v1.24 without carrying forward
that wrapper's superseded option-comparison logic.

The overlay:
- applies generic, symmetric roster-interaction adjustments;
- updates strategic/resilience and buyer feasibility diagnostics;
- refreshes canonical negotiation rankings after the overlay;
- re-sorts exposed retained candidate sections;
- records when upstream candidate selection may be sensitive because the full
  pre-filter candidate universe is unavailable.

It does not classify an option as BETTER/MIXED/WORSE and does not choose the
final recommended action. Those responsibilities belong to
trade_option_governance.py.
"""
from __future__ import annotations

MODEL_VERSION = "FSFFL-Roster-Interaction-Overlay-1.1"

TRACKED_SECTIONS = (
    "suggested_counteroffers",
    "market_sweep_alternatives",
    "top_5_alternatives",
    "ranked_finalists",
    "same_partner_counteroffers",
    "alternate_buyer_candidates",
    "realistic_counter_alternatives",
)


def sf(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def band(score):
    return "HIGH" if score >= .68 else "MEDIUM" if score >= .48 else "LOW" if score >= .28 else "VERY_LOW"


def row_key(row):
    return (
        str(row.get("buyer_user_id") or ""),
        tuple(sorted(map(str, row.get("outgoing_assets") or []))),
        tuple(sorted(map(str, row.get("return_assets") or []))),
    )


def player_ids_from_actions(actions):
    out = []
    for a in actions or []:
        if str(a.get("type") or "").lower() == "trade":
            out.extend(str(x) for x in (a.get("players") or []))
    return sorted(set(out))


def apply_row(row, report, interaction):
    if not row:
        return row
    sim = row.get("simulation") or {}
    actions = sim.get("actions") or sim.get("trade_actions") or []
    focus_uid = str(report.get("focus_user_id") or "")
    buyer_uid = str(row.get("buyer_user_id") or report.get("current_offer_partner_user_id") or "")
    if not focus_uid or not buyer_uid or not actions:
        return row

    adjustment = interaction.trade_adjustments(focus_uid, buyer_uid, actions)
    focus = (adjustment.get("teams") or {}).get(focus_uid) or {}
    buyer = (adjustment.get("teams") or {}).get(buyer_uid) or {}
    fdelta = sf(focus.get("roster_interaction_value_delta"))
    bdelta = sf(buyer.get("roster_interaction_value_delta"))

    sim["roster_interactions"] = adjustment
    sim["trade_player_context"] = interaction.contextual_snapshot(
        focus_uid, player_ids_from_actions(actions)
    )

    strategic = sim.get("strategic") or {}
    weights = strategic.get("objective_weights") or {}
    resilience_weight = clamp(sf(weights.get("resilience"), .15), 0, .35)
    proposed_weighted = round(fdelta * resilience_weight, 2)
    # GM3 strategic profiles already include team-specific replacement
    # resilience from lineup reoptimization. Until pair-insurance adds
    # incremental predictive/simulation value beyond that channel, keep the
    # same-team interaction estimate as a diagnostic instead of paying it a
    # second time in final utility.
    weighted = 0.0
    strategic["roster_interaction_value_delta"] = round(fdelta, 2)
    strategic["roster_interaction_proposed_weighted_delta_diagnostic"] = proposed_weighted
    strategic["roster_interaction_weighted_delta"] = weighted
    strategic["roster_interaction_incremental_value_authorized"] = False
    sim["strategic"] = strategic

    comps = row.get("state_aware_score_components") or {}
    if comps:
        comps["roster_interaction_diagnostic"] = proposed_weighted
        comps["roster_interaction"] = 0.0
        row["state_aware_score_components"] = comps

    row["post_sim_score_pre_roster_interaction"] = sf(row.get("post_sim_score"))
    row["post_sim_score"] = sf(row.get("post_sim_score"))

    br = row.get("buyer_rationality") or {}
    if br:
        shift = sf(buyer.get("acceptance_fit_shift"))
        prior = sf(br.get("heuristic_acceptance_fit_score"), .5)
        prior_band = str(br.get("heuristic_acceptance_fit") or band(prior))
        score = round(clamp(prior + shift, 0, 1), 4)
        new_band = band(score)
        br["heuristic_acceptance_fit_score_pre_roster_interaction"] = prior
        br["heuristic_acceptance_fit_pre_roster_interaction"] = prior_band
        br["roster_interaction_value_delta"] = round(bdelta, 2)
        br["roster_interaction_acceptance_fit_shift"] = round(shift, 4)
        br["heuristic_acceptance_fit_score"] = score
        br["heuristic_acceptance_fit"] = new_band
        br["roster_interaction_crossed_acceptance_band"] = new_band != prior_band
        br["acceptance_fit_basis"] = (
            str(br.get("acceptance_fit_basis") or "") + "_plus_roster_interaction_1_0"
        )
        row["buyer_rationality"] = br
        row["acceptance_likelihood"] = new_band

    return row


def refresh_negotiation_ranking(row, ranker):
    if row and row.get("buyer_rationality"):
        row["negotiation_ranking_pre_roster_interaction"] = row.get("negotiation_ranking")
        row["negotiation_ranking"] = ranker.recompute_from_row(row)
    return row


def sort_rows(rows):
    return sorted(
        rows or [],
        key=lambda x: (
            sf((x.get("negotiation_ranking") or {}).get("score")),
            sf(x.get("post_sim_score")),
            str(row_key(x)),
        ),
        reverse=True,
    )


def apply_to_report(report, interaction, ranker):
    """Apply the validated v1.24 roster overlay without legacy comparisons."""
    pre_orders = {
        section: [row_key(x) for x in (report.get(section) or [])]
        for section in TRACKED_SECTIONS
    }

    current = refresh_negotiation_ranking(
        apply_row(report.get("current_offer_evaluation") or {}, report, interaction),
        ranker,
    )
    report["current_offer_evaluation"] = current

    band_crossings = 0
    for section in TRACKED_SECTIONS:
        rows = []
        for row in report.get(section) or []:
            row = refresh_negotiation_ranking(
                apply_row(row, report, interaction),
                ranker,
            )
            if ((row.get("buyer_rationality") or {}).get(
                "roster_interaction_crossed_acceptance_band"
            )):
                band_crossings += 1
            rows.append(row)
        report[section] = sort_rows(rows)

    post_orders = {
        section: [row_key(x) for x in (report.get(section) or [])]
        for section in TRACKED_SECTIONS
    }
    changed_sections = [
        section for section in TRACKED_SECTIONS
        if pre_orders[section] != post_orders[section]
    ]
    selection_sensitive = bool(changed_sections or band_crossings)

    governance = report.setdefault("governance", {})
    governance["post_ranking_roster_interaction"] = {
        "ranking_refreshed_after_overlay": True,
        "canonical_negotiation_ranking_helper": "negotiation_ranking.recompute_from_row",
        "sections_with_order_change": changed_sections,
        "acceptance_band_crossing_count": band_crossings,
        "upstream_candidate_selection_may_be_sensitive": selection_sensitive,
        "candidate_universe_rebuilt_after_overlay": False,
        "reason_candidate_universe_not_rebuilt": (
            "The current overlay receives the retained candidate universe from "
            "the upstream market-sweep chain rather than the complete pre-filter universe"
        ),
        "trade_decision_roster_interaction_overlay_model_version": MODEL_VERSION,
    }
    governance["recommendation_authority"] = (
        "PROVISIONAL_POST_RANK_OVERLAY_SENSITIVE"
        if selection_sensitive
        else "PROVISIONAL_STABLE_TO_ROSTER_INTERACTION_OVERLAY"
    )
    governance["recommended_next_action_empirically_authoritative"] = False
    governance["recommended_next_action_note"] = (
        "The inherited action is non-authoritative because roster interaction is "
        "provisional; when ordering or an acceptance band changes after the overlay, "
        "the complete upstream candidate universe would need to be regenerated under "
        "the overlay before treating the action as internally stable."
        if selection_sensitive
        else
        "The inherited action remained internally stable to the bounded roster-interaction "
        "overlay on the retained candidate set, but the overlay itself is not empirically calibrated."
    )

    report.setdefault("policy", {}).update({
        "roster_interaction_model_version": interaction.MODEL_VERSION,
        "roster_interaction_overlay_model_version": MODEL_VERSION,
        "roster_specific_correlated_asset_value_enabled": True,\n        "roster_interaction_incremental_final_score_value_authorized": False,\n        "roster_interaction_reason": "team-specific replacement resilience is already modeled; pair-insurance remains diagnostic pending incremental validation",
        "same_team_position_insurance_enabled": True,
        "interaction_rules_generic_and_symmetric": True,
        "player_specific_interaction_exceptions": False,
        "market_value_remains_league_wide": True,
        "interaction_adjustment_bounded": True,
        "interaction_cannot_override_hard_contender_or_roster_legality_gates": True,
        "depth_chart_competition_context_exposed_not_double_counted": True,
        "negotiation_ranking_refreshed_after_post_ranking_interaction_overlay": True,
        "post_overlay_candidate_universe_limit_explicit": True,
        "provisional_overlay_cannot_silently_create_authoritative_action": True,
        "legacy_v30_option_comparison_not_executed_in_current_path": True,
    })
    report.setdefault("simulation", {})["execution_path"] = (
        str((report.get("simulation") or {}).get("execution_path") or "")
        + "_plus_roster_interaction_1_0_plus_post_overlay_ranking_refresh"
    )
    return {
        "sections_with_order_change": changed_sections,
        "acceptance_band_crossing_count": band_crossings,
        "selection_sensitive": selection_sensitive,
    }
