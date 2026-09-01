#!/usr/bin/env python3
"""FSFFL GM 3.0 Team Improvement Lab 1.6.

Trade discovery now preserves multiple governed search lanes: focal improvement,
counterparty bilateral utility, descriptive negotiation fit, seller motivation,
and target diversity. This prevents spectacular but implausibly cheap elite-player
packages from consuming the entire simulation budget. No new cross-unit score is
created; final franchise ranking remains the shared decision utility.
"""
from __future__ import annotations
import copy, importlib.util, json, sys
from pathlib import Path
BASE=Path(__file__).resolve().parent/'run_team_improvement_lab.py'
MODEL_VERSION='FSFFL-GM-Team-Improvement-Lab-1.6'
PROJECTION_MODEL_VERSION='FSFFL-Full-Projection-Universe-1.0'
DEFAULT_TRADE_PACKAGES_PER_TARGET=8
DEFAULT_PRICE_FRONTIER_TARGETS=16
DEFAULT_PRICE_FRONTIER_PACKAGES_PER_TARGET=18
_TARGETED_GM_CACHE={}

def load_base():
    spec=importlib.util.spec_from_file_location('team_improvement_lab_base16',BASE); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod

def _pop_cli_int(name,default):
    if name not in sys.argv:return int(default)
    i=sys.argv.index(name); value=int(sys.argv[i+1]); del sys.argv[i:i+2]; return value

def output_path_from_argv():
    if '--output' not in sys.argv:return None
    i=sys.argv.index('--output'); return Path(sys.argv[i+1]) if i+1<len(sys.argv) else None

def full_projection_doc(base,season):
    path=base.DATA/'simulator'/str(season)/'inputs'/'player_weekly_projections_full.json'; doc=base.load_json(path,{}) or {}
    if doc.get('model_version')!=PROJECTION_MODEL_VERSION or not (doc.get('players') or {}): raise RuntimeError(f'Canonical full projection universe unavailable: {path}')
    return doc,path

def _round_robin_rows(lanes,limit,keyfn):
    out=[]; seen=set(); cursors={k:0 for k in lanes}
    while len(out)<int(limit):
        progressed=False
        for name,lane in lanes.items():
            i=cursors[name]
            while i<len(lane):
                row=lane[i]; i+=1; key=keyfn(row)
                if key and key not in seen:
                    x=copy.deepcopy(row); x.setdefault('discovery_lanes',[]); x['discovery_lanes'].append(name); out.append(x); seen.add(key); progressed=True; break
            cursors[name]=i
            if len(out)>=int(limit):break
        if not progressed:break
    return out

def _price_frontier_sample(packages,budget,sf):
    """Sample the full cost curve without creating a valuation score.

    Coverage is deliberately densest around the seller-utility and focal-utility
    sign changes, because those are the only economically meaningful boundaries
    Trade Decision later interprets. The budget is computational only.
    """
    ordered=sorted(
        [copy.deepcopy(x) for x in (packages or [])],
        key=lambda p:(sf(p.get('package_market_value_coordinate')), -sf(p.get('focal_strategic_utility')))
    )
    n=len(ordered); budget=max(1,int(budget))
    if n<=budget:return ordered

    priority=[]
    def add(i):
        if i is not None and 0<=i<n and i not in priority: priority.append(i)

    add(0); add(n-1)
    seller_i=next((i for i,p in enumerate(ordered) if sf(p.get('seller_strategic_utility'),-1e18)>=0),None)
    focal_i=next((i for i,p in enumerate(ordered) if sf(p.get('focal_strategic_utility'),1e18)<=0),None)
    for center in (seller_i,focal_i):
        if center is not None:
            for d in (0,-1,1,-2,2):
                add(center+d)

    # Also retain packages closest to each zero crossing even when utility is
    # not monotone in the market-ordering coordinate.
    for i in sorted(range(n), key=lambda j:abs(sf(ordered[j].get('seller_strategic_utility'),1e18)))[:3]:
        add(i)
    for i in sorted(range(n), key=lambda j:abs(sf(ordered[j].get('focal_strategic_utility'),1e18)))[:3]:
        add(i)

    chosen=priority[:budget]
    chosen_set=set(chosen)
    remaining=max(0,budget-len(chosen))
    if remaining:
        for j in range(1,remaining+1):
            i=round(j*(n-1)/(remaining+1))
            if i not in chosen_set:
                chosen.append(i); chosen_set.add(i)
    if len(chosen)<budget:
        for i in range(n):
            if i not in chosen_set:
                chosen.append(i); chosen_set.add(i)
                if len(chosen)>=budget:break
    return [ordered[i] for i in sorted(chosen[:budget])]

def _targeted_price_curves(base,focus_uid,target_asset_ids,package_budget):
    """Ask canonical GM3 for deeper curves only on selected trade targets."""
    target_asset_ids=tuple(str(x) for x in target_asset_ids if x)
    if not target_asset_ids:
        return {}
    key=(str(focus_uid),target_asset_ids,int(package_budget))
    if key in _TARGETED_GM_CACHE:
        return copy.deepcopy(_TARGETED_GM_CACHE[key])

    engine=base.load_module(Path(__file__).resolve().parent/'build_fsffl_gm_engine.py','gm3_targeted_price_discovery')
    override=base.load_module(Path(__file__).resolve().parent/'nonprojection_high_priority_overrides.py','gm3_targeted_price_discovery_overrides')
    override.install(engine)
    payload=engine.build_targeted_trade_price_curves(
        str(focus_uid),
        list(target_asset_ids),
        max_packages_per_target=max(1,int(package_budget)),
    )
    curves={
        str(x.get('target_asset_id')):copy.deepcopy(x)
        for x in (payload.get('targets') or [])
        if x.get('target_asset_id')
    }
    _TARGETED_GM_CACHE[key]=copy.deepcopy(curves)
    return curves

def trade_candidates(base,focus_uid,catalog,limit,packages_per_target,frontier_targets,frontier_packages_per_target):
    doc=base.team_doc(focus_uid,'trade_opportunities'); rows=[]; frontier_rows=[]
    opportunities=list(doc.get('opportunities') or [])
    def existing_curve_has_overlap(opp):
        for pkg in (opp.get('price_frontier_candidate_packages') or []):
            if base.sf(pkg.get('focal_strategic_utility'),-1e18)>0 and base.sf(pkg.get('seller_strategic_utility'),-1e18)>=0:
                return True
        return False
    targeted_ids=[]
    for opp in opportunities:
        if len(targeted_ids)>=max(0,int(frontier_targets)):
            break
        if opp.get('target_asset_id') and not existing_curve_has_overlap(opp):
            targeted_ids.append(str(opp.get('target_asset_id')))
    targeted_curves=_targeted_price_curves(
        base,focus_uid,targeted_ids,max(1,int(frontier_packages_per_target))
    ) if targeted_ids else {}
    def make_row(opp,pkg,ordinal,frontier=False):
        tid=str(opp.get('target_asset_id') or ''); seller=str(opp.get('seller_user_id') or ''); target=catalog.get(tid)
        if not target or not seller or seller==str(focus_uid):return None
        aids=[str(x) for x in (pkg.get('focal_outgoing_asset_ids') or [])]; outgoing=[catalog.get(x) for x in aids]
        if not aids or any(x is None for x in outgoing):return None
        row={'channel':'TRADE','seller_user_id':seller,'seller_team':opp.get('seller_team'),'target':target,'outgoing':outgoing,
            'pre_screen_score':base.sf(pkg.get('gm30_decision_score'),base.sf(pkg.get('decision_score'))),
            'acceptance_fit_score':base.sf(pkg.get('acceptance_fit_score')),
            'seller_strategic_utility_precomputed':base.sf(pkg.get('seller_strategic_utility')),
            'source_recommendation_band':pkg.get('recommendation_band'),'target_focal_value':base.sf(opp.get('focal_value')),
            'target_market_dynasty':base.sf(opp.get('market_dynasty')),'target_market_redraft':base.sf(opp.get('market_redraft')),
            'focal_position_need':base.sf(opp.get('focal_position_need')),'seller_motivation_score':base.sf(opp.get('seller_motivation_score')),
            'source_package_ordinal_for_target':ordinal,'source_package_score_owned_by_gm3':True,
            'package_market_value_coordinate':base.sf(pkg.get('package_market_value_coordinate'))}
        if frontier:
            row['price_frontier_search_candidate']=True
            row['price_frontier_search_is_computational_coverage_only']=True
        return row
    for oi,opp in enumerate(opportunities):
        for ordinal,pkg in enumerate(list(opp.get('best_candidate_packages') or [])[:max(1,int(packages_per_target))],1):
            row=make_row(opp,pkg,ordinal,False)
            if row:rows.append(row)
        if oi<max(0,int(frontier_targets)):
            targeted=targeted_curves.get(str(opp.get('target_asset_id') or '')) or {}
            curve=targeted.get('price_frontier_candidate_packages') or opp.get('price_frontier_candidate_packages') or []
            for ordinal,pkg in enumerate(_price_frontier_sample(curve,frontier_packages_per_target,base.sf),1):
                row=make_row(opp,pkg,ordinal,True)
                if row:
                    row['targeted_adaptive_price_discovery_used']=bool(targeted)
                    row['targeted_adaptive_price_discovery_package_economics_owned_by_gm3']=bool(targeted)
                    frontier_rows.append(row)
    key=lambda r:(r['seller_user_id'],r['target']['asset_id'],tuple(sorted(x['asset_id'] for x in r['outgoing'])))
    dedup={}
    for r in rows:dedup.setdefault(key(r),r)
    rows=list(dedup.values())
    focal=sorted(rows,key=lambda x:(x['pre_screen_score'],x['acceptance_fit_score']),reverse=True)
    bilateral=sorted(rows,key=lambda x:(x['seller_strategic_utility_precomputed'],x['pre_screen_score']),reverse=True)
    fit=sorted(rows,key=lambda x:(x['acceptance_fit_score'],x['pre_screen_score']),reverse=True)
    motivation=sorted(rows,key=lambda x:(x['seller_motivation_score'],x['pre_screen_score']),reverse=True)
    target_best=[]; seen_targets=set()
    for r in focal:
        tid=r['target']['asset_id']
        if tid not in seen_targets:target_best.append(r); seen_targets.add(tid)
    selected=_round_robin_rows({'focal_utility':focal,'bilateral_utility':bilateral,'negotiation_fit':fit,'seller_motivation':motivation,'target_diversity':target_best},limit,key)
    seen={key(r) for r in selected}
    for r in frontier_rows:
        k=key(r)
        if k not in seen:selected.append(r); seen.add(k)
    for i,r in enumerate(selected,1):
        r['trade_discovery_rank']=i; r['trade_discovery_multilane']=not bool(r.get('price_frontier_search_candidate'))
    return selected

def waiver_candidates(base,focus_uid,players_catalog,model_inputs,limit):
    _,_,rosters,_,players,season,_,_=model_inputs; owned=base.owner_map(rosters); full,_=full_projection_doc(base,season); rows=[]
    for pid,profile in (full.get('players') or {}).items():
        pid=str(pid)
        if pid in owned:continue
        pos=str(profile.get('position') or ((players or {}).get(pid) or {}).get('position') or '')
        if pos not in {'QB','RB','WR','TE'} or not (profile.get('weeks') or {}):continue
        means=[base.sf(v.get('mean',v.get('median')))*base.sf(v.get('active_probability'),1.0) for v in profile['weeks'].values()]; projected=sum(means)/max(1,len(means)); c=players_catalog.get(f'player:{pid}') or {}; prov=profile.get('projection_provenance') or {}; ecr=base.sf(prov.get('target_ecr'),9999)
        rows.append({'channel':'WAIVER','target':{'asset_id':f'player:{pid}','asset_type':'player','player_id':pid,'name':c.get('name') or profile.get('name') or f'player:{pid}','position':pos,'market_dynasty':base.sf(c.get('market_dynasty')),'market_redraft':base.sf(c.get('market_redraft')),'fsffl_value':base.sf(c.get('fsffl_value')),'owner_user_id':None},'projected_weekly_mean':round(projected,3),'preseason_ecr':None if ecr>=9999 else ecr,'native_full_projection':copy.deepcopy(profile)})
    lanes={'projection':sorted(rows,key=lambda x:x['projected_weekly_mean'],reverse=True),'preseason_ecr':sorted([x for x in rows if x['preseason_ecr'] is not None],key=lambda x:x['preseason_ecr']),'market_dynasty':sorted(rows,key=lambda x:x['target']['market_dynasty'],reverse=True),'fsffl_value':sorted(rows,key=lambda x:x['target']['fsffl_value'],reverse=True)}
    selected=_round_robin_rows(lanes,max(1,int(limit)),lambda r:r['target']['asset_id'])
    for i,r in enumerate(selected,1): r['pre_screen_rank']=i; r['pre_screen_score']=None; r['pre_screen_weighted_score_used']=False
    return selected

def simulate_actions_protect_add(base,dl,lineupopt,rosteraware,model_inputs,baseline_lineups,baseline,focus_uid,actions,sims,seed):
    simmod,league,canonical_rosters,users,players,season,projections,raw_schedule=model_inputs; hypothetical,_=dl.apply_actions(canonical_rosters,actions); touched=dl.touched_users(focus_uid,actions); protected={}
    for a in actions:
        if str(a.get('type') or '').lower()=='add': protected.setdefault(str(a.get('user_id')),set()).update(str(x) for x in (a.get('players') or []))
    legal,resolutions,cuts=rosteraware.legalize_trade_rosters(dl,canonical_rosters,hypothetical,touched,league,players,protected_player_ids_by_uid=protected); effective=list(actions)+list(cuts); lineups,reopt=base.fast_reoptimize(lineupopt,dl,simmod,baseline_lineups,legal,touched,league,users,players,projections); hyp=dl.simulate_from_lineups(simmod,league,legal,users,raw_schedule,lineups,sims,seed); bidx,hidx=base.team_index(baseline),base.team_index(hyp); b,h=bidx[str(focus_uid)],hidx[str(focus_uid)]
    return {'focus_before':b,'focus_after':h,'focus_delta':{k:base.delta(b.get(k),h.get(k)) for k in ['expected_wins','expected_points_for','playoff_probability','bye_probability','championship_probability']},'strategic':dl.strategic_summary(str(focus_uid),effective),'roster_resolution':resolutions,'effective_actions':effective,'teams_reoptimized':reopt,'simulation_count':sims}
def main():
    ppt=_pop_cli_int('--trade-packages-per-target',DEFAULT_TRADE_PACKAGES_PER_TARGET); pft=_pop_cli_int('--price-frontier-targets',DEFAULT_PRICE_FRONTIER_TARGETS); pfpt=_pop_cli_int('--price-frontier-packages-per-target',DEFAULT_PRICE_FRONTIER_PACKAGES_PER_TARGET); out=output_path_from_argv(); base=load_base(); base.MODEL_VERSION=MODEL_VERSION; saved=base.evaluate_row; evaluated_trade_rows=[]
    base.trade_candidates=lambda uid,catalog,limit:trade_candidates(base,uid,catalog,limit,ppt,pft,pfpt); base.waiver_candidates=lambda uid,catalog,mi,limit:waiver_candidates(base,uid,catalog,mi,limit); base.simulate_actions=lambda dl,lo,ra,mi,bl,b,uid,a,s,seed:simulate_actions_protect_add(base,dl,lo,ra,mi,bl,b,uid,a,s,seed)
    def ev(row,uid,dl,lo,ra,mi,bl,b,s,seed):
        if row.get('channel')!='WAIVER' or not row.get('native_full_projection'):
            result=saved(row,uid,dl,lo,ra,mi,bl,b,s,seed)
            if result.get('channel')=='TRADE': evaluated_trade_rows.append(copy.deepcopy(result))
            return result
        m=list(mi); p=copy.deepcopy(m[6]); p.setdefault('players',{})[str(row['target']['player_id'])]=copy.deepcopy(row['native_full_projection']); m[6]=p; return saved(row,uid,dl,lo,ra,tuple(m),bl,b,s,seed)
    base.evaluate_row=ev; base.main()
    if out and out.exists():
        report=json.loads(out.read_text()); league=base.load_json(base.DATA/'league.json',{}) or {}; full,path=full_projection_doc(base,str(league.get('season') or '')); report['model_version']=MODEL_VERSION; report['projection_universe']={'model_version':full.get('model_version'),'path':str(path),'coverage':full.get('coverage') or {},'waiver_candidates_use_canonical_full_projection':True}; report.setdefault('search_summary',{})['trade_packages_per_target_considered']=int(ppt); report['search_summary']['price_frontier_targets_expanded']=int(pft); report['search_summary']['price_frontier_packages_per_target_considered']=int(pfpt); report['search_summary']['targeted_adaptive_price_discovery_enabled']=bool(pft and pfpt); report.setdefault('policy',{}).update({'waiver_candidates_use_canonical_full_projection_universe':True,'waiver_pre_screen_uses_fixed_cross_unit_coefficients':False,'waiver_discovery_is_scale_free_multilane':True,'trade_discovery_is_governed_multilane':True,'trade_discovery_preserves_bilateral_utility_lane':True,'trade_discovery_preserves_negotiation_fit_lane':True,'trade_discovery_preserves_seller_motivation_lane':True,'trade_discovery_preserves_target_diversity_lane':True,'trade_package_pre_screen_score_owned_by_upstream_gm3':True,'targeted_price_discovery_package_economics_owned_by_gm3':True,'targeted_price_discovery_target_selection_is_search_orchestration':True,'targeted_price_discovery_creates_new_trade_value':False,'seller_motivation_is_search_coverage_only':True,'negotiation_fit_is_search_coverage_only':True}); report['ranking_calibration']={'version':'shared-decision-utility-2.0','principle':'Team Improvement and Trade Decision use the same continuous primitive utility','shared_utility_model':'FSFFL-Shared-Decision-Utility-2.0','categorical_state_weights_active':False,'legacy_championship_diminishing_return_rule_active':False,'legacy_dynasty_value_guardrail_authoritative':False,'scale_status':'DATA_DERIVED_LEAGUE_RELATIVE_NO_FIXED_UNIT_CONVERSION_COEFFICIENTS','notes':'Displayed football outcomes remain raw Simulator results. Recommendation ranking uses one shared current/future/liquidity/resilience utility; acceptance remains separate. GM3 1.6 broadens discovery through governed lanes without creating a new ranking score.'}
        frontier={}
        for row in evaluated_trade_rows:
            target=row.get('target') or {}; key=(str(row.get('seller_user_id') or ''),str(target.get('asset_id') or ''),tuple(sorted(str(x.get('asset_id') or '') for x in (row.get('outgoing') or []))))
            prior=frontier.get(key); prior_sims=((prior or {}).get('simulation') or {}).get('simulation_count',0); row_sims=(row.get('simulation') or {}).get('simulation_count',0)
            if prior is None or int(row_sims or 0)>=int(prior_sims or 0): frontier[key]=row
        report['trade_price_frontier_candidates']=sorted(list(frontier.values()),key=lambda x:base.sf(x.get('team_improvement_score')) if x.get('team_improvement_score') is not None else -1e18,reverse=True)
        report['search_summary']['trade_price_frontier_candidates_evaluated']=len(report['trade_price_frontier_candidates'])
        report['policy']['trade_price_frontier_uses_all_evaluated_trade_candidates']=True
        report['policy']['trade_price_frontier_candidates_preserve_gm3_utility']=True
        report['policy']['trade_price_frontier_candidates_ordered_by_gm3_utility']=True
        report['policy']['price_frontier_search_uses_upstream_gm_trade_package_curve']=True
        report['policy']['price_frontier_search_depth_is_computational_control_only']=True
        report['policy']['targeted_adaptive_price_discovery_uses_same_gm3_package_economics']=True
        out.write_text(json.dumps(report,indent=2,sort_keys=True))
if __name__=='__main__':main()
