#!/usr/bin/env python3
"""Document and test the current same-team shock loading semantics.

This is not a calibration of TEAM_SHOCK_RHO. It verifies that the implementation
preserves marginal distributions and that same-team pairwise correlation is the
product of shared-factor loadings, preventing future calibration against the
wrong mathematical target.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np

ROOT=Path(__file__).resolve().parent.parent
SIM=ROOT/"script"/"run_fsffl_season_simulator_preproduction.py"

def load():
    spec=importlib.util.spec_from_file_location("coef_team_shock_sim",SIM)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load Simulator")
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

sim=load()

def row(pid,pos,team):
    return {
        "player_id":pid,
        "position":pos,
        "nfl_team":team,
        "mean":30.0,
        "sd":5.0,
        "active_probability":1.0,
    }

def corr(a,b):
    return float(np.corrcoef(a,b)[0,1])

def main():
    n=60000
    rng=np.random.default_rng(90210)
    shocks={}
    qb,_=sim.generate_player_draws(row("Q","QB","AAA"),1,n,rng,shocks,{})
    wr,_=sim.generate_player_draws(row("W","WR","AAA"),1,n,rng,shocks,{})
    rb,_=sim.generate_player_draws(row("R","RB","BBB"),1,n,rng,shocks,{})

    qb_mean=float(np.mean(qb)); qb_sd=float(np.std(qb))
    wr_mean=float(np.mean(wr)); wr_sd=float(np.std(wr))
    same=corr(qb,wr)
    cross=corr(qb,rb)

    expected_same=sim.TEAM_SHOCK_RHO["QB"]*sim.TEAM_SHOCK_RHO["WR"]

    # Means are far enough from zero that truncation is negligible here.
    if abs(qb_mean-30.0)>0.12 or abs(wr_mean-30.0)>0.12:
        raise AssertionError(f"team-shock transform shifted marginal mean: qb={qb_mean}, wr={wr_mean}")
    if abs(qb_sd-5.0)>0.12 or abs(wr_sd-5.0)>0.12:
        raise AssertionError(f"team-shock transform shifted marginal SD: qb={qb_sd}, wr={wr_sd}")
    if abs(same-expected_same)>0.025:
        raise AssertionError(
            f"same-team empirical correlation {same} does not match loading-product semantics {expected_same}"
        )
    if abs(cross)>0.025:
        raise AssertionError(f"cross-team draws unexpectedly correlated: {cross}")

    print({
        "passed":True,
        "production_behavior_changed":False,
        "calibration_performed":False,
        "qb_loading":sim.TEAM_SHOCK_RHO["QB"],
        "wr_loading":sim.TEAM_SHOCK_RHO["WR"],
        "expected_qb_wr_pairwise_correlation":round(expected_same,6),
        "empirical_qb_wr_pairwise_correlation":round(same,6),
        "empirical_cross_team_correlation":round(cross,6),
        "marginal_qb_mean":round(qb_mean,4),
        "marginal_qb_sd":round(qb_sd,4),
        "interpretation":"TEAM_SHOCK_RHO values are shared-factor loadings, not direct pairwise correlations",
    })

if __name__=="__main__":
    main()
