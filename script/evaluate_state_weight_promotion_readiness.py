#!/usr/bin/env python3
"""Evaluate whether a state-weight challenger is ready for production promotion.

The offline calibrator may identify a statistically interesting candidate. This
gate is deliberately stricter: candidate fit alone cannot authorize production.
"""
from __future__ import annotations
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
DATA=ROOT/"data"
OUT=DATA/"audit"/"state_weight_promotion_readiness.json"

def load(path,default=None):
    try:return json.loads(path.read_text(encoding="utf-8"))
    except Exception:return default

def main():
    prior=load(DATA/"gm"/"state_weight_calibration.json",{}) or {}
    report=load(DATA/"gm"/"state_weight_calibration_report.json",{}) or {}
    readiness=load(DATA/"audit"/"strategy_outcome_readiness.json",{}) or {}
    shadow=load(DATA/"audit"/"state_weight_shadow_research.json",{}) or {}

    folds=report.get("holdout_folds") or []
    improvements=[
        float(x.get("improvement") or 0.0)
        for x in folds
        if x.get("improvement") is not None
    ]
    positive=sum(x>0 for x in improvements)
    negative=sum(x<0 for x in improvements)

    incumbent_class="UNVALIDATED_EXPERT_PRIOR"
    checks={
        "incumbent_explicitly_unvalidated":(
            prior.get("status")=="EXPERT_PRIOR_UNVALIDATED"
            and (prior.get("provenance") or {}).get("empirically_validated") is False
        ),
        "independent_target_artifact_exists":bool(report.get("eligible_sample")),
        "temporal_holdout_folds_exist":len(folds)>=1,
        "holdout_direction_not_uniformly_adverse":bool(improvements) and positive>0 and negative<len(improvements),
        "calibrator_reports_positive_weighted_improvement":float(report.get("weighted_holdout_mae_improvement") or 0.0)>0,
        "pristine_historical_evidence_ready":bool(
            (readiness.get("summary") or {}).get("pristine_temporal_state_weight_calibration_ready")
        ),
        "uncertainty_intervals_reported":bool(report.get("uncertainty_intervals")),
        "independent_invariance_suite_required":True,
        "shadow_comparison_is_non_authoritative":(
            shadow.get("authority")=="SHADOW_RESEARCH_NON_AUTHORITATIVE"
            if shadow else False
        ),
        "downstream_regression_comparison_attached":False,
        "liquidity_resilience_incremental_identifiability_demonstrated":False,
    }

    # The state-weight incumbent is Tier B, so a better-supported bounded
    # challenger need not prove perfection. But the calibrator alone cannot
    # promote it: evidence status, uncertainty, invariants and downstream
    # consequences must be reviewed together.
    blockers=[]
    if not checks["independent_target_artifact_exists"]:
        blockers.append("NO_INDEPENDENT_STRATEGY_OUTCOME_TARGET_ROWS")
    if not checks["temporal_holdout_folds_exist"]:
        blockers.append("NO_TEMPORAL_HOLDOUT_RESULTS")
    if not checks["pristine_historical_evidence_ready"]:
        blockers.append("POINT_IN_TIME_HISTORICAL_INPUTS_NOT_PRISTINE")
    if not checks["uncertainty_intervals_reported"]:
        blockers.append("NO_WEIGHT_UNCERTAINTY_INTERVALS")
    if not checks["downstream_regression_comparison_attached"]:
        blockers.append("NO_DECISION_LEVEL_CURRENT_VS_CANDIDATE_REGRESSION_ARTIFACT")
    if not checks["liquidity_resilience_incremental_identifiability_demonstrated"]:
        blockers.append("LIQUIDITY_RESILIENCE_NOT_INDEPENDENTLY_IDENTIFIED")

    result={
        "model_version":"FSFFL-State-Weight-Promotion-Readiness-1.0",
        "authority":"GOVERNANCE_RESEARCH_ONLY",
        "production_behavior_changed":False,
        "incumbent_evidence_class":incumbent_class,
        "calibrator_promotion_flag_is_sufficient_for_production":False,
        "calibrator_reported_promotion_allowed":bool(report.get("promotion_allowed")),
        "fold_summary":{
            "fold_count":len(improvements),
            "positive_improvement_folds":positive,
            "negative_improvement_folds":negative,
            "improvements":improvements,
        },
        "checks":checks,
        "blockers":blockers,
        "production_promotion_allowed":False,
        "recommended_status":"RETAIN_INCUMBENT_AS_GOVERNED_PRIOR",
        "next_evidence":[
            "Build timestamp-safe historical feature rows or explicitly label reconstructed rows non-pristine.",
            "Define independent multi-horizon strategy outcomes without using the weighted components as their own target.",
            "Estimate current/future relationship first; treat liquidity/resilience as ablations until incremental identifiability is shown.",
            "Report bootstrap or fold-based uncertainty bands around learned weights.",
            "Run decision-level shadow comparisons across synthetic archetypes and historical reconstructed cases.",
            "Only then apply evidence-tiered promotion review."
        ],
        "policy":{
            "tier_b_incumbent_does_not_require_challenger_perfection":True,
            "stronger_provisional_evidence_may_eventually_displace_incumbent":True,
            "statistical_fit_alone_cannot_authorize_production":True,
            "software_regression_equivalence_is_not_empirical_validation":True
        }
    }
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({
        "production_promotion_allowed":False,
        "blockers":blockers,
        "fold_count":len(improvements)
    },indent=2))

if __name__=="__main__":main()
