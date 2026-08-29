#!/usr/bin/env python3
"""FSFFL Counter & Market Sweep 1.25 — outcome-consistent option governance.

Extends validated 1.24 without changing candidate generation or simulation.
The prior option-vs-offer presentation layer could call an alternative BETTER
solely because its state-aware post-simulation score was >750 points higher,
even when a contender's expected points, wins, playoff probability and title
probability were all lower than the current offer. The inherited action logic
used the same score-only shortcut and could therefore force SHOP_BEFORE_ACCEPTING
for an objectively worse competitive alternative, including VERY_LOW acceptance
longshots.

1.25 separates search/ranking utility from decision semantics:
- state-aware score remains available for discovery and tradeoff ranking;
- for contenders, BETTER requires competitive Pareto dominance versus the
  current offer (no competitive outcome is worse and at least one is better);
- an option with a higher composite score but mixed/worse competitive outcomes
  is labeled MIXED rather than BETTER;
- when the current offer is mutually viable, only a non-VERY_LOW option that is
  actually BETTER may change ACCEPT_NOW into COUNTER/SHOP.

No player-specific exceptions are permitted.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent
V30 = SCRIPT / "run_trade_market_sweep_v30.py"
MODEL_VERSION = "FSFFL-Counter-Market-Sweep-1.25"
EPS = 1e-9


def load(path: Path, name: str):
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


def out_path():
    if "--output" not in sys.argv:
        return None
    i = sys.argv.index("--output")
    return Path(sys.argv[i + 1]) if i + 1 < len(sys.argv) else None


def metric(row, key):
    sim = row.get("simulation") or {}
    d = sim.get("focus_delta") or {}
    st = sim.get("strategic") or {}
    if key in d:
        return sf(d.get(key))
    if key == "net_title_equity_swing_against_focus":
        return sf(sim.get(key))
    return sf(st.get(key))


def objective_state(row):
    sim = row.get("simulation") or {}
    st = sim.get("strategic") or {}
    return str(st.get("objective_state") or row.get("focal_current_state") or "unknown")


def competitive_relation(row, current):
    """Pareto relation on the focal contender's simulated competitive outcomes."""
    keys = ("expected_points_for", "expected_wins", "playoff_probability", "championship_probability")
    deltas = {k: metric(row, k) - metric(current, k) for k in keys}
    any_better = any(v > EPS for v in deltas.values())
    any_worse = any(v < -EPS for v in deltas.values())
    if any_better and not any_worse:
        relation = "DOMINATES_CURRENT_OFFER"
    elif any_worse and not any_better:
        relation = "DOMINATED_BY_CURRENT_OFFER"
    elif not any_better and not any_worse:
        relation = "COMPETITIVELY_EQUIVALENT"
    else:
        relation = "COMPETITIVE_TRADEOFF"
    return relation, deltas


def compare(row, current):
    keys = (
        "expected_wins", "expected_points_for", "playoff_probability",
        "bye_probability", "championship_probability", "market_dynasty_delta",
        "strategic_value_delta", "liquidity_value_delta", "break_glass_delta",
        "roster_interaction_value_delta", "net_title_equity_swing_against_focus",
    )
    deltas = {k: round(metric(row, k) - metric(current, k), 5) for k in keys}
    score_delta = round(sf(row.get("post_sim_score")) - sf(current.get("post_sim_score")), 2)
    state = objective_state(current) or objective_state(row)
    relation, _ = competitive_relation(row, current)

    score_verdict = "BETTER" if score_delta > 750 else "WORSE" if score_delta < -750 else "MIXED"
    verdict = score_verdict
    guard_applied = False
    if state in {"contender", "elite_contender"}:
        # A higher composite/future-value score is a tradeoff, not a categorical
        # improvement, when it gives back any of the contender's core simulated
        # competitive outcomes. This is a semantic/dominance guard, not a new
        # calibrated coefficient.
        if score_verdict == "BETTER" and relation != "DOMINATES_CURRENT_OFFER":
            verdict = "MIXED"
            guard_applied = True
        elif score_verdict == "WORSE" and relation == "DOMINATES_CURRENT_OFFER":
            verdict = "MIXED"
            guard_applied = True

    drivers = []
    if abs(deltas["expected_points_for"]) >= 10:
        drivers.append(f"{deltas['expected_points_for']:+.1f} expected points")
    if abs(deltas["expected_wins"]) >= .05:
        drivers.append(f"{deltas['expected_wins']:+.2f} expected wins")
    if abs(deltas["playoff_probability"]) >= .01:
        drivers.append(f"{deltas['playoff_probability']*100:+.1f} pts playoff probability")
    if abs(deltas["championship_probability"]) >= .005:
        drivers.append(f"{deltas['championship_probability']*100:+.1f} pts championship probability")
    if abs(deltas["strategic_value_delta"]) >= 200:
        drivers.append(f"{deltas['strategic_value_delta']:+,.0f} franchise value")
    if abs(deltas["market_dynasty_delta"]) >= 500:
        drivers.append(f"{deltas['market_dynasty_delta']:+,.0f} dynasty value")
    if abs(deltas["liquidity_value_delta"]) >= 500:
        drivers.append(f"{deltas['liquidity_value_delta']:+,.0f} trade flexibility")
    if not drivers:
        drivers.append(f"{score_delta:+,.0f} state-aware score")

    if verdict == "BETTER":
        lead = "Clearly better than the current offer for the focal objective"
    elif verdict == "WORSE":
        lead = "Clearly worse than the current offer for the focal objective"
    else:
        lead = "A mixed tradeoff versus the current offer"
    if guard_applied:
        lead += "; composite score cannot override conflicting contender outcomes"

    return {
        "verdict_vs_current_offer": verdict,
        "raw_score_only_verdict": score_verdict,
        "post_sim_score_delta_vs_current_offer": score_delta,
        "metric_deltas_vs_current_offer": deltas,
        "competitive_relation_vs_current_offer": relation,
        "contender_outcome_consistency_guard_applied": guard_applied,
        "reason": lead + ", driven by " + ", ".join(drivers[:6]) + ".",
        "comparison_basis": "state_aware_score_plus_objective_state_outcome_consistency_and_key_simulation_strategic_deltas",
    }


def acceptance(row):
    return str(row.get("acceptance_likelihood") or ((row.get("buyer_rationality") or {}).get("heuristic_acceptance_fit")) or "")


def actionable_better(row):
    comp = row.get("comparison_to_current_offer") or {}
    return comp.get("verdict_vs_current_offer") == "BETTER" and acceptance(row) != "VERY_LOW"


def current_mutually_viable(current):
    focal = bool(current.get("focal_current_state_beneficial"))
    if not focal:
        # v1.24 current rows may not preserve the flag at top level; the state
        # score/constraint remains the canonical fallback used by v1.17.
        state = objective_state(current)
        comps = current.get("state_aware_score_components") or {}
        post = sf(current.get("post_sim_score"))
        if state in {"contender", "elite_contender"}:
            focal = post > 0 and current.get("championship_equity_constraint") == "PASS" and sf(comps.get("current")) > -500
        elif state == "rebuild":
            focal = post > 0 and sf(comps.get("future")) > 0
        elif state == "retool":
            focal = post > 0 and sf(comps.get("future")) > -250
        else:
            focal = post > 0
    buyer = bool((current.get("buyer_rationality") or {}).get("current_state_viable"))
    return focal and buyer


def recompute_action(report, inherited):
    current = report.get("current_offer_evaluation") or {}
    if not current_mutually_viable(current):
        return inherited, "INHERITED_CURRENT_OFFER_NOT_MUTUALLY_VIABLE"

    counters = [r for r in (report.get("suggested_counteroffers") or []) if actionable_better(r)]
    markets = [r for r in (report.get("market_sweep_alternatives") or []) if actionable_better(r)]
    if counters:
        return "COUNTER_CURRENT_OFFEROR", "ACTIONABLE_BETTER_SAME_PARTNER_COUNTER"
    if markets:
        return "SHOP_BEFORE_ACCEPTING", "ACTIONABLE_BETTER_MARKET_ALTERNATIVE"
    return "ACCEPT_NOW", "NO_ACTIONABLE_BETTER_OPTION_THAN_MUTUALLY_VIABLE_CURRENT_OFFER"


def main():
    v30 = load(V30, "market_v30_for_125")
    v30.main()
    out = out_path()
    if not out or not out.exists():
        return

    report = json.loads(out.read_text(encoding="utf-8"))
    current = report.get("current_offer_evaluation") or {}
    for section in ("suggested_counteroffers", "market_sweep_alternatives"):
        for row in report.get(section) or []:
            comp = compare(row, current)
            row["comparison_to_current_offer"] = comp
            row["why_prefer_over_current_offer"] = comp["reason"]
            row["why_advantageous_for_focus"] = comp["reason"]
            row["actionable_better_than_current_offer"] = actionable_better(row)

    inherited = str(report.get("recommended_next_action") or "REVIEW")
    final_action, action_basis = recompute_action(report, inherited)
    report["recommended_next_action_pre_outcome_consistency"] = inherited
    report["recommended_next_action"] = final_action

    gov = report.setdefault("governance", {})
    gov["option_outcome_consistency"] = {
        "search_score_separated_from_better_verdict": True,
        "contender_better_requires_competitive_pareto_dominance": True,
        "very_low_acceptance_can_trigger_shop_or_counter": False,
        "current_offer_action_recomputed_after_final_option_comparisons": True,
        "action_basis": action_basis,
        "player_specific_exceptions": False,
    }
    report["model_version"] = MODEL_VERSION
    report.setdefault("policy", {}).update({
        "option_comparison_model_version": "FSFFL-Option-Outcome-Consistency-1.0",
        "state_aware_score_is_search_and_tradeoff_signal_not_categorical_better_proof": True,
        "contender_better_requires_no_competitive_outcome_regression": True,
        "contender_better_requires_at_least_one_competitive_outcome_improvement": True,
        "very_low_acceptance_alternative_can_force_shop": False,
        "mixed_tradeoffs_remain_visible": True,
        "candidate_generation_unchanged": True,
        "simulation_unchanged": True,
    })
    report.setdefault("simulation", {})["execution_path"] = str((report.get("simulation") or {}).get("execution_path") or "") + "_plus_outcome_consistent_option_governance"
    out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    main()
