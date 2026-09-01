#!/usr/bin/env python3
"""State-aware hypothetical GM overlay for FSFFL Decision Lab.

Every hypothetical transaction is evaluated using the focal franchise's own
continuous GM objective weights. Incoming assets are re-profiled on the
post-transaction roster rather than inheriting the seller's strategic profile.

Runtime weighting is intentionally cheap: it reads only a tiny precomputed
calibration artifact plus already-produced Simulator 1.0 context. Historical
calibration never runs in an interactive Decision Lab / Market Sweep request.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

SCRIPT = Path(__file__).resolve().parent


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to import {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _gm_core():
    gm30 = _load(SCRIPT / "build_fsffl_gm30.py", "gm30_state_aware_overlay")
    weighting = _load(SCRIPT / "gm_state_weighting.py", "gm_state_weighting_for_overlay")
    high_priority = _load(
        SCRIPT / "nonprojection_high_priority_overrides.py",
        "nonprojection_high_priority_overrides_for_overlay",
    )
    gm30_gov = _load(
        SCRIPT / "gm30_nonprojection_governance.py",
        "gm30_nonprojection_governance_for_overlay",
    )
    season = gm30.active_season()
    gm30.patch_gm22_runtime(season)

    # Hypothetical profiles must use the same governed non-projection economics
    # as production GM3. Dynamic season patching happens first, then structural
    # de-duplication / pick governance, exactly as in gm3/application.py.
    high_priority.install(gm30.core)
    gm30_gov.install(gm30.core)

    # Crucial consistency rule: the same continuous weights used to rank a
    # hypothetical also drive GM strategic-profile construction itself.
    def continuous_team_objective_weights(team):
        return weighting.weights_for_team(team)

    gm30.core._u_team_objective_weights = continuous_team_objective_weights
    gm30.core._state_weighting_runtime = weighting
    gm30.core.DECISION_LAB_GM_GOVERNANCE_RUNTIME = {
        "dynamic_patch_first": True,
        "high_priority_nonprojection_governance_installed": True,
        "gm30_pick_governance_installed": True,
        "continuous_state_weighting_installed": True,
    }
    return gm30.core


def _asset_ids_for_user(base_dl, actions: List[Dict[str, Any]], uid: str) -> Tuple[List[str], List[str]]:
    return base_dl.action_assets_for_user(actions, str(uid))


def _post_context(core, uid: str, actions: List[Dict[str, Any]], base_ctx: Dict[str, Any]):
    """Cheap copy-on-write GM context containing the focal post-move holdings."""
    uid = str(uid)
    sent, received = [], []
    for action in actions:
        typ = str(action.get("type") or "").lower()
        if typ == "trade":
            assets = [f"player:{x}" for x in (action.get("players") or [])] + [str(x) for x in (action.get("picks") or [])]
            if str(action.get("from_user_id")) == uid:
                sent.extend(assets)
            if str(action.get("to_user_id")) == uid:
                received.extend(assets)
        elif typ in {"drop", "cut"} and str(action.get("user_id")) == uid:
            sent.extend(f"player:{x}" for x in (action.get("players") or ([action.get("player_id")] if action.get("player_id") is not None else [])))
        elif typ == "add" and str(action.get("user_id")) == uid:
            received.extend(f"player:{x}" for x in (action.get("players") or ([action.get("player_id")] if action.get("player_id") is not None else [])))
        elif typ == "add_drop" and str(action.get("user_id")) == uid:
            sent.extend(f"player:{x}" for x in (action.get("drop_players") or []))
            received.extend(f"player:{x}" for x in (action.get("add_players") or []))

    ctx = dict(base_ctx)
    ctx["holdings"] = dict(base_ctx.get("holdings") or {})
    holdings = list((base_ctx.get("holdings") or {}).get(uid, []))
    for aid in sent:
        holdings = [x for x in holdings if str(x) != str(aid)]
    for aid in received:
        if aid not in holdings:
            holdings.append(aid)
    ctx["holdings"][uid] = holdings

    ctx["roster_by_uid"] = dict(base_ctx.get("roster_by_uid") or {})
    roster = [str(x) for x in (base_ctx.get("roster_by_uid") or {}).get(uid, [])]
    sent_players = {x.split(":", 1)[1] for x in sent if str(x).startswith("player:")}
    recv_players = [x.split(":", 1)[1] for x in received if str(x).startswith("player:")]
    roster = [x for x in roster if x not in sent_players]
    for pid in recv_players:
        if pid not in roster:
            roster.append(pid)
    ctx["roster_by_uid"][uid] = roster

    # Profiles/depth are roster-sensitive and must be regenerated.
    ctx["_profile_cache"] = {}
    ctx["_depth_cache"] = {}
    return ctx


def _profile_map(core, uid: str, ctx: Dict[str, Any]):
    payload = core.build_strategic_asset_profiles_for_team(str(uid), ctx)
    return {str(x.get("asset_id")): x for x in payload.get("assets") or []}, payload


def _optionality(row: Dict[str, Any]) -> float:
    if not row:
        return 0.0
    if row.get("asset_type") == "pick":
        return float((row.get("pick_profile") or {}).get("upside_optionality") or 0.0)
    return float((row.get("future_distribution") or {}).get("upside_optionality") or 0.0)


def _weighted_total(rows: Iterable[Dict[str, Any]], feature: str) -> float:
    total = 0.0
    for row in rows:
        base = float(row.get("base_franchise_value") or row.get("market_dynasty") or 0.0)
        if feature == "liquidity":
            pp = row.get("pick_profile") or {}
            explicitly_authorized = row.get("liquidity_incremental_value_authorized")
            pick_authorized = pp.get("liquidity_incremental_value_authorized")
            if explicitly_authorized is False or (row.get("asset_type") == "pick" and pick_authorized is False):
                f = 0.0
            else:
                f = float(row.get("liquidity_score") or 0.0)
        elif feature == "strategic":
            f = float(row.get("strategic_score") or 0.0)
        elif feature == "optionality":
            pp = row.get("pick_profile") or {}
            if row.get("asset_type") == "pick" and pp.get("quality_optionality_incremental_value_authorized") is False:
                f = 0.0
            else:
                f = _optionality(row)
        elif feature == "resilience":
            # Availability/injury risk is already sampled by the canonical
            # Simulator via weekly active_probability. Roster fragility and
            # depth-insurance remain valuable diagnostics/search context, but
            # receive no second incremental utility without residual evidence.
            if row.get("resilience_incremental_value_authorized") is False:
                f = 0.0
            elif row.get("final_shared_utility_resilience_basis") == "depth_insurance_only":
                f = float(row.get("depth_insurance_score") or 0.0)
            else:
                f = float(row.get("replacement_resilience_score") or 0.0)
        else:
            f = 0.0
        total += base * f
    return total


def install(base_dl, strategic_posture=None, owner_override_user_id=None):
    """Patch a loaded Decision Lab module in-place and return it.

    Owner strategic posture is applied only to owner_override_user_id. Other
    teams retain AUTO/model-derived preferences so bilateral utility remains
    symmetric and does not project the focal owner's intent onto counterparties.
    """
    core = _gm_core()
    weighting = core._state_weighting_runtime
    posture_mod = _load(SCRIPT / "strategic_posture.py", "strategic_posture_for_overlay")
    selected_posture = strategic_posture or os.getenv("FSFFL_STRATEGIC_POSTURE") or "AUTO"
    override_uid = str(
        owner_override_user_id
        if owner_override_user_id is not None
        else (os.getenv("FSFFL_STRATEGIC_POSTURE_USER_ID") or "")
    )
    base_ctx = core._u_load_context()
    original_summary = base_dl.strategic_summary

    def state_aware_summary(uid, actions):
        uid = str(uid)
        sent_ids, received_ids = _asset_ids_for_user(base_dl, actions, uid)
        baseline_map, baseline_payload = _profile_map(core, uid, base_ctx)
        post_ctx = _post_context(core, uid, actions, base_ctx)
        post_map, post_payload = _profile_map(core, uid, post_ctx)
        team = (base_ctx.get("teams") or {}).get(uid, {})
        weight_resolution = weighting.resolve(team)
        effective_selection = selected_posture if (override_uid and uid == override_uid) else "AUTO"
        posture_resolution = posture_mod.resolve(weight_resolution, effective_selection, weighting)

        # Retain the legacy summary only as a fallback for any asset the GM core
        # cannot profile (e.g. malformed external scenario input).
        legacy = original_summary(uid, actions)
        legacy_sent = {str(x.get("asset_id")): x for x in legacy.get("sent") or []}
        legacy_recv = {str(x.get("asset_id")): x for x in legacy.get("received") or []}

        def normalized(aid: str, profile: Dict[str, Any] | None, fallback: Dict[str, Any] | None, source: str):
            p = profile or {}
            f = fallback or {}
            base = float(p.get("base_franchise_value") or f.get("base_franchise_value") or 0.0)
            return {
                "asset_id": aid,
                "asset_type": p.get("asset_type"),
                "name": p.get("name") or f.get("name") or aid,
                "market_dynasty": float(p.get("market_dynasty") or f.get("market_dynasty") or 0.0),
                "market_redraft": float(p.get("market_redraft") or f.get("market_redraft") or 0.0),
                "base_franchise_value": base,
                "break_glass_value": float(p.get("break_glass_value") or f.get("break_glass_value") or base),
                "core_status": p.get("core_status"),
                "strategic_score": float(p.get("strategic_score") or 0.0),
                "liquidity_score": float(p.get("liquidity_score") or 0.0),
                "liquidity_incremental_value_authorized": p.get("liquidity_incremental_value_authorized"),
                "liquidity_score_diagnostic": float(p.get("liquidity_score_diagnostic") or p.get("liquidity_score") or 0.0),
                "replacement_resilience_score": float(p.get("replacement_resilience_score") or 0.0),
                "replacement_resilience_basis": p.get("replacement_resilience_basis"),
                "fragility_dependency_score": float(p.get("fragility_dependency_score") or 0.0),
                "depth_insurance_score": float(p.get("depth_insurance_score") or 0.0),
                "final_shared_utility_resilience_basis": p.get("final_shared_utility_resilience_basis"),
                "resilience_incremental_value_authorized": p.get("resilience_incremental_value_authorized"),
                "future_distribution": p.get("future_distribution"),
                "pick_profile": p.get("pick_profile"),
                "objective_state": weight_resolution["state"],
                "competitive_state": weight_resolution["state"],
                "strategic_posture": posture_resolution["selected_posture"],
                "objective_weights": posture_resolution["active_weights"],
                "profile_source": source,
            }

        sent_rows = [normalized(aid, baseline_map.get(aid), legacy_sent.get(aid), "baseline_focal_gm") for aid in sent_ids]
        rec_rows = [normalized(aid, post_map.get(aid), legacy_recv.get(aid), "post_trade_focal_gm") for aid in received_ids]

        def total(rows, key):
            return sum(float(x.get(key) or 0.0) for x in rows)

        relevant_rows = sent_rows + rec_rows

        def explicitly_authorized(row, row_key, pick_key=None):
            value = row.get(row_key)
            if value is True:
                return True
            if value is False:
                return False
            if pick_key and row.get("asset_type") == "pick":
                return (row.get("pick_profile") or {}).get(pick_key) is True
            return False

        incremental_channel_authorization = {
            "current": True,
            "future": True,
            "liquidity": any(
                explicitly_authorized(x, "liquidity_incremental_value_authorized", "liquidity_incremental_value_authorized")
                for x in relevant_rows
            ),
            "resilience": any(
                explicitly_authorized(x, "resilience_incremental_value_authorized")
                for x in relevant_rows
            ),
        }

        baseline_team_market_redraft = sum(float((x or {}).get("market_redraft") or 0.0) for x in baseline_map.values())
        post_team_market_redraft = sum(float((x or {}).get("market_redraft") or 0.0) for x in post_map.values())

        return {
            "sent": sent_rows,
            "received": rec_rows,
            "baseline_team_market_redraft_value": round(baseline_team_market_redraft, 2),
            "post_team_market_redraft_value": round(post_team_market_redraft, 2),
            "market_dynasty_delta": round(total(rec_rows, "market_dynasty") - total(sent_rows, "market_dynasty"), 2),
            "market_redraft_delta": round(total(rec_rows, "market_redraft") - total(sent_rows, "market_redraft"), 2),
            "base_franchise_value_delta": round(total(rec_rows, "base_franchise_value") - total(sent_rows, "base_franchise_value"), 2),
            "break_glass_delta": round(total(rec_rows, "break_glass_value") - total(sent_rows, "break_glass_value"), 2),
            "liquidity_value_delta": round(_weighted_total(rec_rows, "liquidity") - _weighted_total(sent_rows, "liquidity"), 2),
            "strategic_value_delta": round(_weighted_total(rec_rows, "strategic") - _weighted_total(sent_rows, "strategic"), 2),
            "optionality_value_delta": round(_weighted_total(rec_rows, "optionality") - _weighted_total(sent_rows, "optionality"), 2),
            "resilience_value_delta": round(_weighted_total(rec_rows, "resilience") - _weighted_total(sent_rows, "resilience"), 2),
            "incremental_channel_authorization": incremental_channel_authorization,
            "incremental_channel_authorization_policy": (
                "liquidity/resilience require explicit residual-value authorization; "
                "unauthorized channels are diagnostic-only and cannot consume final utility weight"
            ),
            "composite_channels_diagnostic_only": ["strategic_value_delta", "break_glass_delta"],
            "objective_state": weight_resolution["state"],
            "competitive_state": weight_resolution["state"],
            "strategic_posture": posture_resolution["selected_posture"],
            "strategic_posture_source": posture_resolution["posture_source"],
            "objective_weights": posture_resolution["active_weights"],
            "calculated_state_objective_weights": posture_resolution["calculated_state_weights"],
            "weight_resolution": weight_resolution,
            "strategic_posture_resolution": posture_resolution,
            "hypothetical_profiles_recomputed": True,
            "hypothetical_profile_model": "FSFFL-GM-3.0 continuous state-aware strategic core",
        }

    base_dl.strategic_summary = state_aware_summary
    return base_dl
