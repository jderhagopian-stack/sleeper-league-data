"""Plain-English labels for user-facing GM 3.0 reports.

Internal JSON/model field names remain unchanged for auditability. Renderers use
these terms so reports read like fantasy-football analysis rather than model
implementation output.
"""

LABELS = {
    "state_aware_utility": "Overall Team Fit",
    "team_improvement_score": "Overall Team Fit",
    "market_dynasty_delta": "Long-Term Trade Value",
    "base_franchise_value_delta": "Value to This Team",
    "strategic_value_delta": "Value to This Team",
    "liquidity_value_delta": "Trade Flexibility",
    "break_glass_delta": "Resale Safety",
    "championship_probability": "Championship Odds",
    "playoff_probability": "Playoff Odds",
    "roster_resolution": "Roster Impact",
    "state_aware_winner": "Overall Winner",
}


def label(key: str, default: str | None = None) -> str:
    return LABELS.get(key, default or key)


def review_classification(value: str) -> str:
    return {
        "ONE_SIDED_STATE_AWARE": "CLEAR WINNER",
        "MUTUALLY_RATIONAL": "WIN-WIN TRADE",
        "MIXED": "MIXED RESULT",
    }.get(str(value or "").upper(), str(value or "TRADE REVIEW").replace("_", " "))
