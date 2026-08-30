#!/usr/bin/env python3
"""Audit production architecture so superseded modules cannot regain authority.

Older implementation layers may remain as dependencies when a newer layer
intentionally wraps them. They must not be directly production-authoritative
for decisions that a newer layer supersedes.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "script"
WORKFLOWS = ROOT / ".github" / "workflows"
OUT = ROOT / "data" / "audit"
OUT.mkdir(parents=True, exist_ok=True)

MODEL_VERSION = "FSFFL-Production-Architecture-Authority-Audit-1.0"
NONPRODUCTION_WORKFLOW_MARKERS = (
    "audit", "test", "governance", "consistency", "sensitivity", "validation",
)


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def has_all(src: str, tokens) -> bool:
    return all(token in src for token in tokens)


def production_workflow_sources():
    out = {}
    for path in sorted(WORKFLOWS.glob("*.yml")):
        lower = path.name.lower()
        if any(marker in lower for marker in NONPRODUCTION_WORKFLOW_MARKERS):
            continue
        out[path.name] = text(path)
    return out


def workflow_direct_exec(sources, pattern):
    hits = []
    rx = re.compile(pattern)
    for name, src in sources.items():
        for line in src.splitlines():
            stripped = line.strip()
            if rx.search(stripped):
                hits.append({"workflow": name, "line": stripped})
    return hits


def main():
    trade_report = text(SCRIPT / "run_trade_report.py")
    trade_engine = text(SCRIPT / "trade_engine.py")
    v31 = text(SCRIPT / "run_trade_market_sweep_v31.py")
    option_governance = text(SCRIPT / "trade_option_governance.py")
    roster_overlay = text(SCRIPT / "roster_interaction_overlay.py")
    roster_resolution = text(SCRIPT / "roster_resolution_governance.py")
    candidate_pools = text(SCRIPT / "trade_candidate_pools.py")
    trade_behavior = text(SCRIPT / "trade_behavioral_intelligence.py")
    v30 = text(SCRIPT / "run_trade_market_sweep_v30.py")
    trade_review = text(SCRIPT / "run_trade_review.py")
    gm_runner = text(SCRIPT / "run_gm300_production_pipeline.sh")
    gm_governed = text(SCRIPT / "run_fsffl_gm30_counterfactual_governed.py")
    gm_cf = text(SCRIPT / "run_fsffl_gm30_counterfactual.py")
    gm_gov = text(SCRIPT / "gm30_nonprojection_governance.py")
    high_priority = text(SCRIPT / "nonprojection_high_priority_overrides.py")
    package_curve = text(SCRIPT / "package_curve_robustness.py")
    production_workflows = production_workflow_sources()

    findings = []

    # TRADE DECISION AUTHORITY
    trade_entry_current = (
        has_all(trade_report, [
            "MARKET_SWEEP=Path('script/trade_engine.py')",
            "EXPECTED_ANALYSIS_MODEL='FSFFL-Counter-Market-Sweep-1.25'",
        ])
        and has_all(trade_engine, [
            'CURRENT_ENGINE = SCRIPT / "run_trade_market_sweep_v31.py"',
            'EXPECTED_MODEL_VERSION = "FSFFL-Counter-Market-Sweep-1.25"',
            "load_current_engine().main()",
        ])
    )
    findings.append({
        "id": "TRADE-ENTRY-001",
        "ok": trade_entry_current,
        "severity": "CRITICAL",
        "observation": "Prospective trade reports must enter through the stable trade_engine.py facade, which delegates to the current authoritative v31 implementation and verifies its model version.",
    })

    v31_final_authority = (
        has_all(v31, [
            "v24.main()",
            "trade_behavioral_intelligence.py",
            "trade_behavior.install(v24, bi2, bi3_cache, bi3_cache_status)",
            "trade_behavior.apply_report_metadata(report, bi2, bi3_cache, bi3_cache_status)",
            "trade_candidate_pools.py",
            "candidate_pools.apply_to_report(report)",
            "roster_resolution_governance.py",
            "roster_resolution.apply_to_report(report)",
            "roster_interaction_overlay.py",
            "overlay.apply_to_report(report, interaction, ranker)",
            "trade_option_governance.py",
            "gov.apply_to_report(report)",
            "post_sim_score_is_diagnostic_not_categorical_decision_rule",
        ])
        and "run_trade_market_sweep_v30.py" not in v31
        and "run_trade_market_sweep_v29.py" not in v31
        and "run_trade_market_sweep_v28.py" not in v31
        and "run_trade_market_sweep_v27.py" not in v31
        and "run_trade_market_sweep_v26.py" not in v31
        and has_all(trade_behavior, [
            'BI3_VERSION = "FSFFL-Behavioral-Intelligence-3.0"',
            "def load_bi3_cache():",
            "def install(v24, bi2, bi3_cache, cache_status):",
            "def apply_report_metadata(report, bi2, bi3_cache, cache_status):",
            "bi3_context_normalized_position_signal_enabled",
        ])
        and has_all(candidate_pools, [
            "def apply_to_report(report):",
            "suggested_counteroffers",
            "market_sweep_alternatives",
            "continuous_state_aware_score_controls_focal_option_eligibility",
        ])
        and has_all(roster_resolution, [
            "def runtime_roster_model(report):",
            "def apply_to_report(report):",
            "simulation.roster_resolution_model_version",
        ])
        and has_all(roster_overlay, [
            "def apply_to_report(report, interaction, ranker):",
            "negotiation_ranking.recompute_from_row",
            "legacy_v30_option_comparison_not_executed_in_current_path",
        ])
        and has_all(option_governance, [
            "def compare(row, current):",
            "def recompute_action(report, inherited):",
            'row["comparison_to_current_offer"] = comp',
            'report["recommended_next_action"] = final_action',
            "DIAGNOSTIC_ONLY_NOT_CATEGORICAL_DECISION_RULE",
        ])
    )
    findings.append({
        "id": "TRADE-AUTHORITY-002",
        "ok": v31_final_authority,
        "severity": "CRITICAL",
        "observation": "Current v31 must preserve the latest production BI3-over-BI2 trade behavior through the shared behavioral component, bypass historical v26-v30, then apply shared candidate-pool, roster-resolution, roster-interaction, and option-governance components.",
    })

    v30_contains_superseded_decision_logic = (
        "if score_delta > 750" in v30 and "elif score_delta < -750" in v30
    )
    v30_currently_executed = "run_trade_market_sweep_v30.py" in v31
    findings.append({
        "id": "TRADE-LEGACY-003",
        "ok": not v30_currently_executed,
        "severity": "INFO" if not v30_currently_executed else "CRITICAL",
        "observation": (
            "Historical v30 still contains its superseded comparison logic for reproducibility, but the current production path no longer executes v30."
            if v30_contains_superseded_decision_logic and not v30_currently_executed else
            "Superseded v30 decision logic has regained a current production execution path."
            if v30_currently_executed else
            "No superseded v30 comparison rule detected."
        ),
        "legacy_logic_present": v30_contains_superseded_decision_logic,
        "historical_v30_executed_in_current_path": v30_currently_executed,
    })

    old_trade_hits = workflow_direct_exec(
        production_workflows,
        r"(?:^|\s)python\s+script/run_trade_market_sweep_v(?:[1-9]|1\d|2\d|30)\.py(?:\s|$)",
    )
    findings.append({
        "id": "TRADE-WORKFLOW-004",
        "ok": not old_trade_hits,
        "severity": "CRITICAL",
        "observation": "No production GitHub Actions path may directly execute a superseded prospective trade-sweep version.",
        "direct_execution_hits": old_trade_hits,
    })

    # The bilateral Trade Review is a separate retrospective product. Shared
    # lineup mechanics must come from the version-neutral optimizer rather than
    # a historical trade-sweep wrapper.
    v13_uses = re.findall(r"\bv13\.([A-Za-z_][A-Za-z0-9_]*)\s*\(", trade_review)
    shared_lineup_optimizer = (
        "lineup_optimizer.py" in trade_review
        and "fast_reoptimize_touched_lineups" in trade_review
        and not v13_uses
    )
    retrospective_separate = (
        "Retrospective bilateral evaluation of a completed trade" in trade_review
        and shared_lineup_optimizer
    )
    findings.append({
        "id": "TRADE-REVIEW-005",
        "ok": retrospective_separate,
        "severity": "CRITICAL",
        "observation": "Retrospective Trade Review must use the shared lineup optimizer and must not regain historical trade-sweep decision authority.",
        "shared_lineup_optimizer": shared_lineup_optimizer,
        "v13_helper_calls": sorted(set(v13_uses)),
    })

    # GM 3.0 AUTHORITY AND PATCH ORDER
    gm_runner_governed = "python script/run_fsffl_gm30_counterfactual_governed.py" in gm_runner
    findings.append({
        "id": "GM3-ENTRY-001",
        "ok": gm_runner_governed,
        "severity": "CRITICAL",
        "observation": "GM 3.0 production must enter through the governed wrapper.",
    })

    expected_order = [
        "gm30.patch_gm22_runtime(season)",
        "high_priority.install(gm30.core)",
        "gm30_gov.install(gm30.core)",
        "package_robustness.install(gm30.core)",
        "counterfactual.install_counterfactual_trade_patch()",
        "gm30.main()",
    ]
    positions = [gm_governed.find(x) for x in expected_order]
    gm_patch_order_ok = all(x >= 0 for x in positions) and positions == sorted(positions)
    findings.append({
        "id": "GM3-PATCH-002",
        "ok": gm_patch_order_ok,
        "severity": "CRITICAL",
        "observation": "Runtime adaptation must occur first; current governance/overrides must wrap it; counterfactual simulation must wrap the fully governed trade generator last.",
        "expected_order": expected_order,
    })

    gm_composition_ok = has_all(high_priority, [
        "original_pick_profile = getattr(engine, \"_u_pick_profile\", None)",
        "out = dict(original_pick_profile(aid, uid, ctx))",
    ]) and has_all(gm_gov, [
        "original_pick_profile = core._u_pick_profile",
        "out = dict(original_pick_profile(aid, uid, ctx))",
    ]) and has_all(package_curve, [
        "original=core.build_universal_trade_opportunities",
        "runs={name:_run_curve(core,original,uid,ctx,profile_by_uid,weights)",
    ]) and has_all(gm_cf, [
        "original = gm30.core.build_universal_trade_opportunities",
        "payload = original(uid, ctx=ctx, profile_by_uid=profile_by_uid)",
    ])
    findings.append({
        "id": "GM3-COMPOSE-003",
        "ok": gm_composition_ok,
        "severity": "CRITICAL",
        "observation": "Newer GM3 layers must wrap the already-current function rather than reload and overwrite an older implementation independently.",
    })

    ungoverned_gm_hits = workflow_direct_exec(
        production_workflows,
        r"(?:^|\s)python\s+script/run_fsffl_gm30_counterfactual\.py(?:\s|$)",
    )
    findings.append({
        "id": "GM3-BYPASS-004",
        "ok": not ungoverned_gm_hits,
        "severity": "CRITICAL",
        "observation": "No production workflow may bypass the governed GM3 wrapper by executing the ungoverned counterfactual entry point directly.",
        "direct_execution_hits": ungoverned_gm_hits,
    })

    failed = [x for x in findings if not x["ok"]]
    warnings = [x for x in findings if x["severity"] == "WARN"]
    payload = {
        "model_version": MODEL_VERSION,
        "policy": {
            "older_modules_may_supply_mechanics": True,
            "older_modules_may_supersede_newer_decision_authority": False,
            "newest_authoritative_layer_must_recompute_superseded_outputs": True,
            "production_bypass_paths_for_superseded_versions_allowed": False,
        },
        "production_workflows_scanned": sorted(production_workflows),
        "summary": {
            "passed": not failed,
            "finding_count": len(findings),
            "failure_count": len(failed),
            "warning_count": len(warnings),
        },
        "findings": findings,
    }
    path = OUT / "production_architecture_authority_audit.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    if failed:
        raise SystemExit("Production architecture authority audit failed: " + ", ".join(x["id"] for x in failed))


if __name__ == "__main__":
    main()
