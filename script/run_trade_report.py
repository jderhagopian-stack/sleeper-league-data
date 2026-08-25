#!/usr/bin/env python3
"""Run FSFFL trade analysis and emit JSON + plain-English PDF + short answer.

Pipeline 1.13 adds adaptive deep confirmation. A fast screen is used for normal
queries; if the screen is internally contradictory or the decision boundary is
close, the entire trade analysis is automatically re-run at higher simulation
depth and only the confirmed output is rendered/delivered.
"""
from __future__ import annotations
import argparse,json,subprocess,sys
from pathlib import Path

MARKET_SWEEP=Path('script/run_trade_market_sweep_v28.py')
PDF_RENDERER=Path('script/render_trade_decision_report_v16.py')
MODEL_VERSION='FSFFL-Trade-Query-Pipeline-1.13'
EXPECTED_ANALYSIS_MODEL='FSFFL-Counter-Market-Sweep-1.22'
REPORT_VERSION='FSFFL-Trade-Decision-Report-1.6'
DEFAULT_ADAPTIVE_CONFIRM_SIMS=1000

def run(cmd):subprocess.run(cmd,check=True)
def sf(v,d=0.0):
    try:return float(v)
    except:return d

def option_rows(report):
    return list(report.get('suggested_counteroffers') or [])+list(report.get('market_sweep_alternatives') or [])

def sensitivity_reasons(report):
    """Return reasons a quick Monte Carlo screen should be deeply confirmed."""
    reasons=[];cur=report.get('current_offer_evaluation') or {};sim=cur.get('simulation') or {};d=sim.get('focus_delta') or {}
    wins=sf(d.get('expected_wins'));pf=sf(d.get('expected_points_for'));play=sf(d.get('playoff_probability'));champ=sf(d.get('championship_probability'))
    # Material internal direction conflict: e.g. wins/PF/playoffs down while title odds rise.
    if abs(champ)>=.02 and abs(wins)>=.15 and champ*wins<0:
        reasons.append('championship_probability_conflicts_with_expected_wins')
    if abs(champ)>=.02 and abs(play)>=.03 and champ*play<0:
        reasons.append('championship_probability_conflicts_with_playoff_probability')
    if abs(champ)>=.02 and abs(pf)>=10 and champ*pf<0:
        reasons.append('championship_probability_conflicts_with_expected_points')

    # If a contender is near the hard title-equity guardrail, sampling noise can flip viability.
    cap=sf((report.get('policy') or {}).get('contender_title_loss_cap'),-1)
    if cap>0 and champ<0 and abs(abs(champ)-cap)<=.0125:
        reasons.append('current_offer_near_title_equity_guardrail')

    rows=option_rows(report)
    deltas=sorted([sf((x.get('comparison_to_current_offer') or {}).get('post_sim_score_delta_vs_current_offer')) for x in rows],reverse=True)
    # The production accept-vs-shop boundary is roughly +750 state-aware points.
    if deltas and abs(deltas[0]-750)<=600:
        reasons.append('best_option_near_accept_vs_shop_boundary')
    scores=sorted([sf(x.get('post_sim_score')) for x in rows],reverse=True)
    if len(scores)>=2 and abs(scores[0]-scores[1])<=600:
        reasons.append('top_recommendations_near_tie')
    return sorted(set(reasons))

def summary(report):
    action=str(report.get('recommended_next_action') or 'REVIEW');cur=report.get('current_offer_evaluation') or {};sim=cur.get('simulation') or {};d=sim.get('focus_delta') or {};st=sim.get('strategic') or {};cs=report.get('suggested_counteroffers') or [];ms=report.get('market_sweep_alternatives') or []
    label={'ACCEPT_NOW':'ACCEPT','COUNTER_CURRENT_OFFEROR':'COUNTER','SHOP_BEFORE_ACCEPTING':'SHOP BEFORE ACCEPTING','DECLINE':'DECLINE'}.get(action,action.replace('_',' '))
    short=f"{label}. Current-offer impact: {float(d.get('expected_wins') or 0):+.2f} expected wins, {float(d.get('championship_probability') or 0)*100:+.1f} pts championship probability, {float(st.get('strategic_value_delta') or 0):+,.0f} overall franchise impact."
    ac=(report.get('simulation') or {}).get('adaptive_confirmation') or {}
    if ac.get('triggered'):short+=f" Confirmed at {int(ac.get('confirmation_sims') or 0):,} simulations after an adaptive uncertainty check."
    short+=f" {len(cs)} suggested counteroffer{'s' if len(cs)!=1 else ''}; {len(ms)} market alternative{'s' if len(ms)!=1 else ''}."
    return short

def market_cmd(a,jp,sims):
    return [sys.executable,str(MARKET_SWEEP),'--scenario',a.scenario,'--quick-sims',str(sims),'--confirm-sims','0','--search-depth',str(a.search_depth),'--seed',str(a.seed),'--output',str(jp)]

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--scenario',required=True);ap.add_argument('--out-dir',required=True);ap.add_argument('--basename',default='trade-decision-report');ap.add_argument('--quick-sims',type=int,default=200);ap.add_argument('--confirm-sims',type=int,default=0);ap.add_argument('--adaptive-confirm-sims',type=int,default=DEFAULT_ADAPTIVE_CONFIRM_SIMS);ap.add_argument('--disable-adaptive-confirmation',action='store_true');ap.add_argument('--search-depth',type=int,default=60);ap.add_argument('--seed',type=int,default=20260821);a=ap.parse_args()
    out=Path(a.out_dir);out.mkdir(parents=True,exist_ok=True);jp=out/f'{a.basename}.json';pp=out/f'{a.basename}.pdf';sp=out/f'{a.basename}-summary.json'

    # Stage 1: fast screen.
    run(market_cmd(a,jp,a.quick_sims));r=json.loads(jp.read_text(encoding='utf-8'))
    if r.get('model_version')!=EXPECTED_ANALYSIS_MODEL:raise RuntimeError(f"Trade report pipeline expected {EXPECTED_ANALYSIS_MODEL}, got {r.get('model_version')}")
    reasons=[] if a.disable_adaptive_confirmation else sensitivity_reasons(r)
    deep_target=max(a.adaptive_confirm_sims,a.confirm_sims)
    triggered=bool(reasons and deep_target>=100 and deep_target>a.quick_sims)

    # Stage 2: only ambiguous/sensitive decisions pay the deep-simulation cost.
    if triggered:
        run(market_cmd(a,jp,deep_target));r=json.loads(jp.read_text(encoding='utf-8'))
        if r.get('model_version')!=EXPECTED_ANALYSIS_MODEL:raise RuntimeError(f"Trade report pipeline expected {EXPECTED_ANALYSIS_MODEL}, got {r.get('model_version')}")

    simmeta=r.setdefault('simulation',{})
    simmeta['adaptive_confirmation']={'enabled':not a.disable_adaptive_confirmation,'triggered':triggered,'screening_sims':a.quick_sims,'confirmation_sims':deep_target if triggered else 0,'trigger_reasons':reasons,'final_metrics_source':'deep_confirmation_rerun' if triggered else 'quick_screen'}
    r.setdefault('policy',{}).update({'adaptive_deep_confirmation_enabled':not a.disable_adaptive_confirmation,'adaptive_confirmation_reruns_full_trade_frontier':True,'contradictory_quick_sim_signals_trigger_confirmation':True,'close_decision_boundaries_trigger_confirmation':True,'final_report_uses_confirmed_metrics_when_triggered':True})
    jp.write_text(json.dumps(r,indent=2,sort_keys=True),encoding='utf-8')

    run([sys.executable,str(PDF_RENDERER),'--input',str(jp),'--output',str(pp)])
    payload={'pipeline_model_version':MODEL_VERSION,'analysis_model_version':r.get('model_version'),'report_model_version':REPORT_VERSION,'recommended_next_action':r.get('recommended_next_action'),'suggested_counteroffer_count':len(r.get('suggested_counteroffers') or []),'market_sweep_alternative_count':len(r.get('market_sweep_alternatives') or []),'adaptive_confirmation':simmeta.get('adaptive_confirmation'),'short_answer':summary(r),'json_report':str(jp),'pdf_report':str(pp),'canonical_model_entry_point':str(MARKET_SWEEP),'delivery_policy':'Always return the short answer and attach/share the plain-English explanatory PDF report for a trade query.'}
    sp.write_text(json.dumps(payload,indent=2),encoding='utf-8');print(json.dumps(payload,indent=2))
if __name__=='__main__':main()
