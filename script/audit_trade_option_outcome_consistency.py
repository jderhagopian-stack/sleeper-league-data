#!/usr/bin/env python3
"""Regression audit for option-vs-offer outcome consistency.

This audit deliberately recreates the failure discovered in the Skattebo trade
report: an alternative has a much higher composite post-simulation score while
being worse on every focal competitive outcome and carrying VERY_LOW acceptance.
It must remain visible as a tradeoff, but cannot be called BETTER or force a
shop/counter action when the current offer is mutually viable.
"""
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
      'post_sim_score':post,
      'acceptance_likelihood':accept,
      'championship_equity_constraint':'PASS',
      'focal_current_state_beneficial':True,
      'buyer_rationality':{'current_state_viable':True,'heuristic_acceptance_fit':accept},
      'simulation':{
        'focus_delta':{'expected_points_for':points,'expected_wins':wins,'playoff_probability':playoffs,'bye_probability':0,'championship_probability':title},
        'strategic':{'objective_state':'retool','market_dynasty_delta':dynasty,'liquidity_value_delta':liquidity,'strategic_value_delta':strategic,'break_glass_delta':0,'roster_interaction_value_delta':0},
        'net_title_equity_swing_against_focus':0,
      },
    }

def main():
    m=load()
    current=row(368.52,23.95,.224,.032,.029,dynasty=-891,liquidity=-871.27,strategic=61.69,accept='MEDIUM')
    # Mirrors the discovered direction and magnitude: +1102.88 composite score,
    # but -17.3 points, -0.08 wins, -1.9 playoff points, -0.5 title points.
    bad=row(1471.40,6.65,.144,.013,.024,dynasty=644.7,liquidity=-40.11,strategic=-174.48,accept='VERY_LOW')
    comp=m.compare(bad,current)
    assert comp['raw_score_only_verdict']=='BETTER',comp
    assert comp['competitive_relation_vs_current_offer']=='DOMINATED_BY_CURRENT_OFFER',comp
    assert comp['verdict_vs_current_offer']=='MIXED',comp
    assert comp['outcome_consistency_guard_applied'] is True,comp
    bad['comparison_to_current_offer']=comp
    assert m.actionable_better(bad) is False
    report={'current_offer_evaluation':current,'suggested_counteroffers':[],'market_sweep_alternatives':[bad]}
    action,basis=m.recompute_action(report,'SHOP_BEFORE_ACCEPTING')
    assert action=='ACCEPT_NOW',(action,basis)
    assert basis=='NO_ACTIONABLE_BETTER_OPTION_THAN_MUTUALLY_VIABLE_CURRENT_OFFER',(action,basis)

    # A genuinely dominant, executable option may still trigger shopping.
    good=row(1300,30,.35,.05,.04,dynasty=-500,liquidity=-500,strategic=200,accept='MEDIUM')
    good['comparison_to_current_offer']=m.compare(good,current)
    assert good['comparison_to_current_offer']['verdict_vs_current_offer']=='BETTER',good['comparison_to_current_offer']
    assert m.actionable_better(good) is True
    report['market_sweep_alternatives']=[good]
    action,basis=m.recompute_action(report,'ACCEPT_NOW')
    assert action=='SHOP_BEFORE_ACCEPTING',(action,basis)

    print({'status':'PASS','dominated_high_score_verdict':comp['verdict_vs_current_offer'],'dominated_longshot_actionable':False,'mutually_viable_current_action_without_better_option':'ACCEPT_NOW','genuinely_dominant_executable_option_can_trigger_shop':True})

if __name__=='__main__':main()
