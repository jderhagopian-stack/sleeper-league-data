#!/usr/bin/env python3
"""GM 3.0 production architecture validation gate."""
from __future__ import annotations
import ast, json, re, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data"
ENGINE=ROOT/"script"/"build_fsffl_gm30.py"
CONFIG=DATA/"gm3_config.json"
GM=DATA/"gm"

def load(p):
    with Path(p).open("r",encoding="utf-8") as f:
        return json.load(f)

def add(rows,name,passed,detail=None):
    row={"check":name,"passed":bool(passed)}
    if detail is not None:
        row["detail"]=detail
    rows.append(row)

def main():
    checks=[]
    engine=ENGINE.read_text(encoding="utf-8") if ENGINE.exists() else ""
    cfg=load(CONFIG) if CONFIG.exists() else {}
    league=load(DATA/"league.json") if (DATA/"league.json").exists() else {}
    season=str(league.get("season") or "")

    add(checks,"engine_exists",ENGINE.exists())
    add(checks,"config_exists",CONFIG.exists())
    try:
        ast.parse(engine)
        add(checks,"engine_python_syntax",True)
    except SyntaxError as exc:
        add(checks,"engine_python_syntax",False,str(exc))

    add(checks,"league_metadata_has_season",bool(season),season or "missing")
    add(checks,"engine_resolves_season","def resolve_season(" in engine)
    add(checks,"no_current_season_literal_in_engine",
        not bool(season and re.search(r"(?<!\\d)"+re.escape(season)+r"(?!\\d)",engine)))

    fixed=sorted(set(re.findall(r'["\\\'](20\\d{2})_first_(?:expected_slot|band)["\\\']',engine)))
    add(checks,"future_pick_schema_dynamic",not fixed,fixed or "none")

    add(checks,"production_build_has_no_team_prompt_or_env",
        "GM30_USER_ID" not in engine and "resolve_perspective_user_id" not in engine)
    add(checks,"config_build_scope_all_teams",
        cfg.get("build_scope",{}).get("mode")=="ALL_TEAMS_EVERY_RUN")
    add(checks,"engine_builds_team_command_centers",
        "team_command_centers" in engine and 'for team in sim["teams"]' in engine)
    add(checks,"gm_output_root_is_data_gm",'OUT = DATA / "gm"' in engine)
    add(checks,"no_legacy_gm3_output_root",'OUT = DATA / "gm3"' not in engine)
    add(checks,"not_gm22_downstream",
        "downstream_only" not in engine.lower() and "downstream decision layer" not in engine.lower())

    sim_paths={k:v for k,v in cfg.get("paths",{}).items()
               if k.startswith("sim_") and isinstance(v,str)}
    add(checks,"simulator_paths_dynamic",
        bool(sim_paths) and all("{season}" in v for v in sim_paths.values()),sim_paths)

    manifest=GM/"manifest.json"
    centers=GM/"team_command_centers.json"
    if manifest.exists():
        m=load(manifest)
        add(checks,"manifest_league_wide",m.get("scope")=="ALL_TEAMS",m.get("scope"))
        add(checks,"manifest_season_matches",str(m.get("season") or "")==season)
    else:
        add(checks,"manifest_runtime_check_deferred",True,"first run pending")

    if centers.exists():
        c=load(centers)
        expected=int(league.get("total_rosters") or 0)
        add(checks,"runtime_team_views_cover_league",
            isinstance(c,dict) and len(c)==expected,
            {"views":len(c) if isinstance(c,dict) else None,"expected":expected})
    else:
        add(checks,"team_views_runtime_check_deferred",True,"first run pending")

    passed=all(x["passed"] for x in checks)
    print(json.dumps({"validator":"FSFFL-GM-3.0-LEAGUE-WIDE-GATE","passed":passed,"checks":checks},indent=2))
    sys.exit(0 if passed else 1)

if __name__=="__main__":
    main()
