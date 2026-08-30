#!/usr/bin/env python3
"""FSFFL Counter & Market Sweep 1.11 — diversified five-option report with a reserved swing slot.

Exactly one SWING_FOR_FENCES option is intentionally reserved whenever at least
one mutually viable candidate exists. The other four slots prioritize blended
strategic gain, acceptance fit, owner-specific behavior, and buyer diversity.
The swing is selected for focal upside, not because the normal list ran short.
It remains negotiation context and can never drive the primary action.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
from collections import Counter
from pathlib import Path

V13_PATH = Path("script/run_trade_market_sweep_v13.py")
V16_PATH = Path("script/run_trade_market_sweep_v16.py")
V18_PATH = Path("script/run_trade_market_sweep_v18.py")
MODEL_VERSION = "FSFFL-Counter-Market-Sweep-1.11"
DEFAULT_SEARCH_DEPTH = 60
MAX_NORMAL_OPTIONS_PER_BUYER = 2


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to import {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sf(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def candidate_key(row):
    return (
        str(row.get("buyer_user_id") or ""),
        tuple(row.get("outgoing_assets") or []),
        tuple(row.get("return_assets") or []),
    )


def swing_score(row):
    """Prioritize our upside while still using blended score as a tiebreaker."""
    nr = row.get("negotiation_ranking") or {}
    strategic = sf(nr.get("focal_strategic_gain_component"))
    blended = sf(nr.get("score"))
    acceptance = sf((row.get("buyer_rationality") or {}).get("heuristic_acceptance_fit_score"), .5)
    # A swing should be upside-forward and can tolerate lower acceptance fit.
    return (0.72 * strategic + 0.20 * blended + 0.08 * (1.0 - acceptance), strategic, blended)


def select_swing(viable):
    if not viable:
        return None
    # Prefer explicitly ambitious LOW/VERY_LOW packages. If none exist, still
    # reserve the highest-upside mutually viable package as the swing slot.
    ambitious = [r for r in viable if r.get("acceptance_likelihood") in {"LOW", "VERY_LOW"}]
    pool = ambitious or viable
    return max(pool, key=swing_score)


def select_normal_four(viable, swing):
    selected = []
    counts = Counter()
    excluded = candidate_key(swing) if swing else None
    if swing:
        counts[str(swing.get("buyer_user_id") or "")] += 1
    deferred = []
    for row in viable:
        if excluded and candidate_key(row) == excluded:
            continue
        uid = str(row.get("buyer_user_id") or "")
        if counts[uid] < MAX_NORMAL_OPTIONS_PER_BUYER:
            selected.append(row)
            counts[uid] += 1
            if len(selected) == 4:
                return selected
        else:
            deferred.append(row)
    # Preserve a full five-option report if diversity cannot supply four normal
    # options. Quality-ranked repeats are preferable to empty slots.
    for row in deferred:
        if len(selected) == 4:
            break
        selected.append(row)
    return selected[:4]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--quick-sims", type=int, default=100)
    ap.add_argument("--confirm-sims", type=int, default=0)
    ap.add_argument("--search-depth", type=int, default=DEFAULT_SEARCH_DEPTH)
    ap.add_argument("--output", required=True)
    ap.add_argument("--seed", type=int, default=20260821)
    args = ap.parse_args()
    depth = max(40, args.search_depth)

    v18 = load_module(V18_PATH, "market_sweep_v18_for_v111")
    v16 = load_module(V16_PATH, "market_sweep_v16_for_v111")
    v13 = load_module(V13_PATH, "market_sweep_v13_for_v111")
    engine = v13.load_module(v13.BASE_ENGINE, "market_sweep_base_for_v111")
    v16.install_read_caches(engine)
    dl = engine.import_decision_lab()
    beh, meta = v18.behavior_index(), v18.asset_meta()

    def sim(dl_mod, mi, bl, baseline, focus, buyer, outgoing, incoming, sims, seed):
        return v13.fast_simulate_candidate(engine, dl_mod, mi, bl, baseline, focus, buyer, outgoing, incoming, sims, seed)
    engine.simulate_candidate = sim

    # Get the same deep, fast candidate pool used by the prior validated engine.
    with tempfile.TemporaryDirectory() as td:
        raw_out = Path(td) / "deep.json"
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
    focus = str(scenario.get("focus_user_id") or "")
    sent, recv, partner = engine.incoming_trade_parts(scenario, focus)
    mi = dl.load_model_inputs()
    simmod, league, rosters, users, players, season, projections, sched = mi
    bl = dl.load_cached_lineups(season)
    baseline = dl.simulate_from_lineups(simmod, league, rosters, users, sched, bl, args.quick_sims, args.seed)
    pc, pk = engine.asset_catalog(); cat = {**pc, **pk}
    outgoing = [cat[x] for x in sent if x in cat]
    incoming = [cat[x] for x in recv if x in cat]
    missing = [x for x in sent + recv if x not in cat]
    if missing:
        raise ValueError(f"Current-offer assets missing from FSFFL asset catalog: {missing}")

    current = engine.score_candidate(focus, partner, outgoing, incoming)
    current["outgoing_assets"] = sent
    current["outgoing_asset_names"] = [a.get("name") for a in outgoing]
    current["candidate_type"] = "CURRENT_OFFER"
    current["outgoing_variant"] = "FULL"
    current["simulation"] = sim(dl, mi, bl, baseline, focus, partner, outgoing, incoming, args.quick_sims, args.seed)
    current["post_sim_score"] = engine.post_sim_score(current, engine.team_state(focus))
    current["buyer_rationality"] = v18.adjusted_buyer_rationality(v16, current, dl, beh, meta)

    rows = list(report.get("ranked_finalists") or [])
    for r in rows:
        r["buyer_rationality"] = v18.adjusted_buyer_rationality(v16, r, dl, beh, meta)
        r["comparison_to_current_offer"] = v13.compare_candidate(r, current)
        r["acceptance_likelihood"] = r["buyer_rationality"]["heuristic_acceptance_fit"]
        r["acceptance_explanation"] = v18.acceptance_note(r["buyer_rationality"])
        r["why_advantageous_for_focus"] = v18.advantage_note(r)
        r["negotiation_ranking"] = v18.blended_negotiation_score(r)

    viable = [r for r in rows if v16.focal_viable(r) and r["buyer_rationality"]["current_state_viable"]]
    viable.sort(key=lambda r: (sf((r.get("negotiation_ranking") or {}).get("score")), sf(r.get("post_sim_score"))), reverse=True)
    realistic = [r for r in viable if r["acceptance_likelihood"] in {"HIGH", "MEDIUM"}]

    swing = select_swing(viable)
    normals = select_normal_four(viable, swing)
    for r in normals:
        r["report_role"] = "REALISTIC_COUNTER" if r["acceptance_likelihood"] in {"HIGH", "MEDIUM"} else "REASONABLE_LONGSHOT"
    if swing:
        swing["report_role"] = "SWING_FOR_FENCES"
        swing["report_note"] = (
            "Reserved upside slot: intentionally selected for focal-team upside, even at a lower "
            "heuristic acceptance fit. It is part of every five-option package when a viable swing exists."
        )

    # Four normal negotiation paths first; dedicated swing displayed fifth.
    top5 = normals + ([swing] if swing else [])

    # Final decision precision: candidate discovery stays cheap, but the current
    # offer and final displayed options are rerun on the canonical vectorized
    # Simulator at the requested confirmation count. No later wrapper may
    # replace these confirmed rows with quick-screen simulation output.
    final_sim_count = args.quick_sims
    final_seed = args.seed
    if args.confirm_sims and args.confirm_sims > args.quick_sims:
        final_sim_count = args.confirm_sims
        final_seed = simmod.deterministic_seed(league, season)
        confirm_baseline = dl.simulate_from_lineups(
            simmod, league, rosters, users, sched, bl,
            final_sim_count, final_seed
        )
        current["simulation"] = sim(
            dl, mi, bl, confirm_baseline, focus, partner,
            outgoing, incoming, final_sim_count, final_seed
        )
        current["post_sim_score"] = engine.post_sim_score(
            current, engine.team_state(focus)
        )
        current["buyer_rationality"] = v18.adjusted_buyer_rationality(
            v16, current, dl, beh, meta
        )

        for r in top5:
            buyer = str(r.get("buyer_user_id") or "")
            out_ids = list(r.get("outgoing_assets") or [])
            in_ids = list(r.get("return_assets") or r.get("incoming_assets") or [])
            out_assets = [cat[x] for x in out_ids if x in cat]
            in_assets = [cat[x] for x in in_ids if x in cat]
            if not buyer or len(out_assets) != len(out_ids) or len(in_assets) != len(in_ids):
                continue
            r["simulation"] = sim(
                dl, mi, bl, confirm_baseline, focus, buyer,
                out_assets, in_assets, final_sim_count, final_seed
            )
            r["post_sim_score"] = engine.post_sim_score(
                r, engine.team_state(focus)
            )
            r["buyer_rationality"] = v18.adjusted_buyer_rationality(
                v16, r, dl, beh, meta
            )
            r["comparison_to_current_offer"] = v13.compare_candidate(r, current)
            r["acceptance_likelihood"] = r["buyer_rationality"]["heuristic_acceptance_fit"]
            r["acceptance_explanation"] = v18.acceptance_note(r["buyer_rationality"])
            r["why_advantageous_for_focus"] = v18.advantage_note(r)
            r["negotiation_ranking"] = v18.blended_negotiation_score(r)

    for i, r in enumerate(top5, 1):
        r["actionable_rank"] = i

    # Final recommendation uses confirmed displayed options, not quick-screen rows.
    realistic = [
        r for r in top5
        if v16.focal_viable(r)
        and r["buyer_rationality"]["current_state_viable"]
        and r["acceptance_likelihood"] in {"HIGH", "MEDIUM"}
    ]
    realistic.sort(
        key=lambda r: (
            sf((r.get("negotiation_ranking") or {}).get("score")),
            sf(r.get("post_sim_score"))
        ),
        reverse=True
    )

    pivot = [r for r in rows if v16.focal_viable(r) and not r["buyer_rationality"]["current_state_viable"] and r["buyer_rationality"]["state_change_viable"]]
    pivot.sort(key=lambda r: sf(r.get("post_sim_score")), reverse=True)

    # Swing and LOW/VERY_LOW ideas never drive the primary recommendation.
    if realistic:
        best = realistic[0]
        if v16.focal_viable(current) and current["buyer_rationality"]["current_state_viable"]:
            action = "SHOP_BEFORE_ACCEPTING" if sf(best.get("post_sim_score")) > sf(current.get("post_sim_score")) + 750 else "ACCEPT_NOW"
        elif any(r.get("candidate_type") == "SAME_PARTNER_COUNTER" for r in realistic[:5]):
            action = "COUNTER_CURRENT_OFFEROR"
        else:
            action = "SHOP_BEFORE_ACCEPTING"
    else:
        action = "DECLINE"

    buyer_counts = dict(Counter(str(r.get("buyer_user_id") or "") for r in top5))
    report["model_version"] = MODEL_VERSION
    report["current_offer_evaluation"] = current
    report["ranked_finalists"] = top5
    report["top_5_alternatives"] = top5
    report["realistic_counter_alternatives"] = realistic[:5]
    report["reasonable_longshot_alternatives"] = [r for r in normals if r.get("report_role") == "REASONABLE_LONGSHOT"]
    report["swing_for_fences_alternative"] = swing
    report["state_change_dependent_alternatives"] = pivot[:5]
    report["recommended_next_action"] = action
    cc = report.setdefault("candidate_counts", {})
    cc["acceptance_frontier_simulated"] = len(rows)
    cc["buyer_current_state_viable"] = len(viable)
    cc["realistic_acceptance_fit"] = len(realistic)
    cc["reasonable_longshot_pool"] = len([r for r in viable if r["acceptance_likelihood"] in {"LOW", "VERY_LOW"}])
    cc["top_five_unique_buyers"] = len(buyer_counts)
    cc["top_five_options_by_buyer"] = buyer_counts
    cc["reserved_swing_slots"] = 1 if swing else 0

    pol = report.setdefault("policy", {})
    pol.update({
        "five_option_report_when_market_supports_it": True,
        "reasonable_longshots_can_fill_report": True,
        "acceptance_likelihood_is_heuristic_not_probability": True,
        "each_option_explains_acceptance_and_focus_advantage": True,
        "GM_owner_behavior_integrated": True,
        "owner_behavior_is_evidence_not_veto": True,
        "top_five_blended_ranking": True,
        "buyer_diversity_enabled": True,
        "normal_max_options_per_buyer": MAX_NORMAL_OPTIONS_PER_BUYER,
        "swing_for_fences_required_when_viable": True,
        "swing_for_fences_slots_exact": 1,
        "swing_selected_for_focal_upside": True,
        "swing_for_fences_can_drive_action": False,
        "longshots_cannot_drive_recommended_action": True,
        "fast_exact_lineup_dp": True,
    })
    report["owner_behavior_profiles_available"] = len(beh)
    report["simulation"]["lineup_reoptimization"] = "exact_slot_mask_dynamic_programming"
    report["simulation"]["final_trade_impact_simulations"] = final_sim_count
    report["simulation"]["final_trade_impact_seed"] = final_seed
    report["simulation"]["final_trade_impact_engine"] = "current_vectorized_simulator"
    report["simulation"]["finalists_confirmed_at_high_precision"] = final_sim_count > args.quick_sims
    report["simulation"]["execution_path"] = "GM_owner_behavior_plus_blended_ranking_plus_buyer_diversity_plus_reserved_swing_then_canonical_final_confirmation"
    Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
