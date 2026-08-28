#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "script" / "build_historical_gm3_bundle.py"
RUNNER = ROOT / "script" / "run_historical_trade_analysis.py"
DEFAULT_OUT = ROOT / "data" / "audit" / "historical_reconstruction_sensitivity.json"


def replace_required(text: str, old: str, new: str) -> str:
    if old not in text:
        raise AssertionError(f"Sensitivity replacement target missing: {old}")
    return text.replace(old, new)


def variants(original: str):
    scenarios = {"baseline": original}

    low = original
    low = replace_required(low, "baselines[pos] * 0.72", "baselines[pos] * 0.60")
    low = replace_required(low, "max(250.0, ppg * (350.0 if pos == \"QB\" else 430.0))", "max(0.0, ppg * (250.0 if pos == \"QB\" else 300.0))")
    low = replace_required(low, "600.0 + 5900.0 * (pct ** 1.35)", "0.0 + 4000.0 * (pct ** 1.70)")
    scenarios["projection_market_low"] = low

    high = original
    high = replace_required(high, "baselines[pos] * 0.72", "baselines[pos] * 0.90")
    high = replace_required(high, "max(250.0, ppg * (350.0 if pos == \"QB\" else 430.0))", "max(500.0, ppg * (450.0 if pos == \"QB\" else 550.0))")
    high = replace_required(high, "600.0 + 5900.0 * (pct ** 1.35)", "1200.0 + 8000.0 * (pct ** 1.00)")
    scenarios["projection_market_high"] = high

    low = original
    low = replace_required(low, 'fallback_cv = {"QB": 0.28, "RB": 0.52, "WR": 0.48, "TE": 0.50}', 'fallback_cv = {"QB": 0.20, "RB": 0.35, "WR": 0.35, "TE": 0.35}')
    low = replace_required(low, "active = 0.62", "active = 0.35")
    low = replace_required(low, "mean * 0.96", "mean * 0.90")
    low = replace_required(low, "0.67 * sd", "0.75 * sd")
    scenarios["uncertainty_injury_low"] = low

    high = original
    high = replace_required(high, 'fallback_cv = {"QB": 0.28, "RB": 0.52, "WR": 0.48, "TE": 0.50}', 'fallback_cv = {"QB": 0.45, "RB": 0.70, "WR": 0.65, "TE": 0.70}')
    high = replace_required(high, "active = 0.62", "active = 0.80")
    high = replace_required(high, "mean * 0.96", "mean * 1.00")
    high = replace_required(high, "0.67 * sd", "0.60 * sd")
    scenarios["uncertainty_injury_high"] = high

    low = original
    low = replace_required(low, "0.88 ** max(0, year - 2024)", "0.80 ** max(0, year - 2024)")
    low = replace_required(low, "if contender <= 0.33:", "if contender <= 0.20:")
    low = replace_required(low, "elif contender >= 0.67:", "elif contender >= 0.80:")
    low = replace_required(low, 'q, tier, ew, lw = 0.78, "early", 0.58, 0.14', 'q, tier, ew, lw = 0.65, "early", 0.45, 0.25')
    low = replace_required(low, 'q, tier, ew, lw = 0.28, "late", 0.14, 0.58', 'q, tier, ew, lw = 0.40, "late", 0.25, 0.45')
    low = replace_required(low, 'q, tier, ew, lw = 0.52, "mid", 0.30, 0.30', 'q, tier, ew, lw = 0.40, "mid", 0.20, 0.20')
    scenarios["future_pick_low"] = low

    high = original
    high = replace_required(high, "0.88 ** max(0, year - 2024)", "0.95 ** max(0, year - 2024)")
    high = replace_required(high, "if contender <= 0.33:", "if contender <= 0.45:")
    high = replace_required(high, "elif contender >= 0.67:", "elif contender >= 0.55:")
    high = replace_required(high, 'q, tier, ew, lw = 0.78, "early", 0.58, 0.14', 'q, tier, ew, lw = 0.90, "early", 0.70, 0.05')
    high = replace_required(high, 'q, tier, ew, lw = 0.28, "late", 0.14, 0.58', 'q, tier, ew, lw = 0.15, "late", 0.05, 0.70')
    high = replace_required(high, 'q, tier, ew, lw = 0.52, "mid", 0.30, 0.30', 'q, tier, ew, lw = 0.65, "mid", 0.40, 0.40')
    scenarios["future_pick_high"] = high
    return scenarios


def decision_signature(report):
    rows = ((report.get("gm3_evaluation") or {}).get("team_results") or {})
    return {
        str(uid): json.dumps((row or {}).get("decision"), sort_keys=True)
        for uid, row in rows.items()
    }


def run_case(season: str, transaction_id: str, sims: int, name: str):
    out = Path("/tmp") / f"fsffl-historical-sensitivity-{name}.json"
    cmd = [
        "python", str(RUNNER),
        "--season", str(season),
        "--transaction-id", str(transaction_id),
        "--sims", str(sims),
        "--output", str(out),
    ]
    subprocess.run(cmd, cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
    return json.loads(out.read_text(encoding="utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2023")
    ap.add_argument("--transaction-id", default="950954472107368448")
    ap.add_argument("--sims", type=int, default=250)
    ap.add_argument("--output", default=str(DEFAULT_OUT))
    args = ap.parse_args()

    original = BUILDER.read_text(encoding="utf-8")
    cases = variants(original)
    reports = {}
    try:
        for name, source in cases.items():
            BUILDER.write_text(source, encoding="utf-8")
            reports[name] = run_case(args.season, args.transaction_id, args.sims, name)
    finally:
        BUILDER.write_text(original, encoding="utf-8")

    baseline_sig = decision_signature(reports["baseline"])
    comparisons = {}
    any_flip = False
    for name, report in reports.items():
        sig = decision_signature(report)
        flips = {uid: sig.get(uid) != baseline_sig.get(uid) for uid in sorted(set(sig) | set(baseline_sig))}
        any_flip = any_flip or any(flips.values())
        comparisons[name] = {
            "decision_signature": sig,
            "decision_changed_from_baseline": flips,
            "gm3_status": (report.get("gm3_evaluation") or {}).get("status"),
            "authoritative_recommendation_allowed": (report.get("gm3_evaluation") or {}).get("authoritative_recommendation_allowed"),
        }

    result = {
        "model_version": "FSFFL-Historical-Reconstruction-Sensitivity-1.0",
        "season": int(args.season),
        "transaction_id": str(args.transaction_id),
        "n_sims_per_scenario": int(args.sims),
        "method": "Executable grouped low/high bound perturbation of reconstruction-only parameters; production source restored after each audit run; no coefficient is tuned to outcome.",
        "scenario_groups": [k for k in cases if k != "baseline"],
        "comparisons": comparisons,
        "summary": {
            "any_decision_flip": any_flip,
            "authoritative_regardless_of_flip": False,
            "strict_oos_backtest_observation": False,
            "interpretation": "Sensitivity evidence is diagnostic only. RECONSTRUCTED_AT_TIME remains non-authoritative whether or not this single case flips because one reconstructed case cannot validate coefficients.",
        },
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    for row in comparisons.values():
        assert row["authoritative_recommendation_allowed"] is False
    assert BUILDER.read_text(encoding="utf-8") == original
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
