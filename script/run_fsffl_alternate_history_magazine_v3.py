#!/usr/bin/env python3
"""Publication-safe draft audit extraction for FSFFL Alternate History.

This wrapper changes only reader-facing extraction/validation. It captures the
already-computed per-pick audit returned by the validated rookie-draft replay
while the generic season cycle is running, then uses that audit to populate the
publication draft tables. No branch state, probability, roster decision,
transaction decision, lineup, MaxPF, or Simulator input is modified.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import alternate_history_engine as ah
import run_fsffl_alternate_history_magazine as base
import run_fsffl_alternate_history_magazine_v2 as polished  # noqa: F401 - installs v2 presentation patches
import run_fsffl_generic_alternate_history as generic
from run_fsffl_alternate_draft_candidates import raw_draft, user_to_roster_for_season
from run_fsffl_alternate_draft_policy import normalized_picks

_CAPTURED_DRAFT_AUDITS: Dict[str, List[Dict[str, Any]]] = {}
_ORIG_REPLAY_DRAFT = generic.replay_rookie_draft_groups
_ORIG_LEAGUE_DRAFTS = base._league_drafts
_ORIG_VALIDATE = base._validate_publication


def _capturing_replay_rookie_draft_groups(*args, **kwargs):
    groups, meta = _ORIG_REPLAY_DRAFT(*args, **kwargs)
    season = str(meta.get("draft_season") or kwargs.get("draft_season") or "")
    audit = list(meta.get("draft_pick_audit") or [])
    if season and audit:
        _CAPTURED_DRAFT_AUDITS[season] = audit
    return groups, meta


def _league_drafts(
    groups,
    seasons: Iterable[str],
    total: int,
    names: Dict[str, str],
    teams: Dict[str, str],
) -> List[Dict[str, Any]]:
    """Build publication draft distributions from the live draft replay audit.

    The final branch state can legitimately lose old bookkeeping keys as later
    historical transactions are applied. The draft replay's own audit is the
    authoritative reporting surface because it is emitted at the exact moment
    each branch-specific pick is simulated.
    """
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
            for a in (_CAPTURED_DRAFT_AUDITS.get(season) or [])
            if int(a.get("round") or 0) > 0 and int(a.get("slot") or 0) > 0
        }

        pick_keys = sorted(set(actual_by_pick) | set(audit_by_pick))
        picks: List[Dict[str, Any]] = []
        for rnd, slot in pick_keys:
            ap = actual_by_pick.get((rnd, slot)) or {}
            audit = audit_by_pick.get((rnd, slot)) or {}
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
            alt_choices = [
                {
                    "player_id": pid,
                    "player_name": names.get(pid, pid),
                    "probability": round(n / total, 8),
                }
                for pid, n in sorted(selection_counts.items(), key=lambda x: (-x[1], x[0]))[:6]
            ]
            alt_ctrl = [
                {
                    "roster_id": rid,
                    "team": teams.get(rid, f"Roster {rid}"),
                    "probability": round(n / total, 8),
                }
                for rid, n in sorted(controller_counts.items(), key=lambda x: (-x[1], x[0]))[:4]
            ]
            top_pid = alt_choices[0]["player_id"] if alt_choices else None
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
                "most_likely_selection_changed": bool(top_pid and actual_pid and top_pid != actual_pid),
            })
        if picks:
            out.append({"draft_season": season, "picks": picks})
    return out


def _validate_publication(report: Dict[str, Any]) -> None:
    _ORIG_VALIDATE(report)
    drafts = report.get("drafts") or []
    if not drafts:
        raise ah.AlternateHistoryError("publication draft audit is empty")
    for draft in drafts:
        picks = draft.get("picks") or []
        if len(picks) != 36:
            raise ah.AlternateHistoryError(
                f"{draft.get('draft_season')} publication draft requires 36 picks, got {len(picks)}"
            )
        missing_choices = [p["pick"] for p in picks if not p.get("alternate_choices")]
        missing_ctrl = [p["pick"] for p in picks if not p.get("alternate_controllers")]
        if missing_choices:
            raise ah.AlternateHistoryError(
                f"{draft.get('draft_season')} publication lost simulated selections at {missing_choices[:5]}"
            )
        if missing_ctrl:
            raise ah.AlternateHistoryError(
                f"{draft.get('draft_season')} publication lost simulated controllers at {missing_ctrl[:5]}"
            )
        for p in picks:
            selection_mass = sum(float(x.get("probability") or 0.0) for x in p.get("alternate_choices") or [])
            controller_mass = sum(float(x.get("probability") or 0.0) for x in p.get("alternate_controllers") or [])
            # Top-N lists may omit small tails, but the retained mass can never be zero or exceed 1.
            if not (0.0 < selection_mass <= 1.00000001):
                raise ah.AlternateHistoryError(
                    f"{draft.get('draft_season')} {p['pick']} invalid displayed selection mass {selection_mass}"
                )
            if not (0.0 < controller_mass <= 1.00000001):
                raise ah.AlternateHistoryError(
                    f"{draft.get('draft_season')} {p['pick']} invalid displayed controller mass {controller_mass}"
                )


generic.replay_rookie_draft_groups = _capturing_replay_rookie_draft_groups
base._league_drafts = _league_drafts
base._validate_publication = _validate_publication


def run(scenario_path: Path, *, particles: int, n_sims: int, seed: int):
    _CAPTURED_DRAFT_AUDITS.clear()
    return base.run(scenario_path, particles=particles, n_sims=n_sims, seed=seed)


def main() -> None:
    p = argparse.ArgumentParser(description="Render audited deterministic FSFFL Alternate History magazine")
    p.add_argument("scenario", type=Path)
    p.add_argument("--particles", type=int, default=base.DEFAULT_PARTICLES)
    p.add_argument("--sims", type=int, default=base.DEFAULT_SIMS)
    p.add_argument("--seed", type=int, default=base.DEFAULT_SEED)
    a = p.parse_args()
    run(a.scenario, particles=a.particles, n_sims=a.sims, seed=a.seed)


if __name__ == "__main__":
    main()
