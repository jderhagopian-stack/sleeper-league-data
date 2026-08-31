#!/usr/bin/env python3
"""FSFFL Opportunity Engine.

Application-layer orchestrator for proactive franchise improvement.

The Opportunity Engine searches and composes. It does not create a competing
valuation, simulation, or recommendation model. Single-step discovery and
cross-channel utility come from GM3 Team Improvement; trade execution advice is
routed to Trade Decision; portfolio bundles are evaluated by the stable GM3 Team
Improvement API with the same shared decision utility.
"""
from __future__ import annotations

import argparse
import copy
import itertools
import json
import subprocess
import sys
import tempfile
from pathlib import Path

MODEL_VERSION = "FSFFL-Opportunity-Engine-1.4"
SCRIPT = Path(__file__).resolve().parent.parent
ROOT = SCRIPT.parent
TEAM_IMPROVEMENT = SCRIPT / "gm3" / "team_improvement.py"
TRADE_ENGINE = SCRIPT / "trade_engine.py"
if str(SCRIPT) not in sys.path:
    sys.path.insert(0, str(SCRIPT))
from gm3 import team_improvement as gm3_team_improvement


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _run_team_improvement(args, raw_output: Path):
    cmd = [
        sys.executable,
        str(TEAM_IMPROVEMENT),
        "--focus-user-id", str(args.focus_user_id),
        "--quick-sims", str(args.quick_sims),
        "--confirm-sims", str(args.confirm_sims),
        "--trade-screen", str(args.trade_screen),
        "--waiver-screen", str(args.waiver_screen),
        "--confirm-top", str(args.confirm_top),
        "--seed", str(args.seed),
        "--output", str(raw_output),
    ]
    subprocess.run(cmd, cwd=ROOT, check=True)


def _annotate(row):
    out = copy.deepcopy(row)
    channel = str(out.get("channel") or "")
    if channel == "TRADE":
        out["opportunity_engine_status"] = "CANDIDATE_REQUIRES_TRADE_DECISION_REVIEW"
        out["decision_authority"] = "GM3_TEAM_IMPROVEMENT_DISCOVERY; TRADE_DECISION_BEFORE_EXECUTION"
    elif channel == "WAIVER":
        out["opportunity_engine_status"] = "GOVERNED_SINGLE_STEP_OPPORTUNITY"
        out["decision_authority"] = "GM3_TEAM_IMPROVEMENT"
    elif channel == "HOLD":
        out["opportunity_engine_status"] = "EXPLICIT_BASELINE"
        out["decision_authority"] = "GM3_TEAM_IMPROVEMENT"
    return out


def _first(rows, predicate):
    return next((_annotate(x) for x in rows if predicate(x)), None)


def _specialist_maps():
    emerging = {}
    prospects = {}
    epath = ROOT / "data" / "gm" / "emerging_value.json"
    ppath = ROOT / "data" / "gm" / "gm30_prospect_radar.json"
    if epath.exists():
        doc = load_json(epath)
        emerging = {
            str(x.get("player_id")): x
            for x in (doc.get("candidates") or [])
            if x.get("player_id") is not None
        }
    if ppath.exists():
        doc = load_json(ppath)
        prospects = {
            str(x.get("player_id")): x
            for x in (doc.get("prospects") or [])
            if x.get("player_id") is not None
        }
    return emerging, prospects


def _with_specialist_intelligence(row, maps):
    out = copy.deepcopy(row)
    target = out.get("target") or {}
    pid = str(target.get("player_id") or "")
    emerging, prospects = maps
    specialist = {}
    if pid and pid in emerging:
        x = emerging[pid]
        specialist["breakout_sleeper_intelligence"] = {
            "source_model": "Breakout / Sleeper Intelligence",
            "signals": copy.deepcopy(x.get("signals") or []),
            "direction": x.get("direction"),
            "confidence_grade": x.get("confidence_grade"),
            "credible_path_to_relevance": x.get("credible_path_to_relevance"),
            "developmental_trajectory_score": x.get("developmental_trajectory_score"),
            "market_mispricing_score": x.get("market_mispricing_score"),
        }
    if pid and pid in prospects:
        x = prospects[pid]
        specialist["draft_intelligence"] = {
            "source_model": "Draft Intelligence",
            "signals": copy.deepcopy(x.get("signals") or []),
            "prospect_score": x.get("prospect_score"),
            "feature_coverage": x.get("feature_coverage"),
            "model_rookie_rank": x.get("model_rookie_rank"),
            "market_rookie_rank": x.get("market_rookie_rank"),
        }
    if specialist:
        out["specialist_intelligence"] = specialist

    if str(out.get("channel") or "") in {"TRADE", "WAIVER"}:
        sim = out.get("simulation") or {}
        evidence = {
            "focal_position_need": out.get("focal_position_need"),
            "seller_motivation_score": out.get("seller_motivation_score"),
            "acceptance_fit": out.get("acceptance_fit"),
            "expected_wins_delta": ((sim.get("focus_delta") or {}).get("expected_wins")),
            "championship_probability_delta": ((sim.get("focus_delta") or {}).get("championship_probability")),
            "market_dynasty_delta": ((sim.get("strategic") or {}).get("market_dynasty_delta")),
            "specialist_sources": sorted(specialist),
        }
        focal = out.get("target_focal_value")
        market = out.get("target_market_dynasty")
        if focal is not None and market is not None:
            evidence["target_model_vs_market_gap"] = round(float(focal or 0) - float(market or 0), 2)
        out["opportunity_evidence"] = evidence
    return out


def _enrich_source(source):
    maps = _specialist_maps()
    out = copy.deepcopy(source)
    for key in ("top_cross_channel_options", "best_trade_options", "best_waiver_options"):
        out[key] = [_with_specialist_intelligence(x, maps) for x in (out.get(key) or [])]
    for key in ("recommended_action", "hold_benchmark"):
        if out.get(key):
            out[key] = _with_specialist_intelligence(out[key], maps)
    return out


def _specialized_views(source):
    """Expose existing governed signals without creating a parallel score."""
    rows = list(source.get("top_cross_channel_options") or [])
    trades = [x for x in rows if str(x.get("channel") or "") == "TRADE"]

    def sim_delta(row, key):
        return float((((row.get("simulation") or {}).get("focus_delta") or {}).get(key)) or 0.0)

    def strategic(row, key):
        return float((((row.get("simulation") or {}).get("strategic") or {}).get(key)) or 0.0)

    model_vs_market = None
    for row in trades:
        focal = float(row.get("target_focal_value") or 0.0)
        market = float(row.get("target_market_dynasty") or 0.0)
        if focal > market > 0:
            model_vs_market = _annotate(row)
            model_vs_market["descriptive_model_vs_market_gap"] = round(focal - market, 2)
            model_vs_market["view_basis"] = "GM3 target focal value exceeds current dynasty market anchor; upstream order preserved"
            break

    buy_low = _first(
        rows,
        lambda x: any(
            str(sig).startswith("BUY_LOW")
            for sig in (
                (((x.get("specialist_intelligence") or {}).get("breakout_sleeper_intelligence") or {}).get("signals") or [])
            )
        ),
    )
    if buy_low:
        bsi = ((buy_low.get("specialist_intelligence") or {}).get("breakout_sleeper_intelligence") or {})
        buy_low["view_basis"] = (
            "Breakout / Sleeper Intelligence carries a BUY_LOW signal; "
            f"direction={bsi.get('direction') or 'UNKNOWN'}; upstream governed order preserved"
        )

    negotiation = _first(
        trades,
        lambda x: str(x.get("acceptance_fit") or "") in {"HIGH", "MEDIUM"},
    )
    if negotiation:
        negotiation["view_basis"] = "GM3/Behavioral Intelligence acceptance fit is HIGH or MEDIUM; not a probability"

    current_upgrade = _first(
        rows,
        lambda x: sim_delta(x, "championship_probability") > 0
        or sim_delta(x, "expected_wins") > 0,
    )
    if current_upgrade:
        current_upgrade["view_basis"] = "positive canonical Simulator current-season outcome delta"

    long_term = _first(rows, lambda x: strategic(x, "market_dynasty_delta") > 0)
    if long_term:
        long_term["view_basis"] = "positive governed long-term market dynasty delta"

    emerging = _first(
        rows,
        lambda x: bool((x.get("specialist_intelligence") or {}).get("breakout_sleeper_intelligence")),
    )
    if emerging:
        emerging["view_basis"] = "target has current Breakout / Sleeper Intelligence evidence; upstream governed order preserved"

    prospect = _first(
        rows,
        lambda x: bool((x.get("specialist_intelligence") or {}).get("draft_intelligence")),
    )
    if prospect:
        prospect["view_basis"] = "target has current Draft Intelligence evidence; upstream governed order preserved"

    return {
        "best_buy_low_candidate": buy_low,
        "best_model_vs_market_acquisition": model_vs_market,
        "best_negotiation_ready_trade": negotiation,
        "best_current_season_upgrade": current_upgrade,
        "best_long_term_value_move": long_term,
        "best_emerging_value_opportunity": emerging,
        "best_draft_intelligence_opportunity": prospect,
    }


def _load_sell_leverage(focus_user_id):
    root = ROOT / "data" / "gm" / "teams"
    if not root.exists():
        return {}
    for path in sorted(root.glob("*/sell_leverage.json")):
        try:
            doc = load_json(path)
        except Exception:
            continue
        if str(doc.get("focal_user_id") or "") == str(focus_user_id):
            return doc
    return {}


def _market_test_view(focus_user_id, limit=5):
    """Use GM3's existing market_should_be_tested signal; do not invent a sell score."""
    doc = _load_sell_leverage(focus_user_id)
    rows = []
    for asset in doc.get("assets") or []:
        buyer = asset.get("best_buyer") or {}
        premium = float(buyer.get("premium_vs_break_glass") or 0.0)
        if asset.get("market_should_be_tested") is not True or premium <= 0:
            continue
        row = copy.deepcopy(asset)
        row["view_basis"] = "GM3 market_should_be_tested with positive best-buyer premium versus break-glass value"
        row["decision_authority"] = "GM3_PORTFOLIO_ASSET_MANAGEMENT"
        rows.append(row)
        if len(rows) >= int(limit):
            break
    return rows


def _asset_ids(row):
    outgoing = {
        str(x.get("asset_id"))
        for x in (row.get("outgoing") or [])
        if x.get("asset_id")
    }
    target = str(((row.get("target") or {}).get("asset_id")) or "")
    return outgoing, target


def _compatible(a, b):
    """Structural compatibility only; final bundle value is evaluated by GM3."""
    if str(a.get("channel") or "") == "HOLD" or str(b.get("channel") or "") == "HOLD":
        return False
    a_out, a_target = _asset_ids(a)
    b_out, b_target = _asset_ids(b)
    if a_target and b_target and a_target == b_target:
        return False
    if a_out & b_out:
        return False
    if a_target and a_target in b_out:
        return False
    if b_target and b_target in a_out:
        return False
    return True


def _portfolio_description(rows):
    return " THEN ".join(str(x.get("description") or x.get("channel") or "MOVE") for x in rows)


def _portfolio_result(rows, result):
    return {
        "description": _portfolio_description(rows),
        "steps": [_annotate(x) for x in rows],
        "team_improvement_score": result.get("team_improvement_score"),
        "simulation": result.get("simulation"),
        "effective_actions": result.get("actions"),
        "decision_authority": "GM3_TEAM_IMPROVEMENT",
        "trade_steps_require_trade_decision_review": any(
            str(x.get("channel") or "") == "TRADE" for x in rows
        ),
        "portfolio_evaluation_source": result.get("authority"),
        "shared_decision_utility": result.get("shared_decision_utility"),
        "_source_rows": copy.deepcopy(rows),
    }


def build_portfolio_view(source, focus_user_id, depth=6, simulations=500,
                         confirm_simulations=5000, confirm_top=3,
                         seed=20260821, limit=5):
    """Screen compatible two-move bundles, then deep-confirm the leading set with GM3."""
    candidates = [
        copy.deepcopy(x)
        for x in (source.get("top_cross_channel_options") or [])[: max(0, int(depth))]
        if str(x.get("channel") or "") in {"TRADE", "WAIVER"}
    ]
    pairs = [(a, b) for a, b in itertools.combinations(candidates, 2) if _compatible(a, b)]
    if not pairs:
        return {
            "best_portfolio": None,
            "top_portfolios": [],
            "candidate_pairs_evaluated": 0,
            "authority": "GM3 Team Improvement",
        }

    screen_evaluator = gm3_team_improvement.portfolio_evaluator(
        str(focus_user_id), simulations=int(simulations), seed=int(seed)
    )
    screened = []
    for a, b in pairs:
        result = screen_evaluator.evaluate([a, b])
        row = _portfolio_result([a, b], result)
        row["screen_team_improvement_score"] = row.get("team_improvement_score")
        row["screen_simulations"] = int(simulations)
        screened.append(row)

    screened.sort(key=lambda x: float(x.get("team_improvement_score") or 0.0), reverse=True)
    n_confirm = min(max(1, int(confirm_top)), len(screened))
    finalists = screened[:n_confirm]

    if int(confirm_simulations) > int(simulations):
        confirm_evaluator = gm3_team_improvement.portfolio_evaluator(
            str(focus_user_id), simulations=int(confirm_simulations), seed=int(seed)
        )
        confirmed = []
        for row in finalists:
            source_rows = row.get("_source_rows") or []
            result = confirm_evaluator.evaluate(source_rows)
            out = _portfolio_result(source_rows, result)
            out["screen_team_improvement_score"] = row.get("screen_team_improvement_score")
            out["screen_simulations"] = int(simulations)
            out["confirmed"] = True
            out["confirmation_simulations"] = int(confirm_simulations)
            confirmed.append(out)
        confirmed.sort(key=lambda x: float(x.get("team_improvement_score") or 0.0), reverse=True)
    else:
        confirmed = finalists
        for row in confirmed:
            row["confirmed"] = True
            row["confirmation_simulations"] = int(simulations)

    best_single_row = (source.get("top_cross_channel_options") or [None])[0]
    comparable_single = None
    if best_single_row:
        evaluator = gm3_team_improvement.portfolio_evaluator(
            str(focus_user_id),
            simulations=int(confirm_simulations if int(confirm_simulations) > 0 else simulations),
            seed=int(seed),
        )
        single_result = evaluator.evaluate([best_single_row])
        comparable_single = {
            "description": best_single_row.get("description"),
            "team_improvement_score": single_result.get("team_improvement_score"),
            "simulation_count": int(confirm_simulations if int(confirm_simulations) > 0 else simulations),
            "authority": "GM3 Team Improvement",
        }

    top = confirmed[: max(1, int(limit))]
    portfolio_beats_single = False
    if top and comparable_single:
        incremental = (
            float(top[0].get("team_improvement_score") or 0.0)
            - float(comparable_single.get("team_improvement_score") or 0.0)
        )
        top[0]["incremental_score_vs_best_single_step_same_precision"] = round(incremental, 2)
        top[0]["preferred_to_best_single_step_on_same_gm3_utility"] = incremental > 0
        portfolio_beats_single = incremental > 0
    for row in top:
        row.pop("_source_rows", None)

    return {
        "best_portfolio": top[0] if top else None,
        "top_portfolios": top,
        "candidate_pairs_evaluated": len(screened),
        "screened_portfolios": len(screened),
        "deep_confirmed_portfolios": len(confirmed),
        "search_depth": int(depth),
        "screen_simulation_count_per_bundle": int(simulations),
        "confirmation_simulation_count_per_finalist": int(confirm_simulations),
        "best_single_step_same_precision": comparable_single,
        "best_portfolio_preferred_to_best_single_step": portfolio_beats_single,
        "authority": "GM3 Team Improvement",
        "search_budget_is_computational_not_decision_authority": True,
        "screening_and_confirmation_use_same_gm3_utility": True,
    }

def _trade_scenario(row, focus_user_id, ordinal):
    target = row.get("target") or {}
    outgoing = list(row.get("outgoing") or [])
    seller = str(row.get("seller_user_id") or "")
    if not seller or not target.get("player_id"):
        raise ValueError("Trade candidate is missing seller or target player")
    return {
        "scenario_id": f"opportunity-engine-{focus_user_id}-{ordinal}",
        "description": str(row.get("description") or "Opportunity Engine generated trade"),
        "transaction_status": "proposed",
        "offer_initiator_user_id": str(focus_user_id),
        "focus_user_id": str(focus_user_id),
        "participant_user_ids": [str(focus_user_id), seller],
        "actions": [
            {
                "type": "trade",
                "from_user_id": str(focus_user_id),
                "to_user_id": seller,
                "players": [
                    str(x.get("player_id"))
                    for x in outgoing
                    if x.get("asset_type") == "player" and x.get("player_id") is not None
                ],
                "picks": [
                    str(x.get("asset_id"))
                    for x in outgoing
                    if x.get("asset_type") == "pick" and x.get("asset_id")
                ],
            },
            {
                "type": "trade",
                "from_user_id": seller,
                "to_user_id": str(focus_user_id),
                "players": [str(target.get("player_id"))],
                "picks": [],
            },
        ],
    }


def _summarize_trade_decision(report):
    cur = report.get("current_offer_evaluation") or {}
    sim = cur.get("simulation") or {}
    raw_action = str(report.get("recommended_next_action") or "").upper()
    generated_action = {
        "ACCEPT_NOW": "OPEN_NEGOTIATION",
        "ACCEPT": "OPEN_NEGOTIATION",
        "OFFER_IN_HAND": "OPEN_NEGOTIATION",
        "COUNTER_CURRENT_OFFEROR": "OPEN_NEGOTIATION",
        "SHOP_BEFORE_ACCEPTING": "EXPLORE_PRICE",
        "DECLINE": "DO_NOT_PURSUE_AT_EXPECTED_COST",
    }.get(raw_action, raw_action or "EXPLORE_PRICE")
    return {
        "trade_decision_model_version": report.get("model_version"),
        "recommended_next_action": generated_action,
        "underlying_trade_decision_action": raw_action,
        "generated_proposal_semantics_applied": True,
        "action_basis": (((report.get("governance") or {}).get("option_outcome_consistency") or {}).get("action_basis")),
        "offer_context": copy.deepcopy(report.get("offer_context") or {}),
        "recommendation_profile": copy.deepcopy(report.get("recommendation_profile") or {}),
        "current_trade_impact": {
            "focus_delta": copy.deepcopy(sim.get("focus_delta") or {}),
            "strategic": copy.deepcopy(sim.get("strategic") or {}),
        },
        "suggested_counteroffers": copy.deepcopy((report.get("suggested_counteroffers") or [])[:2]),
        "market_sweep_alternatives": copy.deepcopy((report.get("market_sweep_alternatives") or [])[:3]),
        "candidate_counts": copy.deepcopy(report.get("candidate_counts") or {}),
        "behavioral_feasibility_is_not_acceptance_probability": True,
        "generated_proposal_willingness_observed": False,
    }


def review_trade_candidates(source, focus_user_id, depth=1, quick_sims=200,
                            confirm_sims=50000, search_depth=60, seed=20260821):
    """Route leading generated trade candidates through authoritative Trade Decision."""
    if int(depth) <= 0:
        return []
    trades = [
        x for x in (source.get("top_cross_channel_options") or [])
        if str(x.get("channel") or "") == "TRADE"
    ][: int(depth)]
    reviews = []
    for ordinal, row in enumerate(trades, 1):
        scenario = _trade_scenario(row, focus_user_id, ordinal)
        with tempfile.TemporaryDirectory(prefix="fsffl-opportunity-trade-") as td:
            td = Path(td)
            scenario_path = td / "scenario.json"
            result_path = td / "trade-decision.json"
            write_json(scenario_path, scenario)
            cmd = [
                sys.executable,
                str(TRADE_ENGINE),
                "--scenario", str(scenario_path),
                "--quick-sims", str(int(quick_sims)),
                "--confirm-sims", str(int(confirm_sims)),
                "--search-depth", str(int(search_depth)),
                "--seed", str(int(seed)),
                "--output", str(result_path),
            ]
            subprocess.run(cmd, cwd=ROOT, check=True)
            report = load_json(result_path)
        reviews.append({
            "source_opportunity_description": row.get("description"),
            "source_team_improvement_score": row.get("team_improvement_score"),
            "source_order_preserved": True,
            "scenario": scenario,
            "trade_decision": _summarize_trade_decision(report),
            "authority": "Trade Decision",
        })
    return reviews


def _attach_trade_review(row, reviews):
    out = copy.deepcopy(row)
    if str(out.get("channel") or "") != "TRADE":
        return out
    desc = str(out.get("description") or "")
    review = next(
        (x for x in reviews if str(x.get("source_opportunity_description") or "") == desc),
        None,
    )
    if review:
        out["trade_decision_review"] = copy.deepcopy(review.get("trade_decision") or {})
        out["opportunity_engine_status"] = "TRADE_DECISION_REVIEWED"
    return out


def build_board(source, focus_user_id=None, portfolio_depth=0, portfolio_sims=500,
                portfolio_confirm_sims=5000, portfolio_confirm_top=3,
                seed=20260821, trade_reviews=None):
    """Compose a board using authoritative source order and governed downstream APIs."""
    source = _enrich_source(source)
    reviews = list(trade_reviews or [])
    ranked = [_attach_trade_review(_annotate(x), reviews) for x in (source.get("top_cross_channel_options") or [])]
    best_trade = next((_attach_trade_review(_annotate(x), reviews) for x in (source.get("best_trade_options") or [])), None)
    best_waiver = next((_annotate(x) for x in (source.get("best_waiver_options") or [])), None)
    recommended = _attach_trade_review(
        _annotate(source.get("recommended_action") or source.get("hold_benchmark") or {}),
        reviews,
    )
    uid = str(focus_user_id or source.get("generated_for_user_id") or "")

    specialized = _specialized_views(source)
    market_test = _market_test_view(uid)
    portfolio = (
        build_portfolio_view(
            source, uid, depth=int(portfolio_depth),
            simulations=int(portfolio_sims),
            confirm_simulations=int(portfolio_confirm_sims),
            confirm_top=int(portfolio_confirm_top),
            seed=int(seed)
        )
        if int(portfolio_depth) >= 2
        else {
            "best_portfolio": None,
            "top_portfolios": [],
            "candidate_pairs_evaluated": 0,
            "authority": "GM3 Team Improvement",
            "disabled": True,
        }
    )

    return {
        "model_version": MODEL_VERSION,
        "generated_for_user_id": source.get("generated_for_user_id"),
        "team_name": source.get("team_name"),
        "team_state": source.get("team_state"),
        "best_move_available": recommended,
        "best_trade_opportunity": best_trade,
        "best_waiver_opportunity": best_waiver,
        "ranked_single_step_opportunities": ranked,
        "specialized_views": specialized,
        "market_test_sell_high_candidates": market_test,
        "portfolio_optimization": portfolio,
        "trade_decision_reviews": reviews,
        "hold_benchmark": _annotate(source.get("hold_benchmark") or {}),
        "search_summary": copy.deepcopy(source.get("search_summary") or {}),
        "provenance": {
            "source_application": "GM3 Team Improvement",
            "source_model_version": source.get("model_version"),
            "cross_channel_order_preserved_from_source": True,
            "opportunity_engine_rescoring_applied": False,
            "opportunity_engine_reranking_applied": False,
            "portfolio_scores_owned_by_gm3_team_improvement": True,
            "trade_decision_review_required_before_execution_advice": True,
            "trade_decision_reviews_routed_through_stable_trade_engine": bool(reviews),
            "waiver_decision_authority": "GM3 Team Improvement",
            "market_test_view_source": "GM3 sell_leverage.market_should_be_tested",
            "specialist_intelligence_sources": ["Draft Intelligence", "Breakout / Sleeper Intelligence"],
            "specialist_intelligence_changes_ranking": False,
        },
        "capability_status": {
            "single_step_trade_search": True,
            "single_step_waiver_search": True,
            "explicit_hold_baseline": True,
            "multi_step_portfolio_optimization": int(portfolio_depth) >= 2,
            "sell_high_buy_low_specialized_views": True,
            "negotiation_revisit_queue": True,
            "league_wide_trade_target_scan_for_focus_team": True,
            "free_agent_waiver_scan_for_focus_team": True,
            "draft_intelligence_context": True,
            "breakout_sleeper_intelligence_context": True,
            "league_wide_continuous_monitoring": False,
            "authoritative_trade_decision_routing": bool(reviews),
        },
        "policy": {
            "application_layer_orchestrator": True,
            "creates_new_valuation_model": False,
            "creates_new_cross_channel_utility": False,
            "search_heuristics_have_final_decision_authority": False,
            "specialized_views_preserve_upstream_order": True,
            "specialist_intelligence_is_context_not_rescoring": True,
            "portfolio_search_budget_is_computational_only": True,
            "shared_core_promotion_requires_second_consumer_or_domain_generic_behavior": True,
        },
    }


def architecture():
    return {
        "model_version": MODEL_VERSION,
        "layer": "Application",
        "application": "Opportunity Engine",
        "source_application": "GM3 Team Improvement",
        "rescoring_authority": False,
        "trade_execution_review": "Trade Decision",
        "trade_decision_facade": "script/trade_engine.py",
        "portfolio_evaluation_authority": "GM3 Team Improvement",
        "shared_core_additions_phase_1": [],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--focus-user-id", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--team-improvement-input")
    ap.add_argument("--quick-sims", type=int, default=200)
    ap.add_argument("--confirm-sims", type=int, default=1000)
    ap.add_argument("--trade-screen", type=int, default=20)
    ap.add_argument("--waiver-screen", type=int, default=20)
    ap.add_argument("--confirm-top", type=int, default=5)
    ap.add_argument("--portfolio-depth", type=int, default=6)
    ap.add_argument("--portfolio-sims", type=int, default=500)
    ap.add_argument("--portfolio-confirm-sims", type=int, default=5000)
    ap.add_argument("--portfolio-confirm-top", type=int, default=3)
    ap.add_argument("--trade-review-depth", type=int, default=1)
    ap.add_argument("--trade-review-quick-sims", type=int, default=200)
    ap.add_argument("--trade-review-confirm-sims", type=int, default=50000)
    ap.add_argument("--trade-review-search-depth", type=int, default=60)
    ap.add_argument("--seed", type=int, default=20260821)
    args = ap.parse_args()

    output = Path(args.output)
    if args.team_improvement_input:
        raw_output = Path(args.team_improvement_input)
    else:
        raw_output = output.with_suffix(".team-improvement.json")
        _run_team_improvement(args, raw_output)

    source = load_json(raw_output)
    if str(source.get("generated_for_user_id")) != str(args.focus_user_id):
        raise RuntimeError("Team Improvement output does not match requested focus user")

    trade_reviews = review_trade_candidates(
        source,
        args.focus_user_id,
        depth=args.trade_review_depth,
        quick_sims=args.trade_review_quick_sims,
        confirm_sims=args.trade_review_confirm_sims,
        search_depth=args.trade_review_search_depth,
        seed=args.seed,
    )
    board = build_board(
        source,
        focus_user_id=args.focus_user_id,
        portfolio_depth=args.portfolio_depth,
        portfolio_sims=args.portfolio_sims,
        portfolio_confirm_sims=args.portfolio_confirm_sims,
        portfolio_confirm_top=args.portfolio_confirm_top,
        seed=args.seed,
        trade_reviews=trade_reviews,
    )
    write_json(output, board)
    print(json.dumps({
        "model_version": MODEL_VERSION,
        "team": board.get("team_name"),
        "best_move": (board.get("best_move_available") or {}).get("description"),
        "ranked_opportunities": len(board.get("ranked_single_step_opportunities") or []),
        "portfolio_pairs_evaluated": (board.get("portfolio_optimization") or {}).get("candidate_pairs_evaluated"),
        "trade_decision_reviews": len(board.get("trade_decision_reviews") or []),
        "output": str(output),
    }, indent=2))


if __name__ == "__main__":
    main()
