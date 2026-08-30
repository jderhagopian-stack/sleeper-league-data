"""Shared FSFFL Reporting module.

Reports/Publications consume authoritative Core/Application/Analytics outputs
through this facade. The module owns presentation intelligence - terminology,
contextual narrative and evidence-backed visuals - but never rescoring or
decision authority.
"""
from report_language import (
    label, explanation, review_classification, team_state, acceptance_fit,
    action, magnitude_word, probability_change, value_change,
    league_rank_context, artificial_ellipsis_hits, BODY_JARGON,
)
from report_context import team_context, analyst_roster_context, competitive_context, roster_change_context, canonical_simulator_team
from report_visuals import (
    position_need_chart,
    position_need_change_chart,
    probability_change_chart,
    league_title_odds_chart,
)

__all__ = [
    "label","explanation","review_classification","team_state","acceptance_fit",
    "action","magnitude_word","probability_change","value_change",
    "league_rank_context","artificial_ellipsis_hits","BODY_JARGON",
    "team_context","analyst_roster_context","competitive_context","roster_change_context","canonical_simulator_team","position_need_chart","position_need_change_chart",
    "probability_change_chart","league_title_odds_chart",
]
