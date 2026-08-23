#!/usr/bin/env python3
"""FSFFL Counter & Market Sweep 1.2 reporting wrapper.

Runs the diversified Counter & Market Sweep engine, guarantees a top-five
finalist report when five candidates are available, evaluates the currently
held offer on the exact same quick-simulation baseline, and compares every
alternative directly against that current offer.

Canonical Sleeper / GM / Simulator state remains read-only.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

BASE_ENGINE = Path("script/run_trade_market_sweep.py")
MODEL_VERSION = "FSFFL-Counter-Market-Sweep-1.2"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to import {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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
        verdict = "BETTER"
        reason = "preserves contender championship-equity guardrail while current offer fails it"
    elif score_delta >= 750 and title_adv >= -0.01:
        verdict = "BETTER"
        reason = "higher strategic utility without a material title-equity sacrifice versus current offer"
    elif score_delta <= -750 and title_adv <= 0.01:
        verdict = "WORSE"
        reason = "lower strategic utility with no compensating championship-equity advantage"
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

    engine = load_module(BASE_ENGINE, "market_sweep_11")
    dl = engine.import_decision_lab()
    scenario = engine.load_json(Path(args.scenario), {}) or {}
    focus_uid = str(scenario.get("focus_user_id") or "")
    sent_ids, received_ids, current_partner = engine.incoming_trade_parts(scenario, focus_uid)

    with tempfile.TemporaryDirectory() as td:
        base_out = Path(td) / "base_market_sweep.json"
        cmd = [
            sys.executable, str(BASE_ENGINE),
            "--scenario", args.scenario,
            "--quick-sims", str(args.quick_sims),
            "--confirm-sims", str(args.confirm_sims),
            "--shortlist", str(shortlist),
            "--finalists", str(finalists),
            "--seed", str(args.seed),
            "--output", str(base_out),
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
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
    current["simulation"] = engine.simulate_candidate(
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
