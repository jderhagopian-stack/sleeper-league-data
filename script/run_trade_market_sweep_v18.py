#!/usr/bin/env python3
"""FSFFL Counter & Market Sweep 1.10 — owner-aware diversified negotiation ranking.

Returns up to five useful counters even when none reaches MEDIUM/HIGH heuristic
acceptance fit. GM owner behavior from completed trades, rookie drafts and
waivers directly informs acceptance and ranking. Top options blend focal
strategic gain, modeled acceptance fit, and owner-specific behavioral match,
then diversify across counterparties when viable alternatives exist. Behavior
is evidence, not a veto and not a calibrated probability. Canonical state is
read-only.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

V13_PATH = Path("script/run_trade_market_sweep_v13.py")
V16_PATH = Path("script/run_trade_market_sweep_v16.py")
OWNER_BEHAVIOR_PATH = Path("data/owner_behavior_profiles.json")
ASSET_PATH = Path("data/fsffl_asset_values.json")
NEGOTIATION_RANKING = Path("script/negotiation_ranking.py")
MODEL_VERSION = "FSFFL-Counter-Market-Sweep-1.10"
DEFAULT_SEARCH_DEPTH = 60
MAX_NORMAL_OPTIONS_PER_BUYER = 2


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to import {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def clamp(x, lo, hi): return max(lo, min(hi, x))
def sf(x, default=0.0):
    try: return float(x)
    except (TypeError, ValueError): return default

def load_json(path, default=None):
    if not Path(path).exists(): return default
    return json.loads(Path(path).read_text(encoding="utf-8"))


def behavior_index():
    profiles = load_json(OWNER_BEHAVIOR_PATH, []) or []
    positions = ("QB", "RB", "WR", "TE")
    shares, pick_raw = {}, {}
    for p in profiles:
        uid = str(p.get("user_id")); t = p.get("trade_profile") or {}; d = p.get("rookie_draft_profile") or {}; w = p.get("waiver_profile") or {}
        acq, drafted, added = t.get("player_positions_acquired") or {}, d.get("positions") or {}, w.get("positions_added") or {}
        scores = {pos: sf(acq.get(pos)) + .70*sf(drafted.get(pos)) + .20*sf(added.get(pos)) for pos in positions}
        total = sum(scores.values()) or 1.0
        shares[uid] = {pos: scores[pos]/total for pos in positions}
        pa = sum(sf(t.get(k)) for k in ("firsts_acquired","seconds_acquired","thirds_acquired"))
        ps = sum(sf(t.get(k)) for k in ("firsts_sent","seconds_sent","thirds_sent"))
        pick_raw[uid] = pa-ps + .15*sf(d.get("rookie_picks_made_2023_plus"))
    avg = {pos:(sum(v[pos] for v in shares.values())/len(shares)) if shares else .25 for pos in positions}
    pv=list(pick_raw.values()); pm=sum(pv)/len(pv) if pv else 0.0; psd=(sum((x-pm)**2 for x in pv)/len(pv))**.5 if pv else 1.0; psd=psd or 1.0
    out={}
    for p in profiles:
        uid=str(p.get("user_id")); t=p.get("trade_profile") or {}; s=shares.get(uid,{})
        pref={pos:round(clamp(((s.get(pos,0)/(avg.get(pos) or .25))-1)/.75,-1,1),3) for pos in positions}
        total=sf(t.get("total_trades")); recent=sf(t.get("recent_trades_2025_2026")); initiated=sf(t.get("initiated_trades")); multi=sf(t.get("multi_asset_trades")); conf="HIGH" if total>=20 else "MEDIUM" if total>=8 else "LOW"
        out[uid]={"position_preference":pref,"pick_preference":round(clamp(((pick_raw.get(uid,0)-pm)/psd)/2,-1,1),3),"completed_trade_sample":int(total),"recent_trade_sample":int(recent),"initiation_rate":round(initiated/total,3) if total else None,"multi_asset_rate":round(multi/total,3) if total else None,"behavior_confidence":conf,"confidence_weight":{"HIGH":1.0,"MEDIUM":.65,"LOW":.35}[conf]}
    return out


def asset_meta():
    d=load_json(ASSET_PATH,{}) or {}; out={}
    for p in d.get("players") or []:
        aid=p.get("asset_id") or (f"player:{p.get('player_id')}" if p.get("player_id") is not None else None)
        if aid: out[str(aid)]={"asset_type":"player","position":p.get("position")}
    for p in d.get("picks") or []:
        if p.get("asset_id"): out[str(p["asset_id"])]={"asset_type":"pick","position":None}
    return out


def owner_behavior_signal(uid: str, receives: List[str], sends: List[str], beh, meta):
    b=beh.get(str(uid)) or {}
    if not b: return {"available":False,"behavior_confidence":"NONE","adjustment":0.0,"reason":"No owner behavior sample; state/market model remains primary."}
    pp=b.get("position_preference") or {}
    rp=[meta.get(a,{}).get("position") for a in receives if meta.get(a,{}).get("asset_type")=="player"]; sp=[meta.get(a,{}).get("position") for a in sends if meta.get(a,{}).get("asset_type")=="player"]
    rp,sp=[x for x in rp if x],[x for x in sp if x]
    rpref=sum(sf(pp.get(x)) for x in rp)/len(rp) if rp else 0.0; spref=sum(sf(pp.get(x)) for x in sp)/len(sp) if sp else 0.0
    position_signal=.75*rpref-.25*spref
    recvp=sum(1 for a in receives if meta.get(a,{}).get("asset_type")=="pick" or a.startswith("pick:")); sendp=sum(1 for a in sends if meta.get(a,{}).get("asset_type")=="pick" or a.startswith("pick:"))
    pick_signal=sf(b.get("pick_preference"))*clamp((recvp-sendp)/2,-1,1)
    size=len(receives)+len(sends); mr=b.get("multi_asset_rate"); complexity=clamp((sf(mr)-.45)/.45,-1,1) if mr is not None and size>=4 else 0.0; activity=clamp((sf(b.get("recent_trade_sample"))-2)/8,-.5,1)
    raw=.52*position_signal+.25*pick_signal+.13*complexity+.10*activity; adj=round(clamp(raw*sf(b.get("confidence_weight"),.35)*.16,-.16,.16),4)
    pos,neg=[],[]
    if position_signal>=.2: pos.append("incoming positions match historical acquisition tendencies")
    elif position_signal<=-.2: neg.append("incoming positions conflict with historical acquisition tendencies")
    if pick_signal>=.15: pos.append("pick flow matches historical pick preference")
    elif pick_signal<=-.15: neg.append("pick flow conflicts with historical pick preference")
    if complexity>=.2: pos.append("manager has historically completed multi-asset trades")
    elif complexity<=-.2: neg.append("package is more complex than this manager's usual completed trades")
    if activity>=.25: pos.append("manager has been an active recent trader")
    return {"available":True,"behavior_confidence":b.get("behavior_confidence"),"completed_trade_sample":b.get("completed_trade_sample"),"recent_trade_sample":b.get("recent_trade_sample"),"initiation_rate":b.get("initiation_rate"),"multi_asset_rate":b.get("multi_asset_rate"),"position_signal":round(position_signal,4),"pick_signal":round(pick_signal,4),"complexity_signal":round(complexity,4),"activity_signal":round(activity,4),"adjustment":adj,"reason":"; ".join((pos[:2]+neg[:2])) if (pos or neg) else "Historical behavior is roughly neutral for this package."}


def adjusted_buyer_rationality(base_mod,row,dl,beh,meta):
    br=base_mod.buyer_rationality(row,dl); uid=str(row.get("buyer_user_id") or ""); receives=[str(x) for x in (row.get("outgoing_assets") or [])]; sends=[str(x) for x in (row.get("return_assets") or [])]
    sig=owner_behavior_signal(uid,receives,sends,beh,meta); base_score=sf(br.get("heuristic_acceptance_fit_score"),.5); score=round(clamp(base_score+sf(sig.get("adjustment")),0,1),4); band="HIGH" if score>=.68 else "MEDIUM" if score>=.48 else "LOW" if score>=.28 else "VERY_LOW"
    br.update({"state_utility_acceptance_fit_score":base_score,"owner_behavior":sig,"heuristic_acceptance_fit_score":score,"heuristic_acceptance_fit":band,"acceptance_fit_basis":"state_utility_plus_GM_owner_behavior","acceptance_fit_is_probability":False}); return br


def acceptance_note(br):
    band=br.get("heuristic_acceptance_fit") or "UNKNOWN"; state=br.get("buyer_state") or "unknown"; title=sf(br.get("buyer_title_delta")); dyn=sf(br.get("buyer_market_dynasty_delta")); behavioral=(br.get("owner_behavior") or {}).get("reason") or "owner behavior neutral/unavailable"
    if band in {"HIGH","MEDIUM"}: return f"{band}: package aligns reasonably with this {state} manager's current objective; owner-specific evidence: {behavioral}."
    if band=="LOW": return f"LOW: plausible but demanding for this {state} manager (title {title:+.1%}, dynasty {dyn:+.0f}); owner-specific evidence: {behavioral}."
    return f"VERY LOW: aggressive ask (title {title:+.1%}, dynasty {dyn:+.0f}); owner-specific evidence: {behavioral}."


def advantage_note(row):
    comp=row.get("comparison_to_current_offer") or {}; md=comp.get("metric_deltas_vs_current_offer") or {}; pieces=[]; champ=sf(md.get("championship_probability")); wins=sf(md.get("expected_wins")); dyn=sf(md.get("market_dynasty_delta"))
    if champ: pieces.append(f"championship probability {champ:+.1%} vs current offer")
    if wins: pieces.append(f"expected wins {wins:+.2f}")
    if dyn: pieces.append(f"dynasty value {dyn:+.0f}")
    return f"{comp.get('verdict_vs_current_offer') or 'MIXED'} than current offer: "+(", ".join(pieces) if pieces else "better strategic fit under the model")


def blended_negotiation_score(row):
    br=row.get("buyer_rationality") or {}; md=(row.get("comparison_to_current_offer") or {}).get("metric_deltas_vs_current_offer") or {}; champ=sf(md.get("championship_probability")); wins=sf(md.get("expected_wins")); dyn=sf(md.get("market_dynasty_delta"))
    strategic=clamp(.50+1.75*champ+.07*wins+dyn/22000.0,0,1); acceptance=clamp(sf(br.get("heuristic_acceptance_fit_score"),.5),0,1); behavior=clamp(.50+sf((br.get("owner_behavior") or {}).get("adjustment"))/.32,0,1)
    nr=load_module(NEGOTIATION_RANKING,"negotiation_ranking_for_v110")
    return nr.compose(strategic,acceptance,behavior)


def diversified_top_five(viable):
    """Prefer breadth of negotiation paths without discarding exceptional repeats."""
    selected=[]; counts=Counter(); deferred=[]
    for row in viable:
        uid=str(row.get("buyer_user_id") or "")
        if counts[uid] < MAX_NORMAL_OPTIONS_PER_BUYER:
            selected.append(row); counts[uid]+=1
            if len(selected)==5: return selected
        else:
            deferred.append(row)
    # If the market cannot support five under the cap, backfill the strongest
    # remaining options rather than returning fewer than five.
    for row in deferred:
        if len(selected)==5: break
        selected.append(row); counts[str(row.get("buyer_user_id") or "")]+=1
    return selected[:5]


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--scenario",required=True); ap.add_argument("--quick-sims",type=int,default=100); ap.add_argument("--confirm-sims",type=int,default=0); ap.add_argument("--search-depth",type=int,default=DEFAULT_SEARCH_DEPTH); ap.add_argument("--output",required=True); ap.add_argument("--seed",type=int,default=20260821); args=ap.parse_args(); depth=max(40,args.search_depth)
    beh,meta=behavior_index(),asset_meta(); v16=load_module(V16_PATH,"market_sweep_v16_for_v110"); v13=load_module(V13_PATH,"market_sweep_v13_for_v110"); engine=v13.load_module(v13.BASE_ENGINE,"market_sweep_base_for_v110"); v16.install_read_caches(engine); dl=engine.import_decision_lab()
    def sim(dl_mod,mi,bl,b,fu,bu,o,i,sims,seed): return v13.fast_simulate_candidate(engine,dl_mod,mi,bl,b,fu,bu,o,i,sims,seed)
    engine.simulate_candidate=sim
    with tempfile.TemporaryDirectory() as td:
        raw=Path(td)/"deep.json"; v13.run_base_engine_in_process(engine,["--scenario",args.scenario,"--quick-sims",str(args.quick_sims),"--confirm-sims",str(args.confirm_sims),"--shortlist",str(depth),"--finalists",str(depth),"--seed",str(args.seed),"--output",str(raw)]); report=json.loads(raw.read_text())
    scenario=engine.load_json(Path(args.scenario),{}) or {}; focus=str(scenario.get("focus_user_id") or ""); sent,recv,partner=engine.incoming_trade_parts(scenario,focus)
    mi=dl.load_model_inputs(); simmod,league,rosters,users,players,season,projections,sched=mi; bl=dl.load_cached_lineups(season); baseline=dl.simulate_from_lineups(simmod,league,rosters,users,sched,bl,args.quick_sims,args.seed)
    pc,pk=engine.asset_catalog(); cat={**pc,**pk}; outgoing=[cat[x] for x in sent if x in cat]; incoming=[cat[x] for x in recv if x in cat]; missing=[x for x in sent+recv if x not in cat]
    if missing: raise ValueError(f"Current-offer assets missing from FSFFL asset catalog: {missing}")
    current=engine.score_candidate(focus,partner,outgoing,incoming); current["outgoing_assets"]=sent; current["outgoing_asset_names"]=[a.get("name") for a in outgoing]; current["candidate_type"]="CURRENT_OFFER"; current["outgoing_variant"]="FULL"; current["simulation"]=sim(dl,mi,bl,baseline,focus,partner,outgoing,incoming,args.quick_sims,args.seed); current["post_sim_score"]=engine.post_sim_score(current,engine.team_state(focus)); current["buyer_rationality"]=adjusted_buyer_rationality(v16,current,dl,beh,meta)
    rows=list(report.get("ranked_finalists") or [])
    for r in rows:
        r["buyer_rationality"]=adjusted_buyer_rationality(v16,r,dl,beh,meta); r["comparison_to_current_offer"]=v13.compare_candidate(r,current); r["acceptance_likelihood"]=r["buyer_rationality"]["heuristic_acceptance_fit"]; r["acceptance_explanation"]=acceptance_note(r["buyer_rationality"]); r["why_advantageous_for_focus"]=advantage_note(r); r["negotiation_ranking"]=blended_negotiation_score(r)
    viable=[r for r in rows if v16.focal_viable(r) and r["buyer_rationality"]["current_state_viable"]]; viable.sort(key=lambda r:(sf((r.get("negotiation_ranking") or {}).get("score")),sf(r.get("post_sim_score"))),reverse=True)
    realistic=[r for r in viable if r["acceptance_likelihood"] in {"HIGH","MEDIUM"}]
    # Presentation diversity applies after quality ranking. A LOW/VERY_LOW deal
    # can fill the report, but never drives the primary action.
    top5=diversified_top_five(viable)

    # Broad discovery is intentionally cheap, but the current offer and every
    # option that survives into the actionable Top 5 are confirmed at the
    # requested high-precision simulation count before final comparison/action.
    final_sim_count=args.quick_sims
    final_seed=args.seed
    if args.confirm_sims and args.confirm_sims > args.quick_sims:
        final_seed=simmod.deterministic_seed(league,season)
        confirm_baseline=dl.simulate_from_lineups(
            simmod,league,rosters,users,sched,bl,args.confirm_sims,final_seed
        )
        current["simulation"]=sim(
            dl,mi,bl,confirm_baseline,focus,partner,outgoing,incoming,
            args.confirm_sims,final_seed
        )
        current["post_sim_score"]=engine.post_sim_score(current,engine.team_state(focus))
        current["buyer_rationality"]=adjusted_buyer_rationality(v16,current,dl,beh,meta)
        for r in top5:
            buyer_uid=str(r.get("buyer_user_id") or "")
            out_ids=list(r.get("outgoing_assets") or [])
            in_ids=list(r.get("return_assets") or r.get("incoming_assets") or [])
            out_assets=[cat[x] for x in out_ids if x in cat]
            in_assets=[cat[x] for x in in_ids if x in cat]
            if not buyer_uid or len(out_assets)!=len(out_ids) or len(in_assets)!=len(in_ids):
                continue
            r["simulation"]=sim(
                dl,mi,bl,confirm_baseline,focus,buyer_uid,out_assets,in_assets,
                args.confirm_sims,final_seed
            )
            r["post_sim_score"]=engine.post_sim_score(r,engine.team_state(focus))
            r["buyer_rationality"]=adjusted_buyer_rationality(v16,r,dl,beh,meta)
            r["comparison_to_current_offer"]=v13.compare_candidate(r,current)
            r["acceptance_likelihood"]=r["buyer_rationality"]["heuristic_acceptance_fit"]
            r["acceptance_explanation"]=acceptance_note(r["buyer_rationality"])
            r["why_advantageous_for_focus"]=advantage_note(r)
            r["negotiation_ranking"]=blended_negotiation_score(r)
        final_sim_count=args.confirm_sims

    for r in top5: r["report_role"]="REALISTIC_COUNTER" if r["acceptance_likelihood"] in {"HIGH","MEDIUM"} else "REASONABLE_LONGSHOT"
    very=[r for r in top5 if r["acceptance_likelihood"]=="VERY_LOW"]; swing=max(very,key=lambda r:sf((r.get("negotiation_ranking") or {}).get("score"))) if very else None
    if swing: swing["report_role"]="SWING_FOR_FENCES"; swing["report_note"]="Aggressive ask included because focal upside is unusually strong; very low heuristic acceptance fit."
    role={"REALISTIC_COUNTER":3,"REASONABLE_LONGSHOT":2,"SWING_FOR_FENCES":1}; top5.sort(key=lambda r:(role.get(r.get("report_role"),0),sf((r.get("negotiation_ranking") or {}).get("score"))),reverse=True)
    for i,r in enumerate(top5,1): r["actionable_rank"]=i
    pivot=[r for r in rows if v16.focal_viable(r) and not r["buyer_rationality"]["current_state_viable"] and r["buyer_rationality"]["state_change_viable"]]; pivot.sort(key=lambda r:sf(r.get("post_sim_score")),reverse=True)
    if realistic:
        best=realistic[0]
        if v16.focal_viable(current) and current["buyer_rationality"]["current_state_viable"]: action="SHOP_BEFORE_ACCEPTING" if sf(best.get("post_sim_score"))>sf(current.get("post_sim_score"))+750 else "ACCEPT_NOW"
        elif any(r.get("candidate_type")=="SAME_PARTNER_COUNTER" for r in realistic[:5]): action="COUNTER_CURRENT_OFFEROR"
        else: action="SHOP_BEFORE_ACCEPTING"
    else: action="DECLINE"
    buyer_counts=dict(Counter(str(r.get("buyer_user_id") or "") for r in top5)); unique_buyers=len(buyer_counts)
    report["model_version"]=MODEL_VERSION; report["current_offer_evaluation"]=current; report["ranked_finalists"]=top5; report["top_5_alternatives"]=top5; report["realistic_counter_alternatives"]=realistic[:5]; report["reasonable_longshot_alternatives"]=[r for r in top5 if r.get("report_role")=="REASONABLE_LONGSHOT"]; report["swing_for_fences_alternative"]=swing; report["state_change_dependent_alternatives"]=pivot[:5]; report["recommended_next_action"]=action
    cc=report.setdefault("candidate_counts",{}); cc["acceptance_frontier_simulated"]=len(rows); cc["buyer_current_state_viable"]=len(viable); cc["realistic_acceptance_fit"]=len(realistic); cc["reasonable_longshot_pool"]=len([r for r in viable if r["acceptance_likelihood"] in {"LOW","VERY_LOW"}]); cc["top_five_unique_buyers"]=unique_buyers; cc["top_five_options_by_buyer"]=buyer_counts
    pol=report.setdefault("policy",{}); pol.update({"five_option_report_when_market_supports_it":True,"reasonable_longshots_can_fill_report":True,"acceptance_likelihood_is_heuristic_not_probability":True,"each_option_explains_acceptance_and_focus_advantage":True,"swing_for_fences_slots_max":1,"longshots_cannot_drive_recommended_action":True,"fast_exact_lineup_dp":True,"GM_owner_behavior_integrated":True,"owner_behavior_sources":["completed_trades","rookie_drafts","waivers"],"owner_behavior_is_evidence_not_veto":True,"acceptance_fit_is_calibrated_probability":False,"top_five_blended_ranking":True,"top_five_blended_ranking_weights":{"focal_strategic_gain":.625,"acceptance_fit":.375,"owner_behavior_match":0.0},"owner_behavior_already_in_acceptance_fit":True,"buyer_diversity_enabled":True,"normal_max_options_per_buyer":MAX_NORMAL_OPTIONS_PER_BUYER,"buyer_cap_backfills_only_when_needed_for_five":True})
    report["owner_behavior_profiles_available"]=len(beh); report["simulation"]["lineup_reoptimization"]="exact_slot_mask_dynamic_programming"; report["simulation"]["execution_path"]="GM_owner_behavior_plus_blended_ranking_plus_buyer_diversity_then_canonical_vectorized_decision_lab"; report["simulation"]["final_trade_impact_simulations"]=final_sim_count; report["simulation"]["final_trade_impact_engine"]="current_vectorized_simulator"; report["simulation"]["final_trade_impact_seed"]=final_seed; report["simulation"]["finalists_confirmed_at_high_precision"]=final_sim_count>args.quick_sims
    Path(args.output).write_text(json.dumps(report,indent=2,sort_keys=True),encoding="utf-8"); print(json.dumps(report,indent=2))

if __name__=="__main__": main()
