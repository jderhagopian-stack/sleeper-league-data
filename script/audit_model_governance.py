#!/usr/bin/env python3
"""FSFFL Model Governance Audit 1.0.

Purpose
-------
Inventory and challenge every materially influential hard-coded number used by
the FSFFL modeling stack. This audit does NOT change model outputs. It creates:
1) a machine-readable inventory of numeric assumptions;
2) a curated list of known high-leverage assumptions;
3) an evidence/provenance classification;
4) a ripple-risk map showing where one assumption can propagate.

A parameter is not considered "validated" merely because it is documented.
Validated means empirically estimated/backtested against an appropriate dataset
or directly defined by league/NFL rules.
"""
from __future__ import annotations
import ast, json, re
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "script"
DATA = ROOT / "data"
OUT = DATA / "audit"
OUT.mkdir(parents=True, exist_ok=True)

MODEL_VERSION = "FSFFL-Model-Governance-Audit-1.0"

MODEL_FILES = [
    "build_fsffl_gm_engine.py",
    "build_fsffl_gm30.py",
    "gm_state_weighting.py",
    "calibrate_gm_state_weights.py",
    "run_team_improvement_lab.py",
    "run_team_improvement_lab_v13.py",
    "run_roster_decision_lab.py",
    "run_trade_market_sweep.py",
    "run_trade_market_sweep_v30.py",
    "roster_aware_trade.py",
    "roster_interaction.py",
    "behavioral_intelligence.py",
    "behavioral_intelligence_v3.py",
    "build_behavioral_action_context.py",
    "build_fsffl_weekly_projections.py",
    "build_fsffl_full_projection_universe.py",
    "build_fsffl_scoring_baseline.py",
    "build_gm30_calibration.py",
    "build_gm30_football_intelligence.py",
    "build_gm30_current_catalysts.py",
    "build_gm30_emerging_value.py",
    "build_gm30_prospect_engine.py",
    "build_gm30_prospect_features.py",
    "build_gm30_prospect_inputs.py",
]

KEYWORDS = (
    "weight","threshold","adjust","premium","discount","penalty","bonus","cap",
    "floor","slope","scale","decay","shrink","confidence","utility","score",
    "factor","liquidity","resilience","fragility","uncertainty","risk","title",
    "championship","playoff","break_glass","optionality","preference","market",
    "replacement","starter","package","tier","probability","smoothing",
)

RULE_NUMBERS = {
    0.5: "league half-PPR setting may be a rule",
    12.0: "league team count may be a rule",
    2.0: "Superflex/2QB market setting may be a rule",
    17.0: "NFL regular-season game count",
    18.0: "league roster size may be a rule",
    3.0: "league rookie-draft rounds may be a rule",
}

HIGH_LEVERAGE_FILES = {
    "build_fsffl_gm_engine.py": 3,
    "build_fsffl_gm30.py": 3,
    "gm_state_weighting.py": 3,
    "run_team_improvement_lab.py": 3,
    "run_trade_market_sweep.py": 3,
    "run_trade_market_sweep_v30.py": 3,
    "run_roster_decision_lab.py": 3,
    "roster_interaction.py": 3,
    "build_fsffl_weekly_projections.py": 2,
    "build_fsffl_full_projection_universe.py": 2,
    "behavioral_intelligence.py": 2,
    "behavioral_intelligence_v3.py": 2,
    "build_gm30_current_catalysts.py": 2,
    "build_gm30_emerging_value.py": 2,
    "build_gm30_prospect_engine.py": 2,
    "build_gm30_calibration.py": 1,
}

def source_lines(path):
    return path.read_text(encoding="utf-8").splitlines()

def context(lines, lineno, radius=2):
    a=max(0,lineno-1-radius); b=min(len(lines),lineno+radius)
    return "\n".join(f"{i+1}: {lines[i]}" for i in range(a,b))

def assignment_name(node, parents):
    p=parents.get(node)
    while p is not None:
        if isinstance(p, ast.Assign):
            names=[]
            for t in p.targets:
                if isinstance(t, ast.Name): names.append(t.id)
                elif isinstance(t, ast.Attribute): names.append(t.attr)
            return ",".join(names)
        if isinstance(p, ast.AnnAssign):
            t=p.target
            return t.id if isinstance(t,ast.Name) else t.attr if isinstance(t,ast.Attribute) else ""
        if isinstance(p, ast.keyword):
            return p.arg or ""
        if isinstance(p, ast.Dict):
            try:
                idx=p.values.index(node)
                k=p.keys[idx]
                return k.value if isinstance(k,ast.Constant) else ""
            except Exception: pass
        p=parents.get(p)
    return ""

def numeric_inventory():
    rows=[]
    for name in MODEL_FILES:
        path=SCRIPT/name
        if not path.exists(): continue
        lines=source_lines(path)
        try: tree=ast.parse("\n".join(lines),filename=str(path))
        except SyntaxError as e:
            rows.append({"file":name,"parse_error":str(e)})
            continue
        parents={}
        for p in ast.walk(tree):
            for ch in ast.iter_child_nodes(p): parents[ch]=p
        for n in ast.walk(tree):
            if not isinstance(n,ast.Constant) or isinstance(n.value,bool) or not isinstance(n.value,(int,float)):
                continue
            v=float(n.value)
            if v in (0.0,1.0,-1.0): continue
            nm=str(assignment_name(n,parents) or "")
            line=(lines[n.lineno-1] if 0<n.lineno<=len(lines) else "").strip()
            searchable=(nm+" "+line).lower()
            if not any(k in searchable for k in KEYWORDS):
                continue
            rule_note=RULE_NUMBERS.get(abs(v))
            near=" ".join(lines[max(0,n.lineno-5):min(len(lines),n.lineno+2)]).lower()
            evidence_hint="unknown"
            if any(x in near for x in ("empirical","historical backtest","learned","cross-valid","calibrat")):
                evidence_hint="possibly_empirical_needs_trace"
            elif rule_note:
                evidence_hint="possibly_rule_defined_needs_trace"
            elif any(x in near for x in ("fallback","prior","heuristic","conservative","expert")):
                evidence_hint="explicit_heuristic_or_prior"
            leverage=HIGH_LEVERAGE_FILES.get(name,1)
            rows.append({
                "file":name,"line":n.lineno,"name":nm,"value":v,
                "source_line":line,"context":context(lines,n.lineno),
                "evidence_hint":evidence_hint,"rule_note":rule_note,
                "leverage":leverage,
            })
    return rows

def curated_findings():
    state_cal=DATA/"gm"/"state_weight_calibration.json"
    state_train=DATA/"gm"/"state_weight_training_examples.json"
    findings=[
      {
        "id":"STATE-WEIGHTS-001","severity":"CRITICAL","status":"UNVALIDATED_ACTIVE",
        "component":"Franchise-state weighting",
        "evidence":"gm_state_weighting.py falls back to embedded LEGACY_ANCHORS whenever data/gm/state_weight_calibration.json is absent or invalid.",
        "observation":f"state_weight_calibration.json exists={state_cal.exists()}; state_weight_training_examples.json exists={state_train.exists()}",
        "why_it_matters":"These weights decide how current production, future value, liquidity and resilience are blended throughout GM decisions. A bad anchor can systematically bias every trade and roster decision.",
        "required_action":"Do not call these weights empirically calibrated until a real training artifact passes holdout validation. Run sensitivity analysis and either calibrate or label them expert priors."
      },
      {
        "id":"GM22-CONFIG-001","severity":"CRITICAL","status":"HEURISTIC_ACTIVE",
        "component":"GM 2.2 strategic valuation adjustments inherited by GM 3.0",
        "evidence":"CONFIG contains hard-coded maxima for roster need, owner preference, competitive window, endowment, starter dependency, depth, recent performance, usage, injury, manual intelligence, championship utility and pick preferences.",
        "why_it_matters":"GM 3.0 explicitly inherits GM 2.2 economics. These percentages alter market values before downstream hold values, trade packages, seller utility and recommendations are calculated.",
        "required_action":"Backtest each adjustment family independently; disable or shrink any family that cannot show incremental predictive value."
      },
      {
        "id":"PACKAGE-ECON-001","severity":"HIGH","status":"HEURISTIC_ACTIVE",
        "component":"Multi-asset package economics",
        "evidence":"GM 2.2 package_effective_value_weights = [1.0, 0.92, 0.84].",
        "why_it_matters":"This directly changes the value of 2-for-1 and 3-for-1 trades and can systematically favor consolidation or diversification.",
        "required_action":"Estimate package discount/premium from actual FSFFL and broader dynasty trade data or perform broad sensitivity tests before relying on the exact curve."
      },
      {
        "id":"TEAM-IMPROVEMENT-001","severity":"HIGH","status":"HEURISTIC_ACTIVE",
        "component":"Team Improvement Lab ranking weights",
        "evidence":"v1.4 reduced state weights and added diminishing returns/guardrails, but the replacement numbers remain judgment-based rather than empirically fitted.",
        "why_it_matters":"The ranking layer decides which trades are surfaced as best opportunities. Even if individual simulations are correct, arbitrary utility weights can reorder the entire recommendation list.",
        "required_action":"Treat v1.4 as a safer provisional calibration, not a validated one. Backtest recommendation rankings against historical decisions/outcomes and run parameter-stability analysis."
      },
      {
        "id":"TRADE-SCORE-001","severity":"CRITICAL","status":"HEURISTIC_ACTIVE",
        "component":"Trade Market Sweep post-simulation ranking",
        "evidence":"run_trade_market_sweep.py ranks simulated deals with hard-coded terms including dynasty + .35*break_glass + 25000*title + 5000*playoff - 12000*competitive_externality, plus additional fixed penalties around contender guardrails.",
        "why_it_matters":"This is another high-leverage utility function where a few percentage points of simulated title probability can outweigh thousands of dynasty-value points. It can reorder alternatives even when the raw simulations are unchanged.",
        "required_action":"Replace these multipliers with one canonical, calibrated utility framework shared with other decision modules. Until calibrated, expose raw metric tradeoffs and treat the composite rank as provisional."
      },
      {
        "id":"DECISION-GATES-001","severity":"HIGH","status":"HEURISTIC_ACTIVE",
        "component":"Roster Decision Lab accept/reject bands",
        "evidence":"run_roster_decision_lab.py uses fixed championship-probability thresholds such as -3%, -1% and +1%, combined with dynasty and break-glass signs, to assign accept/lean/reject bands.",
        "why_it_matters":"A deal can cross from one recommendation band to another because of a one-percentage-point threshold even though simulation uncertainty may be larger than that boundary.",
        "required_action":"Calibrate decision thresholds to simulation uncertainty and historical decision outcomes, or replace hard bands with confidence intervals and continuous utility."
      },
      {
        "id":"TRADE-PRESCREEN-001","severity":"HIGH","status":"HEURISTIC_ACTIVE",
        "component":"Trade discovery and plausibility prescreen",
        "evidence":"Market Sweep uses fixed package-size limits, plausibility thresholds, value-ratio bands, protected-asset penalties, need bonuses and top-N pools before deep simulation.",
        "why_it_matters":"A prescreen error is worse than a bad final weight because a good trade can be removed before the simulator ever evaluates it. This creates hidden false negatives.",
        "required_action":"Measure recall against exhaustive searches on historical/small test universes. Tune thresholds for high recall first, then use simulation to rank precision."
      },
      {
        "id":"ROSTER-CUT-001","severity":"HIGH","status":"HEURISTIC_ACTIVE",
        "component":"Automatic roster-cut selection",
        "evidence":"roster_aware_trade.py computes retention cost as base + .12*break_glass + .06*depth + .04*market_dynasty*liquidity, then multiplies starters by 1.75 and core-status categories by 2.00/1.70/1.35/1.12.",
        "why_it_matters":"When a trade forces a cut, this formula chooses which player disappears. A wrong cut choice changes the simulated lineup, franchise value and final trade verdict.",
        "required_action":"Use exhaustive or top-k simulated cut resolution whenever feasible and validate the prescreen against the true best post-cut roster. Treat the retention formula only as a search accelerator."
      },
      {
        "id":"ROSTER-INTERACTION-001","severity":"HIGH","status":"HEURISTIC_ACTIVE",
        "component":"Backfield/roster interaction value",
        "evidence":"MAX_PAIR_INSURANCE_PCT=.12, PAIR_CAPTURE_SCALE=.30, MAX_PORTFOLIO_ADJUSTMENT=600 and MAX_ACCEPTANCE_FIT_SHIFT=.04 are hard-coded.",
        "why_it_matters":"This layer changes franchise value and estimated willingness of the other manager to accept a trade.",
        "required_action":"Until calibrated, keep the layer bounded and informational; empirically estimate insurance value from historical lineup availability or simulated replacement outcomes."
      },
      {
        "id":"BEHAVIOR-001","severity":"HIGH","status":"HEURISTIC_ACTIVE",
        "component":"Behavioral Intelligence 3.0",
        "evidence":"SOURCE_WEIGHT={trade:1.0,draft:.58,acquisition:.22}, OPPORTUNITY_SMOOTHING=1.0, NEED_FLOOR=.30, residual scaling x3 and confidence curve denominator 7 are assumptions.",
        "why_it_matters":"These values influence inferred manager preferences and therefore seller acceptance/target realism. A mistaken preference model can bias which trades the system thinks are attainable.",
        "required_action":"Cross-validate against held-out manager actions. Compare log loss/ranking accuracy versus simpler baselines and ablate every heuristic."
      },
      {
        "id":"PICK-MODEL-001","severity":"HIGH","status":"HEURISTIC_ACTIVE",
        "component":"Future-pick valuation and pick-quality model",
        "evidence":"GM 3.0 uses hard-coded future discounting, tier multipliers, contender/dynasty/fragility blends, early/late scenario formulas, uncertainty growth and own-pick control bonus.",
        "why_it_matters":"Future picks are common trade currency. Small systematic errors compound across trade valuation, rebuild/contender assessments and alternate-history work.",
        "required_action":"Calibrate pick hit distributions and future discount curves from historical FSFFL rookie drafts plus external dynasty market history; separate market discount from expected-player-value uncertainty."
      },
      {
        "id":"PROJECTION-MEAN-001","severity":"CRITICAL","status":"SINGLE_SOURCE_ACTIVE",
        "component":"Season scoring projection mean",
        "evidence":"build_fsffl_scoring_baseline.py currently derives projected scoring from one Razzball season-projection source and recalculates it under FSFFL scoring. There is no demonstrated multi-source ensemble or out-of-sample calibration of the mean projection layer.",
        "why_it_matters":"Expected points, wins, playoff odds and championship odds all begin with player scoring means. A systematic source bias for one player or position can propagate through every simulation-based trade recommendation.",
        "required_action":"Backtest projection errors by position/player tier, compare multiple independent projection sources, and build an empirically weighted ensemble or uncertainty adjustment. Do not let single-source point estimates create overconfident title-probability differences."
      },
      {
        "id":"PROJECTION-VOL-001","severity":"MEDIUM","status":"PARTIALLY_EMPIRICAL",
        "component":"Weekly projection variance",
        "evidence":"Player/position variance is historically estimated, but POSITION_SD_FLOOR, HISTORY_SEASONS=3, MIN_PLAYER_GAMES=8, blending cap .75 and related gates are judgment choices.",
        "why_it_matters":"Variance affects simulated playoff/title probabilities; downstream utility can magnify those probabilities.",
        "required_action":"Backtest calibration coverage and Brier/CRPS by position under alternative floors/windows. Select parameters on holdout seasons."
      },
      {
        "id":"BREAKOUT-001","severity":"MEDIUM","status":"PARTIALLY_EMPIRICAL",
        "component":"Breakout/emerging-value calibration",
        "evidence":"Learned likelihood-ratio weights are empirical on 2018-2025 data, but breakout labels, bin cutoffs, shrink_k=20, and top-fraction threshold selection are researcher choices.",
        "why_it_matters":"The empirical layer is stronger than most modules, but label definitions can determine what the model 'learns' and may create circular conclusions.",
        "required_action":"Run alternate-label robustness tests, temporal holdout tests and sensitivity to shrinkage/bin definitions. Preserve only signals stable across reasonable specifications."
      },
      {
        "id":"STATE-SOURCE-001","severity":"CRITICAL","status":"MULTIPLE_ACTIVE_SOURCES_OF_TRUTH",
        "component":"Competitive-state weighting",
        "evidence":"Competitive-state economics are independently hard-coded in GM 2.2 _u_team_objective_weights, gm_state_weighting.py, and Team Improvement Lab state_weights. The three implementations are not the same function and do not share one calibrated artifact.",
        "why_it_matters":"The same roster can be valued under different win-now/future tradeoffs depending on which workflow is called. This creates path-dependent answers even when every individual module is internally consistent.",
        "required_action":"Create one canonical state-weight service/artifact and make all decision workflows consume it. Until then, treat cross-module state-aware scores as provisional."
      },
      {
        "id":"PACKAGE-SOURCE-001","severity":"CRITICAL","status":"MULTIPLE_ACTIVE_SOURCES_OF_TRUTH",
        "component":"Multi-asset package discounting",
        "evidence":"The inherited GM engine contains CONFIG package_effective_value_weights=[1.0,.92,.84] and GM22 package_weights=[1.0,.78,.62,.50,.42]. Different code paths therefore encode materially different consolidation discounts.",
        "why_it_matters":"A 3-for-1 package can be valued very differently depending on path. That can reverse trade rankings and create the appearance of precision where the underlying package economics are inconsistent.",
        "required_action":"Trace every caller, retire duplicate curves, and calibrate a single package-economics function before assigning authoritative package values."
      },
      {
        "id":"MARKET-AMPLIFICATION-001","severity":"HIGH","status":"AUDIT_REQUIRED",
        "component":"Repeated use of market-derived information",
        "evidence":"FantasyCalc dynasty/redraft value anchors multiple downstream features; 30-day market trend is then added as a separate adjustment, dynasty-vs-redraft spread feeds upside/downside, dynasty level feeds liquidity, and those derived values feed hold/break-glass/trade scoring.",
        "why_it_matters":"The same market information can enter the model multiple times in transformed form. This can amplify consensus movement rather than add independent information.",
        "required_action":"Perform signal-family ablation: market level only, market trend only, dynasty-redraft spread only, then combinations. Keep only incremental predictive contribution and prevent repeated contribution to the same final utility."
      },
      {
        "id":"VALIDATION-SEMANTICS-001","severity":"HIGH","status":"MISLEADING_TERMINOLOGY_RISK",
        "component":"Meaning of 'validated' / 'proven'",
        "evidence":"Several manifests and module descriptions call inherited workflows 'proven' or 'validated' when the checks primarily establish code correctness, regression stability, coverage, or internal invariants. The GM engine itself explicitly labels important value distributions and pick probabilities as heuristics/not calibrated.",
        "why_it_matters":"Passing software tests does not demonstrate that economic weights predict good dynasty decisions. Conflating those concepts can cause us to trust a stable implementation of an unjustified assumption.",
        "required_action":"Separate SOFTWARE_VALIDATED, DATA_QUALITY_VALIDATED, BACKTESTED, OUT_OF_SAMPLE_VALIDATED, and HEURISTIC_PROVISIONAL statuses everywhere."
      },
      {
        "id":"DOUBLE-COUNT-001","severity":"CRITICAL","status":"AUDIT_REQUIRED",
        "component":"Cross-module double counting",
        "evidence":"Current production/market value/team state/injury/usage/title odds appear in multiple layers (market values, GM adjustments, simulator, roster interaction, Team Improvement ranking).",
        "why_it_matters":"Even individually reasonable inputs can be counted twice or three times, creating hidden leverage and false confidence.",
        "required_action":"Build a full dependency graph and perform ablation tests: remove one signal family at a time and measure changes in asset values and recommendation rankings."
      },
    ]
    return findings

def ripple_map():
    return {
      "FantasyCalc market value":[
        "GM 2.2 adjusted player value","GM 3.0 strategic asset profiles",
        "trade package economics","sell leverage","trade opportunities",
        "Team Improvement Lab","Trade Decision reports"
      ],
      "GM state weights":[
        "base franchise value","hold premium","break-glass/minimum-move value",
        "trade strategic utility","roster cuts","Team Improvement rankings"
      ],
      "weekly player projections":[
        "optimized lineups","expected points","expected wins","playoff odds",
        "bye odds","championship odds","Team Improvement utility","trade verdicts"
      ],
      "behavioral intelligence":[
        "owner preference adjustments","seller acceptance fit",
        "market-sweep realism","recommended counters/targets"
      ],
      "future-pick model":[
        "pick market/franchise value","package value","trade fairness",
        "rebuild/contender optionality","alternate-history transactions"
      ],
      "roster interaction":[
        "roster-specific franchise value","buyer acceptance fit","trade ranking"
      ],
    }

def summary(inv, findings):
    sev=defaultdict(int)
    for x in findings: sev[x["severity"]]+=1
    hints=defaultdict(int)
    for x in inv:
        if "parse_error" not in x: hints[x["evidence_hint"]]+=1
    critical_active=sum(1 for x in findings if x.get("severity")=="CRITICAL" and x.get("status") not in {"VALIDATED","RULE_DEFINED"})
    return {
      "model_version":MODEL_VERSION,
      "overall_governance_status":"PROVISIONAL_NOT_FULLY_VALIDATED" if critical_active else "NO_CRITICAL_UNVALIDATED_FINDINGS",
      "critical_unvalidated_findings":critical_active,
      "files_scanned":len({x.get("file") for x in inv}),
      "numeric_assumptions_flagged":sum(1 for x in inv if "value" in x),
      "curated_findings_by_severity":dict(sev),
      "inventory_evidence_hints":dict(hints),
      "audit_principle":"No hard-coded value is considered justified merely because it is bounded, documented or intuitive.",
    }

def markdown(payload):
    s=payload["summary"]
    lines=[
      "# FSFFL Model Governance Audit",
      "",
      "## Executive finding",
      "",
      "The current system contains several high-leverage heuristic parameters that are not yet empirically identified. The largest governance risk is not one isolated constant; it is propagation and possible double counting across market value, franchise value, simulation, behavioral realism and recommendation ranking.",
      "",
      f"- Numeric assumptions flagged for review: **{s['numeric_assumptions_flagged']}**",
      f"- Critical curated findings: **{s['curated_findings_by_severity'].get('CRITICAL',0)}**",
      f"- High curated findings: **{s['curated_findings_by_severity'].get('HIGH',0)}**",
      "",
      "## Critical / high findings",
      ""
    ]
    for x in payload["curated_findings"]:
        if x["severity"] not in ("CRITICAL","HIGH"): continue
        lines += [
          f"### {x['id']} — {x['severity']} — {x['component']}",
          f"**Status:** {x['status']}",
          "",
          x["evidence"],
          "",
          f"**Why it matters:** {x['why_it_matters']}",
          "",
          f"**Required action:** {x['required_action']}",
          ""
        ]
    lines += [
      "## Governance rule",
      "",
      "Before a parameter is promoted to production as 'validated,' it should have one of three defensible origins:",
      "1. directly defined by league/NFL scoring or roster rules;",
      "2. estimated from historical data with out-of-sample validation;",
      "3. an explicitly provisional prior that passes sensitivity tests and is prevented from dominating the result.",
      "",
      "Anything else should be labeled heuristic and included in sensitivity/ablation testing.",
    ]
    return "\n".join(lines)+"\n"

def main():
    inv=numeric_inventory()
    findings=curated_findings()
    payload={
      "summary":summary(inv,findings),
      "curated_findings":findings,
      "ripple_map":ripple_map(),
      "numeric_assumption_inventory":inv,
    }
    (OUT/"model_governance_audit.json").write_text(json.dumps(payload,indent=2),encoding="utf-8")
    (OUT/"model_governance_audit.md").write_text(markdown(payload),encoding="utf-8")
    print(json.dumps(payload["summary"],indent=2))

if __name__=="__main__":
    main()
