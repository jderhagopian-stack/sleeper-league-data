#!/usr/bin/env python3
"""Audit FSFFL shared-core vs application ownership boundaries.

This is an architecture guardrail, not a model-quality test. It prevents
application-specific reasoning from being accidentally promoted into Shared Core
and prevents shared-core modules from depending on application internals.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "data" / "model_governance" / "application_architecture.json"
OUT = ROOT / "data" / "audit" / "application_boundary_audit.json"
MODEL_VERSION = "FSFFL-Application-Boundary-Audit-1.0"


def load():
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def imports_of(path: Path):
    src = path.read_text(encoding="utf-8")
    refs = set()
    for token in re.findall(r"(?:script/)?([A-Za-z0-9_/]+\.py)", src):
        p = ROOT / "script" / Path(token).name if "/" not in token else ROOT / token
        if p.exists():
            refs.add(p.resolve())
    return refs


def main():
    reg = load()
    shared = set()
    for rows in (reg.get("shared_core") or {}).values():
        for p in rows:
            shared.add((ROOT / p).resolve())

    apps = reg.get("applications") or {}
    app_internal = set()
    scaffolding = set()
    all_declared = set(shared)

    for app in apps.values():
        for p in app.get("internal_reasoning") or []:
            rp = (ROOT / p).resolve()
            app_internal.add(rp)
            all_declared.add(rp)
        for p in app.get("migration_scaffolding") or []:
            rp = (ROOT / p).resolve()
            scaffolding.add(rp)
            all_declared.add(rp)
        ep = app.get("entrypoint")
        if ep:
            all_declared.add((ROOT / ep).resolve())

    missing = sorted(str(p.relative_to(ROOT)) for p in all_declared if not p.exists())

    shared_to_app = []
    for p in shared:
        if not p.exists():
            continue
        for dep in imports_of(p):
            if dep in app_internal or dep in scaffolding:
                shared_to_app.append({
                    "shared_module": str(p.relative_to(ROOT)),
                    "application_dependency": str(dep.relative_to(ROOT)),
                })

    v31 = (ROOT / "script" / "run_trade_market_sweep_v31.py").read_text(encoding="utf-8")
    trade_boundary_ok = (
        'SCRIPT / "trade_decision" / "behavior_integration.py"' in v31
        and 'SCRIPT / "trade_decision" / "historical_behavior_policy.py"' in v31
        and 'TRADE_BEHAVIOR = SCRIPT / "trade_behavioral_intelligence.py"' not in v31
        and 'TRADE_HISTORICAL_BEHAVIOR = SCRIPT / "trade_historical_behavior.py"' not in v31
    )


    gm3_facade = (ROOT / "script" / "run_fsffl_gm30_counterfactual_governed.py").read_text(encoding="utf-8")
    gm3_app = (ROOT / "script" / "gm3" / "application.py").read_text(encoding="utf-8")
    gm3_boundary_ok = (
        "from gm3 import application" in gm3_facade
        and "application.run()" in gm3_facade
        and "gm30.patch_gm22_runtime(season)" in gm3_app
        and "legacy_provider_has_current_application_authority" in gm3_app
    )

    gm_runner = (ROOT / "script" / "run_gm300_production_pipeline.sh").read_text(encoding="utf-8")
    draft_boundary_ok = (
        "python script/draft_intelligence/application.py" in gm_runner
        and "python script/build_gm30_prospect_inputs.py" not in gm_runner
        and "python script/build_gm30_prospect_features.py" not in gm_runner
        and "python script/build_gm30_prospect_engine.py" not in gm_runner
    )
    breakout_boundary_ok = (
        "python script/breakout_intelligence/application.py" in gm_runner
        and "python script/build_gm30_emerging_value.py" not in gm_runner
    )

    decision_lab_src = (ROOT / "script" / "run_roster_decision_lab.py").read_text(encoding="utf-8")
    trade_reg = (reg.get("applications") or {}).get("trade_decision") or {}
    sim_alignment = trade_reg.get("decision_lab_simulator_alignment") or {}
    decision_lab_alignment_explicit = (
        'Path("script/run_fsffl_season_simulator_preproduction.py")' in decision_lab_src
        and "current vectorized Simulator implementation" in decision_lab_src
        and sim_alignment.get("status") == "aligned_current_vectorized_simulator"
        and sim_alignment.get("paired_hypothetical_runs_use_canonical_scoring_mechanics") is True
        and sim_alignment.get("final_trade_confirmation_simulations") == 50000
        and sim_alignment.get("migration_required") is False
    )

    behavior_internal = (
        ROOT / "script" / "trade_decision" / "behavior_integration.py"
    ).read_text(encoding="utf-8")
    historical_internal = (
        ROOT / "script" / "trade_decision" / "historical_behavior_policy.py"
    ).read_text(encoding="utf-8")
    interpretation_marked_app_owned = (
        "trade_behavior_interpretation_owned_by_trade_decision" in behavior_internal
        and "historical_behavior_interpretation_owned_by_trade_decision" in historical_internal
        and "canonical_trade_behavioral_intelligence_shared_component" not in behavior_internal
        and "canonical_historical_state_trade_behavior_shared_component" not in historical_internal
    )

    principles = reg.get("principles") or {}
    principles_ok = all([
        principles.get("shared_core_owns_reusable_concepts") is True,
        principles.get("applications_own_application_specific_reasoning") is True,
        principles.get("canonical_within_application_does_not_imply_shared") is True,
        principles.get("promotion_to_shared_requires_domain_generic_behavior_or_real_second_application_consumer") is True,
        principles.get("migration_scaffolding_is_not_permanent_platform_api") is True,
        principles.get("shared_core_ownership_is_conceptual_not_file_exclusivity") is True,
        principles.get("legacy_mechanics_host_does_not_regain_application_authority") is True,
    ])

    required_shared_concepts = {
        "projection_and_uncertainty",
        "valuation_and_future_pick_economics",
        "team_state_evaluation",
        "roster_resolution",
        "lineup_optimization",
        "simulation",
        "behavioral_intelligence",
        "historical_fact_reconstruction",
        "roster_interaction_primitive",
        "league_rules",
    }
    missing_shared_concepts = sorted(
        required_shared_concepts - set((reg.get("shared_core") or {}).keys())
    )

    findings = {
        "registry_files_present": not missing,
        "missing_declared_files": missing,
        "shared_core_has_no_application_internal_dependencies": not shared_to_app,
        "shared_to_application_dependency_violations": shared_to_app,
        "trade_bi_interpretation_owned_by_trade_decision": trade_boundary_ok,
        "trade_interpretation_files_marked_application_owned": interpretation_marked_app_owned,
        "gm3_application_boundary_active": gm3_boundary_ok,
        "draft_intelligence_application_boundary_active": draft_boundary_ok,
        "breakout_intelligence_application_boundary_active": breakout_boundary_ok,
        "decision_lab_simulator_alignment_explicit": decision_lab_alignment_explicit,
        "architecture_principles_present": principles_ok,
        "required_shared_core_concepts_registered": not missing_shared_concepts,
        "missing_shared_core_concepts": missing_shared_concepts,
    }
    passed = all([
        findings["registry_files_present"],
        findings["shared_core_has_no_application_internal_dependencies"],
        findings["trade_bi_interpretation_owned_by_trade_decision"],
        findings["trade_interpretation_files_marked_application_owned"],
        findings["gm3_application_boundary_active"],
        findings["draft_intelligence_application_boundary_active"],
        findings["breakout_intelligence_application_boundary_active"],
        findings["decision_lab_simulator_alignment_explicit"],
        findings["architecture_principles_present"],
        findings["required_shared_core_concepts_registered"],
    ])
    payload = {
        "model_version": MODEL_VERSION,
        "passed": passed,
        "findings": findings,
        "interpretation": {
            "canonical_does_not_mean_shared": True,
            "second_consumer_or_domain_generic_required_for_core_promotion": True,
            "migration_scaffolding_expected_to_consolidate": True,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    if not passed:
        raise SystemExit("FSFFL application boundary audit failed")


if __name__ == "__main__":
    main()
