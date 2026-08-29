#!/usr/bin/env python3
"""Regression audit for threshold-free option-vs-offer outcome consistency."""
from __future__ import annotations
import importlib.util
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
MOD=ROOT/'script'/'run_trade_market_sweep_v31.py'


def load():
    spec=importlib.util.spec_from_file_location('market_v31_regression',MOD)
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

    # Continuous focal utility is authoritative for retool/rebuild labels; the
    # old future-component cliffs must not reappear in the action fallback.
    clipped=dict(current)
    clipped['focal_current_state_beneficial']=False
    clipped['state_aware_score_components']={'future':-9999,'current':-9999}
    assert m.current_mutually_viable(clipped) is True

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
