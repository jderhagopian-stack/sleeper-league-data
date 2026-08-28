#!/usr/bin/env python3
"""Normalize the existing Razzball-derived FSFFL baseline into source format.

This is a compatibility bridge. It does not change Razzball calculations; it
makes the existing source explicit so the governed ensemble can consume it
without treating the legacy production artifact as an opaque final forecast.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DATA = Path("data")
SIM_ROOT = DATA / "simulator"


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")


def main():
    league = load_json(DATA / "league.json")
    if not league:
        raise RuntimeError("Missing data/league.json")
    season = str(league.get("season") or "").strip()
    if not season:
        raise RuntimeError("Active season missing from data/league.json")

    source_path = SIM_ROOT / season / "sources" / "preseason_fsffl_points.json"
    payload = load_json(source_path)
    if not payload or not payload.get("players"):
        raise RuntimeError(f"Missing populated legacy projection baseline: {source_path}")

    source_label = str(payload.get("source") or "")
    if "razzball" not in source_label.lower():
        raise RuntimeError(
            "Compatibility normalizer only accepts the existing Razzball baseline; "
            f"found source={source_label!r}. Refusing to relabel another source."
        )

    players = {}
    for sid, player in payload["players"].items():
        points = player.get("fsffl_projected_points")
        ppg = player.get("fsffl_projected_ppg")
        if points is None:
            continue
        players[str(sid)] = {
            "sleeper_id": str(sid),
            "player_name": player.get("player_name"),
            "team": player.get("team"),
            "position": player.get("position"),
            "season": str(player.get("season") or season),
            "games_projected": player.get("games_projected"),
            "projected_stats": player.get("projected_stats"),
            "fsffl_projected_points": points,
            "fsffl_projected_ppg": ppg,
            "source": "Razzball",
            "source_match_method": player.get("match_method"),
        }

    normalized = {
        "season": season,
        "source_id": "razzball",
        "source_name": "Razzball",
        "provenance_class": "EXTERNAL_CURRENT_FORECAST",
        "scoring_source": payload.get("scoring_source") or "data/league.json",
        "source_urls": payload.get("source_urls"),
        "source_last_updated": payload.get("source_last_updated"),
        "players": players,
    }

    out = SIM_ROOT / season / "sources" / "projection_razzball.json"
    write_json(out, normalized)
    print(f"Normalized Razzball projection source: {len(players)} players -> {out}")


if __name__ == "__main__":
    main()
