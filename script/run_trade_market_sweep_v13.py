#!/usr/bin/env python3
"""FSFFL Counter & Market Sweep 1.3.

Performance-focused wrapper around the 1.1 candidate generator. It preserves
1.2 Top-5/current-offer comparison behavior, but replaces the exponential DFS
lineup re-optimization used by the Decision Lab with an exact dynamic-programming
assignment solver for hypothetical rosters.

The DP solves the same maximum projected-mean legal lineup assignment problem
in O(players * slots * 2^slots), which is tiny for the FSFFL nine-slot lineup.
Canonical Sleeper / GM / Simulator state remains read-only.

Roster-aware trade resolution is applied before lineup optimization: if a
hypothetical trade would exceed the Sleeper active-roster limit, retention values
provide a fast legal prescreen. For final 50,000-simulation candidates, tractable
focal cut-plan spaces are enumerated and selected by downstream canonical Trade
Score before the final simulation.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import importlib.util
import itertools
import io
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

BASE_ENGINE = Path("script/run_trade_market_sweep.py")
ROSTER_AWARE = Path("script/roster_aware_trade.py")
GM_CORE = Path("script/build_fsffl_gm_engine.py")
FINAL_CUT_PLAN_MAX_COMBINATIONS = 27
FINAL_CUT_PLAN_SCREEN_SIMS = 1000
MODEL_VERSION = "FSFFL-Counter-Market-Sweep-1.3.2"

_GM_CORE_POSITION_NEED = None
_GM_CORE_PLAYER_VALUES = None
_GM_BASELINE_POSITION_NEED_CACHE = {}


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to import {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fast_optimize_weekly_lineup(simmod, roster, week, league, players, projections):
    """Exact max-weight legal lineup assignment via canonical Simulator when available."""
    canonical = getattr(simmod, "optimize_fsffl_fast", None)
    if callable(canonical):
        return canonical(roster, week, league, players, projections)
    core = getattr(simmod, "core", simmod)
    candidates = []
    taxi = set(roster.get("taxi") or [])
    for pid in roster.get("players") or []:
        if pid in taxi:
            continue
        meta = core.player_meta(players, projections, pid)
        pos = meta.get("position")
        pr = core.projection_for(projections, pid, week)
        if not pos or pr is None or pr["active_probability"] <= 0:
            continue
        candidates.append({**meta, **pr})

    slots = core.lineup_slots(league)
    states = {0: (0.0, {})}
    for c in candidates:
        weight = float(c["mean"]) * float(c["active_probability"])
        prior = list(states.items())
        for mask, (value, assign) in prior:
            for idx, slot in enumerate(slots):
                bit = 1 << idx
                if mask & bit or not core.eligible(c["position"], slot):
                    continue
                new_mask = mask | bit
                new_value = value + weight
                old = states.get(new_mask)
                if old is None or new_value > old[0]:
                    new_assign = dict(assign)
                    new_assign[idx] = c
                    states[new_mask] = (new_value, new_assign)

    _, best_assign = max(states.values(), key=lambda x: x[0])
    lineup = []
    for idx, slot in enumerate(slots):
        c = best_assign.get(idx)
        if c is None:
            lineup.append({
                "slot": slot, "player_id": None, "name": "EMPTY", "position": None,
                "mean": 0.0, "sd": 0.1, "active_probability": 0.0,
            })
        else:
            lineup.append({"slot": slot, **c})
    return lineup


def fast_reoptimize_touched_lineups(dl, simmod, baseline_lineups, hypothetical_rosters,
                                     touched_uids, league, users, players, projections):
    lineups = copy.deepcopy(baseline_lineups)
    by_uid, _ = dl.roster_maps(hypothetical_rosters)
    reg_weeks = getattr(simmod, "core", simmod).regular_season_weeks(league)
    playoff_start = int((league.get("settings") or {}).get("playoff_week_start") or 15)
    all_weeks = sorted(set(reg_weeks + [playoff_start, playoff_start + 1, playoff_start + 2]))
    reoptimized = []
    for uid in touched_uids:
        roster = by_uid.get(str(uid))
        if not roster:
            continue
        rid = int(roster.get("roster_id"))
        lineups[rid] = {}
        for week in all_weeks:
            lineups[rid][week] = fast_optimize_weekly_lineup(
                simmod, roster, week, league, players, projections
            )
        reoptimized.append(rid)
    return lineups, reoptimized


_GM_CORE_POSITION_NEED = None


def position_need_snapshot(engine, rosters, focus_uid):
    """Recompute GM3 positional need for a hypothetical roster state.

    This snapshot is deterministic with respect to roster + canonical asset
    values and does not depend on Monte Carlo simulation count. It therefore
    belongs in both quick candidate evaluation and 50k finalist confirmation.

    Uses the same optimized-team-strength and league-relative starter/depth
    formula that produces GM3 command-center position needs. This is model
    output, not report-layer inference.
    """
    global _GM_CORE_POSITION_NEED, _GM_CORE_PLAYER_VALUES
    if _GM_CORE_POSITION_NEED is None:
        _GM_CORE_POSITION_NEED = load_module(GM_CORE, "gm_core_position_need_for_trade")
    gm = _GM_CORE_POSITION_NEED
    if _GM_CORE_PLAYER_VALUES is None:
        pc, _ = engine.asset_catalog()
        _GM_CORE_PLAYER_VALUES = {
            str(row.get("player_id")): {
                "market_redraft": float(row.get("market_redraft") or 0.0),
                "market_dynasty": float(row.get("market_dynasty") or 0.0),
                "position": row.get("position"),
            }
            for row in pc.values()
            if row.get("player_id") is not None
        }
    teams = gm.optimized_team_strengths(rosters, _GM_CORE_PLAYER_VALUES, {})
    row = teams.get(str(focus_uid)) or {}
    return {
        "position_need": dict(row.get("position_need") or {}),
        "contender_score": row.get("contender_score"),
        "dynasty_roster_score": row.get("dynasty_roster_score"),
        "starter_redraft_value": row.get("starter_redraft_value"),
        "starter_dynasty_value": row.get("starter_dynasty_value"),
    }


def _simulate_resolved_candidate(engine, dl, model_inputs, baseline_lineups, baseline,
                                 focus_uid, buyer_uid, actions, hypothetical_rosters,
                                 effective_actions, roster_resolution, auto_cut_actions,
                                 sims, seed):
    simmod, league, canonical_rosters, users, players, season, projections, raw_schedule = model_inputs
    touched = dl.touched_users(focus_uid, actions)
    hypothetical_lineups, reoptimized = fast_reoptimize_touched_lineups(
        dl, simmod, baseline_lineups, hypothetical_rosters, touched,
        league, users, players, projections
    )
    metadata_expected = 0
    metadata_missing = []
    for rid in reoptimized:
        for week, rows in (hypothetical_lineups.get(rid) or {}).items():
            for row in rows or []:
                pid = str(row.get("player_id") or "")
                if not pid:
                    continue
                meta = (players or {}).get(pid) or {}
                team = meta.get("team") or meta.get("team_abbr")
                if team:
                    metadata_expected += 1
                    if not row.get("nfl_team"):
                        metadata_missing.append({"roster_id": rid, "week": week, "player_id": pid})
    hyp = dl.simulate_from_lineups(
        simmod, league, hypothetical_rosters, users, raw_schedule,
        hypothetical_lineups, sims, seed
    )
    bidx, hidx = dl.team_index(baseline), dl.team_index(hyp)
    b, h = bidx[focus_uid], hidx[focus_uid]
    ob, oh = bidx.get(buyer_uid), hidx.get(buyer_uid)
    strategic = dl.strategic_summary(focus_uid, effective_actions)
    buyer_strategic = dl.strategic_summary(buyer_uid, effective_actions) if ob and oh else {}
    baseline_teams = list((baseline or {}).get("teams") or [])
    def mean_metric(key):
        vals = [float(x.get(key) or 0.0) for x in baseline_teams]
        return (sum(vals) / len(vals)) if vals else 0.0
    league_reference = {
        "team_count": len(baseline_teams),
        "expected_wins_mean": mean_metric("expected_wins"),
        "expected_points_for_mean": mean_metric("expected_points_for"),
        "playoff_probability_mean": mean_metric("playoff_probability"),
        "championship_probability_mean": mean_metric("championship_probability"),
        "source": "canonical_baseline_simulator_league_mean",
    }
    # Deterministic roster diagnosis is valid at every simulation budget.
    # Suppressing it for quick market-sweep candidates changes the Shared
    # Decision Utility evidence set (2 signals instead of 3) and can materially
    # over-rank alternatives. Cache only the immutable baseline snapshot.
    baseline_cache_key = str(focus_uid)
    if baseline_cache_key not in _GM_BASELINE_POSITION_NEED_CACHE:
        _GM_BASELINE_POSITION_NEED_CACHE[baseline_cache_key] = position_need_snapshot(
            engine, canonical_rosters, focus_uid
        )
    needs_before = copy.deepcopy(_GM_BASELINE_POSITION_NEED_CACHE[baseline_cache_key])
    needs_after = position_need_snapshot(engine, hypothetical_rosters, focus_uid)
    title_delta = dl.delta(b.get("championship_probability"), h.get("championship_probability"))
    buyer_title_delta = dl.delta(ob.get("championship_probability"), oh.get("championship_probability")) if ob and oh else 0.0
    buyer_delta = {
        "expected_wins": dl.delta(ob.get("expected_wins"), oh.get("expected_wins")) if ob and oh else 0.0,
        "expected_points_for": dl.delta(ob.get("expected_points_for"), oh.get("expected_points_for")) if ob and oh else 0.0,
        "playoff_probability": dl.delta(ob.get("playoff_probability"), oh.get("playoff_probability")) if ob and oh else 0.0,
        "bye_probability": dl.delta(ob.get("bye_probability"), oh.get("bye_probability")) if ob and oh else 0.0,
        "championship_probability": buyer_title_delta,
    }
    return {
        "actions": effective_actions,
        "trade_actions": actions,
        "automatic_roster_cut_actions": auto_cut_actions,
        "roster_resolution": roster_resolution,
        "roster_resolution_model_version": "FSFFL-Roster-Aware-Trade-Resolution-1.4",
        "teams_reoptimized": reoptimized,
        "simulator_runtime_metadata": {
            "reoptimized_lineup_nfl_team_metadata_expected_rows": metadata_expected,
            "reoptimized_lineup_nfl_team_metadata_missing_rows": len(metadata_missing),
            "reoptimized_lineup_nfl_team_metadata_complete": len(metadata_missing) == 0,
        },
        "league_reference": league_reference,
        "focus_before": b,
        "focus_after": h,
        "focus_delta": {
            "expected_wins": dl.delta(b.get("expected_wins"), h.get("expected_wins")),
            "expected_points_for": dl.delta(b.get("expected_points_for"), h.get("expected_points_for")),
            "playoff_probability": dl.delta(b.get("playoff_probability"), h.get("playoff_probability")),
            "bye_probability": dl.delta(b.get("bye_probability"), h.get("bye_probability")),
            "championship_probability": title_delta,
        },
        "buyer_before": ob,
        "buyer_after": oh,
        "buyer_delta": buyer_delta,
        "buyer_strategic": buyer_strategic,
        "buyer_championship_probability_delta": buyer_title_delta,
        "net_title_equity_swing_against_focus": round(
            max(0.0, float(buyer_title_delta or 0.0)) - float(title_delta or 0.0), 5
        ),
        "strategic": strategic,
        "roster_diagnosis": {
            "model_source": "GM3 optimized_team_strengths",
            "before": needs_before,
            "after": needs_after,
            "position_need_delta": {
                pos: round(float((needs_after.get("position_need") or {}).get(pos, 0.0)) - float((needs_before.get("position_need") or {}).get(pos, 0.0)), 3)
                for pos in ("QB", "RB", "WR", "TE")
            },
            "lower_need_score_is_better": True,
        },
    }


def _forced_focus_cut_plan(dl, pre_cut_rosters, focus_uid, plan):
    rosters = copy.deepcopy(pre_cut_rosters)
    by_uid, _ = dl.roster_maps(rosters)
    roster = by_uid.get(str(focus_uid))
    if not roster:
        raise RuntimeError(f"Missing focal roster {focus_uid} for cut-plan optimization")
    for pid in plan:
        dl.remove_player(roster, str(pid))
    action = {
        "type": "cut",
        "user_id": str(focus_uid),
        "players": [str(x) for x in plan],
        "automatic_roster_legalization": True,
        "baseline_aware_incremental_cut": True,
        "final_cut_plan_optimization": True,
    }
    return rosters, action


def _optimize_final_focus_cut_plan(engine, dl, model_inputs, baseline_lineups,
                                   focus_uid, buyer_uid, actions, pre_cut_rosters,
                                   default_rosters, roster_resolution, auto_cut_actions,
                                   sims, seed, outgoing, incoming):
    """Use downstream model utility, not retention-cost ordering, for tractable final focal cuts."""
    res = (roster_resolution or {}).get(str(focus_uid)) or {}
    required = int(res.get("required_cuts") or 0)
    pool = list(res.get("cut_candidate_pool") or [])
    if sims < 50000 or required <= 0 or len(pool) <= required:
        return default_rosters, roster_resolution, auto_cut_actions

    ids = [str(x.get("player_id")) for x in pool if x.get("player_id")]
    plans = list(itertools.combinations(ids, required))
    meta = {
        "eligible_plan_count": len(plans),
        "max_exact_plan_count": FINAL_CUT_PLAN_MAX_COMBINATIONS,
        "screen_simulations": FINAL_CUT_PLAN_SCREEN_SIMS,
        "selection_objective": "canonical_shared_decision_utility",
        "retention_cost_is_final_authority": False,
    }
    if not plans or len(plans) > FINAL_CUT_PLAN_MAX_COMBINATIONS:
        res["final_cut_plan_optimization"] = {
            **meta,
            "status": "FALLBACK_TO_RETENTION_PRESCREEN_PLAN_SPACE_TOO_LARGE",
        }
        return default_rosters, roster_resolution, auto_cut_actions

    simmod, league, canonical_rosters, users, players, season, projections, raw_schedule = model_inputs
    screen_seed = seed
    screen_baseline = dl.simulate_from_lineups(
        simmod, league, canonical_rosters, users, raw_schedule,
        baseline_lineups, FINAL_CUT_PLAN_SCREEN_SIMS, screen_seed
    )
    profile_by_id = {str(x.get("player_id")): x for x in pool}
    scored = []
    for plan in plans:
        plan_rosters, cut_action = _forced_focus_cut_plan(dl, pre_cut_rosters, focus_uid, plan)
        effective_actions = list(actions) + [cut_action]
        plan_resolution = copy.deepcopy(roster_resolution)
        pres = plan_resolution.get(str(focus_uid)) or {}
        selected = [profile_by_id[x] for x in plan if x in profile_by_id]
        pres["selected_cuts"] = selected
        pres["cut_selection_method"] = "shared_decision_utility_exact_plan_search"
        pres["cut_base_franchise_value"] = round(sum(float(x.get("base_franchise_value") or 0.0) for x in selected), 2)
        pres["cut_market_dynasty_value"] = round(sum(float(x.get("market_dynasty") or 0.0) for x in selected), 2)
        plan_resolution[str(focus_uid)] = pres

        sim = _simulate_resolved_candidate(
            engine, dl, model_inputs, baseline_lineups, screen_baseline,
            focus_uid, buyer_uid, actions, plan_rosters, effective_actions,
            plan_resolution, [cut_action], FINAL_CUT_PLAN_SCREEN_SIMS, screen_seed
        )
        temp = {
            "buyer_user_id": str(buyer_uid),
            "outgoing_assets": [str(x.get("asset_id")) for x in outgoing],
            "return_assets": [str(x.get("asset_id")) for x in incoming],
            "simulation": sim,
        }
        score = float(engine.post_sim_score(temp, engine.team_state(focus_uid)))
        scored.append((score, tuple(plan), plan_rosters, plan_resolution, [cut_action]))

    scored.sort(key=lambda x: (x[0], tuple(x[1])), reverse=True)
    best_score, best_plan, best_rosters, best_resolution, best_actions = scored[0]
    default_plan = tuple(str(x.get("player_id")) for x in (res.get("selected_cuts") or []))
    best_res = best_resolution.get(str(focus_uid)) or {}
    best_res["final_cut_plan_optimization"] = {
        **meta,
        "status": "EXACT_TRACTABLE_PLAN_SEARCH",
        "default_retention_plan": list(default_plan),
        "selected_plan": list(best_plan),
        "selected_plan_differs_from_retention_prescreen": tuple(best_plan) != default_plan,
        "screen_shared_decision_utility_score": round(best_score, 2),
        "screen_post_sim_score_compatibility_alias": round(best_score, 2),
        "all_plan_scores": [
            {"cut_player_ids": list(plan), "shared_decision_utility_score": round(score, 2), "post_sim_score_compatibility_alias": round(score, 2)}
            for score, plan, *_ in scored
        ],
    }
    best_resolution[str(focus_uid)] = best_res
    return best_rosters, best_resolution, best_actions


def fast_simulate_candidate(engine, dl, model_inputs, baseline_lineups, baseline,
                            focus_uid, buyer_uid, outgoing, incoming, sims, seed):
    simmod, league, canonical_rosters, users, players, season, projections, raw_schedule = model_inputs
    actions = engine.scenario_actions(focus_uid, buyer_uid, outgoing, incoming)
    pre_cut_rosters, _ = dl.apply_actions(canonical_rosters, actions)
    touched = dl.touched_users(focus_uid, actions)

    roster_aware = load_module(ROSTER_AWARE, "roster_aware_trade_for_v13")
    hypothetical_rosters, roster_resolution, auto_cut_actions = roster_aware.legalize_trade_rosters(
        dl, canonical_rosters, pre_cut_rosters, touched, league, players
    )
    hypothetical_rosters, roster_resolution, auto_cut_actions = _optimize_final_focus_cut_plan(
        engine, dl, model_inputs, baseline_lineups,
        focus_uid, buyer_uid, actions, pre_cut_rosters,
        hypothetical_rosters, roster_resolution, auto_cut_actions,
        sims, seed, outgoing, incoming
    )
    effective_actions = list(actions) + list(auto_cut_actions)
    return _simulate_resolved_candidate(
        engine, dl, model_inputs, baseline_lineups, baseline,
        focus_uid, buyer_uid, actions, hypothetical_rosters,
        effective_actions, roster_resolution, auto_cut_actions, sims, seed
    )


def metric(sim: Dict[str, Any], path: str) -> float:
    cur: Any = sim
    for key in path.split("."):
        cur = (cur or {}).get(key)
    return float(cur or 0.0)


def compare_candidate(candidate: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
    csim = candidate.get("simulation") or {}
    osim = current.get("simulation") or {}
    fields = {
        "expected_wins": "focus_after.expected_wins",
        "expected_points_for": "focus_after.expected_points_for",
        "playoff_probability": "focus_after.playoff_probability",
        "bye_probability": "focus_after.bye_probability",
        "championship_probability": "focus_after.championship_probability",
        "market_dynasty_delta": "strategic.market_dynasty_delta",
        "break_glass_delta": "strategic.break_glass_delta",
        "net_title_equity_swing_against_focus": "net_title_equity_swing_against_focus",
    }
    deltas = {k: round(metric(csim, p) - metric(osim, p), 5) for k, p in fields.items()}
    score_delta = round(float(candidate.get("post_sim_score") or 0.0) - float(current.get("post_sim_score") or 0.0), 2)
    cand_constraint = candidate.get("championship_equity_constraint")
    offer_constraint = current.get("championship_equity_constraint")
    title_adv = deltas["championship_probability"]
    dynasty_adv = deltas["market_dynasty_delta"]

    if cand_constraint == "PASS" and offer_constraint == "FAIL":
        verdict, reason = "BETTER", "preserves contender championship-equity guardrail while current offer fails it"
    elif score_delta >= 750 and title_adv >= -0.01:
        verdict, reason = "BETTER", "higher strategic utility without a material title-equity sacrifice versus current offer"
    elif score_delta <= -750 and title_adv <= 0.01:
        verdict, reason = "WORSE", "lower strategic utility with no compensating championship-equity advantage"
    else:
        verdict = "MIXED"
        if title_adv > 0.02 and dynasty_adv < 0:
            reason = "better for 2026 contention but gives back some future-value advantage"
        elif title_adv < -0.02 and dynasty_adv > 0:
            reason = "better future-value profile but worse for 2026 contention"
        else:
            reason = "tradeoffs are close enough that neither package clearly dominates"
    return {
        "verdict_vs_current_offer": verdict,
        "reason": reason,
        "post_sim_score_delta_vs_current_offer": score_delta,
        "metric_deltas_vs_current_offer": deltas,
    }


def run_base_engine_in_process(engine, argv: List[str]):
    old_argv = sys.argv
    try:
        sys.argv = [str(BASE_ENGINE)] + argv
        with contextlib.redirect_stdout(io.StringIO()):
            engine.main()
    finally:
        sys.argv = old_argv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--quick-sims", type=int, default=100)
    ap.add_argument("--confirm-sims", type=int, default=0)
    ap.add_argument("--shortlist", type=int, default=5)
    ap.add_argument("--finalists", type=int, default=5)
    ap.add_argument("--output", required=True)
    ap.add_argument("--seed", type=int, default=20260821)
    args = ap.parse_args()
    shortlist = max(5, args.shortlist)
    finalists = max(5, args.finalists)

    engine = load_module(BASE_ENGINE, "market_sweep_13_base")
    dl = engine.import_decision_lab()

    def patched_simulate_candidate(dl_mod, model_inputs, baseline_lineups, baseline,
                                   focus_uid, buyer_uid, outgoing, incoming, sims, seed):
        return fast_simulate_candidate(
            engine, dl_mod, model_inputs, baseline_lineups, baseline,
            focus_uid, buyer_uid, outgoing, incoming, sims, seed
        )
    engine.simulate_candidate = patched_simulate_candidate

    scenario = engine.load_json(Path(args.scenario), {}) or {}
    focus_uid = str(scenario.get("focus_user_id") or "")
    sent_ids, received_ids, current_partner = engine.incoming_trade_parts(scenario, focus_uid)

    with tempfile.TemporaryDirectory() as td:
        base_out = Path(td) / "base_market_sweep.json"
        run_base_engine_in_process(engine, [
            "--scenario", args.scenario,
            "--quick-sims", str(args.quick_sims),
            "--confirm-sims", str(args.confirm_sims),
            "--shortlist", str(shortlist),
            "--finalists", str(finalists),
            "--seed", str(args.seed),
            "--output", str(base_out),
        ])
        report = json.loads(base_out.read_text(encoding="utf-8"))

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
    if len(outgoing) != len(sent_ids) or len(incoming) != len(received_ids):
        missing = [x for x in sent_ids + received_ids if x not in catalog]
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

    ranked = list(report.get("ranked_finalists") or [])[:5]

    final_sim_count = args.quick_sims
    confirm_seed = args.seed
    if args.confirm_sims and args.confirm_sims > args.quick_sims:
        confirm_seed = simmod.deterministic_seed(league, season)
        confirm_baseline = dl.simulate_from_lineups(
            simmod, league, rosters, users, raw_schedule, baseline_lineups,
            args.confirm_sims, confirm_seed
        )
        current["simulation"] = patched_simulate_candidate(
            dl, model_inputs, baseline_lineups, confirm_baseline, focus_uid,
            current_partner, outgoing, incoming, args.confirm_sims, confirm_seed
        )
        current["post_sim_score"] = engine.post_sim_score(
            current, engine.team_state(focus_uid)
        )
        for row in ranked:
            buyer_uid = str(row.get("buyer_user_id") or "")
            out_ids = list(row.get("outgoing_assets") or [])
            in_ids = list(row.get("return_assets") or row.get("incoming_assets") or [])
            out_assets = [catalog[x] for x in out_ids if x in catalog]
            in_assets = [catalog[x] for x in in_ids if x in catalog]
            if not buyer_uid or len(out_assets) != len(out_ids) or len(in_assets) != len(in_ids):
                continue
            row["simulation"] = patched_simulate_candidate(
                dl, model_inputs, baseline_lineups, confirm_baseline, focus_uid,
                buyer_uid, out_assets, in_assets, args.confirm_sims, confirm_seed
            )
            row["post_sim_score"] = engine.post_sim_score(
                row, engine.team_state(focus_uid)
            )
        final_sim_count = args.confirm_sims

    for rank, row in enumerate(ranked, 1):
        row["rank"] = rank
        row["comparison_to_current_offer"] = compare_candidate(row, current)

    report["model_version"] = MODEL_VERSION
    report["current_offer_evaluation"] = current
    report["top_5_alternatives"] = ranked
    report["ranked_finalists"] = ranked
    report["candidate_counts"]["reported_top_alternatives"] = len(ranked)
    report["policy"]["top_five_report_required"] = True
    report["policy"]["alternatives_compared_to_current_offer"] = True
    report["policy"]["fast_exact_lineup_dp"] = True
    report["policy"]["roster_aware_trade_resolution"] = True
    report["policy"]["forced_cuts_included_in_simulation_and_strategic_value"] = True
    report["policy"]["roster_limit_source"] = "league.roster_positions"
    report["simulation"]["lineup_reoptimization"] = "exact_slot_mask_dynamic_programming"
    report["simulation"]["final_trade_impact_simulations"] = final_sim_count
    report["simulation"]["final_trade_impact_engine"] = "current_vectorized_simulator"
    report["simulation"]["final_trade_impact_seed"] = confirm_seed
    report["simulation"]["finalists_confirmed_at_high_precision"] = final_sim_count > args.quick_sims

    better = [r for r in ranked if r["comparison_to_current_offer"]["verdict_vs_current_offer"] == "BETTER"]
    mixed = [r for r in ranked if r["comparison_to_current_offer"]["verdict_vs_current_offer"] == "MIXED"]
    worse = [r for r in ranked if r["comparison_to_current_offer"]["verdict_vs_current_offer"] == "WORSE"]
    report["market_comparison_summary"] = {
        "better_than_current_offer": len(better),
        "mixed_vs_current_offer": len(mixed),
        "worse_than_current_offer": len(worse),
        "best_alternative_rank": 1 if ranked else None,
        "best_alternative_verdict": ranked[0]["comparison_to_current_offer"]["verdict_vs_current_offer"] if ranked else None,
    }

    Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
