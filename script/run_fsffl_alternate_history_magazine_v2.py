#!/usr/bin/env python3
"""Publication polish for the deterministic FSFFL Alternate History magazine.

This module intentionally reuses the validated 1.2 publication dataset builder
and changes only reader-facing enrichment / PDF presentation. It does not alter
historical replay, branch probabilities, transaction policy, draft policy,
lineup logic, MaxPF, roster legality, or Simulator 1.0 inputs.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, List, Tuple

import alternate_history_engine as ah
import run_fsffl_alternate_history_magazine as base


def _safe(value: Any) -> str:
    """Make arbitrary team/player text safe for ReportLab's built-in WinAnsi fonts."""
    text = str(value if value is not None else "")
    text = text.replace("—", "-").replace("–", "-").replace("→", "->")
    text = text.replace("’", "'").replace("“", '"').replace("”", '"')
    return text.encode("cp1252", "ignore").decode("cp1252")


def _league_season(*args, **kwargs) -> Dict[str, Any]:
    report = _ORIG_LEAGUE_SEASON(*args, **kwargs)
    teams = args[3] if len(args) > 3 else kwargs.get("teams", {})
    for row in report.get("actual_standings") or []:
        rid = str(row.get("roster_id") or "")
        row["team"] = teams.get(rid, f"Roster {rid}")
    return report


def _validate_publication(report: Dict[str, Any]) -> None:
    _ORIG_VALIDATE(report)
    if len(report.get("drafts") or []) < len(report.get("seasons") or []):
        raise ah.AlternateHistoryError("publication requires a following rookie draft for every completed season")
    for season in report.get("seasons") or []:
        modal = season.get("modal_postseason") or {}
        post = modal.get("postseason") or {}
        if not post.get("championship"):
            raise ah.AlternateHistoryError(f"{season['season']} publication lacks modal championship result")
    if not report.get("transactions"):
        raise ah.AlternateHistoryError("publication transaction audit is empty")


def _render_pdf(report: Dict[str, Any], path: Path) -> None:
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import (
            BaseDocTemplate, Frame, KeepTogether, PageBreak, PageTemplate,
            Paragraph, Spacer, Table, TableStyle,
        )
    except ImportError as exc:
        raise ah.AlternateHistoryError("reportlab is required to render the Alternate History magazine PDF") from exc

    styles = getSampleStyleSheet()
    ink = colors.HexColor("#172133")
    accent = colors.HexColor("#B11F2E")
    gold = colors.HexColor("#C9972B")
    pale = colors.HexColor("#F4F1EA")
    pale_red = colors.HexColor("#F8EAEC")
    rule = colors.HexColor("#D6D6D6")
    white = colors.white

    title = ParagraphStyle("MagazineTitleV2", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=29, leading=31, textColor=ink, alignment=TA_LEFT, spaceAfter=12)
    kicker = ParagraphStyle("KickerV2", parent=styles["BodyText"], fontName="Helvetica-Bold", fontSize=9, leading=11, textColor=accent, spaceAfter=6)
    deck = ParagraphStyle("DeckV2", parent=styles["BodyText"], fontName="Helvetica", fontSize=12, leading=16.5, textColor=ink, spaceAfter=10)
    h1 = ParagraphStyle("H1V2", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=20, leading=23, textColor=accent, spaceBefore=3, spaceAfter=9)
    h2 = ParagraphStyle("H2V2", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=12.5, leading=15, textColor=ink, spaceBefore=7, spaceAfter=5)
    body = ParagraphStyle("BodyV2", parent=styles["BodyText"], fontName="Helvetica", fontSize=9.4, leading=13.2, textColor=ink, spaceAfter=6)
    body_bold = ParagraphStyle("BodyBoldV2", parent=body, fontName="Helvetica-Bold")
    small = ParagraphStyle("SmallV2", parent=body, fontSize=8.3, leading=10.8, spaceAfter=4)
    tiny = ParagraphStyle("TinyV2", parent=body, fontSize=7.6, leading=9.6, spaceAfter=3)
    callout = ParagraphStyle("CalloutV2", parent=body, fontName="Helvetica-Bold", fontSize=11.2, leading=15, textColor=ink, borderColor=gold, borderWidth=1, borderPadding=9, backColor=pale, spaceBefore=5, spaceAfter=9)

    class Doc(BaseDocTemplate):
        pass

    doc = Doc(str(path), pagesize=letter, leftMargin=0.58*inch, rightMargin=0.58*inch, topMargin=0.52*inch, bottomMargin=0.55*inch, title="FSFFL Alternate History")
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="normal")

    def footer(canvas, d):
        canvas.saveState()
        canvas.setStrokeColor(rule)
        canvas.line(doc.leftMargin, 0.38*inch, letter[0]-doc.rightMargin, 0.38*inch)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(ink)
        canvas.drawString(doc.leftMargin, 0.23*inch, "FSFFL ALTERNATE HISTORY")
        canvas.drawRightString(letter[0]-doc.rightMargin, 0.23*inch, str(d.page))
        canvas.restoreState()

    doc.addPageTemplates(PageTemplate(id="main", frames=[frame], onPage=footer))
    story: List[Any] = []
    scenario = report["scenario"]
    focus = report["focus_franchise"]
    fdelta = focus["simulator_deltas"]

    story += [
        Spacer(1, 0.22*inch),
        Paragraph("FSFFL ALTERNATE HISTORY / SPECIAL EDITION", kicker),
        Paragraph(_safe(scenario.get("title") or "Alternate History"), title),
        Paragraph(_safe(f"One decision in {scenario['fork_season']} Week {scenario['fork_week']} sends the league into a different timeline. This edition follows the consequences season by season - standings, playoffs, rookie drafts, transactions, and the league that exists today."), deck),
        Paragraph(_safe(f"THE VERDICT: {focus['team']} reaches the present with {base._num(focus['roster_divergence_score'],1)}/100 roster divergence. Expected wins change by {base._num(fdelta.get('expected_wins'),2)}, playoff probability by {base._pct(fdelta.get('playoff_probability'))}, and championship probability by {base._pct(fdelta.get('championship_probability'))}."), callout),
        Paragraph("What this report is - and is not", h2),
        Paragraph("Every football fact and alternate outcome shown here comes from retained model state, archived Sleeper history, or Simulator 1.0 output. Completed NFL results remain fixed. The prose and layout are deterministic publication logic; they do not invent missing events or use hindsight.", body),
        Spacer(1, 0.05*inch),
        Paragraph(_safe(f"Edition: {report['configuration']['particles']} historical particles / {report['configuration']['simulator_sims']} Simulator draws / probability mass {base._pct(report['summary']['probability_mass'])}."), small),
        PageBreak(),
    ]

    story += [Paragraph("THE BUTTERFLY BOARD", h1), Paragraph("The biggest model-identified changes between actual history and the alternate timeline.", deck)]
    for e in report["butterflies"][:14]:
        story.append(Paragraph(_safe(f"{e['rank']}. {e['kind'].replace('_',' ').title()} - {e['sentence']}"), body))
    story += [Spacer(1, 8), Paragraph("Reading the probabilities", h2), Paragraph("A season table shows probability-weighted expected standings. The playoff path is the single most common exact bracket among retained branches. Draft tables show the complete actual draft and the model's most likely alternate selection; when no alternate selection was recorded, the historical selection is treated as unchanged rather than silently omitted.", body), PageBreak()]

    teams = {r["roster_id"]: r["team"] for r in report["present_day"]["rosters"]}

    def team_name(rid: Any) -> str:
        return _safe(teams.get(str(rid), f"Roster {rid}"))

    def game_text(game: Dict[str, Any]) -> str:
        if not game:
            return "Not available"
        return f"{team_name(game.get('winner'))} def. {team_name(game.get('loser'))}"

    for chapter in report["season_chapters"]:
        season = next(x for x in report["seasons"] if x["season"] == chapter["season"])
        story += [Paragraph(_safe(f"{chapter['season']}: THE TIMELINE IN MOTION"), h1)]
        for i, p in enumerate(chapter["paragraphs"]):
            story.append(Paragraph(_safe(p), deck if i == 0 else body))

        table = [["ALT", "TEAM", "ACT", "EXP W", "EXP PF", "PLAYOFF", "TITLE"]]
        for r in season["alternate_expected_standings"]:
            table.append([
                r["alternate_rank"], _safe(r["team"]), r["actual_seed"] or "-",
                base._num(r["expected_wins"],1), base._num(r["expected_points_for"],0),
                base._pct(r["playoff_probability"]), base._pct(r["championship_probability"]),
            ])
        t = Table(table, colWidths=[0.34*inch,2.30*inch,0.42*inch,0.54*inch,0.62*inch,0.72*inch,0.62*inch], repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,0),ink),("TEXTCOLOR",(0,0),(-1,0),white),
            ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTNAME",(0,1),(-1,-1),"Helvetica"),
            ("FONTSIZE",(0,0),(-1,-1),7.6),("LEADING",(0,0),(-1,-1),9.4),
            ("GRID",(0,0),(-1,-1),0.25,rule),("VALIGN",(0,0),(-1,-1),"MIDDLE"),
            ("ROWBACKGROUNDS",(0,1),(-1,-1),[white,pale]),("TOPPADDING",(0,0),(-1,-1),3.1),("BOTTOMPADDING",(0,0),(-1,-1),3.1),
        ]))
        story += [t, Spacer(1,7)]

        modal = season.get("modal_postseason") or {}
        post = modal.get("postseason") or {}
        story += [Paragraph(_safe(f"PLAYOFF RESULTS - MOST LIKELY EXACT BRACKET ({base._pct(modal.get('probability'))})"), h2)]
        qfs = post.get("quarterfinals") or []
        sfs = post.get("semifinals") or []
        championship = post.get("championship") or {}
        rounds = []
        if qfs:
            rounds.append([Paragraph("QUARTERFINALS", body_bold), Paragraph("<br/>".join(_safe(game_text(g)) for g in qfs), small)])
        if sfs:
            rounds.append([Paragraph("SEMIFINALS", body_bold), Paragraph("<br/>".join(_safe(game_text(g)) for g in sfs), small)])
        rounds.append([Paragraph("CHAMPIONSHIP", body_bold), Paragraph(_safe(game_text(championship)), small)])
        br = Table(rounds, colWidths=[1.25*inch,5.55*inch])
        br.setStyle(TableStyle([("BACKGROUND",(0,0),(0,-1),pale_red),("BOX",(0,0),(-1,-1),0.4,rule),("INNERGRID",(0,0),(-1,-1),0.25,rule),("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),6),("RIGHTPADDING",(0,0),(-1,-1),6),("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5)]))
        story += [br]

        if chapter["changed_transactions"]:
            story += [Paragraph("MAJOR TRANSACTION DEVIATIONS", h2)]
            for tx in chapter["changed_transactions"][:4]:
                top = (tx.get("outcomes") or [{}])[0]
                story.append(Paragraph(_safe(f"{tx['actual_transaction']} - altered/removed {base._pct(tx['probability_changed_or_removed'])}; most common branch outcome: {top.get('outcome','n/a')} ({base._pct(top.get('probability'))})."), small))
        story.append(PageBreak())

        draft = chapter.get("following_draft")
        if draft and draft.get("picks"):
            picks = draft["picks"]
            changed_count = sum(1 for p in picks if p.get("most_likely_selection_changed"))
            story += [Paragraph(_safe(f"{draft['draft_season']} ROOKIE DRAFT"), h1), Paragraph(_safe(f"The complete three-round draft following the {chapter['season']} season. {changed_count} of 36 picks have a different most-likely player than actual history in this alternate timeline."), deck)]
            for chunk_index in range(0, len(picks), 18):
                chunk = picks[chunk_index:chunk_index+18]
                drows = [["PICK", "ACTUAL SELECTION", "MOST LIKELY ALTERNATE", "P"]]
                for p in chunk:
                    alt = (p.get("alternate_choices") or [{}])[0]
                    if alt and alt.get("player_name"):
                        alt_name = alt.get("player_name")
                        prob = base._pct(alt.get("probability"))
                    else:
                        alt_name = p.get("actual_player_name") or "-"
                        prob = "UNCHANGED"
                    drows.append([p["pick"], _safe(p.get("actual_player_name") or "-"), _safe(alt_name), prob])
                dt = Table(drows, colWidths=[0.58*inch,2.35*inch,2.55*inch,0.95*inch], repeatRows=1)
                dt.setStyle(TableStyle([
                    ("BACKGROUND",(0,0),(-1,0),accent),("TEXTCOLOR",(0,0),(-1,0),white),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
                    ("FONTSIZE",(0,0),(-1,-1),8.1),("LEADING",(0,0),(-1,-1),9.8),("GRID",(0,0),(-1,-1),0.25,rule),
                    ("ROWBACKGROUNDS",(0,1),(-1,-1),[white,pale]),("VALIGN",(0,0),(-1,-1),"MIDDLE"),
                    ("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),
                ]))
                story.append(dt)
                if chunk_index + 18 < len(picks):
                    story += [Spacer(1,6), Paragraph("Round 2/3 continued", small), PageBreak(), Paragraph(_safe(f"{draft['draft_season']} ROOKIE DRAFT - CONTINUED"), h1)]
            story.append(PageBreak())

    story += [Paragraph("WHERE THE LEAGUE STANDS NOW", h1), Paragraph("Simulator 1.0 power rankings after the alternate timeline has fully propagated to the present day. The ordering is deterministic: expected wins, then expected points, championship probability, and playoff probability.", deck)]
    prow = [["#", "TEAM", "ACT #", "EXP W", "EXP PF", "PLAYOFF", "TITLE"]]
    for r in report["present_day"]["power_rankings"]["teams"]:
        a = r["alternate"]
        prow.append([r["alternate_power_rank"], _safe(r["team"]), r["actual_power_rank"], base._num(a.get("expected_wins"),1), base._num(a.get("expected_points_for"),0), base._pct(a.get("playoff_probability")), base._pct(a.get("championship_probability"))])
    pt = Table(prow, colWidths=[0.34*inch,2.38*inch,0.48*inch,0.54*inch,0.62*inch,0.72*inch,0.62*inch], repeatRows=1)
    pt.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),ink),("TEXTCOLOR",(0,0),(-1,0),white),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
        ("FONTSIZE",(0,0),(-1,-1),7.8),("LEADING",(0,0),(-1,-1),9.7),("GRID",(0,0),(-1,-1),0.25,rule),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[white,pale]),("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),
    ]))
    story += [pt, Spacer(1,10)]
    movers = sorted(report["present_day"]["power_rankings"]["teams"], key=lambda r: -abs(int(r.get("power_rank_change") or 0)))[:4]
    story += [Paragraph("BIGGEST PRESENT-DAY MOVERS", h2)]
    for r in movers:
        change = int(r.get("power_rank_change") or 0)
        direction = "up" if change > 0 else "down" if change < 0 else "unchanged"
        story.append(Paragraph(_safe(f"{r['team']}: actual No. {r['actual_power_rank']} -> alternate No. {r['alternate_power_rank']} ({abs(change)} spots {direction})."), body))
    story.append(PageBreak())

    rosters = report["present_day"]["rosters"]
    for start in range(0, len(rosters), 3):
        story += [Paragraph("THE 12 ROSTERS" if start == 0 else "THE 12 ROSTERS - CONTINUED", h1)]
        if start == 0:
            story.append(Paragraph("Each card shows the single most common exact present-day roster across retained branches. IN/OUT identifies the most important differences from the actual current roster.", deck))
        for r in rosters[start:start+3]:
            player_text = ", ".join(_safe(x["player_name"]) for x in r["modal_roster"])
            changes = []
            if r["gained_vs_actual"]:
                changes.append("IN: " + ", ".join(_safe(x["player_name"]) for x in r["gained_vs_actual"][:7]))
            if r["lost_vs_actual"]:
                changes.append("OUT: " + ", ".join(_safe(x["player_name"]) for x in r["lost_vs_actual"][:7]))
            card_data = [[Paragraph(_safe(f"{r['team']} - modal roster {base._pct(r['modal_probability'])}"), h2)], [Paragraph(player_text or "No roster players retained.", body)]]
            if changes:
                card_data.append([Paragraph("<b>DIFFERENCES:</b> " + _safe(" | ".join(changes)), small)])
            card = Table(card_data, colWidths=[6.75*inch])
            card.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),pale_red),("BOX",(0,0),(-1,-1),0.45,rule),("LEFTPADDING",(0,0),(-1,-1),8),("RIGHTPADDING",(0,0),(-1,-1),8),("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5)]))
            story += [KeepTogether(card), Spacer(1,8)]
        story.append(PageBreak())

    story += [
        Paragraph("METHODOLOGY & AUDIT NOTE", h1),
        Paragraph("This publication is a deterministic rendering of the same validated Alternate History particles used by the technical report. League-wide standings and postseason results are read from each branch's historical ledger; rookie drafts are read from branch draft state; transactions are joined to archived Sleeper history; present-day rosters are read from branch roster state; and power rankings are calculated from the same Simulator 1.0 outputs. No language model supplies football facts or chooses outcomes for the publication.", body),
        Paragraph("The standings columns labeled ALT are probability-weighted expected ordering across retained particles. The playoff section intentionally labels its bracket as modal: it is the most common exact bracket, not a deterministic claim that every branch produces the same postseason. Draft rows marked UNCHANGED have no alternate selection recorded by the branch draft state and therefore preserve actual history.", body),
        Paragraph(_safe(f"Configuration: {report['configuration']['particles']} historical particles, {report['configuration']['simulator_sims']} Simulator draws, seed {report['configuration']['seed']}; retained probability mass {base._pct(report['summary']['probability_mass'])}."), body),
    ]
    doc.build(story)


_ORIG_LEAGUE_SEASON = base._league_season
_ORIG_VALIDATE = base._validate_publication
base._league_season = _league_season
base._validate_publication = _validate_publication
base._render_pdf = _render_pdf


def run(scenario_path: Path, *, particles: int, n_sims: int, seed: int) -> Tuple[Path, Path]:
    return base.run(scenario_path, particles=particles, n_sims=n_sims, seed=seed)


def main() -> None:
    p = argparse.ArgumentParser(description="Render polished deterministic FSFFL Alternate History magazine")
    p.add_argument("scenario", type=Path)
    p.add_argument("--particles", type=int, default=base.DEFAULT_PARTICLES)
    p.add_argument("--sims", type=int, default=base.DEFAULT_SIMS)
    p.add_argument("--seed", type=int, default=base.DEFAULT_SEED)
    a = p.parse_args()
    run(a.scenario, particles=a.particles, n_sims=a.sims, seed=a.seed)


if __name__ == "__main__":
    main()
