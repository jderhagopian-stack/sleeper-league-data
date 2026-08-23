#!/usr/bin/env python3
"""FSFFL Counter & Market Sweep 1.8 — five-option negotiation report.

Returns up to five useful counters even when none reaches MEDIUM/HIGH heuristic
acceptance fit. The report distinguishes realistic counters from reasonable
longshots and permits at most one swing-for-the-fences option. Human acceptance
is a heuristic fit band, not a calibrated probability. Canonical state is read-only.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
from pathlib import Path

V13_PATH = Path("script/run_trade_market_sweep_v13.py")
V17_PATH = Path("script/run_trade_market_sweep_v16.py")
MODEL_VERSION = "FSFFL-Counter-Market-Sweep-1.8"
DEFAULT_SEARCH_DEPTH = 60


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to import {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def acceptance_note(br):
    band = br.get("heuristic_acceptance_fit") or "UNKNOWN"
    state = br.get("buyer_state") or "unknown"
    title = float(br.get("buyer_title_delta") or 0.0)
    dyn = float(br.get("buyer_market_dynasty_delta") or 0.0)
    if band in {"HIGH", "MEDIUM"}:
        return f"{band}: modeled package is reasonably aligned with this {state} manager's current objective."
    if band == "LOW":
        return f"LOW: possible, but the {state} manager is giving up meaningful utility (title delta {title:+.1%}, dynasty delta {dyn:+.0f})."
    return f"VERY LOW: an aggressive ask; current-state fit is technically viable but the buyer sacrifices substantial modeled utility (title delta {title:+.1%}, dynasty delta {dyn:+.0f})."


def advantage_note(row):
    comp = row.get("comparison_to_current_offer") or {}
    md = comp.get("metric_deltas_vs_current_offer") or {}
    pieces = []
    champ = float(md.get("championship_probability") or 0.0)
    wins = float(md.get("expected_wins") or 0.0)
    dyn = float(md.get("market_dynasty_delta") or 0.0)
    if champ:
        pieces.append(f"championship probability {champ:+.1%} vs current offer")
    if wins:
        pieces.append(f"expected wins {wins:+.2f}")
    if dyn:
        pieces.append(f"dynasty value {dyn:+.0f}")
    verdict = comp.get("verdict_vs_current_offer") or "MIXED"
    return f"{verdict} than current offer: " + (", ".join(pieces) if pieces else "better strategic fit under the model")


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

    v17 = load_module(V17_PATH, "market_sweep_v17_for_v18")
    v13 = load_module(V13_PATH, "market_sweep_v13_for_v18")
    engine = v13.load_module(v13.BASE_ENGINE, "market_sweep_base_for_v18")
    v17.install_read_caches(engine)
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
    baseline = dl.simulate_from_lineups(simmod, league, rosters, users, raw_schedule, baseline_lineups, args.quick_sims, args.seed)
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
    current["simulation"] = patched_simulate_candidate(dl, model_inputs, baseline_lineups, baseline, focus_uid, current_partner, outgoing, incoming, args.quick_sims, args.seed)
    current["post_sim_score"] = engine.post_sim_score(current, engine.team_state(focus_uid))
    current["buyer_rationality"] = v17.buyer_rationality(current, dl)

    rows = list(report.get("ranked_finalists") or [])
    for row in rows:
        row["buyer_rationality"] = v17.buyer_rationality(row, dl)
        row["comparison_to_current_offer"] = v13.compare_candidate(row, current)
        row["acceptance_likelihood"] = row["buyer_rationality"]["heuristic_acceptance_fit"]
        row["acceptance_explanation"] = acceptance_note(row["buyer_rationality"])
        row["why_advantageous_for_focus"] = advantage_note(row)

    mutually_viable = [r for r in rows if v17.focal_viable(r) and r["buyer_rationality"]["current_state_viable"]]
    mutually_viable.sort(key=lambda r: (
        float(r["buyer_rationality"].get("heuristic_acceptance_fit_score") or 0.0),
        float(r.get("post_sim_score") or 0.0),
    ), reverse=True)

    realistic = [r for r in mutually_viable if r["acceptance_likelihood"] in {"HIGH", "MEDIUM"}]
    longshots = [r for r in mutually_viable if r["acceptance_likelihood"] in {"LOW", "VERY_LOW"}]

    # Always try to give the manager five negotiation paths when the market has
    # at least five bilateral-current-state-viable candidates. Realistic deals
    # come first; reasonable longshots fill the remaining slots.
    top5 = list(realistic[:5])
    remaining = 5 - len(top5)
    if remaining > 0:
        top5.extend(longshots[:remaining])

    for row in top5:
        if row["acceptance_likelihood"] in {"HIGH", "MEDIUM"}:
            row["report_role"] = "REALISTIC_COUNTER"
        else:
            row["report_role"] = "REASONABLE_LONGSHOT"

    # At most one selected VERY_LOW/high-upside deal is explicitly called the
    # swing. Prefer the best focal post-sim score among selected VERY_LOW rows.
    very_low = [r for r in top5 if r["acceptance_likelihood"] == "VERY_LOW"]
    if very_low:
        swing = max(very_low, key=lambda r: float(r.get("post_sim_score") or 0.0))
        swing["report_role"] = "SWING_FOR_FENCES"
        swing["report_note"] = "Aggressive ask with very low heuristic acceptance fit; included because the upside to the focal team is unusually strong."
    else:
        swing = None

    # Sort presentation by role quality, then acceptance fit and focal utility.
    role_rank = {"REALISTIC_COUNTER": 3, "REASONABLE_LONGSHOT": 2, "SWING_FOR_FENCES": 1}
    top5.sort(key=lambda r: (
        role_rank.get(r.get("report_role"), 0),
        float(r["buyer_rationality"].get("heuristic_acceptance_fit_score") or 0.0),
        float(r.get("post_sim_score") or 0.0),
    ), reverse=True)
    for i, row in enumerate(top5, 1):
        row["actionable_rank"] = i

    pivot = [r for r in rows if v17.focal_viable(r) and not r["buyer_rationality"]["current_state_viable"] and r["buyer_rationality"]["state_change_viable"]]
    pivot.sort(key=lambda r: float(r.get("post_sim_score") or 0.0), reverse=True)

    # Recommendation semantics remain conservative: LOW/VERY_LOW alternatives
    # are negotiation ideas, not enough by themselves to turn a bad current
    # offer into SHOP/COUNTER/ACCEPT.
    if realistic:
        if v17.focal_viable(current) and current["buyer_rationality"]["current_state_viable"]:
            action = "SHOP_BEFORE_ACCEPTING" if realistic[0].get("post_sim_score", 0) > current.get("post_sim_score", 0) + 750 else "ACCEPT_NOW"
        elif any(r.get("candidate_type") == "SAME_PARTNER_COUNTER" for r in realistic[:5]):
            action = "COUNTER_CURRENT_OFFEROR"
        else:
            action = "SHOP_BEFORE_ACCEPTING"
    else:
        action = "DECLINE"

    report["model_version"] = MODEL_VERSION
    report["current_offer_evaluation"] = current
    report["ranked_finalists"] = top5
    report["top_5_alternatives"] = top5
    report["realistic_counter_alternatives"] = realistic[:5]
    report["reasonable_longshot_alternatives"] = [r for r in top5 if r.get("report_role") == "REASONABLE_LONGSHOT"]
    report["swing_for_fences_alternative"] = swing
    report["state_change_dependent_alternatives"] = pivot[:5]
    report["recommended_next_action"] = action
    report.setdefault("candidate_counts", {})["acceptance_frontier_simulated"] = len(rows)
    report["candidate_counts"]["buyer_current_state_viable"] = len(mutually_viable)
    report["candidate_counts"]["realistic_acceptance_fit"] = len(realistic)
    report["candidate_counts"]["reasonable_longshot_pool"] = len(longshots)
    report.setdefault("policy", {})["five_option_report_when_market_supports_it"] = True
    report["policy"]["reasonable_longshots_can_fill_report"] = True
    report["policy"]["acceptance_likelihood_is_heuristic_not_probability"] = True
    report["policy"]["each_option_explains_acceptance_and_focus_advantage"] = True
    report["policy"]["swing_for_fences_slots_max"] = 1
    report["policy"]["longshots_cannot_drive_recommended_action"] = True
    report["policy"]["fast_exact_lineup_dp"] = True
    report["simulation"]["lineup_reoptimization"] = "exact_slot_mask_dynamic_programming"

    Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
