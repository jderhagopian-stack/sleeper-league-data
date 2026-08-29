#!/usr/bin/env python3
"""Production entrypoint for the FSFFL Native V2 preseason model.

This wrapper keeps the validated projection builder stable while supplying the
schema-aware timestamped role loader used for 2025+ depth-chart files. After the
veteran build it replaces the legacy no-history fallback projection values with
the separately validated native no-history role/age model.
"""
from __future__ import annotations

from datetime import datetime

import build_native_preseason_projections as base

ORIGINAL_ROLE_MAP = base.role_map


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


if __name__ == "__main__":
    base.role_map = role_map
    base.main()
    from nativeize_no_history_output import nativeize
    nativeize()
