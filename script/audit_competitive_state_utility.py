#!/usr/bin/env python3
"""Governance audit for competitive-state utility and its offline calibrator."""
from __future__ import annotations

import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data"
OUT=DATA/"audit"
OUT.mkdir(parents=True,exist_ok=True)
CAL=ROOT/"script"/"calibrate_gm_state_weights.py"
RUNTIME=ROOT/"script"/"gm_state_weighting.py"
PRIOR=DATA/"gm"/"state_weight_calibration.json"
TRAIN=DATA/"gm"/"state_weight_training_examples.json"
MODEL_VERSION="FSFFL-Competitive-State-Utility-Governance-1.0"

def load(path,default=None):
    if not path.exists(): return default
    return json.loads(path.read_text(encoding="utf-8"))

def main():
    prior=load(PRIOR,{}) or {}
    src=CAL.read_text(encoding="utf-8")
    runtime=RUNTIME.read_text(encoding="utf-8")
    training=load(TRAIN,[]) or []
    explicit_target_required=(
        'if outcome.get("strategy_outcome_score") is None:' in src
        and "return None" in src
        and "eligible_examples" in src
        and "component-outcome average is forbidden" in src
    )
    old_equal_weight_target=("sum(vals)/max(len(vals),1)" in src)
    eligible=sum(
        1 for x in training
        if ((x.get("outcome") or {}).get("strategy_outcome_score") is not None)
    )
    temporal_gate=(
        "len(ss) < 3" in src and "min_examples=60" in src
        and "leave_one_season_out" in src
    )
    improvement_gate=("min-holdout-improvement" in src and "promotion_allowed" in src)
    offline_isolation=("offline_only" in src and "gm_state_weighting" not in src.split("runtime_separation")[0])
    prior_unvalidated=(
        prior.get("status")=="EXPERT_PRIOR_UNVALIDATED"
        and (prior.get("provenance") or {}).get("empirically_validated") is False
    )
    runtime_uses_versioned_artifact=("state_weight_calibration.json" in runtime)
    findings=[
      {
        "id":"STATE-TARGET-IDENTIFICATION-001",
        "severity":"CRITICAL" if not explicit_target_required else "INFO",
        "status":"FIXED_IN_OFFLINE_CALIBRATOR" if explicit_target_required else "INVALID_TARGET_FALLBACK_PRESENT",
        "observation":"State-weight learning now requires an explicit independent strategy_outcome_score. The four component outcomes used by the weighted utility may not be averaged and reused as the target for learning those same weights.",
        "authoritative_empirical_claim_allowed":False,
      },
      {
        "id":"STATE-PRIOR-001",
        "severity":"HIGH",
        "status":"EXPERT_PRIOR_UNVALIDATED" if prior_unvalidated else "PROVENANCE_INCONSISTENT",
        "observation":"Active current/future/liquidity/resilience anchors and their championship/dynasty adjustments remain an explicit expert prior until independent historical targets pass temporal validation.",
        "authoritative_empirical_claim_allowed":False,
      },
      {
        "id":"STATE-CALIBRATION-READINESS-001",
        "severity":"HIGH",
        "status":"READY_FOR_FIT" if eligible>=60 and len({str((x.get("metadata") or {}).get("season")) for x in training if ((x.get("outcome") or {}).get("strategy_outcome_score") is not None)})>=3 else "INSUFFICIENT_INDEPENDENT_TARGETS",
        "observation":"Calibration readiness is based on examples with an independent strategy target, not merely on examples containing the component outcome blocks.",
        "eligible_independent_target_examples":eligible,
        "authoritative_empirical_claim_allowed":False,
      },
    ]
    payload={
      "model_version":MODEL_VERSION,
      "production_behavior_changed":False,
      "policy":{
        "independent_target_required":True,
        "component_outcomes_cannot_define_their_own_weight_target":True,
        "leave_one_season_out_required":True,
        "minimum_three_temporal_cohorts_required":True,
        "minimum_sample_gate_required":True,
        "promotion_requires_holdout_improvement":True,
        "runtime_calibration_remains_forbidden":True,
      },
      "summary":{
        "prior_unvalidated":prior_unvalidated,
        "runtime_uses_versioned_artifact":runtime_uses_versioned_artifact,
        "explicit_independent_target_required":explicit_target_required,
        "old_equal_weight_target_present":old_equal_weight_target,
        "temporal_gate_present":temporal_gate,
        "improvement_gate_present":improvement_gate,
        "training_artifact_exists":TRAIN.exists(),
        "training_rows":len(training),
        "eligible_independent_target_rows":eligible,
      },
      "findings":findings,
    }
    (OUT/"competitive_state_utility_audit.json").write_text(json.dumps(payload,indent=2),encoding="utf-8")
    print(json.dumps(payload["summary"],indent=2))
    if not prior_unvalidated: raise SystemExit("Active state weights are not correctly marked unvalidated")
    if not explicit_target_required or old_equal_weight_target: raise SystemExit("State-weight calibrator target identification is unsafe")
    if not temporal_gate or not improvement_gate: raise SystemExit("State-weight promotion gates are incomplete")

if __name__=="__main__": main()
