#!/usr/bin/env python3
"""Read-only normalization for governed decision and utility outputs.

This module never invokes a scoring, simulation, negotiation, or recommendation
engine. It exposes fields already published by their owning applications and
marks absent evidence as unavailable.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Optional


MODEL_VERSION = "FSFFL-League-Intelligence-Decision-Inspector-1.0"


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _number(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def select_record(document: Mapping[str, Any], selector: Optional[str] = None) -> dict[str, Any]:
    """Select one record through an explicit dot/index path.

    An unqualified document is accepted only when it already looks like one
    governed decision record. Reports containing candidate lists require an
    explicit selector so the Terminal does not choose or rank a decision.
    """
    if selector:
        value: Any = document
        for token in selector.split("."):
            if isinstance(value, list):
                try:
                    value = value[int(token)]
                except (ValueError, IndexError) as exc:
                    raise ValueError(f"invalid list selector token: {token}") from exc
            elif isinstance(value, Mapping) and token in value:
                value = value[token]
            else:
                raise ValueError(f"decision selector not found: {selector}")
        if not isinstance(value, Mapping):
            raise ValueError("decision selector must resolve to an object")
        return dict(value)

    selected = document.get("selected_decision")
    if isinstance(selected, Mapping):
        return dict(selected)
    decision_markers = {
        "simulation",
        "decision_attribution",
        "focal_decision_attribution",
        "negotiation_frontier",
        "team_improvement_score",
    }
    if decision_markers.intersection(document):
        return dict(document)
    raise ValueError(
        "input contains multiple or unselected records; provide an explicit decision selector"
    )


def _attribution(record: Mapping[str, Any], *, counterparty: bool = False) -> dict[str, Any]:
    if counterparty:
        candidates = (
            record.get("counterparty_decision_attribution"),
            _mapping(record.get("counterparty_shared_decision_utility")).get("decision_attribution"),
        )
    else:
        candidates = (
            record.get("focal_decision_attribution"),
            record.get("decision_attribution"),
            _mapping(record.get("focal_shared_decision_utility")).get("decision_attribution"),
        )
    for candidate in candidates:
        if isinstance(candidate, Mapping):
            return dict(candidate)
    return {}


def _utility_score(record: Mapping[str, Any], attribution: Mapping[str, Any], *, counterparty: bool = False) -> Optional[float]:
    score = _number(attribution.get("final_shared_decision_utility"))
    if score is not None:
        return score
    keys = (
        ("counterparty_shared_decision_utility_score", "buyer_decision_utility_score")
        if counterparty
        else ("focal_shared_decision_utility_score", "team_improvement_score", "shared_decision_utility_score")
    )
    for key in keys:
        score = _number(record.get(key))
        if score is not None:
            return score
    scored = _mapping(record.get("counterparty_shared_decision_utility" if counterparty else "focal_shared_decision_utility"))
    return _number(scored.get("score"))


def _perspective(
    record: Mapping[str, Any],
    simulation: Mapping[str, Any],
    *,
    counterparty: bool = False,
) -> dict[str, Any]:
    attribution = _attribution(record, counterparty=counterparty)
    side = _mapping(simulation.get("counterparty")) if counterparty else dict(simulation)
    delta = _mapping(side.get("focus_delta"))
    strategic = _mapping(side.get("strategic"))
    score = _utility_score(record, attribution, counterparty=counterparty)
    channels = list(attribution.get("channels") or [])
    primitives = {
        str(row.get("channel")): row.get("primitive_value")
        for row in channels
        if isinstance(row, Mapping) and row.get("channel")
    }
    return {
        "available": bool(side or attribution or score is not None),
        "shared_decision_utility": score,
        "decision_attribution": attribution,
        "attribution_available": bool(attribution),
        "attribution_reconciles": attribution.get("reconciles") if attribution else None,
        "simulator_delta": {
            "expected_wins": _number(delta.get("expected_wins")),
            "expected_points_for": _number(delta.get("expected_points_for")),
            "playoff_probability": _number(delta.get("playoff_probability")),
            "bye_probability": _number(delta.get("bye_probability")),
            "championship_probability": _number(delta.get("championship_probability")),
        },
        "utility_channels": channels,
        "current_value_primitive": _number(primitives.get("current")),
        "future_value_primitive": _number(primitives.get("future")),
        "liquidity_primitive": _number(primitives.get("liquidity")),
        "resilience_primitive": _number(primitives.get("resilience")),
        "strategic_context": strategic,
        "competitive_state": strategic.get("competitive_state"),
        "strategic_posture": strategic.get("strategic_posture"),
        "strategic_posture_source": strategic.get("strategic_posture_source"),
        "active_objective_weights": strategic.get("objective_weights") or {},
        "roster_resolution": _mapping(side.get("roster_resolution")),
    }


def inspect_decision(
    record: Mapping[str, Any],
    *,
    source_path: Optional[str] = None,
    selector: Optional[str] = None,
) -> dict[str, Any]:
    simulation = _mapping(record.get("simulation") or record.get("governed_simulation"))
    focal = _perspective(record, simulation)
    counterparty = _perspective(record, simulation, counterparty=True)
    negotiation = _mapping(record.get("negotiation_frontier"))
    near_frontier = _mapping(
        record.get("near_frontier_evidence") or negotiation.get("near_frontier_evidence")
    )
    attributions = [
        side["decision_attribution"]
        for side in (focal, counterparty)
        if side.get("decision_attribution")
    ]
    fully_reconciled = bool(attributions) and all(row.get("reconciles") is True for row in attributions)
    return {
        "model_version": MODEL_VERSION,
        "authority": "League Intelligence view composition over governed upstream outputs",
        "source": {"path": source_path, "selector": selector},
        "identity": {
            "description": record.get("description"),
            "channel": record.get("channel"),
            "trade_direction": record.get("trade_direction"),
            "target": record.get("target"),
            "incoming": record.get("incoming") or [],
            "outgoing": record.get("outgoing") or [],
        },
        "effective_actions": simulation.get("effective_actions") or record.get("actions") or [],
        "focal_team": focal,
        "counterparty": counterparty,
        "negotiation_frontier": negotiation,
        "near_frontier_evidence": near_frontier,
        "posture_sensitivity": record.get("posture_sensitivity") or simulation.get("posture_sensitivity"),
        "source_contract": {
            "fully_reconciled_attribution": fully_reconciled,
            "partial_inspection": not fully_reconciled,
            "missing_fields_are_reported_as_unavailable": True,
            "terminal_recomputes_shared_decision_utility": False,
            "terminal_recomputes_simulation": False,
            "terminal_recomputes_negotiation_frontier": False,
        },
        "creates_independent_score": False,
        "creates_trade_value": False,
        "creates_acceptance_probability": False,
        "recommendation": False,
    }


def load_and_inspect(path: Path, selector: Optional[str] = None) -> dict[str, Any]:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(document, Mapping):
        raise ValueError("decision input must be a JSON object")
    record = select_record(document, selector)
    return inspect_decision(record, source_path=str(path), selector=selector)


def render_markdown(view: Mapping[str, Any]) -> str:
    identity = _mapping(view.get("identity"))
    focal = _mapping(view.get("focal_team"))
    counterparty = _mapping(view.get("counterparty"))
    contract = _mapping(view.get("source_contract"))

    def fmt(value: Any, digits: int = 3) -> str:
        number = _number(value)
        return "unavailable" if number is None else f"{number:+,.{digits}f}"

    def asset_name(value: Any) -> str:
        if isinstance(value, Mapping):
            return str(value.get("name") or value.get("asset_id") or value.get("player_id") or "unknown asset")
        return str(value)

    def append_channels(lines: list[str], heading: str, side: Mapping[str, Any]) -> None:
        lines.extend(["", f"## {heading}", ""])
        channels = _mapping(side.get("decision_attribution")).get("channels") or []
        if not channels:
            lines.append("Authoritative channel attribution is unavailable in the selected source record.")
            return
        lines.extend([
            "| Channel | Primitive | Weight | Contribution | Authorized |",
            "|---|---:|---:|---:|:---:|",
        ])
        for row in channels:
            lines.append(
                f"| {row.get('channel')} | {fmt(row.get('primitive_value'), 2)} | "
                f"{fmt(row.get('objective_weight'), 4)} | {fmt(row.get('numeric_contribution'), 2)} | "
                f"{'yes' if row.get('authorized_for_final_utility') else 'no'} |"
            )

    lines = [
        "# FSFFL Decision / Utility Inspector",
        "",
        identity.get("description") or "Selected governed decision",
        "",
        "This view exposes authoritative upstream calculations. It does not rescore or recommend the decision.",
        "",
        "## Utility summary",
        "",
        "| Perspective | Shared utility | Expected wins | Playoff probability | Championship probability | Attribution |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for label, side in (("Focal team", focal), ("Counterparty", counterparty)):
        delta = _mapping(side.get("simulator_delta"))
        attr = "reconciled" if side.get("attribution_reconciles") is True else "unavailable or unreconciled"
        lines.append(
            f"| {label} | {fmt(side.get('shared_decision_utility'), 2)} | "
            f"{fmt(delta.get('expected_wins'))} | {fmt(delta.get('playoff_probability'))} | "
            f"{fmt(delta.get('championship_probability'))} | {attr} |"
        )
    lines.extend(["", "## Assets and effective actions", ""])
    target = identity.get("target")
    incoming = list(identity.get("incoming") or [])
    outgoing = list(identity.get("outgoing") or [])
    lines.append(f"- Target: {asset_name(target) if target else 'unavailable'}")
    lines.append(f"- Incoming: {', '.join(asset_name(row) for row in incoming) if incoming else 'unavailable'}")
    lines.append(f"- Outgoing: {', '.join(asset_name(row) for row in outgoing) if outgoing else 'unavailable'}")
    actions = list(view.get("effective_actions") or [])
    if actions:
        lines.append("- Governed effective actions:")
        for action in actions:
            if not isinstance(action, Mapping):
                continue
            assets = list(action.get("players") or []) + list(action.get("picks") or [])
            lines.append(
                f"  - {action.get('type') or 'action'}: {action.get('from_user_id') or 'unknown'} → "
                f"{action.get('to_user_id') or 'unknown'}; assets: {', '.join(map(str, assets)) or 'none'}"
            )
    else:
        lines.append("- Governed effective actions: unavailable")

    append_channels(lines, "Focal utility channels", focal)
    append_channels(lines, "Counterparty utility channels", counterparty)

    lines.extend([
        "",
        "## Strategic context",
        "",
        "| Perspective | Competitive state | Active posture | Posture source | Current weight | Future weight |",
        "|---|---|---|---|---:|---:|",
    ])
    for label, side in (("Focal team", focal), ("Counterparty", counterparty)):
        weights = _mapping(side.get("active_objective_weights"))
        lines.append(
            f"| {label} | {side.get('competitive_state') or 'unavailable'} | "
            f"{side.get('strategic_posture') or 'unavailable'} | "
            f"{side.get('strategic_posture_source') or 'unavailable'} | "
            f"{fmt(weights.get('current'), 4)} | {fmt(weights.get('future'), 4)} |"
        )

    roster_resolution = _mapping(focal.get("roster_resolution"))
    lines.extend(["", "## Roster resolution", ""])
    if roster_resolution:
        lines.extend([
            "| Team/user | Active before | Active after | Required cuts | Selected cuts | Legal |",
            "|---|---:|---:|---:|---|:---:|",
        ])
        for user_id, raw in roster_resolution.items():
            resolution = _mapping(raw)
            cuts = [asset_name(row) for row in (resolution.get("selected_cuts") or [])]
            legal = resolution.get("roster_legal")
            lines.append(
                f"| {user_id} | {resolution.get('active_players_before_trade', 'unavailable')} | "
                f"{resolution.get('legal_active_players_after_resolution', 'unavailable')} | "
                f"{resolution.get('required_cuts', 'unavailable')} | {', '.join(cuts) or 'none'} | "
                f"{'yes' if legal is True else 'no' if legal is False else 'unavailable'} |"
            )
    else:
        lines.append("Authoritative roster-resolution evidence is unavailable in the selected source record.")
    lines.extend([
        "",
        "## Negotiation evidence",
        "",
        f"- Frontier bucket/status: {(_mapping(view.get('negotiation_frontier')).get('bucket') or _mapping(view.get('negotiation_frontier')).get('status') or 'unavailable')}",
        f"- Near-frontier evidence: {'available' if view.get('near_frontier_evidence') else 'unavailable'}",
        "- Acceptance probability: not created by this view.",
        "",
        "## Source contract",
        "",
        f"- Fully reconciled attribution: {'yes' if contract.get('fully_reconciled_attribution') else 'no'}",
        f"- Partial inspection: {'yes' if contract.get('partial_inspection') else 'no'}",
        "- Missing evidence remains unavailable; the Terminal does not reconstruct it.",
        "",
    ])
    return "\n".join(lines)
