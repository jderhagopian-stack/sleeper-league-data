#!/usr/bin/env python3
"""
FSFFL GM 3.0
============

Primary dynasty general-manager engine for the FSFFL.

GM 3.0 is the successor to GM 2.2. During migration it can consume the
existing GM 2.2 asset-value output, but GM 3.0 is the canonical decision
layer.

Architecture:

    Sleeper / NFL / market / historical FSFFL data
                         +
                 Simulator 1.0
                         |
                         v
                     GM 3.0
                         |
                         v
                 COMMAND CENTER

GM 3.0 NEVER writes into data/simulator/.

Core responsibilities:
- Dynasty asset valuation
- 2026 contender utility
- Market disagreement
- Hidden-gem detection
- Breakout detection
- Buy-low detection
- Bust-risk detection
- Early-not-yet detection
- Owner-specific trade intelligence
- Future-pick forecasting
- Roster-slot arbitrage
- Waiver opportunity detection
- Trade-route prioritization
- Evidence/confidence grading
- Persistent decision intelligence

Primary output:
    data/gm/command_center.json

Supporting outputs:
    data/gm/gm30_manifest.json
    data/gm/gm30_simulator_bridge.json
    data/gm/gm30_opportunity_radar.json
    data/gm/gm30_owner_profiles.json
    data/gm/gm30_pick_forecast.json
    data/gm/gm30_roster_arbitrage.json
    data/gm/gm30_trade_routes.json
"""

from __future__ import annotations

import json
import math
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


DATA = Path("data")
OUT = DATA / "gm"
OUT.mkdir(parents=True, exist_ok=True)

MODEL_VERSION = "FSFFL-GM-3.0"

USER_ID = "846634401482792960"
USER_MANAGER = "jimmygoodjob"
USER_TEAM = "Hurts So Good"

SEASON = 2026


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def load(path, default=None):
    path = Path(path)
    if not path.exists():
        return default

    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def dump(filename, payload):
    path = OUT / filename

    with path.open("w", encoding="utf-8") as f:
        json.dump(
            payload,
            f,
            indent=2,
            ensure_ascii=False,
        )


def sf(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def clamp(value, lo=0.0, hi=1.0):
    return max(lo, min(hi, value))


def norm(value):
    return re.sub(
        r"[^a-z0-9]+",
        "",
        str(value or "").lower(),
    )


def pct_rank(value, values):
    values = [
        sf(x, None)
        for x in values
    ]

    values = [
        x
        for x in values
        if x is not None
    ]

    if not values:
        return 0.5

    below = sum(x < value for x in values)
    equal = sum(x == value for x in values)

    return (
        below + 0.5 * equal
    ) / len(values)


def weighted(items):
    """
    items = [(value, weight), ...]

    Missing values are excluded rather than interpreted as negative evidence.

    Returns:
        score
        coverage
    """

    possible_weight = sum(
        weight
        for _, weight in items
        if weight > 0
    )

    present = [
        (value, weight)
        for value, weight in items
        if value is not None and weight > 0
    ]

    if not present or possible_weight <= 0:
        return 0.5, 0.0

    actual_weight = sum(
        weight
        for _, weight in present
    )

    score = sum(
        clamp(value) * weight
        for value, weight in present
    ) / actual_weight

    coverage = actual_weight / possible_weight

    return clamp(score), clamp(coverage)


def resolve_players(payload):
    if isinstance(payload, dict):
        if isinstance(payload.get("players"), list):
            return payload["players"]

    if isinstance(payload, list):
        return payload

    return []


def resolve_rows(payload):
    if isinstance(payload, list):
        return payload

    if isinstance(payload, dict):
        for key in (
            "owners",
            "teams",
            "rows",
            "profiles",
        ):
            if isinstance(payload.get(key), list):
                return payload[key]

    return []


# ---------------------------------------------------------------------------
# AGE CURVE
# ---------------------------------------------------------------------------

def age_score(position, age):
    if age is None:
        return None

    age = sf(age)

    peak = {
        "QB": (24, 32),
        "RB": (21, 26),
        "WR": (21, 28),
        "TE": (22, 29),
    }

    lo, hi = peak.get(
        str(position or "").upper(),
        (22, 29),
    )

    if lo <= age <= hi:
        return 0.78

    if age < lo:
        return clamp(
            0.52 + 0.20 * (age / max(lo, 1))
        )

    return clamp(
        0.78 - 0.075 * (age - hi),
        0.08,
        0.78,
    )


# ---------------------------------------------------------------------------
# SIMULATOR 1.0 BRIDGE
# ---------------------------------------------------------------------------

def build_simulator_bridge(standings):
    teams = (
        standings.get("teams", [])
        if isinstance(standings, dict)
        else []
    )

    expected_points = [
        sf(team.get("expected_points_for"))
        for team in teams
    ]

    expected_wins = [
        sf(team.get("expected_wins"))
        for team in teams
    ]

    output = []

    for team in teams:
        strength, _ = weighted([
            (
                pct_rank(
                    sf(team.get("expected_points_for")),
                    expected_points,
                ),
                0.34,
            ),
            (
                pct_rank(
                    sf(team.get("expected_wins")),
                    expected_wins,
                ),
                0.22,
            ),
            (
                sf(team.get("playoff_probability")),
                0.22,
            ),
            (
                sf(team.get("championship_probability")),
                0.22,
            ),
        ])

        championship = sf(
            team.get("championship_probability")
        )

        playoffs = sf(
            team.get("playoff_probability")
        )

        if championship >= 0.18:
            window = "ELITE_CONTENDER"

        elif playoffs >= 0.70:
            window = "CONTENDER"

        elif playoffs >= 0.35:
            window = "BUBBLE"

        else:
            window = "RETOOL_REBUILD"

        row = dict(team)

        row["gm30_strength_index"] = round(
            strength * 100,
            1,
        )

        row["competitive_window"] = window

        output.append(row)

    output.sort(
        key=lambda x: sf(
            x.get("championship_probability")
        ),
        reverse=True,
    )

    return {
        "model_version": MODEL_VERSION,
        "simulator_model_version":
            standings.get("model_version")
            if isinstance(standings, dict)
            else None,
        "season": SEASON,
        "teams": output,
    }


# ---------------------------------------------------------------------------
# PLAYER PROJECTION FEATURES
# ---------------------------------------------------------------------------

def build_projection_features(projections):
    output = {}

    if not isinstance(projections, dict):
        return output

    players = projections.get(
        "players",
        {},
    )

    for player_id, player in players.items():
        weeks = player.get(
            "weeks",
            {},
        )

        means = []
        deviations = []
        active_probabilities = []

        for week in weeks.values():
            if week.get("is_bye"):
                continue

            means.append(
                sf(week.get("mean"))
            )

            deviations.append(
                sf(week.get("sd"))
            )

            active_probabilities.append(
                sf(
                    week.get(
                        "active_probability"
                    ),
                    1.0,
                )
            )

        if not means:
            continue

        mean_ppg = statistics.mean(means)

        mean_sd = (
            statistics.mean(deviations)
            if deviations
            else 0.0
        )

        output[str(player_id)] = {
            "player_id": str(player_id),
            "name": player.get("name"),
            "position": player.get("position"),

            "projection_ppg":
                mean_ppg,

            "projection_sd":
                mean_sd,

            "projection_cv":
                mean_sd / max(mean_ppg, 0.1),

            "active_probability":
                statistics.mean(
                    active_probabilities
                )
                if active_probabilities
                else 1.0,

            "season_baseline_ppg":
                sf(
                    player.get(
                        "season_baseline_ppg"
                    )
                ),

            "volatility_source":
                player.get(
                    "volatility_source"
                ),
        }

    return output


# ---------------------------------------------------------------------------
# OWNER INTELLIGENCE
# ---------------------------------------------------------------------------

def build_owner_profiles(
    owner_behavior,
    simulator_bridge,
):
    sim_by_user = {
        str(team.get("user_id")): team
        for team in simulator_bridge["teams"]
    }

    output = []

    for owner in resolve_rows(
        owner_behavior
    ):
        user_id = str(
            owner.get("user_id")
        )

        trade = (
            owner.get("trade_profile")
            or {}
        )

        waiver = (
            owner.get("waiver_profile")
            or {}
        )

        rookie = (
            owner.get("rookie_draft_profile")
            or {}
        )

        simulator = sim_by_user.get(
            user_id,
            {},
        )

        total_trades = sf(
            trade.get("total_trades")
        )

        recent_trades = sf(
            trade.get(
                "recent_trades_2025_2026"
            )
        )

        multi_asset = sf(
            trade.get("multi_asset_rate")
        )

        initiation = sf(
            trade.get("initiation_rate")
        )

        first_net = (
            sf(trade.get("firsts_acquired"))
            -
            sf(trade.get("firsts_sent"))
        )

        positions_acquired = (
            trade.get(
                "player_positions_acquired"
            )
            or {}
        )

        positions_sent = (
            trade.get(
                "player_positions_sent"
            )
            or {}
        )

        player_volume = (
            sum(
                sf(x)
                for x in positions_acquired.values()
            )
            +
            sum(
                sf(x)
                for x in positions_sent.values()
            )
        )

        activity = clamp(
            total_trades / 50.0
        )

        negotiability, _ = weighted([
            (
                activity,
                0.36,
            ),
            (
                clamp(
                    recent_trades / 20.0
                ),
                0.24,
            ),
            (
                multi_asset,
                0.20,
            ),
            (
                initiation,
                0.20,
            ),
        ])

        pick_appetite = clamp(
            0.50 + 0.04 * first_net
        )

        output.append({
            "user_id":
                user_id,

            "manager":
                owner.get("manager"),

            "team_name":
                owner.get("team_name"),

            "competitive_window":
                simulator.get(
                    "competitive_window"
                ),

            "playoff_probability":
                simulator.get(
                    "playoff_probability"
                ),

            "championship_probability":
                simulator.get(
                    "championship_probability"
                ),

            "trade_activity_score":
                round(
                    activity * 100,
                    1,
                ),

            "negotiability_score":
                round(
                    negotiability * 100,
                    1,
                ),

            "pick_appetite_score":
                round(
                    pick_appetite * 100,
                    1,
                ),

            "player_liquidity_score":
                round(
                    clamp(
                        player_volume / 80.0
                    ) * 100,
                    1,
                ),

            "multi_asset_preference":
                round(
                    multi_asset,
                    3,
                ),

            "initiation_rate":
                round(
                    initiation,
                    3,
                ),

            "top_trade_partners":
                trade.get(
                    "top_trade_partners"
                )
                or [],

            "position_acquisition_history":
                positions_acquired,

            "position_sale_history":
                positions_sent,

            "rookie_draft_profile":
                rookie,

            "waiver_profile":
                waiver,

            "evidence_quality":
                "HARD_HISTORY_PLUS_SIMULATOR",
        })

    return output


# ---------------------------------------------------------------------------
# FUTURE PICK FORECAST
# ---------------------------------------------------------------------------

def build_pick_forecast(
    simulator_bridge,
    roster_fragility,
):
    fragility_rows = resolve_rows(
        roster_fragility
    )

    fragility_by_user = {
        str(row.get("user_id")): row
        for row in fragility_rows
    }

    wins = [
        sf(team.get("expected_wins"))
        for team in simulator_bridge["teams"]
    ]

    points = [
        sf(team.get("expected_points_for"))
        for team in simulator_bridge["teams"]
    ]

    output = []

    for team in simulator_bridge["teams"]:
        user_id = str(
            team.get("user_id")
        )

        fragility_row = (
            fragility_by_user.get(
                user_id,
                {},
            )
        )

        fragility = sf(
            fragility_row.get(
                "fragility_score"
            ),
            sf(
                fragility_row.get(
                    "roster_fragility"
                ),
                0.5,
            ),
        )

        if fragility > 1:
            fragility /= 100.0

        strength, _ = weighted([
            (
                sf(
                    team.get(
                        "championship_probability"
                    )
                ),
                0.28,
            ),
            (
                sf(
                    team.get(
                        "playoff_probability"
                    )
                ),
                0.24,
            ),
            (
                pct_rank(
                    sf(
                        team.get(
                            "expected_wins"
                        )
                    ),
                    wins,
                ),
                0.24,
            ),
            (
                pct_rank(
                    sf(
                        team.get(
                            "expected_points_for"
                        )
                    ),
                    points,
                ),
                0.14,
            ),
            (
                1 - clamp(fragility),
                0.10,
            ),
        ])

        slot_2027 = (
            1 + 11 * strength
        )

        slot_2028 = (
            1
            +
            11
            *
            (
                0.72 * strength
                +
                0.28 * 0.50
            )
        )

        slot_2029 = (
            1
            +
            11
            *
            (
                0.56 * strength
                +
                0.44 * 0.50
            )
        )

        if slot_2027 <= 4.5:
            band = "EARLY"

        elif slot_2027 <= 8.5:
            band = "MID"

        else:
            band = "LATE"

        output.append({
            "user_id":
                user_id,

            "manager":
                team.get("manager"),

            "team_name":
                team.get("team_name"),

            "2027_first_expected_slot":
                round(slot_2027, 1),

            "2027_first_band":
                band,

            "2028_first_expected_slot":
                round(slot_2028, 1),

            "2029_first_expected_slot":
                round(slot_2029, 1),

            "current_strength_index":
                team.get(
                    "gm30_strength_index"
                ),

            "fragility_input":
                round(
                    fragility,
                    3,
                ),

            "confidence":
                0.76
                if user_id
                in fragility_by_user
                else 0.67,

            "warning":
                (
                    "Future pick location is "
                    "a probability distribution, "
                    "not a deterministic slot."
                ),
        })

    output.sort(
        key=lambda x:
            x["2027_first_expected_slot"]
    )

    return output


# ---------------------------------------------------------------------------
# PLAYER OPPORTUNITY RADAR
# ---------------------------------------------------------------------------

def build_opportunity_radar(
    asset_values,
    projections,
    owner_profiles,
):
    projection_features = (
        build_projection_features(
            projections
        )
    )

    owner_by_user = {
        str(owner.get("user_id")): owner
        for owner in owner_profiles
    }

    players = resolve_players(
        asset_values
    )

    market_values = [
        sf(player.get("market_dynasty"))
        for player in players
        if sf(
            player.get("market_dynasty")
        ) > 0
    ]

    projection_ppgs = [
        row["projection_ppg"]
        for row
        in projection_features.values()
    ]

    output = []

    for player in players:
        player_id = str(
            player.get("player_id")
        )

        projection = (
            projection_features.get(
                player_id,
                {},
            )
        )

        market = sf(
            player.get("market_dynasty")
        )

        fsffl = sf(
            player.get("fsffl_value")
        )

        if market:
            disagreement = clamp(
                0.50
                +
                (
                    (fsffl - market)
                    /
                    max(market, 1000)
                )
                * 0.90
            )
        else:
            disagreement = 0.50

        trend = sf(
            player.get("trend_30_day")
        )

        momentum = clamp(
            0.50
            +
            max(
                -700,
                min(
                    700,
                    trend,
                ),
            )
            / 1600
        )

        if projection:
            projection_strength = pct_rank(
                projection.get(
                    "projection_ppg",
                    0,
                ),
                projection_ppgs,
            )

            cv = projection.get(
                "projection_cv",
                0,
            )

            upside_asymmetry = clamp(
                0.70
                -
                0.35
                *
                abs(
                    cv - 0.65
                )
            )

        else:
            projection_strength = None
            upside_asymmetry = None

        football = (
            player.get(
                "football_intelligence"
            )
            or {}
        )

        usage = (
            football.get(
                "usage_and_snaps"
            )
            or {}
        )

        manual = (
            football.get(
                "manual_news_signal"
            )
            or {}
        )

        role_values = []

        for value in (
            usage.get("usage_signal"),
            usage.get("snap_signal"),
            manual.get("signal"),
        ):
            if value is None:
                continue

            value = sf(value)

            if value < 0:
                value = (
                    value + 1
                ) / 2

            role_values.append(
                clamp(value)
            )

        role_news = (
            statistics.mean(
                role_values
            )
            if role_values
            else None
        )

        age_component = age_score(
            player.get("position"),
            player.get("age"),
        )

        owner = owner_by_user.get(
            str(
                player.get(
                    "current_owner_user_id"
                )
            ),
            {},
        )

        accessibility = clamp(
            sf(
                owner.get(
                    "negotiability_score"
                ),
                50,
            )
            / 100
        )

        composite, coverage = weighted([
            (
                disagreement,
                0.22,
            ),
            (
                role_news,
                0.28,
            ),
            (
                projection_strength,
                0.18,
            ),
            (
                upside_asymmetry,
                0.08,
            ),
            (
                momentum,
                0.08,
            ),
            (
                age_component,
                0.10,
            ),
            (
                accessibility,
                0.06,
            ),
        ])

        cheapness = (
            1
            -
            pct_rank(
                market,
                market_values,
            )
            if market_values
            else 0.50
        )

        negative_momentum = clamp(
            1.0 - momentum
        )

        hidden_gem, _ = weighted([
            (
                composite,
                0.55,
            ),
            (
                cheapness,
                0.25,
            ),
            (
                disagreement,
                0.20,
            ),
        ])

        breakout, _ = weighted([
            (
                role_news,
                0.34,
            ),
            (
                projection_strength,
                0.28,
            ),
            (
                age_component,
                0.14,
            ),
            (
                momentum,
                0.12,
            ),
            (
                disagreement,
                0.12,
            ),
        ])

        buy_low, _ = weighted([
            (
                negative_momentum,
                0.34,
            ),
            (
                disagreement,
                0.28,
            ),
            (
                projection_strength,
                0.18,
            ),
            (
                role_news,
                0.20,
            ),
        ])

        bust_risk, _ = weighted([
            (
                1 - disagreement,
                0.22,
            ),
            (
                None
                if role_news is None
                else 1 - role_news,
                0.26,
            ),
            (
                None
                if projection_strength
                is None
                else
                1 - projection_strength,
                0.20,
            ),
            (
                None
                if age_component is None
                else
                1 - age_component,
                0.16,
            ),
            (
                1 - momentum,
                0.16,
            ),
        ])

        young = (
            (
                player.get("position") == "QB"
                and
                sf(
                    player.get("age"),
                    99,
                ) <= 25
            )
            or
            (
                player.get("position") == "RB"
                and
                sf(
                    player.get("age"),
                    99,
                ) <= 23
            )
            or
            (
                player.get("position") == "WR"
                and
                sf(
                    player.get("age"),
                    99,
                ) <= 24
            )
            or
            (
                player.get("position") == "TE"
                and
                sf(
                    player.get("age"),
                    99,
                ) <= 25
            )
        )

        early_not_yet, _ = weighted([
            (
                disagreement,
                0.30,
            ),
            (
                role_news,
                0.24,
            ),
            (
                age_component,
                0.24,
            ),
            (
                None
                if projection_strength
                is None
                else
                1 - projection_strength,
                0.22,
            ),
        ])

        categories = []

        candidates = [
            (
                "HIDDEN_GEM",
                hidden_gem,
                0.72,
            ),
            (
                "BREAKOUT_WATCH",
                breakout,
                0.70,
            ),
            (
                "BUY_LOW",
                buy_low,
                0.68,
            ),
            (
                "BUST_RISK",
                bust_risk,
                0.72,
            ),
        ]

        for label, score, threshold in candidates:
            if score >= threshold:
                categories.append({
                    "type": label,
                    "score": round(
                        score * 100,
                        1,
                    ),
                })

        if (
            young
            and
            projection_strength is not None
            and
            projection_strength < 0.58
            and
            early_not_yet >= 0.67
        ):
            categories.append({
                "type":
                    "EARLY_NOT_YET",

                "score":
                    round(
                        early_not_yet * 100,
                        1,
                    ),
            })

        categories.sort(
            key=lambda x: x["score"],
            reverse=True,
        )

        confidence = clamp(
            0.34
            +
            0.54 * coverage
            +
            0.06
            * (
                1
                if projection
                else 0
            )
            +
            0.06
            * (
                1
                if role_values
                else 0
            )
        )

        if (
            coverage >= 0.82
            and projection
            and role_values
        ):
            evidence_grade = "A"

        elif coverage >= 0.62:
            evidence_grade = "B"

        elif coverage >= 0.45:
            evidence_grade = "C"

        else:
            evidence_grade = "D"

        gm30_value = (
            fsffl
            *
            (
                1
                +
                max(
                    -0.12,
                    min(
                        0.12,
                        (
                            composite - 0.50
                        )
                        * 0.24,
                    ),
                )
            )
            if fsffl
            else 0.0
        )

        output.append({
            "player_id":
                player_id,

            "name":
                player.get("name"),

            "position":
                player.get("position"),

            "nfl_team":
                player.get("nfl_team"),

            "age":
                player.get("age"),

            "owner_user_id":
                player.get(
                    "current_owner_user_id"
                ),

            "owner_manager":
                player.get(
                    "current_owner_manager"
                ),

            "owner_team":
                player.get(
                    "current_owner_team"
                ),

            "market_dynasty":
                market,

            "fsffl_value":
                fsffl,

            "gm30_value":
                round(
                    gm30_value,
                    1,
                ),

            "gm30_vs_market_pct":
                (
                    round(
                        100
                        *
                        (
                            gm30_value
                            / market
                            -
                            1
                        ),
                        1,
                    )
                    if market
                    else None
                ),

            "trend_30_day":
                trend,

            "projection_ppg":
                (
                    round(
                        projection.get(
                            "projection_ppg",
                            0,
                        ),
                        2,
                    )
                    if projection
                    else None
                ),

            "signal_score":
                round(
                    composite * 100,
                    1,
                ),

            "confidence":
                round(
                    confidence,
                    3,
                ),

            "coverage":
                round(
                    coverage,
                    3,
                ),

            "evidence_grade":
                evidence_grade,

            "categories":
                categories,

            "components": {
                "market_disagreement":
                    round(
                        disagreement,
                        3,
                    ),

                "role_usage_news":
                    (
                        round(
                            role_news,
                            3,
                        )
                        if role_news
                        is not None
                        else None
                    ),

                "projection_strength":
                    (
                        round(
                            projection_strength,
                            3,
                        )
                        if projection_strength
                        is not None
                        else None
                    ),

                "upside_asymmetry":
                    (
                        round(
                            upside_asymmetry,
                            3,
                        )
                        if upside_asymmetry
                        is not None
                        else None
                    ),

                "market_momentum":
                    round(
                        momentum,
                        3,
                    ),

                "age_curve":
                    (
                        round(
                            age_component,
                            3,
                        )
                        if age_component
                        is not None
                        else None
                    ),

                "owner_accessibility":
                    round(
                        accessibility,
                        3,
                    ),
            },
        })

    output.sort(
        key=lambda row:
            max(
                [
                    category["score"]
                    for category
                    in row["categories"]
                ]
                or
                [
                    row["signal_score"]
                ]
            ),
        reverse=True,
    )

    return output


# ---------------------------------------------------------------------------
# ROSTER / WAIVER ARBITRAGE
# ---------------------------------------------------------------------------

def build_roster_arbitrage(
    asset_values,
    projections,
    rosters,
    players,
):
    values = {
        str(row.get("player_id")): row
        for row in resolve_players(
            asset_values
        )
    }

    projection_features = (
        build_projection_features(
            projections
        )
    )

    player_meta = (
        players
        if isinstance(players, dict)
        else {}
    )

    rostered = set()

    team_bottom_assets = []

    for roster in (
        rosters
        if isinstance(rosters, list)
        else []
    ):
        roster_id = str(
            roster.get("roster_id")
        )

        player_ids = [
            str(x)
            for x in (
                roster.get("players")
                or []
            )
        ]

        rostered.update(
            player_ids
        )

        ranked = []

        for player_id in player_ids:
            value = values.get(
                player_id,
                {},
            )

            projection = (
                projection_features.get(
                    player_id,
                    {},
                )
            )

            utility = (
                0.55
                *
                sf(
                    value.get(
                        "fsffl_value"
                    )
                )
                / 10000
                +
                0.45
                *
                clamp(
                    sf(
                        projection.get(
                            "projection_ppg"
                        )
                    )
                    / 24
                )
            )

            ranked.append(
                (
                    utility,
                    player_id,
                    value,
                    projection,
                )
            )

        ranked.sort()

        bottom = []

        for (
            utility,
            player_id,
            value,
            projection,
        ) in ranked[:3]:

            metadata = (
                player_meta.get(
                    player_id,
                    {}
                )
            )

            bottom.append({
                "player_id":
                    player_id,

                "name":
                    (
                        value.get("name")
                        or
                        metadata.get(
                            "full_name"
                        )
                    ),

                "position":
                    (
                        value.get("position")
                        or
                        metadata.get(
                            "position"
                        )
                    ),

                "fsffl_value":
                    value.get(
                        "fsffl_value"
                    ),

                "projection_ppg":
                    (
                        round(
                            projection.get(
                                "projection_ppg",
                                0,
                            ),
                            2,
                        )
                        if projection
                        else None
                    ),

                "roster_slot_utility":
                    round(
                        utility,
                        4,
                    ),
            })

        team_bottom_assets.append({
            "roster_id":
                roster_id,

            "bottom_assets":
                bottom,
        })

    waiver_candidates = []

    for (
        player_id,
        projection,
    ) in projection_features.items():

        if player_id in rostered:
            continue

        metadata = (
            player_meta.get(
                player_id,
                {}
            )
        )

        waiver_score = (
            clamp(
                projection.get(
                    "projection_ppg",
                    0,
                )
                / 15
            )
            *
            (
                0.85
                +
                0.15
                *
                clamp(
                    projection.get(
                        "active_probability",
                        1,
                    )
                )
            )
        )

        waiver_candidates.append({
            "player_id":
                player_id,

            "name":
                (
                    projection.get("name")
                    or
                    metadata.get(
                        "full_name"
                    )
                ),

            "position":
                (
                    projection.get(
                        "position"
                    )
                    or
                    metadata.get(
                        "position"
                    )
                ),

            "projection_ppg":
                round(
                    projection.get(
                        "projection_ppg",
                        0,
                    ),
                    2,
                ),

            "projection_cv":
                round(
                    projection.get(
                        "projection_cv",
                        0,
                    ),
                    3,
                ),

            "waiver_score":
                round(
                    waiver_score * 100,
                    1,
                ),
        })

    waiver_candidates.sort(
        key=lambda x:
            x["waiver_score"],
        reverse=True,
    )

    return {
        "team_bottom_assets":
            team_bottom_assets,

        "top_unrostered_candidates":
            waiver_candidates[:50],
    }


# ---------------------------------------------------------------------------
# TRADE ROUTING
# ---------------------------------------------------------------------------

def build_trade_routes(
    radar,
    owner_profiles,
    simulator_bridge,
):
    owners_by_user = {
        str(owner.get("user_id")): owner
        for owner in owner_profiles
    }

    simulator_by_user = {
        str(team.get("user_id")): team
        for team in simulator_bridge["teams"]
    }

    output = []

    for player in radar:
        owner_id = str(
            player.get(
                "owner_user_id"
            )
            or ""
        )

        if (
            not owner_id
            or
            owner_id == USER_ID
        ):
            continue

        if not player.get("categories"):
            continue

        owner = owners_by_user.get(
            owner_id,
            {},
        )

        simulator = (
            simulator_by_user.get(
                owner_id,
                {},
            )
        )

        primary = max(
            player["categories"],
            key=lambda x: x["score"],
        )

        desirability = (
            primary["score"] / 100
        )

        counterparty_acceptance, _ = weighted([
            (
                sf(
                    owner.get(
                        "negotiability_score"
                    ),
                    50,
                )
                / 100,
                0.42,
            ),
            (
                sf(
                    owner.get(
                        "player_liquidity_score"
                    ),
                    50,
                )
