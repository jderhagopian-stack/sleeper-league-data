#!/usr/bin/env python3
"""Fast regression for governed Trade Decision price-frontier semantics."""
from __future__ import annotations
import copy, importlib.util
from pathlib import Path

P=Path(__file__).resolve().parent/'trade_decision'/'negotiation_frontier.py'
spec=importlib.util.spec_from_file_location('frontier_test',P)
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

def row(desc,target,seller,price,focal,seller_u,fit='LOW',shared_u=None):
    out={
        'channel':'TRADE','description':desc,'seller_user_id':seller,'seller_team':'Seller',
        'target':{'asset_id':target,'name':target},
        'outgoing':[{'asset_id':f'asset:{price}','name':f'Package {price}','market_dynasty':price}],
        'team_improvement_score':focal,
        'seller_strategic_utility_precomputed':seller_u,
        'acceptance_fit':fit,
    }
    if shared_u is not None:
        out['counterparty_shared_decision_utility_score']=shared_u
    return out

gibbs=[
    row('cheap','player:gibbs','s1',100,100,-20,'HIGH'),
    row('clearing','player:gibbs','s1',200,60,5,'LOW'),
    row('overpay','player:gibbs','s1',300,-10,30,'HIGH'),
]
f=mod.build_target_price_frontier(gibbs)
assert f['status']=='ACTIONABLE_PRICE_OVERLAP'
assert f['price_overlap_exists'] is True
assert f['seller_clearing_floor']['package_market_value_coordinate']==200
assert f['opening_package']['package_market_value_coordinate']==200
assert f['rational_focal_ceiling']['package_market_value_coordinate']==200
assert len(f['mutually_beneficial_deal_zone'])==1
assert f['policy']['no_arbitrary_elite_player_premium'] is True
assert f['policy']['behavioral_fit_does_not_change_price_or_utility'] is True
assert f['policy']['production_counterparty_utility_uses_same_shared_decision_utility_as_focal'] is True

# Full counterparty Shared Decision Utility must outrank the old GM2.2
# seller strategic heuristic when both are present.
parity=[
    row('legacy-false-positive','player:parity','s3',100,50,0.5,'HIGH',shared_u=-10),
    row('shared-clearing','player:parity','s3',200,25,-0.5,'LOW',shared_u=5),
]
p=mod.build_target_price_frontier(parity)
assert p['seller_clearing_floor']['package_market_value_coordinate']==200
assert p['seller_clearing_floor']['counterparty_shared_utility']==5
assert p['seller_clearing_floor']['counterparty_utility_source']=='shared_decision_utility'

# Descriptive behavior evidence cannot move the economic frontier.
changed=copy.deepcopy(gibbs)
for x in changed:
    x['acceptance_fit']='VERY_LOW' if x['acceptance_fit']=='HIGH' else 'HIGH'
f2=mod.build_target_price_frontier(changed)
assert f2['seller_clearing_floor']['package_market_value_coordinate']==200
assert f2['rational_focal_ceiling']['package_market_value_coordinate']==200
assert f2['price_overlap_exists'] is True

bijan=[
    row('our-zone','player:bijan','s2',150,50,-30),
    row('seller-zone','player:bijan','s2',350,-20,10),
]
b=mod.build_target_price_frontier(bijan)
assert b['status']=='NO_PRICE_OVERLAP'
assert b['price_overlap_exists'] is False
assert b['seller_clearing_floor']['package_market_value_coordinate']==350
assert b['rational_focal_ceiling']['package_market_value_coordinate']==150
assert b['near_frontier_evidence']['watchlist_eligible'] is True
assert b['near_frontier_evidence']['counterparty_utility_shortfall_at_best_focal_positive_package']==30
assert b['near_frontier_evidence']['focal_utility_shortfall_at_best_counterparty_viable_package']==20
assert b['near_frontier_evidence']['market_coordinate_gap_between_focal_ceiling_and_seller_floor']==200

closer=[
    row('close-focal','player:close','s4',180,35,-2),
    row('close-seller','player:close','s4',200,-1,1),
]
close=mod.build_target_price_frontier(closer)
assert close['price_overlap_exists'] is False
assert close['near_frontier_evidence']['watchlist_eligible'] is True
assert close['near_frontier_evidence']['counterparty_utility_shortfall_at_best_focal_positive_package']==2
assert close['near_frontier_evidence']['focal_utility_shortfall_at_best_counterparty_viable_package']==1

board=mod.build(gibbs+bijan+closer)
assert board['authority']=='Trade Decision'
assert len(board['target_price_frontiers'])==3
assert board['best_price_overlap']['target']['asset_id']=='player:gibbs'
assert board['best_near_frontier_target']['target']['asset_id']=='player:close'
assert board['near_frontier_watchlist'][0]['target']['asset_id']=='player:close'
assert board['policy']['near_frontier_watchlist_uses_no_fixed_utility_cutoff'] is True
assert board['policy']['near_frontier_watchlist_is_negotiation_context_not_actionable_trade_authority'] is True
assert board['policy']['price_frontier_uses_discrete_evaluated_packages'] is True
print('Trade Decision discrete price-frontier regression passed')
