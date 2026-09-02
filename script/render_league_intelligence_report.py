#!/usr/bin/env python3
"""Render the governed League Intelligence Terminal as a polished PDF.

The renderer is presentation-only. It consumes the Terminal payload and never
invokes valuation, simulation, utility, search, or recommendation engines.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping

from reportlab.graphics.charts.barcharts import HorizontalBarChart
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape as landscape_page, letter
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
    kpi_card,
    safe_float,
    styles,
)
from reporting import league_title_odds_chart, team_state


MODEL_VERSION = "FSFFL-League-Intelligence-Report-1.0"
PAGE_WIDTH, PAGE_HEIGHT = landscape_page(letter)
CONTENT_WIDTH = PAGE_WIDTH - 0.8 * inch
FOCUS_DEFAULT = "846634401482792960"


def load(path: Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Terminal input must be a JSON object")
    return value


def _map(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _pct(value: Any, digits: int = 0) -> str:
    return f"{safe_float(value) * 100:.{digits}f}%"


def _signed_pct(value: Any, digits: int = 1) -> str:
    return f"{safe_float(value) * 100:+.{digits}f} pts"


def _rank(rows: list[Mapping[str, Any]], focus_id: str, key: str) -> int | None:
    ordered = sorted(rows, key=lambda row: safe_float(row.get(key)), reverse=True)
    for index, row in enumerate(ordered, 1):
        if str(row.get("user_id")) == focus_id:
            return index
    return None


def _source_health(payload: Mapping[str, Any]) -> dict[str, Any]:
    health = _map(payload.get("contract_health"))
    player = _map(health.get("player_value_authority"))
    context = _map(health.get("gm3_team_context"))
    return {
        "player_quarantined": int(player.get("quarantined_player_count") or 0),
        "market_aliases": int(player.get("market_anchor_alias_count") or 0),
        "model_vs_market_available": bool(player.get("authoritative_model_vs_market_available")),
        "team_context_compatible": bool(context.get("compatible")),
        "team_context_source": context.get("source_path"),
    }


def _validate(payload: Mapping[str, Any], focus_id: str) -> None:
    architecture = _map(payload.get("architecture"))
    read_only = _map(architecture.get("read_only"))
    forbidden_true = [
        key
        for key in (
            "model_state_mutation",
            "league_state_mutation",
            "transaction_execution",
            "recommendation_authority",
            "rescoring_authority",
        )
        if read_only.get(key) is not False
    ]
    if forbidden_true:
        raise ValueError(f"Terminal read-only contract is incomplete: {forbidden_true}")
    views = _map(payload.get("views"))
    heat = _map(views.get("positional_strength_heat_map"))
    if not any(str(row.get("user_id")) == focus_id for row in heat.get("teams") or []):
        raise ValueError(f"focus team not present in positional heat map: {focus_id}")


def _row_table(rows, widths, *, repeat_rows=1, font_size=7.0, row_bgs=True):
    table = Table(rows, colWidths=widths, repeatRows=repeat_rows, hAlign="LEFT")
    commands = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.35, MID_GRAY),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("FONTSIZE", (0, 0), (-1, -1), font_size),
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ]
    if row_bgs:
        for row_index in range(1, len(rows)):
            if row_index % 2 == 0:
                commands.append(("BACKGROUND", (0, row_index), (-1, row_index), LIGHT_GRAY))
    table.setStyle(TableStyle(commands))
    return table


def _heat_color(value: Any):
    value = max(0.0, min(1.0, safe_float(value)))
    if value <= 0.5:
        t = value / 0.5
        start, end = LIGHT_RED, LIGHT_GOLD
    else:
        t = (value - 0.5) / 0.5
        start, end = LIGHT_GOLD, LIGHT_GREEN
    return colors.Color(
        start.red + (end.red - start.red) * t,
        start.green + (end.green - start.green) * t,
        start.blue + (end.blue - start.blue) * t,
    )


def _heat_table(s, teams: list[Mapping[str, Any]], metric: str, focus_id: str):
    rows = [["Team", "QB", "RB", "WR", "TE", "Draft capital", "Overall player value"]]
    values: list[list[Any]] = []
    for team in sorted(teams, key=lambda row: safe_float(_map(row.get("competitive_outcomes")).get("expected_wins")), reverse=True):
        positions = _map(team.get("positions"))
        row_values = [
            _map(positions.get(position)).get(metric) for position in ("QB", "RB", "WR", "TE")
        ] + [
            team.get("future_draft_capital_market_value_league_percentile"),
            team.get("long_term_player_market_value_league_percentile"),
        ]
        rows.append([clean(team.get("team_name"))] + [_pct(value) for value in row_values])
        values.append(row_values)
    table = Table(rows, colWidths=[2.23 * inch] + [1.2 * inch] * 4 + [1.42 * inch, 1.42 * inch], repeatRows=1)
    commands = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.35, MID_GRAY),
        ("ALIGN", (1, 1), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.0),
        ("TOPPADDING", (0, 0), (-1, -1), 2.0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.0),
    ]
    ordered = sorted(teams, key=lambda row: safe_float(_map(row.get("competitive_outcomes")).get("expected_wins")), reverse=True)
    for row_index, (team, row_values) in enumerate(zip(ordered, values), 1):
        if str(team.get("user_id")) == focus_id:
            commands.extend([
                ("BOX", (0, row_index), (-1, row_index), 1.5, BLUE),
                ("TEXTCOLOR", (0, row_index), (0, row_index), BLUE),
            ])
        for col_index, value in enumerate(row_values, 1):
            commands.append(("BACKGROUND", (col_index, row_index), (col_index, row_index), _heat_color(value)))
    table.setStyle(TableStyle(commands))
    return table


def _percentile_chart(title: str, rows: Iterable[tuple[str, Any]], width=4.65 * inch, height=2.25 * inch):
    values = [(clean(label), max(0.0, min(1.0, safe_float(value))) * 100) for label, value in rows]
    values = list(reversed(values))
    drawing = Drawing(width, height)
    drawing.add(String(0, height - 10, title, fontName="Helvetica-Bold", fontSize=9, fillColor=NAVY))
    chart = HorizontalBarChart()
    chart.x = 100
    chart.y = 16
    chart.width = width - 115
    chart.height = height - 40
    chart.data = [[value for _, value in values]]
    chart.categoryAxis.categoryNames = [label for label, _ in values]
    chart.valueAxis.valueMin = 0
    chart.valueAxis.valueMax = 100
    chart.valueAxis.valueStep = 25
    chart.valueAxis.labelTextFormat = lambda value: f"{value:.0f}%"
    chart.bars[0].fillColor = BLUE
    chart.bars[0].strokeColor = None
    chart.categoryAxis.labels.fontSize = 7
    chart.valueAxis.labels.fontSize = 6
    chart.valueAxis.strokeColor = MID_GRAY
    chart.categoryAxis.strokeColor = MID_GRAY
    drawing.add(chart)
    drawing.add(String(0, 2, "League percentile: 100% = strongest", fontName="Helvetica", fontSize=6, fillColor=GRAY))
    return drawing


def _expected_wins_chart(rows: list[Mapping[str, Any]], width=9.8 * inch, height=2.35 * inch):
    selected = list(reversed(rows[:8]))
    drawing = Drawing(width, height)
    drawing.add(String(0, height - 10, "Gross expected-wins improvement if added for free", fontName="Helvetica-Bold", fontSize=9, fillColor=NAVY))
    chart = HorizontalBarChart()
    chart.x = 118
    chart.y = 17
    chart.width = width - 132
    chart.height = height - 43
    chart.data = [[safe_float(_map(_map(row.get("focal_team_context")).get("simulator_delta")).get("expected_wins")) for row in selected]]
    chart.categoryAxis.categoryNames = [f"{clean(row.get('name'))} ({row.get('position')})" for row in selected]
    chart.valueAxis.valueMin = 0
    maximum = max(chart.data[0] or [1.0])
    chart.valueAxis.valueMax = math.ceil(maximum * 5) / 5
    chart.valueAxis.valueStep = 0.2
    chart.valueAxis.labelTextFormat = lambda value: f"{value:.1f}"
    chart.bars[0].fillColor = GREEN
    chart.bars[0].strokeColor = None
    chart.categoryAxis.labels.fontSize = 6.6
    chart.valueAxis.labels.fontSize = 6
    chart.valueAxis.strokeColor = MID_GRAY
    chart.categoryAxis.strokeColor = MID_GRAY
    drawing.add(chart)
    drawing.add(String(0, 2, "Gross scenario only: cost and seller compensation are deliberately excluded", fontName="Helvetica", fontSize=6, fillColor=GRAY))
    return drawing


def _footer(canvas, document):
    canvas.saveState()
    canvas.setStrokeColor(MID_GRAY)
    canvas.setLineWidth(0.4)
    canvas.line(0.4 * inch, 0.34 * inch, PAGE_WIDTH - 0.4 * inch, 0.34 * inch)
    canvas.setFont("Helvetica", 6.4)
    canvas.setFillColor(GRAY)
    canvas.drawString(0.42 * inch, 0.2 * inch, f"{MODEL_VERSION} | Presentation-only; governed Terminal data remains authoritative")
    canvas.drawRightString(PAGE_WIDTH - 0.42 * inch, 0.2 * inch, f"Page {document.page}")
    canvas.restoreState()


def _title(s, heading: str, subheading: str):
    return [P(s, heading, "FS_Title"), P(s, subheading, "FS_Sub"), Spacer(1, 8)]


def _focus_data(payload: Mapping[str, Any], focus_id: str):
    views = _map(payload.get("views"))
    heat = _map(views.get("positional_strength_heat_map"))
    teams = list(heat.get("teams") or [])
    focus = next(row for row in teams if str(row.get("user_id")) == focus_id)
    landscape = list(_map(views.get("league_competitive_landscape")).get("teams") or [])
    focus_competitive = next(row for row in landscape if str(row.get("user_id")) == focus_id)
    context = list(_map(views.get("team_relative_player_context")).get("records") or [])
    external = [
        row for row in context
        if row.get("available") and str(row.get("current_owner_user_id")) != focus_id
    ]
    external.sort(key=lambda row: safe_float(_map(_map(row.get("focal_team_context")).get("shared_decision_utility")).get("score")), reverse=True)
    own = [
        row for row in context
        if row.get("available") and str(row.get("current_owner_user_id")) == focus_id
    ]
    own.sort(key=lambda row: safe_float(_map(_map(row.get("focal_team_context")).get("shared_decision_utility")).get("score")))
    partners = list(_map(views.get("trade_partner_intelligence")).get("focus_team_positional_complementarities") or [])
    partners.sort(key=lambda row: safe_float(row.get("league_relative_strength_gap")), reverse=True)
    return views, teams, focus, landscape, focus_competitive, external, own, partners


def render(payload: Mapping[str, Any], output: Path, focus_id: str) -> None:
    _validate(payload, focus_id)
    views, teams, focus, landscape, focus_comp, external, own, partners = _focus_data(payload, focus_id)
    health = _source_health(payload)
    position_rows = _map(focus.get("positions"))
    focus_outcomes = _map(focus.get("competitive_outcomes"))
    focus_name = clean(focus.get("team_name"))
    expected_wins_rank = _rank(landscape, focus_id, "expected_wins")
    title_rank = _rank(landscape, focus_id, "championship_probability")
    points_rank = _rank(landscape, focus_id, "expected_points_for")

    first_context = next((row for row in external if row.get("available")), {})
    strategic = _map(first_context.get("focal_team_context"))
    calculated_state = strategic.get("competitive_state")
    active_posture = strategic.get("strategic_posture")
    posture_source = strategic.get("strategic_posture_source")

    s = styles()
    s.add(ParagraphStyle(name="LI_Lead", parent=s["FS_Body"], fontName="Helvetica-Bold", fontSize=10.3, leading=13.0, textColor=NAVY))
    s.add(ParagraphStyle(name="LI_Callout", parent=s["FS_Body"], fontSize=8.4, leading=11.1, textColor=BLACK))
    s.add(ParagraphStyle(name="LI_Table", parent=s["FS_Body"], fontSize=6.7, leading=8.1))
    s.add(ParagraphStyle(name="LI_Tiny", parent=s["FS_Small"], fontSize=6.0, leading=7.2))

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    document = SimpleDocTemplate(
        str(output),
        pagesize=landscape_page(letter),
        leftMargin=0.4 * inch,
        rightMargin=0.4 * inch,
        topMargin=0.38 * inch,
        bottomMargin=0.42 * inch,
        title=f"FSFFL League Intelligence - {focus_name}",
        author="FSFFL Reporting",
    )
    story = []

    # Page 1 - executive answer.
    story += _title(s, "FSFFL LEAGUE INTELLIGENCE", f"{focus_name} | A plain-English view of the league, your roster, and where to investigate")
    story.append(P(s, "BOTTOM LINE", "FS_Section"))
    bottom_line = (
        f"{focus_name} projects as one of the league's strongest 2026 teams: #{expected_wins_rank} in expected wins, "
        f"#{points_rank} in expected points, and #{title_rank} in championship probability. Your clearest roster edge is "
        "top-end Superflex quarterback production and a strong wide-receiver group. Running-back starter production is the "
        "most obvious place where an elite addition changes the season outlook."
    )
    story += [P(s, bottom_line, "LI_Lead"), Spacer(1, 7)]
    cards = [
        kpi_card(s, "EXPECTED WINS", f"{safe_float(focus_outcomes.get('expected_wins')):.2f} (#{expected_wins_rank})", "blue", 2.1 * inch),
        kpi_card(s, "PLAYOFF CHANCE", _pct(focus_outcomes.get("playoff_probability"), 1), "positive", 2.1 * inch),
        kpi_card(s, "TITLE CHANCE", f"{_pct(focus_outcomes.get('championship_probability'), 1)} (#{title_rank})", "positive", 2.1 * inch),
        kpi_card(s, "TOP-TWO QB OUTPUT", _pct(_map(position_rows.get("QB")).get("top_two_qb_projection_ppg_league_percentile")), "blue", 2.1 * inch),
        kpi_card(s, "RB STARTER OUTPUT", _pct(_map(position_rows.get("RB")).get("dedicated_starter_projection_ppg_league_percentile")), "warning", 2.1 * inch),
    ]
    card_table = Table([cards], colWidths=[2.12 * inch] * 5)
    card_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 1), ("RIGHTPADDING", (0, 0), (-1, -1), 1)]))
    story += [card_table, Spacer(1, 9)]
    chart = league_title_odds_chart(landscape, width=5.05 * inch, height=2.35 * inch, limit=8)
    takeaways = [
        [P(s, "WHAT THE TERMINAL SAYS", "FS_Section")],
        [P(s, f"<b>Competitive position:</b> the Simulator gives you a {_pct(focus_outcomes.get('playoff_probability'), 1)} playoff chance and {_pct(focus_outcomes.get('championship_probability'), 1)} title chance.", "LI_Callout")],
        [P(s, f"<b>Calculated state:</b> {team_state(calculated_state)}. <b>Active posture:</b> {clean(active_posture)} ({clean(posture_source).replace('_', ' ').title()}). These are displayed separately; posture does not rewrite team strength.", "LI_Callout")],
        [P(s, "<b>Roster shape:</b> quarterback and wide receiver are strengths; running-back starters lag the league even though the bench provides useful depth.", "LI_Callout")],
        [P(s, "<b>How to use this:</b> investigate players and counterparties. The Terminal does not decide what to offer, what another manager will accept, or which trade to send.", "LI_Callout")],
    ]
    takeaway_table = Table(takeaways, colWidths=[5.25 * inch])
    takeaway_table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), LIGHT_BLUE), ("BOX", (0, 0), (-1, -1), 0.7, MID_GRAY), ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9), ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story += [Table([[chart, takeaway_table]], colWidths=[5.15 * inch, 5.35 * inch], style=[("VALIGN", (0, 0), (-1, -1), "TOP")])]

    # Page 2 - competitive landscape.
    story += [PageBreak()] + _title(s, "THE COMPETITIVE LANDSCAPE", "Simulator-owned season outcomes; no separate power score is created by this report")
    landscape_sorted = sorted(landscape, key=lambda row: safe_float(row.get("expected_wins")), reverse=True)
    rows = [["Rank", "Team", "Expected wins", "Expected points", "Playoffs", "Bye", "Championship", "Division"]]
    for rank, row in enumerate(landscape_sorted, 1):
        rows.append([
            rank,
            clean(row.get("team_name")),
            f"{safe_float(row.get('expected_wins')):.2f}",
            f"{safe_float(row.get('expected_points_for')):,.0f}",
            _pct(row.get("playoff_probability"), 1),
            _pct(row.get("bye_probability"), 1),
            _pct(row.get("championship_probability"), 1),
            _pct(row.get("division_probability"), 1),
        ])
    standings = _row_table(rows, [0.42 * inch, 2.5 * inch, 1.1 * inch, 1.15 * inch, 1.05 * inch, 0.9 * inch, 1.15 * inch, 1.0 * inch], font_size=7.3)
    for index, row in enumerate(landscape_sorted, 1):
        if str(row.get("user_id")) == focus_id:
            standings.setStyle(TableStyle([("BOX", (0, index), (-1, index), 1.5, BLUE), ("TEXTCOLOR", (1, index), (1, index), BLUE), ("FONTNAME", (1, index), (1, index), "Helvetica-Bold")]))
    lead = (
        f"The league has three teams above a 14% title chance. {focus_name} is clustered near the top in expected regular-season performance, "
        "but the title race is less settled than the standings alone suggest: playoff seeding and opponent strength create meaningful separation."
    )
    story += [P(s, lead, "LI_Lead"), Spacer(1, 7), standings, Spacer(1, 7), P(s, "How to read this: expected wins and points describe regular-season strength; championship probability also reflects playoff qualification, seeding, and the strength of likely opponents. None of these columns is a manager preference or recommendation.", "FS_Small")]

    # Page 3 - focus roster.
    story += [PageBreak()] + _title(s, "YOUR ROSTER SHAPE", "Continuous league percentiles show where production, depth, and long-term value are concentrated")
    starter_chart = _percentile_chart(
        "Projected starter production",
        [(position, _map(position_rows.get(position)).get("dedicated_starter_projection_ppg_league_percentile")) for position in ("QB", "RB", "WR", "TE")],
    )
    value_chart = _percentile_chart(
        "Long-term position value",
        [(position, _map(position_rows.get(position)).get("total_position_long_term_market_value_league_percentile")) for position in ("QB", "RB", "WR", "TE")],
    )
    story += [Table([[starter_chart, value_chart]], colWidths=[5.2 * inch, 5.2 * inch], style=[("VALIGN", (0, 0), (-1, -1), "TOP")]), Spacer(1, 6)]
    roster_rows = [["Position", "Starter output", "Starter pct.", "Depth output", "Depth pct.", "Long-term value", "Value pct.", "What it means"]]
    meaning = {
        "QB": "Elite Superflex foundation; top-two QB output is best in the league.",
        "RB": "Depth is useful, but the starting pair trails most contenders.",
        "WR": "A clear strength in both near-term production and long-term value.",
        "TE": "Adequate starter production and strong depth, but modest long-term value.",
    }
    for position in ("QB", "RB", "WR", "TE"):
        row = _map(position_rows.get(position))
        roster_rows.append([
            position,
            f"{safe_float(row.get('dedicated_starter_projection_ppg')):.1f} PPG",
            _pct(row.get("dedicated_starter_projection_ppg_league_percentile")),
            f"{safe_float(row.get('projection_depth_beyond_dedicated_slots_ppg')):.1f} PPG",
            _pct(row.get("projection_depth_beyond_dedicated_slots_ppg_league_percentile")),
            f"{safe_float(row.get('total_position_long_term_market_value')):,.0f}",
            _pct(row.get("total_position_long_term_market_value_league_percentile")),
            P(s, meaning[position], "LI_Table"),
        ])
    story += [_row_table(roster_rows, [0.58 * inch, 1.05 * inch, 0.72 * inch, 1.05 * inch, 0.72 * inch, 1.05 * inch, 0.72 * inch, 4.08 * inch], font_size=6.9), Spacer(1, 7)]
    story += [P(s, "The percentages are rankings within this 12-team league, not grades. For example, 82% means the roster is stronger than roughly 82% of the league on that exact measure. FLEX and Superflex are not assigned invented position weights: QB top-two output is shown separately, and the Simulator handles actual lineup optimization.", "FS_Small")]

    # Page 4 - league heat maps.
    story += [PageBreak()] + _title(s, "LEAGUE STRENGTH / WEAKNESS HEAT MAP", "Green is stronger relative to this league; red is weaker. The raw underlying fields remain in the Terminal JSON")
    story += [P(s, "Projected dedicated-starter production", "FS_Section"), _heat_table(s, teams, "dedicated_starter_projection_ppg_league_percentile", focus_id), Spacer(1, 8)]
    story += [P(s, "Long-term roster value by position", "FS_Section"), _heat_table(s, teams, "total_position_long_term_market_value_league_percentile", focus_id), Spacer(1, 6)]
    story += [P(s, "Why this is useful: the first table highlights teams that can score now; the second highlights where dynasty value is concentrated. A team can be strong in one and weak in the other. Draft-capital and overall-player-value columns are identical in both tables because they are portfolio measures, not position measures.", "FS_Small")]

    # Page 5 - rankings.
    rankings = _map(views.get("player_value_rankings"))
    players = list(rankings.get("players") or [])
    story += [PageBreak()] + _title(s, "PLAYER VALUE & RANKINGS", "Long-term market and current-season projection perspectives are deliberately kept separate")
    long_rows = [["Rank", "Player", "Pos.", "Owner", "Long-term value", "2026 PPG", "Projection range"]]
    for row in players[:18]:
        projection = _map(row.get("current_season_projection"))
        range_text = "Unavailable"
        if projection.get("available"):
            range_text = f"{safe_float(projection.get('p25')):.1f} - {safe_float(projection.get('p75')):.1f}"
        long_rows.append([
            row.get("long_term_market_rank"), clean(row.get("name")), row.get("position"), clean(row.get("owner_team")) or "Free agent",
            f"{safe_float(row.get('long_term_market_value')):,.0f}",
            f"{safe_float(projection.get('mean')):.1f}" if projection.get("available") else "-",
            range_text,
        ])
    projection_players = sorted([row for row in players if row.get("current_season_projection_rank")], key=lambda row: int(row.get("current_season_projection_rank")))[:18]
    projection_rows = [["2026 rank", "Player", "Pos.", "Owner", "Projected PPG", "25th-75th range", "Long-term rank"]]
    for row in projection_players:
        projection = _map(row.get("current_season_projection"))
        projection_rows.append([
            row.get("current_season_projection_rank"), clean(row.get("name")), row.get("position"), clean(row.get("owner_team")) or "Free agent",
            f"{safe_float(projection.get('mean')):.1f}", f"{safe_float(projection.get('p25')):.1f} - {safe_float(projection.get('p75')):.1f}", row.get("long_term_market_rank"),
        ])
    story += [
        Table([
            [P(s, "LONG-TERM MARKET LEADERS", "FS_Section"), P(s, "CURRENT-SEASON PROJECTION LEADERS", "FS_Section")],
            [_row_table(long_rows, [0.42 * inch, 1.45 * inch, 0.42 * inch, 1.52 * inch, 0.9 * inch, 0.72 * inch, 0.9 * inch], font_size=6.2), _row_table(projection_rows, [0.48 * inch, 1.45 * inch, 0.42 * inch, 1.48 * inch, 0.82 * inch, 0.92 * inch, 0.7 * inch], font_size=6.2)],
        ], colWidths=[5.25 * inch, 5.25 * inch], style=[("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2)]),
        Spacer(1, 6),
        P(s, "Important limitation: a current independent FSFFL-versus-market ranking is not yet available. The old adjusted value field is quarantined because its provenance is no longer authorized. This report shows the governed market anchor and native current-season projection separately instead of manufacturing a disagreement score.", "FS_Small"),
    ]

    # Page 6 - team-relative value.
    story += [PageBreak()] + _title(s, "VALUE TO YOUR TEAM", "These are roster-specific what-if results, not player prices and not trade recommendations")
    story += [P(s, "The model temporarily adds each external player to your actual roster, reoptimizes the lineup, handles any required cut, and measures the change. It also removes that player from the current owner to show how dependent that roster is on him. Nothing is exchanged in this diagnostic, so it answers 'how much would he help?' rather than 'what should I pay?'", "LI_Lead"), Spacer(1, 6)]
    story += [_expected_wins_chart(external), Spacer(1, 5)]
    fit_rows = [["Player", "Owner", "Pos.", "Help to your team", "Expected wins", "Title odds", "Loss to current owner", "Automatic cut"]]
    for row in external[:10]:
        focal = _map(row.get("focal_team_context"))
        owner = _map(row.get("current_owner_context"))
        utility = _map(focal.get("shared_decision_utility"))
        owner_utility = _map(owner.get("shared_decision_utility"))
        delta = _map(focal.get("simulator_delta"))
        cuts = ", ".join(clean(cut.get("name")) for cut in (focal.get("endogenous_cuts") or [])) or "None"
        fit_rows.append([
            clean(row.get("name")), clean(row.get("current_owner_team")), row.get("position"), f"{safe_float(utility.get('score')):+,.0f}",
            f"{safe_float(delta.get('expected_wins')):+.2f}", _signed_pct(delta.get("championship_probability")),
            f"{safe_float(owner_utility.get('score')):,.0f}", cuts,
        ])
    story += [_row_table(fit_rows, [1.38 * inch, 1.7 * inch, 0.45 * inch, 1.05 * inch, 0.9 * inch, 0.85 * inch, 1.18 * inch, 1.2 * inch], font_size=6.5), Spacer(1, 5)]
    story += [P(s, "Team-fit units combine only the authorized current-season and future-value channels using the active team context; larger positive numbers mean a stronger gross fit for your roster. A large negative owner number means the current team is modeled to lose substantial utility if the player disappears without compensation. The two numbers do not establish a fair price or likelihood of acceptance.", "FS_Small")]

    # Page 7 - own roster retention and partner investigation.
    story += [PageBreak()] + _title(s, "ROSTER DEPENDENCE & TRADE-PARTNER INTELLIGENCE", "Which of your players matter most, and which opposing roster shapes are worth opening")
    own_rows = [["Your player", "Pos.", "Team-fit loss if removed", "Expected wins lost", "Title odds lost", "Plain-language read"]]
    for row in own[:12]:
        focal = _map(row.get("focal_team_context"))
        utility = _map(focal.get("shared_decision_utility"))
        delta = _map(focal.get("simulator_delta"))
        wins = safe_float(delta.get("expected_wins"))
        description = "Core roster dependency" if wins <= -0.45 else "Meaningful contributor" if wins <= -0.2 else "More replaceable in current lineup"
        own_rows.append([
            clean(row.get("name")), row.get("position"), f"{safe_float(utility.get('score')):,.0f}",
            f"{wins:.2f}", _signed_pct(delta.get("championship_probability")), description,
        ])
    partner_rows = [["Team to investigate", "Position they hold", "Your value pct.", "Their value pct.", "Roster-shape gap"]]
    seen = set()
    for row in partners:
        key = (row.get("asset_holding_team_user_id"), row.get("position"))
        if key in seen:
            continue
        seen.add(key)
        partner_rows.append([
            clean(row.get("asset_holding_team")), row.get("position"), _pct(row.get("investigating_team_percentile")),
            _pct(row.get("asset_holding_team_percentile")), _pct(row.get("league_relative_strength_gap")),
        ])
        if len(partner_rows) >= 13:
            break
    story += [
        Table([
            [P(s, "PLAYERS YOUR ROSTER MOST DEPENDS ON", "FS_Section"), P(s, "STRUCTURAL COUNTERPARTY MATCHES", "FS_Section")],
            [_row_table(own_rows, [1.38 * inch, 0.42 * inch, 1.05 * inch, 0.87 * inch, 0.85 * inch, 1.45 * inch], font_size=6.25), _row_table(partner_rows, [1.7 * inch, 0.85 * inch, 0.85 * inch, 0.85 * inch, 0.9 * inch], font_size=6.4)],
        ], colWidths=[5.35 * inch, 5.15 * inch], style=[("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2)]),
        Spacer(1, 6),
        P(s, "The right-hand table only identifies positional complementarity: your roster has less long-term value at a position where the other roster has more. It does not claim that the manager wants to trade, that a particular player is available, or that two assets form a fair offer. Use the player-specific table on the previous page to investigate within these teams.", "FS_Small"),
    ]

    # Page 8 - optional Inspector and source health.
    inspector = _map(views.get("decision_utility_inspector"))
    story += [PageBreak()] + _title(s, "DECISION INSPECTOR & SOURCE HEALTH", "A final audit page: why a selected move scores the way it does, and what the Terminal refuses to show")
    if inspector.get("available", True) and inspector.get("focal_team"):
        identity = _map(inspector.get("identity"))
        focal_side = _map(inspector.get("focal_team"))
        counter = _map(inspector.get("counterparty"))
        decision_rows = [["Perspective", "Overall utility", "Expected wins", "Playoff odds", "Title odds", "Attribution"]]
        for label, side in (("Your team", focal_side), ("Other team", counter)):
            delta = _map(side.get("simulator_delta"))
            decision_rows.append([
                label, f"{safe_float(side.get('shared_decision_utility')):+,.0f}", f"{safe_float(delta.get('expected_wins')):+.2f}",
                _signed_pct(delta.get("playoff_probability")), _signed_pct(delta.get("championship_probability")),
                "Reconciled" if side.get("attribution_reconciles") is True else "Partial / unavailable",
            ])
        story += [P(s, "SELECTED EXAMPLE", "FS_Section"), P(s, clean(identity.get("description") or "Selected governed decision"), "LI_Lead"), Spacer(1, 5), _row_table(decision_rows, [1.2 * inch, 1.25 * inch, 1.15 * inch, 1.15 * inch, 1.15 * inch, 1.3 * inch], font_size=7.0), Spacer(1, 7)]
        channel_tables = []
        for label, side in (("YOUR TEAM - WHY", focal_side), ("OTHER TEAM - WHY", counter)):
            channels = list(side.get("utility_channels") or [])
            channel_rows = [["Time horizon", "Raw effect", "Authorized weight", "Contribution", "Used"]]
            for channel in channels:
                channel_rows.append([
                    "Winning now" if channel.get("channel") == "current" else "Future value" if channel.get("channel") == "future" else clean(channel.get("channel")).title(),
                    f"{safe_float(channel.get('primitive_value')):+,.0f}", f"{safe_float(channel.get('objective_weight')):.1%}",
                    f"{safe_float(channel.get('numeric_contribution')):+,.0f}", "Yes" if channel.get("authorized_for_final_utility") else "No",
                ])
            channel_tables.append([P(s, label, "FS_Section"), _row_table(channel_rows, [1.15 * inch, 0.95 * inch, 1.05 * inch, 0.95 * inch, 0.55 * inch], font_size=6.5)])
        story += [Table([channel_tables], colWidths=[5.15 * inch, 5.15 * inch], style=[("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2)]), Spacer(1, 5)]
        frontier = _map(inspector.get("negotiation_frontier"))
        story += [P(s, f"Negotiation status supplied by Trade Decision: <b>{clean(frontier.get('bucket') or frontier.get('status') or 'Unavailable').replace('_', ' ')}</b>. The Inspector exposes this result; it does not recreate the frontier or estimate acceptance probability.", "FS_Body"), Spacer(1, 7)]
    else:
        story += [P(s, "No decision record was selected for this Terminal build. The Inspector is available, but it requires an explicit record selector so the Terminal cannot silently choose a move and become a recommendation engine.", "LI_Lead"), Spacer(1, 8)]

    status_rows = [
        ["Source / capability", "Status", "What the report does"],
        ["Player rankings", "Safe", "Shows market dynasty value and native projection rankings separately."],
        ["Team-relative context", "Safe" if health["team_context_compatible"] else "Unavailable", "Consumes the fresh GM3/Simulator scenario artifact only when all source hashes match."],
        ["Old adjusted player values", f"{health['player_quarantined']} quarantined", "Excludes them from rankings because current authority/provenance is missing."],
        ["Old team profiles", "Quarantined", "Excludes profiles that mixed calculated team strength with manager posture."],
        ["FSFFL vs. market", "Unavailable", "Does not fabricate an independent model ranking from a market alias."],
        ["Acceptance probability", "Not created", "Leaves counterparty feasibility and negotiation policy to their owning applications."],
    ]
    story += [P(s, "SOURCE HEALTH / FAIL-CLOSED BEHAVIOR", "FS_Section"), _row_table(status_rows, [1.85 * inch, 1.55 * inch, 6.85 * inch], font_size=7.0), Spacer(1, 7)]
    story += [P(s, "What this report proves useful today: league position, roster shape, separate present/future player perspectives, roster-specific player impact, owner dependence, structural trade-partner investigation, and transparent decision attribution. What remains unfinished: an independent governed FSFFL player-value-versus-market view and an adaptable interactive interface.", "LI_Lead")]

    document.build(story, onFirstPage=_footer, onLaterPages=_footer)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True, help="League Intelligence Terminal JSON")
    parser.add_argument("--output", type=Path, required=True, help="Destination PDF")
    parser.add_argument("--focus-user-id", default=FOCUS_DEFAULT)
    args = parser.parse_args()
    payload = load(args.input)
    render(payload, args.output, str(args.focus_user_id))
    print(json.dumps({"renderer_model_version": MODEL_VERSION, "terminal_model_version": payload.get("model_version"), "pdf": str(args.output)}, indent=2))


if __name__ == "__main__":
    main()
