#!/usr/bin/env python3
"""Measure which objective channels can actually consume final utility weight.

This distinguishes nominal current/future/liquidity/resilience state weights
from effective Shared Decision Utility authority after asset-level channel
authorization and structural de-duplication.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "script"
OUT = ROOT / "data" / "audit" / "effective_objective_channel_authority.json"


def loadmod(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    engine = loadmod(SCRIPT / "build_fsffl_gm_engine.py", "channel_authority_engine")
    high = loadmod(
        SCRIPT / "nonprojection_high_priority_overrides.py",
        "channel_authority_high",
    )
    gov = loadmod(
        SCRIPT / "gm30_nonprojection_governance.py",
        "channel_authority_gov",
    )
    weighting = loadmod(
        SCRIPT / "gm_state_weighting.py",
        "channel_authority_weights",
    )
    utility = loadmod(
        SCRIPT / "decision_utility.py",
        "channel_authority_utility",
    )

    high.install(engine)
    gov.install(engine)
    ctx = engine._u_load_context()
    uids = sorted(map(str, ctx.get("owners") or {}))

    teams = []
    all_assets = []
    for uid in uids:
        payload = engine.build_strategic_asset_profiles_for_team(uid, ctx)
        rows = list(payload.get("assets") or [])
        counts = {
            "assets": len(rows),
            "liquidity_authorized": 0,
            "resilience_authorized": 0,
            "pick_liquidity_authorized": 0,
            "pick_quality_optionality_authorized": 0,
        }
        for row in rows:
            pp = row.get("pick_profile") or {}
            liq = row.get("liquidity_incremental_value_authorized")
            if liq is None and row.get("asset_type") == "pick":
                liq = pp.get("liquidity_incremental_value_authorized")
            res = row.get("resilience_incremental_value_authorized")
            if liq is True:
                counts["liquidity_authorized"] += 1
            if res is True:
                counts["resilience_authorized"] += 1
            if pp.get("liquidity_incremental_value_authorized") is True:
                counts["pick_liquidity_authorized"] += 1
            if pp.get("quality_optionality_incremental_value_authorized") is True:
                counts["pick_quality_optionality_authorized"] += 1
            all_assets.append(
                {
                    "user_id": uid,
                    "asset_id": row.get("asset_id"),
                    "asset_type": row.get("asset_type"),
                    "liquidity_authorized": liq is True,
                    "resilience_authorized": res is True,
                    "pick_liquidity_authorized": (
                        pp.get("liquidity_incremental_value_authorized") is True
                    ),
                    "pick_quality_optionality_authorized": (
                        pp.get("quality_optionality_incremental_value_authorized")
                        is True
                    ),
                }
            )
        teams.append(
            {
                "user_id": uid,
                "team_name": (ctx.get("owners", {}).get(uid) or {}).get("team_name"),
                **counts,
            }
        )

    totals = {
        "assets": len(all_assets),
        "liquidity_authorized": sum(x["liquidity_authorized"] for x in all_assets),
        "resilience_authorized": sum(x["resilience_authorized"] for x in all_assets),
        "pick_liquidity_authorized": sum(
            x["pick_liquidity_authorized"] for x in all_assets
        ),
        "pick_quality_optionality_authorized": sum(
            x["pick_quality_optionality_authorized"] for x in all_assets
        ),
    }

    # Show the effective current/future ratio after disabled channels are
    # removed and weights renormalized, without asserting that the prior is
    # empirically correct.
    cal = weighting.load_calibration()
    grid = []
    for strength in [0.0, 0.10, 0.20, 0.35, 0.50, 0.55, 0.70, 0.78, 0.90, 1.0]:
        nominal = weighting.interpolate(strength, cal.get("anchor_points") or [])
        active = {
            "current": True,
            "future": True,
            "liquidity": totals["liquidity_authorized"] > 0,
            "resilience": totals["resilience_authorized"] > 0,
        }
        raw = {k: max(0.0, float(nominal.get(k) or 0.0)) for k in nominal}
        z = sum(raw[k] for k in raw if active.get(k, True)) or 1.0
        effective = {
            k: (raw[k] / z if active.get(k, True) else 0.0)
            for k in ("current", "future", "liquidity", "resilience")
        }
        grid.append(
            {
                "competitive_strength": strength,
                "nominal_weights": {k: round(float(v), 6) for k, v in nominal.items()},
                "effective_weights_if_current_asset_authorization_is_representative": {
                    k: round(v, 6) for k, v in effective.items()
                },
            }
        )

    # Static final-utility contract check.
    util_src = (SCRIPT / "decision_utility.py").read_text(encoding="utf-8")
    contract = {
        "disabled_channels_receive_zero_weight": (
            'k: (raw_weights[k] if active[k] else 0.0)' in util_src
        ),
        "remaining_weights_are_renormalized": (
            "w = {k: v / total_weight for k, v in w.items()}" in util_src
        ),
        "liquidity_authorization_respected": (
            '"liquidity": bool(authorization.get("liquidity", True))' in util_src
        ),
        "resilience_authorization_respected": (
            '"resilience": bool(authorization.get("resilience", True))' in util_src
        ),
    }

    report = {
        "model_version": "FSFFL-Effective-Objective-Channel-Authority-1.0",
        "authority": "RESEARCH_AUDIT_NON_AUTHORITATIVE",
        "production_behavior_changed": False,
        "asset_authorization_totals": totals,
        "teams": teams,
        "effective_weight_grid": grid,
        "shared_decision_utility_contract": contract,
        "conclusions": {
            "all_final_utility_contract_checks_pass": all(contract.values()),
            "liquidity_has_current_asset_level_final_authority": (
                totals["liquidity_authorized"] > 0
            ),
            "resilience_has_current_asset_level_final_authority": (
                totals["resilience_authorized"] > 0
            ),
            "nominal_weight_does_not_imply_effective_weight": True,
            "disabled_channel_weight_mass_is_renormalized_to_authorized_channels": True,
            "upstream_search_or_diagnostic_use_is_separate_from_final_utility": True,
            "empirical_identifiability_claim_allowed": False,
        },
        "interpretation": (
            "If no current governed assets authorize liquidity/resilience as an "
            "incremental value channel, their nominal state-weight entries do not "
            "consume final Shared Decision Utility weight today. They may still "
            "appear in diagnostics/search context and must be audited separately."
        ),
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "asset_authorization_totals": totals,
        "conclusions": report["conclusions"],
    }, indent=2))


if __name__ == "__main__":
    main()
