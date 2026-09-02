#!/usr/bin/env python3
"""Canonical Shared Decision Utility confirmation for GM3 search-cutoff candidates.

Consumes the stage-1 hard-cutoff recall screen. Only a bounded, deterministic
sample of flagged packages is simulated. This remains shadow research and cannot
change production search policy.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
SCRIPT=ROOT/"script"
DATA=ROOT/"data"
STAGE1=DATA/"audit"/"trade_search_hard_cutoff_recall.json"
OUT=DATA/"audit"/"trade_search_hard_cutoff_shared_utility_confirmation.json"
MAX_CONFIRMATIONS=12
SIMULATIONS=250
SEED=20260901

if str(SCRIPT) not in sys.path:
    sys.path.insert(0,str(SCRIPT))

from gm3 import team_improvement

def loadmod(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def load(path,default=None):
    try:return json.loads(path.read_text(encoding="utf-8"))
    except Exception:return default

def main():
    stage1=load(STAGE1,{}) or {}
    candidates=list(stage1.get("candidate_recall_risk_targets") or [])
    base=loadmod(SCRIPT/"run_team_improvement_lab.py","cutoff_confirm_base")
    players,picks=base.asset_catalog()
    catalog={}
    catalog.update(players); catalog.update(picks)

    # Deterministic high-value-first confirmation budget across the league.
    candidates.sort(
        key=lambda x:(
            float(x.get("focal_owner_value") or 0.0),
            float(x.get("market_dynasty") or 0.0),
            str(x.get("focal_user_id") or ""),
            str(x.get("asset_id") or ""),
        ),
        reverse=True,
    )
    candidates=candidates[:MAX_CONFIRMATIONS]
    by_focus=defaultdict(list)
    for c in candidates:
        by_focus[str(c.get("focal_user_id") or "")].append(c)

    confirmations=[]
    evaluators={}
    for focus_uid in sorted(by_focus):
        if not focus_uid:
            continue
        evaluator=team_improvement.portfolio_evaluator(
            focus_uid,simulations=SIMULATIONS,seed=SEED,strategic_posture="AUTO"
        )
        evaluators[focus_uid]=evaluator
        for c in by_focus[focus_uid]:
            pkg=c.get("best_viable_package") or {}
            outgoing_ids=[str(x) for x in (pkg.get("focal_outgoing_asset_ids") or [])]
            target_id=str(c.get("asset_id") or "")
            seller=str(c.get("seller_user_id") or "")
            target=catalog.get(target_id)
            outgoing=[catalog.get(aid) for aid in outgoing_ids]
            if not target or not outgoing_ids or any(x is None for x in outgoing) or not seller:
                confirmations.append({
                    "focal_user_id":focus_uid,
                    "target_asset_id":target_id,
                    "status":"UNCONFIRMABLE_MISSING_CATALOG_OR_PACKAGE",
                    "canonical_false_negative_confirmed":False,
                })
                continue
            row={
                "channel":"TRADE",
                "seller_user_id":seller,
                "target":target,
                "outgoing":outgoing,
                "source":"coefficient_recalibration_cutoff_shadow",
            }
            result=evaluator.evaluate([row])
            focal=float(result.get("team_improvement_score") or 0.0)
            counterparty=result.get("counterparty_shared_decision_utility_score")
            counterparty=float(counterparty) if counterparty is not None else None
            bilateral=(focal>0.0 and counterparty is not None and counterparty>=0.0)
            confirmations.append({
                "focal_user_id":focus_uid,
                "seller_user_id":seller,
                "target_asset_id":target_id,
                "target_name":c.get("name"),
                "market_dynasty":c.get("market_dynasty"),
                "position_need":c.get("position_need"),
                "outgoing_asset_ids":outgoing_ids,
                "simulation_count":SIMULATIONS,
                "seed":SEED,
                "focal_shared_decision_utility":round(focal,2),
                "counterparty_shared_decision_utility":round(counterparty,2) if counterparty is not None else None,
                "canonical_bilateral_nonnegative":bilateral,
                "canonical_false_negative_confirmed":bilateral,
                "decision_attribution":result.get("decision_attribution"),
                "authority":"GM3 Team Improvement / Shared Decision Utility",
            })

    confirmed=[x for x in confirmations if x.get("canonical_false_negative_confirmed")]
    report={
        "model_version":"FSFFL-Trade-Search-Cutoff-Shared-Utility-Confirmation-1.0",
        "authority":"SHADOW_RESEARCH_NON_AUTHORITATIVE",
        "production_behavior_changed":False,
        "source_stage1":"data/audit/trade_search_hard_cutoff_recall.json",
        "confirmation_budget":MAX_CONFIRMATIONS,
        "simulations_per_confirmation":SIMULATIONS,
        "seed":SEED,
        "summary":{
            "stage1_candidate_recall_risks":len(stage1.get("candidate_recall_risk_targets") or []),
            "canonical_candidates_attempted":len(confirmations),
            "canonical_bilateral_false_negatives_confirmed":len(confirmed),
            "evidence_against_hard_cutoff_detected":bool(confirmed),
            "production_change_authorized":False
        },
        "confirmed_false_negatives":confirmed,
        "confirmations":confirmations,
        "policy":{
            "upstream_package_band_is_not_final_authority":True,
            "focal_and_counterparty_confirmation_use_shared_decision_utility":True,
            "trade_decision_review_still_required_before_execution_advice":True,
            "single_current_league_shadow_run_is_not_universal_empirical_validation":True,
            "confirmed_false_negative_can_support_tier_c_structural_elimination_review":True,
            "promotion_requires_separate_reviewable_pr_and_full_regression":True,
            "hurts_so_good_rankings_are_not_ground_truth":True
        },
        "next_action":(
            "ELIMINATION_REVIEW_ELIGIBLE_SEPARATE_PR"
            if confirmed else
            "NO_PRODUCTION_CHANGE_EXPAND_RECALL_EVIDENCE"
        )
    }
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report["summary"],indent=2))

if __name__=="__main__":
    main()
