#!/usr/bin/env python3
"""Permanent end-to-end FSFFL decision-path integration audit.

This audit verifies composition/authority invariants that component-level
governance audits can miss. It deliberately does not fit coefficients or
evaluate projection quality.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "script"
DATA = ROOT / "data"
OUT = DATA / "audit" / "decision_path_integration_audit.json"
MODEL_VERSION = "FSFFL-Decision-Path-Integration-Audit-1.0"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to import {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def synthetic_sim(*, externality=False, authorized=False):
    strategic = {
        "market_dynasty_delta": 700.0,
        "liquidity_value_delta": 0.0,
        "resilience_value_delta": 0.0,
        "baseline_team_market_redraft_value": 18000.0,
        "objective_weights": {
            "current": 0.40,
            "future": 0.30,
            "liquidity": 0.15,
            "resilience": 0.15,
        },
        "incremental_channel_authorization": {
            "current": True,
            "future": True,
            "liquidity": authorized,
            "resilience": authorized,
        },
    }
    sim = {
        "focus_delta": {
            "expected_points_for": 30.0,
            "expected_wins": 0.30,
            "playoff_probability": 0.03,
            "bye_probability": 0.01,
            "championship_probability": 0.03,
        },
        "league_reference": {
            "expected_points_for_mean": 1500.0,
            "expected_wins_mean": 7.0,
            "playoff_probability_mean": 0.50,
            "championship_probability_mean": 1.0 / 12.0,
        },
        "strategic": strategic,
    }
    if externality:
        sim["buyer_championship_probability_delta"] = 0.05
        sim["net_title_equity_swing_against_focus"] = 0.02
    return sim


def main():
    utility = load(SCRIPT / "decision_utility.py", "decision_path_audit_utility")
    attribution = load(SCRIPT / "decision_attribution.py", "decision_path_audit_attribution")

    du = text(SCRIPT / "decision_utility.py")
    dl = text(SCRIPT / "decision_lab_state_aware.py")
    ti = text(SCRIPT / "run_team_improvement_lab_v16.py")
    facade = text(SCRIPT / "gm3" / "team_improvement.py")
    trade_v20 = text(SCRIPT / "run_trade_market_sweep_v20.py")
    trade_gov = text(SCRIPT / "trade_option_governance.py")
    roster = text(SCRIPT / "run_roster_decision_lab.py")
    oe = text(SCRIPT / "opportunity_engine" / "application_v21.py")
    architecture = json.loads(text(DATA / "model_governance" / "application_architecture.json"))

    disabled = utility.score(synthetic_sim(authorized=False))
    active = utility.score(synthetic_sim(authorized=True))
    no_ext = utility.primitive_blocks(synthetic_sim(externality=False, authorized=False))
    with_ext = utility.primitive_blocks(synthetic_sim(externality=True, authorized=False))
    attr = attribution.reconcile(synthetic_sim(externality=True, authorized=False))
    batch_good = attribution.audit_batch([
        attribution.reconcile(synthetic_sim(externality=False, authorized=False)),
        attribution.reconcile(synthetic_sim(externality=True, authorized=False)),
    ])

    expected_current = 0.40 / 0.70
    expected_future = 0.30 / 0.70

    findings = {
        "shared_utility_is_single_numeric_composer": (
            'MODEL_VERSION = "FSFFL-Shared-Decision-Utility-2.0"' in du
            and "components = {k: w[k] * sf(blocks[k]) for k in required}" in du
        ),
        "unauthorized_channels_cannot_consume_weight": (
            disabled["incremental_channel_authorization"]["liquidity"] is False
            and disabled["incremental_channel_authorization"]["resilience"] is False
            and abs(disabled["objective_weights"]["liquidity"]) < 1e-12
            and abs(disabled["objective_weights"]["resilience"]) < 1e-12
            and abs(disabled["objective_weights"]["current"] - expected_current) < 1e-5
            and abs(disabled["objective_weights"]["future"] - expected_future) < 1e-5
            and active["objective_weights"]["liquidity"] > 0
        ),
        "state_aware_runtime_propagates_channel_authorization": (
            '"incremental_channel_authorization": incremental_channel_authorization' in dl
            and "unauthorized channels are diagnostic-only" in dl
        ),
        "opponent_title_externality_changes_current_primitive": (
            abs(float(with_ext["current"]) - float(no_ext["current"])) > 1e-9
            and "net_title_equity_swing_against_focus" in ti
            and "competitive_externality" in ti
        ),
        "team_improvement_exposes_league_reference": (
            "'league_reference':league_reference" in ti
            or "'league_reference': league_reference" in ti
        ),
        "focal_and_counterparty_use_same_shared_authority": (
            "counterparty_shared_decision_utility_source" in ti
            and "same_simulation_same_shared_utility_as_focal" in ti
            and "counterparty_decision_attribution" in ti
        ),
        "portfolio_uses_shared_team_improvement_authority": (
            "self.base.unified_score" in facade
            and "'decision_attribution':attribution" in facade
        ),
        "attribution_reconciles_exact_authoritative_math": (
            attr["reconciles"] is True
            and attr["creates_independent_score"] is False
            and abs(float(attr["component_sum"]) - float(attr["final_shared_decision_utility"])) <= 0.03
        ),
        "always_zero_guard_respects_authorization": batch_good["passed"] is True,
        "trade_post_sim_score_explicitly_shared_utility_alias": (
            'row["shared_decision_utility_score"] = resolved["score"]' in trade_v20
            and "post_sim_score_is_shared_decision_utility_compatibility_alias" in trade_v20
        ),
        "trade_final_authority_does_not_use_legacy_strategic_composite": (
            '"shared_decision_utility_score",' in trade_gov
            and '"strategic_value_delta",' not in trade_gov.split("DECISION_OUTPUTS =", 1)[1].split(")", 1)[0]
            and 'metric(current, "shared_decision_utility_score")' in trade_gov
        ),
        "roster_direct_path_uses_state_aware_shared_utility": (
            "decision_lab_state_aware.py" in roster
            and "decision_attribution_by_user" in roster
            and '"authority": "Shared Decision Utility / GM3 Team Improvement"' in roster
            and '"pareto_diagnostic": classify_decision' in roster
        ),
        "roster_direct_path_uses_canonical_roster_resolution": (
            "roster_aware_trade.py" in roster
            and "legalize_trade_rosters" in roster
            and '"roster_resolution": roster_resolution' in roster
            and '"automatic_roster_cut_actions": cut_actions' in roster
        ),
        "opportunity_engine_does_not_create_valuation_authority": (
            "team_improvement" in oe
            and "team_improvement_score" in oe
            and "shared_decision_utility" not in oe.lower().replace("shared_decision_utility_score", "")
        ),
        "architecture_team_improvement_version_current": (
            (((architecture.get("applications") or {}).get("gm3") or {}).get("application_areas") or {})
            .get("team_improvement", {}).get("current_implementation")
            == "FSFFL-GM-Team-Improvement-Lab-1.6"
        ),
    }

    failures = [k for k, v in findings.items() if not v]
    report = {
        "model_version": MODEL_VERSION,
        "scope": "production decision-path integration/composition; projection-model quality excluded",
        "findings": findings,
        "summary": {
            "passed": not failures,
            "finding_count": len(findings),
            "failure_count": len(failures),
            "failures": failures,
        },
        "synthetic_reconciliation": {
            "disabled_channel_score": disabled,
            "authorized_channel_score": active,
            "attribution": attr,
            "current_primitive_without_opponent_externality": round(float(no_ext["current"]), 6),
            "current_primitive_with_opponent_externality": round(float(with_ext["current"]), 6),
        },
        "ci_guards": {
            "major_authorized_utility_block_always_zero": True,
            "focal_counterparty_authority_mismatch": True,
            "report_decision_value_reconciliation": True,
            "unauthorized_duplicate_channel_weight": True,
            "superseded_trade_composite_final_authority": True,
            "standalone_roster_shared_authority_bypass": True,
            "standalone_roster_legality_bypass": True,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    if failures:
        raise SystemExit("Decision-path integration audit failed: " + ", ".join(failures))
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
