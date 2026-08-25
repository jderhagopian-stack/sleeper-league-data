#!/usr/bin/env python3
"""Narrative/audit-rich Alternate History final report.

Presentation layer only. Reuses the validated generic historical engine and the
existing final-report Simulator boundary, while exposing branch lineage that the
v1 report already had in memory but did not render intelligibly.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import alternate_history_engine as ah
import run_fsffl_alternate_history_final_report as v1
import run_fsffl_alternate_rookie_draft_particles as draft_runner
import run_fsffl_generic_alternate_history as generic
import run_fsffl_multiseason_particle_replay_v3 as season_v3
from alternate_history_branch_scoring import seeded_standings, update_records_from_week
from run_fsffl_alternate_draft_candidates import raw_draft
from run_fsffl_alternate_draft_policy import normalized_picks
from run_fsffl_alternate_history import FSFFLHistoricalAdapter
from run_fsffl_downstream_dependencies import load

DATA = Path("data")


def _prob_rows(counter: Counter, total: int, limit: int = 12) -> List[Dict[str, Any]]:
    return [
        {"value": key, "particles": int(count), "probability": round(count / total, 8),
         "confidence": v1.confidence(count / total)}
        for key, count in counter.most_common(limit)
    ]


def _actual_regular_season(season: str, focus_rid: str, adapter: FSFFLHistoricalAdapter) -> Dict[str, Any]:
    settings = season_v3.historical_settings(adapter, str(season))
    playoff_start = int(settings.get("playoff_week_start") or 15)
    matchups = load(DATA / "stats" / "fsffl" / str(season) / "league_matchups_raw.json") or {}
    records: Dict[str, Dict[str, Any]] = {}
    for week in range(1, playoff_start):
        rows = matchups.get(str(week), []) or []
        scores = {str(row.get("roster_id")): float(row.get("points") or 0.0) for row in rows}
        update_records_from_week(records, rows, scores)
    standings = seeded_standings(records)
    focus = next((row for row in standings if str(row.get("roster_id")) == str(focus_rid)), {})
    return {
        "seed": int(focus.get("seed") or 0) or None,
        "wins": float(focus.get("wins") or 0.0),
        "ties": float(focus.get("ties") or 0.0),
        "points_for": float(focus.get("points_for") or 0.0),
        "standings": standings,
    }


def _season_summary(groups, season: str, focus_rid: str, total: int, teams: Dict[str, str], adapter) -> Dict[str, Any]:
    row = v1.season_summary(groups, season, focus_rid, total, teams)
    actual = _actual_regular_season(season, focus_rid, adapter)
    row["actual"] = actual
    row["expected_seed"] = round(sum(float(x["value"]) * x["probability"] for x in row["focus_seed_distribution"]), 4) if row["focus_seed_distribution"] else None
    row["seed_delta_vs_actual"] = None if row["expected_seed"] is None or actual["seed"] is None else round(row["expected_seed"] - actual["seed"], 4)
    row["wins_delta_vs_actual"] = round(float(row["focus_expected_wins"] or 0.0) - float(actual["wins"] or 0.0), 4)
    row["points_for_delta_vs_actual"] = round(float(row["focus_expected_points_for"] or 0.0) - float(actual["points_for"] or 0.0), 4)
    return row


def _draft_comparisons(groups, focus_rid: str, total: int, names: Dict[str, str], seasons: Iterable[str]) -> List[Dict[str, Any]]:
    out = []
    for season in seasons:
        try:
            actual_picks = normalized_picks(raw_draft(str(season)))
        except Exception:
            continue
        actual_focus = [p for p in actual_picks if str(p.get("roster_id") or p.get("original_roster_id") or "") == str(focus_rid)]
        actual_by_round: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
        for p in actual_focus:
            actual_by_round[int(p.get("round") or 0)].append(p)

        alt: Dict[Tuple[int, int], Counter] = defaultdict(Counter)
        controller_prob: Counter = Counter()
        for group in groups:
            node = group.state.get(draft_runner.DRAFT_KEY) or {}
            seen = set()
            for pick in node.get("picks") or []:
                if str(pick.get("draft_season")) != str(season):
                    continue
                if str(pick.get("controller_roster_id")) != str(focus_rid):
                    continue
                key = (int(pick.get("round") or 0), int(pick.get("slot") or 0), str(pick.get("player_id") or ""))
                if key in seen:
                    continue
                seen.add(key)
                alt[(key[0], key[1])][key[2]] += int(group.count)
                controller_prob[f"{key[0]}.{key[1]:02d}"] += int(group.count)
        if not alt and not actual_focus:
            continue
        picks = []
        for (rnd, slot), counter in sorted(alt.items()):
            actual_candidates = actual_by_round.get(rnd) or []
            actual = next((p for p in actual_candidates if int(p.get("draft_slot") or p.get("slot") or 0) == slot), None)
            if actual is None and len(actual_candidates) == 1:
                actual = actual_candidates[0]
            choices = []
            for pid, count in counter.most_common(8):
                choices.append({"player_id": pid, "player_name": names.get(pid, pid), "probability": round(count / total, 8), "confidence": v1.confidence(count / total)})
            picks.append({
                "round": rnd, "slot": slot, "pick": f"{rnd}.{slot:02d}",
                "actual_player_id": str((actual or {}).get("player_id") or "") or None,
                "actual_player_name": (actual or {}).get("player_name") or names.get(str((actual or {}).get("player_id") or "")) if actual else None,
                "alternate_choices": choices,
            })
        out.append({"draft_season": str(season), "picks": picks})
    return out


def _history_transactions() -> Dict[str, Dict[str, Any]]:
    cache = load(DATA / "alternate_history" / "source_history" / "sleeper_history.json") or {}
    out: Dict[str, Dict[str, Any]] = {}
    for season_row in cache.get("history") or []:
        season = str((season_row.get("league") or {}).get("season") or "")
        for tx in season_row.get("transactions") or []:
            tid = str(tx.get("transaction_id") or "")
            if tid:
                row = dict(tx); row["_season"] = season; out[tid] = row
    for tx in load(DATA / "transactions.json") or []:
        tid = str(tx.get("transaction_id") or "")
        if tid and tid not in out:
            out[tid] = dict(tx)
    return out


def _describe_tx(tx: Dict[str, Any], names: Dict[str, str], teams: Dict[str, str]) -> str:
    t = str(tx.get("type") or "transaction").replace("_", " ").title()
    parts = []
    adds = tx.get("adds") or {}
    drops = tx.get("drops") or {}
    if adds:
        grouped: Dict[str, List[str]] = defaultdict(list)
        for pid, rid in adds.items(): grouped[str(rid)].append(names.get(str(pid), str(pid)))
        parts.append("adds " + "; ".join(f"{teams.get(rid, 'Roster '+rid)}: {', '.join(sorted(ps))}" for rid, ps in sorted(grouped.items())))
    if drops:
        grouped = defaultdict(list)
        for pid, rid in drops.items(): grouped[str(rid)].append(names.get(str(pid), str(pid)))
        parts.append("drops " + "; ".join(f"{teams.get(rid, 'Roster '+rid)}: {', '.join(sorted(ps))}" for rid, ps in sorted(grouped.items())))
    picks = tx.get("draft_picks") or []
    if picks:
        labels = [f"{p.get('season')} R{p.get('round')} ({teams.get(str(p.get('owner_id') or ''), 'orig '+str(p.get('owner_id') or ''))})" for p in picks]
        parts.append("picks " + ", ".join(labels))
    return t + (" — " + " | ".join(parts) if parts else "")


def _transaction_effects(groups, total: int, names: Dict[str, str], teams: Dict[str, str]) -> List[Dict[str, Any]]:
    history = _history_transactions()
    counts: Dict[str, Counter] = defaultdict(Counter)
    examples: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for group in groups:
        per_group = set()
        for trace in group.traces or [[]]:
            for step in trace:
                tid = str(step.get("transaction_id") or "")
                if not tid: continue
                outcome = str(step.get("outcome") or "unknown")
                key = (tid, outcome, str(step.get("package_id") or ""))
                if key in per_group: continue
                per_group.add(key)
                counts[tid][outcome + ("|" + key[2] if key[2] else "")] += int(group.count)
                examples.setdefault((tid, outcome), dict(step))
    rows = []
    for tid, counter in counts.items():
        tx = history.get(tid, {})
        season = str(tx.get("_season") or "")
        outcomes = []
        for raw, count in counter.most_common():
            outcome, _, package_id = raw.partition("|")
            p = min(1.0, count / total)
            outcomes.append({"outcome": outcome, "package_id": package_id or None, "probability": round(p, 8), "confidence": v1.confidence(p)})
        changed = sum(x["probability"] for x in outcomes if x["outcome"] not in {"preserve_historical", "exact", "preserve_exact"})
        rows.append({
            "transaction_id": tid, "season": season, "actual_transaction": _describe_tx(tx, names, teams),
            "type": tx.get("type"), "outcomes": outcomes, "probability_changed_or_removed": round(min(1.0, changed), 8),
        })
    rows.sort(key=lambda r: (-r["probability_changed_or_removed"], r["season"], r["transaction_id"]))
    return rows


def _dominant_branches(groups, total: int, names: Dict[str, str]) -> List[Dict[str, Any]]:
    rows = []
    for idx, group in enumerate(sorted(groups, key=lambda g: (-g.count, json.dumps(g.state, sort_keys=True)))[:20], 1):
        trace = (group.traces or [[]])[0]
        pivots = []
        for step in trace:
            kind = str(step.get("kind") or "")
            if kind == "alternate_rookie_draft_pick":
                if str(step.get("controller_roster_id")):
                    pivots.append(f"{step.get('draft_season')} draft {int(step.get('round') or 0)}.{int(step.get('slot') or 0):02d}: {step.get('player_name') or names.get(str(step.get('player_id')), step.get('player_id'))}")
            else:
                outcome = str(step.get("outcome") or "")
                if outcome and outcome not in {"preserve_historical", "preserve_exact", "exact"}:
                    pivots.append(f"transaction {step.get('transaction_id')}: {outcome}" + (f" ({step.get('package_id')})" if step.get("package_id") else ""))
        rows.append({"rank": idx, "particles": group.count, "probability": round(group.count / total, 8), "pivotal_events": pivots[:18]})
    return rows


def _season_narratives(seasons: List[Dict[str, Any]], drafts: List[Dict[str, Any]], transactions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    draft_map = {str(x["draft_season"]): x for x in drafts}
    out = []
    for row in seasons:
        season = str(row["season"])
        next_draft = draft_map.get(str(int(season) + 1), {})
        changed_txs = [x for x in transactions if x.get("season") == season and x["probability_changed_or_removed"] >= 0.10][:8]
        seed_top = row.get("focus_seed_distribution") or []
        seed_text = ", ".join(f"{x['value']}-seed {v1.pct(x['probability'])}" for x in seed_top[:4]) or "unavailable"
        actual = row.get("actual") or {}
        narrative = (
            f"Actual regular-season finish: seed {actual.get('seed') or 'n/a'}, {v1.num(actual.get('wins'))} wins and {v1.num(actual.get('points_for'))} PF. "
            f"In the alternate timeline, expected wins are {v1.num(row.get('focus_expected_wins'))} and expected PF is {v1.num(row.get('focus_expected_points_for'))}; "
            f"the most likely seed outcomes are {seed_text}."
        )
        out.append({"season": season, "narrative": narrative, "changed_transactions": changed_txs, "following_draft": next_draft})
    return out


def render_markdown(report: Dict[str, Any]) -> str:
    s = report["scenario"]; sim = report["present_day_simulator_outlook"]; roster = report["present_day_roster"]
    L = ["# FSFFL Alternate History Report — Narrative Edition", "", f"## {s['title']}", "",
         f"**Fork:** {s['fork_season']} Week {s['fork_week']}  ", f"**Historical particles:** {report['configuration']['particles']}  ",
         f"**Simulator draws:** {report['configuration']['simulator_sims']}  ", f"**Probability mass:** {v1.pct(report['summary']['present_day_probability_mass'])}  ", "",
         "Completed NFL results are fixed. The narrative below is generated only from branch-specific fantasy state, standings, draft history and transaction traces retained by the validated replay.", "",
         "## Executive Summary", "", f"Present-day roster divergence: **{roster['present_day_roster_divergence_score']:.2f}/100**.", "",
         "### Present-day Simulator impact", "", "| Metric | Actual | Alternate | Delta |", "|---|---:|---:|---:|"]
    for key in v1.METRICS:
        a,b,d=sim['actual'].get(key),sim['alternate'].get(key),sim['deltas'].get(key)
        fmt=v1.pct if 'probability' in key else v1.num
        L.append(f"| {key.replace('_',' ').title()} | {fmt(a)} | {fmt(b)} | {fmt(d)} |")

    L += ["", "## Expected Final Standings by Season", "", "| Season | Actual seed | Alternate expected seed | Most likely alternate seeds | Actual wins | Alt expected wins | PF delta |", "|---|---:|---:|---|---:|---:|---:|"]
    for r in report['season_by_season']:
        dist=', '.join(f"{x['value']} ({v1.pct(x['probability'])})" for x in r['focus_seed_distribution'][:4])
        L.append(f"| {r['season']} | {r['actual'].get('seed') or 'n/a'} | {v1.num(r.get('expected_seed'))} | {dist} | {v1.num(r['actual'].get('wins'))} | {v1.num(r.get('focus_expected_wins'))} | {v1.num(r.get('points_for_delta_vs_actual'))} |")

    L += ["", "## Season-by-Season Alternate History", ""]
    for sec in report['season_narratives']:
        L += [f"### {sec['season']}", "", sec['narrative'], ""]
        if sec['changed_transactions']:
            L.append("**Transactions most affected:**")
            for tx in sec['changed_transactions'][:6]:
                top = tx['outcomes'][0] if tx['outcomes'] else {}
                L.append(f"- {tx['actual_transaction']} → most common branch outcome **{top.get('outcome','n/a')}** ({v1.pct(top.get('probability'))}); changed/removed in {v1.pct(tx['probability_changed_or_removed'])}.")
            L.append("")
        draft=sec.get('following_draft') or {}
        if draft.get('picks'):
            L.append(f"**{draft['draft_season']} rookie draft consequences:**")
            for p in draft['picks']:
                choices=', '.join(f"{x['player_name']} {v1.pct(x['probability'])}" for x in p['alternate_choices'][:4])
                L.append(f"- {p['pick']}: actual **{p.get('actual_player_name') or 'not held'}**; alternate choices: {choices or 'none'}")
            L.append("")

    L += ["## Actual vs Alternate Rookie Drafts", ""]
    for draft in report['draft_comparisons']:
        L += [f"### {draft['draft_season']}", "", "| Pick | Actual selection | Alternate selection distribution |", "|---|---|---|"]
        for p in draft['picks']:
            choices='; '.join(f"{x['player_name']} {v1.pct(x['probability'])}" for x in p['alternate_choices'][:6])
            L.append(f"| {p['pick']} | {p.get('actual_player_name') or '—'} | {choices or '—'} |")
        L.append("")

    L += ["## Largest Butterfly Effects / Dominant Branches", "",
          "These are actual retained branch lineages, not inferred stories. A branch may have low individual probability when the 100 particles finish in many unique states.", ""]
    for b in report['dominant_branches'][:10]:
        L.append(f"### Branch {b['rank']} — {v1.pct(b['probability'])}")
        if b['pivotal_events']:
            for e in b['pivotal_events']: L.append(f"- {e}")
        else: L.append("- No non-preserved downstream pivot recorded in the retained representative trace.")
        L.append("")

    L += ["## Downstream Transaction Decision Audit", "",
          "This replaces the opaque internal-label list. Each row names the historical transaction and shows what the replay did with it.", ""]
    for tx in report['transaction_effects'][:30]:
        outcomes='; '.join(f"{x['outcome']} {v1.pct(x['probability'])}" + (f" [{x['package_id']}]" if x.get('package_id') else '') for x in tx['outcomes'][:5])
        L.append(f"- **{tx.get('season') or 'active'} — {tx['actual_transaction']}** → {outcomes}. Changed/removed: {v1.pct(tx['probability_changed_or_removed'])}.")

    L += ["", "## Present-Day Roster Consequences", "", "### Alternate-roster additions"]
    for r in roster['notable_gains'][:15]: L.append(f"- **{r['player_name']}** — {v1.pct(r['probability_on_alternate_roster'])}")
    L += ["", "### Actual-roster players absent in alternate branches"]
    for r in roster['notable_losses'][:15]: L.append(f"- **{r['player_name']}** — absent {v1.pct(r['probability_lost'])}")
    L += ["", "## Methodology / Interpretation", "",
          "Season standings are recalculated from branch-specific weekly scoring. Actual regular-season standings are reconstructed from the archived Sleeper matchup ledger using the same record/seeding utility. Alternate draft comparisons come from the branch-specific rookie-draft state. Transaction descriptions are joined back to the archived Sleeper transaction IDs; branch outcomes come directly from retained replay traces. No future NFL information is introduced by this reporting layer.", ""]
    return "\n".join(L)


def run(scenario_path: Path, *, particles: int, n_sims: int, seed: int):
    _, groups, generic_report = generic.run_generic(scenario_path, particles=particles, seed=seed, return_groups=True)
    payload = load(scenario_path) or {}
    adapter = FSFFLHistoricalAdapter()
    scenario = ah.scenario_from_json(adapter, payload)
    total = sum(g.count for g in groups)
    if total != particles: raise ah.AlternateHistoryError("narrative report particle mass mismatch")
    names=v1.player_names(); teams=v1.team_names(); sim=v1.simulator_outlook(groups, scenario, n_sims=n_sims)
    active=int(generic_report.get('active_season') or 0); fork=int(payload.get('fork_season') or 0)
    completed=[str(y) for y in range(fork, active)]
    seasons=[_season_summary(groups,y,str(scenario.focus_roster_id),total,teams,adapter) for y in completed]
    roster=v1.roster_distribution(groups,str(scenario.focus_roster_id),total,names,sim.pop('actual_focus_players'))
    drafts=_draft_comparisons(groups,str(scenario.focus_roster_id),total,names,[str(y) for y in range(fork+1,active+1)])
    txs=_transaction_effects(groups,total,names,teams)
    report={
      'model_version':'Fantasy-Alternate-History-1.1-narrative-report',
      'scenario':{'scenario_id':payload.get('scenario_id'),'title':payload.get('title'),'fork_season':str(payload.get('fork_season')),'fork_week':int(payload.get('fork_week') or 0),'focus_roster_id':str(scenario.focus_roster_id),'immutable_nfl_history':True},
      'configuration':{'particles':particles,'simulator_sims':n_sims,'seed':seed},
      'summary':{'present_day_unique_states':len(groups),'present_day_probability_mass':round(total/particles,10),'seasons_traversed':generic_report.get('summary',{}).get('seasons_traversed')},
      'season_by_season':seasons,'draft_comparisons':drafts,'transaction_effects':txs,
      'season_narratives':_season_narratives(seasons,drafts,txs),'dominant_branches':_dominant_branches(groups,total,names),
      'present_day_roster':roster,'present_day_simulator_outlook':sim,
      'generic_phase_audit':generic_report.get('phase_audit') or [],
      'design_invariants':{'presentation_layer_only':True,'completed_nfl_history_immutable':True,'particle_probability_mass_conserved':True,'simulator_runs_only_at_present_day_boundary':True,'narrative_derived_from_retained_branch_state_and_traces':True},
    }
    base=DATA/'alternate_history'/'results'/str(payload.get('scenario_id')); base.mkdir(parents=True,exist_ok=True)
    jp=base/'final_report_1_1.json'; mp=base/'final_report_1_1.md'
    jp.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n'); mp.write_text(render_markdown(report)+'\n')
    # Keep canonical final-report filenames pointed at the improved report for existing consumers/artifact workflows.
    (base/'final_report_1_0.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    (base/'final_report_1_0.md').write_text(render_markdown(report)+'\n')
    print(jp); print(mp)
    print(json.dumps({'present_day_unique_states':len(groups),'roster_divergence_score':roster['present_day_roster_divergence_score'],'simulator_deltas':sim['deltas'],'narrative_report':True},indent=2,sort_keys=True))
    return jp,mp
