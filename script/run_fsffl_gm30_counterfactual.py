#!/usr/bin/env python3
"""
FSFFL GM 3.0 — Counterfactual Transaction Simulator

This is the missing bridge between the GM economics layer and the season
simulator. It evaluates serious GM trade candidates by mutating the actual
league rosters, re-running the SAME Monte Carlo season engine with the SAME
deterministic random seed, and measuring the expected change in:

- season points scored
- expected wins
- playoff probability
- bye probability
- championship probability

The results are injected back into GM 3.0 package scoring BEFORE the universal
command centers are produced.

Architecture
------------
1. GM 2.2 generates economically plausible bilateral packages.
2. This layer screens the best packages with paired Monte Carlo simulations.
3. Counterfactual football-outcome deltas modify GM 3.0's decision score.
4. Packages are re-ranked.
5. The top focal-team packages receive a higher-simulation confirmation run.
6. GM 3.0 command centers therefore consume simulation-informed advice.

Important:
- Future picks affect dynasty economics, but not current-season scoring rosters.
- Player transfers are applied to BOTH teams in bilateral trades.
- Baseline and counterfactual use identical seeds ("common random numbers"),
  sharply reducing Monte Carlo noise in the delta.
"""
from __future__ import annotations

import copy
import json
import os
import sys
from functools import lru_cache
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
DATA = ROOT / "data"

sys.path.insert(0, str(SCRIPT_DIR))

import build_fsffl_gm30 as gm30
import run_fsffl_season_simulator_preproduction as sim


MODEL = "FSFFL-GM-3.0-Counterfactual-Transaction-Simulator-v1"

SCREEN_SIMS = int(os.getenv("GM30_CF_SCREEN_SIMS", "2500"))
FOCAL_SCREEN_SIMS = int(os.getenv("GM30_CF_FOCAL_SCREEN_SIMS", "5000"))
FINAL_SIMS = int(os.getenv("GM30_CF_FINAL_SIMS", "15000"))

GENERAL_TARGETS = int(os.getenv("GM30_CF_GENERAL_TARGETS", "3"))
GENERAL_PACKAGES = int(os.getenv("GM30_CF_GENERAL_PACKAGES", "1"))
FOCAL_TARGETS = int(os.getenv("GM30_CF_FOCAL_TARGETS", "6"))
FOCAL_PACKAGES = int(os.getenv("GM30_CF_FOCAL_PACKAGES", "2"))
FOCAL_FINALISTS = int(os.getenv("GM30_CF_FOCAL_FINALISTS", "3"))

FOCAL_MANAGER = os.getenv("GM30_FOCAL_MANAGER", "jimmygoodjob")


def load(path: Path, default=None):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def clamp(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, x))


class CounterfactualEngine:
    def __init__(self):
        self.league = load(DATA / "league.json", {}) or {}
        self.rosters = load(DATA / "rosters.json", []) or []
        self.users = load(DATA / "users.json", []) or []
        self.players = load(DATA / "players.json", {}) or {}

        if not self.league:
            raise RuntimeError("data/league.json is required")

        self.season = str(self.league["season"])
        self.schedule = load(
            DATA / "stats" / "fsffl" / self.season / "league_matchups_raw.json",
            {},
        ) or {}
        self.projections = load(
            DATA
            / "simulator"
            / self.season
            / "inputs"
            / "player_weekly_projections.json",
            {},
        ) or {}

        validation = sim.core.validate_inputs(
            self.league,
            self.rosters,
            self.users,
            self.players,
            self.schedule,
            self.projections,
        )
        if not validation.get("validation_passed"):
            raise RuntimeError(
                "Counterfactual simulation input validation failed: "
                + json.dumps(validation)
            )

        self.uid_to_roster_id = {
            str(r.get("owner_id")): int(r.get("roster_id"))
            for r in self.rosters
            if r.get("owner_id") is not None and r.get("roster_id") is not None
        }
        self.roster_id_to_uid = {
            rid: uid for uid, rid in self.uid_to_roster_id.items()
        }

        self.focal_uid = None
        for u in self.users:
            uid = str(u.get("user_id") or "")
            manager = u.get("display_name") or u.get("username")
            if manager == FOCAL_MANAGER:
                self.focal_uid = uid
                break

        # Fallback through owner behavior profile if display names differ.
        if self.focal_uid is None:
            for p in load(DATA / "owner_behavior_profiles.json", []) or []:
                if p.get("manager") == FOCAL_MANAGER:
                    self.focal_uid = str(p.get("user_id"))
                    break

        self._baseline_cache = {}
        self._result_cache = {}

    def _run(self, rosters, n_sims):
        seed = sim.deterministic_seed(self.league, self.season)
        return sim.run_preproduction_simulation(
            league=self.league,
            rosters=rosters,
            users=self.users,
            players=self.players,
            raw_schedule=self.schedule,
            projections=self.projections,
            n_sims=int(n_sims),
            seed=seed,
        )

    def baseline(self, n_sims):
        n_sims = int(n_sims)
        if n_sims not in self._baseline_cache:
            self._baseline_cache[n_sims] = self._run(
                copy.deepcopy(self.rosters),
                n_sims,
            )
        return self._baseline_cache[n_sims]

    @staticmethod
    def _team_by_uid(result, uid):
        uid = str(uid)
        return next(
            (x for x in (result.get("teams") or []) if str(x.get("user_id")) == uid),
            None,
        )

    @staticmethod
    def _player_ids(asset_ids):
        out = []
        for aid in asset_ids or []:
            aid = str(aid)
            if aid.startswith("player:"):
                out.append(aid.split(":", 1)[1])
        return out

    def mutate_trade(self, focal_uid, seller_uid, outgoing_asset_ids, target_asset_id):
        focal_uid = str(focal_uid)
        seller_uid = str(seller_uid)

        rosters = copy.deepcopy(self.rosters)
        by_uid = {str(r.get("owner_id")): r for r in rosters}

        if focal_uid not in by_uid or seller_uid not in by_uid:
            raise KeyError("Trade participant not found in data/rosters.json")

        focal = by_uid[focal_uid]
        seller = by_uid[seller_uid]

        outgoing_pids = self._player_ids(outgoing_asset_ids)
        target_pids = self._player_ids([target_asset_id])

        def remove_everywhere(roster, pids):
            pids = set(map(str, pids))
            for key in ("players", "reserve", "taxi"):
                roster[key] = [
                    str(x) for x in (roster.get(key) or [])
                    if str(x) not in pids
                ]

        def add_active(roster, pids):
            current = [str(x) for x in (roster.get("players") or [])]
            for pid in map(str, pids):
                if pid not in current:
                    current.append(pid)
            roster["players"] = current

        # Focal gives outgoing players to seller.
        remove_everywhere(focal, outgoing_pids)
        add_active(seller, outgoing_pids)

        # Seller gives target player to focal.
        remove_everywhere(seller, target_pids)
        add_active(focal, target_pids)

        return rosters

    @staticmethod
    def _delta(after, before, key):
        a = after.get(key)
        b = before.get(key)
        if a is None or b is None:
            return None
        return round(float(a) - float(b), 5)

    def evaluate_trade(
        self,
        focal_uid,
        seller_uid,
        outgoing_asset_ids,
        target_asset_id,
        n_sims,
    ):
        key = (
            str(focal_uid),
            str(seller_uid),
            tuple(sorted(map(str, outgoing_asset_ids or []))),
            str(target_asset_id),
            int(n_sims),
        )
        if key in self._result_cache:
            return self._result_cache[key]

        baseline = self.baseline(n_sims)
        mutated = self.mutate_trade(
            focal_uid,
            seller_uid,
            outgoing_asset_ids,
            target_asset_id,
        )
        counter = self._run(mutated, n_sims)

        before_f = self._team_by_uid(baseline, focal_uid) or {}
        after_f = self._team_by_uid(counter, focal_uid) or {}
        before_s = self._team_by_uid(baseline, seller_uid) or {}
        after_s = self._team_by_uid(counter, seller_uid) or {}

        fields = (
            "expected_points_for",
            "expected_wins",
            "playoff_probability",
            "bye_probability",
            "championship_probability",
        )

        focal_delta = {
            k: self._delta(after_f, before_f, k)
            for k in fields
        }
        seller_delta = {
            k: self._delta(after_s, before_s, k)
            for k in fields
        }

        result = {
            "model": MODEL,
            "simulations": int(n_sims),
            "paired_common_random_numbers": True,
            "focal_user_id": str(focal_uid),
            "seller_user_id": str(seller_uid),
            "focal_before": {k: before_f.get(k) for k in fields},
            "focal_after": {k: after_f.get(k) for k in fields},
            "focal_delta": focal_delta,
            "seller_before": {k: before_s.get(k) for k in fields},
            "seller_after": {k: after_s.get(k) for k in fields},
            "seller_delta": seller_delta,
        }
        self._result_cache[key] = result
        return result

    @staticmethod
    def outcome_score(sim_result):
        """
        Normalize current-season football outcome change roughly to [-1, 1].
        Championship equity gets the highest weight.
        """
        d = sim_result.get("focal_delta") or {}

        points = clamp((d.get("expected_points_for") or 0.0) / 100.0)
        wins = clamp((d.get("expected_wins") or 0.0) / 1.0)
        playoff = clamp((d.get("playoff_probability") or 0.0) / 0.10)
        bye = clamp((d.get("bye_probability") or 0.0) / 0.10)
        title = clamp((d.get("championship_probability") or 0.0) / 0.05)

        return (
            0.15 * points
            + 0.20 * wins
            + 0.20 * playoff
            + 0.10 * bye
            + 0.35 * title
        )

    @staticmethod
    def externality_penalty(sim_result):
        """
        Penalize simultaneously increasing the trade partner's title equity.
        This is intentionally modest: a good trade remains a good trade, but
        strengthening a direct contender is not strategically free.
        """
        d = sim_result.get("seller_delta") or {}
        seller_title = d.get("championship_probability") or 0.0
        seller_playoff = d.get("playoff_probability") or 0.0

        return (
            0.70 * max(clamp(seller_title / 0.05), 0.0)
            + 0.30 * max(clamp(seller_playoff / 0.10), 0.0)
        )


ENGINE = None


def install_counterfactual_trade_patch():
    global ENGINE
    ENGINE = CounterfactualEngine()

    original = gm30.core.build_universal_trade_opportunities

    def simulator_informed_trade_opportunities(uid, ctx=None, profile_by_uid=None):
        uid = str(uid)
        payload = original(uid, ctx=ctx, profile_by_uid=profile_by_uid)

        if payload.get("error"):
            return payload

        # Use the same runtime context GM 2.2 used to derive team objectives.
        local_ctx = ctx or gm30.core._u_load_context()
        team = (local_ctx.get("teams") or {}).get(uid, {})
        _, objective_weights = gm30.core._u_team_objective_weights(team)
        current_weight = float(objective_weights.get("current") or 0.0)

        is_focal = ENGINE.focal_uid is not None and uid == str(ENGINE.focal_uid)
        target_limit = FOCAL_TARGETS if is_focal else GENERAL_TARGETS
        package_limit = FOCAL_PACKAGES if is_focal else GENERAL_PACKAGES
        n_sims = FOCAL_SCREEN_SIMS if is_focal else SCREEN_SIMS

        opportunities = payload.get("opportunities") or []

        for opp_index, opp in enumerate(opportunities):
            packages = opp.get("best_candidate_packages") or []

            # Preserve original scores on every package, even when not simulated.
            for pkg in packages:
                if "gm22_decision_score" not in pkg:
                    pkg["gm22_decision_score"] = pkg.get("decision_score")
                pkg["counterfactual_simulation_status"] = "NOT_SCREENED"

            if opp_index >= target_limit:
                continue

            seller_uid = str(opp.get("seller_user_id") or "")
            target_asset_id = opp.get("target_asset_id")
            if not seller_uid or not target_asset_id:
                continue

            for pkg_index, pkg in enumerate(packages[:package_limit]):
                sim_result = ENGINE.evaluate_trade(
                    focal_uid=uid,
                    seller_uid=seller_uid,
                    outgoing_asset_ids=pkg.get("focal_outgoing_asset_ids") or [],
                    target_asset_id=target_asset_id,
                    n_sims=n_sims,
                )

                football = ENGINE.outcome_score(sim_result)
                externality = ENGINE.externality_penalty(sim_result)
                core_score = float(pkg.get("gm22_decision_score") or 0.0)

                # Simulation has greater authority for contenders, less for rebuilders.
                sim_authority = 0.10 + 0.30 * current_weight

                integrated = (
                    core_score
                    + sim_authority * football
                    - 0.08 * externality
                )

                pkg["counterfactual_simulation_status"] = "SCREENED"
                pkg["counterfactual_simulation"] = sim_result
                pkg["football_outcome_score"] = round(football, 6)
                pkg["competitive_externality_penalty"] = round(externality, 6)
                pkg["simulation_authority_weight"] = round(sim_authority, 4)
                pkg["gm30_decision_score"] = round(integrated, 6)
                # Downstream GM 2.2 command center reads "decision_score".
                pkg["decision_score"] = round(integrated, 6)

                fd = sim_result.get("focal_delta") or {}
                title_delta = fd.get("championship_probability") or 0.0
                wins_delta = fd.get("expected_wins") or 0.0
                points_delta = fd.get("expected_points_for") or 0.0

                # Simulation-aware recommendation guardrails.
                band = pkg.get("recommendation_band")
                if current_weight >= 0.35:
                    if title_delta <= -0.02 and wins_delta <= -0.30:
                        pkg["recommendation_band"] = "focal_overpay_or_bad_timing"
                    elif (
                        title_delta >= 0.02
                        and wins_delta >= 0.20
                        and band in {"low_priority", "negotiation_candidate"}
                    ):
                        pkg["recommendation_band"] = "negotiation_candidate"

                pkg["gm30_simulation_summary"] = {
                    "expected_points_delta": points_delta,
                    "expected_wins_delta": wins_delta,
                    "playoff_probability_delta": fd.get("playoff_probability"),
                    "bye_probability_delta": fd.get("bye_probability"),
                    "championship_probability_delta": title_delta,
                }

            # Re-rank packages after counterfactual evidence.
            rank = {
                "mutual_value_candidate": 4,
                "negotiation_candidate": 3,
                "low_priority": 2,
                "seller_underpaid": 1,
                "focal_overpay_or_bad_timing": 0,
            }
            packages.sort(
                key=lambda x: (
                    rank.get(x.get("recommendation_band"), 0),
                    float(x.get("gm30_decision_score", x.get("decision_score") or -999)),
                    float(x.get("focal_surplus_after_wait_benchmark") or -999999),
                ),
                reverse=True,
            )
            opp["best_candidate_packages"] = packages[:10]
            if packages:
                opp["best_package_recommendation_band"] = packages[0].get(
                    "recommendation_band"
                )
                opp["best_package_decision_score"] = packages[0].get(
                    "decision_score"
                )

        # Higher-precision confirmation for the focal manager's very best packages.
        if is_focal:
            finalists = []
            for oi, opp in enumerate(opportunities[:target_limit]):
                for pi, pkg in enumerate((opp.get("best_candidate_packages") or [])[:package_limit]):
                    if pkg.get("counterfactual_simulation_status") == "SCREENED":
                        finalists.append(
                            (
                                float(pkg.get("decision_score") or -999),
                                oi,
                                pi,
                            )
                        )
            finalists.sort(reverse=True)

            for _, oi, pi in finalists[:FOCAL_FINALISTS]:
                opp = opportunities[oi]
                packages = opp.get("best_candidate_packages") or []
                if pi >= len(packages):
                    continue
                pkg = packages[pi]

                final = ENGINE.evaluate_trade(
                    focal_uid=uid,
                    seller_uid=str(opp.get("seller_user_id")),
                    outgoing_asset_ids=pkg.get("focal_outgoing_asset_ids") or [],
                    target_asset_id=opp.get("target_asset_id"),
                    n_sims=FINAL_SIMS,
                )
                football = ENGINE.outcome_score(final)
                externality = ENGINE.externality_penalty(final)
                core_score = float(pkg.get("gm22_decision_score") or 0.0)
                sim_authority = float(pkg.get("simulation_authority_weight") or 0.2)
                integrated = (
                    core_score
                    + sim_authority * football
                    - 0.08 * externality
                )

                pkg["counterfactual_simulation_status"] = "FINAL_CONFIRMED"
                pkg["counterfactual_simulation"] = final
                pkg["football_outcome_score"] = round(football, 6)
                pkg["competitive_externality_penalty"] = round(externality, 6)
                pkg["gm30_decision_score"] = round(integrated, 6)
                pkg["decision_score"] = round(integrated, 6)

                fd = final.get("focal_delta") or {}
                pkg["gm30_simulation_summary"] = {
                    "expected_points_delta": fd.get("expected_points_for"),
                    "expected_wins_delta": fd.get("expected_wins"),
                    "playoff_probability_delta": fd.get("playoff_probability"),
                    "bye_probability_delta": fd.get("bye_probability"),
                    "championship_probability_delta": fd.get(
                        "championship_probability"
                    ),
                }

            # Re-sort focal opportunities/packages one final time.
            for opp in opportunities[:target_limit]:
                packages = opp.get("best_candidate_packages") or []
                packages.sort(
                    key=lambda x: (
                        float(x.get("decision_score") or -999),
                        float(x.get("focal_surplus_after_wait_benchmark") or -999999),
                    ),
                    reverse=True,
                )
                if packages:
                    opp["best_package_recommendation_band"] = packages[0].get(
                        "recommendation_band"
                    )
                    opp["best_package_decision_score"] = packages[0].get(
                        "decision_score"
                    )

        opportunities.sort(
            key=lambda x: (
                x.get("best_package_recommendation_band") == "mutual_value_candidate",
                x.get("best_package_recommendation_band") == "negotiation_candidate",
                float(x.get("best_package_decision_score") or -999),
                float(x.get("focal_position_need") or 0.0),
            ),
            reverse=True,
        )

        payload["opportunities"] = opportunities
        payload["model_version"] = MODEL
        payload["counterfactual_simulation"] = {
            "enabled": True,
            "focal_manager": FOCAL_MANAGER,
            "focal_user_id": ENGINE.focal_uid,
            "screen_simulations_general": SCREEN_SIMS,
            "screen_simulations_focal": FOCAL_SCREEN_SIMS,
            "final_simulations_focal": FINAL_SIMS,
            "common_random_numbers": True,
            "current_season_player_moves_simulated_bilaterally": True,
            "future_picks_scoring_effect": "NONE_CURRENT_SEASON",
            "decision_integration": (
                "GM22 economics + counterfactual current-season outcome delta "
                "+ opponent-strength externality"
            ),
        }
        return payload

    gm30.core.build_universal_trade_opportunities = (
        simulator_informed_trade_opportunities
    )


def main():
    install_counterfactual_trade_patch()
    gm30.main()


if __name__ == "__main__":
    main()
