#!/usr/bin/env python3
"""Trade Decision Report 1.8 — roster-interaction context."""
from __future__ import annotations
import argparse,importlib.util,json
from pathlib import Path

BASE=Path(__file__).resolve().parent/'render_trade_decision_report_v17.py'
MODEL_VERSION='FSFFL-Trade-Decision-Report-1.8'

def load():
    s=importlib.util.spec_from_file_location('trade_report_v17_base',BASE);m=importlib.util.module_from_spec(s);assert s.loader;s.loader.exec_module(m);return m
b=load();sf=b.sf;clean=b.clean
BASE_COUNTER_TEXT=b.counter_text;BASE_MARKET_TEXT=b.market_text;BASE_NARRATIVE=b.narrative

def interaction_note(row):
    sim=row.get('simulation') or {};ri=sim.get('roster_interactions') or {};teams=ri.get('teams') or {};focus=str(ri.get('focus_user_id') or '');buyer=str(ri.get('buyer_user_id') or '')
    f=teams.get(focus) or {};o=teams.get(buyer) or {};fd=sf(f.get('roster_interaction_value_delta'));od=sf(o.get('roster_interaction_value_delta'))
    if abs(fd)<.5 and abs(od)<.5:return ''
    parts=[]
    if abs(fd)>=.5:parts.append(f'your roster-interaction value {fd:+,.0f}')
    if abs(od)>=.5:parts.append(f"other owner's roster-interaction value {od:+,.0f}")
    return '<b>Roster synergy:</b> '+', '.join(parts)+'. This is a bounded roster-specific adjustment; league-wide market value is unchanged.'

def counter_text(row,i):
    t=BASE_COUNTER_TEXT(row,i);n=interaction_note(row);return t+(f' <font color="#5F6B76">{n}</font>' if n else '')

def market_text(row,i):
    t=BASE_MARKET_TEXT(row,i);n=interaction_note(row);return t+(f' <font color="#5F6B76">{n}</font>' if n else '')

def narrative(r,cur):
    t=BASE_NARRATIVE(r,cur);n=interaction_note(cur);return t+(' '+n if n else '')

def render(r,out):
    b.MODEL_VERSION=MODEL_VERSION;b.counter_text=counter_text;b.market_text=market_text;b.narrative=narrative;b.render(r,out)

def main():
    a=argparse.ArgumentParser();a.add_argument('--input',required=True);a.add_argument('--output',required=True);x=a.parse_args();r=json.loads(Path(x.input).read_text());render(r,Path(x.output));print(json.dumps({'renderer_model_version':MODEL_VERSION,'source_model_version':r.get('model_version'),'pdf':x.output},indent=2))
if __name__=='__main__':main()
