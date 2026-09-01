#!/usr/bin/env python3
"""Build read-only League Intelligence views from governed FSFFL outputs.

This application owns no projection, valuation, simulation, utility, trade, or
recommendation math. It validates and exposes upstream output contracts for
human inspection.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional


MODEL_VERSION = "FSFFL-League-Intelligence-Terminal-1.1"
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA = ROOT / "data"


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


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
    assets: Mapping[str, Any], projections: Mapping[str, Any]
) -> list[Dict[str, Any]]:
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


def build_terminal(data_dir: Path = DEFAULT_DATA) -> Dict[str, Any]:
    data_dir = Path(data_dir)
    asset_path = data_dir / "fsffl_asset_values.json"
    projection_path = data_dir / "simulator" / "2026" / "inputs" / "player_weekly_projections.json"
    standings_path = data_dir / "gm" / "league" / "simulator_context.json"
    assets = _load(asset_path)
    projections = _load(projection_path)
    standings = _load(standings_path)

    players = _rank_players(assets, projections)
    player_value_contract = _player_value_contract_status(players)
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_version": MODEL_VERSION,
        "season": projections.get("season") or standings.get("season"),
        "architecture": architecture(),
        "source_registry": [
            _source_record(asset_path, assets, role="published market anchors and legacy adjusted-value diagnostics"),
            _source_record(projection_path, projections, role="published current-season projection means and uncertainty"),
            _source_record(standings_path, standings, role="published Simulator competitive outcomes"),
        ],
        "contract_health": {
            "competitive_state_strategic_posture": _legacy_contract_status(data_dir),
            "player_value_authority": player_value_contract,
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
            "positional_strength_heat_map": False,
            "trade_partner_map": False,
            "decision_utility_inspector": False,
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
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()
    payload = build_terminal(args.data_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if args.markdown_output:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(
            render_player_rankings_markdown(payload, limit=args.limit), encoding="utf-8"
        )
    print(json.dumps({
        "model_version": MODEL_VERSION,
        "players": payload["views"]["player_value_rankings"]["player_count"],
        "quarantined_adjusted_values": payload["contract_health"]["player_value_authority"]["quarantined_player_count"],
        "output": str(args.output),
        "markdown_output": str(args.markdown_output) if args.markdown_output else None,
    }, indent=2))


if __name__ == "__main__":
    main()
