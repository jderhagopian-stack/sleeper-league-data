#!/usr/bin/env python3
"""Audit for legacy mechanics that regain authority after governed replacements exist."""
from __future__ import annotations
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"data"/"audit"/"legacy_governance_bypass_audit.json"

def read(rel):
    return (ROOT/rel).read_text(encoding="utf-8")

def main():
    gm3=read("script/gm3/application.py")
    dl=read("script/decision_lab_state_aware.py")
    v20=read("script/run_trade_market_sweep_v20.py")
    state=read("script/trade_state_policy.py")\n    utility=read("script/decision_utility.py")\n    bilateral=read("script/trade_bilateral_gate.py")
    hist=read("script/trade_decision/historical_behavior_policy.py")
    high=read("script/nonprojection_high_priority_overrides.py")
    ggov=read("script/gm30_nonprojection_governance.py")

    gm3_order=[
      gm3.index("gm30.patch_gm22_runtime(season)"),
      gm3.index("high_priority.install(gm30.core)"),
      gm3.index("gm30_gov.install(gm30.core)"),
      gm3.index("gm30.core._u_team_objective_weights = simulator_authoritative_objective_weights"),
      gm3.index("package_robustness.install(gm30.core)"),
    ]
    dl_order=[
      dl.index("gm30.patch_gm22_runtime(season)"),
      dl.index("high_priority.install(gm30.core)"),
      dl.index("gm30_gov.install(gm30.core)"),
      dl.index("gm30.core._u_team_objective_weights = continuous_team_objective_weights"),
    ]

    findings={
      "gm3_governance_install_order_correct":gm3_order==sorted(gm3_order),
      "decision_lab_governance_install_order_correct":dl_order==sorted(dl_order),
      "decision_lab_installs_high_priority_nonprojection_governance":"high_priority.install(gm30.core)" in dl,
      "decision_lab_installs_gm30_pick_governance":"gm30_gov.install(gm30.core)" in dl,
      "categorical_state_weight_fallback_removed":"def fallback_weights(" not in v20 and "resolved = utility.score(sim)" in v20 and "categorical fallback weights are forbidden" in utility,
      "categorical_title_loss_cap_disabled":"engine.contender_title_cap = lambda state: None" in v20 and "engine.contender_title_cap(state)" not in v20,
      "categorical_behavior_state_table_removed":all(x not in state for x in ('if state == "elite_contender"','elif state == "contender"','elif state == "retool"','elif state == "rebuild"')),
      "categorical_behavior_conditioning_forbidden":"categorical_state_conditioning_authorized" in state,\n      "categorical_buyer_gate_removed":"FSFFL-Bilateral-Buyer-Gate-2.0" in bilateral and "buyer_decision_utility_score" in bilateral and all(x not in bilateral for x in ('state == "elite_contender"','state == "contender"','state == "retool"','state == "rebuild"')),
      "historical_same_state_analysis_preserved":"historical_behavior_prefers_same_state_samples" in hist and "owner_state_profile(uid, state)" in hist,
      "historical_same_state_categorical_rescaling_removed":"categorical_state_rescaling_authorized" in hist and all(x not in hist for x in ('if state == "elite_contender"','elif state == "contender"','elif state == "retool"','elif state == "rebuild"')),
      "market_momentum_incremental_value_removed":"incremental_adjustment_authorized" in high and "market_momentum_incremental_value_removed" in high,
      "own_pick_control_bonus_removed":"own_pick_control_incremental_value_authorized" in high and "own_pick_control_incremental_value_authorized" in ggov,
      "forecast_uncertainty_incremental_pick_value_removed":"forecast_uncertainty_incremental_value_authorized" in ggov,
      "duplicate_pick_incremental_premiums_removed":"quality_optionality_incremental_value_authorized" in high and "liquidity_incremental_value_authorized" in high,
    }
    passed=all(findings.values())
    payload={
      "model_version":"FSFFL-Legacy-Governance-Bypass-Audit-1.0",
      "passed":passed,
      "policy":{
        "replacement_file_presence_is_not_sufficient":True,
        "production_and_hypothetical_paths_must_install_same_governance":True,
        "provisional_categorical_state_labels_must_not_create_decision_cliffs":True,
        "legacy_mechanics_may_remain_for_reproducibility_but_not_regain_current_authority":True,
      },
      "findings":findings,
      "confirmed_regressions_closed":[
        "GM3 stepwise state-weight function bypass",
        "Decision Lab non-projection governance bypass",
        "Trade Decision categorical championship-loss cap",
        "Trade Decision categorical state-weight fallback",
        "Trade Decision categorical state-conditioned behavior table",\n        "Trade Decision categorical buyer feasibility thresholds",
        "Historical same-state BI categorical compatibility multipliers",
      ],
    }
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(payload,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(payload,indent=2))
    if not passed:
      raise SystemExit("Legacy governance bypass audit failed")

if __name__=="__main__":
    main()
