#!/usr/bin/env python3
"""
FSFFL GM 3.0 — NFL Emerging Value Intelligence v1

Scans the entire active fantasy-relevant NFL player universe, not just FSFFL rosters.
Uses existing Sleeper/player metadata, FSFFL market values, league ownership, and
football-intelligence signals. Missing evidence reduces confidence; it never becomes
negative evidence.

Output:
  data/gm/emerging_value.json
"""
from __future__ import annotations
import json, math
from datetime import datetime, timezone
from pathlib import Path

DATA=Path("data")
OUT=DATA/"gm"
MODEL="FSFFL-GM-3.0-Emerging-Value-v1"
POSITIONS={"QB","RB","WR","TE"}

def load(path, default):
    try:
        with Path(path).open("r",encoding="utf-8") as f:return json.load(f)
    except (OSError,json.JSONDecodeError):return default

def num(x, default=None):
    try:
        if x is None or isinstance(x,bool): return default
        v=float(x)
        return v if math.isfinite(v) else default
    except (TypeError,ValueError): return default

def clamp(x,lo=0.0,hi=1.0): return max(lo,min(hi,x))

def first(d,*keys,default=None):
    for k in keys:
        if isinstance(d,dict) and d.get(k) is not None:return d.get(k)
    return default

def roster_owners():
    out={}
    for r in load(DATA/"rosters.json",[]) or []:
        rid=r.get("roster_id")
        for pid in (r.get("players") or []):
            out[str(pid)]=rid
    return out

def values_by_player():
    payload=load(DATA/"fsffl_asset_values.json",{}) or {}
    rows=payload.get("players",[]) if isinstance(payload,dict) else []
    return {str(x.get("player_id")):x for x in rows if x.get("player_id") is not None}

def intelligence():
    x=load(DATA/"football_intelligence_signals.json",{}) or {}
    return x, x.get("usage") or {}, x.get("snaps") or {}, x.get("manual_intelligence") or {}

def player_universe():
    raw=load(DATA/"players.json",{}) or {}
    if isinstance(raw,list):
        raw={str(x.get("player_id")):x for x in raw if isinstance(x,dict)}
    return raw

def age_curve(pos,age):
    if age is None:return None
    ideal={"QB":25.0,"RB":22.5,"WR":23.5,"TE":24.5}.get(pos,24)
    fade={"QB":37.0,"RB":29.0,"WR":31.0,"TE":32.0}.get(pos,31)
    if age<=ideal:return 1.0
    return clamp(1-(age-ideal)/(fade-ideal))

def pedigree(p):
    pick=num(first(p,"draft_pick","draft_pick_number"))
    rnd=num(first(p,"draft_round"))
    if pick is not None:return clamp(1-(pick-1)/256)
    if rnd is not None:return clamp(1-(rnd-1)/7)
    return None

def market_value(v):
    return num(first(v,"market_value","value","fsffl_value","dynasty_value","ktc_value"))

def normalize_market(rows):
    vals=[market_value(v) for v in rows.values()]
    vals=[x for x in vals if x is not None and x>=0]
    if not vals:return {}
    lo,hi=min(vals),max(vals)
    span=max(hi-lo,1)
    return {pid:clamp((market_value(v)-lo)/span) for pid,v in rows.items()
            if market_value(v) is not None}

def usage_features(u,s):
    # Flexible schema: consume whichever normalized/raw fields exist.
    snap=num(first(s,"snap_share","offense_snap_pct","offensive_snap_pct"))
    route=num(first(u,"route_participation","route_share","routes_pct"))
    target=num(first(u,"target_share","tgt_share"))
    touch=num(first(u,"opportunity_share","touch_share","carry_share"))
    vals=[]
    for x in (snap,route,target,touch):
        if x is None:continue
        if x>1:x/=100
        vals.append(clamp(x))
    return (sum(vals)/len(vals) if vals else None), len(vals)

def manual_features(m):
    if not isinstance(m,dict):return None,[],0
    score=0.5; evidence=[]; n=0
    signals={
        "depth_chart_rise":0.18,"starter_reps":0.16,"camp_buzz":0.10,
        "preseason_role":0.12,"injury_opportunity":0.16,"coach_praise":0.07,
        "depth_chart_fall":-0.18,"injury_concern":-0.14,"role_loss":-0.20,
    }
    for k,w in signals.items():
        v=m.get(k)
        if v:
            score+=w; evidence.append(k); n+=1
    return clamp(score),evidence,n

def classify(row):
    tags=[]; direction="MONITOR"
    young=row["age_curve"]
    ped=row["pedigree"]
    usage=row["usage_score"]
    mkt=row["market_score"]
    intel=row["manual_score"]
    rostered=row["fsffl_rostered"]

    latent=sum(x for x in [young,ped,usage,intel] if x is not None)
    n=sum(x is not None for x in [young,ped,usage,intel])
    latent=latent/n if n else 0.5
    price=mkt if mkt is not None else 0.35
    gap=latent-price
    row["latent_value_score"]=round(latent*100,1)
    row["market_mispricing_score"]=round(gap*100,1)

    if gap>=0.28 and latent>=0.64:
        tags.append("HIDDEN_GEM")
    if usage is not None and usage>=0.58 and young is not None and young>=0.65 and gap>=0.10:
        tags.append("BREAKOUT_CANDIDATE")
    if not rostered and latent>=0.60 and gap>=0.18:
        tags.append("WAIVER_TARGET")
    if rostered and price<=0.42 and latent>=0.62 and gap>=0.18:
        tags.append("BUY_LOW")
    if young is not None and young>=0.72 and ped is not None and ped>=0.48 and price<=0.35:
        tags.append("DYNASTY_STASH")
    if intel is not None and intel>=0.68 and price<=0.50:
        tags.append("ROLE_INFLECTION")
    if price>=0.68 and latent<=0.48 and price-latent>=0.18:
        tags.append("FRAGILE_VALUE")
    if price>=0.58 and latent<=0.42:
        tags.append("VALUE_TRAP_RISK")

    acquire={"HIDDEN_GEM","BREAKOUT_CANDIDATE","WAIVER_TARGET","BUY_LOW","DYNASTY_STASH","ROLE_INFLECTION"}
    sell={"FRAGILE_VALUE","VALUE_TRAP_RISK"}
    if any(x in tags for x in sell): direction="SELL_OR_AVOID"
    elif "WAIVER_TARGET" in tags: direction="ADD"
    elif any(x in tags for x in acquire): direction="ACQUIRE"
    return tags,direction

def main():
    players=player_universe()
    owners=roster_owners()
    vals=values_by_player()
    market_norm=normalize_market(vals)
    fi,usage,snaps,manual=intelligence()
    rows=[]

    for pid,p in players.items():
        if not isinstance(p,dict):continue
        pos=str(first(p,"position",default="")).upper()
        if pos not in POSITIONS:continue
        active=p.get("active")
        if active is False:continue
        name=first(p,"full_name","name")
        if not name or name==pid:continue
        age=num(p.get("age"))
        # Keep the universe broad but fantasy-relevant; old low-information veterans
        # are not emerging-value candidates unless market/usage evidence exists.
        v=vals.get(str(pid),{})
        u=usage.get(str(pid),{}) if isinstance(usage,dict) else {}
        s=snaps.get(str(pid),{}) if isinstance(snaps,dict) else {}
        m=manual.get(str(pid),{}) if isinstance(manual,dict) else {}
        uscore,usage_n=usage_features(u,s)
        mscore,manual_evidence,manual_n=manual_features(m)
        if manual_n==0:mscore=None

        row={
            "player_id":str(pid),"name":name,"position":pos,
            "nfl_team":first(p,"team",default=first(v,"nfl_team")),
            "age":age,
            "years_exp":num(first(p,"years_exp","experience")),
            "draft_round":num(p.get("draft_round")),
            "draft_pick":num(p.get("draft_pick")),
            "fsffl_rostered":str(pid) in owners,
            "fsffl_roster_id":owners.get(str(pid)),
            "age_curve":age_curve(pos,age),
            "pedigree":pedigree(p),
            "market_value":market_value(v),
            "market_score":market_norm.get(str(pid)),
            "usage_score":uscore,
            "manual_score":mscore,
            "manual_evidence":manual_evidence,
        }

        evidence_fields=sum(x is not None for x in
            [row["age_curve"],row["pedigree"],row["market_score"],row["usage_score"],row["manual_score"]])
        row["evidence_coverage"]=round(evidence_fields/5,2)
        row["football_evidence_coverage"]=round((usage_n+manual_n)/max(usage_n+manual_n,4),2) if (usage_n+manual_n) else 0.0
        tags,direction=classify(row)
        row["signals"]=tags
        row["direction"]=direction

        # Confidence is explicitly evidence-based, not score-based.
        cov=row["evidence_coverage"]
        fcov=row["football_evidence_coverage"]
        conf=0.70*cov+0.30*fcov
        row["confidence_score"]=round(conf*100,1)
        row["confidence_grade"]="A" if conf>=.85 else "B" if conf>=.68 else "C" if conf>=.50 else "D"

        if tags:
            rows.append(row)

    rows.sort(key=lambda r:(r["market_mispricing_score"],r["confidence_score"]),reverse=True)
    buckets={}
    for r in rows:
        for tag in r["signals"]:buckets.setdefault(tag,[]).append(r["player_id"])

    OUT.mkdir(parents=True,exist_ok=True)
    payload={
        "generated_at_utc":datetime.now(timezone.utc).isoformat(),
        "model_version":MODEL,
        "scope":"ALL_ACTIVE_QB_RB_WR_TE",
        "player_universe_count":sum(
            1 for p in players.values()
            if isinstance(p,dict) and str(p.get("position","")).upper() in POSITIONS and p.get("active") is not False
        ),
        "candidate_count":len(rows),
        "source_coverage":{
            "fsffl_market_players":len(vals),
            "usage_records":int(fi.get("usage_records") or 0),
            "snap_records":int(fi.get("snap_records") or 0),
            "manual_intelligence_records":int(fi.get("manual_intelligence_records") or 0),
        },
        "warnings":[x for x,cond in [
            ("FOOTBALL_USAGE_EMPTY",int(fi.get("usage_records") or 0)==0 and int(fi.get("snap_records") or 0)==0),
            ("MANUAL_INTELLIGENCE_EMPTY",int(fi.get("manual_intelligence_records") or 0)==0),
        ] if cond],
        "signal_counts":{k:len(v) for k,v in buckets.items()},
        "candidates":rows,
    }
    dump=OUT/"emerging_value.json"
    with dump.open("w",encoding="utf-8") as f:json.dump(payload,f,indent=2)
    print(f"Emerging Value: {len(rows)} candidates from {payload['player_universe_count']} active fantasy players -> {dump}")
    if payload["warnings"]:print("Warnings:",", ".join(payload["warnings"]))

if __name__=="__main__":main()
