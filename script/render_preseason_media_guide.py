#!/usr/bin/env python3
"""Render the FSFFL 2026 Preseason Media Guide.

Presentation-only league publication. It consumes governed Simulator,
League Intelligence, roster, player, and preseason projection outputs.
It does not create valuation, decision, recommendation, or acceptance authority.

The only report-owned roster transform is a transparent projection-optimized
legal lineup used to organize each team's roster page. It is labeled as a
display construct and is not exposed as a new model score or recommendation.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from reportlab.graphics.charts.barcharts import HorizontalBarChart
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from fsffl_report_style import (
    BLACK,
    BLUE,
    GOLD,
    GRAY,
    GREEN,
    LIGHT_BLUE,
    LIGHT_GOLD,
    LIGHT_GRAY,
    LIGHT_GREEN,
    LIGHT_RED,
    MID_GRAY,
    NAVY,
    RED,
    WHITE,
    P,
    clean,
    safe_float,
    styles,
)


MODEL_VERSION = "FSFFL-Preseason-Media-Guide-1.0"
PAGE_SIZE = landscape(letter)
PAGE_WIDTH, PAGE_HEIGHT = PAGE_SIZE
CONTENT_WIDTH = PAGE_WIDTH - 0.84 * inch

DEFAULT_ROSTERS = Path("data/rosters.json")
DEFAULT_USERS = Path("data/users.json")
DEFAULT_PLAYERS = Path("data/players.json")
DEFAULT_PROJECTIONS = Path("data/simulator/2026/sources/preseason_fsffl_points.json")
DEFAULT_SIMULATOR = Path("data/gm/league/simulator_context.json")
DEFAULT_LEAGUE_INTELLIGENCE = Path("data/league_intelligence/terminal.json")

STARTER_ORDER = {"QB": 0, "RB": 1, "WR": 2, "TE": 3, "FLEX": 4, "SUPERFLEX": 5}
ROLE_ORDER = {**STARTER_ORDER, "BENCH": 6, "TAXI": 7, "RESERVE": 8}


def load(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def pct(value: Any, digits: int = 1) -> str:
    return f"{safe_float(value) * 100:.{digits}f}%"


def _player_name(player: Mapping[str, Any], projection: Mapping[str, Any], player_id: str) -> str:
    return clean(player.get("full_name") or projection.get("player_name") or player_id)


def _projection_stats(position: str, stats: Mapping[str, Any] | None) -> str:
    if not isinstance(stats, Mapping):
        return "No supported projection"

    def f(key: str, digits: int = 1) -> str:
        value = stats.get(key)
        if value is None:
            return "-"
        number = safe_float(value)
        if abs(number - round(number)) < 1e-8:
            return str(int(round(number)))
        return f"{number:.{digits}f}"

    if position == "QB":
        return (
            f"Pass {f('pass_yd')} yd / {f('pass_td')} TD / {f('pass_int')} INT; "
            f"Rush {f('rush_yd')} yd / {f('rush_td')} TD"
        )
    if position == "RB":
        return (
            f"Rush {f('rush_att')} att / {f('rush_yd')} yd / {f('rush_td')} TD; "
            f"Rec {f('rec')} / {f('rec_yd')} yd / {f('rec_td')} TD"
        )
    suffix = ""
    if safe_float(stats.get("rush_yd")) > 0 or safe_float(stats.get("rush_td")) > 0:
        suffix = f"; Rush {f('rush_yd')} yd / {f('rush_td')} TD"
    return f"Rec {f('rec')} / {f('rec_yd')} yd / {f('rec_td')} TD{suffix}"


def build_rosters(
    rosters: list[Mapping[str, Any]],
    users: list[Mapping[str, Any]],
    players: Mapping[str, Any],
    projections: Mapping[str, Any],
) -> list[dict[str, Any]]:
    user_map = {
        str(row.get("user_id")): {
            "team_name": clean((row.get("metadata") or {}).get("team_name") or row.get("display_name")),
            "manager": clean(row.get("display_name")),
        }
        for row in users
    }
    projection_players = projections.get("players") or {}
    result: list[dict[str, Any]] = []

    for roster in rosters:
        owner = user_map.get(str(roster.get("owner_id")), {})
        taxi = {str(x) for x in (roster.get("taxi") or [])}
        reserve = {str(x) for x in (roster.get("reserve") or [])}
        rows: list[dict[str, Any]] = []
        for raw_id in roster.get("players") or []:
            player_id = str(raw_id)
            player = players.get(player_id) or {}
            projection = projection_players.get(player_id) or {}
            position = clean(player.get("position") or projection.get("position") or "-")
            status = "ACTIVE"
            if player_id in taxi:
                status = "TAXI"
            elif player_id in reserve:
                status = "RESERVE"
            rows.append({
                "player_id": player_id,
                "name": _player_name(player, projection, player_id),
                "age": player.get("age"),
                "position": position,
                "nfl_team": clean(player.get("team") or projection.get("team") or "-"),
                "injury_status": clean(player.get("injury_status") or ""),
                "roster_status": status,
                "projected_points": projection.get("fsffl_projected_points"),
                "projected_ppg": projection.get("fsffl_projected_ppg"),
                "projected_stats": _projection_stats(position, projection.get("projected_stats")),
                "projection_source": projection.get("source"),
            })
        result.append({
            "roster_id": int(roster.get("roster_id")),
            "user_id": str(roster.get("owner_id")),
            "team_name": clean(owner.get("team_name") or f"Roster {roster.get('roster_id')}"),
            "manager": clean(owner.get("manager") or roster.get("owner_id")),
            "division": int((roster.get("settings") or {}).get("division") or 0),
            "players": rows,
        })
    return result


def projection_optimized_lineup(team: Mapping[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Transparent display transform under the league's legal lineup rules."""
    available = [
        dict(row) for row in team.get("players") or []
        if row.get("roster_status") == "ACTIVE" and row.get("projected_ppg") is not None
    ]
    selected: list[dict[str, Any]] = []
    lineup: list[tuple[str, dict[str, Any]]] = []

    def best(position: str, count: int, slot: str) -> None:
        pool = sorted(
            [row for row in available if row.get("position") == position and row not in selected],
            key=lambda row: safe_float(row.get("projected_ppg")),
            reverse=True,
        )
        for row in pool[:count]:
            selected.append(row)
            lineup.append((slot, row))

    best("QB", 1, "QB")
    best("RB", 2, "RB")
    best("WR", 3, "WR")
    best("TE", 1, "TE")

    flex = sorted(
        [
            row for row in available
            if row not in selected and row.get("position") in {"RB", "WR", "TE"}
        ],
        key=lambda row: safe_float(row.get("projected_ppg")),
        reverse=True,
    )
    if flex:
        selected.append(flex[0])
        lineup.append(("FLEX", flex[0]))

    superflex = sorted(
        [row for row in available if row not in selected],
        key=lambda row: safe_float(row.get("projected_ppg")),
        reverse=True,
    )
    if superflex:
        selected.append(superflex[0])
        lineup.append(("SUPERFLEX", superflex[0]))

    return lineup


def heat_map_by_team(payload: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    if not isinstance(payload, Mapping):
        return {}
    heat = (((payload.get("views") or {}).get("positional_strength_heat_map") or {}).get("teams") or [])
    return {clean(row.get("team_name")): row for row in heat if row.get("team_name")}


def simulator_by_team(payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        clean(row.get("team_name")): row
        for row in (payload.get("teams") or [])
        if row.get("team_name")
    }


def _team_rank(simulator_rows: list[Mapping[str, Any]], team_name: str) -> int:
    ordered = sorted(simulator_rows, key=lambda row: safe_float(row.get("expected_wins")), reverse=True)
    for index, row in enumerate(ordered, 1):
        if clean(row.get("team_name")) == team_name:
            return index
    return 0


def _heat_percentiles(row: Mapping[str, Any] | None) -> dict[str, float]:
    if not isinstance(row, Mapping):
        return {}
    positions = row.get("positions") or {}
    def get(position: str, field: str) -> float:
        return safe_float((positions.get(position) or {}).get(field))
    return {
        "QB": get("QB", "dedicated_starter_projection_ppg_league_percentile"),
        "QB2": get("QB", "top_two_qb_projection_ppg_league_percentile"),
        "RB": get("RB", "dedicated_starter_projection_ppg_league_percentile"),
        "WR": get("WR", "dedicated_starter_projection_ppg_league_percentile"),
        "TE": get("TE", "dedicated_starter_projection_ppg_league_percentile"),
        "Picks": safe_float(row.get("future_draft_capital_market_value_league_percentile")),
    }


def _strength_summary(heat: Mapping[str, Any] | None) -> tuple[list[str], list[str]]:
    values = _heat_percentiles(heat)
    labels = {
        "QB2": "top-two QB projection",
        "RB": "running back",
        "WR": "wide receiver",
        "TE": "tight end",
        "Picks": "future draft capital",
    }
    ranked = sorted(
        [(labels[key], value) for key, value in values.items() if key in labels],
        key=lambda item: item[1],
        reverse=True,
    )
    if not ranked:
        return ([], [])
    return ([x[0] for x in ranked[:2]], [x[0] for x in ranked[-2:]])


def _profile_text(
    team: Mapping[str, Any],
    sim: Mapping[str, Any],
    heat: Mapping[str, Any] | None,
) -> str:
    strengths, weaknesses = _strength_summary(heat)
    lineup = projection_optimized_lineup(team)
    lineup_ppg = sum(safe_float(row.get("projected_ppg")) for _, row in lineup)
    ages = [safe_float(row.get("age")) for row in team.get("players") or [] if row.get("age") is not None]
    avg_age = sum(ages) / len(ages) if ages else 0.0

    title = safe_float(sim.get("championship_probability"))
    playoff = safe_float(sim.get("playoff_probability"))
    if title >= .20:
        tier = "championship favorite/core"
    elif title >= .10:
        tier = "top-tier contender"
    elif playoff >= .55:
        tier = "playoff contender"
    elif playoff >= .20:
        tier = "fringe playoff team"
    else:
        tier = "development/rebuild profile"

    strength_text = ", ".join(strengths) if strengths else "the areas shown in the roster table"
    weakness_text = ", ".join(weaknesses) if weaknesses else "the least productive parts of the projected lineup"

    return (
        f"The current Simulator places {team.get('team_name')} in the <b>{tier}</b> tier: "
        f"{safe_float(sim.get('expected_wins')):.2f} expected wins, {pct(sim.get('playoff_probability'))} playoff odds, "
        f"and {pct(sim.get('championship_probability'))} championship odds. "
        f"League Intelligence identifies <b>{strength_text}</b> as the clearest relative strengths and "
        f"<b>{weakness_text}</b> as the weakest relative areas. "
        f"The report's projection-optimized legal lineup totals about <b>{lineup_ppg:.1f} projected PPG</b> "
        f"before weekly bye and matchup effects, and the current roster's average listed age is <b>{avg_age:.1f}</b>."
    )


def _standard_table(rows, widths, *, font_size=7.1, repeat_rows=1):
    table = Table(rows, colWidths=widths, repeatRows=repeat_rows, hAlign="LEFT")
    commands = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), .35, MID_GRAY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTSIZE", (0, 0), (-1, -1), font_size),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    for row_index in range(1, len(rows)):
        if row_index % 2 == 0:
            commands.append(("BACKGROUND", (0, row_index), (-1, row_index), LIGHT_GRAY))
    table.setStyle(TableStyle(commands))
    return table


def _championship_chart(simulator_rows: list[Mapping[str, Any]]):
    ordered = sorted(simulator_rows, key=lambda row: safe_float(row.get("championship_probability")), reverse=True)
    drawing = Drawing(CONTENT_WIDTH, 3.2 * inch)
    drawing.add(String(0, 3.06 * inch, "2026 championship probability", fontName="Helvetica-Bold", fontSize=10, fillColor=NAVY))
    chart = HorizontalBarChart()
    chart.x = 145
    chart.y = 15
    chart.width = CONTENT_WIDTH - 165
    chart.height = 2.65 * inch
    chart.data = [[safe_float(row.get("championship_probability")) * 100 for row in reversed(ordered)]]
    chart.categoryAxis.categoryNames = [clean(row.get("team_name")) for row in reversed(ordered)]
    chart.valueAxis.valueMin = 0
    chart.valueAxis.valueMax = max(35, max(chart.data[0]) * 1.08 if chart.data[0] else 35)
    chart.valueAxis.labelTextFormat = lambda value: f"{value:.0f}%"
    chart.bars[0].fillColor = BLUE
    chart.bars[0].strokeColor = None
    chart.categoryAxis.labels.fontSize = 6.4
    chart.valueAxis.labels.fontSize = 6.4
    drawing.add(chart)
    return drawing


def _roster_table(team: Mapping[str, Any]):
    lineup = projection_optimized_lineup(team)
    role = {row["player_id"]: slot for slot, row in lineup}
    for row in team.get("players") or []:
        player_id = row.get("player_id")
        if row.get("roster_status") == "TAXI":
            role[player_id] = "TAXI"
        elif row.get("roster_status") == "RESERVE":
            role[player_id] = "RESERVE"
        elif player_id not in role:
            role[player_id] = "BENCH"

    rows_sorted = sorted(
        team.get("players") or [],
        key=lambda row: (
            ROLE_ORDER.get(role.get(row.get("player_id"), "BENCH"), 9),
            -safe_float(row.get("projected_ppg"), -999.0),
            row.get("name") or "",
        ),
    )

    rows = [["Role", "Player", "Pos", "NFL", "Age", "2026 projected stats", "FPts", "PPG", "Status"]]
    for row in rows_sorted:
        rows.append([
            role.get(row.get("player_id"), "BENCH"),
            row.get("name"),
            row.get("position"),
            row.get("nfl_team"),
            row.get("age") if row.get("age") is not None else "-",
            row.get("projected_stats"),
            f"{safe_float(row.get('projected_points')):.1f}" if row.get("projected_points") is not None else "-",
            f"{safe_float(row.get('projected_ppg')):.1f}" if row.get("projected_ppg") is not None else "-",
            row.get("injury_status") or "",
        ])

    table = Table(
        rows,
        colWidths=[.64*inch, 1.38*inch, .38*inch, .40*inch, .38*inch, 3.78*inch, .54*inch, .48*inch, .72*inch],
        repeatRows=1,
        hAlign="LEFT",
    )
    commands = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), .3, MID_GRAY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTSIZE", (0, 0), (-1, -1), 6.3),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 2.2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.2),
    ]
    for index, player in enumerate(rows_sorted, 1):
        player_role = role.get(player.get("player_id"), "BENCH")
        if player_role in STARTER_ORDER:
            bg = LIGHT_GREEN
        elif player_role == "TAXI":
            bg = LIGHT_GOLD
        elif player_role == "RESERVE":
            bg = LIGHT_RED
        elif index % 2 == 0:
            bg = LIGHT_GRAY
        else:
            bg = WHITE
        commands.append(("BACKGROUND", (0, index), (-1, index), bg))
        if player_role in STARTER_ORDER:
            commands.append(("FONTNAME", (0, index), (1, index), "Helvetica-Bold"))
    table.setStyle(TableStyle(commands))
    return table


def render(
    output: Path,
    *,
    rosters_path: Path = DEFAULT_ROSTERS,
    users_path: Path = DEFAULT_USERS,
    players_path: Path = DEFAULT_PLAYERS,
    projections_path: Path = DEFAULT_PROJECTIONS,
    simulator_path: Path = DEFAULT_SIMULATOR,
    league_intelligence_path: Path | None = DEFAULT_LEAGUE_INTELLIGENCE,
) -> None:
    rosters = load(rosters_path)
    users = load(users_path)
    players = load(players_path)
    projections = load(projections_path)
    simulator = load(simulator_path)
    league_intelligence = (
        load(league_intelligence_path)
        if league_intelligence_path and Path(league_intelligence_path).exists()
        else None
    )

    teams = build_rosters(rosters, users, players, projections)
    sim_by_team = simulator_by_team(simulator)
    heat_by_team = heat_map_by_team(league_intelligence)

    if len(teams) != 12:
        raise ValueError(f"Expected 12 FSFFL rosters, found {len(teams)}")
    if len(sim_by_team) != 12:
        raise ValueError(f"Expected 12 Simulator teams, found {len(sim_by_team)}")
    missing_sim = [team["team_name"] for team in teams if team["team_name"] not in sim_by_team]
    if missing_sim:
        raise ValueError(f"Roster teams missing from Simulator output: {missing_sim}")

    total_players = sum(len(team["players"]) for team in teams)
    projected_players = sum(
        1 for team in teams for row in team["players"]
        if row.get("projected_points") is not None
    )

    s = styles()
    s.add(ParagraphStyle(
        name="PMG_Lead", parent=s["FS_Body"], fontSize=10.0, leading=13.1, textColor=BLACK, spaceAfter=5
    ))
    s.add(ParagraphStyle(
        name="PMG_Team", parent=s["FS_Title"], fontSize=20.0, leading=22.0, textColor=NAVY
    ))
    s.add(ParagraphStyle(
        name="PMG_Cover", parent=s["FS_Title"], fontSize=26.0, leading=29.0, alignment=1, textColor=NAVY
    ))

    output.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(output),
        pagesize=PAGE_SIZE,
        leftMargin=.42*inch,
        rightMargin=.42*inch,
        topMargin=.42*inch,
        bottomMargin=.42*inch,
        title="FSFFL 2026 Preseason Media Guide",
    )
    story = [
        Spacer(1, .55*inch),
        P(s, "FSFFL DYNASTY", "FS_Sub"),
        Paragraph("2026 PRESEASON MEDIA GUIDE", s["PMG_Cover"]),
        P(s, "Expanded team-profile edition", "FS_Sub"),
        Spacer(1, .22*inch),
        P(
            s,
            "League outlook | power rankings | division forecasts | championship landscape | "
            "12 full team profiles | complete projected roster tables",
            "FS_Sub",
        ),
        Spacer(1, .35*inch),
        P(
            s,
            "This publication is built from governed Simulator, League Intelligence, roster, player, "
            "and preseason projection outputs. It is presentation-only and creates no new valuation, "
            "trade, or recommendation authority.",
            "FS_Body",
        ),
        Spacer(1, .25*inch),
        P(
            s,
            f"Projection coverage in this roster snapshot: {projected_players} of {total_players} rostered players. "
            "Unsupported projection rows remain blank rather than being estimated.",
            "FS_Small",
        ),
        PageBreak(),
        P(s, "1. League Outlook", "FS_Title"),
        P(
            s,
            "The current Simulator separates the regular-season race from the championship-conversion race. "
            "Expected wins describe the path to the bracket; title probability describes how often each roster "
            "finishes the job once weekly variance and playoff structure are included.",
            "PMG_Lead",
        ),
        _championship_chart(simulator.get("teams") or []),
        Spacer(1, 4),
    ]

    standings_rows = [["Rank", "Team", "Exp W", "Proj PF", "Playoffs", "Bye", "Title", "Division"]]
    ordered_sim = sorted(simulator.get("teams") or [], key=lambda row: safe_float(row.get("expected_wins")), reverse=True)
    for index, row in enumerate(ordered_sim, 1):
        standings_rows.append([
            index,
            clean(row.get("team_name")),
            f"{safe_float(row.get('expected_wins')):.2f}",
            f"{safe_float(row.get('expected_points_for')):,.0f}",
            pct(row.get("playoff_probability")),
            pct(row.get("bye_probability")),
            pct(row.get("championship_probability")),
            pct(row.get("division_probability")),
        ])
    story += [
        _standard_table(
            standings_rows,
            [.42*inch, 2.15*inch, .66*inch, .73*inch, .80*inch, .66*inch, .70*inch, .76*inch],
            font_size=7.1,
        ),
        PageBreak(),
        P(s, "2. Division Forecasts", "FS_Title"),
        P(
            s,
            "Division structure materially changes playoff and bye paths, so the preseason guide keeps division "
            "probability separate from overall team strength.",
            "PMG_Lead",
        ),
    ]

    for division in range(1, 5):
        division_rows = [
            row for row in simulator.get("teams") or [] if int(row.get("division") or 0) == division
        ]
        division_rows.sort(key=lambda row: safe_float(row.get("division_probability")), reverse=True)
        rows = [["Team", "Exp W", "Win division", "Playoffs", "Title"]]
        for row in division_rows:
            rows.append([
                clean(row.get("team_name")),
                f"{safe_float(row.get('expected_wins')):.2f}",
                pct(row.get("division_probability")),
                pct(row.get("playoff_probability")),
                pct(row.get("championship_probability")),
            ])
        story += [
            P(s, f"Division {division}", "FS_Section"),
            _standard_table(rows, [2.55*inch, .78*inch, 1.0*inch, .9*inch, .8*inch], font_size=7.3),
            Spacer(1, 6),
        ]

    story += [
        PageBreak(),
        P(s, "3. How to Read the Team Pages", "FS_Title"),
        P(
            s,
            "Every franchise receives the same two-page treatment. Page one is the team profile. Page two is "
            "the complete roster and 2026 projection table.",
            "PMG_Lead",
        ),
        P(
            s,
            "<b>Projection-optimized legal lineup:</b> the report fills QB, 2 RB, 3 WR, TE, FLEX and SUPERFLEX "
            "with the highest supported preseason PPG among currently active rostered players. This is only a "
            "display transform used to organize the roster page; it is not a new model score or transaction recommendation.",
            "FS_Body",
        ),
        Spacer(1, 5),
        P(
            s,
            "<b>Roster table:</b> each player includes age, NFL team, position-specific 2026 projected stats, "
            "half-PPR projected fantasy points, projected PPG and current roster/injury status. Taxi and reserve "
            "players are never promoted into the displayed starting lineup.",
            "FS_Body",
        ),
        Spacer(1, 5),
        P(
            s,
            "<b>League-relative profile:</b> when a current League Intelligence payload is supplied, the profile "
            "uses its governed positional percentiles. If that source is absent, the report omits those comparisons "
            "rather than reconstructing a substitute score.",
            "FS_Body",
        ),
        PageBreak(),
    ]

    teams_by_name = {team["team_name"]: team for team in teams}
    ordered_teams = sorted(teams, key=lambda team: safe_float(sim_by_team[team["team_name"]].get("expected_wins")), reverse=True)

    for rank, team in enumerate(ordered_teams, 1):
        sim = sim_by_team[team["team_name"]]
        heat = heat_by_team.get(team["team_name"])
        lineup = projection_optimized_lineup(team)
        strengths, weaknesses = _strength_summary(heat)

        metrics = [
            ["2026 rank", f"#{rank}"],
            ["Expected wins", f"{safe_float(sim.get('expected_wins')):.2f}"],
            ["Projected PF", f"{safe_float(sim.get('expected_points_for')):,.0f}"],
            ["Playoffs", pct(sim.get("playoff_probability"))],
            ["Bye", pct(sim.get("bye_probability"))],
            ["Title", pct(sim.get("championship_probability"))],
            ["Win division", pct(sim.get("division_probability"))],
        ]
        metric_table = _standard_table(metrics, [1.08*inch, 1.0*inch], font_size=7.5, repeat_rows=0)

        heat_values = _heat_percentiles(heat)
        heat_rows = [["Area", "League pct."]]
        for key, label in (("QB2", "Top-two QB"), ("RB", "RB"), ("WR", "WR"), ("TE", "TE"), ("Picks", "Draft capital")):
            if key in heat_values:
                heat_rows.append([label, pct(heat_values[key], 0)])
        heat_table = (
            _standard_table(heat_rows, [1.35*inch, .95*inch], font_size=7.1)
            if len(heat_rows) > 1
            else P(s, "League Intelligence percentile context unavailable for this build.", "FS_Small")
        )

        lineup_rows = [["Slot", "Player", "Pos", "PPG"]]
        for slot, row in lineup:
            lineup_rows.append([
                slot,
                row.get("name"),
                row.get("position"),
                f"{safe_float(row.get('projected_ppg')):.1f}",
            ])
        lineup_table = _standard_table(lineup_rows, [.72*inch, 1.55*inch, .42*inch, .52*inch], font_size=7.1)

        story += [
            P(s, f"{rank}. {team['team_name']}", "PMG_Team"),
            P(s, f"Manager: {team['manager']} | Division {team['division']}", "FS_Sub"),
            Spacer(1, 5),
            Paragraph(_profile_text(team, sim, heat), s["PMG_Lead"]),
            Spacer(1, 6),
            Table(
                [[metric_table, heat_table, lineup_table]],
                colWidths=[2.25*inch, 2.45*inch, 3.35*inch],
                style=[
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 3),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ],
            ),
            Spacer(1, 7),
            P(
                s,
                (
                    f"<b>Strongest league-relative areas:</b> {', '.join(strengths)}. "
                    f"<b>Least-strong league-relative areas:</b> {', '.join(weaknesses)}."
                    if strengths and weaknesses
                    else "League Intelligence positional comparison was not supplied for this build."
                ),
                "FS_Body",
            ),
            Spacer(1, 4),
            P(
                s,
                "The team profile is descriptive. It explains the roster's current competitive shape but does not "
                "recommend a trade, waiver move, strategic posture, or valuation adjustment.",
                "FS_Small",
            ),
            PageBreak(),
            P(s, f"{team['team_name']} - Full 2026 Projected Roster", "FS_Title"),
            P(
                s,
                "Green = projection-optimized starter | gray/white = bench | gold = taxi | red = reserve/IR",
                "FS_Small",
            ),
            Spacer(1, 4),
            _roster_table(team),
            Spacer(1, 4),
            P(
                s,
                "Fantasy points use the current FSFFL half-PPR preseason projection bridge. "
                "'No supported projection' means the source did not publish a usable projection row; "
                "the report does not invent a replacement estimate.",
                "FS_Small",
            ),
            PageBreak(),
        ]

    story += [
        P(s, "Methodology & Governance", "FS_Title"),
        P(
            s,
            "Season probabilities come directly from the governed FSFFL Season Simulator. League-relative positional "
            "percentiles come from the read-only League Intelligence Terminal when provided. Roster membership, taxi "
            "and reserve designations come from the current Sleeper roster snapshot. Player age and NFL-team metadata "
            "come from the current player registry. Position-specific stat lines and fantasy-point projections come "
            "from the current 2026 preseason projection bridge.",
            "FS_Body",
        ),
        Spacer(1, 6),
        P(
            s,
            "Authority boundary: this renderer is a Reports / Publications consumer. It does not own projections, "
            "competitive simulation, player value, decision utility, transaction search, negotiation policy, or "
            "strategic posture. It may sort, format, rank existing outputs, and construct the explicitly labeled "
            "projection-optimized legal lineup for presentation only.",
            "FS_Body",
        ),
    ]

    def page_footer(canvas, doc_obj):
        canvas.saveState()
        canvas.setFillColor(GRAY)
        canvas.setFont("Helvetica", 6.2)
        canvas.drawString(.42*inch, .20*inch, f"{MODEL_VERSION} | presentation-only")
        canvas.drawRightString(PAGE_WIDTH-.42*inch, .20*inch, f"Page {doc_obj.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=page_footer, onLaterPages=page_footer)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--rosters", type=Path, default=DEFAULT_ROSTERS)
    parser.add_argument("--users", type=Path, default=DEFAULT_USERS)
    parser.add_argument("--players", type=Path, default=DEFAULT_PLAYERS)
    parser.add_argument("--projections", type=Path, default=DEFAULT_PROJECTIONS)
    parser.add_argument("--simulator", type=Path, default=DEFAULT_SIMULATOR)
    parser.add_argument("--league-intelligence", type=Path, default=DEFAULT_LEAGUE_INTELLIGENCE)
    args = parser.parse_args()
    render(
        args.output,
        rosters_path=args.rosters,
        users_path=args.users,
        players_path=args.players,
        projections_path=args.projections,
        simulator_path=args.simulator,
        league_intelligence_path=args.league_intelligence,
    )


if __name__ == "__main__":
    main()
