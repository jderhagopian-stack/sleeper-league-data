#!/usr/bin/env python3
"""High-priority non-projection structural de-duplication and pick anchoring.

This module intentionally changes only relationships that are structurally
unsupported or duplicated. It does not calibrate new hand-set coefficients.
"""
from __future__ import annotations

import json
import statistics
import urllib.request

STATSGUY_API = "https://api.statsguyfantasy.com/api/v1"
_STATS_GUY_CACHE = None


def _sf(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _statsguy_json(path):
    req = urllib.request.Request(
        STATSGUY_API + path,
        headers={"User-Agent": "FSFFL-GM3-pick-anchor/1.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response)


def _statsguy_anchor(market, fallback):
    """Return Stats Guy pick cells normalized onto the current player-value scale."""
    global _STATS_GUY_CACHE
    if _STATS_GUY_CACHE is not None:
        return _STATS_GUY_CACHE
    try:
        players_payload = _statsguy_json("/players")
        picks_payload = _statsguy_json("/picks")
        sg_players = {}
        for row in players_payload.get("players") or []:
            sid = str(row.get("id") or "")
            value = _sf((row.get("value") or {}).get("sf_dynasty"))
            if sid and value > 0:
                sg_players[sid] = value
        ratios = []
        for row in market.get("dynasty") or []:
            sid = str(row.get("sleeper_id") or "")
            fc_value = _sf(row.get("value"))
            sg_value = _sf(sg_players.get(sid))
            if sid and fc_value > 0 and sg_value > 0:
                ratios.append(fc_value / sg_value)
        if not ratios:
            raise RuntimeError("no overlapping player values for scale normalization")
        scale = statistics.median(ratios)
        detected = {}
        for row in picks_payload.get("picks") or []:
            try:
                year = int(row.get("year")); rnd = int(row.get("round"))
            except (TypeError, ValueError):
                continue
            tier = str(row.get("variant") or "").lower()
            if tier not in {"early", "mid", "late"}:
                continue
            value = _sf((row.get("value") or {}).get("sf_dynasty"))
            if value > 0:
                detected[(year, tier, rnd)] = value * scale
        if not detected:
            raise RuntimeError("Stats Guy returned no usable future-pick variants")
        _STATS_GUY_CACHE = detected
        return detected
    except Exception:
        _STATS_GUY_CACHE = fallback
        return fallback


def install(engine):
    """Install de-duplication overrides on a loaded GM engine module."""
    applied = {
        "gm22_hold_premium_dedup": False,
        "own_pick_control_bonus_removed": False,
        "statsguy_future_pick_anchor": False,
        "market_momentum_incremental_value_removed": False,
    }

    # The current market price already embeds the information that produced its
    # recent move. Preserve momentum as a diagnostic but do not reward it again
    # unless time-ordered incremental evidence supports doing so.
    original_market_momentum = getattr(engine, "market_momentum_adjustment", None)
    if original_market_momentum is not None:
        def diagnostic_only_market_momentum(asset):
            proposed_adj, meta = original_market_momentum(asset)
            meta = dict(meta or {})
            meta["proposed_incremental_adjustment_diagnostic"] = round(_sf(proposed_adj), 4)
            meta["incremental_adjustment_authorized"] = False
            meta["adjustment"] = 0.0
            return 0.0, meta
        engine.market_momentum_adjustment = diagnostic_only_market_momentum
        applied["market_momentum_incremental_value_removed"] = True

    # Stats Guy is the governed future-pick market challenger already promoted
    # on main. Normalize its pick cells to the active player-value scale without
    # inserting a manually chosen conversion coefficient.
    original_infer_pick_values = getattr(engine, "infer_fc_pick_values", None)
    if original_infer_pick_values is not None:
        def infer_statsguy_pick_values(market):
            fallback = original_infer_pick_values(market)
            out = _statsguy_anchor(market, fallback)
            engine.STATSGUY_PICK_ANCHOR_DIAGNOSTICS = {
                "active": out is not fallback,
                "source": "Stats Guy Fantasy /api/v1/picks",
                "scale_normalization": "median overlapping player-value ratio",
                "fallback_source": "existing FantasyCalc-derived pick map",
                "manual_scale_coefficient": None,
            }
            return out
        engine.infer_fc_pick_values = infer_statsguy_pick_values
        applied["statsguy_future_pick_anchor"] = True

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
                "strategic_core": round(premium_pct, 4), "tier_scarcity": 0.0,
                "optionality": 0.0, "liquidity": 0.0,
                "expected_hold_appreciation": 0.0, "resilience": 0.0,
            }
            row["hold_premium_pct"] = round(premium_pct, 4)
            row["hold_premium_value"] = round(base * premium_pct, 1)
            row["break_glass_value"] = round(base * (1.0 + premium_pct), 1)
            row["hold_premium_policy"] = "single_strategic_core_path_no_duplicate_component_adders"

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
                "round": 0.0, "specific_pick_quality": 0.0, "optionality": 0.0,
                "liquidity": 0.0, "own_pick_control": 0.0,
            }
            base = _sf(row.get("base_franchise_value"))
            row["hold_premium_pct"] = 0.0
            row["hold_premium_value"] = 0.0
            row["break_glass_value"] = round(base, 1)
            row["pick_incremental_premium_policy"] = "market_anchor_only_until_residual_incremental_validation"

        payload["high_priority_nonprojection_policy"] = {
            "player_hold_premium_single_incremental_path": True,
            "duplicate_optionality_liquidity_resilience_appreciation_adders_removed": True,
            "own_pick_control_bonus_incremental_value_authorized": False,
            "pick_round_quality_optionality_liquidity_premiums_incremental_value_authorized": False,
            "market_momentum_incremental_value_authorized": False,
            "statsguy_future_pick_market_anchor": applied["statsguy_future_pick_anchor"],
            "new_hand_set_coefficients_introduced": False,
        }
        return payload

    engine.build_strategic_asset_profiles_for_team = deduplicated_profiles
    engine.build_dynamic_core_values_for_team = deduplicated_profiles
    applied["gm22_hold_premium_dedup"] = True
    engine.NONPROJECTION_HIGH_PRIORITY_OVERRIDES = applied
    return engine
