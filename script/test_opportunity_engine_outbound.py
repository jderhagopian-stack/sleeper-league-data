#!/usr/bin/env python3
"""Regression coverage for governed outbound/future-value Opportunity Engine search."""
from __future__ import annotations
import importlib.util
from pathlib import Path

SCRIPT=Path(__file__).resolve().parent

def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod

lab=load(SCRIPT/'run_team_improvement_lab_v16.py','outbound_lab')
base_lab=load(SCRIPT/'run_team_improvement_lab.py','outbound_base_lab')
frontier=load(SCRIPT/'trade_decision'/'negotiation_frontier.py','outbound_frontier')
oe=load(SCRIPT/'opportunity_engine'/'application.py','outbound_oe')

class FakeBase:
    DATA=Path('/fake/data')
    @staticmethod
    def sf(x,default=0.0):
        try:return float(x)
        except (TypeError,ValueError):return default
    @staticmethod
    def load_json(path,default=None):
        path=str(path)
        if path.endswith('franchise_index.json'):
            return {'teams':[
                {'user_id':'focus','team_name':'Focus','paths':{}},
                {'user_id':'buyer','team_name':'Buyer','paths':{'trade_opportunities':'/fake/buyer.json'}},
            ]}
        if path=='/fake/buyer.json':
            return {
                'focal_user_id':'buyer',
                'opportunities':[{
                    'target_asset_id':'player:VET',
                    'seller_user_id':'focus',
                    'seller_team':'Focus',
                    'best_candidate_packages':[{
                        'focal_outgoing_asset_ids':['pick:1','player:YOUNG'],
                        'focal_strategic_utility':0.12,
                        'seller_strategic_utility':0.03,
                        'acceptance_fit_score':0.61,
                        'recommendation_band':'mutual_value_candidate',
                        'decision_score':0.14,
                    }],
                }],
            }
        return default

catalog={
    'player:VET':{
        'asset_id':'player:VET','asset_type':'player','player_id':'VET','name':'Veteran',
        'market_dynasty':5000.0,'market_redraft':7000.0,'owner_user_id':'focus',
    },
    'pick:1':{
        'asset_id':'pick:1','asset_type':'pick','name':'2028 1st',
        'market_dynasty':4000.0,'market_redraft':0.0,'owner_user_id':'buyer',
    },
    'player:YOUNG':{
        'asset_id':'player:YOUNG','asset_type':'player','player_id':'YOUNG','name':'Young Player',
        'market_dynasty':2500.0,'market_redraft':1500.0,'owner_user_id':'buyer',
    },
}

rows=lab._outbound_future_value_rows(FakeBase(),'focus',catalog,packages_per_target=3)
assert len(rows)==1
row=rows[0]
assert row['trade_direction']=='OUTBOUND_FUTURE_VALUE'
assert row['counterparty_user_id']=='buyer'
assert [x['asset_id'] for x in row['outgoing']]==['player:VET']
assert {x['asset_id'] for x in row['incoming']}=={'pick:1','player:YOUNG'}
assert row['package_market_dynasty_delta']==1500.0
assert row['source_inverted_from_counterparty_gm3_acquisition_search'] is True
assert row['outbound_search_creates_new_trade_value'] is False

actions=base_lab.trade_actions('focus',row)
assert actions[0]['from_user_id']=='focus' and actions[0]['to_user_id']=='buyer'
assert actions[0]['players']==['VET']
assert actions[1]['from_user_id']=='buyer' and actions[1]['to_user_id']=='focus'
assert actions[1]['players']==['YOUNG']
assert actions[1]['picks']==['pick:1']
assert base_lab.describe(row)=='Trade Veteran for 2028 1st + Young Player'

scored=dict(row)
scored.update({
    'channel':'TRADE',
    'team_improvement_score':100.0,
    'counterparty_shared_decision_utility_score':50.0,
    'description':'Trade Veteran for 2028 1st + Young Player',
})
classified=frontier.classify_trade(scored)
assert classified['negotiation_frontier']['bucket'] in {'ACTIONABLE_NEGOTIATION','NEGOTIATION_TARGET'}
# Outbound frontier coordinate must describe the requested return, not the
# fixed focal asset being shopped: 4000 + 2500 = 6500.
assert classified['negotiation_frontier']['package_market_value_coordinate']==6500.0

# Portfolio structural compatibility must understand the full incoming package.
other={
    'channel':'TRADE','seller_user_id':'other','target':catalog['player:YOUNG'],
    'outgoing':[catalog['pick:1']],
}
assert oe._compatible(row,other) is False
independent={
    'channel':'TRADE','seller_user_id':'other','target':{
        'asset_id':'player:Z','asset_type':'player','player_id':'Z','name':'Z'
    },
    'outgoing':[{
        'asset_id':'pick:2','asset_type':'pick','name':'2029 2nd'
    }],
}
assert oe._compatible(row,independent) is True

print('Opportunity Engine outbound future-value regression passed')
