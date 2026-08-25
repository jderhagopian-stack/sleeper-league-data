#!/usr/bin/env python3
"""Trade Decision Report 1.7 — explicit post-trade roster/cut impact."""
from __future__ import annotations
import argparse,importlib.util,json
from pathlib import Path

BASE=Path(__file__).resolve().parent/'render_trade_decision_report_v16.py'
MODEL_VERSION='FSFFL-Trade-Decision-Report-1.7'

def load():
    s=importlib.util.spec_from_file_location('trade_report_v16_base',BASE);m=importlib.util.module_from_spec(s);assert s.loader;s.loader.exec_module(m);return m
b=load();sf=b.sf;clean=b.clean;names=b.names

def resolution_parts(row):
    sim=row.get('simulation') or {};res=sim.get('roster_resolution') or {};buyer=str(row.get('buyer_user_id') or '')
    focal=[];buyer_rows=[]
    for uid,x in res.items():
        if int(x.get('required_cuts') or 0)<=0:continue
        (buyer_rows if str(uid)==buyer else focal).append(x)
    return focal,buyer_rows

def cut_names(x):
    return ', '.join(clean(c.get('name')) for c in (x.get('selected_cuts') or [])) or 'none'

def roster_note(row):
    focal,buyer_rows=resolution_parts(row);parts=[]
    for x in focal:
        n=int(x.get('required_cuts') or 0);parts.append(f"<b>Roster impact:</b> requires {n} cut{'s' if n!=1 else ''}: {cut_names(x)}. Forced cuts remove {sf(x.get('cut_market_dynasty_value')):,.0f} dynasty value ({sf(x.get('cut_base_franchise_value')):,.0f} franchise value) from the effective return.")
    for x in buyer_rows:
        n=int(x.get('required_cuts') or 0);parts.append(f"Other owner also needs {n} cut{'s' if n!=1 else ''}: {cut_names(x)}, which is included in acceptance-fit analysis.")
    return ' '.join(parts)

def counter_text(row,i):
    base=b.counter_text(row,i);rn=roster_note(row)
    return base+(f' <font color="#5F6B76">{rn}</font>' if rn else ' <font color="#5F6B76">Roster impact: no forced active-roster cut required.</font>')

def market_text(row,i):
    base=b.market_text(row,i);rn=roster_note(row)
    return base+(f' <font color="#5F6B76">{rn}</font>' if rn else ' <font color="#5F6B76">Roster impact: no forced active-roster cut required.</font>')

def narrative(r,cur):
    text=b.narrative(r,cur);rn=roster_note(cur)
    if rn:text+=' '+rn
    else:text+=' Roster check: the current offer does not require an active-roster cut.'
    return text

def render(r,out):
    b.MODEL_VERSION=MODEL_VERSION
    b.counter_text=counter_text
    b.market_text=market_text
    b.narrative=narrative
    b.render(r,out)

def main():
    a=argparse.ArgumentParser();a.add_argument('--input',required=True);a.add_argument('--output',required=True);x=a.parse_args();r=json.loads(Path(x.input).read_text());render(r,Path(x.output));print(json.dumps({'renderer_model_version':MODEL_VERSION,'source_model_version':r.get('model_version'),'pdf':x.output},indent=2))
if __name__=='__main__':main()
