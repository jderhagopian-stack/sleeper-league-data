#!/usr/bin/env python3
"""Audit cross-asset parameter provenance, with special focus on Superflex QB and picks."""
from __future__ import annotations
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
ENGINE=ROOT/"script/build_fsffl_gm_engine.py"
OVERRIDE=ROOT/"script/nonprojection_high_priority_overrides.py"
UTILITY=ROOT/"script/decision_utility.py"
OUT=ROOT/"data/audit/cross_asset_parameter_provenance.json"

def main():
    e=ENGINE.read_text(encoding="utf-8")
    o=OVERRIDE.read_text(encoding="utf-8")
    u=UTILITY.read_text(encoding="utf-8")
    required={
      "pick_external_interpolation":"canonical_simulator_percentile_continuous_external_market_interpolation" in o,
      "pick_control_disabled":"own_pick_control_incremental_value_authorized" in o and 'out["own_pick_control_bonus"] = 0.0' in o,
      "pick_premiums_disabled":'"pick_round_quality_optionality_liquidity_premiums_incremental_value_authorized": False' in o,
      "market_scarcity_zero": "scarcity_premium = 0.0" in e,
      "final_utility_no_position_multiplier":"position_multiplier" not in u and "qb_multiplier" not in u.lower(),
      "final_utility_uses_four_channels":'required = ("current", "future", "liquidity", "resilience")' in u,
      "qb_search_liquidity_adjustment":'if pos == "QB":\n        base += 0.08' in e,
      "fallback_pick_tier_constants":'{"early": 1.18, "mid": 1.0, "late": 0.84}' in e,
      "fallback_pick_time_constant":"0.88 **" in e,
      "last_resort_pick_mids":"1: 5200.0" in e and "2: 2350.0" in e and "3: 1050.0" in e,
    }
    if not all(required.values()):
        missing=[k for k,v in required.items() if not v]
        raise SystemExit("cross-asset runtime changed; re-audit: "+repr(missing))

    findings=[
      {
        "parameter_id":"CROSS-ASSET-QB-FINAL-MULTIPLIER-001",
        "parameter_name":"explicit final QB/Superflex multiplier",
        "current_value":0.0,
        "runtime_authority":"ABSENT_FROM_SHARED_DECISION_UTILITY",
        "evidence_classification":"RULE_DEFINED",
        "identifiability_class":"SIMULATION_IDENTIFIABLE",
        "recommended_action":"KEEP",
        "reason":"Superflex eligibility and legal lineup reoptimization can generate roster-specific QB opportunity cost endogenously; no extra final position coefficient is currently required.",
        "promotion_evidence":"Only introduce a residual QB multiplier if held-out evidence shows systematic cross-asset error after canonical simulation, market value and replacement depth."
      },
      {
        "parameter_id":"CROSS-ASSET-MARKET-SCARCITY-PREMIUM-001",
        "parameter_name":"incremental market-tier scarcity premium",
        "current_value":0.0,
        "runtime_authority":"STRUCTURALLY_DISABLED",
        "evidence_classification":"LEGACY_ARBITRARY_HEURISTIC",
        "identifiability_class":"UNIDENTIFIED_OR_DUPLICATE",
        "recommended_action":"ELIMINATE",
        "reason":"Market tier is derived from the same external dynasty market anchor; paying it again would double count scarcity.",
        "promotion_evidence":"Reintroduction only with time-ordered residual evidence beyond the market anchor."
      },
      {
        "parameter_id":"PICK-POINT-ESTIMATE-001",
        "parameter_name":"future pick point estimate",
        "current_value":"Stats Guy/external pick cells with continuous Simulator competitive-percentile interpolation",
        "runtime_authority":"ACTIVE_WHEN_EXTERNAL_PICK_ANCHOR_AVAILABLE",
        "evidence_classification":"EVIDENCE_BASED_EXTERNAL_ANCHOR",
        "identifiability_class":"SIMULATION_IDENTIFIABLE",
        "recommended_action":"KEEP",
        "reason":"Replaces hard team-tier cliffs with continuous team-strength interpolation over observed pick-market cells.",
        "promotion_evidence":"Validate frozen forecast-to-realized pick-slot/value cohorts over multiple horizons; current basis is defensible but not a calibrated slot probability."
      },
      {
        "parameter_id":"PICK-FALLBACK-TIER-001",
        "parameter_name":"fallback early/mid/late multipliers",
        "current_value":{"same_year_missing_shape":{"early":1.18,"mid":1.0,"late":0.84},"last_resort":{"early":1.20,"mid":1.0,"late":0.82}},
        "runtime_authority":"FALLBACK_ONLY_WHEN_EXTERNAL_MARKET_SHAPE_UNAVAILABLE",
        "evidence_classification":"UNVALIDATED_EXPERT_PRIOR",
        "identifiability_class":"DIRECTLY_ESTIMABLE",
        "recommended_action":"RETAIN_AS_GOVERNED_PRIOR",
        "reason":"Functionality fallback is preferable to fabricated precision, but values are hand-set and should not be mistaken for empirical pick probabilities.",
        "promotion_evidence":"Observed external same-round tier ratios and historical frozen market snapshots; eliminate fallback once source coverage is reliably complete."
      },
      {
        "parameter_id":"PICK-FALLBACK-TIME-001",
        "parameter_name":"fallback annual future-pick discount",
        "current_value":0.88,
        "runtime_authority":"FALLBACK_ONLY_WHEN_OBSERVED_SAME_ROUND_TIME_CURVE_UNAVAILABLE",
        "evidence_classification":"UNVALIDATED_EXPERT_PRIOR",
        "identifiability_class":"DIRECTLY_ESTIMABLE",
        "recommended_action":"RE_ESTIMATE",
        "reason":"Observed round-specific market time curves are already preferred; 0.88 is only the legacy bounded fallback.",
        "promotion_evidence":"Versioned external pick-market snapshots across horizons; use observed round-specific decay with uncertainty."
      },
      {
        "parameter_id":"PICK-LAST-RESORT-BASE-001",
        "parameter_name":"last-resort round base values",
        "current_value":{"round1":5200.0,"round2":2350.0,"round3":1050.0},
        "runtime_authority":"EMERGENCY_FUNCTIONALITY_FALLBACK",
        "evidence_classification":"LEGACY_ARBITRARY_HEURISTIC",
        "identifiability_class":"DIRECTLY_ESTIMABLE",
        "recommended_action":"REPLACE_WITH_DATA_DERIVED_SCALE",
        "reason":"These should never outrank available external market evidence; they exist only when the source lacks enough structure to infer a value.",
        "promotion_evidence":"Use latest governed external pick anchor or a versioned league-level fallback derived from observed market ratios."
      },
      {
        "parameter_id":"QB-LIQUIDITY-SEARCH-BONUS-001",
        "parameter_name":"QB liquidity +0.08",
        "current_value":0.08,
        "runtime_authority":"DIAGNOSTIC_AND_SEARCH_CONTEXT; PLAYER_LIQUIDITY_INCREMENTAL_FINAL_UTILITY_DISABLED",
        "evidence_classification":"LEGACY_ARBITRARY_HEURISTIC",
        "identifiability_class":"UNIDENTIFIED_OR_DUPLICATE",
        "recommended_action":"DIAGNOSTIC_ONLY",
        "reason":"External dynasty market and Superflex replacement mechanics already carry substantial QB scarcity information. The bonus should not become a hidden final position multiplier.",
        "promotion_evidence":"Search-recall or market residual evidence demonstrating incremental information beyond the external market anchor and roster-specific replacement cost."
      },
      {
        "parameter_id":"PICK-INCREMENTAL-PREMIUMS-001",
        "parameter_name":"round/quality/optionality/liquidity/control pick hold premiums",
        "current_value":0.0,
        "runtime_authority":"STRUCTURALLY_DISABLED_BY_NONPROJECTION_GOVERNANCE",
        "evidence_classification":"LEGACY_ARBITRARY_HEURISTIC",
        "identifiability_class":"UNIDENTIFIED_OR_DUPLICATE",
        "recommended_action":"ELIMINATE",
        "reason":"The external pick market anchor already prices these concepts jointly; no stable residual incremental premium has been demonstrated.",
        "promotion_evidence":"Only re-enable individually if held-out residual value is demonstrated."
      }
    ]
    report={
      "model_version":"FSFFL-Cross-Asset-Parameter-Provenance-1.0",
      "authority":"RESEARCH_AUDIT_NON_AUTHORITATIVE",
      "production_behavior_changed":False,
      "runtime_checks":required,
      "findings":findings,
      "summary":{
        "finding_count":len(findings),
        "explicit_final_qb_multiplier_active":False,
        "market_tier_scarcity_incremental_premium_active":False,
        "pick_incremental_hold_premiums_active":False,
        "fallback_pick_heuristics_still_exist":True,
        "production_changes_recommended_in_this_pr":0
      },
      "conclusion":"Current final cross-asset economics do not require a new QB multiplier. The weakest remaining pick constants are fallback-only and should be replaced by observed market structure as coverage improves."
    }
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report["summary"],indent=2))

if __name__=="__main__":main()
