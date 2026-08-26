#!/usr/bin/env python3
"""Alternate History magazine v6: consensus-path, causal reader publication.

V6 keeps the validated particle model but improves how a reader-facing universe
is chosen and explained:
- the representative rookie draft is the retained coherent path closest to the
  league-wide pick marginals, not an arbitrary tied 5% state;
- each draft pick reports probability of changing from actual history;
- draft-order provenance identifies the original franchise and its actual vs
  alternate slot;
- standings use most-common finish plus an 80% likely range;
- present-day publication includes one complete coherent league state as well
  as consensus membership probabilities;
- butterfly ranking and prose use the same modal-change metric and always name
  the season;
- unsupported emoji are stripped only at PDF rendering time.
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import run_fsffl_alternate_history_magazine as base
import run_fsffl_alternate_history_magazine_v3 as v3
import run_fsffl_alternate_history_magazine_v4 as v4
import run_fsffl_alternate_history_magazine_v5 as v5  # installs reader-first patches
import run_fsffl_alternate_rookie_draft_particles as draft_runner
import run_fsffl_generic_alternate_history as generic
from run_fsffl_alternate_draft_candidates import raw_draft, user_to_roster_for_season

_ORIG_REPLAY_WITH_AUDIT = v4._ORIG_CAPTURE
_ORIG_LEAGUE_DRAFTS = v4._league_drafts
_ORIG_LEAGUE_SEASON = v5._league_season
_ORIG_PRESENT_ROSTERS = v5._present_rosters
_ORIG_VALIDATE = v5._validate_publication


def _pdf_safe(value: Any) -> str:
    text = str(value or "")
    return text.encode("latin-1", "ignore").decode("latin-1").replace("\u2013", "-").replace("\u2014", "-")


def _weighted_quantile(distribution: List[Dict[str, Any]], q: float) -> int | None:
    rows = sorted(
        [(int(r["value"]), float(r.get("probability") or 0.0)) for r in distribution if str(r.get("value") or "").isdigit()],
        key=lambda x: x[0],
    )
    if not rows:
        return None
    target = max(0.0, min(1.0, float(q)))
    running = 0.0
    for value, prob in rows:
        running += prob
        if running + 1e-12 >= target:
            return value
    return rows[-1][0]


def _selection_audit(meta: Dict[str, Any]) -> Dict[Tuple[int, int], Dict[str, int]]:
    return {
        (int(a.get("round") or 0), int(a.get("slot") or 0)): {
            str(pid): int(n) for pid, n in (a.get("selection_counts") or {}).items()
        }
        for a in (meta.get("draft_pick_audit") or [])
    }


def _consensus_path_score(group, season: str, audit, total: int) -> float:
    node = group.state.get(draft_runner.DRAFT_KEY) or {}
    picks = [p for p in (node.get("picks") or []) if str(p.get("draft_season") or "") == season]
    if len(picks) != 36:
        return float("-inf")
    score = 0.0
    for p in picks:
        key = (int(p.get("round") or 0), int(p.get("slot") or 0))
        pid = str(p.get("player_id") or "")
        probability = float((audit.get(key) or {}).get(pid, 0)) / float(total or 1)
        score += math.log(max(probability, 1.0 / max(total * 1000.0, 1.0)))
    # Tiny tie-break preference for a more heavily retained exact state without
    # allowing exact-state weight to dominate consensus closeness.
    score += 0.05 * math.log(max(int(group.count), 1) / float(total or 1))
    return score


def _capture_consensus_representative(*args, **kwargs):
    groups, meta = _ORIG_REPLAY_WITH_AUDIT(*args, **kwargs)
    season = str(meta.get("draft_season") or kwargs.get("draft_season") or "")
    if season and groups:
        total = sum(int(g.count) for g in groups)
        audit = _selection_audit(meta)
        ranked = sorted(
            groups,
            key=lambda g: (
                -_consensus_path_score(g, season, audit, total),
                -int(g.count),
                str((g.traces or [[]])[0]),
            ),
        )
        group = ranked[0]
        node = group.state.get(draft_runner.DRAFT_KEY) or {}
        rows = [dict(p) for p in (node.get("picks") or []) if str(p.get("draft_season") or "") == season]
        rows.sort(key=lambda p: int(p.get("pick_no") or 0))
        v4._REPRESENTATIVE_DRAFTS[season] = {
            "state_particles": int(group.count),
            "state_probability": round(int(group.count) / total, 8) if total else 0.0,
            "consensus_path_score": round(_consensus_path_score(group, season, audit, total), 8),
            "selection_method": "retained_path_maximizing_sum_log_pick_marginals",
            "picks": rows,
        }
    return groups, meta


def _historical_slot_by_roster(season: str) -> Dict[str, int]:
    entry = raw_draft(season)
    draft = entry.get("draft") or {}
    uid_to_roster = user_to_roster_for_season(season)
    out: Dict[str, int] = {}
    for uid, slot in (draft.get("draft_order") or {}).items():
        rid = str(uid_to_roster.get(str(uid)) or "")
        if rid:
            out[rid] = int(slot)
    return out


def _league_drafts(groups, seasons: Iterable[str], total: int, names: Dict[str, str], teams: Dict[str, str]):
    drafts = _ORIG_LEAGUE_DRAFTS(groups, seasons, total, names, teams)
    for draft in drafts:
        season = str(draft["draft_season"])
        historical_slots = _historical_slot_by_roster(season)
        audit_by_pick = {
            (int(a.get("round") or 0), int(a.get("slot") or 0)): a
            for a in (v3._CAPTURED_DRAFT_AUDITS.get(season) or [])
        }
        for p in draft.get("picks") or []:
            key = (int(p.get("round") or 0), int(p.get("slot") or 0))
            audit = audit_by_pick.get(key) or {}
            selection_counts = {str(pid): int(n) for pid, n in (audit.get("selection_counts") or {}).items()}
            controller_counts = {str(rid): int(n) for rid, n in (audit.get("controller_counts") or {}).items()}
            actual_pid = str(p.get("actual_player_id") or "")
            actual_rid = str(p.get("actual_roster_id") or "")
            p["selection_change_probability"] = round(1.0 - selection_counts.get(actual_pid, 0) / float(total), 8) if actual_pid else None
            p["controller_change_probability"] = round(1.0 - controller_counts.get(actual_rid, 0) / float(total), 8) if actual_rid else None
            p["representative_selection_changed"] = bool(p.get("representative_player_id") and p.get("representative_player_id") != actual_pid)
            rep = next((r for r in (v4._REPRESENTATIVE_DRAFTS.get(season, {}).get("picks") or []) if int(r.get("round") or 0) == key[0] and int(r.get("slot") or 0) == key[1]), {})
            original = str(rep.get("original_roster_id") or "")
            p["original_franchise_roster_id"] = original or None
            p["original_franchise_team"] = teams.get(original, f"Roster {original}") if original else None
            p["original_franchise_actual_slot"] = historical_slots.get(original) if original else None
            p["original_franchise_alternate_slot"] = int(p.get("slot") or 0)
            if original and historical_slots.get(original) and int(historical_slots[original]) != int(p.get("slot") or 0):
                p["draft_order_explanation"] = (
                    f"{teams.get(original, f'Roster {original}')} owned this original franchise slot at "
                    f"{int(historical_slots[original])} in real history, but the alternate prior-season finish moves "
                    f"that franchise slot to {int(p.get('slot') or 0)}. The selecting team at draft time is "
                    f"{p.get('representative_team')}."
                )
            else:
                p["draft_order_explanation"] = (
                    f"The original franchise slot remains {int(p.get('slot') or 0)}; the selecting team at draft time is "
                    f"{p.get('representative_team')}."
                )
        rep_meta = v4._REPRESENTATIVE_DRAFTS.get(season) or {}
        draft["representative_selection_method"] = rep_meta.get("selection_method")
        draft["representative_consensus_path_score"] = rep_meta.get("consensus_path_score")
    return drafts


def _league_season(*args, **kwargs):
    row = _ORIG_LEAGUE_SEASON(*args, **kwargs)
    for team in row.get("alternate_expected_standings") or []:
        dist = team.get("seed_distribution") or []
        team["likely_seed_low"] = _weighted_quantile(dist, 0.10)
        team["likely_seed_high"] = _weighted_quantile(dist, 0.90)
    return row


def _present_rosters(groups, total: int, names: Dict[str, str], teams: Dict[str, str]):
    rows = _ORIG_PRESENT_ROSTERS(groups, total, names, teams)
    membership: Dict[str, Dict[str, float]] = {}
    for row in rows:
        membership[str(row["roster_id"])] = {
            str(x["player_id"]): float(x.get("membership_probability") or 0.0)
            for x in (row.get("modal_roster") or [])
        }
        for x in row.get("consensus_roster") or []:
            membership[str(row["roster_id"])][str(x["player_id"])] = float(x.get("membership_probability") or 0.0)
        for x in row.get("lost_vs_actual") or []:
            membership[str(row["roster_id"])][str(x["player_id"])] = float(x.get("retention_probability") or 0.0)

    def state_score(group) -> float:
        score = 0.0
        for rid, players in (group.state.get("roster_players") or {}).items():
            probs = membership.get(str(rid), {})
            for pid in players or []:
                score += math.log(max(probs.get(str(pid), 1.0 / max(total * 1000.0, 1.0)), 1e-12))
        return score

    representative = sorted(groups, key=lambda g: (-state_score(g), -int(g.count), str((g.traces or [[]])[0])))[0]
    rep_weight = round(int(representative.count) / float(total), 8)
    for row in rows:
        rid = str(row["roster_id"])
        ids = sorted(str(x) for x in ((representative.state.get("roster_players") or {}).get(rid) or []))
        row["representative_full_roster"] = [
            {
                "player_id": pid,
                "player_name": names.get(pid, pid),
                "membership_probability": round(membership.get(rid, {}).get(pid, 0.0), 8),
            }
            for pid in ids
        ]
        row["representative_league_state_probability"] = rep_weight
        row["reader_roster_semantics"] = "complete coherent league state plus consensus membership probabilities"
    return rows


def _butterflies(seasons, drafts, transactions, power):
    events: List[Dict[str, Any]] = []
    for season in seasons:
        year = str(season["season"])
        for row in season.get("alternate_expected_standings") or []:
            if not row.get("actual_seed") or not row.get("most_likely_seed"):
                continue
            swing = abs(int(row["most_likely_seed"]) - int(row["actual_seed"]))
            if swing >= 1:
                events.append({
                    "kind": "SEED_SWING", "season": year, "team": row["team"],
                    "impact": float(swing) + float(row.get("most_likely_seed_probability") or 0.0),
                    "sentence": f"{year}: {row['team']} was actually the No. {row['actual_seed']} seed; its most common alternate finish is No. {row['most_likely_seed']} ({v5._pct(row.get('most_likely_seed_probability'))}).",
                })
    for draft in drafts:
        year = str(draft["draft_season"])
        for p in draft.get("picks") or []:
            change = float(p.get("selection_change_probability") or 0.0)
            if change >= 0.35:
                events.append({
                    "kind": "DRAFT_PICK_CHANGED", "season": year, "team": p.get("representative_team"),
                    "impact": 1.5 + change,
                    "sentence": f"{year} {p['pick']}: the selection changes from {p.get('actual_team')} - {p.get('actual_player_name')} in {v5._pct(change)} of timelines. In the coherent representative universe, {p.get('representative_team')} selects {p.get('representative_player_name')}.",
                })
    for tx in transactions:
        change = float(tx.get("probability_changed_or_removed") or 0.0)
        if change >= 0.40:
            year = str(tx.get("season") or "Downstream")
            events.append({
                "kind": "TRADE_CHANGED", "season": year, "team": None, "impact": 1.0 + change,
                "sentence": f"{year}: a real-life roster move changes or disappears in {v5._pct(change)} of alternate timelines: {tx.get('actual_transaction')}.",
            })
    for row in power.get("teams") or []:
        move = int(row.get("power_rank_change") or 0)
        if abs(move) >= 2:
            direction = "rises" if move > 0 else "falls"
            events.append({
                "kind": "POWER_RANK_SWING", "season": "Present", "team": row["team"], "impact": 1.0 + abs(move) / 3.0,
                "sentence": f"Present day: {row['team']} {direction} {abs(move)} spots in the Simulator power order, from No. {row['actual_power_rank']} to No. {row['alternate_power_rank']}.",
            })
    events.sort(key=lambda e: (-float(e["impact"]), str(e.get("season")), str(e.get("team"))))
    for i, event in enumerate(events, 1):
        event["rank"] = i
    return events


def _season_story(season: Dict[str, Any], next_draft: Dict[str, Any] | None, txs: List[Dict[str, Any]]):
    year = str(season["season"])
    rows = season.get("alternate_expected_standings") or []
    champ = (season.get("champion_distribution") or [{}])[0]
    paragraphs: List[str] = []
    if champ:
        paragraphs.append(f"In {year}, {champ.get('team')} is the most common alternate champion at {v5._pct(champ.get('probability'))}.")
    movers = sorted(
        [r for r in rows if r.get("actual_seed") and r.get("most_likely_seed")],
        key=lambda r: (-abs(int(r["most_likely_seed"]) - int(r["actual_seed"])), -float(r.get("most_likely_seed_probability") or 0.0)),
    )[:3]
    if movers:
        paragraphs.append("The clearest standings changes are " + "; ".join(
            f"{r['team']} #{r['actual_seed']} -> most often #{r['most_likely_seed']} ({v5._pct(r.get('most_likely_seed_probability'))})"
            for r in movers
        ) + ".")
    changed = [t for t in txs if str(t.get("season")) == year and float(t.get("probability_changed_or_removed") or 0.0) >= 0.25][:5]
    if changed:
        examples = "; ".join(str(t.get("actual_transaction")) for t in changed[:3])
        paragraphs.append(f"Different roster needs also put {len(changed)} important real-life moves in doubt. Examples: {examples}.")
    if next_draft:
        high = sum(1 for p in next_draft.get("picks") or [] if float(p.get("selection_change_probability") or 0.0) >= 0.50)
        paragraphs.append(f"Those results feed the {next_draft['draft_season']} draft: {high} of 36 slots have at least a 50% chance of producing a different player than real history.")
    return {"season": year, "paragraphs": paragraphs, "major_seed_swings": movers, "changed_transactions": changed, "following_draft": next_draft}


def _validate_publication(report: Dict[str, Any]) -> None:
    # v5's roster semantics string is intentionally superseded here, so run the
    # underlying v4/base contract and then V6-specific reader gates.
    v4._validate_publication(report)
    for draft in report.get("drafts") or []:
        if draft.get("representative_selection_method") != "retained_path_maximizing_sum_log_pick_marginals":
            raise base.ah.AlternateHistoryError(f"{draft.get('draft_season')} representative draft is not consensus-selected")
        for p in draft.get("picks") or []:
            cp = p.get("selection_change_probability")
            if cp is None or not (0.0 <= float(cp) <= 1.0):
                raise base.ah.AlternateHistoryError(f"{draft.get('draft_season')} {p.get('pick')} missing valid selection-change probability")
            if not p.get("draft_order_explanation"):
                raise base.ah.AlternateHistoryError(f"{draft.get('draft_season')} {p.get('pick')} missing order provenance")
    for season in report.get("seasons") or []:
        for row in season.get("alternate_expected_standings") or []:
            if row.get("likely_seed_low") is None or row.get("likely_seed_high") is None:
                raise base.ah.AlternateHistoryError(f"{season['season']} missing likely seed range for {row.get('team')}")
    for roster in report.get("present_day", {}).get("rosters") or []:
        if not roster.get("representative_full_roster"):
            raise base.ah.AlternateHistoryError(f"{roster.get('team')} missing complete representative roster")


def _render_pdf(report: Dict[str, Any], path: Path) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether

    styles = getSampleStyleSheet()
    ink = colors.HexColor("#172133"); accent = colors.HexColor("#B11F2E"); pale = colors.HexColor("#F4F1EA"); white = colors.white
    title = ParagraphStyle("title6", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=28, leading=31, textColor=ink, spaceAfter=10)
    h1 = ParagraphStyle("h16", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=19, leading=22, textColor=accent, spaceBefore=6, spaceAfter=8)
    h2 = ParagraphStyle("h26", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=11, leading=14, textColor=ink, spaceBefore=5, spaceAfter=3)
    body = ParagraphStyle("body6", parent=styles["BodyText"], fontName="Helvetica", fontSize=8.8, leading=12, textColor=ink, spaceAfter=5)
    small = ParagraphStyle("small6", parent=body, fontSize=6.9, leading=8.6)
    deck = ParagraphStyle("deck6", parent=body, fontSize=11.5, leading=15.5, spaceAfter=9)
    doc = SimpleDocTemplate(str(path), pagesize=letter, leftMargin=.5*inch, rightMargin=.5*inch, topMargin=.5*inch, bottomMargin=.5*inch, title="FSFFL Alternate History V2")
    focus = report["focus_franchise"]
    delta = focus.get("simulator_deltas") or {}
    story = [
        Paragraph("FSFFL ALTERNATE HISTORY", h2),
        Paragraph(_pdf_safe(report["scenario"].get("title") or "Alternate History"), title),
        Paragraph("One changed decision. Three seasons of consequences - followed through standings, playoff paths, rookie drafts, roster moves and the present day.", deck),
        Paragraph("THE BOTTOM LINE", h1),
        Paragraph(_pdf_safe(f"{focus['team']} reaches the present with a materially different roster. Simulator expected wins move by {float(delta.get('expected_wins') or 0):+.2f}, playoff probability by {float(delta.get('playoff_probability') or 0)*100:+.1f} points, and title probability by {float(delta.get('championship_probability') or 0)*100:+.1f} points."), deck),
        PageBreak(),
        Paragraph("THE BIGGEST BUTTERFLY EFFECTS", h1),
    ]
    for e in report.get("butterflies", [])[:10]:
        story.append(Paragraph(_pdf_safe(f"{e['rank']}. {e['sentence']}"), body))
    story.append(PageBreak())

    for chapter in report.get("season_chapters") or []:
        season = next(s for s in report["seasons"] if s["season"] == chapter["season"])
        story.append(Paragraph(f"{chapter['season']}: WHAT CHANGED", h1))
        for p in chapter.get("paragraphs") or []:
            story.append(Paragraph(_pdf_safe(p), deck if p == chapter["paragraphs"][0] else body))
        rows = [["Proj", "Team", "Actual", "Most common", "80% range", "Playoffs", "Title"]]
        for r in season["alternate_expected_standings"]:
            lo, hi = r.get("likely_seed_low"), r.get("likely_seed_high")
            rows.append([
                r["alternate_rank"], _pdf_safe(r["team"]), f"#{r['actual_seed']}" if r.get("actual_seed") else "-",
                f"#{r['most_likely_seed']} ({v5._pct(r.get('most_likely_seed_probability'))})",
                f"#{lo}-#{hi}" if lo and hi else "-", v5._pct(r["playoff_probability"]), v5._pct(r["championship_probability"]),
            ])
        t = Table(rows, colWidths=[.32*inch,1.8*inch,.48*inch,1.0*inch,.62*inch,.65*inch,.55*inch], repeatRows=1)
        t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),ink),("TEXTCOLOR",(0,0),(-1,0),white),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,-1),6.6),("GRID",(0,0),(-1,-1),.25,colors.lightgrey),("ROWBACKGROUNDS",(0,1),(-1,-1),[white,pale])]))
        story += [t, Spacer(1,7)]
        draft = chapter.get("following_draft")
        if draft:
            story.append(Paragraph(f"THE {draft['draft_season']} ROOKIE DRAFT", h2))
            story.append(Paragraph("The representative column is one complete retained draft chosen because, collectively, its 36 selections are closest to the model's pick-by-pick consensus. The change percentage is the probability that the slot produces a different player than real history.", body))
            drows = [["Pick", "Real history", "Coherent alternate", "Change"]]
            material = sorted(draft.get("picks") or [], key=lambda p: (-float(p.get("selection_change_probability") or 0.0), int(p.get("round") or 0), int(p.get("slot") or 0)))
            for p in material[:18]:
                if float(p.get("selection_change_probability") or 0.0) < .20 and p.get("actual_team") == p.get("representative_team"):
                    continue
                drows.append([
                    p["pick"], _pdf_safe(f"{p.get('actual_team')}: {p.get('actual_player_name')}"),
                    _pdf_safe(f"{p.get('representative_team')}: {p.get('representative_player_name')}"),
                    v5._pct(p.get("selection_change_probability")),
                ])
            dt = Table(drows, colWidths=[.45*inch,2.25*inch,2.4*inch,.62*inch], repeatRows=1)
            dt.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),accent),("TEXTCOLOR",(0,0),(-1,0),white),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,-1),6.5),("GRID",(0,0),(-1,-1),.25,colors.lightgrey),("VALIGN",(0,0),(-1,-1),"TOP"),("ROWBACKGROUNDS",(0,1),(-1,-1),[white,pale])]))
            story.append(dt)
            moved = [p for p in draft.get("picks") or [] if p.get("original_franchise_actual_slot") and int(p["original_franchise_actual_slot"]) != int(p.get("slot") or 0)]
            if moved:
                story.append(Paragraph("WHY THE ORDER CHANGED", h2))
                for p in moved[:5]:
                    story.append(Paragraph(_pdf_safe(p["draft_order_explanation"]), small))
        story.append(PageBreak())

    story += [Paragraph("WHERE EVERYONE ENDS UP", h1), Paragraph("Below is one complete coherent present-day league state. Percentages beside players show how often that player belongs to the same franchise across all retained timelines; this separates a readable full roster from model uncertainty.", deck)]
    for r in report["present_day"]["rosters"]:
        players = ", ".join(_pdf_safe(f"{x['player_name']} ({v5._pct(x.get('membership_probability'))})") for x in r.get("representative_full_roster") or [])
        changes = []
        if r.get("likely_gained_vs_actual"): changes.append("Likely IN: " + ", ".join(_pdf_safe(x["player_name"]) for x in r["likely_gained_vs_actual"][:6]))
        if r.get("likely_lost_vs_actual"): changes.append("Likely OUT: " + ", ".join(_pdf_safe(x["player_name"]) for x in r["likely_lost_vs_actual"][:6]))
        block = [Paragraph(_pdf_safe(r["team"]), h2), Paragraph(players or "No active players.", small)]
        if changes:
            block.append(Paragraph(" | ".join(changes), small))
        story.append(KeepTogether(block))

    story += [PageBreak(), Paragraph("PRESENT-DAY POWER RANKINGS", h1)]
    prows = [["#", "Team", "Actual #", "Expected wins", "Playoffs", "Title"]]
    for r in report["present_day"]["power_rankings"]["teams"]:
        a = r["alternate"]
        prows.append([r["alternate_power_rank"], _pdf_safe(r["team"]), r["actual_power_rank"], f"{float(a.get('expected_wins') or 0):.1f}", v5._pct(a.get("playoff_probability")), v5._pct(a.get("championship_probability"))])
    pt = Table(prows, colWidths=[.35*inch,2.55*inch,.6*inch,.9*inch,.75*inch,.65*inch], repeatRows=1)
    pt.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),ink),("TEXTCOLOR",(0,0),(-1,0),white),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,-1),7.1),("GRID",(0,0),(-1,-1),.25,colors.lightgrey),("ROWBACKGROUNDS",(0,1),(-1,-1),[white,pale])]))
    story += [pt, PageBreak(), Paragraph("HOW THIS WAS BUILT", h1), Paragraph("Completed NFL results stay fixed. The model changes fantasy ownership and decisions and carries those consequences through standings, playoffs, rookie drafts, transactions and current rosters. Rookie candidates may cross historical round boundaries only when they remain inside a tight same-draft market window; no future NFL performance is used to make the choice.", body), Paragraph(f"Audit configuration: {report['configuration']['particles']} historical timelines; {report['configuration']['simulator_sims']} Simulator draws; probability mass {v5._pct(report['summary']['probability_mass'])}.", small)]
    doc.build(story)


generic.replay_rookie_draft_groups = _capture_consensus_representative
base._league_drafts = _league_drafts
base._league_season = _league_season
base._present_rosters = _present_rosters
base._season_story = _season_story
base._butterflies = _butterflies
base._validate_publication = _validate_publication
base._render_pdf = _render_pdf


def run(scenario_path: Path, *, particles: int, n_sims: int, seed: int):
    v4._REPRESENTATIVE_DRAFTS.clear()
    v3._CAPTURED_DRAFT_AUDITS.clear()
    return base.run(scenario_path, particles=particles, n_sims=n_sims, seed=seed)


def main() -> None:
    p = argparse.ArgumentParser(description="Render consensus-path causal FSFFL Alternate History magazine")
    p.add_argument("scenario", type=Path)
    p.add_argument("--particles", type=int, default=base.DEFAULT_PARTICLES)
    p.add_argument("--sims", type=int, default=base.DEFAULT_SIMS)
    p.add_argument("--seed", type=int, default=base.DEFAULT_SEED)
    a = p.parse_args()
    run(a.scenario, particles=a.particles, n_sims=a.sims, seed=a.seed)


if __name__ == "__main__":
    main()
