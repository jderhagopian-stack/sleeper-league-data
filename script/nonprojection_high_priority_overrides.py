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
        "owner_specific_valuation_multipliers_diagnostic_only": False,
        "football_market_repricing_overlays_diagnostic_only": False,
        "simulator_continuous_pick_quality_anchor": False,
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

    # Future-pick point estimates: replace the legacy hard early/mid/late
    # contender-score cliff with a continuous interpolation across observed
    # external early/mid/late pick-market cells. The interpolation coordinate
    # is the original team's canonical Simulator competitive percentile. This
    # keeps useful pick-quality differentiation without hand-set team-strength
    # blend coefficients or categorical valuation jumps.
    original_build_future_pick_assets = getattr(engine, "build_future_pick_assets", None)
    if original_build_future_pick_assets is not None:
        def simulator_continuous_pick_assets(
            rosters, traded_picks, team_profiles, profile_by_uid, detected_pick_values
        ):
            assets = original_build_future_pick_assets(
                rosters, traded_picks, team_profiles, profile_by_uid, detected_pick_values
            )
            try:
                season = int(engine.LEAGUE_RULES["season"])
                sim = engine.load_json(
                    engine.DATA / "simulator" / str(season) / "outputs" / "standings_projection.json",
                    {},
                ) or {}
                teams = list(sim.get("teams") or [])
                ordered = sorted(
                    teams,
                    key=lambda x: (
                        _sf(x.get("championship_probability")),
                        _sf(x.get("bye_probability")),
                        _sf(x.get("playoff_probability")),
                        _sf(x.get("expected_wins")),
                    ),
                )
                # Weakest=0 -> early anchor; strongest=1 -> late anchor.
                n = len(ordered)
                pct = {
                    str(row.get("user_id")): (i / (n - 1) if n > 1 else 0.5)
                    for i, row in enumerate(ordered)
                    if row.get("user_id") is not None
                }
                for aid, row in assets.items():
                    uid = str(row.get("original_owner_user_id") or "")
                    if uid not in pct:
                        row["pick_quality_point_estimate_source"] = "legacy_fallback_simulator_team_missing"
                        continue
                    year = int(row.get("season"))
                    rnd = int(row.get("round"))
                    early = engine.fallback_pick_value(year, "early", rnd, detected_pick_values)
                    mid = engine.fallback_pick_value(year, "mid", rnd, detected_pick_values)
                    late = engine.fallback_pick_value(year, "late", rnd, detected_pick_values)
                    p = pct[uid]
                    if p <= 0.5:
                        value = early + (mid - early) * (p / 0.5)
                    else:
                        value = mid + (late - mid) * ((p - 0.5) / 0.5)
                    display_tier = "early" if p < (1.0 / 3.0) else "late" if p > (2.0 / 3.0) else "mid"
                    row["legacy_discrete_tier_value_diagnostic"] = row.get("market_dynasty")
                    row["market_dynasty"] = round(value, 1)
                    row["projected_pick_tier"] = display_tier
                    row["simulator_competitive_percentile"] = round(p, 4)
                    row["pick_quality_point_estimate_source"] = "canonical_simulator_percentile_continuous_external_market_interpolation"
                    row["pick_quality_categorical_cliff_authoritative"] = False
                    row["pick_quality_scenario_values"] = {
                        "early": round(early, 1),
                        "mid": round(mid, 1),
                        "late": round(late, 1),
                    }
                    row["pick_quality_scenario_values_are_uncertainty_bounds_not_probabilities"] = True
                applied["simulator_continuous_pick_quality_anchor"] = True
            except Exception as exc:
                for row in assets.values():
                    row["pick_quality_point_estimate_source"] = "legacy_fallback_simulator_unavailable"
                    row["pick_quality_point_estimate_error"] = repr(exc)
            return assets
        engine.build_future_pick_assets = simulator_continuous_pick_assets

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


    # Recent performance, usage, snap, injury, and manual-news signals can be
    # highly useful football context, but multiplying the current dynasty market
    # anchor by hand-set percentages double counts information already reflected
    # in the market and/or the canonical Simulator projections. Preserve the
    # proposed adjustments as diagnostics, but authorize zero incremental
    # dynasty-market repricing until residual held-out evidence supports it.
    original_performance_adjustment = getattr(engine, "performance_adjustment", None)
    if original_performance_adjustment is not None:
        def diagnostic_only_performance_adjustment(asset, performance, baselines):
            proposed, meta = original_performance_adjustment(asset, performance, baselines)
            meta = dict(meta or {})
            meta["proposed_incremental_adjustment_diagnostic"] = round(_sf(proposed), 4)
            meta["incremental_market_repricing_authorized"] = False
            meta["adjustment"] = 0.0
            return 0.0, meta
        engine.performance_adjustment = diagnostic_only_performance_adjustment

    original_football_adjustment = getattr(engine, "football_intelligence_adjustment", None)
    if original_football_adjustment is not None:
        def diagnostic_only_football_adjustment(asset, usage, snaps, manual):
            proposed, meta = original_football_adjustment(asset, usage, snaps, manual)
            meta = dict(meta or {})
            meta["proposed_incremental_adjustment_diagnostic"] = round(_sf(proposed), 4)
            meta["incremental_market_repricing_authorized"] = False
            meta["total_adjustment"] = 0.0
            return 0.0, meta
        engine.football_intelligence_adjustment = diagnostic_only_football_adjustment

    if original_performance_adjustment is not None or original_football_adjustment is not None:
        applied["football_market_repricing_overlays_diagnostic_only"] = True

    # Owner-specific buy/hold/pick multipliers (need, historical preference,
    # competitive-window, endowment, starter and thin-depth premiums) overlap
    # concepts now modeled explicitly by continuous GM3 objective utility,
    # lineup reoptimization and Behavioral Intelligence. Preserve their proposed
    # values as diagnostics, but do not let the legacy hand-set coefficients
    # alter the market-anchored franchise value a second time.
    original_owner_buy = getattr(engine, "owner_player_buy_value", None)
    original_owner_hold = getattr(engine, "owner_player_hold_value", None)
    original_owner_pick = getattr(engine, "owner_pick_value", None)

    if original_owner_buy is not None:
        def governed_owner_player_buy_value(
            uid, asset, team_profiles, prefs, performance=None, baselines=None,
            usage=None, snaps=None, manual=None,
        ):
            proposed, factors = original_owner_buy(
                uid, asset, team_profiles, prefs, performance, baselines,
                usage, snaps, manual,
            )
            factors = dict(factors or {})
            base = _sf(factors.get("fsffl_base"), proposed)
            factors["legacy_proposed_owner_buy_value_diagnostic"] = round(_sf(proposed), 1)
            factors["owner_specific_multiplier_incremental_value_authorized"] = False
            factors["governed_value_basis"] = "market_anchored_fsffl_base_plus_downstream_continuous_utility"
            factors["multiplier"] = 1.0
            return base, factors
        engine.owner_player_buy_value = governed_owner_player_buy_value

    if original_owner_hold is not None and original_owner_buy is not None:
        def governed_owner_player_hold_value(
            uid, asset, team_profiles, prefs, starters, performance=None,
            baselines=None, usage=None, snaps=None, manual=None,
        ):
            proposed_buy, buy_factors = original_owner_buy(
                uid, asset, team_profiles, prefs, performance, baselines,
                usage, snaps, manual,
            )
            governed_buy, factors = engine.owner_player_buy_value(
                uid, asset, team_profiles, prefs, performance, baselines,
                usage, snaps, manual,
            )
            factors = dict(factors or {})
            # Record the former owner-specific buy proposal plus the legacy
            # hold-overlay proposal without granting either incremental value.
            try:
                proposed_hold, _ = original_owner_hold(
                    uid, asset, team_profiles, prefs, starters, performance,
                    baselines, usage, snaps, manual,
                )
            except Exception:
                proposed_hold = proposed_buy
            factors["legacy_proposed_owner_buy_value_diagnostic"] = round(_sf(proposed_buy), 1)
            factors["legacy_proposed_owner_hold_value_diagnostic"] = round(_sf(proposed_hold), 1)
            factors["current_owner_endowment_premium"] = 0.0
            factors["starter_dependency_premium"] = 0.0
            factors["thin_depth_hold_premium"] = 0.0
            factors["hold_multiplier_over_buy_value"] = 1.0
            factors["legacy_owner_hold_overlay_incremental_value_authorized"] = False
            return governed_buy, factors
        engine.owner_player_hold_value = governed_owner_player_hold_value

    if original_owner_pick is not None:
        def governed_owner_pick_value(uid, pick, team_profiles, prefs, hold):
            proposed, factors = original_owner_pick(uid, pick, team_profiles, prefs, hold)
            factors = dict(factors or {})
            base = _sf(factors.get("market_base"), _sf(pick.get("market_dynasty"), proposed))
            factors["legacy_proposed_owner_pick_value_diagnostic"] = round(_sf(proposed), 1)
            factors["pick_preference_adjustment"] = 0.0
            factors["competitive_window_adjustment"] = 0.0
            factors["hold_endowment_premium"] = 0.0
            factors["owner_specific_pick_multiplier_incremental_value_authorized"] = False
            factors["multiplier"] = 1.0
            return base, factors
        engine.owner_pick_value = governed_owner_pick_value

    if any(x is not None for x in (original_owner_buy, original_owner_hold, original_owner_pick)):
        applied["owner_specific_valuation_multipliers_diagnostic_only"] = True

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
            "simulator_continuous_pick_quality_anchor": applied["simulator_continuous_pick_quality_anchor"],
            "owner_specific_valuation_multipliers_incremental_value_authorized": False,
            "owner_specific_valuation_multipliers_diagnostic_only": applied["owner_specific_valuation_multipliers_diagnostic_only"],
            "performance_usage_injury_news_market_repricing_authorized": False,
            "football_market_repricing_overlays_diagnostic_only": applied["football_market_repricing_overlays_diagnostic_only"],
            "new_hand_set_coefficients_introduced": False,
        }
        return payload

    engine.build_strategic_asset_profiles_for_team = deduplicated_profiles
    engine.build_dynamic_core_values_for_team = deduplicated_profiles
    applied["gm22_hold_premium_dedup"] = True
    engine.NONPROJECTION_HIGH_PRIORITY_OVERRIDES = applied
    return engine
