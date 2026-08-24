#!/usr/bin/env python3
"""FSFFL Counter & Market Sweep 1.19 — Behavioral Intelligence 2.0.

Extends 1.18 with a two-layer manager model:
- persistent behavioral traits across years/competitive states;
- state-conditioned variants from trades, drafts, waivers/FAAB, and drops.

Behavior remains secondary evidence. It cannot override current-state utility,
bilateral rationality, or the normal-recommendation focal-value gates.
"""
from __future__ import annotations
import importlib.util, json, sys
from pathlib import Path

SCRIPT=Path(__file__).resolve().parent
V24=SCRIPT/'run_trade_market_sweep_v24.py'; BI=SCRIPT/'behavioral_intelligence.py'
MODEL_VERSION='FSFFL-Counter-Market-Sweep-1.19'

def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def sf(x,d=0.0):
    try:return float(x)
    except (TypeError,ValueError):return d

def clamp(x,a,b):return max(a,min(b,x))

def output_path():
    if '--output' not in sys.argv:return None
    i=sys.argv.index('--output');return Path(sys.argv[i+1]) if i+1<len(sys.argv) else None

def install(v24,bi):
    original=v24.install_historical_state_conditioning
    def upgraded(v23,hist):
        idx=original(v23,hist)
        prior=v23.state_condition_behavior
        def state_condition_behavior(row,br):
            br=prior(row,br)
            uid=str(row.get('buyer_user_id') or '')
            state=str(br.get('buyer_state') or 'unknown')
            shape=v24.candidate_shape(row)
            signals={}; adj=0.0
            # Position acquisition affinities. Persistent and same-state evidence
            # are blended inside BI before entering this layer.
            recv_pos=shape.get('received_positions') or []
            if recv_pos:
                vals=[]
                for p in recv_pos:
                    name={'QB':'qb_accumulation','WR':'wr_affinity','RB':'rb_affinity','TE':'te_affinity'}.get(p)
                    if name:
                        t=bi.trait_score(uid,name,state); vals.append(sf(t.get('score'))); signals[f'{p}_affinity']=t
                if vals: adj += .035*(sum(vals)/len(vals))
            # Pick appetite: candidate net_pick_in is from buyer perspective.
            pick=bi.trait_score(uid,'draft_pick_accumulation',state); signals['draft_pick_accumulation']=pick
            adj += .030*sf(pick.get('score'))*clamp(sf(shape.get('net_pick_in'))/2.0,-1,1)
            # Complex-package comfort.
            large=bi.trait_score(uid,'large_package_tolerance',state);signals['large_package_tolerance']=large
            if int(shape.get('total_assets') or 0)>=4: adj += .020*sf(large.get('score'))
            # Youth preference against the players buyer receives.
            youth=bi.trait_score(uid,'youth_preference',state);signals['youth_preference']=youth
            recv_players=[x for x in shape.get('buyer_receives') or [] if not v24.is_pick(x)]
            if recv_players:
                avg_y=sum(bi.youth_score(str(x).replace('player:','')) for x in recv_players)/len(recv_players)
                adj += .025*sf(youth.get('score'))*((avg_y-.5)*2)
            adj=clamp(adj,-.075,.075)
            base=sf(br.get('heuristic_acceptance_fit_score'),.5)
            score=round(clamp(base+adj,0,1),4)
            ob=dict(br.get('owner_behavior') or {})
            ob['behavioral_intelligence_version']='FSFFL-Behavioral-Intelligence-2.0'
            ob['persistent_plus_state_conditioned_full_action_history']=True
            ob['full_action_sources']=['trades','drafts','waivers_free_agents','faab','drops_cuts']
            ob['behavioral_intelligence_adjustment']=round(adj,4)
            ob['behavioral_intelligence_signals']=signals
            ob['behavioral_intelligence_can_override_current_state_utility']=False
            br['owner_behavior']=ob;br['heuristic_acceptance_fit_score']=score;br['heuristic_acceptance_fit']=v24.band(score)
            br['acceptance_fit_basis']='current_state_utility_plus_historical_same_state_trade_behavior_plus_behavioral_intelligence_2_persistent_and_state_conditioned_full_action_history'
            return br
        v23.state_condition_behavior=state_condition_behavior
        return idx
    v24.install_historical_state_conditioning=upgraded

def main():
    v24=load(V24,'market_v24_for_119');bi=load(BI,'behavioral_intelligence_for_119');install(v24,bi);v24.MODEL_VERSION=MODEL_VERSION;v24.main()
    out=output_path()
    if out and out.exists():
        r=json.loads(out.read_text(encoding='utf-8'));r['model_version']=MODEL_VERSION
        r.setdefault('policy',{}).update({'behavioral_intelligence_version':'FSFFL-Behavioral-Intelligence-2.0','persistent_manager_traits_enabled':True,'state_conditioned_full_action_behavior_enabled':True,'behavioral_sources_include_trades_drafts_waivers_faab_drops':True,'behavioral_history_can_override_current_state_utility':False})
        r['behavioral_intelligence']={'model_version':'FSFFL-Behavioral-Intelligence-2.0','owner_count':len((bi.build().get('owners') or {})),'evidence_weights':bi.build().get('evidence_weights'),'persistent_plus_state_conditioned':True}
        r.setdefault('simulation',{})['execution_path']='GM3_state_aware_plus_behavioral_intelligence_2_full_action_history_plus_historical_state_at_trade_plus_bilateral_market_intelligence_plus_family_dedup_plus_multi_asset_search'
        out.write_text(json.dumps(r,indent=2,sort_keys=True),encoding='utf-8')
if __name__=='__main__':main()
