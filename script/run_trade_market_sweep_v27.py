#!/usr/bin/env python3
"""FSFFL Counter & Market Sweep 1.21 — split same-partner counters from market alternatives.

Runs the validated 1.20/BI3 engine unchanged, then organizes its computed
candidate frontier into two explicit decision products:
- suggested_counteroffers: up to 2 same-partner constructions that are distinct
  from the incoming offer and beneficial to the focal team's current state;
- market_sweep_alternatives: up to 5 other-owner alternatives only.

The split never manufactures candidates. Missing slots stay empty.
"""
from __future__ import annotations
import importlib.util,json,sys
from pathlib import Path
SCRIPT=Path(__file__).resolve().parent
V26=SCRIPT/'run_trade_market_sweep_v26.py'
MODEL_VERSION='FSFFL-Counter-Market-Sweep-1.21'

def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);assert spec.loader;spec.loader.exec_module(m);return m

def sf(v,d=0.0):
    try:return float(v)
    except:return d

def out_path():
    if '--output' not in sys.argv:return None
    i=sys.argv.index('--output');return Path(sys.argv[i+1]) if i+1<len(sys.argv) else None

def key(r):return (str(r.get('buyer_user_id') or ''),tuple(sorted(map(str,r.get('outgoing_assets') or []))),tuple(sorted(map(str,r.get('return_assets') or []))))
def fam(r):return (str(r.get('buyer_user_id') or ''),tuple(sorted(map(str,r.get('outgoing_assets') or []))),tuple(sorted(x for x in map(str,r.get('return_assets') or []) if not x.startswith('pick:'))))
def focal_ok(r):
    if sf(r.get('post_sim_score'))<=0:return False
    state=str((((r.get('simulation') or {}).get('strategic') or {}).get('objective_state')) or r.get('focal_current_state') or '')
    comp=r.get('state_aware_score_components') or {}
    if state=='rebuild' and sf(comp.get('future'))<=0:return False
    if state=='retool' and sf(comp.get('future'))<=-250:return False
    if state in {'contender','elite_contender'} and r.get('championship_equity_constraint')=='FAIL':return False
    return True

def enrich_counter(r):
    x=dict(r);br=x.get('buyer_rationality') or {};accept=br.get('heuristic_acceptance_fit') or x.get('acceptance_likelihood');pl=str(x.get('plausibility') or 'UNRATED')
    x['counter_validation_status']='VALIDATED_ACCEPTANCE' if accept in {'HIGH','MEDIUM'} else 'STRUCTURALLY_PLAUSIBLE_ACCEPTANCE_UNVALIDATED'
    x['acceptance_likelihood']=accept
    x['counter_confidence_note']=(f'{accept} acceptance fit' if accept else f'{pl} structural plausibility; buyer acceptance not fully validated')
    x['report_role']='SUGGESTED_COUNTEROFFER'
    return x

def main():
    v26=load(V26,'market_v26_for_121');v26.main();out=out_path()
    if not out or not out.exists():return
    r=json.loads(out.read_text(encoding='utf-8'));current=r.get('current_offer_evaluation') or {};partner=str(r.get('current_offer_partner_user_id') or current.get('buyer_user_id') or '');current_key=key(current)
    counter_pool=[]
    for row in (r.get('same_partner_counteroffers') or [])+[r.get('best_same_partner') or {}]+(r.get('realistic_counter_alternatives') or [])+(r.get('top_5_alternatives') or []):
        if not row or str(row.get('buyer_user_id') or '')!=partner or key(row)==current_key or not focal_ok(row):continue
        counter_pool.append(row)
    counter_pool.sort(key=lambda x:(1 if ((x.get('buyer_rationality') or {}).get('heuristic_acceptance_fit') in {'HIGH','MEDIUM'}) else 0,sf((x.get('negotiation_ranking') or {}).get('score')),sf(x.get('post_sim_score'))),reverse=True)
    counters=[];seen=set()
    for row in counter_pool:
        f=fam(row)
        if f in seen:continue
        seen.add(f);counters.append(enrich_counter(row))
        if len(counters)==2:break
    market_pool=[]
    for row in (r.get('top_5_alternatives') or [])+(r.get('realistic_counter_alternatives') or []):
        if not row or str(row.get('buyer_user_id') or '')==partner or not focal_ok(row):continue
        market_pool.append(row)
    market_pool.sort(key=lambda x:(sf((x.get('negotiation_ranking') or {}).get('score')),sf(x.get('post_sim_score'))),reverse=True)
    market=[];seen=set()
    for row in market_pool:
        f=fam(row)
        if f in seen:continue
        seen.add(f);market.append(row)
        if len(market)==5:break
    r['model_version']=MODEL_VERSION
    r['suggested_counteroffers']=counters
    r['market_sweep_alternatives']=market
    r['counteroffer_count']=len(counters);r['market_sweep_alternative_count']=len(market)
    r.setdefault('candidate_counts',{}).update({'suggested_counteroffers':len(counters),'market_sweep_alternatives':len(market)})
    r.setdefault('policy',{}).update({'suggested_counteroffers_max':2,'suggested_counteroffers_same_partner_only':True,'suggested_counteroffers_never_padded':True,'market_sweep_max':5,'market_sweep_excludes_current_partner':True,'market_sweep_never_padded':True,'counter_and_market_pools_separate':True})
    r.setdefault('simulation',{})['execution_path']=str((r.get('simulation') or {}).get('execution_path') or '')+'_plus_counter_market_pool_split'
    out.write_text(json.dumps(r,indent=2,sort_keys=True),encoding='utf-8')
if __name__=='__main__':main()
