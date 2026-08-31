#!/usr/bin/env python3
"""Regression audit for threshold-free option-vs-offer outcome consistency."""
from __future__ import annotations
import importlib.util
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
MOD=ROOT/'script'/'trade_option_governance.py'


def load():
    spec=importlib.util.spec_from_file_location('trade_option_governance_regression',MOD)
    mod=importlib.util.module_from_spec(spec);assert spec.loader;spec.loader.exec_module(mod);return mod


def row(post,points,wins,playoffs,title,dynasty=0,liquidity=0,strategic=0,accept='MEDIUM'):
    return {
      'post_sim_score':post,'acceptance_likelihood':accept,'championship_equity_constraint':'PASS',
      'focal_current_state_beneficial':True,
      'buyer_rationality':{'current_state_viable':True,'heuristic_acceptance_fit':accept},
      'simulation':{'focus_delta':{'expected_points_for':points,'expected_wins':wins,'playoff_probability':playoffs,'bye_probability':0,'championship_probability':title},'strategic':{'objective_state':'retool','market_dynasty_delta':dynasty,'liquidity_value_delta':liquidity,'strategic_value_delta':strategic,'break_glass_delta':0,'roster_interaction_value_delta':0},'net_title_equity_swing_against_focus':0},
    }


def main():
    m=load()
    current=row(368.52,23.95,.224,.032,.029,dynasty=-891,liquidity=-871.27,strategic=61.69,accept='MEDIUM')

    # Exact discovered pathology: a much higher composite score cannot rescue an
    # option that is worse on every competitive outcome and overall franchise impact.
    bad=row(1471.40,6.65,.144,.013,.024,dynasty=644.7,liquidity=-40.11,strategic=-174.48,accept='VERY_LOW')
    comp=m.compare(bad,current)
    assert comp['post_sim_score_delta_vs_current_offer']>1000,comp
    assert comp['post_sim_score_role']=='DIAGNOSTIC_ONLY_NOT_CATEGORICAL_DECISION_RULE',comp
    assert comp['competitive_relation_vs_current_offer']=='DOMINATED_BY_CURRENT_OFFER',comp
    assert comp['decision_relation_vs_current_offer']=='DOMINATED_BY_CURRENT_OFFER',comp
    assert comp['verdict_vs_current_offer']=='WORSE',comp
    assert comp['unsupported_numeric_score_cutoff_used'] is False,comp

    # Acceptance fit is Behavioral Intelligence only. LOW fit must not change a
    # genuinely better trade into a worse/mixed valuation.
    low_fit=row(1800,40,.45,.06,.05,dynasty=-300,liquidity=-300,strategic=150,accept='LOW')
    low_comp=m.compare(low_fit,current)
    assert low_comp['verdict_vs_current_offer']=='BETTER',low_comp
    low_fit['comparison_to_current_offer']=low_comp
    report={'current_offer_evaluation':current,'suggested_counteroffers':[],'market_sweep_alternatives':[low_fit]}
    action,basis=m.recompute_action(report,'ACCEPT_NOW')
    assert action=='SHOP_BEFORE_ACCEPTING',(action,basis)
    assert basis=='BETTER_MARKET_ALTERNATIVE_EXISTS_FEASIBILITY_REPORTED_SEPARATELY',(action,basis)

    # Conflicting dimensions are MIXED without any magnitude cliff. Better football
    # outcomes but worse overall franchise impact are a tradeoff, not categorically better.
    mixed=row(5000,40,.45,.06,.05,dynasty=1500,liquidity=900,strategic=-100,accept='HIGH')
    mixed_comp=m.compare(mixed,current)
    assert mixed_comp['competitive_relation_vs_current_offer']=='DOMINATES_CURRENT_OFFER',mixed_comp
    assert mixed_comp['decision_relation_vs_current_offer']=='TRADEOFF_VS_CURRENT_OFFER',mixed_comp
    assert mixed_comp['verdict_vs_current_offer']=='MIXED',mixed_comp

    # A genuinely dominant option remains BETTER independent of score magnitude.
    tiny_score_edge=row(368.53,30,.30,.04,.035,dynasty=-850,liquidity=-850,strategic=70,accept='MEDIUM')
    tiny_comp=m.compare(tiny_score_edge,current)
    assert 0 < tiny_comp['post_sim_score_delta_vs_current_offer'] < 1,tiny_comp
    assert tiny_comp['verdict_vs_current_offer']=='BETTER',tiny_comp

    # Continuous focal utility is authoritative for an offer already in hand;
    # counterparty modeled utility cannot veto our acceptance decision.
    clipped=dict(current)
    clipped['focal_current_state_beneficial']=False
    clipped['state_aware_score_components']={'future':-9999,'current':-9999}
    clipped['buyer_rationality']={'current_state_viable':False,'buyer_decision_utility_score':-9999}
    assert m.current_offer_focally_acceptable(clipped) is True
    action,basis=m.recompute_action(
        {'current_offer_evaluation':clipped,'suggested_counteroffers':[],'market_sweep_alternatives':[]},
        'DECLINE',
    )
    assert action=='ACCEPT_NOW',(action,basis)

    # Pick-only price changes around the same player swap cannot change
    # current-season football outcomes. Separate quick simulations may differ by
    # Monte Carlo noise; normalize them to the confirmed current-offer football
    # state before judging the economic concession.
    current_eq=row(900,30,.30,.04,.03,dynasty=-900,liquidity=100,strategic=-30,accept='MEDIUM')
    current_eq['outgoing_assets']=['player:7611','pick:2027:R2:orig5']
    current_eq['return_assets']=['player:12481']
    current_eq['simulation']['roster_resolution']={'focus':{'required_cuts':0,'selected_cuts':[]},'buyer':{'required_cuts':0,'selected_cuts':[]}}
    counter_eq=row(100,8,.10,.01,-.02,dynasty=-500,liquidity=100,strategic=180,accept='LOW')
    counter_eq['outgoing_assets']=['player:7611','pick:2028:R2:orig1','pick:2029:R3:orig1']
    counter_eq['return_assets']=['player:12481']
    counter_eq['simulation']['roster_resolution']={'focus':{'required_cuts':0,'selected_cuts':[]},'buyer':{'required_cuts':0,'selected_cuts':[]}}
    assert m.normalize_equivalent_competitive_outcomes(counter_eq,current_eq) is True
    assert counter_eq['simulation']['focus_delta']==current_eq['simulation']['focus_delta']
    eq_comp=m.compare(counter_eq,current_eq)
    assert eq_comp['competitive_relation_vs_current_offer']=='EQUIVALENT_TO_CURRENT_OFFER',eq_comp
    assert eq_comp['verdict_vs_current_offer']=='BETTER',eq_comp
    assert counter_eq['simulation']['competitive_outcomes_reused_from_equivalent_player_transaction'] is True

    print({
      'status':'PASS',
      'unsupported_750_threshold_removed':True,
      'dominated_high_score_verdict':'WORSE',
      'low_fit_can_remain_analytically_better':True,
      'acceptance_fit_separate_from_trade_quality':True,
      'mixed_tradeoffs_preserved':True,
      'tiny_score_edge_can_be_better_when_decision_outputs_dominate':True,
      'continuous_state_cliff_removed':True,
    })


if __name__=='__main__':main()
