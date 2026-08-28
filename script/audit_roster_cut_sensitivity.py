#!/usr/bin/env python3
"""Downstream sensitivity audit for automatic roster-cut selection.

For retained production candidates that force a cut on the focal franchise,
re-run the same trade under each shortlisted legal cut using the exact lineup
optimizer, the same simulation seed, and the state-aware v1.14 utility. This
measures whether the fast retention-cost prescreen is decision-safe. It does
not fit or tune retention coefficients and does not mutate production output.
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import itertools
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent
BASE = SCRIPT / "run_trade_market_sweep.py"
V13 = SCRIPT / "run_trade_market_sweep_v13.py"
V20 = SCRIPT / "run_trade_market_sweep_v20.py"
OVERLAY = SCRIPT / "decision_lab_state_aware.py"
ROSTER_AWARE = SCRIPT / "roster_aware_trade.py"
MODEL_VERSION = "FSFFL-Roster-Cut-Sensitivity-1.0"


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


def candidate_key(row):
    buyer = str(row.get("buyer_user_id") or "")
    outs = ",".join(sorted(map(str, row.get("outgoing_assets") or [])))
    returns = ",".join(sorted(map(str, row.get("return_assets") or row.get("incoming_assets") or [])))
    return f"{buyer}|OUT:{outs}|IN:{returns}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--sims", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=20260821)
    ap.add_argument("--max-plans", type=int, default=27)
    args = ap.parse_args()

    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    focus_uid = str(report.get("focus_user_id") or "")
    if not focus_uid:
        raise SystemExit("Report is missing focus_user_id")

    engine = load(BASE, "cut_audit_base_engine")
    v13 = load(V13, "cut_audit_v13")
    v20 = load(V20, "cut_audit_v20")
    overlay = load(OVERLAY, "cut_audit_state_overlay")
    roster_aware = load(ROSTER_AWARE, "cut_audit_roster_aware")
    v20.install_engine_upgrade(engine, overlay)
    dl = engine.import_decision_lab()

    model_inputs = dl.load_model_inputs()
    simmod, league, canonical_rosters, users, players, season, projections, raw_schedule = model_inputs
    baseline_lineups = dl.load_cached_lineups(season)
    baseline = dl.simulate_from_lineups(
        simmod, league, canonical_rosters, users, raw_schedule,
        baseline_lineups, args.sims, args.seed
    )
    baseline_idx = dl.team_index(baseline)
    player_catalog, pick_catalog = engine.asset_catalog()
    catalog = {**player_catalog, **pick_catalog}

    # De-duplicate candidates that appear in multiple presentation sections.
    source_rows = []
    seen = set()
    for section in (
        "current_offer_evaluation", "suggested_counteroffers", "market_sweep_alternatives",
        "top_5_alternatives", "ranked_finalists", "realistic_counter_alternatives",
    ):
        vals = [report.get(section) or {}] if section == "current_offer_evaluation" else (report.get(section) or [])
        for row in vals:
            if not row:
                continue
            k = candidate_key(row)
            if k in seen:
                continue
            seen.add(k)
            source_rows.append(row)

    audited = []
    skipped = []

    for source in source_rows:
        source_resolution = ((source.get("simulation") or {}).get("roster_resolution") or {}).get(focus_uid) or {}
        required = int(source_resolution.get("required_cuts") or 0)
        shortlist = list(source_resolution.get("cut_candidate_shortlist") or [])
        if required <= 0 or len(shortlist) <= required:
            continue

        buyer_uid = str(source.get("buyer_user_id") or "")
        outgoing_ids = [str(x) for x in (source.get("outgoing_assets") or [])]
        incoming_ids = [str(x) for x in (source.get("return_assets") or source.get("incoming_assets") or [])]
        outgoing = [catalog[x] for x in outgoing_ids if x in catalog]
        incoming = [catalog[x] for x in incoming_ids if x in catalog]
        missing = [x for x in outgoing_ids + incoming_ids if x not in catalog]
        if missing or not buyer_uid:
            skipped.append({"candidate_key": candidate_key(source), "reason": "missing_asset_or_buyer", "missing": missing})
            continue

        trade_actions = engine.scenario_actions(focus_uid, buyer_uid, outgoing, incoming)
        pre_cut_rosters, _ = dl.apply_actions(canonical_rosters, trade_actions)
        touched = dl.touched_users(focus_uid, trade_actions)

        # Re-run the production legalizer to establish the exact eligible
        # shortlist under the current code, rather than trusting report text.
        _, fresh_resolution, _ = roster_aware.legalize_trade_rosters(
            dl, canonical_rosters, pre_cut_rosters, touched, league, players
        )
        fres = fresh_resolution.get(focus_uid) or {}
        if int(fres.get("required_cuts") or 0) != required:
            skipped.append({"candidate_key": candidate_key(source), "reason": "resolution_drift"})
            continue
        fresh_shortlist = list(fres.get("cut_candidate_shortlist") or [])
        eligible_ids = [str(x.get("player_id")) for x in fresh_shortlist if x.get("player_id")]
        plans = list(itertools.combinations(eligible_ids, required))
        if len(plans) > args.max_plans:
            skipped.append({"candidate_key": candidate_key(source), "reason": "plan_cap", "plan_count": len(plans)})
            continue

        results = []
        for plan in plans:
            hyp_rosters = copy.deepcopy(pre_cut_rosters)
            by_uid, _ = dl.roster_maps(hyp_rosters)
            focal_roster = by_uid.get(focus_uid)
            if not focal_roster:
                raise RuntimeError(f"Missing focal roster {focus_uid}")
            for pid in plan:
                dl.remove_player(focal_roster, pid)
            if len(roster_aware.active_player_ids(focal_roster)) > int(fres.get("effective_hypothetical_active_limit") or 0):
                raise RuntimeError(f"Forced cut plan did not legalize roster: {plan}")

            cut_action = {
                "type": "cut", "user_id": focus_uid, "players": list(plan),
                "automatic_roster_legalization": False,
                "cut_sensitivity_forced_plan": True,
            }
            effective_actions = list(trade_actions) + [cut_action]
            hyp_lineups, reoptimized = v13.fast_reoptimize_touched_lineups(
                dl, simmod, baseline_lineups, hyp_rosters, touched,
                league, users, players, projections
            )
            hyp = dl.simulate_from_lineups(
                simmod, league, hyp_rosters, users, raw_schedule,
                hyp_lineups, args.sims, args.seed
            )
            hidx = dl.team_index(hyp)
            b = baseline_idx[focus_uid]
            h = hidx[focus_uid]
            ob, oh = baseline_idx.get(buyer_uid), hidx.get(buyer_uid)
            title_delta = dl.delta(b.get("championship_probability"), h.get("championship_probability"))
            buyer_title_delta = dl.delta(ob.get("championship_probability"), oh.get("championship_probability")) if ob and oh else 0.0
            strategic = dl.strategic_summary(focus_uid, effective_actions)
            sim = {
                "actions": effective_actions,
                "trade_actions": trade_actions,
                "teams_reoptimized": reoptimized,
                "focus_before": b,
                "focus_after": h,
                "focus_delta": {
                    "expected_wins": dl.delta(b.get("expected_wins"), h.get("expected_wins")),
                    "expected_points_for": dl.delta(b.get("expected_points_for"), h.get("expected_points_for")),
                    "playoff_probability": dl.delta(b.get("playoff_probability"), h.get("playoff_probability")),
                    "bye_probability": dl.delta(b.get("bye_probability"), h.get("bye_probability")),
                    "championship_probability": title_delta,
                },
                "buyer_championship_probability_delta": buyer_title_delta,
                "net_title_equity_swing_against_focus": round(max(0.0, sf(buyer_title_delta)) - sf(title_delta), 5),
                "strategic": strategic,
            }
            scored = copy.deepcopy(source)
            scored["simulation"] = sim
            state = str(strategic.get("objective_state") or source.get("focal_current_state") or "unknown")
            score = v20.state_aware_post_sim_score(engine, scored, state)
            profile_lookup = {str(x.get("player_id")): x for x in fresh_shortlist}
            results.append({
                "cut_player_ids": list(plan),
                "cut_names": [str((profile_lookup.get(pid) or {}).get("name") or pid) for pid in plan],
                "retention_cost_sum": round(sum(sf((profile_lookup.get(pid) or {}).get("retention_cost")) for pid in plan), 2),
                "state_aware_post_sim_score": round(sf(score), 2),
                "expected_wins_delta": round(sf(sim["focus_delta"].get("expected_wins")), 5),
                "expected_points_delta": round(sf(sim["focus_delta"].get("expected_points_for")), 5),
                "playoff_probability_delta": round(sf(sim["focus_delta"].get("playoff_probability")), 5),
                "championship_probability_delta": round(sf(sim["focus_delta"].get("championship_probability")), 5),
                "strategic_value_delta": round(sf(strategic.get("strategic_value_delta")), 2),
                "market_dynasty_delta": round(sf(strategic.get("market_dynasty_delta")), 2),
                "break_glass_delta": round(sf(strategic.get("break_glass_delta")), 2),
            })

        default_ids = tuple(str(x.get("player_id")) for x in (fres.get("selected_cuts") or []))
        default_row = next((x for x in results if tuple(x["cut_player_ids"]) == default_ids), None)
        best = max(results, key=lambda x: (sf(x["state_aware_post_sim_score"]), -sf(x["retention_cost_sum"]))) if results else None
        score_spread = (max(sf(x["state_aware_post_sim_score"]) for x in results) - min(sf(x["state_aware_post_sim_score"]) for x in results)) if results else 0.0
        default_regret = sf(best.get("state_aware_post_sim_score")) - sf((default_row or {}).get("state_aware_post_sim_score")) if best else 0.0
        audited.append({
            "candidate_key": candidate_key(source),
            "required_cuts": required,
            "shortlist_size": len(fresh_shortlist),
            "default_cut_player_ids": list(default_ids),
            "default_cut_names": [str(x.get("name")) for x in (fres.get("selected_cuts") or [])],
            "best_downstream_cut_player_ids": list((best or {}).get("cut_player_ids") or []),
            "best_downstream_cut_names": list((best or {}).get("cut_names") or []),
            "default_matches_best_downstream_plan": bool(default_row and best and tuple(default_row["cut_player_ids"]) == tuple(best["cut_player_ids"])),
            "default_score_regret": round(default_regret, 2),
            "cut_plan_score_spread": round(score_spread, 2),
            "plans": sorted(results, key=lambda x: sf(x["state_aware_post_sim_score"]), reverse=True),
        })

    any_mismatch = any(not x["default_matches_best_downstream_plan"] for x in audited)
    max_regret = max([sf(x["default_score_regret"]) for x in audited] or [0.0])
    max_spread = max([sf(x["cut_plan_score_spread"]) for x in audited] or [0.0])
    payload = {
        "model_version": MODEL_VERSION,
        "source_report_model_version": report.get("model_version"),
        "purpose": "Measure downstream decision leverage of the retention-cost cut prescreen; no production cut is changed.",
        "interpretation": {
            "historical_validation": False,
            "coefficient_tuning": False,
            "same_seed_across_cut_plans": True,
            "uses_exact_lineup_reoptimization": True,
            "uses_state_aware_v1_14_utility": True,
            "roster_interaction_v1_24_not_reapplied": True,
        },
        "summary": {
            "audited_candidate_count": len(audited),
            "skipped_candidate_count": len(skipped),
            "any_default_cut_differs_from_best_downstream_plan": any_mismatch,
            "max_default_score_regret": round(max_regret, 2),
            "max_cut_plan_score_spread": round(max_spread, 2),
        },
        "candidates": audited,
        "skipped": skipped,
    }
    Path(args.output).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload["summary"], indent=2))
    for row in audited:
        print(json.dumps(row, indent=2))


if __name__ == "__main__":
    main()
