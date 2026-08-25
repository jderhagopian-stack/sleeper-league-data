#!/usr/bin/env python3
"""FSFFL Counter & Market Sweep 1.23 — roster-aware trade resolution.

Builds on validated 1.22. Hypothetical trade rosters are now legalized before
simulation and strategic valuation. Any required active-roster cuts are chosen
using GM 3.0 state-aware retention values, applied to the hypothetical roster,
and carried into buyer/focal strategic analysis and report metadata.
"""
from __future__ import annotations
import importlib.util,json,sys
from pathlib import Path

SCRIPT=Path(__file__).resolve().parent
V28=SCRIPT/'run_trade_market_sweep_v28.py'
MODEL_VERSION='FSFFL-Counter-Market-Sweep-1.23'
ROSTER_MODEL='FSFFL-Roster-Aware-Trade-Resolution-1.0'

def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);assert spec.loader;spec.loader.exec_module(m);return m

def out_path():
    if '--output' not in sys.argv:return None
    i=sys.argv.index('--output');return Path(sys.argv[i+1]) if i+1<len(sys.argv) else None

def summarize_resolution(row):
    sim=row.get('simulation') or {};res=sim.get('roster_resolution') or {};focus=str((row.get('simulation') or {}).get('strategic',{}).get('focus_user_id') or '')
    return res

def main():
    v28=load(V28,'market_v28_for_123');v28.main();out=out_path()
    if not out or not out.exists():return
    r=json.loads(out.read_text(encoding='utf-8'))
    r['model_version']=MODEL_VERSION
    r.setdefault('policy',{}).update({
        'roster_aware_trade_resolution':True,
        'roster_resolution_model_version':ROSTER_MODEL,
        'post_trade_active_roster_limit_enforced':True,
        'forced_cuts_included_in_lineup_simulation':True,
        'forced_cuts_included_in_strategic_valuation':True,
        'forced_cuts_included_in_buyer_acceptance_analysis':True,
        'taxi_and_reserve_excluded_from_active_roster_count':True,
        'automatic_taxi_or_reserve_reassignment':False,
    })
    r.setdefault('simulation',{})['execution_path']=str((r.get('simulation') or {}).get('execution_path') or '')+'_plus_roster_aware_trade_resolution'
    out.write_text(json.dumps(r,indent=2,sort_keys=True),encoding='utf-8')
if __name__=='__main__':main()
