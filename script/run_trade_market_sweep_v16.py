#!/usr/bin/env python3
"""FSFFL Counter & Market Sweep 1.6 — deeper bilateral search.

Builds on 1.5. Rather than simulating a small fixed shortlist and filtering it
once, 1.6 deliberately searches deeper into the market so buyer-irrational
packages can be replaced by mutually viable alternatives.

The search remains read-only and heuristic on human acceptance: it estimates
strategic fit, not a calibrated probability that another manager accepts.
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

V14_PATH = Path("script/run_trade_market_sweep_v14.py")
V13_PATH = Path("script/run_trade_market_sweep_v13.py")
MODEL_VERSION = "FSFFL-Counter-Market-Sweep-1.6"
DEFAULT_SEARCH_DEPTH = 40


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to import {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def buyer_rationality(row: Dict[str, Any], dl) -> Dict[str, Any]:
    sim = row.get("simulation") or {}
    buyer_uid = str(row.get("buyer_user_id") or "")
    state = str(row.get("buyer_state") or "unknown")
    actions = sim.get("actions") or []
    bs = dl.strategic_summary(buyer_uid, actions) if buyer_uid and actions else {}
    dynasty = float(bs.get("market_dynasty_delta") or 0.0)
    redraft = float(bs.get("market_redraft_delta") or 0.0)
    break_glass = float(bs.get("break_glass_delta") or 0.0)
    title = float(sim.get("buyer_championship_probability_delta") or 0.0)

    if state == "elite_contender":
        title_floor = -0.04
        viable = title >= title_floor or (title >= -0.07 and dynasty >= 1800 and redraft >= -1500)
    elif state == "contender":
        title_floor = -0.05
        viable = title >= title_floor or (title >= -0.08 and dynasty >= 1600 and redraft >= -1800)
    elif state == "retool":
        title_floor = -0.10
        viable = dynasty >= -500 and (title >= title_floor or dynasty >= 1200)
    elif state == "rebuild":
        title_floor = None
        viable = dynasty >= -300 or break_glass >= 0
    else:
        title_floor = -0.06
        viable = title >= title_floor and dynasty >= -1200

    pivot_viable = (dynasty >= 700 or break_glass >= 700) and redraft >= -9000
    if viable:
        label = "CURRENT_STATE_VIABLE"
        reason = "buyer-side GM utility is compatible with the owner's current competitive objective"
    elif pivot_viable:
        label = "STATE_CHANGE_DEPENDENT"
        reason = "buyer-side utility conflicts with the current objective but becomes rational under a retool/rebuild pivot"
    else:
        label = "BUYER_IRRATIONAL"
        reason = "buyer gives up too much current and/or long-term utility even after allowing for a strategic-state change"

    score = 0.50
    score += max(-0.30, min(0.30, title * 2.5))
    score += max(-0.18, min(0.18, dynasty / 9000.0))
    score += max(-0.12, min(0.12, break_glass / 12000.0))
    if state in {"elite_contender", "contender"} and title < -0.08:
        score -= 0.25
    score = round(max(0.0, min(1.0, score)), 4)
    band = "HIGH" if score >= 0.68 else "MEDIUM" if score >= 0.48 else "LOW" if score >= 0.28 else "VERY_LOW"

    return {
        "buyer_state": state,
        "current_state_gate": label,
        "current_state_viable": bool(viable),
        "state_change_viable": bool(pivot_viable),
        "heuristic_acceptance_fit_score": score,
        "heuristic_acceptance_fit": band,
        "reason": reason,
        "buyer_title_delta": round(title, 5),
        "buyer_market_dynasty_delta": round(dynasty, 2),
        "buyer_market_redraft_delta": round(redraft, 2),
        "buyer_break_glass_delta": round(break_glass, 2),
        "title_loss_floor_for_current_state": title_floor,
    }


def focal_viable(row: Dict[str, Any]) -> bool:
    return (
        row.get("championship_equity_constraint") == "PASS"
        and row.get("plausibility") in {"HIGH", "MEDIUM"}
    )


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
    with tempfile.TemporaryDirectory() as td:
        raw_out = Path(td) / "v14.json"
        subprocess.run([
            sys.executable, str(V14_PATH), "--scenario", args.scenario,
            "--quick-sims", str(args.quick_sims), "--confirm-sims", str(args.confirm_sims),
            "--shortlist", str(depth), "--finalists", str(depth), "--seed", str(args.seed),
            "--output", str(raw_out),
        ], check=True, stdout=subprocess.DEVNULL)
        report = json.loads(raw_out.read_text(encoding="utf-8"))

    v13 = load_module(V13_PATH, "market_sweep_v13_for_v16")
    engine = v13.load_module(v13.BASE_ENGINE, "market_sweep_base_for_v16")
    dl = engine.import_decision_lab()

    rows = list(report.get("ranked_finalists") or [])
    for row in rows:
        row["buyer_rationality"] = buyer_rationality(row, dl)

    mutually_viable = [
        r for r in rows
        if focal_viable(r) and r["buyer_rationality"]["current_state_viable"]
    ]
    mutually_viable.sort(
        key=lambda r: (
            float(r.get("post_sim_score") or 0.0),
            float(r["buyer_rationality"]["heuristic_acceptance_fit_score"]),
        ),
        reverse=True,
    )

    pivot = [
        r for r in rows
        if focal_viable(r)
        and not r["buyer_rationality"]["current_state_viable"]
        and r["buyer_rationality"]["state_change_viable"]
    ]
    pivot.sort(key=lambda r: float(r.get("post_sim_score") or 0.0), reverse=True)
    rejected = [r for r in rows if r["buyer_rationality"]["current_state_gate"] == "BUYER_IRRATIONAL"]

    top5 = mutually_viable[:5]
    for i, row in enumerate(top5, 1):
        row["actionable_rank"] = i

    current = report.get("current_offer_evaluation") or {}
    if current:
        current["buyer_rationality"] = buyer_rationality(current, dl)

    if not top5:
        action = "DECLINE"
    elif current and focal_viable(current) and current["buyer_rationality"]["current_state_viable"]:
        best = top5[0]
        action = "SHOP_BEFORE_ACCEPTING" if best.get("post_sim_score", 0) > current.get("post_sim_score", 0) + 750 else "ACCEPT_NOW"
    elif any(r.get("candidate_type") == "SAME_PARTNER_COUNTER" for r in top5):
        action = "COUNTER_CURRENT_OFFEROR"
    else:
        action = "SHOP_BEFORE_ACCEPTING"

    report["model_version"] = MODEL_VERSION
    report["ranked_finalists"] = top5
    report["top_5_alternatives"] = top5
    report["state_change_dependent_alternatives"] = pivot[:5]
    report["buyer_irrational_candidates_excluded"] = len(rejected)
    report["recommended_next_action"] = action
    report.setdefault("candidate_counts", {})["deep_search_simulated"] = len(rows)
    report["candidate_counts"]["buyer_current_state_viable"] = len(mutually_viable)
    report["candidate_counts"]["state_change_dependent"] = len(pivot)
    report["candidate_counts"]["buyer_irrational_excluded"] = len(rejected)
    report.setdefault("policy", {})["buyer_current_state_rationality_gate"] = True
    report["policy"]["state_change_dependent_candidates_separated"] = True
    report["policy"]["heuristic_acceptance_fit_not_probability"] = True
    report["policy"]["actionable_top_five_requires_bilateral_utility"] = True
    report["policy"]["deep_search_replaces_filtered_candidates"] = True
    report["policy"]["focal_and_counterparty_must_both_pass"] = True

    Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
