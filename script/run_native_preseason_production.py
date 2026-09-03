#!/usr/bin/env python3
"""Production entrypoint for the FSFFL Native V2 preseason model.

This wrapper keeps the validated projection builder stable while supplying the
schema-aware timestamped role loader used for 2025+ depth-chart files. It also
promotes the separately validated WR/TE preseason opportunity features without
changing QB/RB feature sets. After the veteran build it replaces the legacy
no-history fallback projection values with the separately validated native
no-history role/age model.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

import build_native_preseason_projections as base

ORIGINAL_ROLE_MAP = base.role_map
ORIGINAL_ATTACH_ROLES = base.attach_roles

WR_TE_OPPORTUNITY_FEATURES = [
    "prior_team_skill_target_share",
    "opening_team_vacated_skill_target_share",
    "first_team_x_prior_skill_target_share",
]

# The 2026 NFL Kickoff Game is Sept. 9 at 8:20 p.m. ET = Sept. 10 00:20 UTC.
# Freeze at midnight UTC, 20 minutes before kickoff, unless --as-of is earlier.
base.OPENING_CUTOFF_UTC[2026] = "2026-09-10T00:00:00Z"
for _pos in ("WR", "TE"):
    base.SELECTED[_pos] = list(dict.fromkeys(list(base.SELECTED[_pos]) + WR_TE_OPPORTUNITY_FEATURES))


def role_map(season: int, as_of: datetime | None = None) -> tuple[dict, dict]:
    rows = base.fetch_depth(season)
    if not rows:
        return {}, {"season": season, "rows": 0, "schema": "empty"}
    cols = set(rows[0])
    out = {}
    if "dt" in cols and "pos_rank" in cols:
        cutoff_text = base.OPENING_CUTOFF_UTC.get(season)
        cutoff = base.iso_dt(cutoff_text) if cutoff_text else None
        if as_of is not None and (cutoff is None or as_of < cutoff):
            cutoff = as_of
        eligible = [
            r for r in rows
            if r.get("dt") and (cutoff is None or base.iso_dt(str(r["dt"])) < cutoff)
        ]
        if not eligible:
            return {}, {
                "season": season,
                "rows": len(rows),
                "schema": "timestamped",
                "cutoff": cutoff.isoformat() if cutoff else None,
                "eligible_rows": 0,
            }
        latest = max(base.iso_dt(str(r["dt"])) for r in eligible)
        snap = [r for r in eligible if base.iso_dt(str(r["dt"])) == latest]
        teams = set()
        position_rows = 0
        for r in snap:
            pid = str(r.get("gsis_id") or "").strip()
            pos = str(r.get("pos_abb") or r.get("pos_name") or r.get("pos_grp") or "").upper().strip()
            if not pid or pos not in base.SELECTED:
                continue
            position_rows += 1
            rank = max(1.0, base.fnum(r.get("pos_rank"), 9.0))
            team = str(r.get("team") or "").strip()
            if team:
                teams.add(team)
            key = (pid, pos)
            prev = out.get(key)
            if prev is None or rank < prev["rank"]:
                out[key] = {"rank": rank, "team": team, "snapshot": latest.isoformat()}
        audit = {
            "season": season,
            "rows": len(rows),
            "schema": "timestamped",
            "cutoff": cutoff.isoformat() if cutoff else None,
            "snapshot": latest.isoformat(),
            "snapshot_rows": len(snap),
            "eligible_rows": len(eligible),
            "fantasy_position_rows": position_rows,
            "role_rows": len(out),
            "teams": len(teams),
        }
        if len(out) < 100 or len(teams) < 25:
            raise RuntimeError(f"{season}: incomplete current depth-chart snapshot: {audit}")
        return out, audit

    return ORIGINAL_ROLE_MAP(season, as_of)


def _safe_share(num, denom) -> float:
    numerator = base.fnum(num)
    denominator = base.fnum(denom)
    return numerator / denominator if denominator > 0 else 0.0


def attach_roles(rows: list[dict], maps: dict[int, dict]) -> list[dict]:
    """Attach existing roles plus validated WR/TE opportunity context.

    All added quantities use lag-1 player usage plus the target-season opening
    administrative role map. No target-season realized targets/snaps are used.
    """
    enriched = ORIGINAL_ATTACH_ROLES(rows, maps)
    skill = {"RB", "WR", "TE"}

    prior_team_targets = defaultdict(float)
    prior_players = defaultdict(list)
    for r in enriched:
        pos = str(r.get("position") or "")
        feature_team = str(r.get("feature_team") or "")
        target = int(r["season"])
        if pos in skill and feature_team:
            targets = base.fnum(r.get("lag1_targets"))
            prior_team_targets[(target, feature_team)] += targets
            prior_players[(target, feature_team)].append(r)

    opening_members = defaultdict(set)
    for target, mapping in maps.items():
        for (pid, pos), role in mapping.items():
            if pos not in skill:
                continue
            team = str(role.get("team") or "")
            if team:
                opening_members[(int(target), team)].add(str(pid))

    vacated_targets = defaultdict(float)
    for key, players in prior_players.items():
        target, team = key
        members = opening_members[(target, team)]
        for r in players:
            if str(r.get("player_id")) not in members:
                vacated_targets[key] += base.fnum(r.get("lag1_targets"))

    out = []
    for raw in enriched:
        r = dict(raw)
        target = int(r["season"])
        feature_team = str(r.get("feature_team") or "")
        opening_team = str(r.get("opening_team") or "")
        prior_share = _safe_share(
            r.get("lag1_targets"),
            prior_team_targets[(target, feature_team)],
        )
        vacated_share = _safe_share(
            vacated_targets[(target, opening_team)],
            prior_team_targets[(target, opening_team)],
        )
        r["prior_team_skill_target_share"] = prior_share
        r["opening_team_vacated_skill_target_share"] = vacated_share
        r["first_team_x_prior_skill_target_share"] = (
            base.fnum(r.get("opening_is_first_team")) * prior_share
        )
        out.append(r)
    return out


if __name__ == "__main__":
    base.role_map = role_map
    base.attach_roles = attach_roles
    base.main()
    from nativeize_no_history_output import nativeize
    nativeize()
