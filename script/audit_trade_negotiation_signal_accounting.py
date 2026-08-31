#!/usr/bin/env python3
"""Audit trade-negotiation signal ownership and double-counting controls.

This is a governance audit, not a scoring model. It verifies that the current
Trade Decision / GM3 / Behavioral Intelligence / Opportunity Engine path keeps
utility, gating, search, and descriptive evidence separate before negotiation
price-frontier work is promoted.
"""
from __future__ import annotations
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'audit'; OUT.mkdir(parents=True,exist_ok=True)
MODEL_VERSION='FSFFL-Trade-Negotiation-Signal-Accounting-1.0'
FILES={
 'utility':ROOT/'script'/'decision_utility.py',
 'ranker':ROOT/'script'/'negotiation_ranking.py',
 'bilateral':ROOT/'script'/'trade_bilateral_gate.py',
 'behavior':ROOT/'script'/'trade_decision'/'behavior_integration.py',
 'frontier':ROOT/'script'/'trade_decision'/'negotiation_frontier.py',
 'gm3':ROOT/'script'/'run_team_improvement_lab_v13.py',
 'oe_adapter':ROOT/'script'/'opportunity_engine'/'negotiation_frontier.py',
 'oe':ROOT/'script'/'opportunity_engine'/'application_v2.py',
}
def text(k): return FILES[k].read_text(encoding='utf-8')
def main():
    s={k:text(k) for k in FILES}
    checks={
      'shared_utility_excludes_negotiation_plausibility_weight': '"negotiation_plausibility_incremental_weight": 0.0' in s['utility'],
      'shared_utility_excludes_composite_break_glass_recount': '"composite_strategic_and_break_glass_incremental_weight": 0.0' in s['utility'],
      'acceptance_has_zero_final_ranking_weight': 'ACCEPTANCE_WEIGHT = 0.0' in s['ranker'],
      'owner_behavior_has_zero_final_ranking_weight': 'OWNER_BEHAVIOR_WEIGHT = 0.0' in s['ranker'],
      'ranker_declares_no_exchange_rate': 'arbitrary_strategic_acceptance_exchange_rate_authorized": False' in s['ranker'],
      'bilateral_gate_uses_counterparty_utility': 'buyer_decision_utility_score' in s['bilateral'],
      'bilateral_gate_does_not_use_acceptance_fit': 'acceptance_fit_score' not in s['bilateral'] and 'heuristic_acceptance_fit_score' not in s['bilateral'],
      'behavior_cannot_override_current_state_utility': 'behavioral_intelligence_can_override_current_state_utility"] = False' in s['behavior'],
      'frontier_owned_by_trade_decision': 'AUTHORITY = "Trade Decision"' in s['frontier'] and 'interpretation_owned_by_trade_decision": True' in s['frontier'],
      'frontier_creates_no_trade_value': 'creates_new_trade_value": False' in s['frontier'],
      'frontier_creates_no_acceptance_probability': 'creates_new_acceptance_probability": False' in s['frontier'],
      'frontier_declares_no_utility_acceptance_exchange_rate': 'no_arbitrary_utility_acceptance_exchange_rate": True' in s['frontier'],
      'seller_motivation_is_gm3_discovery_lane': "'seller_motivation':motivation" in s['gm3'],
      'seller_motivation_not_in_shared_utility': 'seller_motivation_score' not in s['utility'],
      'acceptance_fit_is_gm3_search_lane_not_final_utility': "'negotiation_fit':fit" in s['gm3'] and 'acceptance_fit_score' not in s['utility'],
      'oe_adapter_contains_no_classification_logic': 'from trade_decision.negotiation_frontier import' in s['oe_adapter'] and 'def classify_trade' not in s['oe_adapter'],
      'oe_declares_frontier_no_new_utility': "'negotiation_frontier_creates_new_utility':False" in s['oe'],
    }
    domains={
      'focal_franchise_utility':{'owner':'GM3 + Shared Decision Utility','role':'ECONOMIC_UTILITY','incremental_use':'ONE_CANONICAL_SCORE'},
      'counterparty_shared_utility':{'owner':'Trade Decision / bilateral gate consuming shared utility','role':'ECONOMIC_GATE','incremental_use':'SIGN_GATE_ONLY'},
      'market_value':{'owner':'governed market/value inputs','role':'ECONOMIC_INPUT_OR_PRICE_EVIDENCE','incremental_use':'MUST_NOT_BE_RE-ADDED_AS_NEGOTIATION_BONUS'},
      'behavioral_intelligence':{'owner':'Behavioral Intelligence; interpreted by Trade Decision','role':'DESCRIPTIVE_NEGOTIATION_EVIDENCE','incremental_use':'ZERO_FINAL_UTILITY_WEIGHT'},
      'seller_motivation':{'owner':'GM3 discovery','role':'SEARCH_SIGNAL','incremental_use':'SEARCH_COVERAGE_ONLY_UNLESS_INCREMENTAL_VALIDATION_EXISTS'},
      'target_diversity':{'owner':'GM3 discovery','role':'SEARCH_CONTROL','incremental_use':'NO_VALUE_EFFECT'},
      'negotiation_frontier':{'owner':'Trade Decision','role':'INTERPRETATION','incremental_use':'NO_NEW_SCORE'},
      'opportunity_engine':{'owner':'Opportunity Engine','role':'ORCHESTRATION_AND_PRESENTATION','incremental_use':'NO_NUMERIC_DECISION_AUTHORITY'},
    }
    findings=[]
    for name,ok in checks.items():
        if not ok: findings.append({'id':name,'severity':'CRITICAL','status':'FAILED'})
    payload={'model_version':MODEL_VERSION,'production_behavior_changed':False,'policy':{
      'same_signal_may_not_receive_positive_incremental_weight_twice':True,
      'search_signals_may_change_coverage_but_not_candidate_utility':True,
      'behavioral_evidence_may_change_negotiation_interpretation_but_not_economic_value':True,
      'market_price_evidence_may_locate_clearing_region_but_must_not_be_readded_to_focal_utility':True,
      'opportunity_engine_may_not_create_trade_feasibility_score':True,
      'promotion_of_price_frontier_requires_this_audit_to_pass':True,
    },'signal_domains':domains,'checks':checks,'summary':{'passed':all(checks.values()),'checks_passed':sum(checks.values()),'checks_total':len(checks),'failed_checks':[k for k,v in checks.items() if not v]},'findings':findings}
    (OUT/'trade_negotiation_signal_accounting_audit.json').write_text(json.dumps(payload,indent=2),encoding='utf-8')
    print(json.dumps(payload['summary'],indent=2))
    if not payload['summary']['passed']: raise SystemExit('Trade negotiation signal-accounting audit failed')
if __name__=='__main__': main()
