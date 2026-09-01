#!/usr/bin/env python3
"""Governance contract for FSFFL League Intelligence / Analytics Terminal views."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

MODEL_VERSION = "FSFFL-League-Intelligence-Contract-1.0"

ALLOWED_OPERATIONS = frozenset({
    "filter",
    "group",
    "sort_single_governed_field",
    "monotonic_display_transform",
    "comparable_value_delta",
    "governed_join",
    "descriptive_explanation",
})

FORBIDDEN_OPERATIONS = frozenset({
    "new_valuation_model",
    "new_cross_channel_utility",
    "new_trade_acceptance_probability",
    "new_contender_score",
    "new_simulation_model",
    "hidden_weighted_blend",
    "presentation_composite_rerank",
    "execution_recommendation_from_discovery_signal",
})

AUTHORITIES = {
    "player_value": "Canonical valuation sources",
    "team_specific_utility": "GM3",
    "portfolio_utility": "GM3 Team Improvement",
    "competitive_outcomes": "Simulator",
    "trade_execution_review": "Trade Decision",
    "behavioral_evidence": "Behavioral Intelligence",
    "specialist_player_evidence": "Draft / Breakout-Sleeper Intelligence",
    "view_composition": "League Intelligence",
}


@dataclass(frozen=True)
class ViewContract:
    view_id: str
    version: str
    purpose: str
    upstream_authorities: tuple[str, ...]
    governed_fields: tuple[str, ...]
    presentation_transforms: tuple[str, ...] = field(default_factory=tuple)
    allowed_operations: tuple[str, ...] = field(default_factory=tuple)
    forbidden_operations: tuple[str, ...] = field(default_factory=lambda: tuple(sorted(FORBIDDEN_OPERATIONS)))
    discovery_safe: bool = True
    presentation_safe: bool = True

    def validate(self) -> None:
        if not self.view_id.strip():
            raise ValueError("view_id is required")
        if not self.version.strip():
            raise ValueError("version is required")
        if not self.purpose.strip():
            raise ValueError("purpose is required")
        if not self.upstream_authorities:
            raise ValueError("at least one upstream authority is required")
        if not self.governed_fields:
            raise ValueError("at least one governed field is required")

        unknown_allowed = set(self.allowed_operations) - ALLOWED_OPERATIONS
        if unknown_allowed:
            raise ValueError(f"unknown or ungoverned allowed operations: {sorted(unknown_allowed)}")

        missing_forbidden = FORBIDDEN_OPERATIONS - set(self.forbidden_operations)
        if missing_forbidden:
            raise ValueError(f"view contract weakened forbidden operations: {sorted(missing_forbidden)}")

        if "presentation_composite_rerank" not in self.forbidden_operations:
            raise ValueError("presentation-only fields may never become ranking authority")


def validate_contracts(contracts: Iterable[ViewContract]) -> None:
    seen: set[tuple[str, str]] = set()
    for contract in contracts:
        contract.validate()
        key = (contract.view_id, contract.version)
        if key in seen:
            raise ValueError(f"duplicate view contract: {key}")
        seen.add(key)


def first_release_contracts() -> tuple[ViewContract, ...]:
    contracts = (
        ViewContract(
            view_id="player-value-rankings",
            version="1.0",
            purpose="Expose model, market, and governed team-specific player intelligence without creating a new value score.",
            upstream_authorities=("Canonical valuation sources", "GM3"),
            governed_fields=(
                "canonical_player_id",
                "position",
                "fsffl_model_value",
                "market_value",
                "gm3_team_specific_context",
                "owner_team_id",
            ),
            presentation_transforms=("rank", "percentile", "model_minus_market_delta"),
            allowed_operations=(
                "filter",
                "group",
                "sort_single_governed_field",
                "monotonic_display_transform",
                "comparable_value_delta",
                "governed_join",
                "descriptive_explanation",
            ),
        ),
        ViewContract(
            view_id="team-strength-heat-map",
            version="1.0",
            purpose="Expose governed positional and draft-capital strengths and deficits across all franchises.",
            upstream_authorities=("GM3", "Simulator"),
            governed_fields=(
                "team_id",
                "position",
                "starter_strength",
                "depth_strength",
                "team_need",
                "draft_capital_strength",
            ),
            presentation_transforms=("league_rank", "league_percentile", "z_score"),
            allowed_operations=(
                "filter",
                "group",
                "sort_single_governed_field",
                "monotonic_display_transform",
                "governed_join",
                "descriptive_explanation",
            ),
        ),
        ViewContract(
            view_id="value-disagreement-trade-partner-map",
            version="1.0",
            purpose="Expose transparent value disagreements and complementary roster shapes for investigation only.",
            upstream_authorities=("Canonical valuation sources", "GM3", "Behavioral Intelligence"),
            governed_fields=(
                "asset_id",
                "owner_team_id",
                "fsffl_model_value",
                "market_value",
                "gm3_retention_context",
                "team_need",
                "behavioral_evidence",
            ),
            presentation_transforms=("model_minus_market_delta", "surplus_need_alignment"),
            allowed_operations=(
                "filter",
                "group",
                "sort_single_governed_field",
                "comparable_value_delta",
                "governed_join",
                "descriptive_explanation",
            ),
        ),
    )
    validate_contracts(contracts)
    return contracts


if __name__ == "__main__":
    for c in first_release_contracts():
        print(f"{c.view_id}@{c.version}: governed")
