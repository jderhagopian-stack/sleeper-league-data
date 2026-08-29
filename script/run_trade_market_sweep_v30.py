#!/usr/bin/env python3
"""FSFFL Counter & Market Sweep 1.24 — roster-interaction intelligence.

Extends validated 1.23 with a generic, symmetric roster-specific interaction
layer. Same-team same-position insurance/coverage is valued separately from
league-wide market value and separately from lineup simulation.

The layer is bounded and may not override hard contender or roster-legality
gates. No player-specific exceptions are permitted.

Governance note: because this wrapper modifies post-simulation score and buyer
acceptance fit after the v1.23 candidate selectors have run, every exposed
negotiation ranking is refreshed from the canonical shared ranking component.
If the post-ranking overlay changes ordering or crosses an acceptance band used
by upstream selectors, the inherited recommended action is explicitly qualified
rather than silently presented as authoritative.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent
V29 = SCRIPT / "run_trade_market_sweep_v29.py"
NEGOTIATION_RANKING = SCRIPT / "negotiation_ranking.py"
INTERACTION = SCRIPT / "roster_interaction.py"
MODEL_VERSION = "FSFFL-Counter-Market-Sweep-1.24"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def sf(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def out_path():
    if "--output" not in sys.argv:
        return None
    i = sys.argv.index("--output")
    return Path(sys.argv[i + 1]) if i + 1 < len(sys.argv) else None


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


def apply_row(row, report, ri):
    if not row:
        return row
    sim = row.get("simulation") or {}
    actions = sim.get("actions") or sim.get("trade_actions") or []
    focus_uid = str(report.get("focus_user_id") or "")
    buyer_uid = str(row.get("buyer_user_id") or report.get("current_offer_partner_user_id") or "")
    if not focus_uid or not buyer_uid or not actions:
        return row

    interaction = ri.trade_adjustments(focus_uid, buyer_uid, actions)
    focus = (interaction.get("teams") or {}).get(focus_uid) or {}
    buyer = (interaction.get("teams") or {}).get(buyer_uid) or {}
    fdelta = sf(focus.get("roster_interaction_value_delta"))
    bdelta = sf(buyer.get("roster_interaction_value_delta"))

    sim["roster_interactions"] = interaction
    sim["trade_player_context"] = ri.contextual_snapshot(focus_uid, player_ids_from_actions(actions))

    strategic = sim.get("strategic") or {}
    weights = strategic.get("objective_weights") or {}
    resilience_weight = clamp(sf(weights.get("resilience"), .15), 0, .35)
    weighted = round(fdelta * resilience_weight, 2)
    strategic["roster_interaction_value_delta"] = round(fdelta, 2)
    strategic["roster_interaction_weighted_delta"] = weighted
    strategic["strategic_value_delta_pre_roster_interaction"] = sf(strategic.get("strategic_value_delta"))
    strategic["strategic_value_delta"] = round(sf(strategic.get("strategic_value_delta")) + weighted, 2)
    sim["strategic"] = strategic

    comps = row.get("state_aware_score_components") or {}
    if comps:
        comps["resilience_pre_roster_interaction"] = sf(comps.get("resilience"))
        comps["roster_interaction"] = weighted
        comps["resilience"] = round(sf(comps.get("resilience")) + weighted, 2)
        row["state_aware_score_components"] = comps

    row["post_sim_score_pre_roster_interaction"] = sf(row.get("post_sim_score"))
    row["post_sim_score"] = round(sf(row.get("post_sim_score")) + weighted, 2)

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
        br["acceptance_fit_basis"] = str(br.get("acceptance_fit_basis") or "") + "_plus_roster_interaction_1_0"
        row["buyer_rationality"] = br
        row["acceptance_likelihood"] = new_band

    return row


def recompute_negotiation_ranking(row, ranker):
    """Compatibility adapter to the version-neutral shared ranking helper."""
    return ranker.recompute_from_row(row)


def refresh_negotiation_ranking(row, ranker):
    """Reuse the canonical state-aware negotiation transform after overlay."""
    if row and row.get("buyer_rationality"):
        row["negotiation_ranking_pre_roster_interaction"] = row.get("negotiation_ranking")
        row["negotiation_ranking"] = recompute_negotiation_ranking(row, ranker)
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


def compare(row, current):
    def metric(x, key):
        sim = x.get("simulation") or {}
        d = sim.get("focus_delta") or {}
        st = sim.get("strategic") or {}
        if key in d:
            return sf(d.get(key))
        if key == "net_title_equity_swing_against_focus":
            return sf(sim.get(key))
        return sf(st.get(key))

    keys = (
        "expected_wins", "expected_points_for", "playoff_probability",
        "bye_probability", "championship_probability", "market_dynasty_delta",
        "strategic_value_delta", "liquidity_value_delta", "break_glass_delta",
        "roster_interaction_value_delta", "net_title_equity_swing_against_focus",
    )
    deltas = {k: round(metric(row, k) - metric(current, k), 5) for k in keys}
    score_delta = round(sf(row.get("post_sim_score")) - sf(current.get("post_sim_score")), 2)
    if score_delta > 750:
        verdict = "BETTER"
    elif score_delta < -750:
        verdict = "WORSE"
    else:
        verdict = "MIXED"
    drivers = []
    if abs(deltas["expected_wins"]) >= .10:
        drivers.append(f"{deltas['expected_wins']:+.2f} expected wins")
    if abs(deltas["championship_probability"]) >= .01:
        drivers.append(f"{deltas['championship_probability']*100:+.1f} pts championship probability")
    if abs(deltas["strategic_value_delta"]) >= 250:
        drivers.append(f"{deltas['strategic_value_delta']:+,.0f} franchise value")
    if abs(deltas["roster_interaction_value_delta"]) >= 75:
        drivers.append(f"{deltas['roster_interaction_value_delta']:+,.0f} roster-interaction value")
    if not drivers:
        drivers.append(f"{score_delta:+,.0f} state-aware score")
    lead = "Higher" if verdict == "BETTER" else "Lower" if verdict == "WORSE" else "A mixed"
    return {
        "verdict_vs_current_offer": verdict,
        "post_sim_score_delta_vs_current_offer": score_delta,
        "metric_deltas_vs_current_offer": deltas,
        "reason": f"{lead} state-aware tradeoff versus the current offer, driven by " + ", ".join(drivers[:4]) + ".",
        "comparison_basis": "state_aware_post_sim_score_plus_roster_interaction_and_key_simulation_strategic_deltas",
    }


def main():
    v29 = load(V29, "market_v29_for_124")
    ranker = load(NEGOTIATION_RANKING, "negotiation_ranking_shared_for_124")
    ri = load(INTERACTION, "roster_interaction_for_124")
    v29.main()
    out = out_path()
    if not out or not out.exists():
        return

    r = json.loads(out.read_text(encoding="utf-8"))
    tracked_sections = (
        "suggested_counteroffers", "market_sweep_alternatives", "top_5_alternatives",
        "ranked_finalists", "same_partner_counteroffers", "alternate_buyer_candidates",
        "realistic_counter_alternatives",
    )
    pre_orders = {s: [row_key(x) for x in (r.get(s) or [])] for s in tracked_sections}

    current = refresh_negotiation_ranking(apply_row(r.get("current_offer_evaluation") or {}, r, ri), ranker)
    r["current_offer_evaluation"] = current

    band_crossings = 0
    for section in tracked_sections:
        rows = []
        for x in r.get(section) or []:
            x = refresh_negotiation_ranking(apply_row(x, r, ri), ranker)
            if ((x.get("buyer_rationality") or {}).get("roster_interaction_crossed_acceptance_band")):
                band_crossings += 1
            rows.append(x)
        r[section] = sort_rows(rows)

    post_orders = {s: [row_key(x) for x in (r.get(s) or [])] for s in tracked_sections}
    changed_sections = [s for s in tracked_sections if pre_orders[s] != post_orders[s]]

    for section in ("suggested_counteroffers", "market_sweep_alternatives"):
        for row in r.get(section) or []:
            row["comparison_to_current_offer"] = compare(row, current)
            row["why_prefer_over_current_offer"] = row["comparison_to_current_offer"]["reason"]

    selection_sensitive = bool(changed_sections or band_crossings)
    governance = r.setdefault("governance", {})
    governance["post_ranking_roster_interaction"] = {
        "ranking_refreshed_after_overlay": True,
        "canonical_negotiation_ranking_helper": "negotiation_ranking.recompute_from_row",
        "sections_with_order_change": changed_sections,
        "acceptance_band_crossing_count": band_crossings,
        "upstream_candidate_selection_may_be_sensitive": selection_sensitive,
        "candidate_universe_rebuilt_after_overlay": False,
        "reason_candidate_universe_not_rebuilt": "v1.24 receives a filtered v1.23 report rather than the complete pre-filter candidate universe",
    }
    governance["recommendation_authority"] = (
        "PROVISIONAL_POST_RANK_OVERLAY_SENSITIVE" if selection_sensitive
        else "PROVISIONAL_STABLE_TO_ROSTER_INTERACTION_OVERLAY"
    )
    governance["recommended_next_action_empirically_authoritative"] = False
    governance["recommended_next_action_note"] = (
        "The inherited action is non-authoritative because roster interaction is provisional; when ordering or an acceptance band changes after the overlay, the complete upstream candidate universe would need to be regenerated under the overlay before treating the action as internally stable."
        if selection_sensitive else
        "The inherited action remained internally stable to the bounded roster-interaction overlay on the retained candidate set, but the overlay itself is not empirically calibrated."
    )

    r["model_version"] = MODEL_VERSION
    r.setdefault("policy", {}).update({
        "roster_interaction_model_version": ri.MODEL_VERSION,
        "roster_specific_correlated_asset_value_enabled": True,
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
    })
    r.setdefault("simulation", {})["execution_path"] = str((r.get("simulation") or {}).get("execution_path") or "") + "_plus_roster_interaction_1_0_plus_post_overlay_ranking_refresh"
    out.write_text(json.dumps(r, indent=2, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    main()
