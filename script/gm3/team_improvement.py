#!/usr/bin/env python3
"""Stable GM3 Team Improvement application-area entry point.

The current authoritative implementation is Team Improvement Lab 1.5. Historical
wrapper files remain for reproducibility; production callers use this stable
entry point so implementation filenames do not become application authority.
"""
from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

MODEL_VERSION = "FSFFL-GM-Team-Improvement-Application-1.0"
EXPECTED_IMPLEMENTATION_VERSION = "FSFFL-GM-Team-Improvement-Lab-1.5"
SCRIPT = Path(__file__).resolve().parent.parent
IMPLEMENTATION = SCRIPT / "run_team_improvement_lab_v13.py"

# The retained implementation imports sibling Shared Core/application-support modules
# by their historical top-level names. Executing this stable facade from script/gm3
# must preserve the same script/ import root without changing those implementations.
if str(SCRIPT) not in sys.path:
    sys.path.insert(0, str(SCRIPT))


def _load_current():
    spec = importlib.util.spec_from_file_location(
        "fsffl_gm3_team_improvement_current", IMPLEMENTATION
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to import Team Improvement implementation: {IMPLEMENTATION}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class PortfolioEvaluator:
    """Stable GM3 API for evaluating a bundle of already-discovered actions.

    Opportunity Engine may use this API for multi-step portfolio search. The
    scoring and simulation remain owned by GM3 Team Improvement and Shared Core;
    this facade adds no independent weights or valuation logic.
    """

    def __init__(self, focus_user_id: str, simulations: int = 1000, seed: int = 20260821):
        current = _load_current()
        if current.MODEL_VERSION != EXPECTED_IMPLEMENTATION_VERSION:
            raise RuntimeError(
                f"Unexpected Team Improvement implementation: {current.MODEL_VERSION}"
            )
        base = current.load_base()
        base.MODEL_VERSION = current.MODEL_VERSION

        dl = base.load_module(SCRIPT / "run_roster_decision_lab.py", "gm3_portfolio_dl")
        stateaware = base.load_module(SCRIPT / "decision_lab_state_aware.py", "gm3_portfolio_state_aware")
        self.dl = stateaware.install(dl)
        self.lineupopt = base.load_module(SCRIPT / "lineup_optimizer.py", "gm3_portfolio_lineup")
        self.rosteraware = base.load_module(SCRIPT / "roster_aware_trade.py", "gm3_portfolio_roster")
        self.base = base
        self.current = current
        self.focus_user_id = str(focus_user_id)
        self.simulations = int(simulations)
        self.seed = int(seed)
        self.model_inputs = self.dl.load_model_inputs()

        simmod, league, rosters, users, players, season, projections, raw_schedule = self.model_inputs
        self.full_projection_doc, self.full_projection_path = current.full_projection_doc(base, season)
        self.baseline_lineups = self.dl.load_cached_lineups(season)
        self.baseline = self.dl.simulate_from_lineups(
            simmod, league, rosters, users, raw_schedule,
            self.baseline_lineups, self.simulations, self.seed
        )

    def _actions_for_row(self, row):
        channel = str(row.get("channel") or "")
        if channel == "TRADE":
            return self.base.trade_actions(self.focus_user_id, row)
        if channel == "WAIVER":
            return self.base.waiver_actions(self.focus_user_id, row)
        if channel == "HOLD":
            return []
        raise ValueError(f"Unsupported portfolio channel: {channel}")

    def _inputs_with_waiver_projections(self, rows):
        mi = list(self.model_inputs)
        projections = copy.deepcopy(mi[6])
        changed = False
        for row in rows:
            if str(row.get("channel") or "") != "WAIVER":
                continue
            target = row.get("target") or {}
            pid = str(target.get("player_id") or "")
            profile = row.get("native_full_projection")
            if pid and profile:
                projections.setdefault("players", {})[pid] = copy.deepcopy(profile)
                changed = True
        if changed:
            mi[6] = projections
        return tuple(mi)

    def evaluate(self, rows):
        """Evaluate a compatible action bundle with canonical GM3 utility."""
        rows = [copy.deepcopy(x) for x in rows if str(x.get("channel") or "") != "HOLD"]
        actions = []
        for row in rows:
            actions.extend(self._actions_for_row(row))

        if not actions:
            return {
                "team_improvement_score": 0.0,
                "simulation": {
                    "focus_delta": {
                        "expected_wins": 0.0,
                        "expected_points_for": 0.0,
                        "playoff_probability": 0.0,
                        "bye_probability": 0.0,
                        "championship_probability": 0.0,
                    },
                    "strategic": {
                        "market_dynasty_delta": 0.0,
                        "base_franchise_value_delta": 0.0,
                        "break_glass_delta": 0.0,
                    },
                },
                "actions": [],
            }

        mi = self._inputs_with_waiver_projections(rows)
        sim = self.current.simulate_actions_protect_add(
            self.base,
            self.dl,
            self.lineupopt,
            self.rosteraware,
            mi,
            self.baseline_lineups,
            self.baseline,
            self.focus_user_id,
            actions,
            self.simulations,
            self.seed,
        )
        return {
            "team_improvement_score": self.base.unified_score(self.focus_user_id, sim),
            "simulation": sim,
            "actions": sim.get("effective_actions") or actions,
            "source_rows": rows,
            "authority": "GM3 Team Improvement",
            "shared_decision_utility": "FSFFL-Shared-Decision-Utility-2.0",
            "bundle_simulation_source": "current Team Improvement implementation via stable GM3 facade",
        }


def portfolio_evaluator(focus_user_id: str, simulations: int = 1000, seed: int = 20260821):
    return PortfolioEvaluator(focus_user_id, simulations=simulations, seed=seed)


def main():
    current = _load_current()
    if current.MODEL_VERSION != EXPECTED_IMPLEMENTATION_VERSION:
        raise RuntimeError(
            f"Unexpected Team Improvement implementation: {current.MODEL_VERSION}"
        )
    current.main()


if __name__ == "__main__":
    main()
