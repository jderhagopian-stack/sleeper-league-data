#!/usr/bin/env python3
"""Build an INTERNAL-ONLY interim preseason projection source from FFToday raw stats.

Major season projection components come from FFToday and are rescored under the
active FSFFL league rules. Native V2 is retained only for players/stat components
not exposed by FFToday so coverage never drops. Native V2 remains a separate
shadow model and is not overwritten.
"""
from __future__ import annotations
import argparse, json, re, time, urllib.error, urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import run_native_vs_fftoday_historical_benchmark as fft
from build_native_preseason_projections import fsffl_score, norm_name

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data"
POSITIONS=("QB","RB","WR","TE")
STAT_TO_FS={
 "attempts":"pass_att","passing_yards":"pass_yd","passing_tds":"pass_td","interceptions":"pass_int",
 "rushing_attempts":"rush_att","rushing_yards":"rush_yd","rushing_tds":"rush_td",
 "carries":"rush_att","receptions":"rec","receiving_yards":"rec_yd","receiving_tds":"rec_td",
}
_last=[0.0]
_orig=fft.fetch_html

def write_json(path,payload):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8")

def throttled(url):
    for attempt in range(6):
        elapsed=time.monotonic()-_last[0]
        if elapsed<1.25: time.sleep(1.25-elapsed)
        try:
            out=_orig(url); _last[0]=time.monotonic(); return out
        except urllib.error.HTTPError as e:
            _last[0]=time.monotonic()
            if e.code not in {403,429} or attempt==5: raise
            time.sleep(4*(attempt+1))
    raise RuntimeError("unreachable")
fft.fetch_html=throttled

def current_projection_rows(season:int,pos:str):
    all_rows=[]; seen=set(); update_date=None
    for page in range(10):
        q=urllib.parse.urlencode({"LeagueID":1,"PosID":fft.POS_ID[pos],"Season":season,
                                  "cur_page":page,"order_by":"FName","sort_order":"ASC"})
        html=fft.fetch_html("https://www.fftoday.com/rankings/playerproj.php?"+q)
        if page==0:
            m=re.search(r"Updated:\s*(\d{1,2}/\d{1,2}/\d{4})",re.sub(r"<[^>]+>"," ",html),re.I)
            if not m: raise RuntimeError(f"{season} {pos}: update date not found")
            update_date=datetime.strptime(m.group(1),"%m/%d/%Y").date().isoformat()
        rows=fft.parse_fftoday_page(html,pos); new=0
        for r in rows:
            k=norm_name(r["player_name"])
            if k not in seen: seen.add(k); all_rows.append(r); new+=1
        if page>0 and new==0: break
    if not all_rows: raise RuntimeError(f"{season} {pos}: no rows")
    return all_rows,update_date

def main():
    p=argparse.ArgumentParser(); p.add_argument("--season",type=int,default=None)
    p.add_argument("--max-source-age-days",type=int,default=14)
    p.add_argument("--output",type=Path,default=None); a=p.parse_args()
    league=json.loads((DATA/"league.json").read_text()); season=int(a.season or league["season"])
    scoring=league.get("scoring_settings") or {}
    native_path=DATA/"simulator"/str(season)/"sources"/"native_preseason_fsffl_points.json"
    if not native_path.exists(): raise RuntimeError("Build Native V2 shadow source first")
    native=json.loads(native_path.read_text()); players={str(k):dict(v) for k,v in (native.get("players") or {}).items()}
    idx={}
    for sid,r in players.items(): idx.setdefault((norm_name(r.get("player_name","")),str(r.get("position","")).upper()),[]).append(sid)

    fft_rows={}; dates={}; fetched={}
    for pos in POSITIONS:
        rows,d=current_projection_rows(season,pos); dates[pos]=d; fetched[pos]=len(rows)
        for r in rows: fft_rows[(norm_name(r["player_name"]),pos)]=r
    date_values=set(dates.values())
    if len(date_values)!=1: raise RuntimeError(f"FFToday position update dates disagree: {dates}")
    source_date=datetime.fromisoformat(next(iter(date_values))).date()
    age=(datetime.now(timezone.utc).date()-source_date).days
    if age<0 or age>a.max_source_age_days: raise RuntimeError(f"FFToday snapshot age {age} days exceeds gate")

    matched=ambiguous=0; external_stat_players=0
    matched_ids=set()
    for key,er in fft_rows.items():
        ids=idx.get(key,[])
        if len(ids)!=1:
            ambiguous += int(len(ids)>1); continue
        sid=ids[0]; old=players[sid]; pos=key[1]
        # Start with any existing native minor components, but overwrite every
        # major component FFToday exposes.
        stats=dict(old.get("projected_stats") or {})
        native_raw=old.get("projected_stats_native") or {}
        for k,v in native_raw.items():
            fs=STAT_TO_FS.get(k)
            if fs and fs not in stats: stats[fs]=v
        used=[]
        for stat,_ in fft.LAYOUT[pos]:
            if stat in er and stat in STAT_TO_FS:
                stats[STAT_TO_FS[stat]]=float(er[stat]); used.append(stat)
        # Re-score from raw football stats under FSFFL rules; FFToday FPts ignored.
        canonical={
          "passing_yards":stats.get("pass_yd",0),"passing_tds":stats.get("pass_td",0),
          "interceptions":stats.get("pass_int",0),"rushing_yards":stats.get("rush_yd",0),
          "rushing_tds":stats.get("rush_td",0),"receptions":stats.get("rec",0),
          "receiving_yards":stats.get("rec_yd",0),"receiving_tds":stats.get("rec_td",0)
        }
        points=max(0.0,fsffl_score(canonical,scoring))
        old.update({"projected_stats":stats,"fsffl_projected_points":round(points,3),
                    "fsffl_projected_ppg":round(points/17.0,3),"games_projected":17.0,
                    "source":"FFToday Interim Raw Stats","interim_projection_source":"FFToday",
                    "interim_source_date":source_date.isoformat(),"interim_external_stats":sorted(used),
                    "interim_native_component_fallback":True})
        players[sid]=old; matched+=1; external_stat_players+=1; matched_ids.add(sid)

    for sid,r in players.items():
        if sid not in matched_ids:
            r["interim_projection_source"]="FSFFL Native V2 fallback"
            r["source"]="FSFFL Native V2 (interim coverage fallback)"
            r["interim_native_component_fallback"]=True

    out=a.output or DATA/"simulator"/str(season)/"sources"/"interim_preseason_fsffl_points.json"
    payload={"season":str(season),"generated_at_utc":datetime.now(timezone.utc).isoformat(),
      "source":"FFToday raw-stat interim bridge with Native V2 coverage fallback",
      "players":players,
      "audit":{"total_players":len(players),"fftoday_matched_players":matched,
               "native_fallback_players":len(players)-matched,"fftoday_rows_by_position":fetched,
               "ambiguous_matches":ambiguous,"fftoday_source_dates":dates,
               "fftoday_snapshot_age_days":age,
               "external_projection_coverage_pct":round(100*matched/max(1,len(players)),2)},
      "governance":{"deployment_scope":"INTERNAL_PRIVATE_INTERIM_ONLY",
                    "commercial_use_approved":False,
                    "external_projection_values_used":True,
                    "external_fantasy_points_used":False,
                    "native_v2_shadow_retained":True,
                    "production_replacement_target":"FSFFL Native V3 or commercially cleared projection source",
                    "important_note":"Public access is not treated as commercial reuse permission."}}
    write_json(out,payload)
    fallback_names=sorted([r.get("player_name") for sid,r in players.items() if sid not in matched_ids])
    print(json.dumps({"status":"PASS","season":season,"source_date":source_date.isoformat(),
                      "total_players":len(players),"fftoday_matched_players":matched,
                      "native_fallback_players":len(players)-matched,
                      "fallback_players":fallback_names,
                      "coverage_pct":payload["audit"]["external_projection_coverage_pct"]},indent=2))

if __name__=="__main__": main()
