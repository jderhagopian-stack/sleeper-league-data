#!/usr/bin/env python3
"""Focused real-case sanity audit for FSFFL Shared Decision Utility.

This is diagnostic only. It does not fit coefficients or alter decision authority.
It re-evaluates a small set of real production Opportunity Engine packages across
multiple Simulator seeds and governed strategic postures, then reports:
- sign stability across seeds,
- current/future primitive decomposition,
- key Simulator outcome deltas,
- posture sensitivity,
- scale diagnostics.

No intuitive trade label is used as a supervised target.
"""
from __future__ import annotations

import argparse
import copy
import json
import statistics
from pathlib import Path

from gm3 import team_improvement as gm3

CASE_SPECS = [
    {
        "id": "london_2029_early_1st",
        "label": "Drake London -> 2029 Early 1st",
        "match_all": ["Trade Drake London for 2029 Early 1st"],
    },
    {
        "id": "gibbs_ceedeepicks",
        "label": "CeeDee + 2027 Early 2nd + 2028 Mid 1st -> Gibbs",
        "match_all": ["CeeDee Lamb", "2027 Early 2nd", "2028 Mid 1st", "Jahmyr Gibbs"],
    },
    {
        "id": "gibbs_lamarpicks",
        "label": "Lamar + 2028 Mid 1st + 2029 Mid 1st -> Gibbs",
        "match_all": ["Lamar Jackson", "2028 Mid 1st", "2029 Mid 1st", "Jahmyr Gibbs"],
    },
    {
        "id": "mcbride_picks",
        "label": "2028 Mid 1st + 2nd + 3rd -> Trey McBride",
        "match_all": ["2028 Mid 1st", "2028 Mid 2nd", "2028 Mid 3rd", "Trey McBride"],
    },
    {
        "id": "etienne_picks",
        "label": "2027 Mid 2nd + Mid 3rd + 2028 Mid 3rd -> Travis Etienne",
        "match_all": ["2027 Mid 2nd", "2027 Mid 3rd", "2028 Mid 3rd", "Travis Etienne"],
    },
    {
        "id": "jacobs_judkins",
        "label": "Quinshon Judkins -> Josh Jacobs",
        "exact": "Trade Quinshon Judkins for Josh Jacobs",
    },
    {
        "id": "saquon_judkins",
        "label": "Quinshon Judkins + 2028 Late 3rd -> Saquon Barkley",
        "match_all": ["Quinshon Judkins", "2028 Late 3rd", "Saquon Barkley"],
    },
]

DEFAULT_POSTURES = [
    "AUTO",
    "BALANCED_CONTENDER",
    "PUSH_CHIPS_IN",
    "PRESERVE_FUTURE_VALUE",
]
DEFAULT_SEEDS = [20260821, 20260822, 20260823, 20260824]


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def select_case(rows, spec):
    matches = []
    for row in rows:
        desc = str(row.get("description") or "")
        if spec.get("exact") and desc == spec["exact"]:
            matches.append(row)
        elif spec.get("match_all") and all(token in desc for token in spec["match_all"]):
            matches.append(row)
    if not matches:
        raise RuntimeError(f"No production row found for {spec['id']}")
    # Prefer the exact mutually viable structure when duplicates exist; otherwise
    # use the highest focal utility among matching production rows.
    matches.sort(
        key=lambda r: (
            float(r.get("counterparty_shared_decision_utility_score") or -1e18) >= 0,
            float(r.get("team_improvement_score") or -1e18),
        ),
        reverse=True,
    )
    return copy.deepcopy(matches[0])


def extract(result):
    sim = result.get("simulation") or {}
    attr = result.get("decision_attribution") or {}
    strategic = sim.get("strategic") or {}
    blocks = {}
    contributions = {}
    for channel in attr.get("channels") or []:
        name = channel.get("channel")
        blocks[name] = channel.get("primitive_value")
        contributions[name] = channel.get("numeric_contribution")
    d = sim.get("focus_delta") or {}
    diagnostics = attr.get("diagnostics") or {}
    current = float(blocks.get("current") or 0.0)
    wins = float(d.get("expected_wins") or 0.0)
    market_redraft_delta = float(strategic.get("market_redraft_delta") or 0.0)
    return {
        "score": float(result.get("team_improvement_score") or 0.0),
        "current_primitive": current,
        "future_primitive": float(blocks.get("future") or 0.0),
        "current_contribution": float(contributions.get("current") or 0.0),
        "future_contribution": float(contributions.get("future") or 0.0),
        "expected_points_delta": float(d.get("expected_points_for") or 0.0),
        "expected_wins_delta": wins,
        "playoff_probability_delta": float(d.get("playoff_probability") or 0.0),
        "championship_probability_delta": float(d.get("championship_probability") or 0.0),
        "market_dynasty_delta": float(strategic.get("market_dynasty_delta") or 0.0),
        "market_redraft_delta": market_redraft_delta,
        "current_relative_signal": float(diagnostics.get("current_relative_signal") or 0.0),
        "current_value_scale": float(diagnostics.get("current_value_scale") or 0.0),
        "current_value_per_expected_win": (current / wins) if abs(wins) > 1e-12 else None,
        "current_primitive_to_abs_redraft_delta": (
            current / abs(market_redraft_delta)
            if abs(market_redraft_delta) > 1e-12 else None
        ),
        "objective_weights": copy.deepcopy(
            (strategic.get("strategic_posture_resolution") or {}).get("active_weights")
            or strategic.get("objective_weights")
            or {}
        ),
    }


def summarize_seed_runs(runs):
    scores = [x["score"] for x in runs]
    signs = {1 if x > 0 else -1 if x < 0 else 0 for x in scores}
    if signs == {1}:
        stability = "STABLE_POSITIVE"
    elif signs == {-1}:
        stability = "STABLE_NEGATIVE"
    elif signs == {0}:
        stability = "ZERO_ACROSS_SEEDS"
    else:
        stability = "SIGN_UNSTABLE"
    def med(key):
        vals = [x[key] for x in runs if x.get(key) is not None]
        return statistics.median(vals) if vals else None
    return {
        "sign_stability": stability,
        "score_min": min(scores),
        "score_median": statistics.median(scores),
        "score_max": max(scores),
        "score_mean": statistics.mean(scores),
        "score_stdev_across_seeds": statistics.stdev(scores) if len(scores) > 1 else 0.0,
        "median_current_primitive": med("current_primitive"),
        "median_future_primitive": med("future_primitive"),
        "median_expected_wins_delta": med("expected_wins_delta"),
        "median_championship_probability_delta": med("championship_probability_delta"),
        "median_market_dynasty_delta": med("market_dynasty_delta"),
        "median_market_redraft_delta": med("market_redraft_delta"),
        "median_current_value_per_expected_win": med("current_value_per_expected_win"),
        "median_current_primitive_to_abs_redraft_delta": med("current_primitive_to_abs_redraft_delta"),
    }


def render_markdown(audit):
    lines = [
        "# FSFFL Opportunity Utility Sanity Audit",
        "",
        f"Simulations per seed: **{audit['simulations_per_seed']:,}**",
        f"Seeds: **{len(audit['seeds'])}**",
        "",
        "This is a diagnostic real-case sensitivity audit. It does not use owner intuition as a fitted target and does not authorize coefficient changes by itself.",
        "",
        "| Case | Posture | Seed sign stability | Median utility | Current primitive | Future primitive | Expected wins Δ | Dynasty Δ |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for case in audit["cases"]:
        for posture, row in case["postures"].items():
            lines.append(
                f"| {case['label']} | {posture} | {row['summary']['sign_stability']} | "
                f"{row['summary']['score_median']:+,.1f} | "
                f"{row['summary']['median_current_primitive']:+,.1f} | "
                f"{row['summary']['median_future_primitive']:+,.1f} | "
                f"{row['summary']['median_expected_wins_delta']:+.3f} | "
                f"{row['summary']['median_market_dynasty_delta']:+,.1f} |"
            )
    lines += [
        "",
        "## Governance interpretation",
        "",
        f"- Coefficient change supported by this audit alone: **{str(audit['coefficient_change_supported_by_this_audit']).lower()}**.",
        "- A sign-unstable case is treated as simulation-sensitive evidence, not a robust positive or negative preference.",
        "- Stable positive utility is evidence that the current model genuinely prefers the package under that posture; it is not proof that the preference is optimally calibrated.",
        "- The audit reports current-value scale diagnostics so unusually large current primitives can be investigated without silently retuning them.",
        "",
    ]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="Team Improvement production JSON")
    ap.add_argument("--focus-user-id", required=True)
    ap.add_argument("--simulations", type=int, default=1000)
    ap.add_argument("--seeds", default=",".join(str(x) for x in DEFAULT_SEEDS))
    ap.add_argument("--postures", default=",".join(DEFAULT_POSTURES))
    ap.add_argument("--output", required=True)
    ap.add_argument("--markdown")
    args = ap.parse_args()

    doc = load_json(args.input)
    rows = doc.get("trade_price_frontier_candidates") or []
    seeds = [int(x.strip()) for x in args.seeds.split(",") if x.strip()]
    postures = [x.strip() for x in args.postures.split(",") if x.strip()]
    cases = []

    for spec in CASE_SPECS:
        row = select_case(rows, spec)
        case = {
            "id": spec["id"],
            "label": spec["label"],
            "production_description": row.get("description"),
            "production_focal_utility": row.get("team_improvement_score"),
            "production_counterparty_utility": row.get("counterparty_shared_decision_utility_score"),
            "postures": {},
        }
        for posture in postures:
            seed_rows = []
            for seed in seeds:
                evaluator = gm3.portfolio_evaluator(
                    str(args.focus_user_id),
                    simulations=int(args.simulations),
                    seed=int(seed),
                    strategic_posture=posture,
                )
                result = evaluator.evaluate([row])
                extracted = extract(result)
                extracted["seed"] = seed
                seed_rows.append(extracted)
            case["postures"][posture] = {
                "runs": seed_rows,
                "summary": summarize_seed_runs(seed_rows),
            }
        cases.append(case)

    audit = {
        "schema_version": "FSFFL-Opportunity-Utility-Sanity-Audit-1.0",
        "authority": "DIAGNOSTIC_ONLY",
        "focus_user_id": str(args.focus_user_id),
        "simulations_per_seed": int(args.simulations),
        "seeds": seeds,
        "postures": postures,
        "cases": cases,
        "coefficient_change_supported_by_this_audit": False,
        "coefficient_change_reason": (
            "Real-case sensitivity and sign stability are useful diagnostics but are not an "
            "independent empirical optimization target."
        ),
        "owner_intuition_used_as_fitted_target": False,
        "new_utility_created": False,
    }
    Path(args.output).write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    if args.markdown:
        Path(args.markdown).write_text(render_markdown(audit) + "\n", encoding="utf-8")
    print(json.dumps({
        "cases": len(cases),
        "postures": postures,
        "simulations_per_seed": args.simulations,
        "output": args.output,
    }, indent=2))


if __name__ == "__main__":
    main()
