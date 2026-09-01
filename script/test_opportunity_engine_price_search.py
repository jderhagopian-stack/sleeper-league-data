#!/usr/bin/env python3
"""Regression coverage for Opportunity Engine progressive price discovery."""
from __future__ import annotations
import importlib.util
from pathlib import Path

SCRIPT=Path(__file__).resolve().parent

def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod

lab=load(SCRIPT/'run_team_improvement_lab_v16.py','oe_price_search_lab')
oe=load(SCRIPT/'opportunity_engine'/'application.py','oe_generated_semantics')

class FakeBase:
    @staticmethod
    def sf(x,default=0.0):
        try:return float(x)
        except (TypeError,ValueError):return default
    @staticmethod
    def team_doc(uid,key):
        assert key=='trade_opportunities'
        return {'opportunities':[{
            'target_asset_id':'player:T','seller_user_id':'seller','seller_team':'Seller',
            'focal_value':100.0,'market_dynasty':100.0,'market_redraft':100.0,
            'focal_position_need':1.0,'seller_motivation_score':0.5,
            'best_candidate_packages':[{
                'focal_outgoing_asset_ids':['pick:A'],'decision_score':10.0,
                'seller_strategic_utility':-5.0,'focal_strategic_utility':10.0,
                'acceptance_fit_score':0.9,'recommendation_band':'seller_underpaid',
                'package_market_value_coordinate':10.0,
            }],
            'price_frontier_candidate_packages':[
                {'focal_outgoing_asset_ids':['pick:A'],'decision_score':10.0,'seller_strategic_utility':-5.0,'focal_strategic_utility':10.0,'acceptance_fit_score':0.9,'recommendation_band':'seller_underpaid','package_market_value_coordinate':10.0},
                {'focal_outgoing_asset_ids':['pick:B'],'decision_score':8.0,'seller_strategic_utility':-1.0,'focal_strategic_utility':6.0,'acceptance_fit_score':0.7,'recommendation_band':'seller_underpaid','package_market_value_coordinate':20.0},
                {'focal_outgoing_asset_ids':['pick:C'],'decision_score':5.0,'seller_strategic_utility':-0.5,'focal_strategic_utility':3.0,'acceptance_fit_score':0.6,'recommendation_band':'seller_underpaid','package_market_value_coordinate':30.0},
                {'focal_outgoing_asset_ids':['pick:D'],'decision_score':-2.0,'seller_strategic_utility':4.0,'focal_strategic_utility':-1.0,'acceptance_fit_score':0.6,'recommendation_band':'focal_overpay_or_bad_timing','package_market_value_coordinate':40.0},
            ],
        }]}

catalog={
    'player:T':{'asset_id':'player:T','asset_type':'player','player_id':'T','name':'Target','market_dynasty':100.0},
    'pick:A':{'asset_id':'pick:A','asset_type':'pick','name':'A','market_dynasty':10.0},
    'pick:B':{'asset_id':'pick:B','asset_type':'pick','name':'B','market_dynasty':20.0},
    'pick:C':{'asset_id':'pick:C','asset_type':'pick','name':'C','market_dynasty':30.0},
    'pick:D':{'asset_id':'pick:D','asset_type':'pick','name':'D','market_dynasty':40.0},
}
def fake_targeted(base,focus_uid,target_asset_ids,package_budget):
    curve=[dict(x) for x in FakeBase.team_doc('focus','trade_opportunities')['opportunities'][0]['price_frontier_candidate_packages']]
    for x in curve:
        if x['focal_outgoing_asset_ids']==['pick:C']:
            x['seller_strategic_utility']=1.0
            x['recommendation_band']='negotiation_candidate'
    return {'player:T':{'target_asset_id':'player:T','price_frontier_candidate_packages':curve}}
lab._targeted_price_curves=fake_targeted
rows=lab.trade_candidates(FakeBase(),'focus',catalog,limit=1,packages_per_target=4,frontier_targets=1,frontier_packages_per_target=4)
sigs={tuple(x['outgoing'][0]['asset_id'] for _ in [0]) for x in rows}
assert ('pick:C',) in sigs, 'seller-clearing transition must survive expansion'
assert ('pick:D',) in sigs, 'focal-zero transition must survive expansion'
assert any(x.get('price_frontier_search_candidate') for x in rows), 'expanded rows must be explicitly marked'
assert any(x.get('targeted_adaptive_price_discovery_used') for x in rows), 'selected targets must expose targeted adaptive discovery provenance'
assert all(x.get('targeted_adaptive_price_discovery_package_economics_owned_by_gm3') is True for x in rows if x.get('targeted_adaptive_price_discovery_used'))
assert all(x.get('price_frontier_search_is_computational_coverage_only') is True for x in rows if x.get('price_frontier_search_candidate'))

# Generated hypothetical trades may retain the raw Trade Decision action for
# audit, but user-facing routing must never pretend there is an offer in hand.
summary=oe._summarize_trade_decision({
    'model_version':'synthetic',
    'recommended_next_action':'ACCEPT_NOW',
    'current_offer_evaluation':{'simulation':{}},
})
assert summary['recommended_next_action']=='OPEN_NEGOTIATION'
assert summary['underlying_trade_decision_action']=='ACCEPT_NOW'
assert summary['generated_proposal_semantics_applied'] is True
assert summary['generated_proposal_willingness_observed'] is False

print('Opportunity Engine progressive price-search regression passed')

# Frontier sampling must keep local neighbors around economic zero-crossings,
# not merely endpoints/evenly spaced packages.
curve=[]
for i in range(10):
    curve.append({'focal_outgoing_asset_ids':[f'pick:{i}'],'package_market_value_coordinate':float(i+1),'seller_strategic_utility':float(i-5),'focal_strategic_utility':float(7-i)})
sample=lab._price_frontier_sample(curve,8,FakeBase.sf)
coords={int(x['package_market_value_coordinate']) for x in sample}
assert {5,6,7}.issubset(coords), 'seller zero-crossing neighborhood must receive dense coverage'
assert {7,8,9}.issubset(coords), 'focal zero-crossing neighborhood must receive dense coverage'


# Team Improvement v1.6 must preserve the league reference required by the
# Shared Decision Utility current-outcome block, and must expose the seller
# perspective from the same simulation.
class SimBase(FakeBase):
    @staticmethod
    def fast_reoptimize(*args,**kwargs): return ({},['focus','seller'])
    @staticmethod
    def team_index(payload): return {str(x['user_id']):x for x in payload['teams']}
    @staticmethod
    def delta(a,b): return float(b)-float(a)

class FakeDL:
    @staticmethod
    def apply_actions(rosters,actions): return (rosters,[])
    @staticmethod
    def touched_users(focus_uid,actions): return ['focus','seller']
    @staticmethod
    def simulate_from_lineups(*args,**kwargs):
        return {'teams':[
            {'user_id':'focus','expected_wins':8.0,'expected_points_for':1500.0,'playoff_probability':0.70,'bye_probability':0.20,'championship_probability':0.15},
            {'user_id':'seller','expected_wins':6.0,'expected_points_for':1400.0,'playoff_probability':0.50,'bye_probability':0.10,'championship_probability':0.08},
        ]}
    @staticmethod
    def strategic_summary(uid,actions):
        return {'objective_weights':{'current':0.4,'future':0.3,'liquidity':0.15,'resilience':0.15}}

class FakeRosterAware:
    @staticmethod
    def legalize_trade_rosters(*args,**kwargs): return (args[2],{},[])

baseline={'teams':[
    {'user_id':'focus','expected_wins':7.0,'expected_points_for':1400.0,'playoff_probability':0.60,'bye_probability':0.10,'championship_probability':0.10},
    {'user_id':'seller','expected_wins':7.0,'expected_points_for':1450.0,'playoff_probability':0.60,'bye_probability':0.15,'championship_probability':0.12},
]}
mi=(object(),{},[],[],{},'2026',{}, {})
sim=lab.simulate_actions_protect_add(
    SimBase(),FakeDL(),object(),FakeRosterAware(),mi,{},baseline,'focus',
    [{'type':'trade','from_user_id':'focus','to_user_id':'seller','players':[],'picks':['pick:A']}],
    100,1,
)
assert sim['league_reference']['team_count']==2
assert sim['league_reference']['expected_wins_mean']==7.0
assert sim['counterparty_user_id']=='seller'
assert sim['counterparty']['focus_delta']['expected_wins']==-1.0
assert sim['counterparty']['league_reference']==sim['league_reference']


# Final Shared Decision Utility de-duplication: market-derived player liquidity
# cannot receive a second incremental value on top of dynasty market value, and
# resilience must use depth insurance rather than star dependency/fragility.
stateaware=load(SCRIPT/'decision_lab_state_aware.py','stateaware_dedup_regression')
synthetic=[{
    'asset_type':'player',
    'base_franchise_value':1000.0,
    'liquidity_score':0.9,
    'liquidity_incremental_value_authorized':False,
    'replacement_resilience_score':0.9,
    'fragility_dependency_score':0.9,
    'depth_insurance_score':0.2,
    'final_shared_utility_resilience_basis':'depth_insurance_only',
}]
assert stateaware._weighted_total(synthetic,'liquidity')==0.0
assert stateaware._weighted_total(synthetic,'resilience')==200.0
