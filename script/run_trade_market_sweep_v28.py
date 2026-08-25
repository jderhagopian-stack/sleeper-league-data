#!/usr/bin/env python3
"""FSFFL Counter & Market Sweep 1.22 — explicit option-vs-offer comparisons.

Runs the validated 1.21 engine unchanged, then ensures every suggested counter
and market-sweep alternative carries an explicit comparison to the current
offer. The comparison is presentation-ready but derived entirely from the
underlying state-aware simulation/strategic outputs.
"""
from __future__ import annotations
import importlib.util,json,sys
from pathlib import Path

SCRIPT=Path(__file__).resolve().parent
V27=SCRIPT/'run_trade_market_sweep_v27.py'
MODEL_VERSION='FSFFL-Counter-Market-Sweep-1.22'


def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);assert spec.loader;spec.loader.exec_module(m);return m

def sf(v,d=0.0):
    try:return float(v)
    except:return d

def out_path():
    if '--output' not in sys.argv:return None
    i=sys.argv.index('--output');return Path(sys.argv[i+1]) if i+1<len(sys.argv) else None

def metric(row,key):
    sim=row.get('simulation') or {};d=sim.get('focus_delta') or {};st=sim.get('strategic') or {}
    if key in d:return sf(d.get(key))
    if key=='net_title_equity_swing_against_focus':return sf(sim.get(key))
    return sf(st.get(key))

def compare(row,current):
    keys=('expected_wins','expected_points_for','playoff_probability','bye_probability','championship_probability','market_dynasty_delta','strategic_value_delta','liquidity_value_delta','break_glass_delta','net_title_equity_swing_against_focus')
    deltas={k:round(metric(row,k)-metric(current,k),5) for k in keys}
    score_delta=round(sf(row.get('post_sim_score'))-sf(current.get('post_sim_score')),2)
    if score_delta>750: verdict='BETTER'
    elif score_delta<-750: verdict='WORSE'
    else: verdict='MIXED'

    wins=deltas['expected_wins']; champ=deltas['championship_probability']; dyn=deltas['market_dynasty_delta']; liq=deltas['liquidity_value_delta']; overall=deltas['strategic_value_delta']; ext=deltas['net_title_equity_swing_against_focus']
    drivers=[]
    if abs(wins)>=0.10:drivers.append(f"{wins:+.2f} expected wins")
    if abs(champ)>=0.01:drivers.append(f"{champ*100:+.1f} pts championship probability")
    if abs(overall)>=250:drivers.append(f"{overall:+,.0f} franchise value")
    if abs(dyn)>=500:drivers.append(f"{dyn:+,.0f} dynasty value")
    if abs(liq)>=500:drivers.append(f"{liq:+,.0f} trade flexibility")
    if abs(ext)>=0.01:drivers.append(f"{-ext*100:+.1f} pts net contender externality")
    if not drivers:drivers.append(f"{score_delta:+,.0f} state-aware score")
    if verdict=='BETTER': lead='Higher state-aware utility than the current offer'
    elif verdict=='WORSE': lead='Lower state-aware utility than the current offer'
    else: lead='A mixed tradeoff versus the current offer'
    reason=lead+', driven by '+', '.join(drivers[:4])+'.'
    return {'verdict_vs_current_offer':verdict,'post_sim_score_delta_vs_current_offer':score_delta,'metric_deltas_vs_current_offer':deltas,'reason':reason,'comparison_basis':'state_aware_post_sim_score_plus_key_simulation_and_strategic_deltas'}

def main():
    v27=load(V27,'market_v27_for_122');v27.main();out=out_path()
    if not out or not out.exists():return
    r=json.loads(out.read_text(encoding='utf-8'));current=r.get('current_offer_evaluation') or {}
    for section in ('suggested_counteroffers','market_sweep_alternatives'):
        rows=r.get(section) or []
        for row in rows:
            row['comparison_to_current_offer']=compare(row,current)
            row['why_prefer_over_current_offer']=row['comparison_to_current_offer']['reason']
    r['model_version']=MODEL_VERSION
    r.setdefault('policy',{}).update({'every_recommended_option_compared_to_current_offer':True,'option_comparison_includes_explicit_verdict':True,'option_comparison_includes_reason':True,'option_comparison_uses_state_aware_post_sim_score':True})
    r.setdefault('simulation',{})['execution_path']=str((r.get('simulation') or {}).get('execution_path') or '')+'_plus_explicit_option_vs_offer_comparison'
    out.write_text(json.dumps(r,indent=2,sort_keys=True),encoding='utf-8')

if __name__=='__main__':main()
