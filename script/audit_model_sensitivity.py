#!/usr/bin/env python3
"""FSFFL model sensitivity audit.

Quantifies the leverage of selected high-impact assumptions without changing
production outputs. This is a governance diagnostic, not a replacement model.
"""
from __future__ import annotations
import json, importlib.util
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
SCRIPT=ROOT/"script"
DATA=ROOT/"data"
OUT=DATA/"audit"
OUT.mkdir(parents=True,exist_ok=True)

MODEL_VERSION="FSFFL-Model-Sensitivity-Audit-1.0"

def load_module(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    m=importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(m); return m

def sf(x,d=0.0):
    try:return float(x)
    except:return d

def package_curve():
    # Equal-value assets isolate only the package discount assumption.
    a=[1.0,.92,.84]
    b=[1.0,.78,.62,.50,.42]
    out=[]
    for n in (1,2,3):
        av=sum(a[:n]); bv=sum(b[:n])
        out.append({
            "asset_count":n,
            "legacy_config_effective_value":round(av,4),
            "gm22_effective_value":round(bv,4),
            "difference_pct_vs_gm22":round((av/bv-1)*100,2) if bv else None,
        })
    return out

def trade_score_elasticity():
    return {
      "per_1_percentage_point_title_probability":250.0,
      "per_1_percentage_point_playoff_probability":50.0,
      "per_1_percentage_point_opponent_title_externality":-120.0,
      "per_1000_dynasty_value_points":1000.0,
      "per_1000_break_glass_points":350.0,
      "source_formula":"dynasty + .35*break_glass + 25000*title + 5000*playoff - 12000*externality",
    }

def gm_config_envelopes():
    gm=load_module(SCRIPT/"build_fsffl_gm_engine.py","gm_for_sensitivity")
    cfg=gm.CONFIG
    owner=cfg.get("owner_value_weights") or {}
    football=cfg.get("football_intelligence_weights") or {}
    return {
      "owner_value_max_adjustments":owner,
      "owner_value_max_adjustments_sum_if_all_aligned":round(sum(sf(v) for v in owner.values()),4),
      "football_intelligence_component_maxima":football,
      "football_component_nominal_sum_if_all_aligned":round(sum(sf(v) for v in football.values()),4),
      "football_total_runtime_clamp":"±0.22 observed in football_intelligence_adjustment",
      "performance_max_adjustment":sf((cfg.get("performance_weights") or {}).get("max_adjustment")),
      "warning":"These are configured envelopes, not evidence that all maxima occur simultaneously."
    }

def state_weight_divergence():
    gm=load_module(SCRIPT/"build_fsffl_gm_engine.py","gm_state_core")
    sw=load_module(SCRIPT/"gm_state_weighting.py","gm_state_runtime")
    rows=[]
    for c in [0,.2,.35,.45,.55,.65,.78,.9,1.0]:
        state_core,w_core=gm._u_team_objective_weights({"contender_score":c,"dynasty_roster_score":.5})
        rr=sw.resolve({"contender_score":c,"dynasty_roster_score":.5,"user_id":"audit"},simulator_row={"championship_probability":.08})
        w_run=rr["weights"]
        l1=sum(abs(sf(w_core[k])-sf(w_run[k])) for k in ("current","future","liquidity","resilience"))
        rows.append({
          "contender_score":c,
          "gm22_state":state_core,
          "gm22_weights":w_core,
          "runtime_state":rr["state"],
          "runtime_weights":w_run,
          "runtime_source":rr["runtime_source"],
          "l1_weight_difference":round(l1,4),
        })
    return rows

def roster_interaction_elasticity():
    ri=load_module(SCRIPT/"roster_interaction.py","ri_sens")
    p={"team":"NE","position":"RB","market_redraft":3000,"uncertainty":.40}
    s={"team":"NE","position":"RB","market_redraft":1800,"uncertainty":.30}
    base=ri.pair_insurance(p,s)
    rows=[]
    for scale in [0,.5,1,1.5,2]:
        raw=3000*.40*(1800/3000)*(ri.PAIR_CAPTURE_SCALE*scale)
        capped=min(3000*ri.MAX_PAIR_INSURANCE_PCT,raw)
        rows.append({"capture_scale_multiplier":scale,"pair_value":round(capped,2)})
    return {
      "configured_pair_value_example":base,
      "sweep":rows,
      "max_pair_pct":ri.MAX_PAIR_INSURANCE_PCT,
      "portfolio_cap":ri.MAX_PORTFOLIO_ADJUSTMENT,
      "acceptance_fit_cap":ri.MAX_ACCEPTANCE_FIT_SHIFT,
    }

def behavioral_leverage():
    bi=load_module(SCRIPT/"behavioral_intelligence_v3.py","bi_sens")
    sw=bi.SOURCE_WEIGHT
    return {
      "source_weights":sw,
      "trade_vs_draft_weight_ratio":round(sf(sw["trade"])/sf(sw["draft"]),3),
      "trade_vs_acquisition_weight_ratio":round(sf(sw["trade"])/sf(sw["acquisition"]),3),
      "need_floor":bi.NEED_FLOOR,
      "opportunity_smoothing":bi.OPPORTUNITY_SMOOTHING,
      "residual_scale_multiplier":3.0,
      "confidence_sample_scale":7.0,
    }

def pick_model_leverage():
    return {
      "annual_future_pick_discount":.88,
      "one_extra_year_value_change_pct":-12.0,
      "early_tier_multiplier_fallback":1.18,
      "late_tier_multiplier_fallback":.84,
      "early_vs_late_ratio":round(1.18/.84,3),
      "own_pick_control_bonus":.10,
      "pick_quality_strength_mix":"current/dynasty horizon blend then 72% structural weakness + 28% fragility",
      "note":"These are active heuristic mechanics in GM 3.0; they are not calibrated probabilities."
    }

def current_fsffl_vs_market():
    p=DATA/"fsffl_asset_values.json"
    if not p.exists():
        return {"available":False}
    raw=json.loads(p.read_text(encoding="utf-8"))
    rows=[]
    for x in raw.get("players") or []:
        m=sf(x.get("market_dynasty")); v=sf(x.get("fsffl_value"))
        if m>0 and v>0:
            rows.append({"name":x.get("name"),"ratio":v/m,"market_dynasty":m,"fsffl_value":v})
    if not rows:return {"available":False}
    rows.sort(key=lambda x:x["ratio"])
    vals=[x["ratio"] for x in rows]
    def q(pct):
        return vals[int((len(vals)-1)*pct)]
    return {
      "available":True,
      "players":len(rows),
      "min_ratio":round(vals[0],4),
      "p05_ratio":round(q(.05),4),
      "median_ratio":round(q(.50),4),
      "p95_ratio":round(q(.95),4),
      "max_ratio":round(vals[-1],4),
      "largest_discounts":rows[:10],
      "largest_premiums":list(reversed(rows[-10:])),
    }

def projection_leverage():
    return {
      "history_window_seasons":3,
      "min_player_games_for_individual_volatility":8,
      "individual_history_weight_cap":.75,
      "individual_history_full_weight_denominator_games":32,
      "position_sd_floors":{"QB":4.0,"RB":3.5,"WR":3.8,"TE":3.0},
      "weekly_median_shift_sd_multiplier":.08,
      "mean_projection_source":"Razzball season projection scored under FSFFL rules",
    }

def main():
    payload={
      "model_version":MODEL_VERSION,
      "package_discount_divergence":package_curve(),
      "trade_score_elasticity":trade_score_elasticity(),
      "gm_config_adjustment_envelopes":gm_config_envelopes(),
      "state_weight_source_divergence":state_weight_divergence(),
      "roster_interaction_parameter_sensitivity":roster_interaction_elasticity(),
      "behavioral_parameter_leverage":behavioral_leverage(),
      "pick_model_parameter_leverage":pick_model_leverage(),
      "projection_parameter_leverage":projection_leverage(),
      "current_fsffl_value_vs_market":current_fsffl_vs_market(),
    }
    (OUT/"model_sensitivity_audit.json").write_text(json.dumps(payload,indent=2),encoding="utf-8")
    print(json.dumps(payload,indent=2))

if __name__=="__main__":
    main()
