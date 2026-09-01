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

def load_posture():
    path=Path(__file__).resolve().parent/'strategic_posture.py'
    spec=importlib.util.spec_from_file_location('team_improvement_search_posture',path)
    mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod

def _pop_cli_int(name,default):
    if name not in sys.argv:return int(default)
    i=sys.argv.index(name); value=int(sys.argv[i+1]); del sys.argv[i:i+2]; return value

def _pop_cli_str(name,default):
    if name not in sys.argv:return str(default)
    i=sys.argv.index(name); value=str(sys.argv[i+1]); del sys.argv[i:i+2]; return value

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

def _outbound_future_value_rows(base, focus_uid, catalog, packages_per_target):
    """Invert existing universal GM3 acquisition packages that target focal assets.

    This is discovery only. Opponent-side GM3 package economics identify
    plausible interest/packages; authoritative focal and counterparty utility
    are recomputed downstream from the actual inverted trade.
    """
    focus_uid=str(focus_uid)
    idx=base.load_json(base.DATA/'gm'/'franchise_index.json',{}) or {}
    rows=[]
    for team in (idx.get('teams') or []):
        buyer_uid=str(team.get('user_id') or '')
        if not buyer_uid or buyer_uid==focus_uid:
            continue
        path=((team.get('paths') or {}).get('trade_opportunities'))
        if not path:
            continue
        doc=base.load_json(Path(path),{}) or {}
        for opp in (doc.get('opportunities') or []):
            focal_asset_id=str(opp.get('target_asset_id') or '')
            focal_asset=catalog.get(focal_asset_id)
            if not focal_asset or str(focal_asset.get('owner_user_id') or '')!=focus_uid:
                continue
            for ordinal,pkg in enumerate(
                list(opp.get('best_candidate_packages') or [])[:max(1,int(packages_per_target))],
                1,
            ):
                incoming_ids=[str(x) for x in (pkg.get('focal_outgoing_asset_ids') or [])]
                incoming=[catalog.get(x) for x in incoming_ids]
                if not incoming_ids or any(x is None for x in incoming):
                    continue
                # Validate that the inverted package is actually controlled by
                # the modeled buyer. Stale ownership must fail closed.
                if any(str(x.get('owner_user_id') or '')!=buyer_uid for x in incoming):
                    continue
                outgoing=[focal_asset]
                dynasty_delta=sum(base.sf(x.get('market_dynasty')) for x in incoming)-base.sf(focal_asset.get('market_dynasty'))
                redraft_delta=sum(base.sf(x.get('market_redraft')) for x in incoming)-base.sf(focal_asset.get('market_redraft'))
                rows.append({
                    'channel':'TRADE',
                    'trade_direction':'OUTBOUND_FUTURE_VALUE',
                    'counterparty_user_id':buyer_uid,
                    # Compatibility field: downstream historically calls the
                    # other participant seller_user_id.
                    'seller_user_id':buyer_uid,
                    'seller_team':team.get('team_name'),
                    'target':focal_asset,
                    'outgoing':outgoing,
                    'incoming':incoming,
                    'pre_screen_score':None,
                    'acceptance_fit_score':base.sf(pkg.get('acceptance_fit_score')),
                    'seller_strategic_utility_precomputed':base.sf(pkg.get('focal_strategic_utility')),
                    'counterparty_interest_utility_precomputed':base.sf(pkg.get('focal_strategic_utility')),
                    'focal_legacy_utility_precomputed':base.sf(pkg.get('seller_strategic_utility')),
                    'source_recommendation_band':pkg.get('recommendation_band'),
                    'source_package_ordinal_for_target':ordinal,
                    'source_package_score_owned_by_gm3':True,
                    'source_inverted_from_counterparty_gm3_acquisition_search':True,
                    'outbound_search_creates_new_trade_value':False,
                    'package_market_value_coordinate':round(sum(base.sf(x.get('market_dynasty')) for x in incoming),2),
                    'package_market_dynasty_delta':round(dynasty_delta,2),
                    'package_market_redraft_delta':round(redraft_delta,2),
                })
    rows.sort(
        key=lambda x:(
            x.get('package_market_dynasty_delta',-1e18),
            x.get('counterparty_interest_utility_precomputed',-1e18),
            x.get('acceptance_fit_score',0.0),
        ),
        reverse=True,
    )
    return rows

def trade_candidates(base,focus_uid,catalog,limit,packages_per_target,frontier_targets,frontier_packages_per_target,posture='AUTO'):
    doc=base.team_doc(focus_uid,'trade_opportunities'); rows=[]; frontier_rows=[]
    outbound_rows=_outbound_future_value_rows(base,focus_uid,catalog,packages_per_target)
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
            'package_market_value_coordinate':base.sf(pkg.get('package_market_value_coordinate')),
            'package_market_dynasty_delta':round(base.sf(target.get('market_dynasty'))-sum(base.sf(x.get('market_dynasty')) for x in outgoing),2),
            'package_market_redraft_delta':round(base.sf(target.get('market_redraft'))-sum(base.sf(x.get('market_redraft')) for x in outgoing),2)}
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
    key=lambda r:(
        str(r.get('trade_direction') or 'ACQUIRE'),
        str(r.get('counterparty_user_id') or r.get('seller_user_id') or ''),
        str((r.get('target') or {}).get('asset_id') or ''),
        tuple(sorted(str(x.get('asset_id') or '') for x in (r.get('outgoing') or []))),
        tuple(sorted(str(x.get('asset_id') or '') for x in (r.get('incoming') or []))),
    )
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
    future_value=sorted(rows,key=lambda x:(x.get('package_market_dynasty_delta',-1e18),base.sf(x.get('pre_screen_score'),-1e18)),reverse=True)
    immediate=sorted(rows,key=lambda x:(x.get('package_market_redraft_delta',-1e18),base.sf(x.get('pre_screen_score'),-1e18)),reverse=True)
    outbound_future=outbound_rows
    posture_mod=load_posture()
    normalized_posture=posture_mod.normalize_selection(posture)
    available={
        'focal_utility':focal,
        'bilateral_utility':bilateral,
        'negotiation_fit':fit,
        'seller_motivation':motivation,
        'target_diversity':target_best,
        'future_value_preservation':future_value,
        'immediate_current_value':immediate,
        'outbound_future_value':outbound_future,
    }
    lane_order=posture_mod.SEARCH_LANE_ORDERS[normalized_posture]
    lanes={name:available[name] for name in lane_order if name in available}
    selected=_round_robin_rows(lanes,limit,key)
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
    simmod,league,canonical_rosters,users,players,season,projections,raw_schedule=model_inputs
    trade_actions_only=[a for a in actions if str(a.get('type') or '').lower().strip()=='trade']
    runtime_projections=dl.augment_projections_for_actions(trade_actions_only,projections,season) if hasattr(dl,'augment_projections_for_actions') else projections
    trade_added=list(((runtime_projections or {}).get('_decision_lab_projection_augmentation') or {}).get('added_player_ids') or [])
    baseline_lineups_runtime=baseline_lineups
    baseline_runtime=baseline
    if trade_added:
        baseline_lineups_runtime=dl.load_cached_lineups(season,projections_override=runtime_projections)
        baseline_runtime=dl.simulate_from_lineups(simmod,league,canonical_rosters,users,raw_schedule,baseline_lineups_runtime,sims,seed,projections_override=runtime_projections)
    hypothetical,_=dl.apply_actions(canonical_rosters,actions); touched=dl.touched_users(focus_uid,actions); protected={}
    for a in actions:
        if str(a.get('type') or '').lower()=='add': protected.setdefault(str(a.get('user_id')),set()).update(str(x) for x in (a.get('players') or []))
    legal,resolutions,cuts=rosteraware.legalize_trade_rosters(dl,canonical_rosters,hypothetical,touched,league,players,protected_player_ids_by_uid=protected); effective=list(actions)+list(cuts); lineups,reopt=base.fast_reoptimize(lineupopt,dl,simmod,baseline_lineups_runtime,legal,touched,league,users,players,runtime_projections)
    metadata_expected=0; metadata_missing=[]
    for rid in reopt:
        for week,rows in (lineups.get(rid) or {}).items():
            for row in rows or []:
                pid=str(row.get('player_id') or '')
                if not pid: continue
                meta=(players or {}).get(pid) or {}
                team=meta.get('team') or meta.get('team_abbr')
                if team:
                    metadata_expected+=1
                    if not row.get('nfl_team'): metadata_missing.append({'roster_id':rid,'week':week,'player_id':pid})
    hyp=dl.simulate_from_lineups(simmod,league,legal,users,raw_schedule,lineups,sims,seed,projections_override=runtime_projections); bidx,hidx=base.team_index(baseline_runtime),base.team_index(hyp)
    baseline_teams=list((baseline_runtime or {}).get('teams') or [])
    def mean_metric(key):
        vals=[float(x.get(key) or 0.0) for x in baseline_teams]
        return (sum(vals)/len(vals)) if vals else 0.0
    league_reference={'team_count':len(baseline_teams),'expected_wins_mean':mean_metric('expected_wins'),'expected_points_for_mean':mean_metric('expected_points_for'),'playoff_probability_mean':mean_metric('playoff_probability'),'championship_probability_mean':mean_metric('championship_probability'),'source':'canonical_baseline_simulator_league_mean'}
    def perspective(uid):
        uid=str(uid); before,after=bidx[uid],hidx[uid]
        focus_delta={k:base.delta(before.get(k),after.get(k)) for k in ['expected_wins','expected_points_for','playoff_probability','bye_probability','championship_probability']}
        opponent_title_gain=sum(
            max(0.0, base.sf(base.delta(bidx[str(other)].get('championship_probability'),hidx[str(other)].get('championship_probability'))))
            for other in touched if str(other)!=uid and str(other) in bidx and str(other) in hidx
        )
        focus_title_delta=base.sf(focus_delta.get('championship_probability'))
        net_swing=opponent_title_gain-focus_title_delta
        return {
            'focus_before':before,'focus_after':after,'league_reference':league_reference,
            'focus_delta':focus_delta,
            'buyer_championship_probability_delta':round(opponent_title_gain,5),
            'net_title_equity_swing_against_focus':round(net_swing,5),
            'competitive_externality':{
                'focus_championship_probability_delta':round(focus_title_delta,5),
                'opponent_positive_championship_probability_delta_sum':round(opponent_title_gain,5),
                'net_title_equity_swing_against_focus':round(net_swing,5),
            },
            'strategic':dl.strategic_summary(uid,effective),'roster_resolution':resolutions,
            'effective_actions':effective,'teams_reoptimized':reopt,'simulation_count':sims,
            'simulator_features':{
                **copy.deepcopy(hyp.get('features') or {}),
                'reoptimized_lineup_nfl_team_metadata_expected_rows':metadata_expected,
                'reoptimized_lineup_nfl_team_metadata_missing_rows':len(metadata_missing),
                'reoptimized_lineup_nfl_team_metadata_complete':len(metadata_missing)==0,
                'baseline_trade_projection_augmentation_applied':bool(trade_added),
                'baseline_trade_projection_added_player_ids':trade_added,
            }
        }
    result=perspective(str(focus_uid))
    counterparties=[str(x) for x in touched if str(x)!=str(focus_uid)]
    if counterparties:
        result['counterparty_user_id']=counterparties[0]
        result['counterparty']=perspective(counterparties[0])
    return result
def main():
    ppt=_pop_cli_int('--trade-packages-per-target',DEFAULT_TRADE_PACKAGES_PER_TARGET); pft=_pop_cli_int('--price-frontier-targets',DEFAULT_PRICE_FRONTIER_TARGETS); pfpt=_pop_cli_int('--price-frontier-packages-per-target',DEFAULT_PRICE_FRONTIER_PACKAGES_PER_TARGET); posture=_pop_cli_str('--strategic-posture','AUTO'); out=output_path_from_argv(); base=load_base(); base.MODEL_VERSION=MODEL_VERSION; base._strategic_posture_override=posture; saved=base.evaluate_row; utility=base.load_module(Path(__file__).resolve().parent/'decision_utility.py','team_improvement_counterparty_decision_utility'); attribution=base.load_module(Path(__file__).resolve().parent/'decision_attribution.py','team_improvement_decision_attribution'); evaluated_trade_rows=[]
    base.trade_candidates=lambda uid,catalog,limit:trade_candidates(base,uid,catalog,limit,ppt,pft,pfpt,posture); base.waiver_candidates=lambda uid,catalog,mi,limit:waiver_candidates(base,uid,catalog,mi,limit); base.simulate_actions=lambda dl,lo,ra,mi,bl,b,uid,a,s,seed:simulate_actions_protect_add(base,dl,lo,ra,mi,bl,b,uid,a,s,seed)
    def ev(row,uid,dl,lo,ra,mi,bl,b,s,seed):
        if row.get('channel')!='WAIVER' or not row.get('native_full_projection'):
            result=saved(row,uid,dl,lo,ra,mi,bl,b,s,seed)
            result['decision_attribution']=attribution.reconcile(result.get('simulation') or {})
            if result.get('channel')=='TRADE':
                focal_score=utility.score(result.get('simulation') or {})
                result['focal_shared_decision_utility']=focal_score
                result['focal_decision_attribution']=attribution.reconcile(result.get('simulation') or {})
                result['focal_shared_decision_utility_score']=base.sf(focal_score.get('score'))
                result['focal_shared_decision_utility_matches_team_improvement_score']=abs(base.sf(focal_score.get('score'))-base.sf(result.get('team_improvement_score')))<0.011
                cp=(result.get('simulation') or {}).get('counterparty')
                if cp:
                    cp_score=utility.score(cp)
                    result['counterparty_shared_decision_utility_score']=base.sf(cp_score.get('score'))
                    result['counterparty_shared_decision_utility']=cp_score
                    result['counterparty_decision_attribution']=attribution.reconcile(cp)
                    result['counterparty_shared_decision_utility_model_version']=cp_score.get('model_version')
                    result['counterparty_shared_decision_utility_source']='same_simulation_same_shared_utility_as_focal'
                evaluated_trade_rows.append(copy.deepcopy(result))
                if row.get('price_frontier_search_candidate'):
                    # Preserve the real GM3 utility for the dedicated frontier,
                    # but make this search-only row ineligible for broad Team
                    # Improvement recommendation/portfolio slots.
                    routed=copy.deepcopy(result)
                    routed['actionable']=False
                    routed['price_frontier_search_excluded_from_broad_ranking']=True
                    return routed
            return result
        m=list(mi); p=copy.deepcopy(m[6]); pid=str(row['target']['player_id']); native_ids={str(x) for x in (p.get('players') or {})}; p.setdefault('players',{})[pid]=copy.deepcopy(row['native_full_projection']); p['_decision_lab_projection_augmentation']={'source_model':'FSFFL-Full-Projection-Universe-1.0','added_player_ids':[pid] if pid not in native_ids else [],'native_player_count':len(native_ids),'final_player_count':len(p.get('players') or {}),'unrelated_full_universe_players_added':False}; m[6]=p; result=saved(row,uid,dl,lo,ra,tuple(m),bl,b,s,seed); result['decision_attribution']=attribution.reconcile(result.get('simulation') or {}); return result
    base.evaluate_row=ev; base.main()
    if out and out.exists():
        report=json.loads(out.read_text()); league=base.load_json(base.DATA/'league.json',{}) or {}; full,path=full_projection_doc(base,str(league.get('season') or '')); report['model_version']=MODEL_VERSION; report['projection_universe']={'model_version':full.get('model_version'),'path':str(path),'coverage':full.get('coverage') or {},'waiver_candidates_use_canonical_full_projection':True}; report.setdefault('search_summary',{})['strategic_posture']=posture; report['search_summary']['strategic_posture_changes_search_coverage_only']=True; report.setdefault('search_summary',{})['trade_packages_per_target_considered']=int(ppt); report['search_summary']['price_frontier_targets_expanded']=int(pft); report['search_summary']['price_frontier_packages_per_target_considered']=int(pfpt); report['search_summary']['targeted_adaptive_price_discovery_enabled']=bool(pft and pfpt); report.setdefault('policy',{}).update({'waiver_candidates_use_canonical_full_projection_universe':True,'hypothetical_simulator_uses_same_projection_universe_as_lineup_optimizer':True,'waiver_pre_screen_uses_fixed_cross_unit_coefficients':False,'waiver_discovery_is_scale_free_multilane':True,'trade_discovery_is_governed_multilane':True,'trade_discovery_preserves_bilateral_utility_lane':True,'trade_discovery_preserves_negotiation_fit_lane':True,'trade_discovery_preserves_seller_motivation_lane':True,'trade_discovery_preserves_target_diversity_lane':True,'trade_package_pre_screen_score_owned_by_upstream_gm3':True,'targeted_price_discovery_package_economics_owned_by_gm3':True,'targeted_price_discovery_target_selection_is_search_orchestration':True,'targeted_price_discovery_creates_new_trade_value':False,'targeted_price_discovery_rows_are_search_only_not_broad_ranking':True,'counterparty_trade_feasibility_uses_same_shared_decision_utility_as_focal':True,'team_improvement_simulation_exposes_league_reference_for_current_utility':True,'trade_rows_expose_shared_utility_attribution':True,'decision_attribution_reconciles_authoritative_shared_utility_without_rescoring':True,'seller_motivation_is_search_coverage_only':True,'negotiation_fit_is_search_coverage_only':True,'outbound_future_value_discovery_uses_inverted_existing_gm3_packages':True,'outbound_future_value_discovery_creates_new_trade_value':False,'outbound_future_value_candidates_recomputed_through_shared_decision_utility':True}); report['ranking_calibration']={'version':'shared-decision-utility-2.0','principle':'Team Improvement and Trade Decision use the same continuous primitive utility','shared_utility_model':'FSFFL-Shared-Decision-Utility-2.0','categorical_state_weights_active':False,'legacy_championship_diminishing_return_rule_active':False,'legacy_dynasty_value_guardrail_authoritative':False,'scale_status':'DATA_DERIVED_LEAGUE_RELATIVE_NO_FIXED_UNIT_CONVERSION_COEFFICIENTS','notes':'Displayed football outcomes remain raw Simulator results. Recommendation ranking uses one shared current/future/liquidity/resilience utility; acceptance remains separate. GM3 1.6 broadens discovery through governed lanes without creating a new ranking score.'}
        frontier={}
        for row in evaluated_trade_rows:
            target=row.get('target') or {}; key=(
                str(row.get('trade_direction') or 'ACQUIRE'),
                str(row.get('counterparty_user_id') or row.get('seller_user_id') or ''),
                str(target.get('asset_id') or ''),
                tuple(sorted(str(x.get('asset_id') or '') for x in (row.get('outgoing') or []))),
                tuple(sorted(str(x.get('asset_id') or '') for x in (row.get('incoming') or []))),
            )
            prior=frontier.get(key); prior_sims=((prior or {}).get('simulation') or {}).get('simulation_count',0); row_sims=(row.get('simulation') or {}).get('simulation_count',0)
            if prior is None or int(row_sims or 0)>=int(prior_sims or 0): frontier[key]=row
        report['trade_price_frontier_candidates']=sorted(list(frontier.values()),key=lambda x:base.sf(x.get('team_improvement_score')) if x.get('team_improvement_score') is not None else -1e18,reverse=True)
        report['search_summary']['trade_price_frontier_candidates_evaluated']=len(report['trade_price_frontier_candidates'])
        report['search_summary']['outbound_future_value_candidates_evaluated']=sum(
            1 for x in evaluated_trade_rows if str(x.get('trade_direction') or '')=='OUTBOUND_FUTURE_VALUE'
        )

        # Targeted frontier rows are search coverage for price discovery, not
        # additional broad single-step recommendations. Keep them in the
        # dedicated frontier output but prevent them from crowding target
        # diversity, waiver opportunities, or portfolio construction.
        def broad_row(x):
            return not bool((x or {}).get('price_frontier_search_candidate'))
        report['top_cross_channel_options']=[
            x for x in (report.get('top_cross_channel_options') or []) if broad_row(x)
        ]
        report['best_trade_options']=[
            x for x in (report.get('best_trade_options') or []) if broad_row(x)
        ]
        if report.get('recommended_action') and not broad_row(report.get('recommended_action')):
            candidates=list(report.get('top_cross_channel_options') or [])
            report['recommended_action']=copy.deepcopy(candidates[0] if candidates else report.get('hold_benchmark'))
        report['search_summary']['targeted_price_frontier_rows_excluded_from_broad_ranking']=True
        report['policy']['trade_price_frontier_uses_all_evaluated_trade_candidates']=True
        report['policy']['trade_price_frontier_candidates_preserve_gm3_utility']=True
        report['policy']['trade_price_frontier_candidates_ordered_by_gm3_utility']=True
        report['policy']['price_frontier_search_uses_upstream_gm_trade_package_curve']=True
        report['policy']['price_frontier_search_depth_is_computational_control_only']=True
        report['policy']['targeted_adaptive_price_discovery_uses_same_gm3_package_economics']=True
        out.write_text(json.dumps(report,indent=2,sort_keys=True))
if __name__=='__main__':main()
