#!/usr/bin/env python3
"""Compare Opportunity Engine search/simulation configurations against a deep reference.

This is an empirical search-budget diagnostic. It does not tune valuation weights or
change any owning model. Candidate identity/rank stability, portfolio stability, score
error, and runtime are measured relative to a deliberately deeper same-state run.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def key(row):
    if not row:
        return "NONE"
    channel = str(row.get("channel") or "")
    target = str(((row.get("target") or {}).get("asset_id")) or "")
    outgoing = tuple(sorted(str(x.get("asset_id")) for x in (row.get("outgoing") or []) if x.get("asset_id")))
    return f"{channel}|{target}|{','.join(outgoing)}"


def rows(doc):
    return (
        (doc.get("source_team_improvement") or {}).get("top_cross_channel_options")
        or doc.get("ranked_single_step_opportunities")
        or doc.get("top_cross_channel_options")
        or []
    )


def ranked(doc, n=10):
    return [key(x) for x in rows(doc)[:n]]


def recommended(doc):
    return (
        doc.get("best_move_available")
        or doc.get("recommended_action")
        or (doc.get("source_team_improvement") or {}).get("recommended_action")
    )


def score_map(doc):
    return {key(x): float(x.get("team_improvement_score") or 0.0) for x in rows(doc)}


def rank_map(doc, n=20):
    return {candidate: i + 1 for i, candidate in enumerate(ranked(doc, n))}


def best_portfolio(doc):
    return ((doc.get("portfolio_optimization") or {}).get("best_portfolio") or {})


def portfolio_key(doc):
    p = best_portfolio(doc)
    steps = p.get("steps") or []
    return " || ".join(key(x) for x in steps) if steps else "NONE"


def compare(reference, candidate):
    rr = ranked(reference, 10)
    cr = ranked(candidate, 10)
    rs, cs = set(rr), set(cr)
    ref_best = key(recommended(reference))
    cand_best = key(recommended(candidate))
    rscore, cscore = score_map(reference), score_map(candidate)
    common = rs & cs
    mae = sum(abs(cscore[x] - rscore[x]) for x in common) / len(common) if common else None
    rranks, cranks = rank_map(reference), rank_map(candidate)
    rank_common = set(rranks) & set(cranks)
    rank_mae = (
        sum(abs(rranks[x] - cranks[x]) for x in rank_common) / len(rank_common)
        if rank_common else None
    )
    rp, cp = best_portfolio(reference), best_portfolio(candidate)
    ref_portfolio_score = float(rp.get("team_improvement_score") or 0.0)
    cand_portfolio_score = float(cp.get("team_improvement_score") or 0.0)
    return {
        "best_action_match": cand_best == ref_best,
        "reference_best": ref_best,
        "candidate_best": cand_best,
        "top_10_recall": round(len(common) / max(1, len(rs)), 4),
        "top_5_overlap": len(set(rr[:5]) & set(cr[:5])),
        "top_3_overlap": len(set(rr[:3]) & set(cr[:3])),
        "common_candidate_score_mae": None if mae is None else round(mae, 6),
        "common_candidate_rank_mae": None if rank_mae is None else round(rank_mae, 4),
        "best_portfolio_match": portfolio_key(reference) == portfolio_key(candidate),
        "reference_best_portfolio": portfolio_key(reference),
        "candidate_best_portfolio": portfolio_key(candidate),
        "reference_best_portfolio_move_count": int(rp.get("move_count") or 0),
        "candidate_best_portfolio_move_count": int(cp.get("move_count") or 0),
        "best_portfolio_score_absolute_error": round(abs(cand_portfolio_score - ref_portfolio_score), 6),
        "candidate_bundles_evaluated": int(((candidate.get("portfolio_optimization") or {}).get("candidate_bundles_evaluated")) or 0),
        "candidate_search_summary": candidate.get("search_summary") or (candidate.get("source_team_improvement") or {}).get("search_summary") or {},
        "candidate_search_configuration": candidate.get("search_configuration") or {},
    }


def runtimes(path):
    if not path:
        return {}
    with Path(path).open(newline="", encoding="utf-8") as fh:
        return {row["configuration"]: int(row["runtime_seconds"]) for row in csv.DictReader(fh)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reference", required=True)
    ap.add_argument("--candidate", action="append", required=True, help="label=path")
    ap.add_argument("--runtime-csv")
    ap.add_argument("--output", required=True)
    a = ap.parse_args()
    ref = load(a.reference)
    runtime = runtimes(a.runtime_csv)
    results = {}
    for spec in a.candidate:
        label, path = spec.split("=", 1)
        results[label] = compare(ref, load(path))
        if label in runtime:
            results[label]["runtime_seconds"] = runtime[label]
            results[label]["runtime_ratio_vs_prod"] = round(
                runtime[label] / max(1, runtime.get("prod", runtime[label])), 3
            )
    out = {
        "purpose": "Opportunity Engine search-depth, package-depth, adaptive-portfolio and Monte Carlo stability calibration",
        "coefficient_tuning": False,
        "reference_is_ground_truth": False,
        "reference_role": "deeper same-state computational benchmark",
        "reference_runtime_seconds": runtime.get("reference"),
        "results": results,
        "interpretation_policy": {
            "prefer_smallest_budget_that_preserves_best_action_and_high_top_10_recall": True,
            "prefer_smallest_portfolio_budget_that_preserves_leading_bundle_when_practical": True,
            "runtime_is_part_of_budget_selection": True,
            "package_depth_and_beam_width_are_search_budgets_not_value_weights": True,
            "do_not_change_valuation_coefficients_from_this_audit": True,
        },
    }
    Path(a.output).write_text(json.dumps(out, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(out, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
