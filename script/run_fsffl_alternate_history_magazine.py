#!/usr/bin/env python3
"""Build the publication-grade FSFFL Alternate History magazine.

This module is deliberately downstream of the validated historical engine.  It
never changes branch probabilities, historical NFL outcomes, transaction/draft
policy, lineup rules, MaxPF, or Simulator inputs.  It turns retained particle
state into a league-wide publication dataset and renders a deterministic PDF.

The prose is template driven.  Every factual sentence is derived from retained
state, archived Sleeper history, or Simulator 1.0 output generated in this run.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import alternate_history_engine as ah
import run_fsffl_alternate_history_final_report as v1
import run_fsffl_alternate_history_report_v2 as v2
import run_fsffl_alternate_rookie_draft_particles as draft_runner
import run_fsffl_generic_alternate_history as generic
import run_fsffl_multiseason_particle_replay_v3 as season_v3
import run_fsffl_weighted_alternate_outlook as weighted
from run_fsffl_alternate_draft_candidates import raw_draft
from run_fsffl_alternate_draft_policy import normalized_picks
from run_fsffl_alternate_history import FSFFLHistoricalAdapter
from run_fsffl_downstream_dependencies import load
from run_fsffl_gm30_counterfactual import CounterfactualEngine

DATA = Path("data")
DEFAULT_PARTICLES = 100
DEFAULT_SIMS = 500
DEFAULT_SEED = 20260824


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100.0 * float(x):.1f}%"


def _num(x: float | None, digits: int = 1) -> str:
    return "n/a" if x is None else f"{float(x):.{digits}f}"


def _weighted(counter: Counter, total: int, limit: int = 12) -> List[Dict[str, Any]]:
    return [
        {"value": key, "particles": int(count), "probability": round(count / total, 8)}
        for key, count in counter.most_common(limit)
    ]


def _actual_team_map() -> Dict[str, Dict[str, Any]]:
    return {str(r.get("roster_id")): r for r in (load(DATA / "rosters.json") or [])}


def _league_season(groups, season: str, total: int, teams: Dict[str, str], adapter) -> Dict[str, Any]:
    focus_actual = v2._actual_regular_season(str(season), "1", adapter)
    actual_standings = focus_actual.get("standings") or []
    actual_by_rid = {str(r.get("roster_id")): r for r in actual_standings}

    acc: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "covered": 0, "wins": 0.0, "pf": 0.0, "maxpf": 0.0,
        "seeds": Counter(), "finishes": Counter(), "playoffs": 0, "titles": 0,
    })
    bracket_counter: Counter = Counter()
    bracket_example: Dict[str, Dict[str, Any]] = {}
    champion_counter: Counter = Counter()

    for group in groups:
        row = ((group.state.get(season_v3.LEDGER_KEY) or {}).get(str(season)) or {})
        standings = row.get("standings") or []
        if not standings:
            continue
        count = int(group.count)
        playoff_field = {str(x) for x in (row.get("playoff_field") or [])}
        post = row.get("postseason") or {}
        finish = {str(k): int(v) for k, v in (post.get("finish_by_roster") or {}).items()}
        champ = str(((post.get("championship") or {}).get("winner")) or "")
        if champ:
            champion_counter[champ] += count
        if post:
            key = json.dumps(post, sort_keys=True, separators=(",", ":"))
            bracket_counter[key] += count
            bracket_example.setdefault(key, post)
        maxpf = row.get("season_max_pf") or {}
        for s in standings:
            rid = str(s.get("roster_id"))
            a = acc[rid]
            a["covered"] += count
            a["wins"] += count * float(s.get("wins") or 0.0)
            a["pf"] += count * float(s.get("points_for") or 0.0)
            a["maxpf"] += count * float(maxpf.get(rid) or 0.0)
            seed = int(s.get("seed") or 0)
            if seed:
                a["seeds"][str(seed)] += count
            if rid in playoff_field:
                a["playoffs"] += count
            if rid in finish:
                a["finishes"][str(finish[rid])] += count
            if rid == champ:
                a["titles"] += count

    rows = []
    for rid, a in acc.items():
        covered = int(a["covered"])
        if not covered:
            continue
        exp_seed = sum(float(seed) * n for seed, n in a["seeds"].items()) / covered if a["seeds"] else None
        actual = actual_by_rid.get(rid) or {}
        rows.append({
            "roster_id": rid,
            "team": teams.get(rid, f"Roster {rid}"),
            "actual_seed": int(actual.get("seed") or 0) or None,
            "actual_wins": float(actual.get("wins") or 0.0),
            "actual_points_for": float(actual.get("points_for") or 0.0),
            "expected_seed": round(exp_seed, 4) if exp_seed is not None else None,
            "expected_wins": round(a["wins"] / covered, 4),
            "expected_points_for": round(a["pf"] / covered, 4),
            "expected_max_pf": round(a["maxpf"] / covered, 4),
            "playoff_probability": round(a["playoffs"] / total, 8),
            "championship_probability": round(a["titles"] / total, 8),
            "seed_distribution": _weighted(a["seeds"], total),
            "finish_distribution": _weighted(a["finishes"], total),
            "seed_delta_vs_actual": None if exp_seed is None or not actual else round(exp_seed - float(actual.get("seed") or 0), 4),
            "wins_delta_vs_actual": round(a["wins"] / covered - float(actual.get("wins") or 0.0), 4),
            "pf_delta_vs_actual": round(a["pf"] / covered - float(actual.get("points_for") or 0.0), 4),
        })
    rows.sort(key=lambda r: (float(r["expected_seed"] or 99), -float(r["expected_wins"]), -float(r["expected_points_for"]), r["team"]))
    for i, row in enumerate(rows, 1):
        row["alternate_rank"] = i

    modal_post = None
    if bracket_counter:
        key, count = bracket_counter.most_common(1)[0]
        modal_post = {
            "probability": round(count / total, 8),
            "postseason": bracket_example[key],
        }

    champions = []
    for rid, count in champion_counter.most_common():
        champions.append({"roster_id": rid, "team": teams.get(rid, f"Roster {rid}"), "probability": round(count / total, 8)})

    return {
        "season": str(season),
        "actual_standings": actual_standings,
        "alternate_expected_standings": rows,
        "champion_distribution": champions,
        "modal_postseason": modal_post,
    }


def _league_drafts(groups, seasons: Iterable[str], total: int, names: Dict[str, str], teams: Dict[str, str]) -> List[Dict[str, Any]]:
    out = []
    for season in seasons:
        try:
            actual = normalized_picks(raw_draft(str(season)))
        except Exception:
            actual = []
        actual_by_pick: Dict[Tuple[int, int], Dict[str, Any]] = {}
        for p in actual:
            rnd = int(p.get("round") or 0)
            slot = int(p.get("draft_slot") or p.get("slot") or p.get("pick_no") or 0)
            if rnd and slot:
                actual_by_pick[(rnd, slot)] = p

        choices: Dict[Tuple[int, int], Counter] = defaultdict(Counter)
        controllers: Dict[Tuple[int, int], Counter] = defaultdict(Counter)
        for group in groups:
            node = group.state.get(draft_runner.DRAFT_KEY) or {}
            seen = set()
            for p in node.get("picks") or []:
                if str(p.get("draft_season")) != str(season):
                    continue
                rnd = int(p.get("round") or 0); slot = int(p.get("slot") or 0)
                pid = str(p.get("player_id") or ""); rid = str(p.get("controller_roster_id") or "")
                key = (rnd, slot, pid, rid)
                if not rnd or not slot or not pid or key in seen:
                    continue
                seen.add(key)
                choices[(rnd, slot)][pid] += int(group.count)
                if rid:
                    controllers[(rnd, slot)][rid] += int(group.count)

        pick_keys = sorted(set(actual_by_pick) | set(choices))
        picks = []
        for key in pick_keys:
            rnd, slot = key
            ap = actual_by_pick.get(key) or {}
            actual_pid = str(ap.get("player_id") or "")
            actual_rid = str(ap.get("roster_id") or ap.get("original_roster_id") or "")
            alt_choices = [{"player_id": pid, "player_name": names.get(pid, pid), "probability": round(n / total, 8)} for pid, n in choices[key].most_common(6)]
            alt_ctrl = [{"roster_id": rid, "team": teams.get(rid, f"Roster {rid}"), "probability": round(n / total, 8)} for rid, n in controllers[key].most_common(4)]
            top_pid = alt_choices[0]["player_id"] if alt_choices else None
            picks.append({
                "round": rnd, "slot": slot, "pick": f"{rnd}.{slot:02d}",
                "actual_player_id": actual_pid or None,
                "actual_player_name": names.get(actual_pid, actual_pid) if actual_pid else None,
                "actual_roster_id": actual_rid or None,
                "actual_team": teams.get(actual_rid, f"Roster {actual_rid}") if actual_rid else None,
                "alternate_choices": alt_choices,
                "alternate_controllers": alt_ctrl,
                "most_likely_selection_changed": bool(top_pid and actual_pid and top_pid != actual_pid),
            })
        if picks:
            out.append({"draft_season": str(season), "picks": picks})
    return out


def _present_rosters(groups, total: int, names: Dict[str, str], teams: Dict[str, str]) -> List[Dict[str, Any]]:
    actual = _actual_team_map()
    all_rids = sorted(set(teams) | {str(r) for g in groups for r in (g.state.get("roster_players") or {})}, key=lambda x: int(x) if x.isdigit() else 999)
    rows = []
    for rid in all_rids:
        exact: Counter = Counter()
        membership: Counter = Counter()
        for group in groups:
            roster = tuple(sorted(str(x) for x in ((group.state.get("roster_players") or {}).get(rid) or [])))
            exact[roster] += int(group.count)
            for pid in roster:
                membership[pid] += int(group.count)
        modal, count = exact.most_common(1)[0] if exact else (tuple(), 0)
        actual_players = {str(x) for x in ((actual.get(rid) or {}).get("players") or [])}
        modal_set = set(modal)
        rows.append({
            "roster_id": rid,
            "team": teams.get(rid, f"Roster {rid}"),
            "modal_probability": round(count / total, 8),
            "modal_roster": [{"player_id": pid, "player_name": names.get(pid, pid), "membership_probability": round(membership[pid] / total, 8)} for pid in modal],
            "consensus_roster": [{"player_id": pid, "player_name": names.get(pid, pid), "membership_probability": round(n / total, 8)} for pid, n in membership.most_common() if n / total >= 0.5],
            "gained_vs_actual": [{"player_id": pid, "player_name": names.get(pid, pid), "membership_probability": round(membership[pid] / total, 8)} for pid in sorted(modal_set - actual_players)],
            "lost_vs_actual": [{"player_id": pid, "player_name": names.get(pid, pid), "retention_probability": round(membership.get(pid, 0) / total, 8)} for pid in sorted(actual_players - modal_set)],
        })
    return rows


def _league_simulator(groups, n_sims: int, total: int, teams: Dict[str, str]) -> Dict[str, Any]:
    groups = sorted(groups, key=lambda g: (-g.count, json.dumps(g.state, sort_keys=True)))
    allocations = weighted.allocate_sims(groups, int(n_sims))
    engine = CounterfactualEngine()
    baseline = engine.baseline(int(n_sims))
    baseline_by_uid = {str(r.get("user_id")): r for r in (baseline.get("teams") or [])}
    rid_to_uid = {str(rid): str(uid) for rid, uid in engine.roster_id_to_uid.items()}
    alt_rows: Dict[str, List[Tuple[float, Dict[str, Any]]]] = defaultdict(list)
    for group, sims in zip(groups, allocations):
        weight = group.count / total
        result = engine._run(weighted.simulator_rosters_from_state(engine, group.state), int(sims))
        by_uid = {str(r.get("user_id")): r for r in (result.get("teams") or [])}
        for rid, uid in rid_to_uid.items():
            alt_rows[rid].append((weight, by_uid.get(uid, {})))

    results = []
    for rid, uid in rid_to_uid.items():
        actual = baseline_by_uid.get(uid, {})
        alt = {key: weighted.weighted_metric(alt_rows[rid], key) for key in weighted.METRICS}
        actual_metrics = {key: actual.get(key) for key in weighted.METRICS}
        results.append({
            "roster_id": rid,
            "team": teams.get(rid, f"Roster {rid}"),
            "actual": actual_metrics,
            "alternate": alt,
            "deltas": {key: None if alt.get(key) is None or actual_metrics.get(key) is None else round(float(alt[key]) - float(actual_metrics[key]), 6) for key in weighted.METRICS},
        })

    # A transparent deterministic power order: expected wins is primary, then
    # expected points, championship probability, playoff probability.  No
    # editorial/LLM judgment enters the ordering.
    actual_order = sorted(results, key=lambda r: (-float(r["actual"].get("expected_wins") or 0), -float(r["actual"].get("expected_points_for") or 0), -float(r["actual"].get("championship_probability") or 0), r["team"]))
    alt_order = sorted(results, key=lambda r: (-float(r["alternate"].get("expected_wins") or 0), -float(r["alternate"].get("expected_points_for") or 0), -float(r["alternate"].get("championship_probability") or 0), r["team"]))
    actual_rank = {r["roster_id"]: i for i, r in enumerate(actual_order, 1)}
    alt_rank = {r["roster_id"]: i for i, r in enumerate(alt_order, 1)}
    for r in results:
        r["actual_power_rank"] = actual_rank[r["roster_id"]]
        r["alternate_power_rank"] = alt_rank[r["roster_id"]]
        r["power_rank_change"] = actual_rank[r["roster_id"]] - alt_rank[r["roster_id"]]
    results.sort(key=lambda r: r["alternate_power_rank"])
    return {"method": "Expected wins, then expected points, championship probability, playoff probability", "simulator_draws": sum(allocations), "teams": results}


def _butterflies(seasons, drafts, transactions, power) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for season in seasons:
        y = season["season"]
        for row in season["alternate_expected_standings"]:
            if row["actual_seed"] is None or row["expected_seed"] is None:
                continue
            swing = abs(float(row["expected_seed"]) - float(row["actual_seed"]))
            if swing >= 0.75:
                events.append({"kind": "SEED_SWING", "season": y, "team": row["team"], "impact": round(swing, 4), "sentence": f"{row['team']} moves from the actual No. {row['actual_seed']} seed to an expected No. {_num(row['expected_seed'], 2)} seed."})
    for draft in drafts:
        for pick in draft["picks"]:
            if pick.get("most_likely_selection_changed") and pick.get("alternate_choices"):
                alt = pick["alternate_choices"][0]
                p = float(alt["probability"])
                events.append({"kind": "DRAFT_PICK_CHANGED", "season": draft["draft_season"], "team": (pick.get("alternate_controllers") or [{}])[0].get("team"), "impact": round(1.5 + p, 4), "sentence": f"At {pick['pick']} in the {draft['draft_season']} rookie draft, {pick.get('actual_player_name') or 'the actual selection'} gives way most often to {alt['player_name']} ({_pct(p)})."})
    for tx in transactions:
        p = float(tx.get("probability_changed_or_removed") or 0.0)
        if p >= 0.25:
            events.append({"kind": "TRADE_CHANGED", "season": tx.get("season"), "team": None, "impact": round(2.0 * p, 4), "sentence": f"A {tx.get('season') or 'downstream'} historical transaction changes or disappears in {_pct(p)} of alternate branches: {tx['actual_transaction']}."})
    for row in power.get("teams") or []:
        change = int(row.get("power_rank_change") or 0)
        if abs(change) >= 2:
            direction = "rises" if change > 0 else "falls"
            events.append({"kind": "POWER_RANK_SWING", "season": "present", "team": row["team"], "impact": 1.0 + abs(change) / 3.0, "sentence": f"By the present day, {row['team']} {direction} {abs(change)} spots in the Simulator power order, from No. {row['actual_power_rank']} to No. {row['alternate_power_rank']}."})
    events.sort(key=lambda e: (-float(e["impact"]), str(e.get("season")), str(e.get("team"))))
    for i, e in enumerate(events, 1): e["rank"] = i
    return events


def _season_story(season: Dict[str, Any], next_draft: Dict[str, Any] | None, txs: List[Dict[str, Any]]) -> Dict[str, Any]:
    y = season["season"]
    alt = season["alternate_expected_standings"]
    biggest = sorted([r for r in alt if r.get("actual_seed") and r.get("expected_seed")], key=lambda r: -abs(float(r["expected_seed"]) - float(r["actual_seed"])))[:3]
    champ = (season.get("champion_distribution") or [{}])[0]
    paragraphs = []
    if champ:
        paragraphs.append(f"The most likely {y} champion in the alternate timeline is {champ.get('team')} at {_pct(champ.get('probability'))}. The regular season is reordered from the actual standings by the fork and its downstream roster decisions.")
    if biggest:
        bits = [f"{r['team']} ({r['actual_seed']} to {_num(r['expected_seed'],2)})" for r in biggest]
        paragraphs.append("The largest expected seed movements are " + ", ".join(bits) + ".")
    changed = [t for t in txs if str(t.get("season")) == str(y) and float(t.get("probability_changed_or_removed") or 0) >= 0.10][:5]
    if changed:
        paragraphs.append(f"The season also contains {len(changed)} prominently altered historical transaction decisions; the transaction cards below show whether the original deal survives, is substituted, or disappears.")
    if next_draft and next_draft.get("picks"):
        changed_picks = sum(1 for p in next_draft["picks"] if p.get("most_likely_selection_changed"))
        paragraphs.append(f"Those standings and postseason consequences feed directly into the {next_draft['draft_season']} rookie draft, where {changed_picks} selections have a different most-likely player than actual history.")
    return {"season": y, "paragraphs": paragraphs, "major_seed_swings": biggest, "changed_transactions": changed, "following_draft": next_draft}


def _validate_publication(report: Dict[str, Any]) -> None:
    if abs(float(report["summary"]["probability_mass"]) - 1.0) > 1e-9:
        raise ah.AlternateHistoryError("publication probability mass is not 1.0")
    teams = report["present_day"]["rosters"]
    if len(teams) != 12:
        raise ah.AlternateHistoryError(f"publication requires 12 present-day rosters, got {len(teams)}")
    power = report["present_day"]["power_rankings"]["teams"]
    if len(power) != 12 or sorted(r["alternate_power_rank"] for r in power) != list(range(1, 13)):
        raise ah.AlternateHistoryError("publication power ranking does not contain unique ranks 1-12")
    for season in report["seasons"]:
        if len(season["alternate_expected_standings"]) != 12:
            raise ah.AlternateHistoryError(f"{season['season']} alternate standings do not contain 12 teams")
        if len(season["actual_standings"]) != 12:
            raise ah.AlternateHistoryError(f"{season['season']} actual standings do not contain 12 teams")


def _render_pdf(report: Dict[str, Any], path: Path) -> None:
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER, TA_LEFT
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import BaseDocTemplate, Frame, KeepTogether, PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle
    except ImportError as exc:
        raise ah.AlternateHistoryError("reportlab is required to render the Alternate History magazine PDF") from exc

    styles = getSampleStyleSheet()
    ink = colors.HexColor("#172133"); accent = colors.HexColor("#B11F2E"); gold = colors.HexColor("#C9972B")
    pale = colors.HexColor("#F4F1EA"); rule = colors.HexColor("#D6D6D6"); white = colors.white
    title = ParagraphStyle("MagazineTitle", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=30, leading=32, textColor=ink, alignment=TA_LEFT, spaceAfter=12)
    deck = ParagraphStyle("Deck", parent=styles["BodyText"], fontName="Helvetica", fontSize=13, leading=18, textColor=ink, spaceAfter=12)
    h1 = ParagraphStyle("H1", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=21, leading=24, textColor=accent, spaceBefore=4, spaceAfter=10)
    h2 = ParagraphStyle("H2", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=13, leading=16, textColor=ink, spaceBefore=8, spaceAfter=6)
    body = ParagraphStyle("Body", parent=styles["BodyText"], fontName="Helvetica", fontSize=9.5, leading=13.5, textColor=ink, spaceAfter=7)
    small = ParagraphStyle("Small", parent=body, fontSize=7.4, leading=9.5)
    callout = ParagraphStyle("Callout", parent=body, fontName="Helvetica-Bold", fontSize=12, leading=16, textColor=ink, borderColor=gold, borderWidth=1, borderPadding=9, backColor=pale, spaceBefore=6, spaceAfter=10)

    class Doc(BaseDocTemplate):
        pass
    doc = Doc(str(path), pagesize=letter, leftMargin=0.55*inch, rightMargin=0.55*inch, topMargin=0.55*inch, bottomMargin=0.55*inch, title="FSFFL Alternate History")
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="normal")
    def page(canvas, d):
        canvas.saveState(); canvas.setStrokeColor(rule); canvas.line(doc.leftMargin, 0.38*inch, letter[0]-doc.rightMargin, 0.38*inch)
        canvas.setFont("Helvetica", 7); canvas.setFillColor(ink); canvas.drawString(doc.leftMargin, 0.23*inch, "FSFFL ALTERNATE HISTORY")
        canvas.drawRightString(letter[0]-doc.rightMargin, 0.23*inch, str(d.page)); canvas.restoreState()
    doc.addPageTemplates(PageTemplate(id="main", frames=[frame], onPage=page))
    story = []
    s = report["scenario"]
    focus = report["focus_franchise"]
    story += [Spacer(1, 0.3*inch), Paragraph("FSFFL ALTERNATE HISTORY", h2), Paragraph(s.get("title") or "Alternate History", title), Paragraph(f"What changes when one decision in {s['fork_season']} Week {s['fork_week']} sends the league down a different path? This edition follows the validated timeline from the fork through every completed season, rookie draft and the present-day league.", deck)]
    focus_delta = focus["simulator_deltas"]
    story += [Paragraph(f"THE VERDICT: {focus['team']} reaches the present with {_num(focus['roster_divergence_score'],1)}/100 roster divergence. Expected wins change by {_num(focus_delta.get('expected_wins'),2)}, playoff probability by {_pct(focus_delta.get('playoff_probability'))}, and championship probability by {_pct(focus_delta.get('championship_probability'))}.", callout)]
    story += [Paragraph("How to read this edition", h2), Paragraph("Completed NFL outcomes remain fixed. Fantasy ownership, decisions, standings, playoff routing, rookie draft order and selections may change. Standings are probability-weighted across retained historical particles; a modal playoff bracket or roster is the single most common exact outcome, not a claim that every branch is identical.", body), PageBreak()]

    story += [Paragraph("THE BUTTERFLY BOARD", h1), Paragraph("The largest deterministic changes identified by the publication engine.", deck)]
    for e in report["butterflies"][:12]:
        story.append(Paragraph(f"<b>{e['rank']}. {e['kind'].replace('_',' ').title()}</b> - {e['sentence']}", body))
    story.append(PageBreak())

    draft_map = {d["draft_season"]: d for d in report["drafts"]}
    for chapter in report["season_chapters"]:
        season = next(x for x in report["seasons"] if x["season"] == chapter["season"])
        story += [Paragraph(f"{chapter['season']}: THE TIMELINE IN MOTION", h1)]
        for p in chapter["paragraphs"]: story.append(Paragraph(p, deck if p == chapter["paragraphs"][0] else body))
        table = [["Alt", "Team", "Actual", "Exp W", "Exp PF", "Playoffs", "Title"]]
        for r in season["alternate_expected_standings"]:
            table.append([r["alternate_rank"], r["team"], r["actual_seed"] or "-", _num(r["expected_wins"],1), _num(r["expected_points_for"],0), _pct(r["playoff_probability"]), _pct(r["championship_probability"])])
        t = Table(table, colWidths=[0.36*inch,2.25*inch,0.48*inch,0.55*inch,0.62*inch,0.7*inch,0.62*inch], repeatRows=1)
        t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),ink),("TEXTCOLOR",(0,0),(-1,0),white),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTNAME",(0,1),(-1,-1),"Helvetica"),("FONTSIZE",(0,0),(-1,-1),7.2),("GRID",(0,0),(-1,-1),0.25,rule),("VALIGN",(0,0),(-1,-1),"MIDDLE"),("ROWBACKGROUNDS",(0,1),(-1,-1),[white,pale])]))
        story += [t, Spacer(1,8)]
        champs = season.get("champion_distribution") or []
        if champs:
            story += [Paragraph("Championship picture", h2), Paragraph("; ".join(f"{x['team']} {_pct(x['probability'])}" for x in champs[:5]), body)]
        if chapter["changed_transactions"]:
            story += [Paragraph("Major transaction deviations", h2)]
            for tx in chapter["changed_transactions"]:
                top = (tx.get("outcomes") or [{}])[0]
                story.append(Paragraph(f"{tx['actual_transaction']} - changed/removed {_pct(tx['probability_changed_or_removed'])}; most common branch outcome: {top.get('outcome','n/a')} {_pct(top.get('probability'))}.", small))
        draft = chapter.get("following_draft")
        if draft and draft.get("picks"):
            story += [Paragraph(f"{draft['draft_season']} rookie draft: what changed", h2)]
            drows = [["Pick","Actual","Most likely alternate","Probability"]]
            for p in draft["picks"]:
                alt = (p.get("alternate_choices") or [{}])[0]
                if p.get("most_likely_selection_changed") or (alt and float(alt.get("probability") or 0) >= .25):
                    drows.append([p["pick"], p.get("actual_player_name") or "-", alt.get("player_name") or "-", _pct(alt.get("probability"))])
            if len(drows) > 1:
                dt = Table(drows, colWidths=[0.6*inch,1.75*inch,1.95*inch,0.85*inch], repeatRows=1)
                dt.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),accent),("TEXTCOLOR",(0,0),(-1,0),white),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,-1),7.5),("GRID",(0,0),(-1,-1),0.25,rule),("ROWBACKGROUNDS",(0,1),(-1,-1),[white,pale])]))
                story.append(dt)
        story.append(PageBreak())

    story += [Paragraph("WHERE THE LEAGUE STANDS NOW", h1), Paragraph("Simulator-derived power rankings and modal present-day rosters after the alternate history has fully propagated.", deck)]
    prow = [["#","Team","Actual #","Exp W","Exp PF","Playoff","Title"]]
    for r in report["present_day"]["power_rankings"]["teams"]:
        a = r["alternate"]
        prow.append([r["alternate_power_rank"], r["team"], r["actual_power_rank"], _num(a.get("expected_wins"),1), _num(a.get("expected_points_for"),0), _pct(a.get("playoff_probability")), _pct(a.get("championship_probability"))])
    pt = Table(prow, colWidths=[0.3*inch,2.35*inch,0.5*inch,0.55*inch,0.62*inch,0.68*inch,0.62*inch], repeatRows=1)
    pt.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),ink),("TEXTCOLOR",(0,0),(-1,0),white),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,-1),7.2),("GRID",(0,0),(-1,-1),0.25,rule),("ROWBACKGROUNDS",(0,1),(-1,-1),[white,pale])]))
    story += [pt, PageBreak(), Paragraph("THE 12 ROSTERS", h1), Paragraph("Each roster below is the single most common exact present-day roster across the retained particles. The percentage is that modal roster's probability.", body)]
    for r in report["present_day"]["rosters"]:
        players = ", ".join(x["player_name"] for x in r["modal_roster"])
        changes = []
        if r["gained_vs_actual"]: changes.append("IN: " + ", ".join(x["player_name"] for x in r["gained_vs_actual"][:8]))
        if r["lost_vs_actual"]: changes.append("OUT: " + ", ".join(x["player_name"] for x in r["lost_vs_actual"][:8]))
        block = [Paragraph(f"{r['team']} - modal roster {_pct(r['modal_probability'])}", h2), Paragraph(players or "No roster players retained.", small)]
        if changes: block.append(Paragraph(" | ".join(changes), small))
        story.append(KeepTogether(block))

    story += [PageBreak(), Paragraph("METHODOLOGY & AUDIT NOTE", h1), Paragraph("This publication is a deterministic rendering of the same validated Alternate History particles used by the technical report. League-wide standings and postseason results are read from each branch's historical ledger; rookie drafts are read from branch draft state; transactions are joined to archived Sleeper history; present-day rosters are read from branch roster state; and power rankings are calculated from the same Simulator 1.0 outputs. No language model supplies football facts or chooses outcomes for the publication.", body)]
    story.append(Paragraph(f"Configuration: {report['configuration']['particles']} historical particles, {report['configuration']['simulator_sims']} Simulator draws, seed {report['configuration']['seed']}; probability mass {_pct(report['summary']['probability_mass'])}.", body))
    doc.build(story)


def run(scenario_path: Path, *, particles: int, n_sims: int, seed: int) -> Tuple[Path, Path]:
    _, groups, generic_report = generic.run_generic(scenario_path, particles=particles, seed=seed, return_groups=True)
    payload = load(scenario_path) or {}
    adapter = FSFFLHistoricalAdapter(); scenario = ah.scenario_from_json(adapter, payload)
    total = sum(int(g.count) for g in groups)
    if total != int(particles): raise ah.AlternateHistoryError("publication particle mass mismatch")
    names = v1.player_names(); teams = v1.team_names()
    active = int(generic_report.get("active_season") or 0); fork = int(payload.get("fork_season") or 0)
    seasons = [_league_season(groups, str(y), total, teams, adapter) for y in range(fork, active)]
    drafts = _league_drafts(groups, [str(y) for y in range(fork + 1, active + 1)], total, names, teams)
    txs = v2._transaction_effects(groups, total, names, teams)
    rosters = _present_rosters(groups, total, names, teams)
    power = _league_simulator(groups, n_sims, total, teams)
    focus_rid = str(scenario.focus_roster_id)
    focus_power = next(r for r in power["teams"] if r["roster_id"] == focus_rid)
    actual_focus = next((r for r in (_actual_team_map().get(focus_rid) or {}).get("players", [])), None)
    # Reuse exact focus roster divergence calculation already certified in v1.
    actual_players = [str(x) for x in ((_actual_team_map().get(focus_rid) or {}).get("players") or [])]
    focus_roster = v1.roster_distribution(groups, focus_rid, total, names, actual_players)
    draft_map = {d["draft_season"]: d for d in drafts}
    chapters = [_season_story(s, draft_map.get(str(int(s["season"]) + 1)), txs) for s in seasons]
    butterflies = _butterflies(seasons, drafts, txs, power)
    report = {
        "model_version": "Fantasy-Alternate-History-1.2-publication",
        "scenario": {"scenario_id": payload.get("scenario_id"), "title": payload.get("title"), "fork_season": str(payload.get("fork_season")), "fork_week": int(payload.get("fork_week") or 0), "focus_roster_id": focus_rid},
        "configuration": {"particles": int(particles), "simulator_sims": int(n_sims), "seed": int(seed)},
        "summary": {"probability_mass": round(total / particles, 10), "present_day_unique_states": len(groups), "seasons_traversed": generic_report.get("summary",{}).get("seasons_traversed")},
        "focus_franchise": {"roster_id": focus_rid, "team": teams.get(focus_rid, f"Roster {focus_rid}"), "roster_divergence_score": focus_roster["present_day_roster_divergence_score"], "simulator_deltas": focus_power["deltas"]},
        "seasons": seasons,
        "season_chapters": chapters,
        "drafts": drafts,
        "transactions": txs,
        "butterflies": butterflies,
        "present_day": {"rosters": rosters, "power_rankings": power},
        "design_invariants": {"presentation_layer_only": True, "facts_derived_from_retained_model_state": True, "no_llm_generated_football_outcomes": True, "completed_nfl_history_immutable": True, "probability_mass_conserved": True},
    }
    _validate_publication(report)
    base = DATA / "alternate_history" / "results" / str(payload.get("scenario_id")); base.mkdir(parents=True, exist_ok=True)
    jp = base / "alternate_history_magazine_1_0.json"; pp = base / "alternate_history_magazine_1_0.pdf"
    jp.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    _render_pdf(report, pp)
    print(jp); print(pp)
    print(json.dumps({"probability_mass": report["summary"]["probability_mass"], "seasons": len(seasons), "drafts": len(drafts), "teams": len(rosters), "power_ranked_teams": len(power["teams"]), "butterflies": len(butterflies)}, indent=2, sort_keys=True))
    return jp, pp


def main() -> None:
    p = argparse.ArgumentParser(description="Render deterministic FSFFL Alternate History magazine")
    p.add_argument("scenario", type=Path); p.add_argument("--particles", type=int, default=DEFAULT_PARTICLES); p.add_argument("--sims", type=int, default=DEFAULT_SIMS); p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    a = p.parse_args(); run(a.scenario, particles=a.particles, n_sims=a.sims, seed=a.seed)


if __name__ == "__main__": main()
