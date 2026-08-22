#!/usr/bin/env python3
"""
FSFFL GM 3.0 — Phase-Aware Football Intelligence Builder

Creates data/football_intelligence_signals.json with an explicit NFL season phase.
Uses prior-season evidence when current-season regular-season usage should not yet exist.
The same file contract is used year-round.
"""
from __future__ import annotations
import csv, io, json, urllib.request
from datetime import datetime, timezone, date
from pathlib import Path

DATA=Path("data")
OUT=DATA/"football_intelligence_signals.json"
POSITIONS={"QB","RB","WR","TE"}

def load(path, default):
    try:
        with Path(path).open("r",encoding="utf-8") as f:return json.load(f)
    except Exception:return default

def dump(obj):
    with OUT.open("w",encoding="utf-8") as f:json.dump(obj,f,indent=2)

def season_phase(season, today=None):
    today=today or date.today()
    # Calendar-relative controller; no year-specific hard coding.
    if today.year < season: return "OFFSEASON"
    md=(today.month,today.day)
    if md < (4,1): return "POSTSEASON"
    if md < (7,15): return "OFFSEASON"
    if md < (8,1): return "TRAINING_CAMP"
    if md < (9,5): return "PRESEASON"
    if md < (10,1): return "REGULAR_SEASON_EARLY"
    if md < (12,20): return "REGULAR_SEASON"
    return "PLAYOFFS"

def weights(phase):
    return {
      "OFFSEASON":{"prior_nfl":.45,"structural":.35,"news_role":.20,"current_usage":0},
      "TRAINING_CAMP":{"prior_nfl":.35,"structural":.25,"news_role":.40,"current_usage":0},
      "PRESEASON":{"prior_nfl":.30,"structural":.20,"news_role":.30,"preseason_usage":.20,"current_usage":0},
      "REGULAR_SEASON_EARLY":{"prior_nfl":.25,"structural":.15,"news_role":.15,"current_usage":.45},
      "REGULAR_SEASON":{"prior_nfl":.10,"structural":.10,"news_role":.10,"current_usage":.70},
      "PLAYOFFS":{"prior_nfl":.10,"structural":.10,"news_role":.15,"current_usage":.65},
      "POSTSEASON":{"prior_nfl":.45,"structural":.35,"news_role":.20,"current_usage":0},
    }[phase]

def fetch_csv(url):
    req=urllib.request.Request(url,headers={"User-Agent":"FSFFL-GM30/1.0"})
    with urllib.request.urlopen(req,timeout=30) as r:
        return list(csv.DictReader(io.StringIO(r.read().decode("utf-8"))))

def nflverse_snap_url(season):
    return ("https://github.com/nflverse/nflverse-data/releases/download/"
            f"snap_counts/snap_counts_{season}.csv")

def prior_snap_evidence(season):
    # Previous completed season is useful in every phase, especially preseason.
    prior=season-1
    try: rows=fetch_csv(nflverse_snap_url(prior))
    except Exception as e:return {},str(e)
    players=load(DATA/"players.json",{}) or {}
    if isinstance(players,list):
        players={str(x.get("player_id")):x for x in players if isinstance(x,dict)}
    # nflverse name matching fallback. Sleeper IDs are not guaranteed in snap data.
    by_name={}
    for pid,p in players.items():
        if isinstance(p,dict) and p.get("full_name"):
            by_name[str(p["full_name"]).strip().lower()]=str(pid)
    accum={}
    for r in rows:
        name=str(r.get("player") or r.get("player_name") or "").strip()
        pid=by_name.get(name.lower())
        if not pid:continue
        off=float(r.get("offense_snaps") or r.get("off_snaps") or 0)
        team=float(r.get("offense_pct") or r.get("off_pct") or 0)
        x=accum.setdefault(pid,{"games":0,"offense_snaps":0.0,"pct_sum":0.0})
        x["games"]+=1;x["offense_snaps"]+=off;x["pct_sum"]+=team
    out={}
    for pid,x in accum.items():
        out[pid]={
          "source_season":prior,
          "games":x["games"],
          "offense_snaps":round(x["offense_snaps"],1),
          "offense_snap_pct":round(x["pct_sum"]/max(x["games"],1),4),
          "evidence_type":"PRIOR_REGULAR_SEASON"
        }
    return out,None

def main():
    league=load(DATA/"league.json",{}) or {}
    season=int(league.get("season") or date.today().year)
    phase=season_phase(season)
    w=weights(phase)
    prior_snaps,err=prior_snap_evidence(season)

    # Preserve manually curated/news intelligence if another process has supplied it.
    old=load(OUT,{}) or {}
    manual=old.get("manual_intelligence") or {}
    preseason=old.get("preseason_usage") or {}

    # Current-season regular usage is intentionally empty until it should exist.
    usage={}
    snaps={}
    warnings=[]
    if phase in {"REGULAR_SEASON_EARLY","REGULAR_SEASON","PLAYOFFS"}:
        try:
            current,_=prior_snap_evidence(season+1)  # helper resolves (season+1)-1 = season
            snaps=current
        except Exception as e:
            warnings.append("CURRENT_USAGE_UNAVAILABLE")
    else:
        warnings.append("CURRENT_REGULAR_SEASON_USAGE_NOT_EXPECTED")

    if err:warnings.append("PRIOR_SEASON_SNAP_FETCH_FAILED")
    if not manual:warnings.append("CAMP_NEWS_INTELLIGENCE_NOT_YET_INGESTED")
    if phase=="PRESEASON" and not preseason:warnings.append("PRESEASON_USAGE_NOT_YET_INGESTED")

    payload={
      "generated_at_utc":datetime.now(timezone.utc).isoformat(),
      "model_version":"FSFFL-GM-3.0-Football-Intelligence-v1",
      "active_season":season,
      "season_phase":phase,
      "phase_weights":w,
      "phase_interpretation":{
        "current_regular_season_usage_expected":phase in {"REGULAR_SEASON_EARLY","REGULAR_SEASON","PLAYOFFS"},
        "prior_season_evidence_expected":True,
        "camp_news_priority":phase in {"TRAINING_CAMP","PRESEASON"},
        "preseason_usage_priority":phase=="PRESEASON"
      },
      "usage_records":len(usage),
      "snap_records":len(snaps),
      "prior_snap_records":len(prior_snaps),
      "manual_intelligence_records":len(manual),
      "preseason_usage_records":len(preseason),
      "usage":usage,
      "snaps":snaps,
      "prior_snaps":prior_snaps,
      "manual_intelligence":manual,
      "preseason_usage":preseason,
      "warnings":warnings
    }
    dump(payload)
    print(f"Football Intelligence: season={season} phase={phase}")
    print(f"Prior snap evidence: {len(prior_snaps)} players")
    print(f"Current usage: {len(usage)} | current snaps: {len(snaps)}")
    if warnings:print("Warnings:",", ".join(warnings))

if __name__=="__main__":main()
