#!/usr/bin/env python3
"""FSFFL Counter & Market Sweep 1.12 — state-aware hypothetical GM profiles.

Upgrade over 1.11:
- every candidate's incoming assets are re-profiled for the focal franchise on
  the hypothetical post-trade roster using the GM 3.0 inherited strategic core;
- the post-simulation ranking uses the focal franchise's actual objective
  weights for elite_contender, contender, retool and rebuild states;
- contender title-equity caps remain hard guardrails;
- no canonical roster or GM state is mutated.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent
V19_PATH = SCRIPT / "run_trade_market_sweep_v19.py"
V13_PATH = Path("script/run_trade_market_sweep_v13.py")
MODEL_VERSION = "FSFFL-Counter-Market-Sweep-1.12"


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


def fallback_weights(state: str):
    return {
        "elite_contender": {"current": 0.50, "future": 0.25, "liquidity": 0.10, "resilience": 0.15},
        "contender": {"current": 0.40, "future": 0.35, "liquidity": 0.10, "resilience": 0.15},
        "retool": {"current": 0.23, "future": 0.47, "liquidity": 0.15, "resilience": 0.15},
        "rebuild": {"current": 0.10, "future": 0.60, "liquidity": 0.20, "resilience": 0.10},
    }.get(str(state), {"current": 0.25, "future": 0.45, "liquidity": 0.15, "resilience": 0.15})


def state_aware_post_sim_score(engine, row, state: str):
    sim = row.get("simulation") or {}
    d = sim.get("focus_delta") or {}
    s = sim.get("strategic") or {}
    weights = s.get("objective_weights") or fallback_weights(state)

    current_mult = sf(weights.get("current"), .25) / .40
    future_mult = sf(weights.get("future"), .45) / .35
    liquidity_mult = sf(weights.get("liquidity"), .15) / .10
    resilience_mult = sf(weights.get("resilience"), .15) / .15

    title = sf(d.get("championship_probability"))
    playoff = sf(d.get("playoff_probability"))
    wins = sf(d.get("expected_wins"))
    points = sf(d.get("expected_points_for"))
    dynasty = sf(s.get("market_dynasty_delta"))
    break_glass = sf(s.get("break_glass_delta"))
    liquidity = sf(s.get("liquidity_value_delta"))
    strategic = sf(s.get("strategic_value_delta"))
    optionality = sf(s.get("optionality_value_delta"))
    externality = sf(sim.get("net_title_equity_swing_against_focus"))
    plausibility = sf(row.get("plausibility_score"))

    # Normalize each franchise against the historical contender-weight baseline.
    # This preserves score scale while making the objective genuinely state-aware.
    current_block = 25000.0 * title + 5000.0 * playoff + 400.0 * wins + 1.25 * points
    future_block = dynasty + 0.30 * break_glass + 0.18 * optionality
    liquidity_block = 0.25 * liquidity
    resilience_block = 0.15 * strategic + 0.08 * break_glass

    score = (
        current_mult * current_block
        + future_mult * future_block
        + liquidity_mult * liquidity_block
        + resilience_mult * resilience_block
        - current_mult * 12000.0 * externality
        + 1200.0 * plausibility
    )

    if row.get("plausibility") == "LOW":
        score -= 3000.0
    elif row.get("plausibility") == "THEORETICAL_ONLY":
        score -= 6000.0

    cap = engine.contender_title_cap(state)
    if cap is not None and title < -cap:
        score -= 12000.0 + 50000.0 * abs(title + cap)
        row["championship_equity_constraint"] = "FAIL"
    else:
        row["championship_equity_constraint"] = "PASS"

    row["state_aware_objective_weights"] = weights
    row["state_aware_score_components"] = {
        "current": round(current_mult * current_block, 2),
        "future": round(future_mult * future_block, 2),
        "liquidity": round(liquidity_mult * liquidity_block, 2),
        "resilience": round(resilience_mult * resilience_block, 2),
        "opponent_externality": round(-current_mult * 12000.0 * externality, 2),
    }
    return round(score, 2)


def install_engine_upgrade(engine, overlay):
    original_import = engine.import_decision_lab

    def upgraded_import_decision_lab():
        return overlay.install(original_import())

    engine.import_decision_lab = upgraded_import_decision_lab
    engine.post_sim_score = lambda row, state: state_aware_post_sim_score(engine, row, state)
    return engine


def output_path_from_argv():
    if "--output" not in sys.argv:
        return None
    i = sys.argv.index("--output")
    return Path(sys.argv[i + 1]) if i + 1 < len(sys.argv) else None


def main():
    v19 = load_module(V19_PATH, "market_sweep_v19_for_v112")
    overlay = load_module(SCRIPT / "decision_lab_state_aware.py", "decision_lab_state_aware_for_v112")
    original_v19_loader = v19.load_module

    def patched_v19_loader(path: Path, name: str):
        mod = original_v19_loader(path, name)
        if Path(path) == V13_PATH:
            original_v13_loader = mod.load_module

            def patched_v13_loader(base_path: Path, base_name: str):
                engine = original_v13_loader(base_path, base_name)
                if Path(base_path) == mod.BASE_ENGINE:
                    install_engine_upgrade(engine, overlay)
                return engine

            mod.load_module = patched_v13_loader
        return mod

    v19.load_module = patched_v19_loader
    v19.MODEL_VERSION = MODEL_VERSION
    v19.main()

    output = output_path_from_argv()
    if output and output.exists():
        report = json.loads(output.read_text(encoding="utf-8"))
        report["model_version"] = MODEL_VERSION
        policy = report.setdefault("policy", {})
        policy.update({
            "focal_post_trade_gm_profiles_recomputed": True,
            "all_competition_classifications_state_aware": True,
            "state_aware_objective_weighted_ranking": True,
            "incoming_assets_use_focal_post_trade_profile": True,
            "seller_profile_not_used_as_focal_strategic_value": True,
        })
        sim = report.setdefault("simulation", {})
        sim["execution_path"] = "GM3_post_trade_profile_recompute_plus_state_weighted_market_sweep_plus_fast_decision_lab"
        output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    main()
