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
    lines = [
        f"# FSFFL Opportunity Engine — {board.get('team_name') or 'Franchise'}",
        "",
        f"**Team state:** {board.get('team_state') or 'N/A'}",
        f"**Model:** {board.get('model_version') or 'N/A'}",
        "",
        "## Best plan available",
        line_for(board.get("best_plan_available") or board.get("best_move_available") or {}),
        f"**Execution status:** {((board.get('best_plan_available') or {}).get('execution_status') or {}).get('status') or 'N/A'}",
        "",
        "## Best single move available",
        line_for(board.get("best_move_available") or {}),
    ]

    best = board.get("best_move_available") or {}
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
            lines.append(f"{i}. {line_for(row)}")
    else:
        lines.append("No positive single-step opportunity cleared the governed benchmark.")

    views = board.get("specialized_views") or {}
    lines += ["", "## Specialized views"]
    for label, key in [
        ("Buy-low candidate", "best_buy_low_candidate"),
        ("Model-vs-market acquisition", "best_model_vs_market_acquisition"),
        ("Negotiation-ready trade", "best_negotiation_ready_trade"),
        ("Current-season upgrade", "best_current_season_upgrade"),
        ("Long-term value move", "best_long_term_value_move"),
        ("Emerging-value opportunity", "best_emerging_value_opportunity"),
        ("Draft-intelligence opportunity", "best_draft_intelligence_opportunity"),
    ]:
        lines.append(f"- **{label}:** {line_for(views.get(key) or {})}")

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
    lines += ["", "## Best two-move portfolio"]
    best_portfolio = portfolio.get("best_portfolio") or {}
    if best_portfolio:
        lines.append(line_for(best_portfolio))
        lines.append(
            f"Screened {int(portfolio.get('candidate_pairs_evaluated') or 0)} structurally compatible pairs "
            f"at {int(portfolio.get('screen_simulation_count_per_bundle') or 0):,} simulations each; "
            f"deep-confirmed {int(portfolio.get('deep_confirmed_portfolios') or 0)} finalists at "
            f"{int(portfolio.get('confirmation_simulation_count_per_finalist') or 0):,} simulations each."
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
        if best_portfolio.get("trade_steps_require_trade_decision_review"):
            lines.append("Trade steps remain subject to Trade Decision review before execution advice.")
    else:
        lines.append("No two-move portfolio was evaluated or no compatible pair was available.")

    revisit = board.get("negotiation_revisit_queue") or []
    lines += ["", "## Negotiation revisit queue"]
    if revisit:
        for row in revisit[:5]:
            lines.append(f"- {line_for(row)} — {row.get('acceptance_fit') or 'LOW FIT'}")
    else:
        lines.append("No positive-utility LOW/VERY_LOW-fit trade is currently queued for revisit.")

    coverage = board.get("search_coverage") or {}
    lines += [
        "",
        "## Search coverage",
        f"- Trade candidates screened: {int(coverage.get('trade_candidates_screened') or 0)}",
        f"- Waiver candidates screened: {int(coverage.get('waiver_candidates_screened') or 0)}",
        f"- Portfolio pairs evaluated: {int(coverage.get('portfolio_pairs_evaluated') or 0)}",
        f"- Trade Decision reviews completed: {int(coverage.get('trade_decision_reviews_completed') or 0)}",
        "- Search is bounded for computation and is not represented as exhaustive.",
    ]

    reviews = board.get("trade_decision_reviews") or []
    lines += ["", "## Trade Decision routing"]
    if reviews:
        for review in reviews:
            td = review.get("trade_decision") or {}
            lines.append(
                f"- {review.get('source_opportunity_description')}: "
                f"{td.get('generated_proposal_guidance') or td.get('recommended_next_action') or 'REVIEW'}"
            )
    else:
        lines.append("Trade Decision routing was disabled for this run.")

    lines += [
        "",
        "## Governance",
        "- Opportunity Engine searches and composes; it does not own player/pick valuation or a competing utility.",
        "- Simulator remains authoritative for competitive outcomes.",
        "- GM3 Team Improvement remains authoritative for franchise improvement and portfolio evaluation.",
        "- Trade Decision remains authoritative for generated trade review and negotiation policy.",
        "- Behavioral feasibility is not represented as an acceptance probability.",
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
