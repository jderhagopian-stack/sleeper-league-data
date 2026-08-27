"""Plain-English language standard for all user-facing FSFFL reports.

The model's internal JSON field names remain unchanged for auditability.
Renderers translate those fields into fantasy-football language a league
manager can understand without knowing the implementation.

Rules:
- lead with the football decision, not the model architecture;
- use familiar fantasy terms before technical terms;
- explain unavoidable model concepts in one short sentence;
- never expose internal score names as if they were self-explanatory;
- keep raw model/version details in footers or methodology notes;
- do not change the underlying calculation while simplifying the wording.
"""

LABELS = {
    "state_aware_utility": "Overall Team Fit",
    "team_improvement_score": "Overall Team Fit",
    "market_dynasty_delta": "Long-Term Trade Value",
    "base_franchise_value_delta": "Value to This Team",
    "base_franchise_delta": "Value to This Team",
    "strategic_value_delta": "Overall Franchise Impact",
    "liquidity_value_delta": "Future Trade Flexibility",
    "break_glass_delta": "Resale Safety",
    "break_glass_value": "Minimum Price to Move",
    "liquidity_score": "Ease of Trading",
    "market_redraft_delta": "2026 Playing Value",
    "championship_probability": "Championship Odds",
    "playoff_probability": "Playoff Odds",
    "bye_probability": "First-Round Bye Odds",
    "division_probability": "Division-Win Odds",
    "expected_points_for": "Expected Points",
    "expected_wins": "Expected Wins",
    "roster_resolution": "Roster Impact",
    "state_aware_winner": "Overall Winner",
    "contender_score": "How Ready This Team Is to Win Now",
    "dynasty_roster_score": "Long-Term Roster Strength",
    "objective_weights": "What the Model Prioritizes for This Team",
    "roster_interaction_value_delta": "Roster Fit / Insurance Value",
    "competitive_externality": "Effect on the Rest of the League",
    "heuristic_acceptance_fit": "How Well the Offer Fits the Other Manager",
    "post_sim_score": "Overall Deal Score",
}

EXPLANATIONS = {
    "Long-Term Trade Value": "How much dynasty-market value the roster gains or loses.",
    "Value to This Team": "What those assets are worth specifically to this roster and competitive window.",
    "Overall Franchise Impact": "The model's bottom-line blend of winning now, future value, roster fit and flexibility.",
    "Future Trade Flexibility": "How much easier or harder the roster will be to reshape later.",
    "Resale Safety": "How much value is likely to remain available if plans change and the asset needs to be moved.",
    "Minimum Price to Move": "The model's estimate of what it should take to justify moving an especially important asset.",
    "Ease of Trading": "How readily the asset can be turned into useful value in another deal.",
    "Roster Fit / Insurance Value": "Extra value created or lost because this player interacts with players already on the roster, such as covering the same backfield.",
    "Overall Team Fit": "How well the move fits this team's current goal after accounting for lineup impact, future value and roster costs.",
    "Effect on the Rest of the League": "Whether the move also strengthens rivals enough to offset some of the benefit to this team.",
}


def label(key: str, default: str | None = None) -> str:
    return LABELS.get(key, default or key)


def explanation(key_or_label: str, default: str = "") -> str:
    human = LABELS.get(key_or_label, key_or_label)
    return EXPLANATIONS.get(human, default)


def review_classification(value: str) -> str:
    return {
        "ONE_SIDED_STATE_AWARE": "CLEAR WINNER",
        "MUTUALLY_RATIONAL": "WIN-WIN TRADE",
        "MIXED": "MIXED RESULT",
    }.get(str(value or "").upper(), str(value or "TRADE REVIEW").replace("_", " "))


def team_state(value: str) -> str:
    return {
        "elite_contender": "Elite Contender",
        "contender": "Contender",
        "fringe_contender": "Fringe Contender",
        "retool": "Retooling",
        "rebuild": "Rebuilding",
        "unknown": "Not Yet Determined",
    }.get(str(value or "").lower(), str(value or "Not Yet Determined").replace("_", " ").title())


def acceptance_fit(value: str) -> str:
    return {
        "HIGH": "Strong chance this type of offer interests the other manager",
        "MEDIUM": "Reasonable chance this type of offer interests the other manager",
        "LOW": "This structure is a weak fit for the other manager",
        "VERY_LOW": "This structure is very unlikely to fit the other manager",
    }.get(str(value or "").upper(), "The model does not have enough evidence to judge the other manager's interest")


def action(value: str) -> str:
    return {
        "ACCEPT_NOW": "ACCEPT",
        "SHOP_BEFORE_ACCEPTING": "SHOP AROUND FIRST",
        "COUNTER_CURRENT_OFFEROR": "COUNTER",
        "DECLINE": "DECLINE",
        "HOLD": "HOLD",
        "TRADE": "MAKE THE TRADE",
        "WAIVER": "ADD FROM WAIVERS",
    }.get(str(value or "").upper(), str(value or "REVIEW").replace("_", " "))


# Technical phrases that should not appear in the body of user-facing reports.
# They may still appear in tiny model-version footers for auditing.
BODY_JARGON = (
    "state-aware",
    "break-glass",
    "liquidity value",
    "objective weights",
    "competitive externality",
    "heuristic acceptance",
    "post-sim",
    "roster interaction value",
    "adaptive confirmation",
    "simulation multiverse",
)
