#!/usr/bin/env python3
"""Static governance audit of the production trade-decision path.

The goal is not to certify model quality from source-code shape.  It records
which path is production-authoritative, which heuristic gates have decision
leverage, and where correlated value families are reused in final ranking.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "script"
OUT = ROOT / "data" / "audit"
OUT.mkdir(parents=True, exist_ok=True)

MODEL_VERSION = "FSFFL-Decision-Path-Integrity-Audit-1.0"


def text(name: str) -> str:
    return (SCRIPT / name).read_text(encoding="utf-8")


def main():
    report = text("run_trade_report.py")
    v30 = text("run_trade_market_sweep_v30.py")
    v29 = text("run_trade_market_sweep_v29.py")
    v13 = text("run_trade_market_sweep_v13.py")
    v16 = text("run_trade_market_sweep_v16.py")
    state = text("decision_lab_state_aware.py")
    behavior = text("behavioral_intelligence_v3.py")
    behavior_prod_test = (ROOT / ".github" / "workflows" / "test-behavioral-intelligence-v3-production.yml").read_text(encoding="utf-8")

    production_roster_aware = (
        "run_trade_market_sweep_v30.py" in report
        and "run_trade_market_sweep_v29.py" in v30
        and "legalize_trade_rosters" in v13
        and "forced_cut" in v13
    )
    runtime_version_single_source = (
        "simulation.roster_resolution_model_version" in v29
        and "ROSTER_MODEL=" not in v29.replace("runtime_roster_model", "")
    )

    acceptance_declared_heuristic = (
        "not a calibrated" in v16.lower()
        or "not a calibrated\nprobability" in v16.lower()
        or "heuristic_acceptance_fit_not_probability" in v16
    )
    acceptance_has_authoritative_gate = (
        'in {"HIGH", "MEDIUM"}' in v16
        and "recommended_next_action" in v16
        and "realistic" in v16
    )

    final_overlap_tokens = {
        "dynasty_delta": "market_dynasty_delta" in state,
        "break_glass_delta": "break_glass_delta" in state,
        "liquidity_block": "liquidity_block" in state,
        "strategic_value_delta": "strategic_value_delta" in state,
    }
    final_composite_overlap = all(final_overlap_tokens.values())

    behavior_oos_predictive_test = any(
        token in behavior_prod_test.lower()
        for token in (
            "holdout acceptance", "held-out acceptance", "future acceptance",
            "out-of-sample acceptance", "predictive log loss", "brier score",
        )
    )

    findings = [
        {
            "id": "DECISION-PATH-ROSTER-001",
            "severity": "INFO" if production_roster_aware else "CRITICAL",
            "status": "PRODUCTION_PATH_ROSTER_AWARE" if production_roster_aware else "PRODUCTION_PATH_INTEGRITY_FAILURE",
            "observation": (
                "The production report chain reaches the roster-aware v1.3 simulation path, which legalizes post-trade active rosters before simulation and carries forced cuts into effective actions."
                if production_roster_aware else
                "The production report chain could not be statically verified as roster-aware."
            ),
            "software_invariant": production_roster_aware,
        },
        {
            "id": "DECISION-PATH-VERSION-001",
            "severity": "INFO" if runtime_version_single_source else "HIGH",
            "status": "SINGLE_RUNTIME_SOURCE" if runtime_version_single_source else "DUPLICATE_VERSION_SOURCE",
            "observation": "Production policy metadata must report the resolver version emitted by the simulation that actually ran; no second hard-coded resolver version is authoritative.",
            "software_invariant": runtime_version_single_source,
        },
        {
            "id": "ACCEPTANCE-GATE-001",
            "severity": "HIGH",
            "status": "PROVISIONAL_HIGH_LEVERAGE_HEURISTIC" if acceptance_has_authoritative_gate else "NO_AUTHORITATIVE_HEURISTIC_GATE_DETECTED",
            "observation": (
                "Human acceptance is explicitly described as heuristic rather than probabilistic, but MEDIUM/HIGH hand-set fit bands still gate realistic Top-5 inclusion and can drive the recommended negotiation action. "
                "Until held-out choice/acceptance prediction is available, these thresholds must remain provisional and sensitivity-qualified."
            ),
            "declared_not_probability": acceptance_declared_heuristic,
            "has_authoritative_decision_leverage": acceptance_has_authoritative_gate,
            "authoritative_empirical_claim_allowed": False,
        },
        {
            "id": "FINAL-SCORE-OVERLAP-001",
            "severity": "HIGH",
            "status": "ABLATION_REQUIRED" if final_composite_overlap else "NOT_DETECTED",
            "observation": (
                "The state-aware final score separately uses dynasty, break-glass and liquidity families while also using strategic_value_delta, a downstream GM composite built from current/future/liquidity/resilience value. "
                "This may encode intentional portfolio utility, but incremental value must be established by ablation before the composite can be treated as independent evidence."
            ),
            "detected_components": final_overlap_tokens,
            "authoritative_empirical_claim_allowed": False,
        },
        {
            "id": "BEHAVIOR-OOS-001",
            "severity": "HIGH",
            "status": "PREDICTIVE_HOLDOUT_PRESENT" if behavior_oos_predictive_test else "STRUCTURAL_VALIDATION_ONLY",
            "observation": (
                "Behavioral Intelligence 3 has strong leakage/boundedness/sample-confidence tests, but its production workflow does not demonstrate held-out prediction of future manager acceptance/actions. "
                "Its hand-set blend weights and adjustment caps therefore remain bounded secondary evidence rather than statistically estimated acceptance coefficients."
            ),
            "holdout_predictive_acceptance_test_detected": behavior_oos_predictive_test,
            "authoritative_empirical_claim_allowed": behavior_oos_predictive_test,
        },
    ]

    payload = {
        "model_version": MODEL_VERSION,
        "purpose": "Separate production-path software invariants from empirical decision-model validation.",
        "summary": {
            "production_roster_aware": production_roster_aware,
            "runtime_roster_version_single_source": runtime_version_single_source,
            "provisional_high_leverage_acceptance_gate": acceptance_has_authoritative_gate,
            "final_score_overlap_ablation_required": final_composite_overlap,
            "behavioral_predictive_holdout_detected": behavior_oos_predictive_test,
        },
        "findings": findings,
    }
    (OUT / "decision_path_integrity_audit.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload["summary"], indent=2))

    if not production_roster_aware:
        raise SystemExit("Production trade path failed roster-awareness invariant")
    if not runtime_version_single_source:
        raise SystemExit("Production trade path has a duplicate/stale roster resolver version source")


if __name__ == "__main__":
    main()
