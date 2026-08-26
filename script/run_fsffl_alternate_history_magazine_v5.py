#!/usr/bin/env python3
"""Alternate History magazine v5: reader-first publication layer.

This module changes presentation only. It preserves the validated historical
particle model, coherent sequential draft path from v4, transaction policy,
and Simulator outputs while translating them into plain-language league story.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, List

import run_fsffl_alternate_history_magazine as base
import run_fsffl_alternate_history_magazine_v4 as v4

_ORIG_LEAGUE_SEASON = base._league_season
_ORIG_PRESENT_ROSTERS = base._present_rosters
_ORIG_SEASON_STORY = base._season_story
_ORIG_BUTTERFLIES = base._butterflies
_ORIG_VALIDATE = base._validate_publication


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100.0 * float(x):.0f}%"


def _league_season(*args, **kwargs):
    row = _ORIG_LEAGUE_SEASON(*args, **kwargs)
    for team in row.get("alternate_expected_standings") or []:
        seeds = team.get("seed_distribution") or []
        mode = seeds[0] if seeds else {}
        team["most_likely_seed"] = int(mode.get("value")) if str(mode.get("value") or "").isdigit() else None
        team["most_likely_seed_probability"] = mode.get("probability")
    return row


def _present_rosters(groups, total: int, names: Dict[str, str], teams: Dict[str, str]):
    rows = _ORIG_PRESENT_ROSTERS(groups, total, names, teams)
    actual = base._actual_team_map()
    for row in rows:
        rid = str(row["roster_id"])
        actual_ids = {str(x) for x in ((actual.get(rid) or {}).get("players") or [])}
        consensus = {str(x["player_id"]): x for x in row.get("consensus_roster") or []}
        # Reader-facing roster changes are based on player membership, not on a
        # low-probability exact modal roster.
        row["likely_gained_vs_actual"] = [
            x for pid, x in consensus.items() if pid not in actual_ids
        ]
        retention = {}
        for x in row.get("modal_roster") or []:
            retention[str(x["player_id"])] = float(x.get("membership_probability") or 0.0)
        for x in row.get("lost_vs_actual") or []:
            retention[str(x["player_id"])] = float(x.get("retention_probability") or 0.0)
        row["likely_lost_vs_actual"] = [
            {"player_id": pid, "player_name": names.get(pid, pid), "retention_probability": round(retention.get(pid, 0.0), 8)}
            for pid in sorted(actual_ids)
            if retention.get(pid, 0.0) < 0.5
        ]
        row["reader_roster_semantics"] = "players present in at least half of retained timelines"
    return rows


def _season_story(season: Dict[str, Any], next_draft: Dict[str, Any] | None, txs: List[Dict[str, Any]]):
    y = season["season"]
    rows = season.get("alternate_expected_standings") or []
    champ = (season.get("champion_distribution") or [{}])[0]
    paragraphs: List[str] = []
    if champ:
        paragraphs.append(
            f"In the alternate {y} season, {champ.get('team')} is the most common champion, winning the league in {_pct(champ.get('probability'))} of retained timelines."
        )
    movers = sorted(
        [r for r in rows if r.get("actual_seed") and r.get("most_likely_seed")],
        key=lambda r: -abs(int(r["most_likely_seed"]) - int(r["actual_seed"])),
    )[:3]
    if movers:
        bits = [
            f"{r['team']} moves from No. {r['actual_seed']} to most often No. {r['most_likely_seed']} ({_pct(r.get('most_likely_seed_probability'))})"
            for r in movers
        ]
        paragraphs.append("The biggest standings swings: " + "; ".join(bits) + ".")
    changed = [t for t in txs if str(t.get("season")) == str(y) and float(t.get("probability_changed_or_removed") or 0) >= 0.10][:5]
    if changed:
        paragraphs.append(f"{len(changed)} important real-life roster moves are no longer automatic because the teams reach those decision points with different rosters and needs.")
    if next_draft and next_draft.get("picks"):
        changed_picks = sum(1 for p in next_draft["picks"] if p.get("most_likely_selection_changed"))
        paragraphs.append(f"That new finish changes who controls the next rookie picks. In the coherent {next_draft['draft_season']} representative draft, {changed_picks} of 36 player selections differ from real history.")
    return {"season": y, "paragraphs": paragraphs, "major_seed_swings": movers, "changed_transactions": changed, "following_draft": next_draft}


def _butterflies(seasons, drafts, transactions, power):
    events = _ORIG_BUTTERFLIES(seasons, drafts, transactions, power)
    # Replace decimal expected-seed prose with outcomes a league reader can
    # visualize. Draft prose identifies both the selecting franchise and player.
    season_lookup = {s["season"]: s for s in seasons}
    for e in events:
        if e.get("kind") == "SEED_SWING":
            row = next((r for r in season_lookup.get(str(e.get("season")), {}).get("alternate_expected_standings", []) if r.get("team") == e.get("team")), None)
            if row and row.get("most_likely_seed"):
                e["sentence"] = f"{row['team']} was actually the No. {row['actual_seed']} seed; in the alternate timeline its most common finish is No. {row['most_likely_seed']} ({_pct(row.get('most_likely_seed_probability'))})."
    return events


def _validate_publication(report: Dict[str, Any]) -> None:
    _ORIG_VALIDATE(report)
    for season in report.get("seasons") or []:
        for row in season.get("alternate_expected_standings") or []:
            if row.get("most_likely_seed") is None:
                raise base.ah.AlternateHistoryError(f"{season['season']} missing reader-facing most-likely seed for {row.get('team')}")
    for roster in report.get("present_day", {}).get("rosters") or []:
        if roster.get("reader_roster_semantics") != "players present in at least half of retained timelines":
            raise base.ah.AlternateHistoryError(f"missing consensus roster semantics for {roster.get('team')}")


def _render_pdf(report: Dict[str, Any], path: Path) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether

    styles = getSampleStyleSheet()
    ink = colors.HexColor("#172133"); accent = colors.HexColor("#B11F2E"); pale = colors.HexColor("#F4F1EA"); white = colors.white
    title = ParagraphStyle("title", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=27, leading=30, textColor=ink, spaceAfter=12)
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=19, leading=22, textColor=accent, spaceBefore=6, spaceAfter=9)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=11, leading=14, textColor=ink, spaceBefore=6, spaceAfter=4)
    body = ParagraphStyle("body", parent=styles["BodyText"], fontName="Helvetica", fontSize=9, leading=12.5, textColor=ink, spaceAfter=6)
    small = ParagraphStyle("small", parent=body, fontSize=7.3, leading=9.2)
    deck = ParagraphStyle("deck", parent=body, fontSize=12, leading=16, spaceAfter=10)
    doc = SimpleDocTemplate(str(path), pagesize=letter, leftMargin=.55*inch, rightMargin=.55*inch, topMargin=.55*inch, bottomMargin=.55*inch, title="FSFFL Alternate History V2")
    story = [Paragraph("FSFFL ALTERNATE HISTORY", h2), Paragraph(report["scenario"].get("title") or "Alternate History", title), Paragraph("One changed decision. Three seasons of consequences. This edition follows the league as standings, playoff paths, rookie picks, trades and present-day rosters change around it.", deck)]
    focus = report["focus_franchise"]
    story += [Paragraph("THE BOTTOM LINE", h1), Paragraph(f"By the present day, {focus['team']}'s roster has materially diverged from real history. The pages that follow focus on what changed and why; model mechanics are kept to the final audit note.", deck), PageBreak()]

    story += [Paragraph("THE BIGGEST BUTTERFLY EFFECTS", h1)]
    for e in report.get("butterflies", [])[:10]:
        story.append(Paragraph(f"<b>{e['rank']}.</b> {e['sentence']}", body))
    story.append(PageBreak())

    for chapter in report.get("season_chapters") or []:
        season = next(s for s in report["seasons"] if s["season"] == chapter["season"])
        story.append(Paragraph(f"{chapter['season']}: WHAT CHANGED", h1))
        for p in chapter.get("paragraphs") or []:
            story.append(Paragraph(p, deck if p == chapter["paragraphs"][0] else body))
        rows = [["Alt", "Team", "Actual", "Most likely", "Playoffs", "Title"]]
        for r in season["alternate_expected_standings"]:
            rows.append([r["alternate_rank"], r["team"], f"#{r['actual_seed']}" if r.get("actual_seed") else "-", f"#{r['most_likely_seed']} ({_pct(r.get('most_likely_seed_probability'))})", _pct(r["playoff_probability"]), _pct(r["championship_probability"])])
        t = Table(rows, colWidths=[.35*inch,2.25*inch,.55*inch,1.15*inch,.7*inch,.6*inch], repeatRows=1)
        t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),ink),("TEXTCOLOR",(0,0),(-1,0),white),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,-1),7),("GRID",(0,0),(-1,-1),.25,colors.lightgrey),("ROWBACKGROUNDS",(0,1),(-1,-1),[white,pale])]))
        story += [t, Spacer(1,8)]
        draft = chapter.get("following_draft")
        if draft:
            story.append(Paragraph(f"THE {draft['draft_season']} ROOKIE DRAFT", h2))
            story.append(Paragraph("The alternate column is one complete, possible draft from a retained timeline. A player selected earlier cannot appear again later.", body))
            drows = [["Pick", "Actual team / player", "Alternate team / player", "Pick chance"]]
            for p in draft.get("picks") or []:
                if not p.get("most_likely_selection_changed") and p.get("actual_team") == p.get("representative_team"):
                    continue
                drows.append([p["pick"], f"{p.get('actual_team')}: {p.get('actual_player_name')}", f"{p.get('representative_team')}: {p.get('representative_player_name')}", _pct(p.get("representative_pick_marginal_probability"))])
            if len(drows) == 1:
                drows.append(["-", "No material changes", "No material changes", "-"])
            dt = Table(drows, colWidths=[.5*inch,2.25*inch,2.35*inch,.65*inch], repeatRows=1)
            dt.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),accent),("TEXTCOLOR",(0,0),(-1,0),white),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,-1),6.6),("GRID",(0,0),(-1,-1),.25,colors.lightgrey),("VALIGN",(0,0),(-1,-1),"TOP"),("ROWBACKGROUNDS",(0,1),(-1,-1),[white,pale])]))
            story.append(dt)
        story.append(PageBreak())

    story += [Paragraph("WHERE EVERYONE ENDS UP", h1), Paragraph("These are consensus rosters: players who appear on the franchise in at least half of retained alternate timelines. We do not present a 1%-probability exact roster as though it were certain.", deck)]
    for r in report["present_day"]["rosters"]:
        players = ", ".join(f"{x['player_name']} ({_pct(x['membership_probability'])})" for x in r.get("consensus_roster") or [])
        changes = []
        if r.get("likely_gained_vs_actual"): changes.append("Likely IN: " + ", ".join(x["player_name"] for x in r["likely_gained_vs_actual"][:8]))
        if r.get("likely_lost_vs_actual"): changes.append("Likely OUT: " + ", ".join(x["player_name"] for x in r["likely_lost_vs_actual"][:8]))
        block = [Paragraph(r["team"], h2), Paragraph(players or "No player reaches the 50% consensus threshold.", small)]
        if changes: block.append(Paragraph(" | ".join(changes), small))
        story.append(KeepTogether(block))

    story += [PageBreak(), Paragraph("PRESENT-DAY POWER RANKINGS", h1)]
    prows = [["#", "Team", "Actual #", "Expected wins", "Playoffs", "Title"]]
    for r in report["present_day"]["power_rankings"]["teams"]:
        a = r["alternate"]
        prows.append([r["alternate_power_rank"], r["team"], r["actual_power_rank"], f"{float(a.get('expected_wins') or 0):.1f}", _pct(a.get("playoff_probability")), _pct(a.get("championship_probability"))])
    pt = Table(prows, colWidths=[.35*inch,2.55*inch,.6*inch,.9*inch,.75*inch,.65*inch], repeatRows=1)
    pt.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),ink),("TEXTCOLOR",(0,0),(-1,0),white),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,-1),7.2),("GRID",(0,0),(-1,-1),.25,colors.lightgrey),("ROWBACKGROUNDS",(0,1),(-1,-1),[white,pale])]))
    story += [pt, PageBreak(), Paragraph("HOW THIS WAS BUILT", h1), Paragraph("The football outcomes in this magazine come from the validated Alternate History model, not from invented narrative. Completed NFL results stay fixed. The model changes fantasy ownership and decisions, then carries those consequences through standings, playoffs, rookie drafts, transactions, current rosters and the Simulator. The representative rookie drafts are complete sequential drafts from retained timelines, so no player can be selected twice.", body), Paragraph(f"Audit configuration: {report['configuration']['particles']} historical timelines; {report['configuration']['simulator_sims']} Simulator draws; probability mass {_pct(report['summary']['probability_mass'])}.", small)]
    doc.build(story)


base._league_season = _league_season
base._present_rosters = _present_rosters
base._season_story = _season_story
base._butterflies = _butterflies
base._validate_publication = _validate_publication
base._render_pdf = _render_pdf


def run(scenario_path: Path, *, particles: int, n_sims: int, seed: int):
    return v4.run(scenario_path, particles=particles, n_sims=n_sims, seed=seed)


def main() -> None:
    p = argparse.ArgumentParser(description="Render reader-first coherent FSFFL Alternate History magazine")
    p.add_argument("scenario", type=Path)
    p.add_argument("--particles", type=int, default=base.DEFAULT_PARTICLES)
    p.add_argument("--sims", type=int, default=base.DEFAULT_SIMS)
    p.add_argument("--seed", type=int, default=base.DEFAULT_SEED)
    a = p.parse_args()
    run(a.scenario, particles=a.particles, n_sims=a.sims, seed=a.seed)


if __name__ == "__main__":
    main()
