#!/usr/bin/env python3
"""Capture extreme outcomes from a deterministic 50,000-universe Simulator 1.0 pass."""
from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

import build_fsffl_season_simulator as core
import run_fsffl_season_simulator_preproduction as prod

DATA = Path("data")
SIM_ROOT = DATA / "simulator"


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")


def sid(i):
    return int(i) + 1


def simulate_roster_week(roster, week, lineup, backups, n_sims, rng, shocks, adjustments):
    rows = {}
    for row in lineup:
        if row.get("player_id") is not None:
            rows[row["player_id"]] = row
    for chain in backups.values():
        for row in chain:
            rows[row["player_id"]] = row
    points, available = {}, {}
    for pid, row in rows.items():
        p, a = prod.generate_player_draws(row, week, n_sims, rng, shocks, adjustments)
        points[pid], available[pid] = p, a
    used = {pid: np.zeros(n_sims, dtype=bool) for pid in rows}
    total = np.zeros(n_sims, dtype=np.float32)
    slot_order = sorted(range(len(lineup)), key=lambda i: prod.SLOT_SCARCITY.get(lineup[i]["slot"], 5))
    for i in slot_order:
        starter = lineup[i]
        chain = ([starter] if starter.get("player_id") is not None else []) + backups.get(i, [])
        filled = np.zeros(n_sims, dtype=bool)
        for cand in chain:
            pid = cand["player_id"]
            mask = (~filled) & available[pid] & (~used[pid])
            if not np.any(mask):
                continue
            total[mask] += points[pid][mask]
            used[pid][mask] = True
            filled[mask] = True
            if np.all(filled):
                break
    return total, points, rows


def main():
    started = time.perf_counter()
    league = core.load_json(DATA / "league.json")
    rosters = core.load_json(DATA / "rosters.json", [])
    users = core.load_json(DATA / "users.json", [])
    players = core.load_json(DATA / "players.json", {})
    if not league:
        raise RuntimeError("data/league.json is required")
    season = str(league.get("season"))
    schedule_path = DATA / "stats" / "fsffl" / season / "league_matchups_raw.json"
    raw_schedule = core.load_json(schedule_path, {})
    projections = core.load_json(SIM_ROOT / season / "inputs" / "player_weekly_projections.json")
    if not rosters or not users or not players or not raw_schedule:
        raise RuntimeError(f"Missing core FSFFL data; expected schedule at {schedule_path}")
    if not projections:
        raise RuntimeError("Missing player_weekly_projections.json")

    n_sims = int(os.getenv("FSFFL_SIMULATIONS", "50000"))
    seed = prod.deterministic_seed(league, season)
    roster_dir = core.roster_directory(rosters, users)
    reg_weeks = core.regular_season_weeks(league)
    by_week, _ = core.build_schedule(raw_schedule, reg_weeks)
    playoff_start = int((league.get("settings") or {}).get("playoff_week_start") or 15)
    playoff_weeks = [playoff_start, playoff_start + 1, playoff_start + 2]
    all_weeks = sorted(set(reg_weeks + playoff_weeks))
    roster_ids = sorted(roster_dir)
    rid_to_i = {rid: i for i, rid in enumerate(roster_ids)}
    i_to_rid = {i: rid for rid, i in rid_to_i.items()}
    n_teams = len(roster_ids)
    adjustments, adjustment_source = prod.load_opponent_adjustments(season)

    lineups, backups = defaultdict(dict), defaultdict(dict)
    for rid, roster in roster_dir.items():
        for week in all_weeks:
            lineup = prod.optimize_fsffl_fast(roster, week, league, players, projections)
            lineups[rid][week] = lineup
            backups[rid][week] = prod.build_backup_chains(roster, week, lineup, players, projections)

    rng = np.random.default_rng(seed)
    week_to_i = {w: i for i, w in enumerate(all_weeks)}
    scores = np.zeros((n_sims, len(all_weeks), n_teams), dtype=np.float32)
    player_season, player_info = {}, {}
    max_player_week = None
    shocks = {}
    for week in all_weeks:
        wi = week_to_i[week]
        for rid in roster_ids:
            total, point_map, meta = simulate_roster_week(roster_dir[rid], week, lineups[rid][week], backups[rid][week], n_sims, rng, shocks, adjustments)
            scores[:, wi, rid_to_i[rid]] = total
            for pid, arr in point_map.items():
                row = meta[pid]
                info = {"player_id": str(pid), "player": row.get("name"), "position": row.get("position"), "nfl_team": row.get("nfl_team"), "roster_id": int(rid), "fsffl_team": roster_dir[rid]["team_name"], "baseline_ppg": round(float(row.get("mean", 0.0)), 3)}
                player_info[pid] = info
                if week in reg_weeks:
                    player_season.setdefault(pid, np.zeros(n_sims, dtype=np.float32)); player_season[pid] += arr
                k = int(np.argmax(arr)); val = float(arr[k])
                if max_player_week is None or val > max_player_week["points"]:
                    max_player_week = {"simulation_id": sid(k), "week": int(week), **info, "points": round(val, 3)}

    reg_idx = [week_to_i[w] for w in reg_weeks]; reg_scores = scores[:, reg_idx, :]
    pf = reg_scores.sum(axis=1, dtype=np.float64); wins = np.zeros((n_sims, n_teams), dtype=np.int16); biggest_margin = None
    for week in reg_weeks:
        wi = week_to_i[week]
        for a, b in by_week.get(week, []):
            ai, bi = rid_to_i[a], rid_to_i[b]; sa, sb = scores[:, wi, ai], scores[:, wi, bi]; aw = sa >= sb
            wins[:, ai] += aw; wins[:, bi] += ~aw
            margin = np.abs(sa - sb); k = int(np.argmax(margin)); m = float(margin[k])
            if biggest_margin is None or m > biggest_margin["margin"]:
                winner_i = ai if sa[k] >= sb[k] else bi; loser_i = bi if winner_i == ai else ai
                biggest_margin = {"simulation_id": sid(k), "week": int(week), "winner": roster_dir[i_to_rid[winner_i]]["team_name"], "loser": roster_dir[i_to_rid[loser_i]]["team_name"], "winner_score": round(float(scores[k, wi, winner_i]), 3), "loser_score": round(float(scores[k, wi, loser_i]), 3), "margin": round(m, 3)}

    orders = prod.fast_seed_orders(wins, pf, roster_ids); playoff_teams = int((league.get("settings") or {}).get("playoff_teams") or 6)
    def team_week_extreme(fn):
        f = int(fn(reg_scores)); s, w, t = np.unravel_index(f, reg_scores.shape)
        return {"simulation_id": sid(s), "week": int(reg_weeks[w]), "team": roster_dir[i_to_rid[t]]["team_name"], "score": round(float(reg_scores[s,w,t]), 3)}
    max_team_week, min_team_week = team_week_extreme(np.argmax), team_week_extreme(np.argmin)
    def season_pf_extreme(fn):
        f = int(fn(pf)); s, t = np.unravel_index(f, pf.shape); w = int(wins[s,t])
        return {"simulation_id": sid(s), "team": roster_dir[i_to_rid[t]]["team_name"], "points_for": round(float(pf[s,t]),3), "record": f"{w}-{len(reg_weeks)-w}"}
    max_season_pf, min_season_pf = season_pf_extreme(np.argmax), season_pf_extreme(np.argmin)
    max_w = int(wins.max()); best = np.argwhere(wins == max_w); s,t = max(best,key=lambda z: pf[z[0],z[1]])
    best_record = {"simulation_id":sid(s),"team":roster_dir[i_to_rid[int(t)]]["team_name"],"record":f"{max_w}-{len(reg_weeks)-max_w}","points_for":round(float(pf[s,t]),3)}
    min_w = int(wins.min()); worst = np.argwhere(wins == min_w); s,t = min(worst,key=lambda z: pf[z[0],z[1]])
    worst_record = {"simulation_id":sid(s),"team":roster_dir[i_to_rid[int(t)]]["team_name"],"record":f"{min_w}-{len(reg_weeks)-min_w}","points_for":round(float(pf[s,t]),3)}

    sim=np.arange(n_sims); top6=orders[:,:6]; w1,w2,w3=[week_to_i[x] for x in playoff_weeks]; s1,s2,s3,s4,s5,s6=[top6[:,j] for j in range(6)]
    g1=np.where(scores[sim,w1,s3]>=scores[sim,w1,s6],s3,s6); g2=np.where(scores[sim,w1,s4]>=scores[sim,w1,s5],s4,s5)
    seed_num=np.full((n_sims,n_teams),99,dtype=np.int16)
    for j in range(6): seed_num[sim,top6[:,j]]=j+1
    low=np.where(seed_num[sim,g1]>seed_num[sim,g2],g1,g2); high=np.where(low==g1,g2,g1)
    semi1=np.where(scores[sim,w2,s1]>=scores[sim,w2,low],s1,low); semi2=np.where(scores[sim,w2,s2]>=scores[sim,w2,high],s2,high)
    champion=np.where(scores[sim,w3,semi1]>=scores[sim,w3,semi2],semi1,semi2); champion_seed=seed_num[sim,champion]
    title_counts=np.zeros(n_teams,dtype=np.int64); np.add.at(title_counts,champion,1); title_rates=title_counts/n_sims
    rare_i=sorted(set(int(x) for x in champion),key=lambda i:title_rates[i])[0]; s=int(np.flatnonzero(champion==rare_i)[0]); w=int(wins[s,rare_i])
    rarest_champion={"simulation_id":sid(s),"team":roster_dir[i_to_rid[rare_i]]["team_name"],"seed":int(champion_seed[s]),"record":f"{w}-{len(reg_weeks)-w}","season_points_for":round(float(pf[s,rare_i]),3),"title_rate":round(float(title_rates[rare_i]),6)}
    max_seed=int(champion_seed.max()); cands=np.flatnonzero(champion_seed==max_seed); s=int(min(cands,key=lambda z:pf[z,champion[z]])); ci=int(champion[s]); w=int(wins[s,ci])
    highest_seed_champion={"simulation_id":sid(s),"team":roster_dir[i_to_rid[ci]]["team_name"],"seed":max_seed,"record":f"{w}-{len(reg_weeks)-w}","season_points_for":round(float(pf[s,ci]),3)}
    best_miss=None
    for s in range(n_sims):
        missed=orders[s,playoff_teams:]; i=int(max(missed,key=lambda z:(wins[s,z],pf[s,z]))); key=(int(wins[s,i]),float(pf[s,i]))
        if best_miss is None or key>best_miss["_key"]:
            best_miss={"_key":key,"simulation_id":sid(s),"team":roster_dir[i_to_rid[i]]["team_name"],"record":f"{key[0]}-{len(reg_weeks)-key[0]}","points_for":round(key[1],3),"seed":int(np.where(orders[s]==i)[0][0]+1)}
    best_miss.pop("_key",None)
    max_player_season=None; surprise_superstar=None
    for pid,arr in player_season.items():
        info=player_info[pid]; k=int(np.argmax(arr)); val=float(arr[k]); cand={"simulation_id":sid(k),**info,"season_points":round(val,3),"simulated_ppg":round(val/len(reg_weeks),3)}
        if max_player_season is None or val>max_player_season["season_points"]: max_player_season=cand
        if info["baseline_ppg"]<=8.0 and (surprise_superstar is None or val>surprise_superstar["season_points"]): surprise_superstar=cand
    output={"generated_at_utc":core.now_utc(),"model_version":"FSFFL-Season-Simulator-1.0-multiverse","season":season,"simulations":n_sims,"rng_seed":seed,"opponent_adjustment_source":adjustment_source,"runtime_seconds":round(time.perf_counter()-started,3),"notes":{"simulation_id":"1-based ID within this deterministic multiverse pass.","surprise_superstar_rule":"Highest regular-season total among players with baseline PPG <= 8.0.","historical_records":"Simulation extremes only; compare with the FSFFL historical record book before labeling a real league record."},"player_extremes":{"highest_single_week":max_player_week,"highest_regular_season_total":max_player_season,"unexpected_superstar_season":surprise_superstar},"team_extremes":{"highest_single_week_score":max_team_week,"lowest_single_week_score":min_team_week,"highest_regular_season_points":max_season_pf,"lowest_regular_season_points":min_season_pf,"best_regular_season_record":best_record,"worst_regular_season_record":worst_record,"biggest_margin_of_victory":biggest_margin,"best_team_to_miss_playoffs":best_miss},"playoff_extremes":{"rarest_champion":rarest_champion,"highest_seed_champion":highest_seed_champion}}
    out=SIM_ROOT/season/"outputs"/"multiverse_outliers.json"; write_json(out,output)
    print(f"Multiverse tracker complete: {n_sims:,} universes in {output['runtime_seconds']:.3f}s"); print(f"Wrote {out}")

if __name__ == "__main__": main()
