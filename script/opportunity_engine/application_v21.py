#!/usr/bin/env python3
"""FSFFL Opportunity Engine 2.3 — executable-opportunity search."""
from __future__ import annotations
import argparse, copy, datetime as dt, hashlib, json, os, statistics, subprocess, sys
from pathlib import Path
SCRIPT=Path(__file__).resolve().parent.parent; ROOT=SCRIPT.parent; TEAM_IMPROVEMENT=SCRIPT/'gm3'/'team_improvement.py'
if str(SCRIPT) not in sys.path:sys.path.insert(0,str(SCRIPT))
from opportunity_engine import application as v1
from opportunity_engine import application_v2 as v2
from opportunity_engine import negotiation_frontier
from opportunity_engine import focal_utility_stability
from gm3 import team_improvement as gm3_team_improvement
MODEL_VERSION='FSFFL-Opportunity-Engine-2.3'
def _run_team_improvement(a,out):
    subprocess.run([sys.executable,str(TEAM_IMPROVEMENT),'--focus-user-id',str(a.focus_user_id),'--quick-sims',str(a.quick_sims),'--confirm-sims',str(a.confirm_sims),'--trade-screen',str(a.trade_screen),'--waiver-screen',str(a.waiver_screen),'--confirm-top',str(a.confirm_top),'--trade-packages-per-target',str(a.trade_packages_per_target),'--price-frontier-targets',str(a.price_frontier_targets),'--price-frontier-packages-per-target',str(a.price_frontier_packages_per_target),'--strategic-posture',str(a.strategic_posture),'--seed',str(a.seed),'--output',str(out)],cwd=ROOT,check=True)
def _trade_signature(row):
    return (
        str(row.get('trade_direction') or 'ACQUIRE'),
        str(row.get('counterparty_user_id') or row.get('seller_user_id') or ''),
        str((row.get('target') or {}).get('asset_id') or ''),
        tuple(sorted(str(a.get('asset_id') or '') for a in (row.get('outgoing') or []))),
        tuple(sorted(str(a.get('asset_id') or '') for a in (row.get('incoming') or []))),
    )
def _preserve_specialized_views(views, actionable_trade_signatures, simulation_sensitive_signatures):
    preserved={}
    for key,row in (views or {}).items():
        if not row:
            preserved[key]=None
            continue
        out=copy.deepcopy(row)
        channel=str(out.get('channel') or '')
        if channel=='TRADE':
            sig=_trade_signature(out)
            focal=float(out.get('team_improvement_score') or 0.0)
            counterparty=float(out.get('counterparty_shared_decision_utility_score') or 0.0)
            if sig in actionable_trade_signatures:
                status='ACTIONABLE'
            elif sig in simulation_sensitive_signatures:
                status='SIMULATION_SENSITIVE'
            elif focal <= 0:
                status='FOCAL_NON_POSITIVE'
            elif counterparty < 0:
                status='THEORETICAL_COUNTERPARTY_FAILURE'
            else:
                status='INVESTIGATIVE_ONLY'
            out['opportunity_routing_status']=status
            out['specialist_view_is_recommendation']=False
            out['specialist_view_preserved_despite_non_actionable_status']=status!='ACTIONABLE'
        elif channel=='WAIVER':
            status='ACTIONABLE' if float(out.get('team_improvement_score') or 0)>0 else 'INVESTIGATIVE_ONLY'
            out['opportunity_routing_status']=status
            out['specialist_view_is_recommendation']=False
            out['specialist_view_preserved_despite_non_actionable_status']=status!='ACTIONABLE'
        else:
            out['opportunity_routing_status']='ANALYTICAL_CONTEXT'
            out['specialist_view_is_recommendation']=False
            out['specialist_view_preserved_despite_non_actionable_status']=True
        preserved[key]=out
    return preserved


def _execution_plan(rows):
    return {'steps':[{'step':i+1,'channel':r.get('channel'),'description':r.get('description'),'execution_authority':'Trade Decision' if r.get('channel')=='TRADE' else 'GM3 Team Improvement'} for i,r in enumerate(rows)],'live_ownership_and_availability_must_be_rechecked_before_execution':True,'counterparty_acceptance_is_not_assumed':True}
def _portfolio_result(rows,result):
    out=v1._portfolio_result(rows,result); out['move_count']=len(rows); out['execution_plan']=_execution_plan(rows); return out
def build_portfolio(source,uid,depth,max_moves,beam,sims,confirm,confirm_top,seed,strategic_posture='AUTO'):
    """Filter theoretical counterparty failures, then delegate bundle search to full OE2."""
    frontier=negotiation_frontier.build(source.get('trade_price_frontier_candidates') or source.get('top_cross_channel_options') or [])
    trades=frontier['actionable_negotiations']+frontier['negotiation_targets']
    signatures={_trade_signature(x) for x in trades}
    filtered=copy.deepcopy(source)
    filtered['top_cross_channel_options']=[
        copy.deepcopy(r) for r in (source.get('top_cross_channel_options') or [])
        if r.get('channel')=='WAIVER' or (
            r.get('channel')=='TRADE' and
            _trade_signature(r) in signatures
        )
    ]
    out=v2.build_adaptive_portfolio_view(
        filtered,str(uid),depth=depth,max_moves=max_moves,beam_width=beam,
        simulations=sims,confirm_simulations=confirm,confirm_top=confirm_top,seed=seed,
        strategic_posture=strategic_posture
    )
    out['excludes_theoretical_counterparty_failures']=True
    return out
def _prospective(source):
    raw=json.dumps(source,sort_keys=True,separators=(',',':'),default=str).encode(); return {'schema_version':'FSFFL-Opportunity-Prospective-Snapshot-1.0','generated_at_utc':dt.datetime.now(dt.timezone.utc).isoformat(),'source_revision':os.getenv('GITHUB_SHA'),'source_input_sha256':hashlib.sha256(raw).hexdigest(),'contains_future_outcomes':False}
def build_board(source,a,reviews):
    b=v2.build_board(source,a,reviews)
    frontier_rows=source.get('trade_price_frontier_candidates') or source.get('top_cross_channel_options') or []
    frontier=negotiation_frontier.build(frontier_rows)
    b['model_version']=MODEL_VERSION; b['competitive_state']=source.get('competitive_state') or source.get('team_state'); b['strategic_posture']=copy.deepcopy(source.get('strategic_posture') or {}); b['negotiation_frontier']=frontier; b['best_price_overlap']=frontier.get('best_price_overlap')
    overlap_keys={
        (
            str(x.get('trade_direction') or 'ACQUIRE'),
            str(x.get('counterparty_user_id') or x.get('seller_user_id') or ''),
            str((x.get('target') or {}).get('asset_id') or ''),
        )
        for x in (frontier.get('target_price_frontiers') or [])
        if x.get('price_overlap_exists')
    }
    bilateral_rows=[]
    seen_bilateral=set()
    for row in (frontier.get('actionable_negotiations') or [])+(frontier.get('negotiation_targets') or []):
        sig=_trade_signature(row)
        if sig not in seen_bilateral:
            seen_bilateral.add(sig)
            bilateral_rows.append(copy.deepcopy(row))
    stability_seeds=[
        int(a.seed)+i*1009
        for i in range(max(0,int(getattr(a,'actionability_stability_seeds',0))))
    ]
    stability=focal_utility_stability.evaluate(
        bilateral_rows,
        a.focus_user_id,
        simulations=int(getattr(a,'actionability_stability_sims',500)),
        seeds=stability_seeds,
        strategic_posture=a.strategic_posture,
    )
    stability_by_signature={}
    if stability.get('enabled'):
        for row,result in zip(bilateral_rows,stability.get('rows') or []):
            stability_by_signature[_trade_signature(row)]=copy.deepcopy(result)
    for row in bilateral_rows:
        result=stability_by_signature.get(_trade_signature(row))
        row['focal_utility_stability_confirmation']=copy.deepcopy(result) if result else {
            'classification':'NOT_RUN',
            'confirmed_for_headline_action':True,
        }
    bilateral_by_signature={_trade_signature(x):x for x in bilateral_rows}
    raw_actionable_trades=[
        x for x in (frontier.get('actionable_negotiations') or [])+(frontier.get('negotiation_targets') or [])
        if (
            str(x.get('trade_direction') or 'ACQUIRE'),
            str(x.get('counterparty_user_id') or x.get('seller_user_id') or ''),
            str((x.get('target') or {}).get('asset_id') or ''),
        ) in overlap_keys
    ]
    actionable_trades=[]
    simulation_sensitive_trades=[]
    for raw_row in raw_actionable_trades:
        row=copy.deepcopy(bilateral_by_signature.get(_trade_signature(raw_row)) or raw_row)
        confirmation=row.get('focal_utility_stability_confirmation') or {}
        if bool(confirmation.get('confirmed_for_headline_action',True)):
            actionable_trades.append(row)
        else:
            simulation_sensitive_trades.append(row)
    actionable=actionable_trades[0] if actionable_trades else None
    explore=next((x for x in (frontier.get('negotiation_targets') or []) if x not in actionable_trades),None)
    waivers=[copy.deepcopy(x) for x in (source.get('top_cross_channel_options') or []) if x.get('channel')=='WAIVER' and float(x.get('team_improvement_score') or 0)>0]
    ranked_candidates=actionable_trades+waivers
    ranked_candidates.sort(key=lambda x:float(x.get('team_improvement_score') or 0),reverse=True)
    b['ranked_single_step_opportunities']=ranked_candidates
    b['best_actionable_trade']=actionable
    b['best_trade_opportunity']=actionable or explore
    b['best_trade_to_explore']=explore
    b['best_theoretical_upgrade']=frontier.get('best_theoretical_upgrade')
    b['high_impact_price_gap_targets']=frontier.get('high_impact_price_gap_targets') or []
    b['near_frontier_watchlist']=frontier.get('near_frontier_watchlist') or []
    b['best_near_frontier_target']=frontier.get('best_near_frontier_target')
    b['focal_utility_stability_confirmation']=stability
    b['simulation_sensitive_trade_watchlist']=simulation_sensitive_trades
    b['best_simulation_sensitive_trade']=(
        simulation_sensitive_trades[0] if simulation_sensitive_trades else None
    )
    b['outbound_future_value_opportunities']=[
        copy.deepcopy(x) for x in actionable_trades
        if str(x.get('trade_direction') or '')=='OUTBOUND_FUTURE_VALUE'
    ]
    b['best_outbound_future_value_opportunity']=(
        b['outbound_future_value_opportunities'][0]
        if b['outbound_future_value_opportunities'] else None
    )
    actionable_trade_signatures={_trade_signature(x) for x in actionable_trades}
    simulation_sensitive_signatures={_trade_signature(x) for x in simulation_sensitive_trades}
    b['specialized_views']=_preserve_specialized_views(
        b.get('specialized_views') or {},
        actionable_trade_signatures,
        simulation_sensitive_signatures,
    )
    candidates=[x for x in [actionable,waivers[0] if waivers else None] if x]
    if candidates:b['best_move_available']=max(candidates,key=lambda x:float(x.get('team_improvement_score') or 0))
    elif source.get('hold_benchmark'):b['best_move_available']=copy.deepcopy(source.get('hold_benchmark'))
    blocked_signatures={
        sig for sig,row in bilateral_by_signature.items()
        if stability_by_signature.get(sig)
        and not bool(stability_by_signature[sig].get('confirmed_for_headline_action'))
    }
    portfolio_source=copy.deepcopy(source)
    for key in ('top_cross_channel_options','trade_price_frontier_candidates'):
        portfolio_source[key]=[
            copy.deepcopy(row) for row in (portfolio_source.get(key) or [])
            if row.get('channel')!='TRADE' or _trade_signature(row) not in blocked_signatures
        ]
    b['portfolio_optimization']=build_portfolio(portfolio_source,a.focus_user_id,a.portfolio_depth,a.portfolio_max_moves,a.portfolio_beam_width,a.portfolio_sims,a.portfolio_confirm_sims,a.portfolio_confirm_top,a.seed,a.strategic_posture); b['prospective_validation']=_prospective(source)
    b['search_configuration']={'trade_candidates':a.trade_screen,'waiver_candidates':a.waiver_screen,'trade_packages_per_target':a.trade_packages_per_target,'price_frontier_targets':a.price_frontier_targets,'price_frontier_packages_per_target':a.price_frontier_packages_per_target,'quick_sims':a.quick_sims,'confirm_sims':a.confirm_sims,'portfolio_candidate_depth':a.portfolio_depth,'portfolio_max_moves':a.portfolio_max_moves,'portfolio_beam_width':a.portfolio_beam_width,'strategic_posture':a.strategic_posture,'actionability_stability_seeds':int(getattr(a,'actionability_stability_seeds',0)),'actionability_stability_sims_per_seed':int(getattr(a,'actionability_stability_sims',500))}
    b.setdefault('policy',{}).update({'negotiation_frontier_creates_new_utility':False,'negotiation_price_frontier_uses_all_evaluated_trade_candidates':bool(source.get('trade_price_frontier_candidates')),'heuristic_acceptance_fit_is_probability':False,'theoretical_counterparty_failures_can_be_headline_action':False,'theoretical_packages_can_occupy_actionable_ranking':False,'non_positive_focal_utility_can_occupy_actionable_ranking':False,'actionable_routing_orders_only_by_existing_gm3_utility':True,'portfolio_search_excludes_theoretical_counterparty_failures':True,'targeted_adaptive_price_discovery_creates_new_utility':False,'targeted_adaptive_price_discovery_creates_new_trade_value':False,'targeted_adaptive_price_discovery_package_economics_owned_by_gm3':True,'targeted_adaptive_price_discovery_target_selection_owned_by_opportunity_search':True,'near_frontier_watchlist_creates_new_trade_value':False,'near_frontier_watchlist_creates_acceptance_probability':False,'near_frontier_watchlist_uses_fixed_utility_cutoff':False,'strategic_posture_changes_competitive_state':False,'strategic_posture_search_guidance_creates_new_utility':False,'strategic_posture_uses_existing_governed_weight_curve':True,'outbound_future_value_search_uses_existing_counterparty_gm3_packages':True,'outbound_future_value_search_creates_new_utility':False,'outbound_future_value_candidates_require_shared_utility_recheck':True,'focal_utility_sign_stability_creates_new_utility':False,'focal_utility_sign_stability_uses_fixed_margin_threshold':False,'simulation_sensitive_focal_utility_can_be_headline_action':False,'portfolio_search_excludes_simulation_sensitive_focal_utility':True,'specialist_views_require_actionability':False,'specialist_views_create_recommendation_authority':False})
    b.setdefault('capability_status',{}).update({'negotiation_frontier':True,'discrete_price_frontier':True,'progressive_price_frontier_search':True,'actionable_vs_explore_vs_theoretical_trade_separation':True,'high_impact_price_gap_bucket':True,'near_frontier_negotiation_watchlist':True,'targeted_adaptive_price_discovery':True,'competitive_state_strategic_posture_separation':True,'owner_strategic_posture_override':True,'outbound_future_value_trade_search':True,'multi_asset_incoming_trade_packages':True,'focal_utility_sign_stability_confirmation':True,'simulation_sensitive_trade_watchlist':True,'specialist_view_routing_status':True})
    b.setdefault('provenance',{})['negotiation_frontier_bilateral_authority']='canonical governed counterparty shared utility'; b['provenance']['targeted_adaptive_price_discovery_package_authority']='GM3 trade package economics'
    return b
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--focus-user-id',required=True); ap.add_argument('--output',required=True); ap.add_argument('--team-improvement-input'); ap.add_argument('--quick-sims',type=int,default=500); ap.add_argument('--confirm-sims',type=int,default=2500); ap.add_argument('--trade-screen',type=int,default=75); ap.add_argument('--waiver-screen',type=int,default=50); ap.add_argument('--confirm-top',type=int,default=15); ap.add_argument('--trade-packages-per-target',type=int,default=12); ap.add_argument('--price-frontier-targets',type=int,default=16); ap.add_argument('--price-frontier-packages-per-target',type=int,default=18); ap.add_argument('--portfolio-depth',type=int,default=15); ap.add_argument('--portfolio-max-moves',type=int,default=3); ap.add_argument('--portfolio-beam-width',type=int,default=10); ap.add_argument('--portfolio-sims',type=int,default=1000); ap.add_argument('--portfolio-confirm-sims',type=int,default=5000); ap.add_argument('--portfolio-confirm-top',type=int,default=3); ap.add_argument('--trade-review-depth',type=int,default=3); ap.add_argument('--trade-review-quick-sims',type=int,default=200); ap.add_argument('--trade-review-confirm-sims',type=int,default=50000); ap.add_argument('--trade-review-search-depth',type=int,default=60); ap.add_argument('--robustness-seeds',type=int,default=0); ap.add_argument('--robustness-sims',type=int,default=500); ap.add_argument('--actionability-stability-seeds',type=int,default=4); ap.add_argument('--actionability-stability-sims',type=int,default=500); ap.add_argument('--strategic-posture',default='AUTO'); ap.add_argument('--seed',type=int,default=20260821); a=ap.parse_args(); out=Path(a.output); raw=Path(a.team_improvement_input) if a.team_improvement_input else out.with_suffix('.team-improvement.json')
    if not a.team_improvement_input:_run_team_improvement(a,raw)
    source=v1.load_json(raw); prefrontier=negotiation_frontier.build(source.get('trade_price_frontier_candidates') or source.get('top_cross_channel_options') or []); review_source=copy.deepcopy(source); review_source['top_cross_channel_options']=(prefrontier.get('actionable_negotiations') or [])+(prefrontier.get('negotiation_targets') or []); reviews=v1.review_trade_candidates(review_source,a.focus_user_id,depth=a.trade_review_depth,quick_sims=a.trade_review_quick_sims,confirm_sims=a.trade_review_confirm_sims,search_depth=a.trade_review_search_depth,seed=a.seed,strategic_posture=a.strategic_posture); board=build_board(source,a,reviews); v1.write_json(out,board); print(json.dumps({'model_version':MODEL_VERSION,'team':board.get('team_name'),'best_actionable_trade':(board.get('best_actionable_trade') or {}).get('description'),'best_trade_to_explore':(board.get('best_trade_to_explore') or {}).get('description'),'best_theoretical_upgrade':(board.get('best_theoretical_upgrade') or {}).get('description'),'output':str(out)},indent=2))
if __name__=='__main__':main()
