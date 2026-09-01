#!/usr/bin/env python3
"""FSFFL Counter & Market Sweep 1.14 — continuous state-aware GM profiles and ranking.

Upgrade over 1.13:
- incoming assets are re-profiled for the focal franchise on the hypothetical
  post-trade roster using continuous GM objective weights;
- runtime weights come from a tiny precomputed calibration artifact plus
  already-produced Simulator 1.0 context, never from historical calibration;
- descriptive competition classifications remain, but hard weight cliffs do not;
- the final negotiation ranking consumes the state-aware post-simulation score
  instead of rebuilding focal strategic gain from generic wins/dynasty deltas;
- categorical contender title-equity caps are disabled; championship impact remains a continuous simulation input;
- canonical roster and GM state remain read-only.
"""
from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent
V19_PATH = SCRIPT / "run_trade_market_sweep_v19.py"
V13_PATH = Path("script/run_trade_market_sweep_v13.py")
V18_PATH = Path("script/run_trade_market_sweep_v18.py")
MODEL_VERSION = "FSFFL-Counter-Market-Sweep-1.14"
NEGOTIATION_RANKING = SCRIPT / "negotiation_ranking.py"
HIGH_PRIORITY_OVERRIDES = SCRIPT / "nonprojection_high_priority_overrides.py"
DECISION_UTILITY = SCRIPT / "decision_utility.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to import {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sf(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def state_aware_post_sim_score(engine, row, state: str):
    sim = row.get("simulation") or {}
    utility = load_module(DECISION_UTILITY, "shared_decision_utility_for_trade")
    attribution = load_module(SCRIPT / "decision_attribution.py", "decision_attribution_for_trade")
    resolved = utility.score(sim)
    row["championship_equity_constraint"] = "CONTINUOUS_NO_CATEGORICAL_CAP"
    row["state_aware_objective_weights"] = resolved["objective_weights"]
    row["state_aware_score_components"] = {
        **resolved["components"],
        "composite_strategic_and_break_glass_incremental_weight": 0.0,
        "negotiation_plausibility_incremental_weight": 0.0,
    }
    row["decision_utility_model_version"] = resolved["model_version"]
    row["decision_utility_scale_status"] = resolved["scale_status"]
    row["shared_decision_utility_score"] = resolved["score"]
    row["decision_attribution"] = attribution.reconcile(sim)
    row["post_sim_score_is_shared_decision_utility_compatibility_alias"] = True
    return resolved["score"]

def state_aware_blended_negotiation_score(row):
    nr = load_module(NEGOTIATION_RANKING, "negotiation_ranking_for_v114")
    return nr.recompute_from_row(row)


def install_engine_upgrade(engine, overlay, high_priority=None):
    # Keep the installer callable by older audit/runtime helpers that predate
    # the explicit high-priority override argument. Production callers may pass
    # the already-loaded module; compatibility callers load the same canonical
    # override here rather than silently skipping it.
    if high_priority is None:
        high_priority = load_module(
            HIGH_PRIORITY_OVERRIDES,
            "nonprojection_high_priority_overrides_for_v114_compat",
        )
    high_priority.install(engine)
    original_import = engine.import_decision_lab
    def upgraded_import_decision_lab(): return overlay.install(original_import())
    engine.import_decision_lab = upgraded_import_decision_lab
    engine.contender_title_cap = lambda state: None
    engine.post_sim_score = lambda row, state: state_aware_post_sim_score(engine, row, state)
    return engine


def output_path_from_argv():
    if "--output" not in sys.argv: return None
    i = sys.argv.index("--output")
    return Path(sys.argv[i + 1]) if i + 1 < len(sys.argv) else None


def main():
    v19 = load_module(V19_PATH, "market_sweep_v19_for_v114")
    overlay = load_module(SCRIPT / "decision_lab_state_aware.py", "decision_lab_state_aware_for_v114")
    high_priority = load_module(HIGH_PRIORITY_OVERRIDES, "nonprojection_high_priority_overrides_for_v114")
    original_v19_loader = v19.load_module
    def patched_v19_loader(path: Path, name: str):
        mod = original_v19_loader(path, name)
        if Path(path) == V13_PATH:
            original_v13_loader = mod.load_module
            def patched_v13_loader(base_path: Path, base_name: str):
                engine = original_v13_loader(base_path, base_name)
                if Path(base_path) == mod.BASE_ENGINE: install_engine_upgrade(engine, overlay, high_priority)
                return engine
            mod.load_module = patched_v13_loader
        elif Path(path) == V18_PATH:
            mod.blended_negotiation_score = state_aware_blended_negotiation_score
        return mod
    v19.load_module = patched_v19_loader
    v19.MODEL_VERSION = MODEL_VERSION
    v19.main()
    output = output_path_from_argv()
    if output and output.exists():
        report = json.loads(output.read_text(encoding="utf-8")); report["model_version"] = MODEL_VERSION
        policy = report.setdefault("policy", {})
        policy.update({"focal_post_trade_gm_profiles_recomputed": True, "all_competition_classifications_state_aware": True, "continuous_state_weighting": True, "state_aware_objective_weighted_ranking": True, "final_negotiation_ranking_uses_state_aware_post_sim_score": True, "incoming_assets_use_focal_post_trade_profile": True, "seller_profile_not_used_as_focal_strategic_value": True, "historical_calibration_runs_during_interactive_query": False, "runtime_weight_resolution_reads_precomputed_artifacts_only": True, "calibration_fallback_enabled": True, "negotiation_plausibility_separate_from_focal_strategic_value": True, "player_hold_premium_single_incremental_path": True, "own_pick_control_bonus_incremental_value_authorized": False})
        current_strategic = (((report.get("current_offer_evaluation") or {}).get("simulation") or {}).get("strategic") or {}); wr = current_strategic.get("weight_resolution") or {}
        report["state_weighting"] = {"model_version": wr.get("calibration_model_version"), "calibration_status": wr.get("calibration_status"), "runtime_source": wr.get("runtime_source"), "objective_state": current_strategic.get("objective_state"), "objective_weights": current_strategic.get("objective_weights"), "inputs": wr.get("inputs"), "adjustments": wr.get("adjustments")}
        report.setdefault("simulation", {})["execution_path"] = "GM3_continuous_state_weights_plus_post_trade_profile_recompute_plus_state_aware_negotiation_ranking_plus_fast_decision_lab"
        output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


if __name__ == "__main__": main()