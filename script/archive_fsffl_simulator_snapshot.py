#!/usr/bin/env python3
"""
Archive one compact FSFFL Simulator production snapshot per UTC day.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

DATA = Path("data")
SIM_ROOT = DATA / "simulator"


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")


def main():
    league = load_json(DATA / "league.json")
    if not league:
        raise RuntimeError("Missing data/league.json")

    season = str(league.get("season"))
    root = SIM_ROOT / season
    standings = load_json(root / "outputs" / "standings_projection.json")
    simulation = load_json(root / "outputs" / "season_simulation.json")
    preprod = load_json(root / "outputs" / "preproduction_validation.json")
    opponent_audit = load_json(root / "outputs" / "opponent_adjustment_audit.json")
    dynamic_audit = load_json(root / "outputs" / "dynamic_projection_audit.json")

    if not standings or not simulation:
        raise RuntimeError("Simulator outputs missing; cannot archive snapshot.")

    date_key = datetime.now(timezone.utc).date().isoformat()

    snapshot = {
        "snapshot_date_utc": date_key,
        "generated_at_utc": standings.get("generated_at_utc"),
        "season": season,
        "model_version": standings.get("model_version"),
        "simulations": simulation.get("simulations"),
        "rng_seed": simulation.get("rng_seed"),
        "runtime": simulation.get("runtime"),
        "features": simulation.get("features"),
        "validation": {
            "preproduction": preprod,
            "opponent_adjustments": opponent_audit,
            "dynamic_projection_summary": {
                "season_type": (dynamic_audit or {}).get("season_type"),
                "current_week": (dynamic_audit or {}).get("current_week"),
                "players_with_actual_samples": (
                    dynamic_audit or {}
                ).get("players_with_actual_samples"),
                "players_with_future_mean_changes": (
                    dynamic_audit or {}
                ).get("players_with_future_mean_changes"),
                "availability_rows_changed": (
                    dynamic_audit or {}
                ).get("availability_rows_changed"),
            },
        },
        "teams": standings.get("teams"),
    }

    path = root / "snapshots" / f"{date_key}.json"
    write_json(path, snapshot)
    print(f"Archived simulator snapshot: {path}")


if __name__ == "__main__":
    main()
