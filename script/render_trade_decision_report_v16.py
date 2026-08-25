#!/usr/bin/env python3
"""Trade Decision Report 1.6 — explicit why-better comparisons + confirmation note."""
from __future__ import annotations
import argparse,importlib.util,json
from pathlib import Path

BASE=Path(__file__).resolve().parent/'render_trade_decision_report_v15.py'
MODEL_VERSION='FSFFL-Trade-Decision-Report-1.6'

def load():
    s=importlib.util.spec_from_file_location('trade_report_v15_base',BASE);m=importlib.util.module_from_spec(s);assert s.loader;s.loader.exec_module(m);return m
b=load();sf=b.sf;clean=b.clean;names=b.names

def comparison_sentence(row):
    c=row.get('comparison_to_current_offer') or {};v=str(c.get('verdict_vs_current_offer') or 'MIXED');reason=clean(c.get('reason') or '')
    if not reason:
        delta=sf(c.get('post_sim_score_delta_vs_current_offer'))
        reason=f'{delta:+,.0f} state-aware score versus the current offer.'
    return f'<b>{v} vs current offer.</b> Why: {reason}'

def counter_text(row,i):
    sim=row.get('simulation') or {};d=sim.get('focus_delta') or {};st=sim.get('strategic') or {};validated=row.get('counter_validation_status')=='VALIDATED_ACCEPTANCE';conf=b.confidence_phrase(row.get('acceptance_likelihood')) if validated else 'Strategically sensible, but the model does not yet have enough evidence to rate acceptance confidence.'
    return f'<b>{i}. Send {names(row.get("outgoing_asset_names"))}; receive {names(row.get("return_asset_names"))}.</b> Impact on your team: {sf(d.get("expected_wins")):+.2f} expected wins, {sf(st.get("market_dynasty_delta")):+,.0f} dynasty value, and {sf(st.get("strategic_value_delta")):+,.0f} overall franchise impact. {comparison_sentence(row)} <font color="#5F6B76">{conf}</font>'

def market_text(row,i):
    sim=row.get('simulation') or {};d=sim.get('focus_delta') or {};st=sim.get('strategic') or {}
    return f'<b>{i}. {clean(row.get("buyer_team"))}</b> - send {names(row.get("outgoing_asset_names"))}; receive {names(row.get("return_asset_names"))}. Impact on your team: {sf(d.get("expected_wins")):+.2f} expected wins and {sf(st.get("strategic_value_delta")):+,.0f} overall franchise impact. {comparison_sentence(row)} <font color="#5F6B76">{b.confidence_phrase(row.get("acceptance_likelihood"))}.</font>'

def narrative(r,cur):
    text=b.narrative(r,cur)
    ac=(r.get('simulation') or {}).get('adaptive_confirmation') or {}
    if ac.get('triggered'):
        text += f" Final decision metrics shown here were automatically re-run at {int(ac.get('confirmation_sims') or 0):,} simulations because the {int(ac.get('screening_sims') or 0):,}-simulation screen showed a close or internally conflicting signal."
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
