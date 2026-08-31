#!/usr/bin/env python3
"""FSFFL Opportunity Engine 2.1 — executable-opportunity search."""
from __future__ import annotations
import argparse, copy, datetime as dt, hashlib, json, os, statistics, subprocess, sys
from pathlib import Path
SCRIPT=Path(__file__).resolve().parent.parent; ROOT=SCRIPT.parent; TEAM_IMPROVEMENT=SCRIPT/'gm3'/'team_improvement.py'
if str(SCRIPT) not in sys.path:sys.path.insert(0,str(SCRIPT))
from opportunity_engine import application as v1
from opportunity_engine import application_v2 as v2
from opportunity_engine import negotiation_frontier
from gm3 import team_improvement as gm3_team_improvement
MODEL_VERSION='FSFFL-Opportunity-Engine-2.1'
def _run_team_improvement(a,out):
    subprocess.run([sys.executable,str(TEAM_IMPROVEMENT),'--focus-user-id',str(a.focus_user_id),'--quick-sims',str(a.quick_sims),'--confirm-sims',str(a.confirm_sims),'--trade-screen',str(a.trade_screen),'--waiver-screen',str(a.waiver_screen),'--confirm-top',str(a.confirm_top),'--trade-packages-per-target',str(a.trade_packages_per_target),'--seed',str(a.seed),'--output',str(out)],cwd=ROOT,check=True)
def _execution_plan(rows):
    return {'steps':[{'step':i+1,'channel':r.get('channel'),'description':r.get('description'),'execution_authority':'Trade Decision' if r.get('channel')=='TRADE' else 'GM3 Team Improvement'} for i,r in enumerate(rows)],'live_ownership_and_availability_must_be_rechecked_before_execution':True,'counterparty_acceptance_is_not_assumed':True}
def _portfolio_result(rows,result):
    out=v1._portfolio_result(rows,result); out['move_count']=len(rows); out['execution_plan']=_execution_plan(rows); return out
def build_portfolio(source,uid,depth,max_moves,beam,sims,confirm,confirm_top,seed):
    """Filter theoretical counterparty failures, then delegate bundle search to full OE2."""
    frontier=negotiation_frontier.build(source.get('trade_price_frontier_candidates') or source.get('top_cross_channel_options') or [])
    trades=frontier['actionable_negotiations']+frontier['negotiation_targets']
    signatures={(x.get('seller_user_id'),(x.get('target') or {}).get('asset_id'),tuple(sorted(a.get('asset_id') for a in (x.get('outgoing') or [])))) for x in trades}
    filtered=copy.deepcopy(source)
    filtered['top_cross_channel_options']=[
        copy.deepcopy(r) for r in (source.get('top_cross_channel_options') or [])
        if r.get('channel')=='WAIVER' or (
            r.get('channel')=='TRADE' and
            (r.get('seller_user_id'),(r.get('target') or {}).get('asset_id'),tuple(sorted(a.get('asset_id') for a in (r.get('outgoing') or [])))) in signatures
        )
    ]
    out=v2.build_adaptive_portfolio_view(
        filtered,str(uid),depth=depth,max_moves=max_moves,beam_width=beam,
        simulations=sims,confirm_simulations=confirm,confirm_top=confirm_top,seed=seed
    )
    out['excludes_theoretical_counterparty_failures']=True
    return out
def _prospective(source):
    raw=json.dumps(source,sort_keys=True,separators=(',',':'),default=str).encode(); return {'schema_version':'FSFFL-Opportunity-Prospective-Snapshot-1.0','generated_at_utc':dt.datetime.now(dt.timezone.utc).isoformat(),'source_revision':os.getenv('GITHUB_SHA'),'source_input_sha256':hashlib.sha256(raw).hexdigest(),'contains_future_outcomes':False}
def build_board(source,a,reviews):
    b=v2.build_board(source,a,reviews); frontier_rows=source.get('trade_price_frontier_candidates') or source.get('top_cross_channel_options') or []; frontier=negotiation_frontier.build(frontier_rows); b['model_version']=MODEL_VERSION; b['negotiation_frontier']=frontier; b['best_price_overlap']=frontier.get('best_price_overlap')
    # Actionable headline is now an executable-opportunity view, not merely the highest cheap hypothetical.
    actionable=frontier.get('best_actionable_trade'); explore=frontier.get('best_negotiation_target'); waiver=next((x for x in source.get('top_cross_channel_options') or [] if x.get('channel')=='WAIVER'),None)
    b['best_actionable_trade']=actionable; b['best_trade_to_explore']=explore; b['best_theoretical_upgrade']=frontier.get('best_theoretical_upgrade')
    candidates=[x for x in [actionable,explore,waiver] if x]
    if candidates: b['best_move_available']=max(candidates,key=lambda x:float(x.get('team_improvement_score') or 0))
    b['portfolio_optimization']=build_portfolio(source,a.focus_user_id,a.portfolio_depth,a.portfolio_max_moves,a.portfolio_beam_width,a.portfolio_sims,a.portfolio_confirm_sims,a.portfolio_confirm_top,a.seed); b['prospective_validation']=_prospective(source)
    b['search_configuration']={'trade_candidates':a.trade_screen,'waiver_candidates':a.waiver_screen,'trade_packages_per_target':a.trade_packages_per_target,'quick_sims':a.quick_sims,'confirm_sims':a.confirm_sims,'portfolio_candidate_depth':a.portfolio_depth,'portfolio_max_moves':a.portfolio_max_moves,'portfolio_beam_width':a.portfolio_beam_width}
    b.setdefault('policy',{}).update({'negotiation_frontier_creates_new_utility':False,'negotiation_price_frontier_uses_all_evaluated_trade_candidates':bool(source.get('trade_price_frontier_candidates')),'heuristic_acceptance_fit_is_probability':False,'theoretical_counterparty_failures_can_be_headline_action':False,'portfolio_search_excludes_theoretical_counterparty_failures':True}); b.setdefault('capability_status',{}).update({'negotiation_frontier':True,'discrete_price_frontier':True,'actionable_vs_explore_vs_theoretical_trade_separation':True}); b.setdefault('provenance',{})['negotiation_frontier_bilateral_authority']='canonical governed counterparty shared utility'; return b
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--focus-user-id',required=True); ap.add_argument('--output',required=True); ap.add_argument('--team-improvement-input'); ap.add_argument('--quick-sims',type=int,default=500); ap.add_argument('--confirm-sims',type=int,default=2500); ap.add_argument('--trade-screen',type=int,default=75); ap.add_argument('--waiver-screen',type=int,default=50); ap.add_argument('--confirm-top',type=int,default=15); ap.add_argument('--trade-packages-per-target',type=int,default=12); ap.add_argument('--portfolio-depth',type=int,default=15); ap.add_argument('--portfolio-max-moves',type=int,default=3); ap.add_argument('--portfolio-beam-width',type=int,default=10); ap.add_argument('--portfolio-sims',type=int,default=1000); ap.add_argument('--portfolio-confirm-sims',type=int,default=5000); ap.add_argument('--portfolio-confirm-top',type=int,default=3); ap.add_argument('--trade-review-depth',type=int,default=3); ap.add_argument('--trade-review-quick-sims',type=int,default=200); ap.add_argument('--trade-review-confirm-sims',type=int,default=50000); ap.add_argument('--trade-review-search-depth',type=int,default=60); ap.add_argument('--robustness-seeds',type=int,default=0); ap.add_argument('--robustness-sims',type=int,default=500); ap.add_argument('--seed',type=int,default=20260821); a=ap.parse_args(); out=Path(a.output); raw=Path(a.team_improvement_input) if a.team_improvement_input else out.with_suffix('.team-improvement.json')
    if not a.team_improvement_input:_run_team_improvement(a,raw)
    source=v1.load_json(raw); reviews=v1.review_trade_candidates(source,a.focus_user_id,depth=a.trade_review_depth,quick_sims=a.trade_review_quick_sims,confirm_sims=a.trade_review_confirm_sims,search_depth=a.trade_review_search_depth,seed=a.seed); board=build_board(source,a,reviews); v1.write_json(out,board); print(json.dumps({'model_version':MODEL_VERSION,'team':board.get('team_name'),'best_actionable_trade':(board.get('best_actionable_trade') or {}).get('description'),'best_trade_to_explore':(board.get('best_trade_to_explore') or {}).get('description'),'best_theoretical_upgrade':(board.get('best_theoretical_upgrade') or {}).get('description'),'output':str(out)},indent=2))
if __name__=='__main__':main()
