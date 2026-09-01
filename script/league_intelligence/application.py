#!/usr/bin/env python3
"""Build read-only League Intelligence views from governed FSFFL outputs.

This application owns no projection, valuation, simulation, utility, trade, or
recommendation math. It validates and exposes upstream output contracts for
human inspection.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

SCRIPT_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from league_intelligence import decision_inspector


MODEL_VERSION = "FSFFL-League-Intelligence-Terminal-1.3"
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA = ROOT / "data"


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _number(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _round(value: Any, digits: int = 3) -> Optional[float]:
    number = _number(value)
    return None if number is None else round(number, digits)


def _mean(values: Iterable[Any]) -> Optional[float]:
    numbers = [number for value in values if (number := _number(value)) is not None]
    return None if not numbers else sum(numbers) / len(numbers)


def architecture() -> Dict[str, Any]:
    return {
        "model_version": MODEL_VERSION,
        "application": "League Intelligence / Analytics Terminal",
        "role": "read-only analytics and observability",
        "owns": [
            "stable analytics view contracts",
            "source compatibility and provenance disclosure",
            "sorting, filtering, ranks, percentiles, and transparent arithmetic comparisons",
        ],
        "does_not_own": [
            "player projections",
            "player or pick valuation",
            "competitive simulation",
            "shared decision utility",
            "trade recommendation or negotiation policy",
            "opportunity search",
            "strategic posture selection",
        ],
        "read_only": {
            "model_state_mutation": False,
            "league_state_mutation": False,
            "transaction_execution": False,
            "recommendation_authority": False,
            "rescoring_authority": False,
        },
        "governing_rule": "Engines calculate; analytics/views expose; Opportunity Engine searches/recommends.",
    }


def _source_record(path: Path, payload: Mapping[str, Any], *, role: str) -> Dict[str, Any]:
    try:
        display_path = str(path.relative_to(ROOT))
    except ValueError:
        display_path = str(path)
    return {
        "path": display_path,
        "role": role,
        "model_version": payload.get("model_version") or payload.get("source_model"),
        "model_stage": payload.get("model_stage"),
        "source": payload.get("source"),
        "generated_at_utc": payload.get("generated_at_utc"),
        "available": True,
    }


def _projection_summary(profile: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if not profile:
        return {
            "available": False,
            "reason": "player absent from the published projection universe",
        }
    weeks = [
        week for week in (profile.get("weeks") or {}).values()
        if isinstance(week, Mapping) and not week.get("is_bye")
    ]
    return {
        "available": True,
        "season_baseline_ppg": _round(profile.get("season_baseline_ppg")),
        "mean_weekly_projection": _round(_mean(w.get("mean") for w in weeks)),
        "mean_weekly_sd": _round(_mean(w.get("sd") for w in weeks)),
        "mean_weekly_p25": _round(_mean(w.get("p25") for w in weeks)),
        "mean_weekly_p75": _round(_mean(w.get("p75") for w in weeks)),
        "volatility_cv": _round(profile.get("volatility_cv"), 5),
        "volatility_source": profile.get("volatility_source"),
        "projection_provenance": profile.get("projection_provenance") or {},
        "weeks_observed": len(weeks),
    }


def _published_fsffl_value_status(row: Mapping[str, Any]) -> Dict[str, Any]:
    """Classify, but never consume, the legacy adjusted player-value field."""
    published = _number(row.get("fsffl_value"))
    market = _number(row.get("market_dynasty"))
    if published is None:
        return {
            "published_value": None,
            "available_for_ranking": False,
            "quarantined": False,
            "reason": "no published FSFFL adjusted value",
        }
    delta = published - market if market is not None else None
    if delta is None or abs(delta) < 1e-9:
        return {
            "published_value": _round(published, 2),
            "available_for_ranking": False,
            "quarantined": False,
            "reason": "field repeats the market anchor and is not an independent FSFFL model value",
        }
    return {
        "published_value": _round(published, 2),
        "published_minus_market": _round(delta, 2),
        "available_for_ranking": False,
        "quarantined": True,
        "reason": (
            "legacy adjustment lacks a current source contract authorizing it as "
            "canonical player-value authority"
        ),
        "diagnostic_adjustments": {
            "recent_performance": (row.get("recent_performance_signal") or {}).get("adjustment"),
            "football_intelligence": (row.get("football_intelligence") or {}).get("total_adjustment"),
        },
    }


def _rank_map(
    rows: Iterable[Mapping[str, Any]], value_getter: Any
) -> tuple[Dict[str, int], Dict[str, int]]:
    eligible = [row for row in rows if _number(value_getter(row)) is not None]
    ordered = sorted(
        eligible,
        key=lambda row: (
            _number(value_getter(row)) or float("-inf"),
            str(row.get("name") or ""),
        ),
        reverse=True,
    )
    overall: Dict[str, int] = {}
    positional: Dict[str, int] = {}
    position_counts: Dict[str, int] = {}
    for rank, row in enumerate(ordered, 1):
        player_id = str(row.get("player_id") or "")
        position = str(row.get("position") or "UNK")
        position_counts[position] = position_counts.get(position, 0) + 1
        overall[player_id] = rank
        positional[player_id] = position_counts[position]
    return overall, positional


def _rank_players(
    assets: Mapping[str, Any], projections: Mapping[str, Any],
    team_context: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> list[Dict[str, Any]]:
    team_context = team_context or {}
    source_rows = [dict(row) for row in (assets.get("players") or [])]
    projection_players = projections.get("players") or {}
    projection_summaries = {
        str(row.get("player_id") or ""): _projection_summary(
            projection_players.get(str(row.get("player_id") or ""))
        )
        for row in source_rows
    }
    market_ranks, market_position_ranks = _rank_map(
        source_rows, lambda row: row.get("market_dynasty")
    )
    projection_rows = [
        {
            **row,
            "projection_ppg": projection_summaries[str(row.get("player_id") or "")].get(
                "mean_weekly_projection"
            ),
        }
        for row in source_rows
    ]
    projection_ranks, projection_position_ranks = _rank_map(
        projection_rows, lambda row: row.get("projection_ppg")
    )
    ordered = sorted(
        source_rows,
        key=lambda row: (
            _number(row.get("market_dynasty")) is not None,
            _number(row.get("market_dynasty")) or float("-inf"),
            str(row.get("name") or ""),
        ),
        reverse=True,
    )
    output: list[Dict[str, Any]] = []
    for row in ordered:
        player_id = str(row.get("player_id") or "")
        output.append({
            "player_id": player_id,
            "name": row.get("name"),
            "position": str(row.get("position") or "UNK"),
            "nfl_team": row.get("nfl_team"),
            "age": row.get("age"),
            "injury_status": row.get("injury_status"),
            "current_owner_user_id": row.get("current_owner_user_id"),
            "current_owner_manager": row.get("current_owner_manager"),
            "current_owner_team": row.get("current_owner_team"),
            "long_term_market_rank": market_ranks.get(player_id),
            "long_term_market_position_rank": market_position_ranks.get(player_id),
            "long_term_market_value": _round(row.get("market_dynasty"), 2),
            "market_redraft_value": _round(row.get("market_redraft"), 2),
            "source_market_rank": row.get("market_rank"),
            "source_market_position_rank": row.get("position_rank"),
            "current_season_projection_rank": projection_ranks.get(player_id),
            "current_season_projection_position_rank": projection_position_ranks.get(player_id),
            "current_season_projection": projection_summaries[player_id],
            "published_fsffl_value_status": _published_fsffl_value_status(row),
            "team_specific_context": team_context.get(player_id) or {
                "available": False,
                "reason": "no current governed GM3 team-context payload supplied",
            },
            "field_provenance": {
                "identity_and_ownership": {
                    "authority": "published league asset catalog",
                    "source": "data/fsffl_asset_values.json#players",
                    "source_model": assets.get("model_version"),
                    "generated_at_utc": assets.get("generated_at_utc"),
                },
                "long_term_market_value": {
                    "authority": "published external dynasty market anchor",
                    "source": "data/fsffl_asset_values.json#players[].market_dynasty",
                    "source_model": assets.get("model_version"),
                    "generated_at_utc": assets.get("generated_at_utc"),
                },
                "current_season_projection": {
                    "authority": "published Projection System output",
                    "source": "data/simulator/2026/inputs/player_weekly_projections.json#players",
                    "model_stage": projections.get("model_stage"),
                    "source_description": projections.get("source"),
                },
                "ranks": {
                    "authority": "League Intelligence monotonic presentation transform",
                    "creates_new_value": False,
                },
            },
        })
    return output


def _percentile(value: Any, population: Iterable[Any]) -> Optional[float]:
    number = _number(value)
    values = [x for item in population if (x := _number(item)) is not None]
    if number is None or not values:
        return None
    if len(values) == 1:
        return 1.0
    less = sum(x < number for x in values)
    equal = sum(x == number for x in values)
    return round((less + 0.5 * (equal - 1)) / (len(values) - 1), 4)


def _team_context_status(
    path: Optional[Path], data_dir: Path, focus_user_id: Optional[str]
) -> tuple[Dict[str, Any], Dict[str, Mapping[str, Any]], Optional[Mapping[str, Any]]]:
    if not path:
        return ({
            "available": False,
            "compatible": False,
            "reason": "no GM3 team-context payload supplied",
            "terminal_consumes_payload": False,
        }, {}, None)
    path = Path(path)
    if not path.exists():
        return ({
            "available": False,
            "compatible": False,
            "reason": f"team-context payload does not exist: {path}",
            "terminal_consumes_payload": False,
        }, {}, None)
    payload = _load(path)
    expected_focus = str(focus_user_id or payload.get("focus_user_id") or "")
    errors = []
    if payload.get("authority") != "GM3 Team Improvement":
        errors.append("payload authority is not GM3 Team Improvement")
    if str(payload.get("focus_user_id") or "") != expected_focus:
        errors.append("payload focus team does not match requested viewer team")
    contract = payload.get("scenario_contract") or {}
    if not contract.get("zero_price_counterfactual"):
        errors.append("payload lacks the zero-price scenario declaration")
    if any(contract.get(key) for key in (
        "fair_price_estimate", "willingness_to_pay_estimate",
        "trade_acceptance_probability", "recommendation",
    )):
        errors.append("payload claims forbidden price, acceptance, or recommendation authority")
    for source, expected_hash in (payload.get("source_hashes") or {}).items():
        source_path = ROOT / source
        if not source_path.exists() or _sha256(source_path) != expected_hash:
            errors.append(f"source revision mismatch: {source}")
    compatible = not errors
    records = {
        str(row.get("player_id") or ""): row
        for row in (payload.get("records") or [])
        if compatible and row.get("player_id")
    }
    return ({
        "available": True,
        "compatible": compatible,
        "focus_user_id": payload.get("focus_user_id"),
        "model_version": payload.get("model_version"),
        "shared_decision_utility": payload.get("shared_decision_utility"),
        "generated_at_utc": payload.get("generated_at_utc"),
        "record_count": payload.get("record_count"),
        "available_record_count": payload.get("available_record_count"),
        "errors": errors,
        "terminal_consumes_payload": compatible,
        "quarantine_enforced": not compatible,
    }, records, payload if compatible else None)


def _positional_heat_map(
    assets: Mapping[str, Any], projections: Mapping[str, Any],
    league: Mapping[str, Any], standings: Mapping[str, Any],
) -> Dict[str, Any]:
    positions = ("QB", "RB", "WR", "TE")
    dedicated_slots = {
        position: sum(str(slot) == position for slot in (league.get("roster_positions") or []))
        for position in positions
    }
    projection_players = projections.get("players") or {}
    teams = {
        str(row.get("user_id") or ""): {
            "user_id": str(row.get("user_id") or ""),
            "manager": row.get("manager"),
            "team_name": row.get("team_name"),
            "positions": {},
            "future_draft_capital_market_value": 0.0,
            "long_term_player_market_value": 0.0,
            "competitive_outcomes": {
                key: row.get(key) for key in (
                    "expected_wins", "expected_points_for", "playoff_probability",
                    "bye_probability", "championship_probability",
                )
            },
        }
        for row in (standings.get("teams") or [])
    }
    by_team_position: Dict[str, Dict[str, list[Dict[str, Any]]]] = {
        uid: {position: [] for position in positions} for uid in teams
    }
    for player in (assets.get("players") or []):
        uid = str(player.get("current_owner_user_id") or "")
        position = str(player.get("position") or "")
        if uid not in teams or position not in positions:
            continue
        projection = _projection_summary(projection_players.get(str(player.get("player_id") or "")))
        by_team_position[uid][position].append({
            "projection": _number(projection.get("mean_weekly_projection")),
            "market": _number(player.get("market_dynasty")) or 0.0,
        })
        teams[uid]["long_term_player_market_value"] += _number(player.get("market_dynasty")) or 0.0
    for pick in (assets.get("picks") or []):
        uid = str(pick.get("current_owner_user_id") or "")
        if uid in teams:
            teams[uid]["future_draft_capital_market_value"] += _number(pick.get("market_dynasty")) or 0.0
    for uid, team in teams.items():
        team["future_draft_capital_market_value"] = round(team["future_draft_capital_market_value"], 2)
        team["long_term_player_market_value"] = round(team["long_term_player_market_value"], 2)
        for position in positions:
            rows = by_team_position[uid][position]
            projections_sorted = sorted(
                (row["projection"] for row in rows if row["projection"] is not None), reverse=True
            )
            markets = sorted((row["market"] for row in rows), reverse=True)
            slots = dedicated_slots[position]
            team["positions"][position] = {
                "dedicated_starter_slots": slots,
                "rostered_player_count": len(rows),
                "projected_player_count": len(projections_sorted),
                "dedicated_starter_projection_ppg": round(sum(projections_sorted[:slots]), 3),
                "projection_depth_beyond_dedicated_slots_ppg": round(sum(projections_sorted[slots:]), 3),
                "total_rostered_projection_ppg": round(sum(projections_sorted), 3),
                "dedicated_starter_long_term_market_value": round(sum(markets[:slots]), 2),
                "total_position_long_term_market_value": round(sum(markets), 2),
            }
            if position == "QB":
                team["positions"][position]["top_two_qb_projection_ppg"] = round(sum(projections_sorted[:2]), 3)
    team_rows = list(teams.values())
    for team in team_rows:
        for position in positions:
            row = team["positions"][position]
            for field in (
                "dedicated_starter_projection_ppg",
                "projection_depth_beyond_dedicated_slots_ppg",
                "total_position_long_term_market_value",
            ):
                row[f"{field}_league_percentile"] = _percentile(
                    row[field], (other["positions"][position][field] for other in team_rows)
                )
            if position == "QB":
                row["top_two_qb_projection_ppg_league_percentile"] = _percentile(
                    row["top_two_qb_projection_ppg"],
                    (other["positions"][position]["top_two_qb_projection_ppg"] for other in team_rows),
                )
        for field in ("future_draft_capital_market_value", "long_term_player_market_value"):
            team[f"{field}_league_percentile"] = _percentile(
                team[field], (other[field] for other in team_rows)
            )
    team_rows.sort(key=lambda row: str(row.get("team_name") or ""))
    return {
        "authority": {
            "current_season": "Projection System",
            "long_term_and_draft_capital": "published market anchors",
            "competitive_outcomes": "Simulator",
            "percentiles": "League Intelligence monotonic display transform",
        },
        "creates_new_strength_model": False,
        "creates_categorical_team_labels": False,
        "dedicated_slots_derived_from_league_rules": dedicated_slots,
        "superflex_handling": "top-two QB projection is exposed separately; SUPER_FLEX is not forced into QB",
        "teams": team_rows,
    }


def _trade_partner_map(heat_map: Mapping[str, Any], focus_user_id: Optional[str],
                       team_context_payload: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    teams = list(heat_map.get("teams") or [])
    positions = ("QB", "RB", "WR", "TE")
    rows = []
    for buyer in teams:
        for holder in teams:
            if buyer["user_id"] == holder["user_id"]:
                continue
            for position in positions:
                buyer_pct = buyer["positions"][position]["total_position_long_term_market_value_league_percentile"]
                holder_pct = holder["positions"][position]["total_position_long_term_market_value_league_percentile"]
                rows.append({
                    "investigating_team_user_id": buyer["user_id"],
                    "investigating_team": buyer["team_name"],
                    "asset_holding_team_user_id": holder["user_id"],
                    "asset_holding_team": holder["team_name"],
                    "position": position,
                    "investigating_team_percentile": buyer_pct,
                    "asset_holding_team_percentile": holder_pct,
                    "league_relative_strength_gap": round((holder_pct or 0.0) - (buyer_pct or 0.0), 4),
                })
    rows.sort(key=lambda row: row["league_relative_strength_gap"], reverse=True)
    player_rows = []
    for row in ((team_context_payload or {}).get("records") or []):
        if not row.get("available") or row.get("scenario") != "ZERO_PRICE_ROSTER_TRANSFER":
            continue
        focal = row.get("focal_team_context") or {}
        owner = row.get("current_owner_context") or {}
        player_rows.append({
            "player_id": row.get("player_id"),
            "name": row.get("name"),
            "position": row.get("position"),
            "current_owner_user_id": row.get("current_owner_user_id"),
            "current_owner_team": row.get("current_owner_team"),
            "viewer_gross_marginal_utility": ((focal.get("shared_decision_utility") or {}).get("score")),
            "current_owner_zero_compensation_utility": ((owner.get("shared_decision_utility") or {}).get("score")),
            "viewer_simulator_delta": focal.get("simulator_delta") or {},
            "viewer_utility_components": ((focal.get("shared_decision_utility") or {}).get("components") or {}),
            "current_owner_utility_components": ((owner.get("shared_decision_utility") or {}).get("components") or {}),
            "fair_price_estimate": False,
            "recommendation": False,
        })
    player_rows.sort(key=lambda row: _number(row["viewer_gross_marginal_utility"]) or float("-inf"), reverse=True)
    return {
        "purpose": "identify league-relative roster complementarities for investigation",
        "recommendation": False,
        "acceptance_probability": False,
        "fair_trade_claim": False,
        "positional_complementarities": rows,
        "focus_team_user_id": str(focus_user_id) if focus_user_id else None,
        "focus_team_positional_complementarities": [
            row for row in rows if str(row["investigating_team_user_id"]) == str(focus_user_id)
        ] if focus_user_id else [],
        "focus_team_player_context": player_rows,
    }


def _legacy_contract_status(data_dir: Path) -> Dict[str, Any]:
    """Identify pre-state/posture-separation GM artifacts without consuming them."""
    teams_dir = data_dir / "gm" / "teams"
    incompatible = []
    for path in sorted(teams_dir.glob("*/core_values.json")):
        payload = _load(path)
        has_legacy_state = "team_state" in payload
        has_separated_state = (
            "competitive_state" in payload and "strategic_posture" in payload
        )
        if has_legacy_state and not has_separated_state:
            incompatible.append(str(path.relative_to(ROOT)))
    return {
        "required_contract": "competitive state and strategic posture exposed separately",
        "compatible": not incompatible,
        "incompatible_artifacts": incompatible,
        "terminal_consumes_incompatible_artifacts": False,
        "note": (
            "Pre-separation artifacts are disclosed but excluded from current Terminal views."
            if incompatible else "Published GM team artifacts satisfy the separated state/posture contract."
        ),
    }


def _player_value_contract_status(players: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    statuses = [row["published_fsffl_value_status"] for row in players]
    quarantined = [status for status in statuses if status["quarantined"]]
    aliases = [
        status for status in statuses
        if not status["quarantined"] and status.get("published_value") is not None
    ]
    return {
        "required_contract": (
            "independent canonical FSFFL player value with current provenance and "
            "explicitly authorized adjustments"
        ),
        "compatible": not quarantined,
        "published_player_count": len(statuses),
        "quarantined_player_count": len(quarantined),
        "market_anchor_alias_count": len(aliases),
        "terminal_uses_published_fsffl_value_for_ranking": False,
        "quarantine_enforced": True,
        "active_rankings_safe_for_presentation": True,
        "authoritative_model_vs_market_available": False,
        "note": (
            "Legacy adjusted values are quarantined; values equal to market are disclosed as aliases, "
            "not presented as independent model values."
        ),
    }


def build_terminal(
    data_dir: Path = DEFAULT_DATA,
    *,
    focus_user_id: Optional[str] = None,
    team_context_path: Optional[Path] = None,
    decision_input_path: Optional[Path] = None,
    decision_selector: Optional[str] = None,
) -> Dict[str, Any]:
    data_dir = Path(data_dir)
    asset_path = data_dir / "fsffl_asset_values.json"
    projection_path = data_dir / "simulator" / "2026" / "inputs" / "player_weekly_projections.json"
    standings_path = data_dir / "gm" / "league" / "simulator_context.json"
    league_path = data_dir / "league.json"
    assets = _load(asset_path)
    projections = _load(projection_path)
    standings = _load(standings_path)
    league = _load(league_path)

    team_context_status, team_context_records, team_context_payload = _team_context_status(
        team_context_path, data_dir, focus_user_id
    )
    if not focus_user_id and team_context_payload:
        focus_user_id = str(team_context_payload.get("focus_user_id") or "")
    players = _rank_players(assets, projections, team_context_records)
    player_value_contract = _player_value_contract_status(players)
    heat_map = _positional_heat_map(assets, projections, league, standings)
    trade_partner_map = _trade_partner_map(heat_map, focus_user_id, team_context_payload)
    if decision_input_path:
        decision_view = decision_inspector.load_and_inspect(
            decision_input_path, decision_selector
        )
        decision_status = {
            "available": True,
            "compatible": True,
            "fully_reconciled_attribution": bool(
                (decision_view.get("source_contract") or {}).get("fully_reconciled_attribution")
            ),
            "partial_inspection": bool(
                (decision_view.get("source_contract") or {}).get("partial_inspection")
            ),
            "source_path": str(decision_input_path),
            "selector": decision_selector,
        }
    else:
        decision_view = {
            "available": False,
            "reason": "no governed decision input was selected",
            "creates_independent_score": False,
            "creates_trade_value": False,
            "creates_acceptance_probability": False,
            "recommendation": False,
        }
        decision_status = {
            "available": False,
            "compatible": False,
            "partial_inspection": False,
            "reason": "no governed decision input was selected",
        }
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_version": MODEL_VERSION,
        "season": projections.get("season") or standings.get("season"),
        "architecture": architecture(),
        "source_registry": [
            _source_record(asset_path, assets, role="published market anchors and legacy adjusted-value diagnostics"),
            _source_record(projection_path, projections, role="published current-season projection means and uncertainty"),
            _source_record(standings_path, standings, role="published Simulator competitive outcomes"),
            _source_record(league_path, league, role="rule-defined league roster construction"),
        ],
        "contract_health": {
            "competitive_state_strategic_posture": _legacy_contract_status(data_dir),
            "player_value_authority": player_value_contract,
            "gm3_team_context": team_context_status,
            "decision_utility_inspector": decision_status,
        },
        "views": {
            "player_value_rankings": {
                "authority": {
                    "long_term": "published external market dynasty value",
                    "current_season": "published projection-system weekly mean and uncertainty",
                },
                "creates_new_player_value": False,
                "creates_cross_horizon_composite": False,
                "recommendation": False,
                "sort_default": "long_term_market_value descending",
                "player_count": len(players),
                "players": players,
                "unavailable_perspectives": [{
                    "perspective": "independent FSFFL model value and model-versus-market discrepancy",
                    "reason": (
                        "the published fsffl_value field is either a market alias or a legacy adjusted "
                        "value without a current canonical authority contract"
                    ),
                }],
            },
            "league_competitive_landscape": {
                "authority": "Simulator application output",
                "creates_independent_power_score": False,
                "teams": standings.get("teams") or [],
                "simulator_model_version": standings.get("simulator_model_version"),
            },
            "team_relative_player_context": {
                "authority": "GM3 Team Improvement",
                "focus_user_id": str(focus_user_id) if focus_user_id else None,
                "gross_context_only": True,
                "creates_trade_price": False,
                "recommendation": False,
                "records": list(team_context_records.values()),
            },
            "positional_strength_heat_map": heat_map,
            "trade_partner_intelligence": trade_partner_map,
            "decision_utility_inspector": decision_view,
        },
        "capability_status": {
            "read_only_application_boundary": True,
            "source_provenance": True,
            "source_contract_health": True,
            "player_value_rankings": True,
            "long_term_market_rankings": True,
            "current_season_projection_rankings": True,
            "projection_uncertainty": True,
            "model_vs_market": False,
            "model_vs_market_blocked_by_source_contract": True,
            "league_competitive_landscape": True,
            "team_specific_player_context": bool(team_context_status.get("compatible")),
            "viewer_team_and_current_owner_context": bool(team_context_status.get("compatible")),
            "positional_strength_heat_map": True,
            "trade_partner_map": True,
            "decision_utility_inspector": bool(decision_status.get("available")),
        },
    }


def _fmt(value: Any, digits: int = 1) -> str:
    number = _number(value)
    return "-" if number is None else f"{number:,.{digits}f}"


def render_player_rankings_markdown(payload: Mapping[str, Any], *, limit: int = 25) -> str:
    view = payload["views"]["player_value_rankings"]
    players = view["players"]
    contract = payload["contract_health"]["player_value_authority"]
    lines = [
        "# FSFFL Player Value & Rankings Terminal",
        "",
        f"Season: {payload.get('season')}  ",
        f"Terminal contract: {payload.get('model_version')}",
        "",
        "This is a read-only analytical view. It exposes separate governed perspectives and does not create a blended player score or recommendation.",
        "",
        "## Source health",
        "",
        "- Active long-term perspective: published market dynasty value.",
        "- Active current-season perspective: published weekly projection mean and uncertainty.",
        f"- Quarantined legacy adjusted player values: {contract['quarantined_player_count']}.",
        f"- Market aliases not presented as independent model values: {contract['market_anchor_alias_count']}.",
        "- Model-versus-market ranking: unavailable until a current independent FSFFL player-value authority is published.",
        "",
        "## Long-term market ranking",
        "",
        "| Rank | Player | Pos | NFL | Owner | Value | 2026 PPG | 2026 Rank |",
        "|---:|---|:---:|:---:|---|---:|---:|---:|",
    ]
    for row in players[:limit]:
        projection = row["current_season_projection"]
        lines.append(
            f"| {row['long_term_market_rank']} | {row['name']} | {row['position']} | "
            f"{row.get('nfl_team') or '-'} | {row.get('current_owner_team') or 'Unrostered'} | "
            f"{_fmt(row['long_term_market_value'], 0)} | "
            f"{_fmt(projection.get('mean_weekly_projection'))} | "
            f"{row.get('current_season_projection_rank') or '-'} |"
        )

    projected = [
        row for row in players if row.get("current_season_projection_rank") is not None
    ]
    projected.sort(key=lambda row: row["current_season_projection_rank"])
    lines.extend([
        "",
        "## Current-season projection ranking",
        "",
        "| Rank | Player | Pos | Owner | Mean | P25 | P75 | SD | Long-term Rank |",
        "|---:|---|:---:|---|---:|---:|---:|---:|---:|",
    ])
    for row in projected[:limit]:
        projection = row["current_season_projection"]
        lines.append(
            f"| {row['current_season_projection_rank']} | {row['name']} | {row['position']} | "
            f"{row.get('current_owner_team') or 'Unrostered'} | "
            f"{_fmt(projection.get('mean_weekly_projection'))} | "
            f"{_fmt(projection.get('mean_weekly_p25'))} | "
            f"{_fmt(projection.get('mean_weekly_p75'))} | "
            f"{_fmt(projection.get('mean_weekly_sd'))} | "
            f"{row.get('long_term_market_rank') or '-'} |"
        )

    for position in ("QB", "RB", "WR", "TE"):
        position_rows = [row for row in players if row["position"] == position]
        lines.extend([
            "",
            f"## {position} long-term ranking",
            "",
            "| Pos. Rank | Player | Owner | Value | 2026 Pos. Rank | 2026 PPG |",
            "|---:|---|---|---:|---:|---:|",
        ])
        for row in position_rows[:10]:
            projection = row["current_season_projection"]
            lines.append(
                f"| {row['long_term_market_position_rank']} | {row['name']} | "
                f"{row.get('current_owner_team') or 'Unrostered'} | "
                f"{_fmt(row['long_term_market_value'], 0)} | "
                f"{row.get('current_season_projection_position_rank') or '-'} | "
                f"{_fmt(projection.get('mean_weekly_projection'))} |"
            )

    team_status = payload["contract_health"]["gm3_team_context"]
    if team_status.get("compatible"):
        context_rows = [
            row for row in payload["views"]["team_relative_player_context"]["records"]
            if row.get("available")
        ]
        external = [row for row in context_rows if row.get("scenario") == "ZERO_PRICE_ROSTER_TRANSFER"]
        external.sort(
            key=lambda row: _number(((row.get("focal_team_context") or {}).get("shared_decision_utility") or {}).get("score")) or float("-inf"),
            reverse=True,
        )
        retained = [row for row in context_rows if row.get("scenario") == "FOCAL_ROSTER_REMOVAL"]
        retained.sort(
            key=lambda row: _number(((row.get("focal_team_context") or {}).get("shared_decision_utility") or {}).get("score")) or float("inf")
        )
        lines.extend([
            "",
            "## Team-relative player context",
            "",
            "These are gross GM3 roster counterfactuals, not trade prices. External players are transferred without compensation; players already on the viewing roster are removed to measure retention dependence.",
            "",
            "### Largest gross gains for the viewing team",
            "",
            "| Player | Pos | Current team | Viewer utility | Owner utility | Exp. wins delta |",
            "|---|:---:|---|---:|---:|---:|",
        ])
        for row in external[:10]:
            focal = row.get("focal_team_context") or {}
            owner = row.get("current_owner_context") or {}
            lines.append(
                f"| {row.get('name')} | {row.get('position')} | {row.get('current_owner_team')} | "
                f"{_fmt(((focal.get('shared_decision_utility') or {}).get('score')), 0)} | "
                f"{_fmt(((owner.get('shared_decision_utility') or {}).get('score')), 0)} | "
                f"{_fmt((focal.get('simulator_delta') or {}).get('expected_wins'), 2)} |"
            )
        lines.extend([
            "",
            "### Largest retention losses for the viewing team",
            "",
            "| Player | Pos | Removal utility | Exp. wins delta | Playoff delta |",
            "|---|:---:|---:|---:|---:|",
        ])
        for row in retained[:10]:
            focal = row.get("focal_team_context") or {}
            lines.append(
                f"| {row.get('name')} | {row.get('position')} | "
                f"{_fmt(((focal.get('shared_decision_utility') or {}).get('score')), 0)} | "
                f"{_fmt((focal.get('simulator_delta') or {}).get('expected_wins'), 2)} | "
                f"{_fmt((focal.get('simulator_delta') or {}).get('playoff_probability'), 3)} |"
            )

    heat = payload["views"]["positional_strength_heat_map"]
    lines.extend([
        "",
        "## League positional strength heat map",
        "",
        "Values are league percentiles of governed raw fields. They are display transforms, not team grades or decision weights.",
        "",
        "| Team | QB starters | QB top-2 | RB starters | WR starters | TE starters | Future picks |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ])
    for team in heat.get("teams") or []:
        pos = team["positions"]
        lines.append(
            f"| {team.get('team_name')} | "
            f"{_fmt(pos['QB']['dedicated_starter_projection_ppg_league_percentile'], 3)} | "
            f"{_fmt(pos['QB']['top_two_qb_projection_ppg_league_percentile'], 3)} | "
            f"{_fmt(pos['RB']['dedicated_starter_projection_ppg_league_percentile'], 3)} | "
            f"{_fmt(pos['WR']['dedicated_starter_projection_ppg_league_percentile'], 3)} | "
            f"{_fmt(pos['TE']['dedicated_starter_projection_ppg_league_percentile'], 3)} | "
            f"{_fmt(team.get('future_draft_capital_market_value_league_percentile'), 3)} |"
        )

    partner = payload["views"]["trade_partner_intelligence"]
    focus_rows = [row for row in partner.get("focus_team_positional_complementarities") or [] if row.get("league_relative_strength_gap", 0) > 0]
    if focus_rows:
        lines.extend([
            "",
            "## Trade-partner intelligence",
            "",
            "These are roster-shape differences worth investigating. They do not claim that a fair or acceptable trade exists.",
            "",
            "The comparison field is total long-term positional market value; the raw values remain available in the JSON payload.",
            "",
            "| Position | Team with relative strength | Viewing team value pct. | Other team value pct. | Gap |",
            "|:---:|---|---:|---:|---:|",
        ])
        for row in focus_rows[:12]:
            lines.append(
                f"| {row.get('position')} | {row.get('asset_holding_team')} | "
                f"{_fmt(row.get('investigating_team_percentile'), 3)} | "
                f"{_fmt(row.get('asset_holding_team_percentile'), 3)} | "
                f"{_fmt(row.get('league_relative_strength_gap'), 3)} |"
            )

    state_contract = payload["contract_health"]["competitive_state_strategic_posture"]
    lines.extend([
        "",
        "## Quarantine and limitations",
        "",
        f"- {len(state_contract['incompatible_artifacts'])} pre-separation team-profile artifacts are excluded because they combine competitive state and strategic posture.",
        "- The legacy adjusted `fsffl_value` field is never used to order this view.",
        "- No current/future weighted blend, team-fit multiplier, trade signal, or recommendation is calculated here.",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_DATA / "league_intelligence" / "terminal.json",
    )
    parser.add_argument("--markdown-output", type=Path)
    parser.add_argument("--focus-user-id")
    parser.add_argument("--team-context", type=Path)
    parser.add_argument("--decision-input", type=Path)
    parser.add_argument("--decision-selector")
    parser.add_argument("--decision-markdown-output", type=Path)
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()
    payload = build_terminal(
        args.data_dir,
        focus_user_id=args.focus_user_id,
        team_context_path=args.team_context,
        decision_input_path=args.decision_input,
        decision_selector=args.decision_selector,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if args.markdown_output:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(
            render_player_rankings_markdown(payload, limit=args.limit), encoding="utf-8"
        )
    if args.decision_markdown_output:
        view = payload["views"]["decision_utility_inspector"]
        if not view.get("available", True):
            raise ValueError("--decision-markdown-output requires --decision-input")
        args.decision_markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.decision_markdown_output.write_text(
            decision_inspector.render_markdown(view), encoding="utf-8"
        )
    print(json.dumps({
        "model_version": MODEL_VERSION,
        "players": payload["views"]["player_value_rankings"]["player_count"],
        "quarantined_adjusted_values": payload["contract_health"]["player_value_authority"]["quarantined_player_count"],
        "output": str(args.output),
        "markdown_output": str(args.markdown_output) if args.markdown_output else None,
        "decision_inspector_available": payload["capability_status"]["decision_utility_inspector"],
        "decision_markdown_output": str(args.decision_markdown_output) if args.decision_markdown_output else None,
    }, indent=2))


if __name__ == "__main__":
    main()
