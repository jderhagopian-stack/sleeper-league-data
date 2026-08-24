#!/usr/bin/env python3
"""FSFFL Behavioral Intelligence 2.0.

Two simultaneous layers: persistent manager traits across years/states and
state-conditioned variants. Evidence sources are completed trades, draft
selections, waiver/free-agent adds, FAAB, and drops/cuts. All temporal features
are evaluated as of the historical action; same-season future results are never
used for state reconstruction.
"""
from __future__ import annotations
import importlib.util,json,math
from collections import Counter,defaultdict
from functools import lru_cache
from pathlib import Path
DATA=Path('data');SCRIPT=Path(__file__).resolve().parent;HIST=SCRIPT/'historical_state_behavior.py'
TRADE_WEIGHT=1.0;DRAFT_WEIGHT=.58;WAIVER_WEIGHT=.22;DROP_WEIGHT=.16
STATES=('elite_contender','contender','retool','rebuild');CURRENT_SEASON=2026

def loadj(p,d):
    try:return json.loads(Path(p).read_text(encoding='utf-8'))
    except Exception:return d
def sf(x,d=0.0):
    try:return float(x)
    except (TypeError,ValueError):return d
def clamp(x,a=0,b=1):return max(a,min(b,x))
def histmod():
    s=importlib.util.spec_from_file_location('bi_hist',HIST);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
@lru_cache(maxsize=1)
def players():
    raw=loadj(DATA/'players.json',{});return {str(x.get('player_id')):x for x in raw} if isinstance(raw,list) else {str(k):v for k,v in raw.items()}
def age_now(pid):return sf(players().get(str(pid),{}).get('age'),None)
def age_at(pid,season):
    a=age_now(pid)
    if a is None:return None
    try:return max(18.0,a-(CURRENT_SEASON-int(season)))
    except Exception:return a
def youth_score(pid,season=CURRENT_SEASON):
    a=age_at(str(pid).replace('player:',''),season)
    if a is None:return .5
    return clamp((29-a)/8)
def pos_share(c):
    n=sum(c.values()) or 1;return {p:round(c.get(p,0)/n,4) for p in ('QB','RB','WR','TE')}
def strength(v):
    a=abs(v);return 'VERY_HIGH' if a>=.65 else 'HIGH' if a>=.42 else 'MODERATE' if a>=.22 else 'LOW'
def trait(v,n,src):
    conf=clamp((1-math.exp(-max(0,n)/8))*min(1,len(src)/2),0,.98);return {'score':round(v,4),'strength':strength(v),'confidence':round(conf,4),'weighted_sample':round(n,2),'sources':sorted(src)}
def state_for_event(hist,season,rid,created=None):
    season=int(season or 0);rid=str(rid or '')
    if created:
        wk=hist.completed_week_before_trade(season,created)
        if wk>0:
            p=hist.performance_signal(season,rid,wk)
            if p:
                rel=clamp(wk/8);raw=.67*p['record_percentile']+.33*p['points_percentile'];return hist.classify(.5+rel*(raw-.5)),clamp(.42+.055*wk,.42,.94),'in_season_pre_event_results'
    p=hist.performance_signal(season-1,rid,14) if season>=2023 else None
    if p:
        raw=.70*p['record_percentile']+.30*p['points_percentile'];return hist.classify(.5+.78*(raw-.5)),.64,'prior_season_anchor'
    return 'unknown',.20,'low_information'
def blank():return {'w':0.0,'sources':set(),'pos_add':Counter(),'pos_out':Counter(),'youth_in':0.0,'youth_n':0.0,'faab':0.0,'faab_n':0.0,'pick_in':0.0,'pick_out':0.0,'large':0.0,'trade_n':0.0,'draft_n':0.0,'waiver_n':0.0,'drop_n':0.0}
def aw(a,w,s):a['w']+=w;a['sources'].add(s)
def consume_trade(a,s,season,conf=1):
    w=TRADE_WEIGHT*conf;aw(a,w,'trade');a['trade_n']+=conf;rp=s.get('received_players') or [];sp=s.get('sent_players') or []
    for p in rp:a['pos_add'][str(p.get('position') or 'UNK')]+=w;a['youth_in']+=w*youth_score(p.get('player_id'),season);a['youth_n']+=w
    for p in sp:a['pos_out'][str(p.get('position') or 'UNK')]+=w
    a['pick_in']+=w*len(s.get('received_picks') or []);a['pick_out']+=w*len(s.get('sent_picks') or [])
    if len(rp)+len(sp)+len(s.get('received_picks') or [])+len(s.get('sent_picks') or [])>=4:a['large']+=w
    bid=sf(s.get('faab_sent'));a['faab']+=w*bid;a['faab_n']+=w if bid>0 else 0
def consume_draft(a,r,conf=1):
    w=DRAFT_WEIGHT*conf;aw(a,w,'draft');a['draft_n']+=conf;a['pos_add'][str(r.get('position') or 'UNK')]+=w;a['youth_in']+=w*youth_score(r.get('player_id'),r.get('season'));a['youth_n']+=w
def consume_acq(a,r,conf=1):
    add=r.get('players_added') or [];drop=r.get('players_dropped') or [];season=r.get('season')
    if add:
        w=WAIVER_WEIGHT*conf;aw(a,w,'waiver');a['waiver_n']+=conf
        for p in add:a['pos_add'][str(p.get('position') or 'UNK')]+=w;a['youth_in']+=w*youth_score(p.get('player_id'),season);a['youth_n']+=w
        bid=sf(r.get('faab_bid'));a['faab']+=w*bid;a['faab_n']+=w if bid>0 else 0
    if drop:
        w=DROP_WEIGHT*conf;aw(a,w,'drop');a['drop_n']+=conf
        for p in drop:a['pos_out'][str(p.get('position') or 'UNK')]+=w
def finalize(a):
    ps=pos_share(a['pos_add']);po=pos_share(a['pos_out']);sample=a['w'];src=a['sources']
    qb=(ps['QB']-.30)/.30;wr=(ps['WR']-.25)/.25;rb=(ps['RB']-.25)/.25;te=(ps['TE']-.20)/.20;y=(a['youth_in']/a['youth_n']-.5)*2 if a['youth_n'] else 0;p=(a['pick_in']-a['pick_out'])/max(1,a['trade_n']*1.5);large=a['large']/max(.01,a['trade_n']) if a['trade_n'] else 0;faab=(a['faab']/max(.01,a['faab_n'])-10)/20 if a['faab_n'] else 0
    return {'weighted_action_sample':round(sample,2),'source_mix':{k:round(a[k],2) for k in ('trade_n','draft_n','waiver_n','drop_n')},'position_acquisition_share':ps,'position_exit_share':po,'traits':{'qb_accumulation':trait(clamp(qb,-1,1),sample,src),'wr_affinity':trait(clamp(wr,-1,1),sample,src),'rb_affinity':trait(clamp(rb,-1,1),sample,src),'te_affinity':trait(clamp(te,-1,1),sample,src),'youth_preference':trait(clamp(y,-1,1),sample,src),'draft_pick_accumulation':trait(clamp(p,-1,1),max(1,a['trade_n']),{'trade'} if a['trade_n'] else set()),'large_package_tolerance':trait(clamp(2*large-.5,-1,1),max(1,a['trade_n']),{'trade'} if a['trade_n'] else set()),'faab_aggressiveness':trait(clamp(faab,-1,1),max(1,a['waiver_n']),{'waiver'} if a['waiver_n'] else set())}}
@lru_cache(maxsize=1)
def build():
    hist=histmod();h=hist.build_index();sr={(r['transaction_id'],r['user_id']):r for r in h.get('sides',[])};career=defaultdict(blank);state=defaultdict(lambda:defaultdict(blank));names={}
    for t in [x for x in loadj(DATA/'trade_ledger.json',[]) if x.get('status')=='complete']:
        season=t.get('season')
        for s in t.get('sides') or []:
            uid=str(s.get('user_id') or '');names[uid]={'manager':s.get('manager'),'team_name':s.get('team_name')};x=sr.get((str(t.get('transaction_id')),uid),{});st=x.get('historical_state','unknown');cf=sf(x.get('historical_state_confidence'),.3);consume_trade(career[uid],s,season,1)
            if st in STATES:consume_trade(state[uid][st],s,season,cf)
    for r in loadj(DATA/'draft_ledger.json',[]):
        uid=str(r.get('user_id') or '');names.setdefault(uid,{'manager':r.get('manager'),'team_name':r.get('team_name')});st,cf,_=state_for_event(hist,r.get('season'),r.get('roster_id'));consume_draft(career[uid],r,1)
        if st in STATES:consume_draft(state[uid][st],r,cf)
    for r in loadj(DATA/'acquisition_ledger.json',[]):
        if r.get('status')!='complete':continue
        uid=str(r.get('user_id') or '');names.setdefault(uid,{'manager':r.get('manager'),'team_name':r.get('team_name')});st,cf,_=state_for_event(hist,r.get('season'),r.get('roster_id'),r.get('created_utc'));consume_acq(career[uid],r,1)
        if st in STATES:consume_acq(state[uid][st],r,cf)
    owners={uid:{**names.get(uid,{}),'persistent':finalize(career[uid]),'by_state':{st:finalize(a) for st,a in state[uid].items() if a['w']>0}} for uid in sorted(career)}
    return {'model_version':'FSFFL-Behavioral-Intelligence-2.0','persistent_plus_state_conditioned':True,'historical_player_age_adjusted_to_action_season':True,'evidence_weights':{'trade':TRADE_WEIGHT,'draft':DRAFT_WEIGHT,'waiver':WAIVER_WEIGHT,'drop':DROP_WEIGHT},'future_same_season_result_leakage_allowed':False,'owners':owners}
def owner_profile(uid):return (build().get('owners') or {}).get(str(uid),{})
def trait_score(uid,name,state=None):
    p=owner_profile(uid);b=(((p.get('persistent') or {}).get('traits') or {}).get(name) or {});s=((((p.get('by_state') or {}).get(str(state)) or {}).get('traits') or {}).get(name) or {}) if state else {};sc=sf(s.get('confidence'));blend=clamp(sc*.65,0,.65);bv=sf(b.get('score'));sv=sf(s.get('score'))
    return {'score':round((1-blend)*bv+blend*sv,4),'persistent_score':bv,'state_score':sv if s else None,'state_blend_weight':round(blend,4),'persistent_confidence':sf(b.get('confidence')),'state_confidence':sc,'state':state}
def main():
    import argparse
    ap=argparse.ArgumentParser();ap.add_argument('--output',default='data/behavioral_intelligence.json');a=ap.parse_args();Path(a.output).write_text(json.dumps(build(),indent=2,sort_keys=True),encoding='utf-8');print(a.output)
if __name__=='__main__':main()
