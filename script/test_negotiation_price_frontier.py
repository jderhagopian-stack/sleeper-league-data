#!/usr/bin/env python3
"""Fast regression for governed Trade Decision price-frontier semantics."""
from __future__ import annotations
import copy, importlib.util
from pathlib import Path

P=Path(__file__).resolve().parent/'trade_decision'/'negotiation_frontier.py'
spec=importlib.util.spec_from_file_location('frontier_test',P)
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

def row(desc,target,seller,price,focal,seller_u,fit='LOW'):
    return {
        'channel':'TRADE','description':desc,'seller_user_id':seller,'seller_team':'Seller',
        'target':{'asset_id':target,'name':target},
        'outgoing':[{'asset_id':f'asset:{price}','name':f'Package {price}','market_dynasty':price}],
        'team_improvement_score':focal,
        'seller_strategic_utility_precomputed':seller_u,
        'acceptance_fit':fit,
    }

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

board=mod.build(gibbs+bijan)
assert board['authority']=='Trade Decision'
assert len(board['target_price_frontiers'])==2
assert board['best_price_overlap']['target']['asset_id']=='player:gibbs'
assert board['policy']['price_frontier_uses_discrete_evaluated_packages'] is True
print('Trade Decision discrete price-frontier regression passed')
