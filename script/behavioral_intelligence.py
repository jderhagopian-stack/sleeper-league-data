#!/usr/bin/env python3
"""FSFFL Behavioral Intelligence 2.0.

Separates manager behavior into two evidence layers:
1) persistent traits observed across seasons and competitive states;
2) state-conditioned traits observed when the manager was in a comparable
   competitive window.

Evidence sources: completed trades, rookie/startup drafts, waiver/free-agent
adds, FAAB activity, drops/cuts. State reconstruction never uses future
same-season results. Historical roster context is approximate where applicable.
"""
from __future__ import annotations

import importlib.util
import json
import math
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

DATA=Path('data'); SCRIPT=Path(__file__).resolve().parent
HIST=SCRIPT/'historical_state_behavior.py'
TRADE_WEIGHT=1.00; DRAFT_WEIGHT=.58; WAIVER_WEIGHT=.22; DROP_WEIGHT=.16
STATES=('elite_contender','contender','retool','rebuild')


def loadj(path, default):
    try: return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception: return default

def sf(x,d=0.0):
    try:return float(x)
    except (TypeError,ValueError):return d

def clamp(x,a=0.0,b=1.0): return max(a,min(b,x))

def histmod():
    spec=importlib.util.spec_from_file_location('bi_hist',HIST); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

@lru_cache(maxsize=1)
def players():
    raw=loadj(DATA/'players.json',{})
    if isinstance(raw,list): return {str(x.get('player_id')):x for x in raw}
    return {str(k):v for k,v in raw.items()}

def age(pid):
    p=players().get(str(pid),{}); return sf(p.get('age'),None)

def youth_score(pid):
    a=age(pid)
    if a is None:return .5
    return clamp((29.0-a)/8.0)

def pos_share(counter):
    tot=sum(counter.values()) or 1
    return {p:round(counter.get(p,0)/tot,4) for p in ('QB','RB','WR','TE')}

def classify_strength(v):
    av=abs(v)
    return 'VERY_HIGH' if av>=.65 else 'HIGH' if av>=.42 else 'MODERATE' if av>=.22 else 'LOW'

def trait(value, sample, sources):
    conf=clamp((1-math.exp(-max(0,sample)/8.0))*min(1.0,len(sources)/2.0),0,.98)
    return {'score':round(value,4),'strength':classify_strength(value),'confidence':round(conf,4),'weighted_sample':round(sample,2),'sources':sorted(sources)}

def state_for_event(hist, season, roster_id, created_utc=None):
    season=int(season or 0); rid=str(roster_id or '')
    if created_utc:
        wk=hist.completed_week_before_trade(season,created_utc)
        if wk>0:
            perf=hist.performance_signal(season,rid,wk)
            if perf:
                rel=clamp(wk/8.0); raw=.67*perf['record_percentile']+.33*perf['points_percentile']; score=.5+rel*(raw-.5)
                return hist.classify(score), clamp(.42+.055*wk,.42,.94), 'in_season_pre_event_results'
    perf=hist.performance_signal(season-1,rid,14) if season>=2023 else None
    if perf:
        raw=.70*perf['record_percentile']+.30*perf['points_percentile']; score=.5+.78*(raw-.5)
        return hist.classify(score),.64,'prior_season_anchor'
    return 'unknown',.20,'low_information'

def blank_acc():
    return {'w':0.0,'sources':set(),'pos_add':Counter(),'pos_out':Counter(),'youth_in':0.0,'youth_n':0.0,'faab':0.0,'faab_n':0.0,'pick_in':0.0,'pick_out':0.0,'large':0.0,'trade_n':0.0,'draft_n':0.0,'waiver_n':0.0,'drop_n':0.0}
def add_weight(a,w,src): a['w']+=w; a['sources'].add(src)
def consume_trade(a, side, conf=1.0):
    w=TRADE_WEIGHT*conf; add_weight(a,w,'trade'); a['trade_n']+=conf
    rp=side.get('received_players') or []; sp=side.get('sent_players') or []
    for p in rp:
        a['pos_add'][str(p.get('position') or 'UNK')]+=w; a['youth_in']+=w*youth_score(p.get('player_id')); a['youth_n']+=w
    for p in sp:a['pos_out'][str(p.get('position') or 'UNK')]+=w
    a['pick_in']+=w*len(side.get('received_picks') or []); a['pick_out']+=w*len(side.get('sent_picks') or [])
    n=len(rp)+len(sp)+len(side.get('received_picks') or [])+len(side.get('sent_picks') or [])
    if n>=4:a['large']+=w
    a['faab']+=w*(sf(side.get('faab_sent'))); a['faab_n']+=w if sf(side.get('faab_sent'))>0 else 0

def consume_draft(a,row,conf=1.0):
    w=DRAFT_WEIGHT*conf; add_weight(a,w,'draft'); a['draft_n']+=conf
    a['pos_add'][str(row.get('position') or 'UNK')]+=w; a['youth_in']+=w*youth_score(row.get('player_id')); a['youth_n']+=w

def consume_acq(a,row,conf=1.0):
    added=row.get('players_added') or []; dropped=row.get('players_dropped') or []
    if added:
        w=WAIVER_WEIGHT*conf; add_weight(a,w,'waiver'); a['waiver_n']+=conf
        for p in added:
            a['pos_add'][str(p.get('position') or 'UNK')]+=w; a['youth_in']+=w*youth_score(p.get('player_id')); a['youth_n']+=w
        bid=sf(row.get('faab_bid')); a['faab']+=w*bid; a['faab_n']+=w if bid>0 else 0
    if dropped:
        w=DROP_WEIGHT*conf; add_weight(a,w,'drop'); a['drop_n']+=conf
        for p in dropped:a['pos_out'][str(p.get('position') or 'UNK')]+=w

def finalize(a):
    ps=pos_share(a['pos_add']); po=pos_share(a['pos_out']); w=max(.01,a['w'])
    # Position affinity is relative to a neutral 25% baseline. QB gets a small
    # Superflex premium threshold to avoid labeling ordinary QB activity hoarding.
    qb=(ps['QB']-.30)/.30; wr=(ps['WR']-.25)/.25; rb=(ps['RB']-.25)/.25; te=(ps['TE']-.20)/.20
    youth=(a['youth_in']/a['youth_n']-.5)*2 if a['youth_n'] else 0
    pick=(a['pick_in']-a['pick_out'])/max(1.0,a['trade_n']*1.5)
    large=a['large']/max(.01,a['trade_n']) if a['trade_n'] else 0
    faab=(a['faab']/max(.01,a['faab_n'])-10)/20 if a['faab_n'] else 0
    sample=a['w']; src=a['sources']
    return {
      'weighted_action_sample':round(sample,2),'source_mix':{k:round(a[k],2) for k in ('trade_n','draft_n','waiver_n','drop_n')},
      'position_acquisition_share':ps,'position_exit_share':po,
      'traits':{
        'qb_accumulation':trait(clamp(qb,-1,1),sample,src),
        'wr_affinity':trait(clamp(wr,-1,1),sample,src),
        'rb_affinity':trait(clamp(rb,-1,1),sample,src),
        'te_affinity':trait(clamp(te,-1,1),sample,src),
        'youth_preference':trait(clamp(youth,-1,1),sample,src),
        'draft_pick_accumulation':trait(clamp(pick,-1,1),max(1,a['trade_n']),{'trade'} if a['trade_n'] else set()),
        'large_package_tolerance':trait(clamp(2*large-0.5,-1,1),max(1,a['trade_n']),{'trade'} if a['trade_n'] else set()),
        'faab_aggressiveness':trait(clamp(faab,-1,1),max(1,a['waiver_n']),{'waiver'} if a['waiver_n'] else set()),
      }
    }

@lru_cache(maxsize=1)
def build():
    hist=histmod(); hidx=hist.build_index(); state_rows={(r['transaction_id'],r['user_id']):r for r in hidx.get('sides',[])}
    career=defaultdict(blank_acc); stateacc=defaultdict(lambda:defaultdict(blank_acc)); names={}
    trades=[x for x in loadj(DATA/'trade_ledger.json',[]) if x.get('status')=='complete']
    for t in trades:
      for s in t.get('sides') or []:
        uid=str(s.get('user_id') or ''); names[uid]={'manager':s.get('manager'),'team_name':s.get('team_name')}; sr=state_rows.get((str(t.get('transaction_id')),uid),{}); st=sr.get('historical_state','unknown'); conf=sf(sr.get('historical_state_confidence'),.3)
        consume_trade(career[uid],s,1.0)
        if st in STATES: consume_trade(stateacc[uid][st],s,conf)
    for r in loadj(DATA/'draft_ledger.json',[]):
      uid=str(r.get('user_id') or ''); names.setdefault(uid,{'manager':r.get('manager'),'team_name':r.get('team_name')}); st,conf,_=state_for_event(hist,r.get('season'),r.get('roster_id'))
      consume_draft(career[uid],r,1.0)
      if st in STATES:consume_draft(stateacc[uid][st],r,conf)
    for r in loadj(DATA/'acquisition_ledger.json',[]):
      if r.get('status')!='complete':continue
      uid=str(r.get('user_id') or ''); names.setdefault(uid,{'manager':r.get('manager'),'team_name':r.get('team_name')}); st,conf,_=state_for_event(hist,r.get('season'),r.get('roster_id'),r.get('created_utc'))
      consume_acq(career[uid],r,1.0)
      if st in STATES:consume_acq(stateacc[uid][st],r,conf)
    owners={}
    for uid in sorted(career):
      owners[uid]={**names.get(uid,{}),'persistent':finalize(career[uid]),'by_state':{st:finalize(a) for st,a in stateacc[uid].items() if a['w']>0}}
    return {'model_version':'FSFFL-Behavioral-Intelligence-2.0','persistent_plus_state_conditioned':True,'evidence_weights':{'trade':TRADE_WEIGHT,'draft':DRAFT_WEIGHT,'waiver':WAIVER_WEIGHT,'drop':DROP_WEIGHT},'future_same_season_result_leakage_allowed':False,'owners':owners}

def owner_profile(uid):return (build().get('owners') or {}).get(str(uid),{})
def trait_score(uid,trait_name,state=None):
    p=owner_profile(uid); base=(((p.get('persistent') or {}).get('traits') or {}).get(trait_name) or {})
    st=((((p.get('by_state') or {}).get(str(state)) or {}).get('traits') or {}).get(trait_name) or {}) if state else {}
    bc=sf(base.get('confidence')); sc=sf(st.get('confidence')); sv=sf(st.get('score')); bv=sf(base.get('score'))
    blend=clamp(sc*.65,0,.65)
    return {'score':round((1-blend)*bv+blend*sv,4),'persistent_score':bv,'state_score':sv if st else None,'state_blend_weight':round(blend,4),'persistent_confidence':bc,'state_confidence':sc,'state':state}

def main():
    import argparse
    ap=argparse.ArgumentParser();ap.add_argument('--output',default='data/behavioral_intelligence.json');args=ap.parse_args();Path(args.output).write_text(json.dumps(build(),indent=2,sort_keys=True),encoding='utf-8');print(args.output)
if __name__=='__main__':main()
