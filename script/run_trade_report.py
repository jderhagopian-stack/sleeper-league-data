#!/usr/bin/env python3
"""Run FSFFL trade analysis and always emit JSON + one-page PDF + short answer.

Manager-facing entry point for trade queries. Analysis uses the canonical
continuous state-aware, bilateral market-intelligence, league-realistic
multi-asset Counter & Market Sweep 1.16 path, then delegates presentation to
the standardized PDF renderer.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

MARKET_SWEEP = Path("script/run_trade_market_sweep_v22.py")
PDF_RENDERER = Path("script/render_trade_decision_report.py")
MODEL_VERSION = "FSFFL-Trade-Query-Pipeline-1.3"
EXPECTED_ANALYSIS_MODEL = "FSFFL-Counter-Market-Sweep-1.16"


def run(cmd):
    subprocess.run(cmd, check=True)


def summary(report):
    action = str(report.get("recommended_next_action") or "REVIEW")
    current = report.get("current_offer_evaluation") or {}
    sim = current.get("simulation") or {}
    delta = sim.get("focus_delta") or {}
    top = report.get("top_5_alternatives") or []
    label = {
        "ACCEPT_NOW": "ACCEPT",
        "COUNTER_CURRENT_OFFEROR": "COUNTER",
        "SHOP_BEFORE_ACCEPTING": "SHOP BEFORE ACCEPTING",
        "DECLINE": "DECLINE",
    }.get(action, action.replace("_", " "))
    short = (
        f"{label}. Current-offer impact: {float(delta.get('expected_wins') or 0):+.2f} expected wins, "
        f"{float(delta.get('championship_probability') or 0)*100:+.0f} pts championship probability."
    )
    if top:
        best = top[0]
        short += f" Best modeled negotiation path: {best.get('buyer_team')} - receive {', '.join(best.get('return_asset_names') or [])}."
    return short


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--basename", default="trade-decision-report")
    ap.add_argument("--quick-sims", type=int, default=100)
    ap.add_argument("--confirm-sims", type=int, default=0)
    ap.add_argument("--search-depth", type=int, default=60)
    ap.add_argument("--seed", type=int, default=20260821)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{args.basename}.json"
    pdf_path = out_dir / f"{args.basename}.pdf"
    summary_path = out_dir / f"{args.basename}-summary.json"

    run([
        sys.executable, str(MARKET_SWEEP),
        "--scenario", args.scenario,
        "--quick-sims", str(args.quick_sims),
        "--confirm-sims", str(args.confirm_sims),
        "--search-depth", str(args.search_depth),
        "--seed", str(args.seed),
        "--output", str(json_path),
    ])
    run([sys.executable, str(PDF_RENDERER), "--input", str(json_path), "--output", str(pdf_path)])

    report = json.loads(json_path.read_text(encoding="utf-8"))
    if report.get("model_version") != EXPECTED_ANALYSIS_MODEL:
        raise RuntimeError(
            f"Trade report pipeline expected {EXPECTED_ANALYSIS_MODEL}, got {report.get('model_version')}"
        )
    payload = {
        "pipeline_model_version": MODEL_VERSION,
        "analysis_model_version": report.get("model_version"),
        "recommended_next_action": report.get("recommended_next_action"),
        "short_answer": summary(report),
        "json_report": str(json_path),
        "pdf_report": str(pdf_path),
        "canonical_model_entry_point": str(MARKET_SWEEP),
        "delivery_policy": "Always return the short answer and attach/share the PDF report for a trade query.",
    }
    summary_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
