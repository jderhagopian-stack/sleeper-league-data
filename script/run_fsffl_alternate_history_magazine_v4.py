#!/usr/bin/env python3
"""Alternate History magazine v4: coherent reader-facing rookie drafts.

The simulation itself is already sequential inside every particle.  Earlier
publication versions summarized each pick independently, which can create an
impossible composite draft (for example, the same rookie appearing as the
marginal mode at two different slots).  This wrapper keeps the per-pick
marginals for uncertainty reporting but publishes one *real retained particle
path* as the representative draft.

Publication invariants added here:
- every post-fork rookie draft has exactly 36 representative picks;
- every representative pick has a named fantasy-team controller;
- a rookie can appear only once in a representative draft;
- representative picks come from one retained sequential particle state, never
  from independently combined per-pick modes;
- the marginal mode remains available as audit data and is not mislabeled as a
  coherent draft path.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import alternate_history_engine as ah
import run_fsffl_alternate_history_magazine as base
import run_fsffl_alternate_history_magazine_v3 as v3
import run_fsffl_alternate_rookie_draft_particles as draft_runner
import run_fsffl_generic_alternate_history as generic
from run_fsffl_alternate_draft_candidates import raw_draft, user_to_roster_for_season
from run_fsffl_alternate_draft_policy import normalized_picks

_REPRESENTATIVE_DRAFTS: Dict[str, Dict[str, Any]] = {}
_ORIG_CAPTURE = generic.replay_rookie_draft_groups
_ORIG_VALIDATE = base._validate_publication


def _capture_representative_draft(*args, **kwargs):
    groups, meta = _ORIG_CAPTURE(*args, **kwargs)
    season = str(meta.get("draft_season") or kwargs.get("draft_season") or "")
    if season and groups:
        total = sum(int(g.count) for g in groups)
        # A representative draft must be a single actually retained state.  Use
        # the highest-weight post-draft state, with deterministic state ordering
        # as the tie breaker inherited from the replay merge order.
        group = sorted(groups, key=lambda g: -int(g.count))[0]
        node = group.state.get(draft_runner.DRAFT_KEY) or {}
        rows = [
            dict(p) for p in (node.get("picks") or [])
            if str(p.get("draft_season") or "") == season
        ]
        rows.sort(key=lambda p: int(p.get("pick_no") or 0))
        _REPRESENTATIVE_DRAFTS[season] = {
            "state_particles": int(group.count),
            "state_probability": round(int(group.count) / total, 8) if total else 0.0,
            "picks": rows,
        }
    return groups, meta


def _league_drafts(
    groups,
    seasons: Iterable[str],
    total: int,
    names: Dict[str, str],
    teams: Dict[str, str],
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for season in seasons:
        season = str(season)
        entry = raw_draft(season)
        actual = normalized_picks(entry)
        actual_by_pick: Dict[Tuple[int, int], Dict[str, Any]] = {
            (int(p.get("round") or 0), int(p.get("draft_slot") or 0)): p
            for p in actual
            if int(p.get("round") or 0) > 0 and int(p.get("draft_slot") or 0) > 0
        }
        uid_to_roster = user_to_roster_for_season(season)
        audit_by_pick = {
            (int(a.get("round") or 0), int(a.get("slot") or 0)): a
            for a in (v3._CAPTURED_DRAFT_AUDITS.get(season) or [])
            if int(a.get("round") or 0) > 0 and int(a.get("slot") or 0) > 0
        }
        rep = _REPRESENTATIVE_DRAFTS.get(season) or {}
        rep_by_pick = {
            (int(p.get("round") or 0), int(p.get("slot") or 0)): p
            for p in (rep.get("picks") or [])
            if int(p.get("round") or 0) > 0 and int(p.get("slot") or 0) > 0
        }

        pick_keys = sorted(set(actual_by_pick) | set(audit_by_pick) | set(rep_by_pick))
        picks: List[Dict[str, Any]] = []
        for rnd, slot in pick_keys:
            ap = actual_by_pick.get((rnd, slot)) or {}
            audit = audit_by_pick.get((rnd, slot)) or {}
            rp = rep_by_pick.get((rnd, slot)) or {}
            actual_pid = str(ap.get("player_id") or "")
            actual_uid = str(ap.get("picked_by_user_id") or "")
            actual_rid = str(uid_to_roster.get(actual_uid) or "")

            selection_counts = {
                str(pid): int(n)
                for pid, n in (audit.get("selection_counts") or {}).items()
                if str(pid) and int(n) > 0
            }
            controller_counts = {
                str(rid): int(n)
                for rid, n in (audit.get("controller_counts") or {}).items()
                if str(rid) and int(n) > 0
            }
            marginal = [
                {
                    "player_id": pid,
                    "player_name": names.get(pid, pid),
                    "probability": round(n / total, 8),
                }
                for pid, n in sorted(selection_counts.items(), key=lambda x: (-x[1], x[0]))
            ]
            marginal_mode = marginal[0] if marginal else None

            rep_pid = str(rp.get("player_id") or "")
            rep_rid = str(rp.get("controller_roster_id") or "")
            rep_prob = round(selection_counts.get(rep_pid, 0) / total, 8) if rep_pid else 0.0
            rep_choice = {
                "player_id": rep_pid,
                "player_name": names.get(rep_pid, rp.get("player_name") or rep_pid),
                "probability": rep_prob,
            } if rep_pid else None
            # Preserve the old field for downstream rendering, but make its
            # first row the coherent representative choice.  Remaining rows are
            # explicitly marginal alternatives for audit/uncertainty.
            alt_choices: List[Dict[str, Any]] = []
            if rep_choice:
                alt_choices.append(rep_choice)
            alt_choices.extend(x for x in marginal if x.get("player_id") != rep_pid)
            alt_choices = alt_choices[:6]

            alt_ctrl = [
                {
                    "roster_id": rid,
                    "team": teams.get(rid, f"Roster {rid}"),
                    "probability": round(n / total, 8),
                }
                for rid, n in sorted(controller_counts.items(), key=lambda x: (-x[1], x[0]))[:4]
            ]
            rep_controller = {
                "roster_id": rep_rid,
                "team": teams.get(rep_rid, f"Roster {rep_rid}"),
                "probability": round(controller_counts.get(rep_rid, 0) / total, 8),
            } if rep_rid else None
            if rep_controller:
                alt_ctrl = [rep_controller] + [x for x in alt_ctrl if x.get("roster_id") != rep_rid]
                alt_ctrl = alt_ctrl[:4]

            picks.append({
                "round": rnd,
                "slot": slot,
                "pick": f"{rnd}.{slot:02d}",
                "actual_player_id": actual_pid or None,
                "actual_player_name": names.get(actual_pid, actual_pid) if actual_pid else None,
                "actual_roster_id": actual_rid or None,
                "actual_team": teams.get(actual_rid, f"Roster {actual_rid}") if actual_rid else None,
                "alternate_choices": alt_choices,
                "alternate_controllers": alt_ctrl,
                "representative_player_id": rep_pid or None,
                "representative_player_name": names.get(rep_pid, rp.get("player_name") or rep_pid) if rep_pid else None,
                "representative_controller_roster_id": rep_rid or None,
                "representative_team": teams.get(rep_rid, f"Roster {rep_rid}") if rep_rid else None,
                "representative_pick_marginal_probability": rep_prob if rep_pid else None,
                "marginal_mode_player_id": marginal_mode.get("player_id") if marginal_mode else None,
                "marginal_mode_player_name": marginal_mode.get("player_name") if marginal_mode else None,
                "marginal_mode_probability": marginal_mode.get("probability") if marginal_mode else None,
                "most_likely_selection_changed": bool(rep_pid and actual_pid and rep_pid != actual_pid),
            })
        if picks:
            out.append({
                "draft_season": season,
                "representative_state_probability": rep.get("state_probability"),
                "representative_state_particles": rep.get("state_particles"),
                "publication_selection_semantics": "single_retained_sequential_particle_path",
                "picks": picks,
            })
    return out


def _validate_publication(report: Dict[str, Any]) -> None:
    _ORIG_VALIDATE(report)
    for draft in report.get("drafts") or []:
        season = str(draft.get("draft_season") or "")
        picks = draft.get("picks") or []
        if len(picks) != 36:
            raise ah.AlternateHistoryError(
                f"{season} coherent publication draft requires 36 picks, got {len(picks)}"
            )
        rep_players = [str(p.get("representative_player_id") or "") for p in picks]
        if any(not pid for pid in rep_players):
            raise ah.AlternateHistoryError(f"{season} representative draft has a missing player")
        if len(set(rep_players)) != len(rep_players):
            duplicates = sorted({pid for pid in rep_players if rep_players.count(pid) > 1})
            raise ah.AlternateHistoryError(
                f"{season} representative draft selects a rookie more than once: {duplicates[:5]}"
            )
        missing_controller = [
            p.get("pick") for p in picks if not p.get("representative_controller_roster_id")
        ]
        if missing_controller:
            raise ah.AlternateHistoryError(
                f"{season} representative draft has missing fantasy-team controllers: {missing_controller[:5]}"
            )
        missing_actual_team = [p.get("pick") for p in picks if not p.get("actual_team")]
        if missing_actual_team:
            raise ah.AlternateHistoryError(
                f"{season} historical draft has missing fantasy-team attribution: {missing_actual_team[:5]}"
            )
        if draft.get("publication_selection_semantics") != "single_retained_sequential_particle_path":
            raise ah.AlternateHistoryError(f"{season} draft publication semantics are not sequential")


generic.replay_rookie_draft_groups = _capture_representative_draft
base._league_drafts = _league_drafts
base._validate_publication = _validate_publication


def run(scenario_path: Path, *, particles: int, n_sims: int, seed: int):
    _REPRESENTATIVE_DRAFTS.clear()
    v3._CAPTURED_DRAFT_AUDITS.clear()
    return base.run(scenario_path, particles=particles, n_sims=n_sims, seed=seed)


def main() -> None:
    p = argparse.ArgumentParser(description="Render coherent-draft FSFFL Alternate History magazine")
    p.add_argument("scenario", type=Path)
    p.add_argument("--particles", type=int, default=base.DEFAULT_PARTICLES)
    p.add_argument("--sims", type=int, default=base.DEFAULT_SIMS)
    p.add_argument("--seed", type=int, default=base.DEFAULT_SEED)
    a = p.parse_args()
    run(a.scenario, particles=a.particles, n_sims=a.sims, seed=a.seed)


if __name__ == "__main__":
    main()
