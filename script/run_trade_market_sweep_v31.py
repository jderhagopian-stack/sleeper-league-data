#!/usr/bin/env python3
"""FSFFL Counter & Market Sweep 1.25 - evidence-consistent option governance.

Extends validated 1.24 without changing candidate generation or simulation.

Architecture:
- trade quality is determined by actual simulated football outcomes plus the
  model's overall franchise-impact output, never by counterparty acceptance fit;
- counterparty acceptance fit remains a separate Behavioral Intelligence output;
- post-simulation composite score remains available as a diagnostic/search signal,
  but no unsupported numeric score cutoff can create BETTER/WORSE labels;
- BETTER/WORSE are Pareto classifications over core competitive outcomes and
  overall franchise impact; conflicting evidence is a MIXED tradeoff;
- descriptive competitive-state labels do not create focal-utility cliffs.

No player-specific exceptions are permitted.
"""
from __future__ import annotations
import importlib.util,json,sys
from pathlib import Path

SCRIPT=Path(__file__).resolve().parent
V30=SCRIPT/'run_trade_market_sweep_v30.py'
MODEL_VERSION='FSFFL-Counter-Market-Sweep-1.25'
EPS=1e-9


def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def sf(v,d=0.0):
    try:return float(v)
    except (TypeError,ValueError):return d


def out_path():
    if '--output' not in sys.argv:return None
    i=sys.argv.index('--output');return Path(sys.argv[i+1]) if i+1<len(sys.argv) else None


def metric(row,key):
    sim=row.get('simulation') or {};d=sim.get('focus_delta') or {};st=sim.get('strategic') or {}
    if key in d:return sf(d.get(key))
    if key=='net_title_equity_swing_against_focus':return sf(sim.get(key))
    return sf(st.get(key))


def objective_state(row):
    sim=row.get('simulation') or {};st=sim.get('strategic') or {}
    return str(st.get('objective_state') or row.get('focal_current_state') or 'unknown')


def relation_from_deltas(deltas):
    any_better=any(v>EPS for v in deltas.values())
    any_worse=any(v<-EPS for v in deltas.values())
    if any_better and not any_worse:return 'DOMINATES_CURRENT_OFFER'
    if any_worse and not any_better:return 'DOMINATED_BY_CURRENT_OFFER'
    if not any_better and not any_worse:return 'EQUIVALENT_TO_CURRENT_OFFER'
    return 'TRADEOFF_VS_CURRENT_OFFER'


def competitive_relation(row,current):
    keys=('expected_points_for','expected_wins','playoff_probability','championship_probability')
    deltas={k:metric(row,k)-metric(current,k) for k in keys}
    return relation_from_deltas(deltas),deltas


def compare(row,current):
    diagnostic_keys=('expected_wins','expected_points_for','playoff_probability','bye_probability','championship_probability','market_dynasty_delta','strategic_value_delta','liquidity_value_delta','break_glass_delta','roster_interaction_value_delta','net_title_equity_swing_against_focus')
    deltas={k:round(metric(row,k)-metric(current,k),5) for k in diagnostic_keys}
    score_delta=round(sf(row.get('post_sim_score'))-sf(current.get('post_sim_score')),2)

    # Categorical trade quality uses only interpretable decision outputs. The
    # first four are simulated football results; strategic_value_delta is the
    # model's bottom-line franchise impact, which already blends future value,
    # roster fit and flexibility. No arbitrary score magnitude threshold is used.
    decision_keys=('expected_points_for','expected_wins','playoff_probability','championship_probability','strategic_value_delta')
    decision_deltas={k:deltas[k] for k in decision_keys}
    decision_relation=relation_from_deltas(decision_deltas)
    verdict={
        'DOMINATES_CURRENT_OFFER':'BETTER',
        'DOMINATED_BY_CURRENT_OFFER':'WORSE',
    }.get(decision_relation,'MIXED')
    comp_relation,_=competitive_relation(row,current)

    drivers=[]
    if abs(deltas['expected_points_for'])>=10:drivers.append(f"{deltas['expected_points_for']:+.1f} expected points")
    if abs(deltas['expected_wins'])>=.05:drivers.append(f"{deltas['expected_wins']:+.2f} expected wins")
    if abs(deltas['playoff_probability'])>=.01:drivers.append(f"{deltas['playoff_probability']*100:+.1f} pts playoff probability")
    if abs(deltas['championship_probability'])>=.005:drivers.append(f"{deltas['championship_probability']*100:+.1f} pts championship probability")
    if abs(deltas['strategic_value_delta'])>=200:drivers.append(f"{deltas['strategic_value_delta']:+,.0f} franchise value")
    if abs(deltas['market_dynasty_delta'])>=500:drivers.append(f"{deltas['market_dynasty_delta']:+,.0f} dynasty value")
    if abs(deltas['liquidity_value_delta'])>=500:drivers.append(f"{deltas['liquidity_value_delta']:+,.0f} trade flexibility")
    if not drivers:drivers.append(f"{score_delta:+,.0f} composite-score diagnostic")
    lead='Clearly better than the current offer across the decision outputs' if verdict=='BETTER' else 'Clearly worse than the current offer across the decision outputs' if verdict=='WORSE' else 'A mixed tradeoff versus the current offer'
    return {
        'verdict_vs_current_offer':verdict,
        'post_sim_score_delta_vs_current_offer':score_delta,
        'post_sim_score_role':'DIAGNOSTIC_ONLY_NOT_CATEGORICAL_DECISION_RULE',
        'metric_deltas_vs_current_offer':deltas,
        'decision_metric_deltas_vs_current_offer':decision_deltas,
        'decision_relation_vs_current_offer':decision_relation,
        'competitive_relation_vs_current_offer':comp_relation,
        'reason':lead+', driven by '+', '.join(drivers[:6])+'.',
        'comparison_basis':'pareto_dominance_across_core_competitive_outcomes_and_overall_franchise_impact',
        'unsupported_numeric_score_cutoff_used':False,
    }


def acceptance(row):
    return str(row.get('acceptance_likelihood') or ((row.get('buyer_rationality') or {}).get('heuristic_acceptance_fit')) or 'UNKNOWN')


def current_mutually_viable(current):
    state=objective_state(current);post=sf(current.get('post_sim_score'))
    focal=post>0
    if state in {'contender','elite_contender'} and current.get('championship_equity_constraint')=='FAIL':focal=False
    buyer=bool((current.get('buyer_rationality') or {}).get('current_state_viable'))
    return focal and buyer


def recompute_action(report,inherited):
    current=report.get('current_offer_evaluation') or {}
    if not current_mutually_viable(current):return inherited,'INHERITED_CURRENT_OFFER_NOT_MUTUALLY_VIABLE'
    counters=[x for x in (report.get('suggested_counteroffers') or []) if (x.get('comparison_to_current_offer') or {}).get('verdict_vs_current_offer')=='BETTER']
    markets=[x for x in (report.get('market_sweep_alternatives') or []) if (x.get('comparison_to_current_offer') or {}).get('verdict_vs_current_offer')=='BETTER']
    # Feasibility never changes whether an option is BETTER. Existing action
    # vocabulary is retained for renderer compatibility; Behavioral Intelligence
    # fit is displayed separately so the user can judge pursuit practicality.
    if counters:return 'COUNTER_CURRENT_OFFEROR','BETTER_SAME_PARTNER_COUNTER_EXISTS_FEASIBILITY_REPORTED_SEPARATELY'
    if markets:return 'SHOP_BEFORE_ACCEPTING','BETTER_MARKET_ALTERNATIVE_EXISTS_FEASIBILITY_REPORTED_SEPARATELY'
    return 'ACCEPT_NOW','NO_BETTER_OPTION_THAN_MUTUALLY_VIABLE_CURRENT_OFFER'


def main():
    v30=load(V30,'market_v30_for_125');v30.main();out=out_path()
    if not out or not out.exists():return
    report=json.loads(out.read_text(encoding='utf-8'));current=report.get('current_offer_evaluation') or {}
    for section in ('suggested_counteroffers','market_sweep_alternatives'):
        for row in report.get(section) or []:
            comp=compare(row,current)
            row['comparison_to_current_offer']=comp
            row['why_prefer_over_current_offer']=comp['reason']
            row['why_advantageous_for_focus']=comp['reason']
            row['counterparty_feasibility']={'acceptance_fit':acceptance(row),'source':'BEHAVIORAL_INTELLIGENCE','affects_trade_valuation':False,'reported_separately':True}
            row['actionable_better_than_current_offer']=comp.get('verdict_vs_current_offer')=='BETTER'
    inherited=str(report.get('recommended_next_action') or 'REVIEW')
    final_action,action_basis=recompute_action(report,inherited)
    report['recommended_next_action_pre_outcome_consistency']=inherited
    report['recommended_next_action']=final_action
    report.setdefault('governance',{})['option_outcome_consistency']={
        'categorical_score_threshold_removed':True,
        'post_sim_score_is_diagnostic_not_categorical_decision_rule':True,
        'better_worse_uses_pareto_decision_outputs':True,
        'decision_outputs':['expected_points_for','expected_wins','playoff_probability','championship_probability','strategic_value_delta'],
        'acceptance_fit_affects_trade_valuation':False,
        'acceptance_fit_reported_as_separate_behavioral_intelligence':True,
        'acceptance_fit_hard_gate_on_trade_quality':False,
        'descriptive_state_labels_create_action_cliffs':False,
        'current_offer_action_recomputed_after_final_option_comparisons':True,
        'action_basis':action_basis,
        'player_specific_exceptions':False,
    }
    report['model_version']=MODEL_VERSION
    report.setdefault('policy',{}).update({
        'option_comparison_model_version':'FSFFL-Option-Outcome-Consistency-1.3',
        'unsupported_numeric_score_cutoff_used_for_better_worse':False,
        'state_aware_score_is_search_and_diagnostic_signal_not_categorical_better_proof':True,
        'better_requires_no_regression_across_decision_outputs':True,
        'worse_requires_no_improvement_across_decision_outputs':True,
        'conflicting_decision_outputs_are_mixed':True,
        'acceptance_likelihood_is_separate_from_trade_valuation':True,
        'behavioral_intelligence_informs_counterparty_feasibility_not_trade_value':True,
        'low_or_very_low_acceptance_changes_trade_quality_verdict':False,
        'descriptive_state_labels_create_action_cliffs':False,
        'mixed_tradeoffs_remain_visible':True,
        'candidate_generation_unchanged':True,
        'simulation_unchanged':True,
    })
    report.setdefault('simulation',{})['execution_path']=str((report.get('simulation') or {}).get('execution_path') or '')+'_plus_outcome_consistent_option_governance'
    out.write_text(json.dumps(report,indent=2,sort_keys=True),encoding='utf-8')


if __name__=='__main__':main()
