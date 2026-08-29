#!/usr/bin/env python3
"""Run FSFFL weekly distributions from the internal interim season means."""
from __future__ import annotations
import json
from pathlib import Path
import build_fsffl_weekly_projections as weekly

ORIGINAL=weekly.load_json
def prefer_interim(path:Path):
    path=Path(path)
    if path.name=="preseason_fsffl_points.json":
        interim=path.with_name("interim_preseason_fsffl_points.json")
        if interim.exists(): return ORIGINAL(interim)
    return ORIGINAL(path)
def write(path,payload):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8")
def main():
    weekly.load_json=prefer_interim; weekly.main()
    league=ORIGINAL(weekly.DATA/"league.json"); season=str(league.get("season"))
    root=weekly.SIM_ROOT/season
    src=ORIGINAL(root/"sources"/"interim_preseason_fsffl_points.json")
    out=ORIGINAL(root/"inputs"/"player_weekly_projections.json")
    audit=ORIGINAL(root/"outputs"/"weekly_projection_audit.json")
    out["source"]="FFToday interim raw-stat season means with Native V2 fallback; weekly distribution width calibrated from historical NFL outcomes"
    out["model_stage"]="interim_external_season_means_schedule_neutral_weekly"
    out["season_mean_model"]="FFToday-Interim-Raw-Stats"
    out["external_projection_values_used"]=True
    out["external_projection_blend_enabled"]=False
    out["interim_projection_audit"]=src.get("audit")
    write(root/"inputs"/"player_weekly_projections.json",out)
    audit["season_mean_source"]="interim_preseason_fsffl_points.json"
    audit["interim_projection_audit"]=src.get("audit")
    audit["external_projection_values_used"]=True
    audit["deployment_scope"]="INTERNAL_PRIVATE_INTERIM_ONLY"
    audit.setdefault("important_limitations",[])
    audit["important_limitations"].append("Interim external projection source is not approved for commercial reuse.")
    write(root/"outputs"/"weekly_projection_audit.json",audit)

    # Once a validated opponent scorecard is versioned, every normal interim
    # production run automatically redistributes weekly means by matchup.
    scorecard_path=weekly.DATA/"model_validation"/"weekly_opponent_adjustment_scorecard.json"
    opponent_applied=False
    if scorecard_path.exists():
        import validate_apply_weekly_opponent_adjustment as opp
        card=ORIGINAL(scorecard_path)
        opp.apply_current(int(season),league.get("scoring_settings") or {},card)
        opponent_applied=True

    print(json.dumps({"status":"PASS","season":season,"weekly_players":len(out.get("players") or {}),
                      "external_projection_values_used":True,
                      "opponent_adjustment_applied":opponent_applied,
                      "weekly_quality_gate_passed":(audit.get("quality_gate") or {}).get("passed")},indent=2))
if __name__=="__main__": main()
