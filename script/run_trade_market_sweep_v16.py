#!/usr/bin/env python3
"""FSFFL Counter & Market Sweep 1.7 — true deep bilateral search.

Runs a genuinely deep candidate pool before applying focal and buyer-side
rationality gates. The actionable report prioritizes bilateral trades with at
least MEDIUM heuristic acceptance fit. If fewer than five such trades exist, it
may include at most one clearly labeled SWING_FOR_FENCES candidate that still
passes both teams' current-state strategic gates but has LOW/VERY_LOW heuristic
acceptance fit.

Human acceptance remains a strategic-fit heuristic, not a calibrated
probability. Canonical Sleeper / GM / Simulator state remains read-only.
"""
from __future__ import annotations

import argparse
import functools
import importlib.util
import json
import tempfile
from pathlib import Path
from typing import Any, Dict

V13_PATH = Path("script/run_trade_market_sweep_v13.py")
DECISION_UTILITY_PATH = Path("script/decision_utility.py")
MODEL_VERSION = "FSFFL-Counter-Market-Sweep-1.7"
DEFAULT_SEARCH_DEPTH = 40


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to import {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def install_read_caches(engine):
    """Memoize immutable GM reads for this process only."""
    engine.franchise_index = functools.lru_cache(maxsize=1)(engine.franchise_index)
    engine.asset_catalog = functools.lru_cache(maxsize=1)(engine.asset_catalog)
    engine.command_center = functools.lru_cache(maxsize=None)(engine.command_center)
    engine.strategic_assets = functools.lru_cache(maxsize=None)(engine.strategic_assets)
    engine.need_map = functools.lru_cache(maxsize=None)(engine.need_map)
    engine.team_state = functools.lru_cache(maxsize=None)(engine.team_state)
    return engine


def buyer_rationality(row: Dict[str, Any], dl) -> Dict[str, Any]:
    """Describe buyer incentive using the same continuous utility as the focal side.

    This replaces categorical state-specific title/value floors as decision
    authority. Acceptance remains a plausibility description, not a calibrated
    probability.
    """
    sim = row.get("simulation") or {}
    buyer_uid = str(row.get("buyer_user_id") or "")
    state = str(row.get("buyer_state") or "unknown")
    actions = sim.get("actions") or []
    bs = sim.get("buyer_strategic") or (
        dl.strategic_summary(buyer_uid, actions) if buyer_uid and actions else {}
    )
    dynasty = float(bs.get("market_dynasty_delta") or 0.0)
    redraft = float(bs.get("market_redraft_delta") or 0.0)
    break_glass = float(bs.get("break_glass_delta") or 0.0)
    buyer_delta = sim.get("buyer_delta") or {}
    title = float(
        buyer_delta.get(
            "championship_probability",
            sim.get("buyer_championship_probability_delta") or 0.0,
        )
        or 0.0
    )

    utility_score = None
    utility_status = "UNAVAILABLE_NEUTRAL_SEARCH"
    if bs.get("objective_weights"):
        utility = load_module(DECISION_UTILITY_PATH, "buyer_shared_decision_utility")
        buyer_sim = {
            "focus_delta": buyer_delta,
            "strategic": bs,
            # Counterparty incentive should not subtract the focal-team externality.
            # That is a focal strategic consideration, not a cost to the buyer.
            "net_title_equity_swing_against_focus": 0.0,
        }
        resolved = utility.score(buyer_sim)
        utility_score = float(resolved.get("score") or 0.0)
        utility_status = resolved.get("scale_status") or "PROVISIONAL_SHARED_UTILITY"

    # Missing governed buyer utility no longer falls back to categorical hard
    # thresholds. Keep the candidate searchable and report uncertainty.
    viable = True if utility_score is None else utility_score >= 0.0
    label = (
        "BUYER_UTILITY_NONNEGATIVE"
        if utility_score is not None and viable
        else "BUYER_UTILITY_NEGATIVE"
        if utility_score is not None
        else "BUYER_UTILITY_UNAVAILABLE"
    )
    reason = (
        "buyer-side shared continuous utility is non-negative"
        if utility_score is not None and viable
        else "buyer-side shared continuous utility is negative"
        if utility_score is not None
        else "governed buyer utility unavailable; candidate retained rather than rejected by a legacy heuristic"
    )

    # Convert utility to a bounded descriptive fit using transaction exposure as
    # its own scale. This removes the old state-specific floors and hand-set
    # title/dynasty/break-glass coefficients. It is not an acceptance probability.
    sent = list(bs.get("sent") or [])
    received = list(bs.get("received") or [])
    exposure = sum(
        abs(float(x.get("market_dynasty") or x.get("base_franchise_value") or 0.0))
        for x in sent + received
    )
    if utility_score is None or exposure <= 0:
        score = 0.5
    else:
        import math
        score = round(max(0.0, min(1.0, 0.5 + 0.5 * math.tanh(utility_score / exposure))), 4)
    band = "HIGH" if score >= 0.68 else "MEDIUM" if score >= 0.48 else "LOW" if score >= 0.28 else "VERY_LOW"

    return {
        "buyer_state": state,
        "current_state_gate": label,
        "current_state_viable": bool(viable),
        "state_change_viable": False,
        "heuristic_acceptance_fit_score": score,
        "heuristic_acceptance_fit": band,
        "acceptance_band_is_descriptive_not_probability": True,
        "reason": reason,
        "buyer_decision_utility_score": None if utility_score is None else round(utility_score, 2),
        "buyer_decision_utility_status": utility_status,
        "buyer_utility_exposure_scale": round(exposure, 2),
        "buyer_title_delta": round(title, 5),
        "buyer_market_dynasty_delta": round(dynasty, 2),
        "buyer_market_redraft_delta": round(redraft, 2),
        "buyer_break_glass_delta": round(break_glass, 2),
        "title_loss_floor_for_current_state": None,
        "categorical_buyer_state_thresholds_authoritative": False,
    }

def focal_viable(row: Dict[str, Any]) -> bool:
    return row.get("championship_equity_constraint") == "PASS" and row.get("plausibility") in {"HIGH", "MEDIUM"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--quick-sims", type=int, default=100)
    ap.add_argument("--confirm-sims", type=int, default=0)
    ap.add_argument("--search-depth", type=int, default=DEFAULT_SEARCH_DEPTH)
    ap.add_argument("--output", required=True)
    ap.add_argument("--seed", type=int, default=20260821)
    args = ap.parse_args()
    depth = max(20, args.search_depth)

    v13 = load_module(V13_PATH, "market_sweep_v13_for_v17")
    engine = v13.load_module(v13.BASE_ENGINE, "market_sweep_base_for_v17")
    install_read_caches(engine)
    dl = engine.import_decision_lab()

    def patched_simulate_candidate(dl_mod, model_inputs, baseline_lineups, baseline,
                                   focus_uid, buyer_uid, outgoing, incoming, sims, seed):
        return v13.fast_simulate_candidate(
            engine, dl_mod, model_inputs, baseline_lineups, baseline,
            focus_uid, buyer_uid, outgoing, incoming, sims, seed
        )
    engine.simulate_candidate = patched_simulate_candidate

    with tempfile.TemporaryDirectory() as td:
        raw_out = Path(td) / "deep_base.json"
        v13.run_base_engine_in_process(engine, [
            "--scenario", args.scenario,
            "--quick-sims", str(args.quick_sims),
            "--confirm-sims", str(args.confirm_sims),
            "--shortlist", str(depth),
            "--finalists", str(depth),
            "--seed", str(args.seed),
            "--output", str(raw_out),
        ])
        report = json.loads(raw_out.read_text(encoding="utf-8"))

    scenario = engine.load_json(Path(args.scenario), {}) or {}
    focus_uid = str(scenario.get("focus_user_id") or "")
    sent_ids, received_ids, current_partner = engine.incoming_trade_parts(scenario, focus_uid)
    model_inputs = dl.load_model_inputs()
    simmod, league, rosters, users, players, season, projections, raw_schedule = model_inputs
    baseline_lineups = dl.load_cached_lineups(season)
    baseline = dl.simulate_from_lineups(
        simmod, league, rosters, users, raw_schedule, baseline_lineups, args.quick_sims, args.seed
    )
    player_catalog, pick_catalog = engine.asset_catalog()
    catalog = {**player_catalog, **pick_catalog}
    outgoing = [catalog[x] for x in sent_ids if x in catalog]
    incoming = [catalog[x] for x in received_ids if x in catalog]
    missing = [x for x in sent_ids + received_ids if x not in catalog]
    if missing:
        raise ValueError(f"Current-offer assets missing from FSFFL asset catalog: {missing}")

    current = engine.score_candidate(focus_uid, current_partner, outgoing, incoming)
    current["outgoing_assets"] = sent_ids
    current["outgoing_asset_names"] = [a.get("name") for a in outgoing]
    current["candidate_type"] = "CURRENT_OFFER"
    current["outgoing_variant"] = "FULL"
    current["simulation"] = patched_simulate_candidate(
        dl, model_inputs, baseline_lineups, baseline, focus_uid, current_partner,
        outgoing, incoming, args.quick_sims, args.seed
    )
    current["post_sim_score"] = engine.post_sim_score(current, engine.team_state(focus_uid))
    current["buyer_rationality"] = buyer_rationality(current, dl)

    rows = list(report.get("ranked_finalists") or [])
    for row in rows:
        row["buyer_rationality"] = buyer_rationality(row, dl)
        row["comparison_to_current_offer"] = v13.compare_candidate(row, current)

    mutually_viable = [r for r in rows if focal_viable(r) and r["buyer_rationality"]["current_state_viable"]]
    mutually_viable.sort(key=lambda r: (
        float(r.get("post_sim_score") or 0.0),
        float(r["buyer_rationality"]["heuristic_acceptance_fit_score"]),
    ), reverse=True)

    realistic = [
        r for r in mutually_viable
        if r["buyer_rationality"]["heuristic_acceptance_fit"] in {"HIGH", "MEDIUM"}
    ]
    swing_pool = [
        r for r in mutually_viable
        if r["buyer_rationality"]["heuristic_acceptance_fit"] in {"LOW", "VERY_LOW"}
    ]

    # Prefer five realistic counters. Only if the realistic pool has fewer than
    # five do we permit one explicitly labeled aspirational option.
    top5 = realistic[:5]
    swing = None
    if len(top5) < 5 and swing_pool:
        swing = swing_pool[0]
        swing["report_role"] = "SWING_FOR_FENCES"
        swing["report_note"] = (
            "Aspirational counter: passes both teams' modeled current-state strategic gates, "
            "but heuristic human acceptance fit is LOW/VERY_LOW. Do not treat as a likely yes."
        )
        top5.append(swing)

    for row in top5:
        row.setdefault("report_role", "REALISTIC_COUNTER")
    for i, row in enumerate(top5, 1):
        row["actionable_rank"] = i

    pivot = [r for r in rows if focal_viable(r)
             and not r["buyer_rationality"]["current_state_viable"]
             and r["buyer_rationality"]["state_change_viable"]]
    pivot.sort(key=lambda r: float(r.get("post_sim_score") or 0.0), reverse=True)
    rejected = [r for r in rows if r["buyer_rationality"]["current_state_gate"] == "BUYER_IRRATIONAL"]

    # Negotiation action is driven only by realistic counters; the swing slot
    # can never by itself cause SHOP/COUNTER/ACCEPT behavior.
    if not realistic:
        action = "DECLINE"
    elif focal_viable(current) and current["buyer_rationality"]["current_state_viable"]:
        action = "SHOP_BEFORE_ACCEPTING" if realistic[0].get("post_sim_score", 0) > current.get("post_sim_score", 0) + 750 else "ACCEPT_NOW"
    elif any(r.get("candidate_type") == "SAME_PARTNER_COUNTER" for r in realistic[:5]):
        action = "COUNTER_CURRENT_OFFEROR"
    else:
        action = "SHOP_BEFORE_ACCEPTING"

    report["model_version"] = MODEL_VERSION
    report["current_offer_evaluation"] = current
    report["ranked_finalists"] = top5
    report["top_5_alternatives"] = top5
    report["realistic_counter_alternatives"] = realistic[:5]
    report["swing_for_fences_alternative"] = swing
    report["state_change_dependent_alternatives"] = pivot[:5]
    report["buyer_irrational_candidates_excluded"] = len(rejected)
    report["recommended_next_action"] = action
    report.setdefault("candidate_counts", {})["deep_search_simulated"] = len(rows)
    report["candidate_counts"]["buyer_current_state_viable"] = len(mutually_viable)
    report["candidate_counts"]["realistic_acceptance_fit"] = len(realistic)
    report["candidate_counts"]["swing_for_fences_pool"] = len(swing_pool)
    report["candidate_counts"]["state_change_dependent"] = len(pivot)
    report["candidate_counts"]["buyer_irrational_excluded"] = len(rejected)
    report.setdefault("policy", {})["buyer_current_state_rationality_gate"] = True
    report["policy"]["state_change_dependent_candidates_separated"] = True
    report["policy"]["heuristic_acceptance_fit_not_probability"] = True
    report["policy"]["actionable_realistic_counters_require_medium_or_high_acceptance_fit"] = True
    report["policy"]["swing_for_fences_slots_max"] = 1
    report["policy"]["swing_for_fences_cannot_drive_recommended_action"] = True
    report["policy"]["deep_search_replaces_filtered_candidates"] = True
    report["policy"]["focal_and_counterparty_must_both_pass"] = True
    report["policy"]["fast_exact_lineup_dp"] = True
    report["simulation"]["lineup_reoptimization"] = "exact_slot_mask_dynamic_programming"

    Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
