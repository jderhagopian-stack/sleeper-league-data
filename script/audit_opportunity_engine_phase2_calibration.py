#!/usr/bin/env python3
"""Compare Opportunity Engine search/simulation configurations against a deep reference.

This is an empirical search-budget diagnostic. It does not tune valuation weights or
change any owning model. Candidate identity/rank stability and score error are measured
relative to a deliberately deeper reference run produced from the same model state.
"""
from __future__ import annotations

import argparse
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


def ranked(doc, n=10):
    return [key(x) for x in (doc.get("source_team_improvement", {}).get("top_cross_channel_options") or doc.get("top_cross_channel_options") or [])[:n]]


def score_map(doc):
    rows = doc.get("source_team_improvement", {}).get("top_cross_channel_options") or doc.get("top_cross_channel_options") or []
    return {key(x): float(x.get("team_improvement_score") or 0.0) for x in rows}


def compare(reference, candidate):
    rr = ranked(reference, 10)
    cr = ranked(candidate, 10)
    rs, cs = set(rr), set(cr)
    ref_best = key(reference.get("recommended_action") or (reference.get("source_team_improvement") or {}).get("recommended_action"))
    cand_best = key(candidate.get("recommended_action") or (candidate.get("source_team_improvement") or {}).get("recommended_action"))
    rscore, cscore = score_map(reference), score_map(candidate)
    common = rs & cs
    mae = sum(abs(cscore[x] - rscore[x]) for x in common) / len(common) if common else None
    return {
        "best_action_match": cand_best == ref_best,
        "reference_best": ref_best,
        "candidate_best": cand_best,
        "top_10_recall": round(len(common) / max(1, len(rs)), 4),
        "top_5_overlap": len(set(rr[:5]) & set(cr[:5])),
        "common_candidate_score_mae": None if mae is None else round(mae, 6),
        "candidate_search_summary": (candidate.get("source_team_improvement") or candidate).get("search_summary") or {},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reference", required=True)
    ap.add_argument("--candidate", action="append", required=True, help="label=path")
    ap.add_argument("--output", required=True)
    a = ap.parse_args()
    ref = load(a.reference)
    results = {}
    for spec in a.candidate:
        label, path = spec.split("=", 1)
        results[label] = compare(ref, load(path))
    out = {
        "purpose": "Opportunity Engine search-depth and Monte Carlo stability calibration",
        "coefficient_tuning": False,
        "reference_is_ground_truth": False,
        "reference_role": "deeper same-state computational benchmark",
        "results": results,
    }
    Path(a.output).write_text(json.dumps(out, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(out, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
