#!/usr/bin/env python3
"""Offline calibration harness for FSFFL franchise-state objective weights.

This program is deliberately NOT imported by Decision Lab or Market Sweep.
It is intended for scheduled/manual GM rebuild work. It evaluates candidate
continuous weight curves against a historical feature/outcome dataset and emits
a candidate artifact for explicit promotion only after validation.

Historical FSFFL data has known limitations (especially historical market value
at transaction time), so calibration is regularized toward the GM 2.2/3.0
expert priors and refuses to label an output validated unless minimum evidence
and holdout-improvement thresholds are met.
"""
from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = ROOT / "data" / "gm" / "state_weight_training_examples.json"
DEFAULT_PRIOR = ROOT / "data" / "gm" / "state_weight_prior.json"
DEFAULT_OUTPUT = ROOT / "data" / "gm" / "state_weight_calibration_candidate.json"
DEFAULT_REPORT = ROOT / "data" / "gm" / "state_weight_calibration_report.json"
KEYS = ("current", "future", "liquidity", "resilience")


def load(path: Path, default=None):
    if not path.exists(): return default
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")


def sf(x, default=0.0):
    try: return float(x)
    except (TypeError, ValueError): return default


def clamp(x, lo, hi): return max(lo, min(hi, x))


def normalize(w):
    x={k:max(sf(w.get(k)),0.0) for k in KEYS}; z=sum(x.values()) or 1.0
    return {k:x[k]/z for k in KEYS}


def interpolate(c, anchors):
    pts=sorted((sf(x["contender_score"]),normalize(x["weights"])) for x in anchors)
    c=clamp(sf(c),0.0,1.0)
    if c<=pts[0][0]: return dict(pts[0][1])
    if c>=pts[-1][0]: return dict(pts[-1][1])
    for (x0,w0),(x1,w1) in zip(pts,pts[1:]):
        if x0<=c<=x1:
            t=(c-x0)/max(x1-x0,1e-9)
            return normalize({k:w0[k]+t*(w1[k]-w0[k]) for k in KEYS})
    return dict(pts[-1][1])


def utility(example, weights):
    outcome=example.get("outcome") or {}
    blocks={
        "current":sf(outcome.get("current_success")),
        "future":sf(outcome.get("future_value_success")),
        "liquidity":sf(outcome.get("liquidity_success")),
        "resilience":sf(outcome.get("resilience_success")),
    }
    return sum(weights[k]*blocks[k] for k in KEYS)


def observed_target(example):
    """Independent calibration target in [-1,1], or None when unavailable.

    Component outcomes are model inputs to the utility function. Their average is
    therefore not an independent target for learning those same weights.
    """
    outcome=example.get("outcome") or {}
    if outcome.get("strategy_outcome_score") is None: return None
    return clamp(sf(outcome.get("strategy_outcome_score")),-1.0,1.0)


def eligible_examples(examples): return [x for x in examples if observed_target(x) is not None]


def score_examples(examples, anchors, prior_anchors=None, regularization=.12):
    if not examples: return {"loss":None,"mae":None,"n":0}
    err=[]
    for ex in examples:
        target=observed_target(ex)
        if target is None: continue
        inp=ex.get("inputs") or {}
        pred=utility(ex,interpolate(inp.get("contender_score",.5),anchors))
        err.append(pred-target)
    if not err: return {"loss":None,"mae":None,"n":0}
    mse=sum(e*e for e in err)/len(err); mae=sum(abs(e) for e in err)/len(err); reg=0.0
    if prior_anchors:
        prior_by_x={round(sf(x["contender_score"]),4):normalize(x["weights"]) for x in prior_anchors}
        for a in anchors:
            pw=prior_by_x.get(round(sf(a["contender_score"]),4))
            if not pw: continue
            aw=normalize(a["weights"]); reg+=sum((aw[k]-pw[k])**2 for k in KEYS)
    return {"loss":mse+regularization*reg,"mse":mse,"mae":mae,"regularization":reg,"n":len(err)}


def seasons(examples):
    return sorted({str((x.get("metadata") or {}).get("season")) for x in examples if (x.get("metadata") or {}).get("season") is not None})


def candidate_anchors(prior):
    base=prior.get("anchor_points") or []; deltas=(-0.04,-0.02,0.0,0.02,0.04)
    for shifts in itertools.product(deltas,repeat=len(base)):
        rows=[]; valid=True; prev_current=-1.0
        for row,shift in zip(base,shifts):
            w=dict(row["weights"])
            shift=clamp(shift,-max(w["current"]-.05,0),max(w["future"]-.18,0))
            w["current"]+=shift; w["future"]-=shift; w=normalize(w)
            if w["current"]+1e-9<prev_current: valid=False; break
            prev_current=w["current"]
            rows.append({"contender_score":row["contender_score"],"weights":w})
        if valid: yield rows


def leave_one_season_out(examples, prior, min_examples=60):
    ss=seasons(examples); prior_anchors=prior.get("anchor_points") or []
    if len(examples)<min_examples or len(ss)<3: return None
    folds=[]
    for hold in ss:
        train=[x for x in examples if str((x.get("metadata") or {}).get("season"))!=hold]
        test=[x for x in examples if str((x.get("metadata") or {}).get("season"))==hold]
        best=None
        for anchors in candidate_anchors(prior):
            sc=score_examples(train,anchors,prior_anchors)
            if best is None or sc["loss"]<best[0]: best=(sc["loss"],anchors)
        cand=score_examples(test,best[1],prior_anchors,regularization=0)
        base=score_examples(test,prior_anchors,prior_anchors,regularization=0)
        folds.append({"season":hold,"n":len(test),"candidate_mae":cand["mae"],"prior_mae":base["mae"],"improvement":(base["mae"] or 0)-(cand["mae"] or 0)})
    return folds


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--input",default=str(DEFAULT_INPUT)); ap.add_argument("--prior",default=str(DEFAULT_PRIOR))
    ap.add_argument("--output",default=str(DEFAULT_OUTPUT)); ap.add_argument("--report",default=str(DEFAULT_REPORT))
    ap.add_argument("--min-examples",type=int,default=60); ap.add_argument("--min-holdout-improvement",type=float,default=.005)
    args=ap.parse_args()

    examples=load(Path(args.input),[]) or []; calibration_examples=eligible_examples(examples)
    prior=load(Path(args.prior),{}) or {}
    if not prior.get("anchor_points"): raise SystemExit("Missing prior calibration anchor points")

    report={
        "model_version":"FSFFL-GM-State-Weight-Calibrator-1.1",
        "input":args.input,"prior":args.prior,"sample":len(examples),"eligible_sample":len(calibration_examples),
        "excluded_without_independent_target":len(examples)-len(calibration_examples),"seasons":seasons(calibration_examples),
        "target_policy":"explicit strategy_outcome_score required; component-outcome average is forbidden as a calibration target",
        "method":"regularized conservative grid + leave-one-season-out validation",
        "runtime_separation":"offline_only; never imported by Decision Lab or Market Sweep",
        "limitations":[
            "Historical market value at the exact time of older transactions is incomplete unless supplied by the training dataset.",
            "Completed transactions omit rejected offers and therefore do not identify the full opportunity set.",
            "Pre-trade roster reconstruction is approximate for older seasons.",
        ],
    }
    folds=leave_one_season_out(calibration_examples,prior,args.min_examples); report["holdout_folds"]=folds or []
    if not folds:
        report["status"]="INSUFFICIENT_EVIDENCE_KEEP_PRIOR"; report["promotion_allowed"]=False; dump(Path(args.report),report)
        candidate=dict(prior); candidate["status"]="PRIOR_RETAINED_INSUFFICIENT_HISTORICAL_EVIDENCE"; dump(Path(args.output),candidate)
        print(json.dumps(report,indent=2)); return

    best=None
    for anchors in candidate_anchors(prior):
        sc=score_examples(calibration_examples,anchors,prior.get("anchor_points") or [])
        if best is None or sc["loss"]<best[0]: best=(sc["loss"],anchors,sc)
    weighted_improvement=sum(f["improvement"]*f["n"] for f in folds)/max(sum(f["n"] for f in folds),1)
    passed=weighted_improvement>=args.min_holdout_improvement
    report.update({"weighted_holdout_mae_improvement":weighted_improvement,"fit":best[2],"status":"VALIDATED_CANDIDATE" if passed else "NO_MATERIAL_HOLDOUT_IMPROVEMENT_KEEP_PRIOR","promotion_allowed":passed})
    candidate=dict(prior)
    if passed:
        candidate["anchor_points"]=best[1]; candidate["status"]="HISTORICALLY_CROSS_VALIDATED_CANDIDATE"
    else: candidate["status"]="PRIOR_RETAINED_NO_MATERIAL_HOLDOUT_IMPROVEMENT"
    candidate["calibration_validation"]={"sample":len(calibration_examples),"seasons":seasons(calibration_examples),"weighted_holdout_mae_improvement":weighted_improvement,"promotion_allowed":passed}
    dump(Path(args.output),candidate); dump(Path(args.report),report); print(json.dumps(report,indent=2))


if __name__=="__main__": main()
