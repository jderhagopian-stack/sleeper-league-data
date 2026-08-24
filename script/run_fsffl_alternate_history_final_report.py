#!/usr/bin/env python3
"""Render the final user-facing FSFFL Alternate History report.

This is a presentation/orchestration layer over the validated generic historical
engine and Simulator 1.0. It does not alter branch probabilities, historical
NFL outcomes, draft logic, or Simulator inputs.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import alternate_history_engine as ah
import run_fsffl_generic_alternate_history as generic
import run_fsffl_multiseason_particle_replay_v3 as season_v3
import run_fsffl_weighted_alternate_outlook as weighted
from run_fsffl_downstream_dependencies import load
from run_fsffl_gm30_counterfactual import CounterfactualEngine

DATA = Path("data")
DEFAULT_PARTICLES = 100
DEFAULT_SIMS = 500
DEFAULT_SEED = 20260824
METRICS = weighted.METRICS


def player_names() -> Dict[str, str]:
    rows = load(DATA / "players.json") or {}
    out: Dict[str, str] = {}
    for pid, row in rows.items():
        name = row.get("full_name") or " ".join(
            x for x in [str(row.get("first_name") or "").strip(), str(row.get("last_name") or "").strip()] if x
        ).strip()
        out[str(pid)] = name or str(pid)
    return out


def team_names() -> Dict[str, str]:
    users = load(DATA / "users.json") or []
    rosters = load(DATA / "rosters.json") or []
    uid_to_name: Dict[str, str] = {}
    for user in users:
        uid = str(user.get("user_id") or "")
        meta = user.get("metadata") or {}
        uid_to_name[uid] = str(meta.get("team_name") or user.get("display_name") or uid)
    out: Dict[str, str] = {}
    for roster in rosters:
        rid = str(roster.get("roster_id") or "")
        uid = str(roster.get("owner_id") or "")
        if rid:
            out[rid] = uid_to_name.get(uid) or f"Roster {rid}"
    return out


def confidence(probability: float) -> str:
    p = float(probability)
    if p >= 0.90:
        return "NEAR-CERTAIN"
    if p >= 0.67:
        return "HIGH"
    if p >= 0.40:
        return "MEDIUM"
    return "LOW"


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{100.0 * float(value):.1f}%"


def num(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{float(value):.{digits}f}"


def weighted_counter(groups, extractor) -> Counter:
    out: Counter = Counter()
    for group in groups:
        value = extractor(group)
        if value is not None:
            out[str(value)] += int(group.count)
    return out


def probability_rows(counter: Counter, total: int, *, limit: int = 12) -> List[Dict[str, Any]]:
    return [
        {
            "value": key,
            "particles": int(count),
            "probability": round(count / total, 8),
            "confidence": confidence(count / total),
        }
        for key, count in counter.most_common(limit)
    ]


def standings_row(group, season: str, rid: str) -> Dict[str, Any]:
    ledger = group.state.get(season_v3.LEDGER_KEY) or {}
    season_row = ledger.get(str(season)) or {}
    return next(
        (row for row in (season_row.get("standings") or []) if str(row.get("roster_id")) == str(rid)),
        {},
    )


def season_summary(groups, season: str, focus_rid: str, total: int, teams: Dict[str, str]) -> Dict[str, Any]:
    seed_counter: Counter = Counter()
    slot_counter: Counter = Counter()
    champ_counter: Counter = Counter()
    playoff_particles = 0
    champion_particles = 0
    weighted_wins = 0.0
    weighted_pf = 0.0
    weighted_maxpf = 0.0
    covered = 0

    for group in groups:
        ledger = group.state.get(season_v3.LEDGER_KEY) or {}
        row = ledger.get(str(season)) or {}
        standings = row.get("standings") or []
        if not standings:
            continue
        count = int(group.count)
        focus = next((x for x in standings if str(x.get("roster_id")) == str(focus_rid)), {})
        if focus:
            covered += count
            seed = int(focus.get("seed") or 0)
            if seed:
                seed_counter[str(seed)] += count
            weighted_wins += count * float(focus.get("wins") or 0.0)
            weighted_pf += count * float(focus.get("points_for") or 0.0)
            weighted_maxpf += count * float((row.get("season_max_pf") or {}).get(str(focus_rid)) or 0.0)
        playoff_field = {str(x) for x in (row.get("playoff_field") or [])}
        if str(focus_rid) in playoff_field:
            playoff_particles += count
        postseason = row.get("postseason") or {}
        champ = str(((postseason.get("championship") or {}).get("winner")) or "")
        if champ:
            champ_counter[champ] += count
            if champ == str(focus_rid):
                champion_particles += count
        draft_slots = row.get("full_following_draft_slots") or {}
        if str(focus_rid) in draft_slots:
            slot_counter[str(draft_slots[str(focus_rid)])] += count

    denom = covered or total
    most_likely_champ = None
    if champ_counter:
        champ_rid, champ_count = champ_counter.most_common(1)[0]
        most_likely_champ = {
            "roster_id": champ_rid,
            "team": teams.get(champ_rid, f"Roster {champ_rid}"),
            "probability": round(champ_count / total, 8),
            "confidence": confidence(champ_count / total),
        }
    return {
        "season": str(season),
        "coverage_probability": round(covered / total, 8),
        "focus_playoff_probability": round(playoff_particles / total, 8),
        "focus_championship_probability": round(champion_particles / total, 8),
        "focus_expected_wins": round(weighted_wins / denom, 4) if denom else None,
        "focus_expected_points_for": round(weighted_pf / denom, 4) if denom else None,
        "focus_expected_max_pf": round(weighted_maxpf / denom, 4) if denom else None,
        "focus_seed_distribution": probability_rows(seed_counter, total),
        "following_draft_slot_distribution": probability_rows(slot_counter, total),
        "most_likely_champion": most_likely_champ,
    }


def roster_distribution(groups, focus_rid: str, total: int, names: Dict[str, str], actual_players: Iterable[str]) -> Dict[str, Any]:
    counts: Counter = Counter()
    roster_sizes = 0.0
    for group in groups:
        players = {str(x) for x in ((group.state.get("roster_players") or {}).get(str(focus_rid)) or [])}
        roster_sizes += group.count * len(players)
        for pid in players:
            counts[pid] += int(group.count)
    actual = {str(x) for x in actual_players}
    membership = [
        {
            "player_id": pid,
            "player_name": names.get(pid, pid),
            "probability_on_alternate_roster": round(count / total, 8),
            "on_actual_roster": pid in actual,
            "confidence": confidence(count / total),
        }
        for pid, count in counts.most_common()
    ]
    gained = [x for x in membership if not x["on_actual_roster"] and x["probability_on_alternate_roster"] >= 0.05]
    lost = []
    for pid in sorted(actual):
        p = counts.get(pid, 0) / total
        if p < 0.95:
            lost.append({
                "player_id": pid,
                "player_name": names.get(pid, pid),
                "probability_retained": round(p, 8),
                "probability_lost": round(1.0 - p, 8),
                "confidence": confidence(1.0 - p),
            })
    # Expected Jaccard distance from the actual current roster, weighted over branches.
    distance = 0.0
    for group in groups:
        alt = {str(x) for x in ((group.state.get("roster_players") or {}).get(str(focus_rid)) or [])}
        union = actual | alt
        jaccard = 0.0 if not union else 1.0 - (len(actual & alt) / len(union))
        distance += (group.count / total) * jaccard
    return {
        "expected_roster_size": round(roster_sizes / total, 4),
        "present_day_roster_divergence_score": round(100.0 * distance, 2),
        "definition": "100 x probability-weighted Jaccard distance between actual and alternate current focus rosters",
        "membership_probabilities": membership,
        "notable_gains": gained[:20],
        "notable_losses": sorted(lost, key=lambda x: -x["probability_lost"])[:20],
    }


def trace_effects(groups, total: int) -> List[Dict[str, Any]]:
    counts: Counter = Counter()
    examples: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for group in groups:
        seen = set()
        for trace in (group.traces or [[]]):
            for step in trace:
                kind = str(step.get("kind") or "unknown")
                tid = str(step.get("transaction_id") or step.get("pick_no") or "")
                outcome = str(step.get("outcome") or step.get("player_name") or step.get("player_id") or "")
                key = (kind, tid, outcome)
                if key in seen:
                    continue
                seen.add(key)
                counts[key] += int(group.count)
                examples.setdefault(key, dict(step))
    rows = []
    for key, count in counts.most_common(30):
        step = examples[key]
        p = min(1.0, count / total)
        rows.append({
            "kind": key[0],
            "transaction_or_pick": key[1],
            "outcome": key[2],
            "season": step.get("season") or step.get("draft_season"),
            "probability": round(p, 8),
            "confidence": confidence(p),
            "detail": step,
        })
    return rows


def simulator_outlook(groups, scenario, *, n_sims: int) -> Dict[str, Any]:
    groups = sorted(groups, key=lambda group: (-group.count, json.dumps(group.state, sort_keys=True)))
    allocations = weighted.allocate_sims(groups, int(n_sims))
    engine = CounterfactualEngine()
    focus_rid = int(scenario.focus_roster_id)
    focus_uid = engine.roster_id_to_uid.get(focus_rid)
    if focus_uid is None:
        raise ah.AlternateHistoryError(f"final report unable to resolve focus roster {focus_rid}")
    baseline = engine.baseline(int(n_sims))
    baseline_focus = weighted.team(baseline, focus_uid)
    weighted_rows: List[Tuple[float, Dict[str, Any]]] = []
    state_results = []
    total_particles = sum(group.count for group in groups)
    for idx, (group, sims) in enumerate(zip(groups, allocations)):
        weight = group.count / total_particles
        result = engine._run(weighted.simulator_rosters_from_state(engine, group.state), int(sims))
        focus_team = weighted.team(result, focus_uid)
        weighted_rows.append((weight, focus_team))
        state_results.append({
            "state_index": idx,
            "particles": group.count,
            "probability": round(weight, 8),
            "simulations": int(sims),
            "focus_outlook": {key: focus_team.get(key) for key in METRICS},
        })
    alternate = {key: weighted.weighted_metric(weighted_rows, key) for key in METRICS}
    actual = {key: baseline_focus.get(key) for key in METRICS}
    deltas = {
        key: None if alternate.get(key) is None or actual.get(key) is None
        else round(float(alternate[key]) - float(actual[key]), 6)
        for key in METRICS
    }
    actual_roster = next(
        (row for row in engine.rosters if int(row.get("roster_id") or -1) == focus_rid),
        {},
    )
    return {
        "actual": actual,
        "alternate": alternate,
        "deltas": deltas,
        "state_simulation_allocations": state_results,
        "actual_focus_players": [str(x) for x in (actual_roster.get("players") or [])],
        "simulator_draws": sum(allocations),
    }


def render_markdown(report: Dict[str, Any]) -> str:
    s = report["scenario"]
    sim = report["present_day_simulator_outlook"]
    roster = report["present_day_roster"]
    lines = [
        f"# FSFFL Alternate History Report",
        "",
        f"## {s['title']}",
        "",
        f"**Fork:** {s['fork_season']} Week {s['fork_week']}  ",
        f"**Historical particles:** {report['configuration']['particles']}  ",
        f"**Simulator draws:** {report['configuration']['simulator_sims']}  ",
        f"**Present-day unique states:** {report['summary']['present_day_unique_states']}  ",
        "",
        "Completed NFL results are fixed. Only fantasy ownership, manager decisions, draft position, draft choices, and their downstream fantasy-league consequences are allowed to change.",
        "",
        "## Executive Summary",
        "",
        f"The alternate timeline reaches the present with a **{roster['present_day_roster_divergence_score']:.1f}/100 roster-divergence score** for the focus franchise. This is a probability-weighted Jaccard distance from the actual current roster, not a subjective rating.",
        "",
        "### Present-day Simulator impact",
        "",
        "| Metric | Actual | Alternate | Delta |",
        "|---|---:|---:|---:|",
    ]
    for key in METRICS:
        a, b, d = sim["actual"].get(key), sim["alternate"].get(key), sim["deltas"].get(key)
        if "probability" in key:
            lines.append(f"| {key.replace('_', ' ').title()} | {pct(a)} | {pct(b)} | {pct(d)} |")
        else:
            lines.append(f"| {key.replace('_', ' ').title()} | {num(a)} | {num(b)} | {num(d)} |")

    lines += ["", "## Season-by-Season Butterfly Effects", ""]
    for row in report["season_by_season"]:
        lines += [
            f"### {row['season']}",
            f"- Focus playoff probability: **{pct(row['focus_playoff_probability'])}**",
            f"- Focus championship probability: **{pct(row['focus_championship_probability'])}**",
            f"- Expected wins: **{num(row['focus_expected_wins'])}**",
        ]
        if row.get("most_likely_champion"):
            champ = row["most_likely_champion"]
            lines.append(
                f"- Most likely champion: **{champ['team']}** ({pct(champ['probability'])}, {champ['confidence']} confidence)"
            )
        if row.get("following_draft_slot_distribution"):
            top = row["following_draft_slot_distribution"][0]
            lines.append(
                f"- Most likely following rookie-draft slot for focus team: **{top['value']}** ({pct(top['probability'])})"
            )
        lines.append("")

    lines += ["## Present-Day Roster Consequences", ""]
    if roster["notable_gains"]:
        lines.append("### Players appearing on the alternate roster")
        for row in roster["notable_gains"][:12]:
            lines.append(
                f"- **{row['player_name']}** — {pct(row['probability_on_alternate_roster'])} ({row['confidence']})"
            )
        lines.append("")
    if roster["notable_losses"]:
        lines.append("### Actual-roster players lost in alternate branches")
        for row in roster["notable_losses"][:12]:
            lines.append(
                f"- **{row['player_name']}** — lost in {pct(row['probability_lost'])} of branches ({row['confidence']})"
            )
        lines.append("")

    lines += ["## Highest-Probability Downstream Decisions", ""]
    for row in report["major_downstream_effects"][:15]:
        label = row["outcome"] or row["kind"]
        lines.append(
            f"- **{label}** — {pct(row['probability'])} ({row['confidence']}); {row['kind']}"
        )

    lines += [
        "",
        "## Confidence Guide",
        "",
        "- **NEAR-CERTAIN:** at least 90% of historical particles",
        "- **HIGH:** 67% to <90%",
        "- **MEDIUM:** 40% to <67%",
        "- **LOW:** below 40%",
        "",
        "## Methodology",
        "",
        "The historical engine replays the counterfactual chronologically using only information available at each decision point. Completed NFL scoring remains immutable. Fantasy standings and MaxPF are recalculated from alternate ownership; rookie draft position and selections are branch-specific; downstream transactions are replayed against each branch's live roster state. At the present-day boundary, each surviving state receives Simulator 1.0 draws and the final outlook is probability-weighted across all states.",
        "",
    ]
    return "\n".join(lines)


def run(scenario_path: Path, *, particles: int, n_sims: int, seed: int) -> Tuple[Path, Path]:
    _, groups, generic_report = generic.run_generic(
        scenario_path,
        particles=particles,
        seed=seed,
        return_groups=True,
    )
    payload = load(scenario_path) or {}
    adapter = generic.predraft.FSFFLHistoricalAdapter() if hasattr(generic.predraft, "FSFFLHistoricalAdapter") else None
    # Reuse the parsed scenario retained by the generic boundary metadata via a normal adapter.
    from run_fsffl_alternate_history import FSFFLHistoricalAdapter
    scenario = ah.scenario_from_json(FSFFLHistoricalAdapter(), payload)
    total = sum(group.count for group in groups)
    if total != particles:
        raise ah.AlternateHistoryError("final report particle mass mismatch")

    names = player_names()
    teams = team_names()
    sim = simulator_outlook(groups, scenario, n_sims=n_sims)
    completed_seasons = [
        str(year) for year in range(int(payload.get("fork_season") or 0), int(generic_report.get("active_season") or 0))
    ]
    seasons = [season_summary(groups, year, str(scenario.focus_roster_id), total, teams) for year in completed_seasons]
    roster = roster_distribution(
        groups,
        str(scenario.focus_roster_id),
        total,
        names,
        sim.pop("actual_focus_players"),
    )
    report = {
        "model_version": "Fantasy-Alternate-History-1.0-final-report",
        "scenario": {
            "scenario_id": payload.get("scenario_id"),
            "title": payload.get("title"),
            "fork_season": str(payload.get("fork_season")),
            "fork_week": int(payload.get("fork_week") or 0),
            "focus_roster_id": str(scenario.focus_roster_id),
            "immutable_nfl_history": True,
        },
        "configuration": {"particles": particles, "simulator_sims": n_sims, "seed": seed},
        "summary": {
            "present_day_unique_states": len(groups),
            "present_day_probability_mass": 1.0,
            "seasons_traversed": generic_report.get("summary", {}).get("seasons_traversed"),
        },
        "season_by_season": seasons,
        "present_day_roster": roster,
        "major_downstream_effects": trace_effects(groups, total),
        "present_day_simulator_outlook": sim,
        "generic_phase_audit": generic_report.get("phase_audit") or [],
        "design_invariants": {
            "presentation_layer_only": True,
            "completed_nfl_history_immutable": True,
            "particle_probability_mass_conserved": True,
            "confidence_labels_derived_from_particle_frequency": True,
            "simulator_runs_only_at_present_day_boundary": True,
        },
    }
    base = DATA / "alternate_history" / "results" / str(payload.get("scenario_id"))
    base.mkdir(parents=True, exist_ok=True)
    json_path = base / "final_report_1_0.json"
    md_path = base / "final_report_1_0.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    md_path.write_text(render_markdown(report) + "\n")
    print(json_path)
    print(md_path)
    print(json.dumps({
        "present_day_unique_states": len(groups),
        "roster_divergence_score": roster["present_day_roster_divergence_score"],
        "simulator_deltas": sim["deltas"],
    }, indent=2, sort_keys=True))
    return json_path, md_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Run and render the final FSFFL Alternate History report")
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--particles", type=int, default=DEFAULT_PARTICLES)
    parser.add_argument("--sims", type=int, default=DEFAULT_SIMS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    run(args.scenario, particles=args.particles, n_sims=args.sims, seed=args.seed)


if __name__ == "__main__":
    main()
