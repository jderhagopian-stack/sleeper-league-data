#!/usr/bin/env python3
"""Alternate History magazine v8: league-story publication layer.

Presentation-only upgrade over the validated V7 engine. It keeps all model
semantics unchanged and turns the retained 100-particle state into a reader-first
alternate-history magazine with causal timelines, roster/pick comparisons,
actual-vs-alternate power rankings, and narrative league conclusions.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

import alternate_history_engine as ah
import build_fsffl_season_simulator as sim
import run_fsffl_alternate_history_final_report as v1
import run_fsffl_alternate_history_magazine as base
import run_fsffl_alternate_history_magazine_v6 as v6
import run_fsffl_alternate_history_report_v2 as v2
import run_fsffl_generic_alternate_history as generic
from run_fsffl_alternate_history import FSFFLHistoricalAdapter
from run_fsffl_downstream_dependencies import load

DATA = Path("data")


def _safe(value: Any) -> str:
    return str(value or "").encode("latin-1", "ignore").decode("latin-1").replace("\u2013", "-").replace("\u2014", "-")


def _pct(value: Any) -> str:
    return "n/a" if value is None else f"{100.0 * float(value):.1f}%"


def _name_rows(items, key="player_name") -> str:
    return ", ".join(_safe(x.get(key)) for x in (items or [])) or "-"


def _pick_label(key: str, teams: Dict[str, str]) -> str:
    m = re.match(r"pick:(\d+):R(\d+):orig(.+)", str(key))
    if not m:
        return str(key)
    season, rnd, orig = m.groups()
    suffix = "1st" if rnd == "1" else "2nd" if rnd == "2" else "3rd"
    origin = "own" if False else teams.get(str(orig), f"Roster {orig}")
    return f"{season} {suffix} ({origin})"


def _full_pick_inventory(owner_map: Dict[str, str], current_season: int, teams: Dict[str, str]) -> Dict[str, List[Dict[str, str]]]:
    out: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    roster_ids = sorted(teams, key=lambda x: int(x) if str(x).isdigit() else 999)
    for season in range(current_season + 1, current_season + 4):
        for rnd in range(1, 4):
            for orig in roster_ids:
                key = f"pick:{season}:R{rnd}:orig{orig}"
                owner = str(owner_map.get(key, orig))
                suffix = "1st" if rnd == 1 else "2nd" if rnd == 2 else "3rd"
                source = "own" if owner == str(orig) else f"from {teams.get(str(orig), f'Roster {orig}')}"
                out[owner].append({"key": key, "label": f"{season} {suffix} ({source})"})
    return out


def _choose_representative(groups, roster_rows):
    target = {
        str(r["roster_id"]): tuple(sorted(str(x["player_id"]) for x in (r.get("representative_full_roster") or [])))
        for r in roster_rows
    }
    matches = []
    for g in groups:
        good = True
        for rid, wanted in target.items():
            got = tuple(sorted(str(x) for x in ((g.state.get("roster_players") or {}).get(rid) or [])))
            if got != wanted:
                good = False
                break
        if good:
            matches.append(g)
    if matches:
        return sorted(matches, key=lambda g: (-int(g.count), str((g.traces or [[]])[0])))[0]
    return sorted(groups, key=lambda g: (-int(g.count), str((g.traces or [[]])[0])))[0]


def _enrich_present_rosters(groups, total: int, names: Dict[str, str], teams: Dict[str, str], adapter):
    rows = v6._present_rosters(groups, total, names, teams)
    representative = _choose_representative(groups, rows)
    actual_state = adapter.current_state()

    league = load(DATA / "league.json") or {}
    current_season = int(league.get("season") or 0)
    players = load(DATA / "players.json") or {}
    projections = load(DATA / "simulator" / str(current_season) / "inputs" / "player_weekly_projections.json") or {}
    actual_rosters = {str(r.get("roster_id")): r for r in (load(DATA / "rosters.json") or [])}

    actual_picks = _full_pick_inventory(actual_state.pick_owners, current_season, teams)
    alternate_picks = _full_pick_inventory(
        {str(k): str(v) for k, v in (representative.state.get("pick_owners") or {}).items()},
        current_season,
        teams,
    )

    for row in rows:
        rid = str(row["roster_id"])
        ar = actual_rosters.get(rid) or {}
        actual_players = [str(x) for x in (ar.get("players") or [])]
        actual_starters = [str(x) for x in (ar.get("starters") or []) if str(x) in actual_players]
        actual_taxi = set(str(x) for x in (ar.get("taxi") or []))
        actual_reserve = set(str(x) for x in (ar.get("reserve") or []))
        actual_bench = [p for p in actual_players if p not in set(actual_starters) | actual_taxi | actual_reserve]

        alt_players = sorted(str(x) for x in ((representative.state.get("roster_players") or {}).get(rid) or []))
        alt_taxi = set(str(x) for x in ((representative.state.get("roster_taxi") or {}).get(rid) or []))
        alt_reserve = set(str(x) for x in ((representative.state.get("roster_reserve") or {}).get(rid) or []))
        lineup_pool = [p for p in alt_players if p not in alt_reserve]
        lineup = sim.optimize_weekly_lineup(
            {"players": lineup_pool, "taxi": sorted(alt_taxi)},
            1, league, players, projections,
        )
        alt_starter_ids = [str(x.get("player_id")) for x in lineup if x.get("player_id")]
        alt_bench = [p for p in alt_players if p not in set(alt_starter_ids) | alt_taxi | alt_reserve]

        def pack(ids):
            return [{"player_id": p, "player_name": names.get(p, p)} for p in ids]

        row["actual_comparison"] = {
            "starters": pack(actual_starters),
            "bench": pack(actual_bench),
            "taxi": pack(sorted(actual_taxi)),
            "reserve": pack(sorted(actual_reserve)),
            "future_picks": actual_picks.get(rid, []),
        }
        row["alternate_comparison"] = {
            "starters": pack(alt_starter_ids),
            "bench": pack(alt_bench),
            "taxi": pack(sorted(alt_taxi)),
            "reserve": pack(sorted(alt_reserve)),
            "future_picks": alternate_picks.get(rid, []),
        }
    return rows, representative


def _asset_values():
    raw = load(DATA / "fsffl_asset_values.json") or {}
    return {
        str(p.get("player_id")): {
            "name": p.get("name"),
            "value": float(p.get("fsffl_value") or p.get("market_dynasty") or 0.0),
            "position": p.get("position"),
        }
        for p in (raw.get("players") or [])
    }


def _ownership_swings(rosters, values, teams):
    actual_owner, alt_owner = {}, {}
    for r in rosters:
        rid = str(r["roster_id"])
        for section in ("starters", "bench", "taxi", "reserve"):
            for p in (r["actual_comparison"].get(section) or []):
                actual_owner[str(p["player_id"])] = rid
            for p in (r["alternate_comparison"].get(section) or []):
                alt_owner[str(p["player_id"])] = rid
    swings = []
    for pid in set(actual_owner) | set(alt_owner):
        a, b = actual_owner.get(pid), alt_owner.get(pid)
        if a == b:
            continue
        meta = values.get(pid) or {}
        swings.append({
            "player_id": pid,
            "player_name": meta.get("name") or pid,
            "position": meta.get("position"),
            "value": float(meta.get("value") or 0.0),
            "actual_team": teams.get(str(a), "Free Agent") if a else "Free Agent",
            "alternate_team": teams.get(str(b), "Free Agent") if b else "Free Agent",
        })
    return sorted(swings, key=lambda x: (-x["value"], x["player_name"]))


def _team_winners(rosters, power, values):
    by_power = {str(x["roster_id"]): x for x in (power.get("teams") or [])}
    rows = []
    for r in rosters:
        rid = str(r["roster_id"])
        actual_ids = {
            str(p["player_id"])
            for sec in ("starters", "bench", "taxi", "reserve")
            for p in (r["actual_comparison"].get(sec) or [])
        }
        alt_ids = {
            str(p["player_id"])
            for sec in ("starters", "bench", "taxi", "reserve")
            for p in (r["alternate_comparison"].get(sec) or [])
        }
        asset_delta = sum(values.get(p, {}).get("value", 0.0) for p in alt_ids) - sum(values.get(p, {}).get("value", 0.0) for p in actual_ids)
        pr = by_power.get(rid) or {}
        d = pr.get("deltas") or {}
        score = (
            1.6 * float(pr.get("power_rank_change") or 0)
            + 12.0 * float(d.get("playoff_probability") or 0)
            + 18.0 * float(d.get("championship_probability") or 0)
            + asset_delta / 2500.0
        )
        rows.append({
            "roster_id": rid, "team": r["team"], "score": round(score, 3),
            "asset_delta": round(asset_delta, 1),
            "power_rank_change": int(pr.get("power_rank_change") or 0),
            "playoff_delta": float(d.get("playoff_probability") or 0),
            "title_delta": float(d.get("championship_probability") or 0),
        })
    return sorted(rows, key=lambda x: (-x["score"], x["team"]))


def _biggest_butterflies(seasons, drafts, transactions, power, ownership):
    events: List[Dict[str, Any]] = []
    for x in ownership[:14]:
        events.append({
            "kind": "STAR_OWNERSHIP", "season": "Present",
            "impact": 8.0 + min(x["value"], 12000.0) / 1800.0,
            "sentence": f"{x['player_name']} is on {x['actual_team']} today, but lands on {x['alternate_team']} in the coherent alternate present.",
        })
    for tx in transactions:
        change = float(tx.get("probability_changed_or_removed") or 0.0)
        if change >= .45:
            events.append({
                "kind": "TRADE_OR_MOVE", "season": str(tx.get("season") or ""),
                "impact": 6.0 + 4.0 * change,
                "sentence": f"{tx.get('season')}: {tx.get('actual_transaction')} changes or disappears in {_pct(change)} of alternate timelines.",
            })
    for d in drafts:
        for p in d.get("picks") or []:
            change = float(p.get("selection_change_probability") or 0.0)
            if change >= .50:
                events.append({
                    "kind": "DRAFT_CASCADE", "season": str(d.get("draft_season")),
                    "impact": 5.0 + 3.0 * change,
                    "sentence": f"{d['draft_season']} {p['pick']}: reality was {p.get('actual_team')} selecting {p.get('actual_player_name')}; the representative alternate has {p.get('representative_team')} selecting {p.get('representative_player_name')} ({_pct(change)} chance the player changes).",
                })
    for r in power.get("teams") or []:
        move = abs(int(r.get("power_rank_change") or 0))
        if move >= 2:
            events.append({
                "kind": "POWER_SHIFT", "season": "Present",
                "impact": 4.0 + move,
                "sentence": f"{r['team']} moves from No. {r['actual_power_rank']} in today's league to No. {r['alternate_power_rank']} in the alternate present.",
            })
    for s in seasons:
        for r in s.get("alternate_expected_standings") or []:
            if r.get("actual_seed") and r.get("most_likely_seed"):
                swing = abs(int(r["actual_seed"]) - int(r["most_likely_seed"]))
                if swing >= 2:
                    events.append({
                        "kind": "SEASON_FINISH", "season": str(s["season"]),
                        "impact": 2.0 + .5 * swing,
                        "sentence": f"{s['season']}: {r['team']} actually finished No. {r['actual_seed']}; its most likely alternate finish is No. {r['most_likely_seed']} ({_pct(r.get('most_likely_seed_probability'))}).",
                    })
    events.sort(key=lambda e: (-float(e["impact"]), str(e["season"]), e["sentence"]))
    for i, e in enumerate(events, 1):
        e["rank"] = i
    return events


def _impacted_teams(payload, transactions, focus_team):
    players = []
    for a in payload.get("actions") or []:
        for k in ("add_player", "drop_player"):
            if a.get(k):
                players.append(str(a[k]))
    teams = [focus_team]
    for tx in transactions:
        text = str(tx.get("actual_transaction") or "")
        if not any(p.casefold() in text.casefold() for p in players):
            continue
        m = re.search(r":\s*([^:]+?)\s*:\s*[^:]+$", text)
        if m:
            team = m.group(1).strip()
            if team and team not in teams:
                teams.append(team)
        else:
            for marker in ("adds ", "drops "):
                if marker in text:
                    tail = text.split(marker, 1)[1]
                    team = tail.split(":", 1)[0].strip()
                    if team and team not in teams:
                        teams.append(team)
    return teams[:3]


def _narrative(report, impacted):
    by_team_roster = {r["team"]: r for r in report["present_day"]["rosters"]}
    by_team_power = {r["team"]: r for r in report["present_day"]["power_rankings"]["teams"]}
    paras = []
    for team in impacted[:2]:
        rr = by_team_roster.get(team)
        pp = by_team_power.get(team)
        if not rr or not pp:
            continue
        gained = [x["player_name"] for x in (rr.get("likely_gained_vs_actual") or [])[:4]]
        lost = [x["player_name"] for x in (rr.get("likely_lost_vs_actual") or [])[:4]]
        d = pp.get("deltas") or {}
        detail = []
        if gained: detail.append("gains " + ", ".join(gained))
        if lost: detail.append("loses " + ", ".join(lost))
        roster_line = "; ".join(detail) if detail else "keeps a broadly recognizable core"
        paras.append(
            f"{team}: the original fork does not stay isolated. By the present, the franchise {roster_line}. "
            f"Its Simulator rank moves from No. {pp['actual_power_rank']} to No. {pp['alternate_power_rank']}, "
            f"with playoff odds changing {float(d.get('playoff_probability') or 0)*100:+.1f} percentage points and title odds {float(d.get('championship_probability') or 0)*100:+.1f} points."
        )
    winners = report.get("who_won_lost") or []
    if winners:
        best, worst = winners[0], winners[-1]
        paras.append(
            f"League-wide, the fork redistributes far more than one player. {best['team']} is the biggest net beneficiary by the combined roster-and-Simulator score, while {worst['team']} absorbs the largest downside. "
            f"Across the league, star ownership, draft slots, future picks and hundreds of downstream roster decisions move in response."
        )
    paras.append(
        "The important takeaway is not that every historical result flips. Some outcomes remain stubbornly stable. The alternate-history engine is most revealing where the original decision changes roster needs, which then changes trades, waivers and draft choices and ultimately produces a different present-day balance of power."
    )
    return paras


def _story_metadata(report):
    txs = sorted(
        [t for t in report["transactions"] if float(t.get("probability_changed_or_removed") or 0) >= .50],
        key=lambda t: (int(t.get("season") or 9999), -float(t.get("probability_changed_or_removed") or 0)),
    )
    drafts = []
    for d in report["drafts"]:
        changed = sorted(d.get("picks") or [], key=lambda p: -float(p.get("selection_change_probability") or 0))
        if changed:
            drafts.append((d["draft_season"], changed[0]))
    chain = [{"label": "THE FORK", "text": report["scenario"]["title"]}]
    for t in txs[:4]:
        chain.append({"label": str(t.get("season")), "text": f"{t.get('actual_transaction')} is altered/erased in {_pct(t.get('probability_changed_or_removed'))} of timelines."})
    for year, p in drafts[:2]:
        chain.append({"label": str(year), "text": f"At {p['pick']}, the selection changes from {p.get('actual_player_name')} to {p.get('representative_player_name')} in the representative path."})
    swings = report.get("ownership_swings") or []
    if swings:
        x = swings[0]
        chain.append({"label": "PRESENT", "text": f"{x['player_name']} ends up on {x['alternate_team']} instead of {x['actual_team']}."})
    report["chain_reaction"] = chain[:8]
    report["point_of_no_return"] = txs[0] if txs else None

    stable = [r for r in report["present_day"]["power_rankings"]["teams"] if int(r.get("power_rank_change") or 0) == 0]
    low_change = sum(1 for d in report["drafts"] for p in d.get("picks") or [] if float(p.get("selection_change_probability") or 0) < .20)
    report["what_didnt_change"] = {
        "stable_power_teams": [r["team"] for r in stable],
        "low_change_draft_slots": low_change,
        "total_draft_slots": sum(len(d.get("picks") or []) for d in report["drafts"]),
    }


def _render_pdf(report: Dict[str, Any], path: Path) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether

    styles = getSampleStyleSheet()
    ink = colors.HexColor("#172133"); accent = colors.HexColor("#B11F2E"); pale = colors.HexColor("#F4F1EA"); white = colors.white
    title = ParagraphStyle("v8title", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=27, leading=30, textColor=ink, spaceAfter=10)
    h1 = ParagraphStyle("v8h1", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=18, leading=21, textColor=accent, spaceBefore=6, spaceAfter=7)
    h2 = ParagraphStyle("v8h2", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=10.5, leading=13, textColor=ink, spaceBefore=5, spaceAfter=3)
    body = ParagraphStyle("v8body", parent=styles["BodyText"], fontName="Helvetica", fontSize=8.3, leading=11.2, textColor=ink, spaceAfter=5)
    small = ParagraphStyle("v8small", parent=body, fontSize=6.7, leading=8.4)
    deck = ParagraphStyle("v8deck", parent=body, fontSize=11, leading=14.5, spaceAfter=8)
    doc = SimpleDocTemplate(str(path), pagesize=letter, leftMargin=.46*inch, rightMargin=.46*inch, topMargin=.45*inch, bottomMargin=.45*inch, title="FSFFL Alternate History - League Story Edition")

    def table(rows, widths, header=ink, fs=6.5):
        t = Table(rows, colWidths=widths, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,0),header),("TEXTCOLOR",(0,0),(-1,0),white),
            ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,-1),fs),
            ("GRID",(0,0),(-1,-1),.25,colors.lightgrey),("VALIGN",(0,0),(-1,-1),"TOP"),
            ("ROWBACKGROUNDS",(0,1),(-1,-1),[white,pale]),
        ]))
        return t

    story = [
        Paragraph("FSFFL ALTERNATE HISTORY", h2),
        Paragraph(_safe(report["scenario"].get("title")), title),
        Paragraph("A league-wide time machine: one changed decision, followed through the transactions, drafts, rosters, seasons and present-day power structure it alters.", deck),
        Paragraph("THE BOTTOM LINE", h1),
    ]
    for p in report.get("bottom_line_narrative") or []:
        story.append(Paragraph(_safe(p), deck if len(story) < 7 else body))

    story += [Spacer(1,5), Paragraph("CHAIN REACTION", h1)]
    for i, x in enumerate(report.get("chain_reaction") or [], 1):
        story.append(Paragraph(_safe(f"{i}. {x['label']} - {x['text']}"), body))
    if report.get("point_of_no_return"):
        x = report["point_of_no_return"]
        story += [Paragraph("POINT OF NO RETURN", h2), Paragraph(_safe(f"The first major downstream break is {x.get('actual_transaction')}, altered or removed in {_pct(x.get('probability_changed_or_removed'))} of alternate timelines."), body)]
    story.append(PageBreak())

    story += [Paragraph("BIGGEST BUTTERFLY EFFECTS", h1), Paragraph("These are ranked for league significance, not just standings movement. Star ownership, erased/altered deals and draft cascades are intentionally weighted above ordinary seed changes.", deck)]
    for e in report.get("butterflies") or []:
        if e["rank"] > 14: break
        story.append(Paragraph(_safe(f"{e['rank']}. {e['sentence']}"), body))

    story += [Spacer(1,6), Paragraph("BIGGEST ASSET SWINGS", h1)]
    arows = [["Player", "Reality", "Alternate present"]]
    for x in (report.get("ownership_swings") or [])[:12]:
        arows.append([_safe(x["player_name"]), _safe(x["actual_team"]), _safe(x["alternate_team"])])
    story.append(table(arows,[1.65*inch,2.45*inch,2.45*inch],accent,7.0))
    story.append(PageBreak())

    story += [Paragraph("WHO WON - AND WHO LOST - FROM THE FORK?", h1)]
    wrows = [["Team", "Alt rank move", "Playoff delta", "Title delta", "Roster value delta"]]
    for x in report.get("who_won_lost") or []:
        wrows.append([_safe(x["team"]), f"{x['power_rank_change']:+d}", f"{x['playoff_delta']*100:+.1f} pp", f"{x['title_delta']*100:+.1f} pp", f"{x['asset_delta']:+,.0f}"])
    story.append(table(wrows,[2.15*inch,.85*inch,1.0*inch,.9*inch,1.15*inch],ink,6.8))
    story.append(PageBreak())

    for chapter in report.get("season_chapters") or []:
        season = next(s for s in report["seasons"] if s["season"] == chapter["season"])
        story.append(Paragraph(f"{chapter['season']}: THE ALTERNATE SEASON", h1))
        for p in chapter.get("paragraphs") or []:
            story.append(Paragraph(_safe(p), body))
        story.append(Paragraph("Most likely alternate means the single most common outcome across retained alternate timelines. If reality is still the most common result, it will appear unchanged here.", small))
        srows = [["Alt #","Team","Actual","Most likely alternate","80% range","Playoffs","Title"]]
        for r in season.get("alternate_expected_standings") or []:
            lo, hi = r.get("likely_seed_low"), r.get("likely_seed_high")
            srows.append([r["alternate_rank"],_safe(r["team"]),f"#{r['actual_seed']}" if r.get("actual_seed") else "-",f"#{r['most_likely_seed']} ({_pct(r.get('most_likely_seed_probability'))})",f"#{lo}-#{hi}",_pct(r.get("playoff_probability")),_pct(r.get("championship_probability"))])
        story.append(table(srows,[.35*inch,1.65*inch,.48*inch,1.25*inch,.62*inch,.65*inch,.55*inch],ink,6.2))

        changed = [t for t in report["transactions"] if str(t.get("season")) == str(chapter["season"])]
        changed.sort(key=lambda t: -float(t.get("probability_changed_or_removed") or 0))
        if changed:
            story += [Spacer(1,6), Paragraph("MAJOR TRANSACTION CHANGES", h2)]
            for t in changed[:7]:
                if float(t.get("probability_changed_or_removed") or 0) < .25: continue
                story.append(Paragraph(_safe(f"{_pct(t.get('probability_changed_or_removed'))} changed/removed - {t.get('actual_transaction')}"), small))

        draft = chapter.get("following_draft")
        if draft:
            story += [Spacer(1,7), Paragraph(f"{draft['draft_season']} DRAFT - REALITY VS ALTERNATE", h2), Paragraph("Picks are shown in pick order. The alternate selection is from one coherent retained draft; the change percentage is the chance that this slot produces a different player than reality.", small)]
            drows = [["Pick","Actual team","Actual player","Alt team","Alt player","Change"]]
            for p in sorted(draft.get("picks") or [], key=lambda x:(int(x.get("round") or 0),int(x.get("slot") or 0))):
                drows.append([p["pick"],_safe(p.get("actual_team")),_safe(p.get("actual_player_name")),_safe(p.get("representative_team")),_safe(p.get("representative_player_name")),_pct(p.get("selection_change_probability"))])
            story.append(table(drows,[.4*inch,1.25*inch,1.35*inch,1.25*inch,1.35*inch,.55*inch],accent,5.8))
        story.append(PageBreak())

    story += [Paragraph("WHERE EVERYONE ENDS UP", h1), Paragraph("Each team is shown side-by-side: the real current roster and one coherent alternate present. Alternate starters are optimized with the same current Simulator lineup rules; taxi, reserve and future-pick ownership come directly from the retained alternate state.", deck)]
    for r in report["present_day"]["rosters"]:
        a, b = r["actual_comparison"], r["alternate_comparison"]
        story.append(Paragraph(_safe(r["team"]), h2))
        rows = [["Category","REALITY","ALTERNATE PRESENT"],
                ["Starters",_safe(_name_rows(a["starters"])),_safe(_name_rows(b["starters"]))],
                ["Bench",_safe(_name_rows(a["bench"])),_safe(_name_rows(b["bench"]))],
                ["Taxi",_safe(_name_rows(a["taxi"])),_safe(_name_rows(b["taxi"]))],
                ["Reserve/IR",_safe(_name_rows(a["reserve"])),_safe(_name_rows(b["reserve"]))],
                ["Future picks",_safe(", ".join(x["label"] for x in a["future_picks"]) or "-"),_safe(", ".join(x["label"] for x in b["future_picks"]) or "-")]]
        story.append(KeepTogether([table(rows,[.75*inch,3.0*inch,3.0*inch],ink,5.9),Spacer(1,6)]))
    story.append(PageBreak())

    story += [Paragraph("PRESENT-DAY POWER RANKINGS - REALITY VS ALTERNATE", h1), Paragraph("The 'Reality' columns are the Simulator outlook for the league as it actually exists today. The 'Alternate' columns use the coherent counterfactual present-day rosters.", deck)]
    prows = [["Team","Real #","Alt #","Real wins","Alt wins","Real PO","Alt PO","Real title","Alt title"]]
    for r in report["present_day"]["power_rankings"]["teams"]:
        a, b = r["actual"], r["alternate"]
        prows.append([_safe(r["team"]),r["actual_power_rank"],r["alternate_power_rank"],f"{float(a.get('expected_wins') or 0):.1f}",f"{float(b.get('expected_wins') or 0):.1f}",_pct(a.get("playoff_probability")),_pct(b.get("playoff_probability")),_pct(a.get("championship_probability")),_pct(b.get("championship_probability"))])
    story.append(table(prows,[1.55*inch,.42*inch,.42*inch,.55*inch,.55*inch,.55*inch,.55*inch,.62*inch,.62*inch],ink,5.7))

    wd = report.get("what_didnt_change") or {}
    story += [Spacer(1,10), Paragraph("WHAT DIDN'T CHANGE", h1)]
    stable = ", ".join(_safe(x) for x in wd.get("stable_power_teams") or []) or "No team holds exactly the same Simulator rank."
    story.append(Paragraph(_safe(f"Stable present-day power ranks: {stable}."), body))
    story.append(Paragraph(_safe(f"{wd.get('low_change_draft_slots',0)} of {wd.get('total_draft_slots',0)} modeled draft slots have less than a 20% chance of changing player. The model does not force chaos where the historical path remains resilient."), body))

    story += [PageBreak(), Paragraph("HOW THIS WAS BUILT", h1), Paragraph("This is a publication layer over the validated Alternate History engine. Completed NFL outcomes remain fixed historical facts. The counterfactual changes fantasy ownership and decisions, then carries those effects through historical transactions, standings, playoffs, drafts, current rosters and Simulator 1.0. Narrative sections summarize model outputs; they do not invent football outcomes.", body), Paragraph(_safe(f"Audit: {report['configuration']['particles']} retained historical timelines; {report['configuration']['simulator_sims']} Simulator draws; probability mass {_pct(report['summary']['probability_mass'])}."), small)]
    doc.build(story)


def _validate(report):
    v6._validate_publication(report)
    for r in report["present_day"]["rosters"]:
        if not r.get("actual_comparison") or not r.get("alternate_comparison"):
            raise base.ah.AlternateHistoryError(f"{r.get('team')} missing side-by-side roster comparison")
    if not report.get("ownership_swings"):
        raise base.ah.AlternateHistoryError("V8 requires ownership swing analysis")
    if not report.get("bottom_line_narrative"):
        raise base.ah.AlternateHistoryError("V8 requires league narrative")


def run(scenario_path: Path, *, particles: int, n_sims: int, seed: int) -> Tuple[Path, Path]:
    v6.v4._REPRESENTATIVE_DRAFTS.clear()
    v6.v3._CAPTURED_DRAFT_AUDITS.clear()
    _, groups, generic_report = generic.run_generic(scenario_path, particles=particles, seed=seed, return_groups=True)
    payload = load(scenario_path) or {}
    adapter = FSFFLHistoricalAdapter(); scenario = ah.scenario_from_json(adapter, payload)
    total = sum(int(g.count) for g in groups)
    if total != int(particles):
        raise ah.AlternateHistoryError("publication particle mass mismatch")

    names = v1.player_names(); teams = v1.team_names()
    active = int(generic_report.get("active_season") or 0); fork = int(payload.get("fork_season") or 0)
    seasons = [v6._league_season(groups, str(y), total, teams, adapter) for y in range(fork, active)]
    drafts = v6._league_drafts(groups, [str(y) for y in range(fork + 1, active + 1)], total, names, teams)
    txs = v2._transaction_effects(groups, total, names, teams)
    rosters, representative = _enrich_present_rosters(groups, total, names, teams, adapter)
    power = base._league_simulator(groups, n_sims, total, teams)
    focus_rid = str(scenario.focus_roster_id)
    focus_power = next(r for r in power["teams"] if r["roster_id"] == focus_rid)
    actual_players = [str(x) for x in ((base._actual_team_map().get(focus_rid) or {}).get("players") or [])]
    focus_roster = v1.roster_distribution(groups, focus_rid, total, names, actual_players)
    draft_map = {d["draft_season"]: d for d in drafts}
    chapters = [v6._season_story(s, draft_map.get(str(int(s["season"]) + 1)), txs) for s in seasons]

    values = _asset_values()
    ownership = _ownership_swings(rosters, values, teams)
    butterflies = _biggest_butterflies(seasons, drafts, txs, power, ownership)
    winners = _team_winners(rosters, power, values)

    report = {
        "model_version": "Fantasy-Alternate-History-1.4-league-story",
        "scenario": {"scenario_id": payload.get("scenario_id"), "title": payload.get("title"), "fork_season": str(payload.get("fork_season")), "fork_week": int(payload.get("fork_week") or 0), "focus_roster_id": focus_rid},
        "configuration": {"particles": int(particles), "simulator_sims": int(n_sims), "seed": int(seed)},
        "summary": {"probability_mass": round(total / particles, 10), "present_day_unique_states": len(groups), "seasons_traversed": generic_report.get("summary",{}).get("seasons_traversed")},
        "focus_franchise": {"roster_id": focus_rid, "team": teams.get(focus_rid, f"Roster {focus_rid}"), "roster_divergence_score": focus_roster["present_day_roster_divergence_score"], "simulator_deltas": focus_power["deltas"]},
        "seasons": seasons, "season_chapters": chapters, "drafts": drafts,
        "transactions": txs, "butterflies": butterflies,
        "present_day": {"rosters": rosters, "power_rankings": power},
        "ownership_swings": ownership, "who_won_lost": winners,
        "design_invariants": {"presentation_layer_only": True, "facts_derived_from_retained_model_state": True, "no_llm_generated_football_outcomes": True, "completed_nfl_history_immutable": True, "probability_mass_conserved": True},
    }
    impacted = _impacted_teams(payload, txs, report["focus_franchise"]["team"])
    report["directly_impacted_teams"] = impacted
    report["bottom_line_narrative"] = _narrative(report, impacted)
    _story_metadata(report)
    _validate(report)

    out = DATA / "alternate_history" / "results" / str(payload.get("scenario_id")); out.mkdir(parents=True, exist_ok=True)
    jp = out / "alternate_history_magazine_1_0.json"; pp = out / "alternate_history_magazine_1_0.pdf"
    jp.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    _render_pdf(report, pp)
    print(jp); print(pp)
    print(json.dumps({"probability_mass": report["summary"]["probability_mass"], "seasons": len(seasons), "drafts": len(drafts), "teams": len(rosters), "power_ranked_teams": len(power["teams"]), "butterflies": len(butterflies), "ownership_swings": len(ownership)}, indent=2, sort_keys=True))
    return jp, pp


def main() -> None:
    p = argparse.ArgumentParser(description="Render league-story FSFFL Alternate History magazine")
    p.add_argument("scenario", type=Path)
    p.add_argument("--particles", type=int, default=base.DEFAULT_PARTICLES)
    p.add_argument("--sims", type=int, default=base.DEFAULT_SIMS)
    p.add_argument("--seed", type=int, default=base.DEFAULT_SEED)
    a = p.parse_args()
    run(a.scenario, particles=a.particles, n_sims=a.sims, seed=a.seed)


if __name__ == "__main__":
    main()
