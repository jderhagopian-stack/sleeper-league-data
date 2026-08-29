#!/usr/bin/env python3
"""Replace provisional external no-history means with native role/age forecasts.

This postprocessor keeps the existing Sleeper-ID/player universe for compatibility,
but no projection value from the legacy external preseason file survives. Players
without a veteran Native V2 forecast receive a separately validated first-year /
no-prior-history model trained on historical entrants using only opening role and
age metadata.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import build_native_preseason_projections as base
from native_projection_challenger import RidgeModel, choose_alpha_temporally
from run_native_no_history_role_benchmark import FEATURES, entrant_rows
from run_native_projection_core_context_benchmark import fetch_players
from run_native_projection_nflverse_benchmark import TARGETS

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data'


def norm_name(value:str)->str:
    return base.norm_name(value)


def current_role_snapshot(season:int,as_of:datetime)->dict:
    rows=base.fetch_depth(season)
    eligible=[r for r in rows if r.get('dt') and base.iso_dt(str(r['dt']))<=as_of]
    if not eligible: return {}
    latest=max(base.iso_dt(str(r['dt'])) for r in eligible)
    snap=[r for r in eligible if base.iso_dt(str(r['dt']))==latest]
    out={}
    for r in snap:
        pos=str(r.get('pos_abb') or r.get('pos_name') or '').upper().strip()
        if pos not in TARGETS: continue
        name=str(r.get('player_name') or '').strip(); pid=str(r.get('gsis_id') or '').strip()
        if not name: continue
        rank=max(1.0,base.fnum(r.get('pos_rank'),9.0))
        key=(norm_name(name),pos)
        prev=out.get(key)
        if prev is None or rank<prev['rank']:
            out[key]={'rank':rank,'gsis_id':pid,'team':str(r.get('team') or '').strip(),'snapshot':latest.isoformat()}
    return out


def age_features(pid:str,season:int,players:dict)->tuple[int,float,float]:
    meta=players.get(pid,{}) if pid else {}
    birth=str(meta.get('birth_date') or '')
    if not birth: return 0,0.0,0.0
    try:
        born=date.fromisoformat(birth[:10]); age=(date(season,9,1)-born).days/365.2425
        if not (17<=age<=50): return 0,0.0,0.0
        return 1,age,age*age
    except ValueError:
        return 0,0.0,0.0


def fit_models(rows:list[dict])->dict:
    models={}
    for pos in TARGETS:
        train=[r for r in rows if r['position']==pos]
        models[pos]={}
        for target in TARGETS[pos]:
            alpha,_=choose_alpha_temporally(train,FEATURES,target)
            models[pos][target]=RidgeModel(alpha).fit([[float(r[f]) for f in FEATURES] for r in train],[float(r[target]) for r in train])
    return models


def feature_row(role:dict|None,pid:str,season:int,players:dict)->dict:
    rank=float(role['rank']) if role else 9.0
    age_ok,age,age_sq=age_features(pid,season,players)
    return {
        'opening_role_available':int(role is not None),
        'opening_is_first_team':int(bool(role and rank==1.0)),
        'opening_depth_rank':min(rank,4.0) if role else 4.0,
        'age_available':age_ok,'target_age':age,'target_age_sq':age_sq,
    }


def nativeize(season:int=2026,as_of:datetime|None=None)->dict:
    as_of=as_of or datetime.now(timezone.utc)
    path=DATA/'simulator'/str(season)/'sources'/'native_preseason_fsffl_points.json'
    payload=json.loads(path.read_text(encoding='utf-8'))
    players_out=payload.get('players') or {}
    historical=entrant_rows(2017,2024); models=fit_models(historical); meta=fetch_players(); roles=current_role_snapshot(season,as_of)
    scoring=(json.loads((DATA/'league.json').read_text(encoding='utf-8')).get('scoring_settings') or {})
    replaced=0; role_matched=0
    external_keys={'razzball_half_ppr_points_reference','razzball_half_ppr_ppg_reference','preseason_ecr','expert_rank_sd','projected_stats'}
    for sid,row in players_out.items():
        if str(row.get('source'))=='FSFFL Native V2':
            continue
        pos=str(row.get('position') or '').upper()
        if pos not in models: continue
        role=roles.get((norm_name(row.get('player_name')),pos)); role_matched+=int(role is not None)
        pid=str((role or {}).get('gsis_id') or '')
        f=feature_row(role,pid,season,meta); x=[[float(f[k]) for k in FEATURES]]
        raw={}
        for target,model in models[pos].items():
            raw[target.removeprefix('next_')]=round(max(0.0,float(model.predict(x)[0])),3)
        points=max(0.0,base.fsffl_score(raw,scoring))
        for k in external_keys: row.pop(k,None)
        row.update({
            'fsffl_projected_points':round(points,3),'fsffl_projected_ppg':round(points/17.0,3),'games_projected':17.0,
            'projected_stats_native':raw,'source':'FSFFL Native V2 No-History','native_model_version':'FSFFL-Native-V2-no-history-role-age',
            'native_gsis_id':pid or None,'native_role_team':(role or {}).get('team'),'native_role_snapshot':(role or {}).get('snapshot'),
            'fallback_source_retained':False,'no_history_role_matched':bool(role),
        })
        replaced+=1
    audit=payload.setdefault('audit',{})
    veteran=int(audit.get('native_players') or 0)
    audit.update({'native_veteran_players':veteran,'native_no_history_players':replaced,'native_no_history_role_matched':role_matched,'native_players':veteran+replaced,'fallback_players':0,'native_coverage_pct':round(100*(veteran+replaced)/max(1,len(players_out)),2)})
    payload['source']='FSFFL Native V2: veteran role-aware raw-stat model plus validated native no-history role/age model'
    gov=payload.setdefault('governance',{})
    gov.update({'external_projection_values_used':False,'fallback_is_provisional':False,'no_history_model_production_promoted':True,'current_depth_role_source_requires_commercial_replacement_or_clearance':True})
    path.write_text(json.dumps(payload,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    print(json.dumps({'status':'PASS','season':season,'native_veteran_players':veteran,'native_no_history_players':replaced,'role_matched_no_history':role_matched,'total_players':len(players_out),'native_projection_value_coverage_pct':audit['native_coverage_pct']},indent=2))
    return payload

if __name__=='__main__': nativeize()
