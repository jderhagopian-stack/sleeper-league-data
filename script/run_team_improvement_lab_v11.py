#!/usr/bin/env python3
"""FSFFL GM 3.0 Team Improvement Lab 1.1.

Extends Team Improvement Lab 1.0 so waiver discovery is driven by the complete
current Simulator projection pool rather than only players already represented
in the market-value catalog. This is important for true off-radar free agents.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

BASE = Path(__file__).resolve().parent / "run_team_improvement_lab.py"
MODEL_VERSION = "FSFFL-GM-Team-Improvement-Lab-1.1"


def load_base():
    spec = importlib.util.spec_from_file_location("team_improvement_lab_base", BASE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {BASE}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def full_pool_waiver_candidates(base, focus_uid, players_catalog, model_inputs, limit):
    _, _, rosters, _, players, _, projections, _ = model_inputs
    owned = base.owner_map(rosters)
    projection_players = (projections or {}).get("players") or {}
    rows = []
    for pid, proj in projection_players.items():
        pid = str(pid)
        if pid in owned:
            continue
        meta = (players or {}).get(pid) or {}
        catalog = players_catalog.get(f"player:{pid}") or {}
        position = proj.get("position") or catalog.get("position") or meta.get("position")
        if position not in {"QB", "RB", "WR", "TE"}:
            continue
        weeks = proj.get("weeks") or {}
        if not weeks:
            continue
        future_means = [base.sf(v.get("mean", v.get("median"))) * base.sf(v.get("active_probability"), 1.0) for v in weeks.values()]
        projected = sum(future_means) / max(1, len(future_means))
        if projected <= 0:
            continue
        asset = {
            "asset_id": f"player:{pid}",
            "asset_type": "player",
            "player_id": pid,
            "name": catalog.get("name") or proj.get("name") or meta.get("full_name") or meta.get("name") or f"player:{pid}",
            "position": position,
            "market_dynasty": base.sf(catalog.get("market_dynasty")),
            "market_redraft": base.sf(catalog.get("market_redraft")),
            "fsffl_value": base.sf(catalog.get("fsffl_value")),
            "owner_user_id": None,
            "market_value_available": bool(catalog),
        }
        market = base.sf(asset.get("market_dynasty")); redraft = base.sf(asset.get("market_redraft"))
        # Projection signal dominates waiver pre-screening so an unpriced breakout
        # candidate can still surface. Market value helps rank established FAs.
        screen = projected * 220 + market * .35 + redraft * .15
        rows.append({
            "channel": "WAIVER",
            "target": asset,
            "projected_weekly_mean": round(projected, 3),
            "pre_screen_score": round(screen, 2),
            "waiver_discovery_source": "full_simulator_projection_pool",
        })
    rows.sort(key=lambda x: x["pre_screen_score"], reverse=True)
    return rows[:limit]


def main():
    base = load_base()
    base.MODEL_VERSION = MODEL_VERSION
    base.waiver_candidates = lambda focus_uid, players_catalog, model_inputs, limit: full_pool_waiver_candidates(
        base, focus_uid, players_catalog, model_inputs, limit
    )
    base.main()


if __name__ == "__main__":
    main()
