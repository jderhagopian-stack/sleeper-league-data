#!/usr/bin/env python3
"""Audit rule-defined playoff, seeding, and lineup architecture."""
from __future__ import annotations

import importlib.util
import json
import random
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"script"
DATA=ROOT/"data"
OUT=DATA/"audit"
OUT.mkdir(parents=True,exist_ok=True)

sys.path.insert(0,str(SCRIPT))
spec=importlib.util.spec_from_file_location("season_sim",SCRIPT/"build_fsffl_season_simulator.py")
sim=importlib.util.module_from_spec(spec)
spec.loader.exec_module(sim)


def main():
    league=json.loads((DATA/"league.json").read_text(encoding="utf-8"))
    settings=league.get("settings") or {}
    src=(SCRIPT/"build_fsffl_season_simulator.py").read_text(encoding="utf-8")

    # Synthetic four-division league. A stronger wildcard must remain behind
    # all four division champions because Sleeper division winners get top seeds.
    records={1:10,2:9,3:8,4:7,5:6,6:12,7:5,8:11}
    pf={rid:1000+rid for rid in records}
    pa={rid:900+rid for rid in records}
    divisions={1:1,2:1,3:2,4:2,5:3,6:3,7:4,8:4}
    order=sim.seed_teams(records,pf,pa,divisions=divisions,playoff_teams=6)
    division_winners={1,3,6,8}

    no_div_order=sim.seed_teams(records,pf,pa,divisions={},playoff_teams=6)
    higher_seed_tie_champs={}
    for n in (4,6,8):
        weeks=list(range(15,15+sim.playoff_round_count(n)))
        lineups={rid:{w:[] for w in weeks} for rid in range(1,n+1)}
        higher_seed_tie_champs[n]=sim.simulate_playoffs(
            list(range(1,n+1)),lineups,weeks,random.Random(1)
        )

    current={
        "team_count":int(settings.get("num_teams") or league.get("total_rosters") or 0),
        "divisions":int(settings.get("divisions") or 0),
        "playoff_teams":int(settings.get("playoff_teams") or 0),
        "playoff_week_start":int(settings.get("playoff_week_start") or 0),
        "playoff_round_type":int(settings.get("playoff_round_type") or 0),
        "playoff_seed_type":int(settings.get("playoff_seed_type") or 0),
    }

    runtime={
        "division_winners_get_top_seeds":set(order[:4])==division_winners,
        "wildcard_cannot_displace_division_winner":order.index(2)>=4,
        "no_division_mode_uses_overall_standings":no_div_order[0]==6 and no_div_order[1]==8,
        "four_team_round_count":sim.playoff_round_count(4)==2,
        "six_team_round_count":sim.playoff_round_count(6)==3,
        "eight_team_round_count":sim.playoff_round_count(8)==3,
        "six_team_byes":sim.first_round_byes(6)==2,
        "four_team_no_byes":sim.first_round_byes(4)==0,
        "eight_team_no_byes":sim.first_round_byes(8)==0,
        "higher_seed_wins_ties_in_supported_brackets":higher_seed_tie_champs=={4:1,6:1,8:1},
        "configured_playoff_weeks_not_15_17_literal":(
            "for w in [15,16,17]" not in src
            and "reg_weeks + [15,16,17]" not in src
            and "Weeks 15-17 projection coverage" not in src
        ),
        "six_team_bracket_not_hardcoded":(
            "if len(seed_order) < 6" not in src
            and "seed_order[:6]" not in src
        ),
        "team_count_not_defaulted_to_12":'"ROSTERS_12"' not in src,
        "canonical_lineup_eligibility_used":(
            "slot_eligible_positions" in src
            and "normalize_position" in src
        ),
    }

    report={
        "schema_version":"1.0",
        "audit_family":"rule-defined playoff and seeding architecture",
        "production_projection_formula_behavior_changed":False,
        "production_simulator_rule_behavior_changed":True,
        "current_league":current,
        "policy":{
            "playoff_team_count_comes_from_league_settings":True,
            "playoff_start_comes_from_league_settings":True,
            "division_winners_receive_top_seeds_when_divisions_exist":True,
            "standard_sleeper_brackets_supported":[4,6,8],
            "playoff_ties_advance_higher_seed":True,
            "alternate_multiweek_round_semantics_not_guessed":True,
            "nonstandard_playoff_round_type_requires_separate_verified_mapping":True,
        },
        "runtime_markers":runtime,
        "findings":[
            {
                "id":"PLAYOFF-DIVISION-SEEDING-001",
                "severity":"CRITICAL",
                "status":"FIXED_RULE_DEFINED",
                "observation":"The prior simulator ranked all teams together and ignored configured divisions. Division winners now receive top seeds before wild cards.",
                "evidence_tier":"RULE_DEFINED",
                "authoritative_use":True,
            },
            {
                "id":"PLAYOFF-BRACKET-HARDCODE-001",
                "severity":"HIGH",
                "status":"FIXED_RULE_DEFINED",
                "observation":"The prior simulator hardcoded six teams, two byes and Weeks 15-17. Standard 4/6/8-team bracket length and bye count now derive from league settings.",
                "evidence_tier":"RULE_DEFINED",
                "authoritative_use":True,
            },
            {
                "id":"PLAYOFF-ROUND-TYPE-001",
                "severity":"MEDIUM",
                "status":"QUALIFIED_UNMAPPED_IF_NONSTANDARD",
                "observation":"Sleeper exposes playoff_round_type, but alternate/multiweek semantics are not inferred from an undocumented numeric code. Current FSFFL value is recorded; nonstandard behavior requires verified mapping before authoritative simulation.",
                "evidence_tier":"RULE_DEFINED_VALUE_WITH_UNVERIFIED_EXTERNAL_SEMANTICS",
                "authoritative_use":False,
            },
        ],
    }
    (OUT/"playoff_configuration_audit.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report,indent=2))

    missing=[k for k,v in runtime.items() if not v]
    if missing:
        raise SystemExit(f"Playoff-rule regression failed: {missing}")
    if current["playoff_teams"] not in (4,6,8):
        raise SystemExit("Current league uses an unsupported Sleeper playoff bracket size")

if __name__=="__main__":
    main()
