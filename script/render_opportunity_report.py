#!/usr/bin/env python3
"""Render Opportunity Engine output as a manager-facing FSFFL PDF.

Presentation only. Rankings, utility, simulation, valuation and negotiation
authority remain upstream. This renderer joins governed player context from the
FSFFL asset-value and simulator projection stores so owners can understand why
an opportunity matters in football terms.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.platypus import PageBreak, SimpleDocTemplate, Spacer, Table, TableStyle

from fsffl_report_style import (
    BLACK, BLUE, GOLD, GRAY, GREEN, LIGHT_BLUE, LIGHT_GOLD, LIGHT_GRAY,
    LIGHT_GREEN, LIGHT_RED, MID_GRAY, NAVY, RED, P, clean, footer, kpi_card,
    safe_float, styles,
)

MODEL_VERSION = "FSFFL-Opportunity-Report-1.0"
DEFAULT_ASSETS = "data/fsffl_asset_values.json"
DEFAULT_PROJECTIONS = "data/simulator/2026/inputs/player_weekly_projections.json"
DEFAULT_ROSTERS = "data/rosters.json"


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def action_box(s, action):
    tone = {
        "PURSUE": GREEN,
        "CONSIDER": BLUE,
        "EXPLORE": NAVY,
        "WAIT": GOLD,
        "AVOID": RED,
    }.get(str(action), NAVY)
    t = Table([[P(s, action, "FS_WhiteLabel")]], colWidths=[0.85 * inch], rowHeights=[0.25 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), tone),
        ("BOX", (0, 0), (-1, -1), 0.4, tone),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
    ]))
    return t


def target_name(row):
    return str((row.get("target") or {}).get("name") or "Opportunity")


def target_id(row):
    target = row.get("target") or {}
    raw = target.get("player_id") or target.get("asset_id") or ""
    raw = str(raw)
    return raw.split(":", 1)[1] if raw.startswith("player:") else raw


def structure(row):
    d = str(row.get("description") or "")
    return d[6:] if d.startswith("Trade ") else d


def focal_channel(row, name):
    attr = row.get("focal_decision_attribution") or row.get("decision_attribution") or {}
    for x in attr.get("channels") or []:
        if x.get("channel") == name:
            return safe_float(x.get("numeric_contribution"))
    return 0.0


def counterparty_channel(row, name):
    attr = row.get("counterparty_decision_attribution") or {}
    for x in attr.get("channels") or []:
        if x.get("channel") == name:
            return safe_float(x.get("numeric_contribution"))
    return 0.0


def trade_metrics(row):
    sim = row.get("simulation") or {}
    focus = sim.get("focus_delta") or {}
    strategic = sim.get("strategic") or {}
    stability = row.get("bilateral_utility_stability_confirmation") or row.get("focal_utility_stability_confirmation") or {}
    return {
        "expected_wins": safe_float(focus.get("expected_wins")),
        "expected_points": safe_float(focus.get("expected_points_for")),
        "playoff": safe_float(focus.get("playoff_probability")),
        "championship": safe_float(focus.get("championship_probability")),
        "redraft_delta": safe_float(strategic.get("market_redraft_delta")),
        "dynasty_delta": safe_float(strategic.get("market_dynasty_delta")),
        "utility": safe_float(row.get("team_improvement_score")),
        "counterparty_utility": safe_float(row.get("counterparty_shared_decision_utility_score")),
        "acceptance_fit": str(row.get("acceptance_fit") or "N/A"),
        "stability": stability,
    }


def build_player_indexes(assets, projections):
    players = assets.get("players") or []
    by_id = {str(p.get("player_id")): p for p in players if p.get("player_id") is not None}
    by_name = {str(p.get("name")): p for p in players if p.get("name")}

    proj_players = projections.get("players") or {}
    proj_by_id = {str(k): v for k, v in proj_players.items()}
    proj_by_name = {str(v.get("name")): v for v in proj_players.values() if v.get("name")}

    redraft_rank = {}
    ppg_rank = {}
    positions = sorted({str(p.get("position")) for p in players if p.get("position")})
    for pos in positions:
        redraft = sorted(
            [p for p in players if str(p.get("position")) == pos],
            key=lambda x: safe_float(x.get("market_redraft")),
            reverse=True,
        )
        for i, p in enumerate(redraft, 1):
            redraft_rank[str(p.get("player_id"))] = i

        ppg = sorted(
            [(pid, pr) for pid, pr in proj_by_id.items() if str(pr.get("position")) == pos],
            key=lambda x: safe_float(x[1].get("season_baseline_ppg")),
            reverse=True,
        )
        for i, (pid, _) in enumerate(ppg, 1):
            ppg_rank[pid] = i

    return by_id, by_name, proj_by_id, proj_by_name, redraft_rank, ppg_rank


def player_profile(row, indexes):
    by_id, by_name, proj_by_id, proj_by_name, redraft_rank, ppg_rank = indexes
    pid = target_id(row)
    name = target_name(row)
    asset = by_id.get(pid) or by_name.get(name) or {}
    pid = str(asset.get("player_id") or pid)
    proj = proj_by_id.get(pid) or proj_by_name.get(name) or {}
    return {
        "player_id": pid,
        "name": name,
        "position": str(asset.get("position") or proj.get("position") or ""),
        "nfl_team": str(asset.get("nfl_team") or proj.get("team") or ""),
        "age": asset.get("age"),
        "dynasty_position_rank": asset.get("position_rank"),
        "overall_dynasty_rank": asset.get("market_rank"),
        "redraft_position_rank": redraft_rank.get(pid),
        "projected_ppg": proj.get("season_baseline_ppg"),
        "projection_position_rank": ppg_rank.get(pid),
        "trend_30_day": asset.get("trend_30_day"),
        "injury_status": asset.get("injury_status"),
        "owner_team": asset.get("current_owner_team"),
        "market_dynasty": asset.get("market_dynasty"),
        "market_redraft": asset.get("market_redraft"),
    }


def focal_roster(board, rosters, focus_user_id=None):
    uid = str(focus_user_id or board.get("focus_user_id") or board.get("focus_user") or "")
    if not uid:
        raise ValueError("Opportunity report requires a focal user id; pass --focus-user-id or publish it in the Opportunity board.")
    for r in rosters:
        if str(r.get("owner_id")) == uid:
            return r
    raise ValueError(f"Focal user {uid} was not found in the supplied roster data.")


def current_position_reference(profile, board, rosters, indexes, focus_user_id=None):
    """Return the most relevant same-position roster benchmark.

    Prefer the lowest-projected current starter at the target position because
    that is the more decision-relevant benchmark for an acquisition: the
    incoming player normally has to beat the marginal starter, not the best
    player already on the roster. If the focal team has no current starter at
    that position, fall back to its best active non-taxi/non-reserve player.

    This is presentation context only; it does not assert the exact player
    displaced after multi-position lineup reoptimization.
    """
    by_id, _, proj_by_id, _, _, ppg_rank = indexes
    roster = focal_roster(board, rosters, focus_user_id)
    taxi = {str(x) for x in (roster.get("taxi") or [])}
    reserve = {str(x) for x in (roster.get("reserve") or [])}
    active_ids = [str(x) for x in (roster.get("players") or []) if str(x) not in taxi and str(x) not in reserve]
    starter_ids = [str(x) for x in (roster.get("starters") or []) if str(x) in active_ids]

    def same_position(ids):
        rows = []
        for pid in ids:
            asset = by_id.get(pid) or {}
            if str(asset.get("position")) != profile.get("position"):
                continue
            proj = proj_by_id.get(pid) or {}
            rows.append((safe_float(proj.get("season_baseline_ppg")), pid, asset, proj))
        return rows

    starter_candidates = same_position(starter_ids)
    if starter_candidates:
        _, pid, asset, proj = min(starter_candidates, key=lambda x: x[0])
        basis = "marginal_same_position_starter"
    else:
        active_candidates = same_position(active_ids)
        if not active_candidates:
            return {}
        _, pid, asset, proj = max(active_candidates, key=lambda x: x[0])
        basis = "best_active_same_position_fallback"

    return {
        "player_id": pid,
        "name": asset.get("name"),
        "age": asset.get("age"),
        "projected_ppg": proj.get("season_baseline_ppg"),
        "projection_position_rank": ppg_rank.get(pid),
        "market_dynasty": asset.get("market_dynasty"),
        "comparison_basis": basis,
    }


def profile_sentence(profile, ref, focal_team_name):
    parts = []
    pos = profile.get("position") or "player"
    if profile.get("projected_ppg") is not None:
        parts.append(
            f"Projects for {safe_float(profile.get('projected_ppg')):.1f} fantasy points per game "
            f"(#{int(profile.get('projection_position_rank') or 0)} among projected {pos}s)."
        )
    if profile.get("dynasty_position_rank"):
        parts.append(
            f"Current dynasty market rank: {pos}{int(profile.get('dynasty_position_rank'))}; "
            f"overall #{int(profile.get('overall_dynasty_rank') or 0)}."
        )
    if ref and ref.get("projected_ppg") is not None and profile.get("projected_ppg") is not None:
        delta = safe_float(profile.get("projected_ppg")) - safe_float(ref.get("projected_ppg"))
        age_delta = None
        if profile.get("age") is not None and ref.get("age") is not None:
            age_delta = safe_float(profile.get("age")) - safe_float(ref.get("age"))
        age_text = ""
        if age_delta is not None and abs(age_delta) >= 0.5:
            age_text = f" The target is {abs(age_delta):.0f} year{'s' if abs(age_delta) != 1 else ''} {'older' if age_delta > 0 else 'younger'}."
        if ref.get("comparison_basis") == "marginal_same_position_starter":
            benchmark = f"{clean(focal_team_name)}'s marginal current {pos} starter"
        else:
            benchmark = f"{clean(focal_team_name)}'s best active {pos} fallback"
        parts.append(
            f"Compared with {benchmark}, {clean(ref.get('name'))} "
            f"({safe_float(ref.get('projected_ppg')):.1f} PPG), that is {delta:+.1f} PPG.{age_text} "
            "This same-position comparison is context only; the full simulation includes every outgoing asset and lineup reoptimization."
        )
    return " ".join(parts)


def profile_table(s, profile, ref):
    def rank(v, prefix=""):
        return f"{prefix}{int(v)}" if v else "N/A"

    injury = str(profile.get("injury_status") or "Healthy / no active tag")
    trend = profile.get("trend_30_day")
    trend_text = f"{safe_float(trend):+,.0f}" if trend is not None else "N/A"
    rows = [
        [
            P(s, "AGE / TEAM", "FS_CardLabel"),
            P(s, "DYNASTY POS.", "FS_CardLabel"),
            P(s, "2026 PPG", "FS_CardLabel"),
            P(s, "2026 POS. RANK", "FS_CardLabel"),
            P(s, "30-DAY TREND", "FS_CardLabel"),
            P(s, "HEALTH", "FS_CardLabel"),
        ],
        [
            P(s, f"{profile.get('age') if profile.get('age') is not None else 'N/A'} | {profile.get('nfl_team') or 'N/A'}", "FS_Body"),
            P(s, rank(profile.get("dynasty_position_rank"), profile.get("position") or ""), "FS_Body"),
            P(s, f"{safe_float(profile.get('projected_ppg')):.1f}" if profile.get("projected_ppg") is not None else "N/A", "FS_Body"),
            P(s, rank(profile.get("projection_position_rank"), profile.get("position") or ""), "FS_Body"),
            P(s, trend_text, "FS_Body"),
            P(s, injury, "FS_Small"),
        ],
    ]
    t = Table(rows, colWidths=[1.02*inch,1.04*inch,.88*inch,1.04*inch,.94*inch,2.18*inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), LIGHT_GRAY),
        ("GRID", (0,0), (-1,-1), .35, MID_GRAY),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("ALIGN", (0,0), (4,-1), "CENTER"),
        ("LEFTPADDING", (0,0), (-1,-1), 4),
        ("RIGHTPADDING", (0,0), (-1,-1), 4),
        ("TOPPADDING", (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
    ]))
    return t


def decision_rows(board):
    ranked = board.get("ranked_single_step_opportunities") or []
    best = board.get("best_actionable_trade") or {}
    etienne = next((r for r in ranked if "Travis Etienne" in str(r.get("description") or "")), {})
    explore = board.get("best_trade_to_explore") or {}
    rows = []
    if best:
        rows.append((best, "PURSUE", "Best stable win-now consolidation"))
    if etienne:
        rows.append((etienne, "CONSIDER", "Lower-cost current-production upgrade"))
    if explore:
        rows.append((explore, "EXPLORE", "Elite upside; price still uncertain"))
    return rows


def opportunity_story(s, row, action, profile, ref, focal_team_name):
    m = trade_metrics(row)
    current = focal_channel(row, "current")
    future = focal_channel(row, "future")
    stab = m["stability"]
    if current > 0 and future < 0:
        tradeoff = "This is a win-now consolidation trade: current competitive gains outweigh a modeled future-value sacrifice."
    elif current > 0 and future > 0:
        tradeoff = "The current and future channels both improve in the modeled outcome."
    elif current < 0 and future > 0:
        tradeoff = "This is primarily a future-value move that sacrifices current production."
    else:
        tradeoff = "The current-versus-future tradeoff is mixed."

    header = Table([[P(s, target_name(row), "FS_Section"), action_box(s, action)]], colWidths=[6.48*inch,.9*inch])
    header.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(0,0),LIGHT_GRAY),
        ("BOX",(0,0),(-1,-1),.6,MID_GRAY),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("LEFTPADDING",(0,0),(-1,-1),6),("RIGHTPADDING",(0,0),(-1,-1),6),
        ("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),
    ]))

    cards = [
        kpi_card(s, "Expected Wins", f"{m['expected_wins']:+.2f}", "positive" if m["expected_wins"] >= 0 else "negative", 1.40*inch),
        kpi_card(s, "Championship", f"{m['championship']*100:+.1f} pts", "positive" if m["championship"] >= 0 else "warning", 1.40*inch),
        kpi_card(s, "2026 Value", f"{m['redraft_delta']:+,.0f}", "positive" if m["redraft_delta"] >= 0 else "negative", 1.40*inch),
        kpi_card(s, "Dynasty Value", f"{m['dynasty_delta']:+,.0f}", "positive" if m["dynasty_delta"] >= 0 else "negative", 1.40*inch),
        kpi_card(s, "Seller Utility", f"{m['counterparty_utility']:+,.0f}", "positive" if m["counterparty_utility"] > 0 else "negative", 1.40*inch),
    ]
    card_grid = Table([cards], colWidths=[1.45*inch]*5)
    card_grid.setStyle(TableStyle([
        ("VALIGN",(0,0),(-1,-1),"TOP"),
        ("LEFTPADDING",(0,0),(-1,-1),1),("RIGHTPADDING",(0,0),(-1,-1),1),
    ]))

    other = (
        f"The seller gives up current competitive value ({counterparty_channel(row,'current'):+,.0f}) "
        f"but gains future franchise value ({counterparty_channel(row,'future'):+,.0f})."
    )
    confidence = ""
    if stab:
        confidence = (
            f"{str(stab.get('classification') or '').replace('_',' ')}. "
            f"{clean(focal_team_name)} range {safe_float(stab.get('score_min')):+,.0f} to {safe_float(stab.get('score_max')):+,.0f}; "
            f"seller range {safe_float(stab.get('counterparty_score_min')):+,.0f} to {safe_float(stab.get('counterparty_score_max')):+,.0f}."
        )

    return [
        header, Spacer(1,4),
        P(s, f"<b>Structure:</b> {structure(row)}", "FS_Body"),
        Spacer(1,3), profile_table(s, profile, ref), Spacer(1,3),
        P(s, profile_sentence(profile, ref, focal_team_name), "FS_Small"),
        Spacer(1,4), card_grid, Spacer(1,4),
        P(s, "ANALYST VIEW", "FS_Section"),
        P(s, tradeoff, "FS_Body"),
        P(s, "WHY THIS PLAYER SPECIFICALLY", "FS_Section"),
        P(s, profile_sentence(profile, ref, focal_team_name), "FS_Body"),
        P(s, "WHY THE OTHER MANAGER MIGHT SAY YES", "FS_Section"),
        P(s, other + f" Negotiation fit: {m['acceptance_fit']}.", "FS_Body"),
        P(s, "CONFIDENCE", "FS_Section"),
        P(s, confidence or "No separate repeated-seed confirmation is attached to this row.", "FS_Small"),
    ]


def render(board_path, output, assets_path=DEFAULT_ASSETS, projections_path=DEFAULT_PROJECTIONS, rosters_path=DEFAULT_ROSTERS, focus_user_id=None):
    board = load(board_path)
    assets = load(assets_path)
    projections = load(projections_path)
    rosters = load(rosters_path)
    if not board.get("focus_user_id"):
        team_name = str(board.get("team_name") or "")
        owner = next(
            (
                p.get("current_owner_user_id")
                for p in (assets.get("players") or [])
                if str(p.get("current_owner_team") or "") == team_name and p.get("current_owner_user_id")
            ),
            None,
        )
        if owner:
            board = dict(board)
            board["focus_user_id"] = str(owner)
    indexes = build_player_indexes(assets, projections)
    s = styles()

    doc = SimpleDocTemplate(
        str(output), pagesize=letter,
        leftMargin=.46*inch, rightMargin=.46*inch,
        topMargin=.38*inch, bottomMargin=.44*inch,
    )

    rows = decision_rows(board)
    focal_team_name = str(board.get("team_name") or "Franchise")
    story = [
        P(s, "FSFFL OPPORTUNITY REPORT", "FS_Title"),
        P(s, f"{board.get('team_name') or 'Franchise'} | Competitive state: {board.get('competitive_state') or board.get('team_state') or 'N/A'} | Strategic posture: {(board.get('strategic_posture') or {}).get('selected_posture') or 'AUTO'} | {board.get('model_version') or ''}", "FS_Sub"),
        Spacer(1,5),
    ]

    if rows:
        top = rows[0][0]
        banner = Table([[
            P(s, "BOTTOM LINE", "FS_WhiteLabel"),
            P(s, f"<b>Pursue {target_name(top)} first.</b> It is the strongest current opportunity after football impact, future-value cost, counterparty economics and repeated-seed stability are considered together.", "FS_Body")
        ]], colWidths=[1.2*inch,6.18*inch])
        banner.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(0,0),NAVY),("BACKGROUND",(1,0),(1,0),LIGHT_GREEN),
            ("BOX",(0,0),(-1,-1),.7,MID_GRAY),("VALIGN",(0,0),(-1,-1),"MIDDLE"),
            ("LEFTPADDING",(0,0),(-1,-1),7),("RIGHTPADDING",(0,0),(-1,-1),7),
            ("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),6),
        ]))
        story += [banner, Spacer(1,6)]

    story += [P(s, "DECISION BOARD", "FS_Section")]
    table_rows = [[
        P(s,"TARGET","FS_CardLabel"),P(s,"ACTION","FS_CardLabel"),P(s,"PLAYER SNAPSHOT","FS_CardLabel"),
        P(s,"EXP. WINS","FS_CardLabel"),P(s,"TITLE","FS_CardLabel"),P(s,"DYNASTY","FS_CardLabel")
    ]]
    for row, action, _ in rows:
        m = trade_metrics(row)
        profile = player_profile(row, indexes)
        snap = (
            f"{profile.get('position') or ''} | Age {profile.get('age') if profile.get('age') is not None else 'N/A'} | "
            f"{safe_float(profile.get('projected_ppg')):.1f} PPG | "
            f"{profile.get('position') or ''}{int(profile.get('dynasty_position_rank') or 0)} dynasty"
        )
        table_rows.append([
            P(s,target_name(row),"FS_Body"),action_box(s,action),P(s,snap,"FS_Small"),
            P(s,f"{m['expected_wins']:+.2f}","FS_Body"),P(s,f"{m['championship']*100:+.1f} pts","FS_Body"),
            P(s,f"{m['dynasty_delta']:+,.0f}","FS_Body")
        ])
    dt = Table(table_rows, colWidths=[1.0*inch,.9*inch,2.65*inch,.68*inch,.72*inch,.8*inch])
    dt.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),LIGHT_GRAY),("GRID",(0,0),(-1,-1),.35,MID_GRAY),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),("LEFTPADDING",(0,0),(-1,-1),4),
        ("RIGHTPADDING",(0,0),(-1,-1),4),("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),
    ]))
    story += [dt]

    for i, (row, action, _) in enumerate(rows):
        story += [PageBreak()]
        profile = player_profile(row, indexes)
        ref = current_position_reference(profile, board, rosters, indexes, focus_user_id)
        story += opportunity_story(s, row, action, profile, ref, focal_team_name)

    story += [PageBreak(), P(s, "WATCHLIST & REPORTING NOTES", "FS_Title")]
    sensitive = board.get("simulation_sensitive_trade_watchlist") or []
    story += [P(s, "SIMULATION-SENSITIVE - DO NOT PROMOTE", "FS_Section")]
    sr = [[P(s,"TRADE","FS_CardLabel"),P(s,"POINT ESTIMATE","FS_CardLabel"),P(s,"WHY WITHHELD","FS_CardLabel")]]
    for row in sensitive[:5]:
        m = trade_metrics(row)
        st = m["stability"]
        sr.append([
            P(s,structure(row),"FS_Small"),
            P(s,f"HSG {m['utility']:+,.0f}; seller {m['counterparty_utility']:+,.0f}","FS_Small"),
            P(s,(
                f"HSG {safe_float(st.get('score_min')):+,.0f} to {safe_float(st.get('score_max')):+,.0f}; "
                f"seller {safe_float(st.get('counterparty_score_min')):+,.0f} to {safe_float(st.get('counterparty_score_max')):+,.0f}. "
                "At least one side changes sign across repeated simulations."
            ),"FS_Small"),
        ])
    stbl = Table(sr, colWidths=[3.5*inch,1.5*inch,2.35*inch])
    stbl.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),LIGHT_RED),("GRID",(0,0),(-1,-1),.35,MID_GRAY),
        ("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),4),("RIGHTPADDING",(0,0),(-1,-1),4),
        ("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),
    ]))
    story += [stbl, Spacer(1,7)]
    story += [
        P(s, "HOW TO READ PLAYER CONTEXT", "FS_Section"),
        P(s, f"Dynasty position rank and overall rank come from the governed FSFFL asset-value layer. 2026 projected PPG and projection position rank come from the canonical simulator weekly-projection input. The comparison player is the lowest-projected current {clean(focal_team_name)} starter at the same position when one exists, otherwise the best active same-position fallback; taxi and reserve are excluded. These fields explain the opportunity; they do not create a second ranking or change Opportunity Engine utility.", "FS_Small"),
        Spacer(1,4),
        P(s, "REPORTING NOTE", "FS_Section"),
        P(s, "This PDF is presentation only. Opportunity rankings, Shared Decision Utility, player and pick values, simulation results, Trade Decision authority and bilateral-stability rules remain unchanged.", "FS_Small"),
    ]

    doc.build(story, onFirstPage=lambda c,d: footer(c, f"{MODEL_VERSION} | Reporting Layer 1.0 | Model output unchanged"),
              onLaterPages=lambda c,d: footer(c, f"{MODEL_VERSION} | Reporting Layer 1.0 | Model output unchanged"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--assets", default=DEFAULT_ASSETS)
    ap.add_argument("--projections", default=DEFAULT_PROJECTIONS)
    ap.add_argument("--rosters", default=DEFAULT_ROSTERS)
    ap.add_argument("--focus-user-id")
    a = ap.parse_args()
    render(a.input, Path(a.output), a.assets, a.projections, a.rosters, a.focus_user_id)
    print(json.dumps({"renderer_model_version": MODEL_VERSION, "pdf": a.output}, indent=2))


if __name__ == "__main__":
    main()
