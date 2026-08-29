#!/usr/bin/env python3
"""High-priority non-projection structural de-duplication.

This module intentionally changes only relationships that are structurally
unsupported or duplicated. It does not calibrate new coefficients.
"""
from __future__ import annotations


def _sf(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def install(engine):
    """Install de-duplication overrides on a loaded GM engine module.

    Legacy market-sweep layers may load an older engine that does not expose
    the GM-2.2 strategic-profile helpers. In that case there is nothing to
    patch here, so return cleanly rather than breaking the sweep runtime.
    """

    applied = {
        "gm22_hold_premium_dedup": False,
        "own_pick_control_bonus_removed": False,
    }

    # A pick's quality model already keys off the original franchise and its
    # projected finish. Merely being the original owner is not independent
    # evidence that the same pick is intrinsically more valuable.
    original_pick_profile = getattr(engine, "_u_pick_profile", None)
    if original_pick_profile is not None:
        def pick_profile_without_control_bonus(aid, uid, ctx):
            out = dict(original_pick_profile(aid, uid, ctx))
            original_bonus = _sf(out.get("own_pick_control_bonus"))
            out["own_pick_control_bonus_diagnostic"] = original_bonus
            out["own_pick_control_bonus"] = 0.0
            out["own_pick_control_incremental_value_authorized"] = False
            return out

        engine._u_pick_profile = pick_profile_without_control_bonus
        applied["own_pick_control_bonus_removed"] = True

    # The native GM-2.2 player hold premium first builds a strategic score from
    # current value, future optionality, liquidity and replacement resilience.
    # It then adds optionality, liquidity, appreciation and resilience premiums
    # again. Collapse this to the existing strategic-score premium transform so
    # each family has one incremental value path. No replacement coefficient is
    # invented; the pre-existing core transform is retained provisionally.
    original_profiles = getattr(engine, "build_strategic_asset_profiles_for_team", None)
    if original_profiles is None or not hasattr(engine, "GM22"):
        engine.NONPROJECTION_HIGH_PRIORITY_OVERRIDES = applied
        return engine

    def deduplicated_profiles(uid, ctx=None):
        payload = original_profiles(uid, ctx)
        max_premium = _sf(engine.GM22.get("max_static_exit_premium_pct"), 0.85)

        for row in payload.get("players") or []:
            strategic = _clamp(_sf(row.get("strategic_score")), 0.0, 1.0)
            base = _sf(row.get("base_franchise_value"))
            core = 0.04 + 0.30 * (strategic ** 1.65)
            premium_pct = _clamp(core, 0.03, max_premium)

            prior = dict(row.get("premium_components") or {})
            row["premium_component_diagnostics"] = prior
            row["premium_components"] = {
                "strategic_core": round(premium_pct, 4),
                "tier_scarcity": 0.0,
                "optionality": 0.0,
                "liquidity": 0.0,
                "expected_hold_appreciation": 0.0,
                "resilience": 0.0,
            }
            row["hold_premium_pct"] = round(premium_pct, 4)
            row["hold_premium_value"] = round(base * premium_pct, 1)
            row["break_glass_value"] = round(base * (1.0 + premium_pct), 1)
            row["hold_premium_policy"] = "single_strategic_core_path_no_duplicate_component_adders"

        # Pick market value already varies by round/year and, where available,
        # early/mid/late quality. The native strategic layer then re-added round,
        # quality, optionality (itself derived from quality), liquidity and own-
        # pick control premiums. Without residual transaction evidence showing
        # incremental value beyond the market anchor, those are duplicate or
        # unvalidated positive adders. Preserve them as diagnostics only.
        for row in payload.get("picks") or []:
            pp = row.get("pick_profile") or {}
            pp["own_pick_control_incremental_value_authorized"] = False
            pp["liquidity_incremental_value_authorized"] = False
            pp["quality_optionality_incremental_value_authorized"] = False

            row["liquidity_score_diagnostic"] = _sf(row.get("liquidity_score"), 0.5)
            row["liquidity_score"] = 0.5

            prior = dict(row.get("premium_components") or {})
            row["premium_component_diagnostics"] = prior
            row["premium_components"] = {
                "round": 0.0,
                "specific_pick_quality": 0.0,
                "optionality": 0.0,
                "liquidity": 0.0,
                "own_pick_control": 0.0,
            }
            base = _sf(row.get("base_franchise_value"))
            row["hold_premium_pct"] = 0.0
            row["hold_premium_value"] = 0.0
            row["break_glass_value"] = round(base, 1)
            row["pick_incremental_premium_policy"] = (
                "market_anchor_only_until_residual_incremental_validation"
            )

        payload["high_priority_nonprojection_policy"] = {
            "player_hold_premium_single_incremental_path": True,
            "duplicate_optionality_liquidity_resilience_appreciation_adders_removed": True,
            "own_pick_control_bonus_incremental_value_authorized": False,
            "pick_round_quality_optionality_liquidity_premiums_incremental_value_authorized": False,
            "new_coefficients_introduced": False,
        }
        return payload

    engine.build_strategic_asset_profiles_for_team = deduplicated_profiles
    engine.build_dynamic_core_values_for_team = deduplicated_profiles
    applied["gm22_hold_premium_dedup"] = True
    engine.NONPROJECTION_HIGH_PRIORITY_OVERRIDES = applied
    return engine
