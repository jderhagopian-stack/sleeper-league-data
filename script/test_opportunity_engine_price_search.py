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
                {'focal_outgoing_asset_ids':['pick:C'],'decision_score':5.0,'seller_strategic_utility':1.0,'focal_strategic_utility':3.0,'acceptance_fit_score':0.6,'recommendation_band':'negotiation_candidate','package_market_value_coordinate':30.0},
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
rows=lab.trade_candidates(FakeBase(),'focus',catalog,limit=1,packages_per_target=4,frontier_targets=1,frontier_packages_per_target=4)
sigs={tuple(x['outgoing'][0]['asset_id'] for _ in [0]) for x in rows}
assert ('pick:C',) in sigs, 'seller-clearing transition must survive expansion'
assert ('pick:D',) in sigs, 'focal-zero transition must survive expansion'
assert any(x.get('price_frontier_search_candidate') for x in rows), 'expanded rows must be explicitly marked'
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
