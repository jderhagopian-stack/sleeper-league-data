#!/usr/bin/env python3
"""Presentation-only renderer for an Opportunity Engine board."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def line_for(row):
    if not row:
        return "None identified"
    desc = str(row.get("description") or row.get("asset") or "Unnamed opportunity")
    score = row.get("team_improvement_score")
    if score is not None:
        return f"{desc} — GM3 improvement score {float(score):+,.1f}"
    return desc


def render(board):
    posture = board.get("strategic_posture") or {}
    lines = [
        f"# FSFFL Opportunity Engine — {board.get('team_name') or 'Franchise'}",
        "",
        f"**Competitive state:** {board.get('competitive_state') or board.get('team_state') or posture.get('competitive_state') or 'N/A'}",
        f"**Strategic posture:** {posture.get('selected_posture') or 'AUTO'}",
        f"**Posture source:** {posture.get('posture_source') or 'MODEL_DEFAULT'}",
        f"**Model:** {board.get('model_version') or 'N/A'}",
        "",
        "## Best move available",
        line_for(board.get("best_move_available") or {}),
    ]

    best = board.get("best_move_available") or {}
    actionable = board.get("best_actionable_trade") or {}
    explore = board.get("best_trade_to_explore") or {}
    outbound = board.get("outbound_future_value_opportunities") or []
    price_gaps = board.get("high_impact_price_gap_targets") or []
    lines += [
        "",
        "## CONSIDER / OPEN NEGOTIATION",
        (
            line_for(actionable)
            + (
                f"; modeled counterparty utility {float(actionable.get('counterparty_shared_decision_utility_score')):+,.1f}"
                if actionable and actionable.get('counterparty_shared_decision_utility_score') is not None
                else ""
            )
        ) if actionable else "No trade currently clears both sides of the governed price frontier.",
        "",
        "## OUTBOUND / FUTURE VALUE",
    ]
    if outbound:
        for row in outbound[:6]:
            incoming = " + ".join(x.get("name") or x.get("asset_id") or "asset" for x in (row.get("incoming") or []))
            outgoing = " + ".join(x.get("name") or x.get("asset_id") or "asset" for x in (row.get("outgoing") or []))
            lines.append(
                f"- **Shop {outgoing}:** modeled return {incoming}; "
                f"GM3 improvement {float(row.get('team_improvement_score') or 0):+,.1f}; "
                f"counterparty utility {float(row.get('counterparty_shared_decision_utility_score') or 0):+,.1f}."
            )
    else:
        lines.append("No outbound future-value package currently clears both sides of the governed utility check.")
    lines += [
        "",
        "## EXPLORE PRICE",
        line_for(explore) if explore else "No incomplete-but-economically-viable negotiation frontier is currently promoted.",
        "",
        "## PRICE GAP TOO WIDE",
    ]
    if price_gaps:
        for pf in price_gaps[:6]:
            target=pf.get("target") or {}
            floor=pf.get("seller_clearing_floor") or {}
            ceiling=pf.get("rational_focal_ceiling") or {}
            lines.append(
                f"- **{target.get('name') or target.get('asset_id') or 'Target'}:** "
                f"our ceiling: {ceiling.get('description') or 'none found'}; "
                f"seller floor: {floor.get('description') or 'not found'}."
            )
    else:
        lines.append("No material price-gap target identified.")
    watchlist = board.get("near_frontier_watchlist") or []
    lines += ["", "## WORTH DISCUSSING / NEAR FRONTIER"]
    if watchlist:
        for pf in watchlist[:6]:
            target = pf.get("target") or {}
            near = pf.get("near_frontier_evidence") or {}
            focal_pkg = near.get("best_focal_positive_package_for_counterparty") or {}
            seller_pkg = near.get("best_counterparty_viable_package_for_focal") or {}
            seller_short = near.get("counterparty_utility_shortfall_at_best_focal_positive_package")
            focal_short = near.get("focal_utility_shortfall_at_best_counterparty_viable_package")
            gap = near.get("market_coordinate_gap_between_focal_ceiling_and_seller_floor")
            detail = (
                f"closest focal-positive package: {focal_pkg.get('description') or 'none'}"
                + (f"; seller utility shortfall {float(seller_short):,.1f}" if seller_short is not None else "")
                + (f"; seller-clearing package: {seller_pkg.get('description') or 'not found'}" if seller_pkg else "")
                + (f"; focal utility shortfall there {float(focal_short):,.1f}" if focal_short is not None else "")
                + (f"; package-coordinate gap {float(gap):,.0f}" if gap is not None else "")
            )
            lines.append(f"- **{target.get('name') or target.get('asset_id') or 'Target'}:** {detail}.")
        lines.append("These are negotiation watchlist targets, not actionable trades. No fixed utility cutoff is used; the report shows the observed distance to bilateral viability.")
    else:
        lines.append("No focal-positive trade target reached the near-frontier watchlist in the evaluated package set.")

    lines += ["", "## WHAT TO DO NEXT"]
    if actionable:
        lines.append(f"Consider opening negotiations around {line_for(actionable)}; verify the bilateral margin and tradeoff before making an offer.")
    elif explore:
        lines.append(f"Continue price discovery on {line_for(explore)}")
    else:
        lines.append("Do not force a trade. Continue scanning realistic alternatives and re-run price discovery as values or rosters change.")

    review = best.get("trade_decision_review") or {}
    if review:
        lines += [
            "",
            f"**Trade Decision:** {review.get('recommended_next_action') or 'REVIEW'}",
            f"**Trade Decision basis:** {review.get('action_basis') or 'N/A'}",
        ]

    lines += ["", "## Top single-step opportunities"]
    ranked = board.get("ranked_single_step_opportunities") or []
    if ranked:
        for i, row in enumerate(ranked[:10], 1):
            suffix = ""
            if row.get("channel") == "TRADE" and row.get("counterparty_shared_decision_utility_score") is not None:
                suffix = f"; counterparty utility {float(row.get('counterparty_shared_decision_utility_score')):+,.1f}"
            lines.append(f"{i}. {line_for(row)}{suffix}")
    else:
        lines.append("No positive single-step opportunity cleared the governed benchmark.")

    views = board.get("specialized_views") or {}
    lines += ["", "## Governed specialist views"]
    for label, key in [
        ("Highest governed opportunity carrying a buy-low signal", "best_buy_low_candidate"),
        ("Highest governed model-vs-market acquisition", "best_model_vs_market_acquisition"),
        ("Highest governed trade with medium/high negotiation fit", "best_negotiation_ready_trade"),
        ("Highest governed current-season upgrade", "best_current_season_upgrade"),
        ("Highest governed long-term-value move", "best_long_term_value_move"),
        ("Highest governed emerging-value opportunity", "best_emerging_value_opportunity"),
        ("Highest governed draft-intelligence opportunity", "best_draft_intelligence_opportunity"),
    ]:
        lines.append(f"- **{label}:** {line_for(views.get(key) or {})}")
    lines.append("These are filtered views of the governed upstream order; they are not separate specialist rankings.")

    frontier = board.get("negotiation_frontier") or {}
    price_frontiers = frontier.get("target_price_frontiers") or []
    lines += ["", "## Negotiation price frontier"]
    if price_frontiers:
        for pf in price_frontiers[:5]:
            target = pf.get("target") or {}
            opener = pf.get("opening_package") or {}
            floor = pf.get("seller_clearing_floor") or {}
            ceiling = pf.get("rational_focal_ceiling") or {}
            lines.append(
                f"- **{target.get('name') or target.get('asset_id') or 'Target'}:** {pf.get('status') or 'UNKNOWN'}; "
                f"opening package: {opener.get('description') or 'none found'}; "
                f"seller clearing floor: {floor.get('description') or 'not found in evaluated packages'}; "
                f"rational focal ceiling: {ceiling.get('description') or 'none'}."
            )
        lines.append("These are discrete frontiers over packages actually evaluated by GM3; they are not continuous acceptance probabilities or invented player premiums.")
    else:
        lines.append("No evaluated trade-package frontier was available for this run.")

    market = board.get("market_test_sell_high_candidates") or []
    lines += ["", "## Market-test / sell-high candidates"]
    if market:
        for row in market[:5]:
            buyer = row.get("best_buyer") or {}
            lines.append(
                f"- {row.get('asset') or row.get('asset_id')} — best modeled buyer: "
                f"{buyer.get('buyer_team') or buyer.get('buyer_manager') or 'N/A'}; "
                f"premium vs break-glass {float(buyer.get('premium_vs_break_glass') or 0):+,.0f}"
            )
    else:
        lines.append("No GM3 market-test candidate with a positive modeled premium.")

    portfolio = board.get("portfolio_optimization") or {}
    lines += ["", "## Best multi-move portfolio"]
    best_portfolio = portfolio.get("best_portfolio") or {}
    if best_portfolio:
        lines.append(line_for(best_portfolio))
        move_count = int(best_portfolio.get("move_count") or len(best_portfolio.get("steps") or []))
        lines.append(f"Portfolio contains {move_count} moves.")
        bundles = int(portfolio.get("candidate_bundles_evaluated") or portfolio.get("candidate_pairs_evaluated") or 0)
        lines.append(
            f"Screened {bundles} structurally compatible bundles at "
            f"{int(portfolio.get('screen_simulation_count_per_bundle') or 0):,} simulations each; "
            f"deep-confirmed {int(portfolio.get('deep_confirmed_portfolios') or 0)} finalists at "
            f"{int(portfolio.get('confirmation_simulation_count_per_finalist') or 0):,} simulations each."
        )
        if portfolio.get("adaptive_search"):
            lines.append(
                f"Adaptive search: up to {int(portfolio.get('max_moves') or 0)} moves, "
                f"beam width {int(portfolio.get('beam_width') or 0)}, "
                f"bundle sizes evaluated {portfolio.get('bundle_sizes_evaluated') or []}."
            )
        if best_portfolio.get("incremental_score_vs_best_single_step_same_precision") is not None:
            lines.append(
                f"Incremental GM3 score versus the best single step at the same precision: "
                f"{float(best_portfolio.get('incremental_score_vs_best_single_step_same_precision')):+,.1f}."
            )
            lines.append(
                "Portfolio preference on the same GM3 utility: "
                + ("PREFERRED" if best_portfolio.get("preferred_to_best_single_step_on_same_gm3_utility") else "NOT PREFERRED")
            )
        plan = best_portfolio.get("execution_plan") or {}
        if plan.get("live_ownership_and_availability_must_be_rechecked_before_execution"):
            lines.append("Execution note: ownership, waiver availability and trade preconditions must be rechecked immediately before acting.")
        if best_portfolio.get("trade_steps_require_trade_decision_review"):
            lines.append("Trade steps remain subject to Trade Decision review before execution advice.")
    else:
        lines.append("No compatible multi-move portfolio was identified or portfolio search was disabled.")

    robustness = board.get("robustness") or {}
    lines += ["", "## Recommendation robustness"]
    any_robust = False
    for label, key in [("Best single step", "best_single_step"), ("Best portfolio", "best_portfolio")]:
        row = robustness.get(key) or {}
        if not row.get("enabled"):
            continue
        any_robust = True
        lines.append(
            f"- **{label}:** mean score {float(row.get('score_mean') or 0):+,.1f}; "
            f"range {float(row.get('score_min') or 0):+,.1f} to {float(row.get('score_max') or 0):+,.1f}; "
            f"seed-sign stable: {'YES' if row.get('sign_stable') else 'NO'}."
        )
    if not any_robust:
        lines.append("Independent-seed robustness diagnostics were disabled for this run.")
    lines.append("Robustness diagnostics do not rerank the board or create a second utility.")

    reviews = board.get("trade_decision_reviews") or []
    lines += ["", "## Trade Decision routing"]
    if reviews:
        for routed in reviews:
            td = routed.get("trade_decision") or {}
            lines.append(
                f"- {routed.get('source_opportunity_description')}: "
                f"{td.get('recommended_next_action') or 'REVIEW'}"
            )
    else:
        lines.append("No actionable generated trade required Trade Decision routing in this run.")

    config = board.get("search_configuration") or {}
    if config:
        lines += ["", "## Search / simulation budget"]
        lines.append(
            f"Single-step screen: {int(config.get('trade_candidates') or 0)} trade candidates, "
            f"{int(config.get('waiver_candidates') or 0)} waiver candidates, "
            f"{int(config.get('trade_packages_per_target') or 0)} upstream packages per trade target."
        )
        lines.append(
            f"Single-step simulations: {int(config.get('quick_sims') or 0):,} screening / "
            f"{int(config.get('confirm_sims') or 0):,} confirmation."
        )
        lines.append("These are computational search budgets, not valuation weights.")

    prospective = board.get("prospective_validation") or {}
    if prospective:
        lines += ["", "## Prospective validation record"]
        lines.append(f"Snapshot time: {prospective.get('generated_at_utc') or 'N/A'}")
        lines.append(f"Input fingerprint: {prospective.get('source_input_sha256') or 'N/A'}")
        lines.append("This snapshot is intended to be graded later without backfilling future information into the original recommendation.")

    lines += [
        "",
        "## Governance",
        "- Opportunity Engine searches and composes; it does not own player/pick valuation or a competing utility.",
        "- Simulator remains authoritative for competitive outcomes.",
        "- GM3 Team Improvement remains authoritative for franchise improvement and portfolio evaluation.",
        "- Trade Decision remains authoritative for generated trade review and negotiation policy.",
        "- Behavioral feasibility is not represented as an acceptance probability.",
        "- Search depth, package depth, beam width and simulation counts are computational budgets, not football-value coefficients.",
        "",
    ]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    board = load(args.input)
    Path(args.output).write_text(render(board), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
