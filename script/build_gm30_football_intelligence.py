#!/usr/bin/env python3
"""
FSFFL GM 3.0 — Phase-Aware Football Intelligence Builder v2

Year-round controller:
- Prior completed NFL snap evidence
- Current regular-season usage when appropriate
- Structured current PRESEASON box-score role evidence when in preseason

Preseason source:
ESPN public scoreboard/summary JSON. We use measurable box-score role signals:
starter designation, pass-attempt share, carry share, target share, touch share,
games played, and latest-game trend.

We DO NOT fabricate route participation or true snap share when unavailable.

Output:
  data/football_intelligence_signals.json
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone, date
from pathlib import Path

DATA=Path("data")
OUT=DATA/"football_intelligence_signals.json"
POSITIONS={"QB","RB","WR","TE"}

ESPN_SCOREBOARD=(
    "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
    "?dates={season}&seasontype=1&week={week}&limit=100"
)
ESPN_SUMMARY=(
    "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={event_id}"
)

def load(path, default):
    try:
        with Path(path).open("r",encoding="utf-8") as f:return json.load(f)
    except Exception:return default

def dump(obj):
    with OUT.open("w",encoding="utf-8") as f:json.dump(obj,f,indent=2)

def season_phase(season, today=None):
    today=today or date.today()
    if today.year < season:return "OFFSEASON"
    md=(today.month,today.day)
    if md < (4,1):return "POSTSEASON"
    if md < (7,15):return "OFFSEASON"
    if md < (8,1):return "TRAINING_CAMP"
    if md < (9,5):return "PRESEASON"
    if md < (10,1):return "REGULAR_SEASON_EARLY"
    if md < (12,20):return "REGULAR_SEASON"
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

def fetch_bytes(url,timeout=30):
    req=urllib.request.Request(
        url,
        headers={
            "User-Agent":"FSFFL-GM30/2.0",
            "Accept":"application/json,text/csv,*/*",
        },
    )
    with urllib.request.urlopen(req,timeout=timeout) as r:return r.read()

def fetch_json(url):
    return json.loads(fetch_bytes(url).decode("utf-8"))

def fetch_csv(url):
    return list(csv.DictReader(io.StringIO(fetch_bytes(url).decode("utf-8"))))

def nflverse_snap_url(season):
    return (
        "https://github.com/nflverse/nflverse-data/releases/download/"
        f"snap_counts/snap_counts_{season}.csv"
    )

def norm_name(x):
    x=str(x or "").lower()
    x=re.sub(r"[^a-z0-9 ]+","",x)
    return re.sub(r"\s+"," ",x).strip()

def fnum(x,default=0.0):
    try:
        if x is None or isinstance(x,bool):return default
        v=float(str(x).replace("%","").strip())
        return v if math.isfinite(v) else default
    except Exception:return default

def player_map():
    players=load(DATA/"players.json",{}) or {}
    if isinstance(players,list):
        players={str(x.get("player_id")):x for x in players if isinstance(x,dict)}
    return players

def prior_snap_evidence(season):
    prior=season-1
    try:rows=fetch_csv(nflverse_snap_url(prior))
    except Exception as e:return {},str(e)
    players=player_map()
    by_name={}
    for pid,p in players.items():
        if isinstance(p,dict) and p.get("full_name"):
            by_name[norm_name(p["full_name"])]=str(pid)
    accum={}
    for r in rows:
        pid=by_name.get(norm_name(r.get("player") or r.get("player_name")))
        if not pid:continue
        off=fnum(r.get("offense_snaps") or r.get("off_snaps"))
        pct=fnum(r.get("offense_pct") or r.get("off_pct"))
        x=accum.setdefault(pid,{"games":0,"offense_snaps":0.0,"pct_sum":0.0})
        x["games"]+=1;x["offense_snaps"]+=off;x["pct_sum"]+=pct
    out={}
    for pid,x in accum.items():
        out[pid]={
          "source_season":prior,
          "games":x["games"],
          "offense_snaps":round(x["offense_snaps"],1),
          "offense_snap_pct":round(x["pct_sum"]/max(x["games"],1),4),
          "evidence_type":"PRIOR_REGULAR_SEASON",
        }
    return out,None

def stat_value_map(category):
    """Return athlete rows with statistic labels mapped to values."""
    labels=category.get("labels") or category.get("names") or []
    out=[]
    for a in category.get("athletes") or []:
        stats=a.get("stats") or []
        mapped={}
        for i,val in enumerate(stats):
            key=str(labels[i]).strip().upper() if i<len(labels) else f"STAT_{i}"
            mapped[key]=val
        out.append((a,mapped))
    return out

def int_stat(m,*keys):
    for k in keys:
        if k in m and m[k] not in (None,""):
            raw=str(m[k]).split("/")[0].replace(",","").strip()
            try:return int(float(raw))
            except Exception:pass
    return 0

def passing_attempts(m):
    # ESPN often provides C/ATT as a single field.
    for k in ("C/ATT","CMP/ATT","COMP/ATT"):
        if k in m:
            parts=str(m[k]).split("/")
            if len(parts)>=2:
                try:return int(float(parts[1]))
                except Exception:pass
    return int_stat(m,"ATT","PASS ATT")

def preseason_events(season):
    events={}
    errors=[]
    # NFL preseason is normally represented across ESPN weeks 1-4 (HOF included
    # in some seasons). Dedupe by event id.
    for week in range(1,5):
        try:
            payload=fetch_json(ESPN_SCOREBOARD.format(season=season,week=week))
            for e in payload.get("events") or []:
                eid=str(e.get("id") or "")
                if eid:events[eid]=e
        except Exception as e:
            errors.append(f"ESPN_PRESEASON_SCOREBOARD_WEEK_{week}_FAILED")
    return events,errors

def extract_preseason_usage(season):
    players=player_map()
    by_name={}
    for pid,p in players.items():
        if not isinstance(p,dict):continue
        name=p.get("full_name") or p.get("name")
        if name:by_name.setdefault(norm_name(name),[]).append((str(pid),p))

    events,event_errors=preseason_events(season)
    records=defaultdict(lambda:{
        "games":0,"starter_games":0,
        "pass_attempts":0,"team_pass_attempts":0,
        "carries":0,"team_carries":0,
        "targets":0,"team_targets":0,
        "receptions":0,"team_receptions":0,
        "touches":0,"team_touches":0,
        "game_log":[],
    })
    summary_errors=[]
    completed=0

    for eid,event in events.items():
        status=((event.get("status") or {}).get("type") or {})
        if not status.get("completed"):
            continue
        completed+=1
        try:
            summary=fetch_json(ESPN_SUMMARY.format(event_id=urllib.parse.quote(eid)))
        except Exception:
            summary_errors.append(eid)
            continue

        for team_block in ((summary.get("boxscore") or {}).get("players") or []):
            team=((team_block.get("team") or {}).get("abbreviation") or "").upper()
            # First pass: collect all per-player game stats and team denominators.
            game={}
            for category in team_block.get("statistics") or []:
                cname=str(category.get("name") or category.get("displayName") or "").lower()
                for athlete_row,m in stat_value_map(category):
                    athlete=athlete_row.get("athlete") or {}
                    name=athlete.get("displayName") or athlete.get("fullName")
                    if not name:continue
                    nn=norm_name(name)
                    g=game.setdefault(nn,{
                        "name":name,"starter":False,"pass_attempts":0,
                        "carries":0,"targets":0,"receptions":0,
                    })
                    g["starter"]=g["starter"] or bool(athlete_row.get("starter"))
                    if "passing" in cname:
                        g["pass_attempts"]+=passing_attempts(m)
                    elif "rushing" in cname:
                        g["carries"]+=int_stat(m,"CAR","ATT","RUSH ATT")
                    elif "receiv" in cname:
                        g["targets"]+=int_stat(m,"TGTS","TGT","TARGETS")
                        g["receptions"]+=int_stat(m,"REC","RECEPTIONS")

            team_pa=sum(x["pass_attempts"] for x in game.values())
            team_car=sum(x["carries"] for x in game.values())
            team_tgt=sum(x["targets"] for x in game.values())
            team_rec=sum(x["receptions"] for x in game.values())
            team_touch=team_car+team_rec

            for nn,g in game.items():
                matches=by_name.get(nn) or []
                if not matches:continue
                # Prefer same team when Sleeper team is populated.
                same=[x for x in matches if str((x[1].get("team") or "")).upper()==team]
                pid,p=(same or matches)[0]
                pos=str(p.get("position") or "").upper()
                if pos not in POSITIONS:continue

                r=records[pid]
                r["games"]+=1
                r["starter_games"]+=1 if g["starter"] else 0
                r["pass_attempts"]+=g["pass_attempts"]
                r["team_pass_attempts"]+=team_pa
                r["carries"]+=g["carries"]
                r["team_carries"]+=team_car
                r["targets"]+=g["targets"]
                r["team_targets"]+=team_tgt
                r["receptions"]+=g["receptions"]
                r["team_receptions"]+=team_rec
                r["touches"]+=g["carries"]+g["receptions"]
                r["team_touches"]+=team_touch
                r["game_log"].append({
                    "event_id":eid,
                    "starter":bool(g["starter"]),
                    "pass_attempts":g["pass_attempts"],
                    "carries":g["carries"],
                    "targets":g["targets"],
                    "receptions":g["receptions"],
                    "team_pass_attempts":team_pa,
                    "team_carries":team_car,
                    "team_targets":team_tgt,
                    "team_touches":team_touch,
                })

    out={}
    for pid,r in records.items():
        p=players.get(pid,{})
        pos=str(p.get("position") or "").upper()
        games=max(r["games"],1)
        starter_rate=r["starter_games"]/games
        pass_share=r["pass_attempts"]/r["team_pass_attempts"] if r["team_pass_attempts"] else 0.0
        carry_share=r["carries"]/r["team_carries"] if r["team_carries"] else 0.0
        target_share=r["targets"]/r["team_targets"] if r["team_targets"] else 0.0
        touch_share=r["touches"]/r["team_touches"] if r["team_touches"] else 0.0

        latest=r["game_log"][-1] if r["game_log"] else {}
        latest_share=0.0
        if pos=="QB" and latest.get("team_pass_attempts"):
            latest_share=latest["pass_attempts"]/latest["team_pass_attempts"]
        elif pos=="RB" and latest.get("team_touches"):
            latest_share=(latest["carries"]+latest["receptions"])/latest["team_touches"]
        elif pos in {"WR","TE"} and latest.get("team_targets"):
            latest_share=latest["targets"]/latest["team_targets"]

        # Meaningful role signal: conservative thresholds. Merely appearing in a
        # preseason box score is not enough to create a catalyst.
        meaningful=False
        strength=0.0
        reasons=[]
        if starter_rate>=0.50:
            meaningful=True;strength=max(strength,0.82);reasons.append("STARTER_DESIGNATION")
        if pos=="QB" and r["pass_attempts"]>=8 and pass_share>=0.35:
            meaningful=True;strength=max(strength,min(0.90,0.55+pass_share*0.35));reasons.append("PASS_ATTEMPT_SHARE")
        if pos=="RB" and (r["carries"]+r["receptions"])>=6 and touch_share>=0.18:
            meaningful=True;strength=max(strength,min(0.90,0.55+touch_share*0.60));reasons.append("TOUCH_SHARE")
        if pos in {"WR","TE"} and r["targets"]>=3 and target_share>=0.16:
            meaningful=True;strength=max(strength,min(0.90,0.55+target_share*0.70));reasons.append("TARGET_SHARE")
        if latest.get("starter"):
            meaningful=True;strength=max(strength,0.86);reasons.append("LATEST_GAME_STARTER")

        if not meaningful:
            continue

        out[pid]={
            "source_season":season,
            "source":"ESPN_PRESEASON_BOXSCORE",
            "evidence_type":"STRUCTURED_PRESEASON_USAGE",
            "games":r["games"],
            "starter_games":r["starter_games"],
            "starter_rate":round(starter_rate,4),
            "pass_attempts":r["pass_attempts"],
            "pass_attempt_share":round(pass_share,4),
            "carries":r["carries"],
            "carry_share":round(carry_share,4),
            "targets":r["targets"],
            "target_share":round(target_share,4),
            "receptions":r["receptions"],
            "touches":r["touches"],
            "touch_share":round(touch_share,4),
            "latest_game_role_share":round(latest_share,4),
            "latest_game_starter":bool(latest.get("starter")),
            "meaningful_role_signal":True,
            "signal_strength":round(strength,3),
            "signal_reasons":sorted(set(reasons)),
            "limitations":[
                "BOX_SCORE_ROLE_EVIDENCE_NOT_TRUE_SNAP_SHARE",
                "ROUTE_PARTICIPATION_NOT_AVAILABLE",
            ],
        }

    diagnostics={
        "events_found":len(events),
        "completed_events":completed,
        "summary_failures":len(summary_errors),
        "players_with_any_boxscore_record":len(records),
        "players_with_meaningful_usage_signal":len(out),
    }
    warnings=list(event_errors)
    if summary_errors:warnings.append("SOME_ESPN_PRESEASON_SUMMARIES_FAILED")
    if not events:warnings.append("NO_ESPN_PRESEASON_EVENTS_FOUND")
    if completed and not out:warnings.append("NO_MEANINGFUL_PRESEASON_USAGE_SIGNALS")
    return out,diagnostics,warnings

def main():
    league=load(DATA/"league.json",{}) or {}
    season=int(league.get("season") or date.today().year)
    phase=season_phase(season)
    w=weights(phase)
    prior_snaps,err=prior_snap_evidence(season)

    old=load(OUT,{}) or {}
    manual=old.get("manual_intelligence") or {}

    usage={}
    snaps={}
    warnings=[]
    preseason={}
    preseason_diag={}

    if phase=="PRESEASON":
        preseason,preseason_diag,pre_warnings=extract_preseason_usage(season)
        warnings.extend(pre_warnings)
    elif phase in {"TRAINING_CAMP","OFFSEASON","POSTSEASON"}:
        preseason={}
    else:
        # Preserve same-season preseason evidence as context after Week 1 if it
        # already exists in the file, but regular-season usage dominates.
        preseason=old.get("preseason_usage") or {}

    if phase in {"REGULAR_SEASON_EARLY","REGULAR_SEASON","PLAYOFFS"}:
        try:
            current,_=prior_snap_evidence(season+1)
            snaps=current
        except Exception:
            warnings.append("CURRENT_USAGE_UNAVAILABLE")
    else:
        warnings.append("CURRENT_REGULAR_SEASON_USAGE_NOT_EXPECTED")

    if err:warnings.append("PRIOR_SEASON_SNAP_FETCH_FAILED")
    if not manual:warnings.append("CAMP_NEWS_INTELLIGENCE_NOT_YET_INGESTED")
    if phase=="PRESEASON" and not preseason:warnings.append("PRESEASON_USAGE_NOT_YET_INGESTED")

    payload={
      "generated_at_utc":datetime.now(timezone.utc).isoformat(),
      "model_version":"FSFFL-GM-3.0-Football-Intelligence-v2-Preseason-Usage",
      "active_season":season,
      "season_phase":phase,
      "phase_weights":w,
      "phase_interpretation":{
        "current_regular_season_usage_expected":phase in {"REGULAR_SEASON_EARLY","REGULAR_SEASON","PLAYOFFS"},
        "prior_season_evidence_expected":True,
        "camp_news_priority":phase in {"TRAINING_CAMP","PRESEASON"},
        "preseason_usage_priority":phase=="PRESEASON",
      },
      "usage_records":len(usage),
      "snap_records":len(snaps),
      "prior_snap_records":len(prior_snaps),
      "manual_intelligence_records":len(manual),
      "preseason_usage_records":len(preseason),
      "preseason_usage_diagnostics":preseason_diag,
      "usage":usage,
      "snaps":snaps,
      "prior_snaps":prior_snaps,
      "manual_intelligence":manual,
      "preseason_usage":preseason,
      "warnings":sorted(set(warnings)),
    }
    dump(payload)
    print(f"Football Intelligence v2: season={season} phase={phase}")
    print(f"Prior snap evidence: {len(prior_snaps)} players")
    print(f"Structured preseason usage signals: {len(preseason)}")
    if preseason_diag:print("Preseason diagnostics:",json.dumps(preseason_diag,sort_keys=True))
    if payload["warnings"]:print("Warnings:",", ".join(payload["warnings"]))

if __name__=="__main__":main()
