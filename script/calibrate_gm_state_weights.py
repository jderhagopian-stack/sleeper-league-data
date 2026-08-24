#!/usr/bin/env python3
"""Offline calibration harness for FSFFL franchise-state objective weights.

This program is deliberately NOT imported by Decision Lab or Market Sweep.
It is intended for scheduled/manual GM rebuild work. It evaluates candidate
continuous weight curves against a historical feature/outcome dataset and emits
an artifact that the lightweight gm_state_weighting runtime can read.

Historical FSFFL data has known limitations (especially historical market value
at transaction time), so calibration is regularized toward the GM 2.2/3.0
expert priors and refuses to label an output validated unless minimum evidence
and holdout-improvement thresholds are met.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = ROOT / "data" / "gm" / "state_weight_training_examples.json"
DEFAULT_PRIOR = ROOT / "data" / "gm" / "state_weight_calibration.json"
DEFAULT_OUTPUT = ROOT / "data" / "gm" / "state_weight_calibration_candidate.json"
DEFAULT_REPORT = ROOT / "data" / "gm" / "state_weight_calibration_report.json"
KEYS = ("current", "future", "liquidity", "resilience")


def load(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")


def sf(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def normalize(w):
    x = {k: max(sf(w.get(k)), 0.0) for k in KEYS}
    z = sum(x.values()) or 1.0
    return {k: x[k] / z for k in KEYS}


def interpolate(c, anchors):
    pts = sorted((sf(x["contender_score"]), normalize(x["weights"])) for x in anchors)
    c = clamp(sf(c), 0.0, 1.0)
    if c <= pts[0][0]: return dict(pts[0][1])
    if c >= pts[-1][0]: return dict(pts[-1][1])
    for (x0,w0),(x1,w1) in zip(pts, pts[1:]):
        if x0 <= c <= x1:
            t = (c-x0) / max(x1-x0, 1e-9)
            return normalize({k:w0[k]+t*(w1[k]-w0[k]) for k in KEYS})
    return dict(pts[-1][1])


def utility(example, weights):
    """Ex-post strategic utility from normalized historical outcome features."""
    outcome = example.get("outcome") or {}
    blocks = {
        "current": sf(outcome.get("current_success")),
        "future": sf(outcome.get("future_value_success")),
        "liquidity": sf(outcome.get("liquidity_success")),
        "resilience": sf(outcome.get("resilience_success")),
    }
    return sum(weights[k] * blocks[k] for k in KEYS)


def observed_target(example):
    """Target in [-1,1]. Prefer explicit strategy_outcome_score when supplied."""
    outcome = example.get("outcome") or {}
    if outcome.get("strategy_outcome_score") is not None:
        return clamp(sf(outcome.get("strategy_outcome_score")), -1.0, 1.0)
    vals = [sf(outcome.get(k)) for k in ("current_success","future_value_success","liquidity_success","resilience_success")]
    return clamp(sum(vals)/max(len(vals),1), -1.0, 1.0)


def score_examples(examples, anchors, prior_anchors=None, regularization=.12):
    if not examples:
        return {"loss": None, "mae": None, "n": 0}
    err = []
    for ex in examples:
        inp = ex.get("inputs") or {}
        w = interpolate(inp.get("contender_score", .5), anchors)
        pred = utility(ex, w)
        err.append(pred - observed_target(ex))
    mse = sum(e*e for e in err)/len(err)
    mae = sum(abs(e) for e in err)/len(err)
    reg = 0.0
    if prior_anchors:
        prior_by_x = {round(sf(x["contender_score"]),4): normalize(x["weights"]) for x in prior_anchors}
        for a in anchors:
            pw = prior_by_x.get(round(sf(a["contender_score"]),4))
            if not pw: continue
            aw = normalize(a["weights"])
            reg += sum((aw[k]-pw[k])**2 for k in KEYS)
    return {"loss": mse + regularization*reg, "mse": mse, "mae": mae, "regularization": reg, "n": len(err)}


def seasons(examples):
    return sorted({str((x.get("metadata") or {}).get("season")) for x in examples if (x.get("metadata") or {}).get("season") is not None})


def candidate_anchors(prior):
    """Conservative grid around expert priors; preserve monotone strategic shape."""
    base = prior.get("anchor_points") or []
    deltas = (-0.04, -0.02, 0.0, 0.02, 0.04)
    # Tune present/future tradeoff only; liquidity/resilience remain prior-anchored
    # until historical evidence is rich enough to identify all four independently.
    for shifts in itertools.product(deltas, repeat=len(base)):
        rows=[]; valid=True; prev_current=-1.0
        for row,shift in zip(base, shifts):
            w=dict(row["weights"])
            shift=clamp(shift, -max(w["current"]-.05,0), max(w["future"]-.18,0))
            w["current"] += shift
            w["future"] -= shift
            w=normalize(w)
            if w["current"] + 1e-9 < prev_current:
                valid=False; break
            prev_current=w["current"]
            rows.append({"contender_score": row["contender_score"], "weights": w})
        if valid:
            yield rows


def leave_one_season_out(examples, prior, min_examples=60):
    ss=seasons(examples)
    prior_anchors=prior.get("anchor_points") or []
    if len(examples) < min_examples or len(ss) < 3:
        return None
    folds=[]
    for hold in ss:
        train=[x for x in examples if str((x.get("metadata") or {}).get("season")) != hold]
        test=[x for x in examples if str((x.get("metadata") or {}).get("season")) == hold]
        best=None
        for anchors in candidate_anchors(prior):
            sc=score_examples(train, anchors, prior_anchors)
            if best is None or sc["loss"] < best[0]: best=(sc["loss"], anchors)
        cand=score_examples(test,best[1],prior_anchors,regularization=0)
        base=score_examples(test,prior_anchors,prior_anchors,regularization=0)
        folds.append({"season":hold,"n":len(test),"candidate_mae":cand["mae"],"prior_mae":base["mae"],"improvement":(base["mae"] or 0)-(cand["mae"] or 0)})
    return folds


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--input", default=str(DEFAULT_INPUT))
    ap.add_argument("--prior", default=str(DEFAULT_PRIOR))
    ap.add_argument("--output", default=str(DEFAULT_OUTPUT))
    ap.add_argument("--report", default=str(DEFAULT_REPORT))
    ap.add_argument("--min-examples", type=int, default=60)
    ap.add_argument("--min-holdout-improvement", type=float, default=.005)
    args=ap.parse_args()

    examples=load(Path(args.input), []) or []
    prior=load(Path(args.prior), {}) or {}
    if not prior.get("anchor_points"):
        raise SystemExit("Missing prior calibration anchor points")

    report={
        "model_version":"FSFFL-GM-State-Weight-Calibrator-1.0",
        "input":args.input,
        "sample":len(examples),
        "seasons":seasons(examples),
        "method":"regularized conservative grid + leave-one-season-out validation",
        "runtime_separation":"offline_only; never imported by Decision Lab or Market Sweep",
        "limitations":[
            "Historical market value at the exact time of older transactions is incomplete unless supplied by the training dataset.",
            "Completed transactions omit rejected offers and therefore do not identify the full opportunity set.",
            "Pre-trade roster reconstruction is approximate for older seasons.",
        ],
    }
    folds=leave_one_season_out(examples, prior, args.min_examples)
    report["holdout_folds"]=folds or []

    if not folds:
        report["status"]="INSUFFICIENT_EVIDENCE_KEEP_PRIOR"
        report["promotion_allowed"]=False
        dump(Path(args.report),report)
        # Deliberately write the prior as a candidate, clearly marked unvalidated.
        candidate=dict(prior)
        candidate["status"]="PRIOR_RETAINED_INSUFFICIENT_HISTORICAL_EVIDENCE"
        dump(Path(args.output),candidate)
        print(json.dumps(report,indent=2))
        return

    best=None
    for anchors in candidate_anchors(prior):
        sc=score_examples(examples,anchors,prior.get("anchor_points") or [])
        if best is None or sc["loss"] < best[0]: best=(sc["loss"],anchors,sc)
    weighted_improvement=sum(f["improvement"]*f["n"] for f in folds)/max(sum(f["n"] for f in folds),1)
    passed=weighted_improvement >= args.min_holdout_improvement
    report.update({"weighted_holdout_mae_improvement":weighted_improvement,"fit":best[2],"status":"VALIDATED_CANDIDATE" if passed else "NO_MATERIAL_HOLDOUT_IMPROVEMENT_KEEP_PRIOR","promotion_allowed":passed})

    candidate=dict(prior)
    if passed:
        candidate["anchor_points"]=best[1]
        candidate["status"]="HISTORICALLY_CROSS_VALIDATED_CANDIDATE"
    else:
        candidate["status"]="PRIOR_RETAINED_NO_MATERIAL_HOLDOUT_IMPROVEMENT"
    candidate["calibration_validation"]={"sample":len(examples),"seasons":seasons(examples),"weighted_holdout_mae_improvement":weighted_improvement,"promotion_allowed":passed}
    dump(Path(args.output),candidate)
    dump(Path(args.report),report)
    print(json.dumps(report,indent=2))


if __name__ == "__main__":
    main()
