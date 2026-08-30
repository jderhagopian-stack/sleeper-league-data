#!/usr/bin/env python3
"""Ablate overlapping final-score channels without changing production scoring.

Consumes an already-generated state-aware market-sweep report. The audit asks
whether removing selected correlated channels changes finalist ordering. It is
sensitivity evidence, not historical validation and not coefficient tuning.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

MODEL_VERSION = "FSFFL-Final-Score-Ablation-1.1"


def f(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def key(row):
    # Reduced audit rows already carry the stable identity derived from the
    # source candidate. Never try to reconstruct it from fields intentionally
    # omitted by the reduction step.
    if row.get("candidate_key"):
        return str(row["candidate_key"])
    if row.get("candidate_id"):
        return str(row["candidate_id"])
    buyer = str(row.get("buyer_user_id") or "")
    outs = ",".join(sorted(str(x) for x in row.get("outgoing_assets") or []))
    # Market-sweep candidates call the counterparty return side return_assets;
    # some adjacent modules use incoming_assets. Prefer the populated field.
    returns = row.get("return_assets") or row.get("incoming_assets") or []
    ins = ",".join(sorted(str(x) for x in returns))
    return f"{buyer}|OUT:{outs}|IN:{ins}"


def rank(rows, score_key):
    return [key(x) for x in sorted(rows, key=lambda r: (f(r[score_key]), key(r)), reverse=True)]


def discordance(a, b):
    if len(set(a)) != len(a) or len(set(b)) != len(b):
        raise ValueError("Ablation ranking contains duplicate candidate identities")
    common = [x for x in a if x in set(b)]
    pos = {x: i for i, x in enumerate(b)}
    pairs = 0
    flips = 0
    for i in range(len(common)):
        for j in range(i + 1, len(common)):
            pairs += 1
            if pos[common[i]] > pos[common[j]]:
                flips += 1
    return {"pair_count": pairs, "discordant_pairs": flips, "discordance_rate": round(flips / pairs, 4) if pairs else 0.0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    rows = list(report.get("ranked_finalists") or report.get("top_5_alternatives") or [])
    fixture_fallback_used = False
    if not rows:
        fixture_fallback_used = True
        rows = [
            {
                "candidate_key": "fixture:A",
                "post_sim_score": 1800.0,
                "simulation": {"strategic": {
                    "objective_weights": {"resilience": .15, "liquidity": .10},
                    "strategic_value_delta": 600.0,
                    "break_glass_delta": 350.0,
                    "liquidity_value_delta": 250.0,
                }},
            },
            {
                "candidate_key": "fixture:B",
                "post_sim_score": 1200.0,
                "simulation": {"strategic": {
                    "objective_weights": {"resilience": .15, "liquidity": .10},
                    "strategic_value_delta": -200.0,
                    "break_glass_delta": 800.0,
                    "liquidity_value_delta": 500.0,
                }},
            },
            {
                "candidate_key": "fixture:C",
                "post_sim_score": 900.0,
                "simulation": {"strategic": {
                    "objective_weights": {"resilience": .15, "liquidity": .10},
                    "strategic_value_delta": 300.0,
                    "break_glass_delta": -150.0,
                    "liquidity_value_delta": -200.0,
                }},
            },
        ]

    source_keys = [key(x) for x in rows]
    if len(set(source_keys)) != len(source_keys):
        raise SystemExit(f"Finalist candidate identities are not unique: {source_keys}")

    audited = []
    for row in rows:
        sim = row.get("simulation") or {}
        s = sim.get("strategic") or {}
        weights = s.get("objective_weights") or row.get("state_aware_objective_weights") or {}
        wr = f(weights.get("resilience"), .15)
        wl = f(weights.get("liquidity"), .10)
        strategic = f(s.get("strategic_value_delta"))
        break_glass = f(s.get("break_glass_delta"))
        liquidity = f(s.get("liquidity_value_delta"))
        full = f(row.get("post_sim_score"))

        # Algebra from state_aware_post_sim_score:
        # resilience_mult*(.15*strategic) == weight_resilience*strategic
        strategic_composite_channel = wr * strategic
        # resilience_mult*(.08*break_glass)
        repeated_break_glass_channel = (wr / .15) * .08 * break_glass if .15 else 0.0
        # liquidity_mult*(.25*liquidity)
        direct_liquidity_channel = (wl / .10) * .25 * liquidity if .10 else 0.0

        item = {
            "candidate_key": key(row),
            "full_score": round(full, 4),
            "strategic_composite_channel": round(strategic_composite_channel, 4),
            "repeated_break_glass_channel": round(repeated_break_glass_channel, 4),
            "direct_liquidity_channel": round(direct_liquidity_channel, 4),
            "score_without_strategic_composite": round(full - strategic_composite_channel, 4),
            "score_without_repeated_break_glass": round(full - repeated_break_glass_channel, 4),
            "score_without_direct_liquidity": round(full - direct_liquidity_channel, 4),
            "score_without_all_three_overlap_channels": round(full - strategic_composite_channel - repeated_break_glass_channel - direct_liquidity_channel, 4),
            "absolute_strategic_composite_share_of_score": round(abs(strategic_composite_channel) / max(1.0, abs(full)), 4),
        }
        audited.append(item)

    scenarios = {
        "full_score": "full_score",
        "without_strategic_composite": "score_without_strategic_composite",
        "without_repeated_break_glass": "score_without_repeated_break_glass",
        "without_direct_liquidity": "score_without_direct_liquidity",
        "without_all_three_overlap_channels": "score_without_all_three_overlap_channels",
    }
    rankings = {name: rank(audited, field) for name, field in scenarios.items()}
    base = rankings["full_score"]
    comparisons = {}
    for name, ordering in rankings.items():
        if name == "full_score":
            continue
        comparisons[name] = {
            "top_candidate_changed": bool(ordering and base and ordering[0] != base[0]),
            "exact_order_changed": ordering != base,
            **discordance(base, ordering),
        }

    max_share = max(x["absolute_strategic_composite_share_of_score"] for x in audited)
    any_order_change = any(x["exact_order_changed"] for x in comparisons.values())
    any_top_change = any(x["top_candidate_changed"] for x in comparisons.values())
    payload = {
        "model_version": MODEL_VERSION,
        "source_report_model_version": report.get("model_version"),
        "purpose": "Measure decision leverage of correlated final-score channels; no production score is changed.",
        "interpretation": {
            "historical_validation": False,
            "coefficient_tuning": False,
            "rank_stability_test": True,
            "a_rank_flip_does_not_prove_a_channel_is_wrong": True,
            "no_rank_flip_does_not_prove_incremental_validity": True,
        },
        "summary": {
            "candidate_count": len(audited),
            "source_report_finalist_count": len(report.get("ranked_finalists") or report.get("top_5_alternatives") or []),
            "fixture_fallback_used": fixture_fallback_used,
            "unique_candidate_count": len(set(source_keys)),
            "any_ablation_changes_order": any_order_change,
            "any_ablation_changes_top_candidate": any_top_change,
            "max_absolute_strategic_composite_share_of_full_score": round(max_share, 4),
        },
        "rankings": rankings,
        "comparisons": comparisons,
        "candidates": audited,
    }
    Path(args.output).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload["summary"], indent=2))
    print(json.dumps(comparisons, indent=2))


if __name__ == "__main__":
    main()
