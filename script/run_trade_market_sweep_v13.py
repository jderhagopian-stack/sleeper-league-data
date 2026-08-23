#!/usr/bin/env python3
"""FSFFL Counter & Market Sweep 1.3.

Performance-focused wrapper around the 1.1 candidate generator. It preserves
1.2 Top-5/current-offer comparison behavior, but replaces the exponential DFS
lineup re-optimization used by the Decision Lab with an exact dynamic-programming
assignment solver for hypothetical rosters.

The DP solves the same maximum projected-mean legal lineup assignment problem
in O(players * slots * 2^slots), which is tiny for the FSFFL nine-slot lineup.
Canonical Sleeper / GM / Simulator state remains read-only.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import importlib.util
import io
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

BASE_ENGINE = Path("script/run_trade_market_sweep.py")
MODEL_VERSION = "FSFFL-Counter-Market-Sweep-1.3"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to import {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fast_optimize_weekly_lineup(simmod, roster, week, league, players, projections):
    """Exact max-weight legal lineup assignment via slot-mask DP."""
    candidates = []
    taxi = set(roster.get("taxi") or [])
    for pid in roster.get("players") or []:
        if pid in taxi:
            continue
        meta = simmod.player_meta(players, projections, pid)
        pos = meta.get("position")
        pr = simmod.projection_for(projections, pid, week)
        if not pos or pr is None or pr["active_probability"] <= 0:
            continue
        candidates.append({**meta, **pr})

    slots = simmod.lineup_slots(league)
    # mask -> (projected value, assignment dict slot_index -> player row)
    states = {0: (0.0, {})}
    for c in candidates:
        weight = float(c["mean"]) * float(c["active_probability"])
        prior = list(states.items())
        for mask, (value, assign) in prior:
            for idx, slot in enumerate(slots):
                bit = 1 << idx
                if mask & bit or not simmod.eligible(c["position"], slot):
                    continue
                new_mask = mask | bit
                new_value = value + weight
                old = states.get(new_mask)
                if old is None or new_value > old[0]:
                    new_assign = dict(assign)
                    new_assign[idx] = c
                    states[new_mask] = (new_value, new_assign)

    # All projected values in the canonical feed are non-negative. Maximizing
    # total projected value therefore reproduces the DFS optimum while allowing
    # genuinely unfillable slots to remain empty.
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
    reg_weeks = simmod.regular_season_weeks(league)
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


def fast_simulate_candidate(engine, dl, model_inputs, baseline_lineups, baseline,
                            focus_uid, buyer_uid, outgoing, incoming, sims, seed):
    simmod, league, canonical_rosters, users, players, season, projections, raw_schedule = model_inputs
    actions = engine.scenario_actions(focus_uid, buyer_uid, outgoing, incoming)
    hypothetical_rosters, _ = dl.apply_actions(canonical_rosters, actions)
    touched = dl.touched_users(focus_uid, actions)
    hypothetical_lineups, reoptimized = fast_reoptimize_touched_lineups(
        dl, simmod, baseline_lineups, hypothetical_rosters, touched,
        league, users, players, projections
    )
    hyp = dl.simulate_from_lineups(
        simmod, league, hypothetical_rosters, users, raw_schedule,
        hypothetical_lineups, sims, seed
    )
    bidx, hidx = dl.team_index(baseline), dl.team_index(hyp)
    b, h = bidx[focus_uid], hidx[focus_uid]
    ob, oh = bidx.get(buyer_uid), hidx.get(buyer_uid)
    strategic = dl.strategic_summary(focus_uid, actions)
    title_delta = dl.delta(b.get("championship_probability"), h.get("championship_probability"))
    buyer_title_delta = dl.delta(ob.get("championship_probability"), oh.get("championship_probability")) if ob and oh else 0.0
    return {
        "actions": actions,
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
        "net_title_equity_swing_against_focus": round(
            max(0.0, float(buyer_title_delta or 0.0)) - float(title_delta or 0.0), 5
        ),
        "strategic": strategic,
    }


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

    # Monkeypatch only the Decision Lab candidate re-optimization path used by
    # this process. Core Simulator 1.0 remains untouched.
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
    report["simulation"]["lineup_reoptimization"] = "exact_slot_mask_dynamic_programming"

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
