#!/usr/bin/env python3
"""Run FSFFL trade analysis and always emit JSON + one-page PDF + short answer.

Manager-facing entry point. Analysis uses Market Sweep 1.20 with Behavioral
Intelligence 3.0 context-normalized positional behavior, BI2 persistent/state
traits, dynamic competitive-state utility, bilateral realism, and league-
realistic multi-asset search, then delegates presentation to the standardized
PDF renderer.
"""
from __future__ import annotations
import argparse,json,subprocess,sys
from pathlib import Path
MARKET_SWEEP=Path('script/run_trade_market_sweep_v26.py')
PDF_RENDERER=Path('script/render_trade_decision_report.py')
MODEL_VERSION='FSFFL-Trade-Query-Pipeline-1.7'
EXPECTED_ANALYSIS_MODEL='FSFFL-Counter-Market-Sweep-1.20'
def run(cmd):subprocess.run(cmd,check=True)
def summary(report):
    action=str(report.get('recommended_next_action') or 'REVIEW');current=report.get('current_offer_evaluation') or {};sim=current.get('simulation') or {};delta=sim.get('focus_delta') or {};top=report.get('top_5_alternatives') or []
    label={'ACCEPT_NOW':'ACCEPT','COUNTER_CURRENT_OFFEROR':'COUNTER','SHOP_BEFORE_ACCEPTING':'SHOP BEFORE ACCEPTING','DECLINE':'DECLINE'}.get(action,action.replace('_',' '))
    short=f"{label}. Current-offer impact: {float(delta.get('expected_wins') or 0):+.2f} expected wins, {float(delta.get('championship_probability') or 0)*100:+.0f} pts championship probability."
    if top:
        b=top[0];short+=f" Best modeled negotiation path: {b.get('buyer_team')} - receive {', '.join(b.get('return_asset_names') or [])}."
    return short
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--scenario',required=True);ap.add_argument('--out-dir',required=True);ap.add_argument('--basename',default='trade-decision-report');ap.add_argument('--quick-sims',type=int,default=100);ap.add_argument('--confirm-sims',type=int,default=0);ap.add_argument('--search-depth',type=int,default=60);ap.add_argument('--seed',type=int,default=20260821);a=ap.parse_args()
    out=Path(a.out_dir);out.mkdir(parents=True,exist_ok=True);jp=out/f'{a.basename}.json';pp=out/f'{a.basename}.pdf';sp=out/f'{a.basename}-summary.json'
    run([sys.executable,str(MARKET_SWEEP),'--scenario',a.scenario,'--quick-sims',str(a.quick_sims),'--confirm-sims',str(a.confirm_sims),'--search-depth',str(a.search_depth),'--seed',str(a.seed),'--output',str(jp)])
    run([sys.executable,str(PDF_RENDERER),'--input',str(jp),'--output',str(pp)])
    r=json.loads(jp.read_text(encoding='utf-8'))
    if r.get('model_version')!=EXPECTED_ANALYSIS_MODEL:raise RuntimeError(f"Trade report pipeline expected {EXPECTED_ANALYSIS_MODEL}, got {r.get('model_version')}")
    payload={'pipeline_model_version':MODEL_VERSION,'analysis_model_version':r.get('model_version'),'recommended_next_action':r.get('recommended_next_action'),'short_answer':summary(r),'json_report':str(jp),'pdf_report':str(pp),'canonical_model_entry_point':str(MARKET_SWEEP),'delivery_policy':'Always return the short answer and attach/share the PDF report for a trade query.'}
    sp.write_text(json.dumps(payload,indent=2),encoding='utf-8');print(json.dumps(payload,indent=2))
if __name__=='__main__':main()
