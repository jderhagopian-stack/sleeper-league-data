#!/usr/bin/env python3
"""Build the governed native V2 preseason projection baseline.

The model predicts league-agnostic raw season statistics first, then applies the
active league scoring rules as a downstream bridge. Veteran means use the
validated native V2 feature set. Players without sufficient NFL history retain
the existing preseason baseline as an explicitly provisional fallback so the
production universe does not lose coverage.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from native_projection_challenger import RidgeModel, choose_alpha_temporally
from run_native_projection_core_context_benchmark import AGE, DURABILITY, enrich, fetch_players
from run_native_projection_nflverse_benchmark import (
    FEATURES as BASE_FEATURES,
    STATS,
    TARGETS,
    fetch_csv,
    make_lagged_rows,
    normalize_season,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEPTH_URL = "https://github.com/nflverse/nflverse-data/releases/download/depth_charts/depth_charts_{season}.csv"
ROLE = ["opening_role_available", "opening_is_first_team", "opening_depth_rank"]
QB_REFINEMENT = [
    "opening_team_known",
    "opening_team_changed",
    "qb1_x_lag1_attempts",
    "qb1_x_lag1_pass_yards",
    "qb1_x_lag1_rush_yards",
]
SELECTED = {
    "QB": list(DURABILITY["QB"]) + ROLE + QB_REFINEMENT,
    "RB": ROLE,
    "WR": list(AGE["WR"]) + ROLE,
    "TE": list(AGE["TE"]) + ROLE,
}
# A midnight UTC cutoff safely preceding each season's opening-night kickoff.
# 2026 can also be capped earlier by --as-of when run during preseason.
OPENING_CUTOFF_UTC = {
    2025: "2025-09-04T00:00:00Z",
    2026: "2026-09-03T00:00:00Z",
}


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def norm_name(value: str) -> str:
    s = str(value or "").lower().replace("’", "'")
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\.?\b", "", s)
    return "".join(ch for ch in s if ch.isalnum())


def fnum(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def fetch_depth(season: int) -> list[dict]:
    req = urllib.request.Request(DEPTH_URL.format(season=season), headers={"User-Agent":"FSFFL-native-v2-production/1.0"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return list(csv.DictReader(io.StringIO(r.read().decode("utf-8-sig"))))


def iso_dt(v: str) -> datetime:
    return datetime.fromisoformat(v.replace("Z", "+00:00"))


def role_map(season: int, as_of: datetime | None = None) -> tuple[dict, dict]:
    rows = fetch_depth(season)
    if not rows:
        return {}, {"season":season,"rows":0,"schema":"empty"}
    cols = set(rows[0])
    out = {}
    if "dt" in cols and "pos_rank" in cols:
        cutoff_text = OPENING_CUTOFF_UTC.get(season)
        cutoff = iso_dt(cutoff_text) if cutoff_text else None
        if as_of is not None and (cutoff is None or as_of < cutoff):
            cutoff = as_of
        eligible = [r for r in rows if r.get("dt") and (cutoff is None or iso_dt(str(r["dt"])) < cutoff)]
        if not eligible:
            return {}, {"season":season,"rows":len(rows),"schema":"timestamped","cutoff":cutoff.isoformat() if cutoff else None,"eligible_rows":0}
        latest = max(iso_dt(str(r["dt"])) for r in eligible)
        snap = [r for r in eligible if iso_dt(str(r["dt"])) == latest]
        for r in snap:
            pid = str(r.get("gsis_id") or "").strip()
            pos = str(r.get("pos_grp") or r.get("pos_abb") or "").upper().strip()
            if not pid or pos not in SELECTED:
                continue
            rank = max(1.0, fnum(r.get("pos_rank"), 9.0))
            key = (pid, pos)
            prev = out.get(key)
            if prev is None or rank < prev["rank"]:
                out[key] = {"rank":rank,"team":str(r.get("team") or "").strip(),"snapshot":latest.isoformat()}
        return out, {"season":season,"rows":len(rows),"schema":"timestamped","cutoff":cutoff.isoformat() if cutoff else None,"snapshot":latest.isoformat(),"eligible_rows":len(eligible),"role_rows":len(out)}

    # Historical <=2024 validation/training bridge: opening-week administrative
    # records only. These are explicitly not treated as timestamp-proven.
    for r in rows:
        if str(r.get("game_type") or "").upper() != "REG" or str(r.get("week") or "").strip() != "1":
            continue
        pid = str(r.get("gsis_id") or "").strip()
        pos = str(r.get("position") or "").upper().strip()
        if not pid or pos not in SELECTED:
            continue
        rank = max(1.0, fnum(r.get("depth_team"), 9.0))
        key = (pid,pos)
        prev = out.get(key)
        if prev is None or rank < prev["rank"]:
            out[key] = {"rank":rank,"team":str(r.get("club_code") or "").strip(),"snapshot":"week1_admin"}
    return out, {"season":season,"rows":len(rows),"schema":"historical_week1_admin","role_rows":len(out),"freeze_provenance":"PROVISIONAL_NOT_TIMESTAMPED"}


def make_future_rows(season_rows: list[dict], target_season: int) -> list[dict]:
    idx={(int(r["season"]),str(r["player_id"])):r for r in season_rows}
    feature_season=target_season-1
    out=[]
    for (s,pid),cur in sorted(idx.items()):
        if s != feature_season:
            continue
        prev=idx.get((feature_season-1,pid))
        row={
            "season":target_season,
            "feature_season":feature_season,
            "player_id":pid,
            "player_name":cur["player_name"],
            "position":cur["position"],
            "next_season_present":1,
            "team_change":0,
            "lag1_games":fnum(cur.get("games")),
            "lag2_available":int(prev is not None),
            "lag2_games":fnum(prev.get("games")) if prev else 0.0,
            "next_games":0.0,
            "feature_team":str(cur.get("team") or ""),
        }
        for stat in STATS:
            row[f"lag1_{stat}"]=fnum(cur.get(stat))
            row[f"lag2_{stat}"]=fnum(prev.get(stat)) if prev else 0.0
            row[f"next_{stat}"]=0.0
        out.append(row)
    return out


def attach_roles(rows: list[dict], maps: dict[int,dict]) -> list[dict]:
    out=[]
    for raw in rows:
        r=dict(raw); pos=r["position"]; role=maps.get(int(r["season"]),{}).get((str(r["player_id"]),pos))
        rank=float(role["rank"]) if role else 9.0
        r["opening_role_available"]=int(role is not None)
        r["opening_is_first_team"]=int(bool(role and rank == 1.0))
        r["opening_depth_rank"]=min(rank,4.0) if role else 4.0
        opening_team=str(role.get("team") or "") if role else ""
        feature_team=str(r.get("feature_team") or "")
        r["opening_team_known"]=int(bool(opening_team))
        r["opening_team_changed"]=int(bool(opening_team and feature_team and opening_team != feature_team))
        r["qb1_x_lag1_attempts"]=fnum(r.get("lag1_attempts"))*r["opening_is_first_team"]
        r["qb1_x_lag1_pass_yards"]=fnum(r.get("lag1_passing_yards"))*r["opening_is_first_team"]
        r["qb1_x_lag1_rush_yards"]=fnum(r.get("lag1_rushing_yards"))*r["opening_is_first_team"]
        r["opening_team"]=opening_team
        out.append(r)
    return out


def add_feature_teams(rows: list[dict], season_rows: list[dict]) -> list[dict]:
    idx={(int(r["season"]),str(r["player_id"])):str(r.get("team") or "") for r in season_rows}
    out=[]
    for raw in rows:
        r=dict(raw); r["feature_team"]=idx.get((int(r["feature_season"]),str(r["player_id"])),""); out.append(r)
    return out


def train_predict(train_rows: list[dict], test_rows: list[dict], pos: str) -> dict[str,dict]:
    features=list(BASE_FEATURES[pos])+list(SELECTED[pos])
    train=[r for r in train_rows if r["position"]==pos]
    test=[r for r in test_rows if r["position"]==pos]
    result={str(r["player_id"]):{"player_name":r["player_name"],"position":pos,"team":r.get("opening_team") or r.get("feature_team"),"raw_stats":{}} for r in test}
    for target in TARGETS[pos]:
        alpha,_=choose_alpha_temporally(train,features,target)
        model=RidgeModel(alpha).fit([[fnum(r.get(f)) for f in features] for r in train],[fnum(r.get(target)) for r in train])
        preds=model.predict([[fnum(r.get(f)) for f in features] for r in test])
        stat=target.removeprefix("next_")
        for r,pred in zip(test,preds):
            result[str(r["player_id"])]["raw_stats"][stat]=round(max(0.0,float(pred)),3)
    return result


def fsffl_score(stats: dict, scoring: dict) -> float:
    # nflverse canonical stat -> Sleeper league scoring key
    terms={
        "passing_yards":"pass_yd",
        "passing_tds":"pass_td",
        "interceptions":"pass_int",
        "rushing_yards":"rush_yd",
        "rushing_tds":"rush_td",
        "receptions":"rec",
        "receiving_yards":"rec_yd",
        "receiving_tds":"rec_td",
    }
    return sum(fnum(stats.get(stat))*fnum(scoring.get(key)) for stat,key in terms.items())


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--season",type=int,default=None)
    p.add_argument("--start-season",type=int,default=2016)
    p.add_argument("--as-of",default=None,help="UTC ISO timestamp; current time by default")
    p.add_argument("--output",type=Path,default=None)
    p.add_argument("--raw-output",type=Path,default=None)
    a=p.parse_args()
    league=load_json(DATA/"league.json"); target=int(a.season or league.get("season")); scoring=league.get("scoring_settings") or {}
    as_of=iso_dt(a.as_of) if a.as_of else datetime.now(timezone.utc)
    if as_of.tzinfo is None: as_of=as_of.replace(tzinfo=timezone.utc)
    source=[]
    for season in range(a.start_season,target):
        source.extend(normalize_season(fetch_csv(season),season))
    hist=add_feature_teams(enrich(make_lagged_rows(source),source,fetch_players()),source)
    future=enrich(make_future_rows(source,target),source,fetch_players())

    role_maps={}; role_audits={}
    for season in sorted({int(r["season"]) for r in hist} | {target}):
        rm,ra=role_map(season,as_of if season==target else None); role_maps[season]=rm; role_audits[str(season)]=ra
    hist=attach_roles(hist,role_maps); future=attach_roles(future,role_maps)

    predicted={}
    for pos in ("QB","RB","WR","TE"):
        predicted.update(train_predict(hist,future,pos))

    sim=DATA/"simulator"/str(target); sources=sim/"sources"
    fallback_path=sources/"preseason_fsffl_points.json"; fallback=load_json(fallback_path) if fallback_path.exists() else {"players":{}}
    prior_path=sources/"selected_preseason_prior.json"; prior=load_json(prior_path) if prior_path.exists() else {"players":{}}
    # Map GSIS forecasts to Sleeper IDs through robust name+position matching against
    # the existing preseason universe. Ambiguous matches are not overwritten.
    name_index={}
    for sid,row in (fallback.get("players") or {}).items():
        key=(norm_name(row.get("player_name")),str(row.get("position") or "").upper()); name_index.setdefault(key,[]).append(str(sid))
    players=dict(fallback.get("players") or {})
    native_count=0; ambiguous=0; unmatched=0
    raw_players={}
    for gsis,row in predicted.items():
        key=(norm_name(row["player_name"]),row["position"]); ids=name_index.get(key,[])
        if len(ids)!=1:
            ambiguous += int(len(ids)>1); unmatched += int(len(ids)==0); continue
        sid=ids[0]; old=dict(players[sid]); points=max(0.0,fsffl_score(row["raw_stats"],scoring)); games=17.0
        old.update({
            "fsffl_projected_points":round(points,3),
            "fsffl_projected_ppg":round(points/games,3),
            "games_projected":games,
            "projected_stats_native":row["raw_stats"],
            "source":"FSFFL Native V2",
            "native_model_version":"FSFFL-Native-V2-role-aware",
            "native_gsis_id":gsis,
            "native_role_team":row.get("team"),
            "fallback_source_retained":False,
        })
        players[sid]=old; native_count+=1; raw_players[gsis]=row
    for sid,row in players.items():
        if str(row.get("source")) != "FSFFL Native V2":
            row["fallback_source_retained"]=True
            row["fallback_reason"]="No uniquely matched veteran native forecast; preserve coverage for rookies/no-history/unmatched players."

    out=a.output or sources/"native_preseason_fsffl_points.json"
    rawout=a.raw_output or sources/"native_raw_stat_projections.json"
    write_json(rawout,{"season":str(target),"model":"FSFFL-Native-V2-role-aware","as_of_utc":as_of.isoformat(),"players":raw_players,"feature_sets":SELECTED,"role_source_audit":role_audits,"governance":{"fantasy_scoring_used_in_training":False,"target_season_realized_stats_used":False,"current_role_snapshot_timestamped":bool(role_audits.get(str(target),{}).get("schema")=="timestamped"),"commercial_role_source_approved":False}})
    write_json(out,{"season":str(target),"generated_at_utc":datetime.now(timezone.utc).isoformat(),"source":"FSFFL Native V2 raw-stat model with coverage-preserving provisional fallback","players":players,"audit":{"native_players":native_count,"fallback_players":sum(1 for r in players.values() if r.get("fallback_source_retained")),"native_unmatched":unmatched,"native_ambiguous":ambiguous,"total_players":len(players),"native_coverage_pct":round(100*native_count/max(1,len(players)),2)},"governance":{"native_means_production_promoted":True,"external_projection_blend_enabled":False,"fallback_is_provisional":True,"current_depth_role_source_requires_commercial_replacement_or_clearance":True}})
    print(json.dumps({"status":"PASS","season":target,"native_players":native_count,"total_players":len(players),"native_coverage_pct":round(100*native_count/max(1,len(players)),2),"raw_output":str(rawout),"output":str(out)},indent=2))

if __name__=="__main__": main()
