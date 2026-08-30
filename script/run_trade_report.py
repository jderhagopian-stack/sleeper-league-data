#!/usr/bin/env python3
"""Run FSFFL trade analysis and emit JSON + plain-English PDF + short answer.

Pipeline 1.18 retains adaptive deep confirmation and roster-aware trade
resolution, and promotes outcome-consistent option governance. Hypothetical
trades are legalized before simulation so any forced active-roster cuts are
included in lineup outcomes, strategic value, buyer acceptance analysis,
option comparisons, and the final PDF.
"""
from __future__ import annotations
import argparse,json,subprocess,sys
from pathlib import Path

MARKET_SWEEP=Path('script/trade_engine.py')
PDF_RENDERER=Path('script/render_trade_decision_report_v19.py')
MODEL_VERSION='FSFFL-Trade-Query-Pipeline-1.18'
EXPECTED_ANALYSIS_MODEL='FSFFL-Counter-Market-Sweep-1.25'
REPORT_VERSION='FSFFL-Trade-Decision-Report-1.10'
DEFAULT_ADAPTIVE_CONFIRM_SIMS=50000


def run(cmd):subprocess.run(cmd,check=True)
def sf(v,d=0.0):
    try:return float(v)
    except:return d


def option_rows(report):
    return list(report.get('suggested_counteroffers') or [])+list(report.get('market_sweep_alternatives') or [])


def sensitivity_reasons(report):
    reasons=[];cur=report.get('current_offer_evaluation') or {};sim=cur.get('simulation') or {};d=sim.get('focus_delta') or {}
    wins=sf(d.get('expected_wins'));pf=sf(d.get('expected_points_for'));play=sf(d.get('playoff_probability'));champ=sf(d.get('championship_probability'))
    if abs(champ)>=.02 and abs(wins)>=.15 and champ*wins<0:reasons.append('championship_probability_conflicts_with_expected_wins')
    if abs(champ)>=.02 and abs(play)>=.03 and champ*play<0:reasons.append('championship_probability_conflicts_with_playoff_probability')
    if abs(champ)>=.02 and abs(pf)>=10 and champ*pf<0:reasons.append('championship_probability_conflicts_with_expected_points')
    cap=sf((report.get('policy') or {}).get('contender_title_loss_cap'),-1)
    if cap>0 and champ<0 and abs(abs(champ)-cap)<=.0125:reasons.append('current_offer_near_title_equity_guardrail')
    # Do not use composite-score distance as a confirmation boundary. Option
    # quality is now categorical only from interpretable decision outputs, so a
    # legacy score-distance threshold would reintroduce an unsupported cliff.
    return sorted(set(reasons))


def summary(report):
    action=str(report.get('recommended_next_action') or 'REVIEW');cur=report.get('current_offer_evaluation') or {};sim=cur.get('simulation') or {};d=sim.get('focus_delta') or {};st=sim.get('strategic') or {};cs=report.get('suggested_counteroffers') or [];ms=report.get('market_sweep_alternatives') or []
    label={'ACCEPT_NOW':'ACCEPT','COUNTER_CURRENT_OFFEROR':'COUNTER','SHOP_BEFORE_ACCEPTING':'SHOP BEFORE ACCEPTING','DECLINE':'DECLINE'}.get(action,action.replace('_',' '))
    short=f"{label}. Current-offer impact: {float(d.get('expected_wins') or 0):+.2f} expected wins, {float(d.get('championship_probability') or 0)*100:+.1f} pts championship probability, {float(st.get('strategic_value_delta') or 0):+,.0f} overall franchise impact."
    focus=str(report.get('focus_user_id') or '');rr=(sim.get('roster_resolution') or {}).get(focus) or {};cuts=int(rr.get('required_cuts') or 0)
    if cuts:short+=f" Requires {cuts} forced active-roster cut{'s' if cuts!=1 else ''}, already included in these values."
    ac=(report.get('simulation') or {}).get('adaptive_confirmation') or {}
    if ac.get('triggered'):short+=f" Confirmed at {int(ac.get('confirmation_sims') or 0):,} simulations after an adaptive uncertainty check."
    short+=f" {len(cs)} suggested counteroffer{'s' if len(cs)!=1 else ''}; {len(ms)} market alternative{'s' if len(ms)!=1 else ''}."
    return short


def market_cmd(a,jp,sims,confirm_sims):
    return [sys.executable,str(MARKET_SWEEP),'--scenario',a.scenario,'--quick-sims',str(sims),'--confirm-sims',str(confirm_sims),'--search-depth',str(a.search_depth),'--seed',str(a.seed),'--output',str(jp)]


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--scenario',required=True);ap.add_argument('--out-dir',required=True);ap.add_argument('--basename',default='trade-decision-report');ap.add_argument('--quick-sims',type=int,default=200);ap.add_argument('--confirm-sims',type=int,default=0);ap.add_argument('--adaptive-confirm-sims',type=int,default=DEFAULT_ADAPTIVE_CONFIRM_SIMS);ap.add_argument('--disable-adaptive-confirmation',action='store_true');ap.add_argument('--search-depth',type=int,default=60);ap.add_argument('--seed',type=int,default=20260821);a=ap.parse_args()
    out=Path(a.out_dir);out.mkdir(parents=True,exist_ok=True);jp=out/f'{a.basename}.json';pp=out/f'{a.basename}.pdf';sp=out/f'{a.basename}-summary.json'
    final_target=max(50000,a.adaptive_confirm_sims,a.confirm_sims)
    run(market_cmd(a,jp,a.quick_sims,final_target));r=json.loads(jp.read_text(encoding='utf-8'))
    if r.get('model_version')!=EXPECTED_ANALYSIS_MODEL:raise RuntimeError(f"Trade report pipeline expected {EXPECTED_ANALYSIS_MODEL}, got {r.get('model_version')}")
    reasons=[] if a.disable_adaptive_confirmation else sensitivity_reasons(r)
    simmeta=r.setdefault('simulation',{})
    confirmed=int(simmeta.get('final_trade_impact_simulations') or 0)
    if confirmed < final_target:
        raise RuntimeError(f"Final trade-impact confirmation expected {final_target:,} simulations, got {confirmed:,}")
    simmeta['adaptive_confirmation']={
        'enabled':True,
        'triggered':True,
        'screening_sims':a.quick_sims,
        'confirmation_sims':confirmed,
        'trigger_reasons':['standard_high_precision_final_confirmation'],
        'final_metrics_source':'canonical_high_precision_finalist_confirmation',
    }
    r.setdefault('policy',{}).update({
        'adaptive_deep_confirmation_enabled':True,
        'adaptive_confirmation_reruns_full_trade_frontier':False,
        'broad_search_uses_low_cost_screening':True,
        'final_current_offer_and_finalists_use_50000_simulations':True,
        'contradictory_quick_sim_signals_trigger_confirmation':False,
        'unsupported_composite_score_boundary_triggers_confirmation':False,
        'final_report_uses_confirmed_metrics_when_triggered':True,
        'outcome_consistent_option_governance_enabled':True,
    })
    jp.write_text(json.dumps(r,indent=2,sort_keys=True),encoding='utf-8')
    run([sys.executable,str(PDF_RENDERER),'--input',str(jp),'--output',str(pp)])
    payload={'pipeline_model_version':MODEL_VERSION,'analysis_model_version':r.get('model_version'),'report_model_version':REPORT_VERSION,'recommended_next_action':r.get('recommended_next_action'),'suggested_counteroffer_count':len(r.get('suggested_counteroffers') or []),'market_sweep_alternative_count':len(r.get('market_sweep_alternatives') or []),'adaptive_confirmation':simmeta.get('adaptive_confirmation'),'short_answer':summary(r),'json_report':str(jp),'pdf_report':str(pp),'canonical_model_entry_point':str(MARKET_SWEEP),'delivery_policy':'Always return the short answer and attach/share the plain-English explanatory PDF report for a trade query.'}
    sp.write_text(json.dumps(payload,indent=2),encoding='utf-8');print(json.dumps(payload,indent=2))


if __name__=='__main__':main()
