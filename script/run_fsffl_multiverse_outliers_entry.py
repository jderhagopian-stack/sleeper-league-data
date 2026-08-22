#!/usr/bin/env python3
"""Compatibility entrypoint for the FSFFL Multiverse tracker.

The original tracker expected legacy top-level schedule filenames. Simulator 1.0's
canonical schedule lives at data/stats/fsffl/<season>/league_matchups_raw.json.
This entrypoint redirects those legacy lookups to the canonical schedule and then
runs the tracker unchanged.
"""
from pathlib import Path

import build_fsffl_season_simulator as core

DATA = Path("data")
_ORIGINAL_LOAD_JSON = core.load_json
_LEGACY_SCHEDULE_NAMES = {"schedule.json", "full_schedule.json", "matchups.json"}


def canonical_load_json(path, default=None):
    path = Path(path)
    if path.parent == DATA and path.name in _LEGACY_SCHEDULE_NAMES:
        league = _ORIGINAL_LOAD_JSON(DATA / "league.json")
        if not league:
            return default
        season = str(league.get("season"))
        canonical = DATA / "stats" / "fsffl" / season / "league_matchups_raw.json"
        return _ORIGINAL_LOAD_JSON(canonical, default)
    return _ORIGINAL_LOAD_JSON(path, default)


core.load_json = canonical_load_json

import run_fsffl_multiverse_outliers as tracker  # noqa: E402

tracker.core.load_json = canonical_load_json


if __name__ == "__main__":
    tracker.main()
