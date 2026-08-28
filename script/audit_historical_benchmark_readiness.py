#!/usr/bin/env python3
"""Historical-backtest and benchmark readiness audit.

This audit distinguishes historical facts/mechanics that can already be tested
from model-quality claims that require time-frozen inputs. It never backfills
current values or forecasts into past decisions.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data"; OUT=DATA/"audit"; OUT.mkdir(parents=True,exist_ok=True)
MODEL_VERSION="FSFFL-Historical-Benchmark-Readiness-1.0"

def load(path,default=None):
    if not path.exists(): return default
    try:return json.loads(path.read_text(encoding="utf-8"))
    except Exception:return default

def main():
    trades=load(DATA/"trade_ledger.json",[]) or []
    completed=[x for x in trades if str(x.get("status") or "")=="complete"]
    bundles=list((DATA/"historical_gm3").glob("*/*.json")) if (DATA/"historical_gm3").exists() else []
    snapshots=list((DATA/"simulator").glob("*/snapshots/*.json")) if (DATA/"simulator").exists() else []
    empirical=load(OUT/"empirical_readiness_audit.json",{}) or {}
    trans=load(OUT/"transaction_evidence_readiness_audit.json",{}) or {}
    projections=int((empirical.get("summary") or {}).get("historical_projection_artifact_count") or 0)
    frozen=int((empirical.get("summary") or {}).get("frozen_historical_gm3_bundle_count") or len(bundles))
    acceptance_ready=False; package_ready=False
    for x in trans.get("findings",[]) or []:
        if x.get("id")=="ACCEPTANCE-CALIBRATION-READINESS-001":
            acceptance_ready=bool(x.get("authoritative_empirical_claim_allowed"))
        if x.get("id")=="PACKAGE-CALIBRATION-READINESS-001":
            package_ready=bool(x.get("authoritative_empirical_claim_allowed"))

    findings=[
      {
        "id":"HISTORICAL-GM3-BACKTEST-001",
        "status":"READY" if frozen else "BLOCKED_MISSING_TIME_FROZEN_BUNDLES",
        "completed_trade_count":len(completed),
        "frozen_bundle_count":frozen,
        "observation":"Historical transaction/state reconstruction exists, but authoritative at-the-time GM3 grading requires complete frozen league, projection, market and GM inputs for the trade timestamp. Missing bundles must return NOT_GRADED rather than use current values.",
        "authoritative_backtest_allowed":bool(frozen),
      },
      {
        "id":"HISTORICAL-PROJECTION-BACKTEST-001",
        "status":"READY" if projections else "BLOCKED_MISSING_CONTEMPORANEOUS_FORECAST_ARCHIVE",
        "historical_projection_artifact_count":projections,
        "observation":"Realized scoring history cannot by itself identify forecast bias or residual uncertainty. Time-frozen forecast snapshots are required.",
        "authoritative_backtest_allowed":bool(projections),
      },
      {
        "id":"HISTORICAL-PACKAGE-RESIDUAL-001",
        "status":"READY" if package_ready else "BLOCKED_MISSING_CONTEMPORANEOUS_VALUE_SNAPSHOTS",
        "observation":"Completed trade geometry is useful descriptively but cannot identify a package discount against contemporaneous market value without frozen value snapshots.",
        "authoritative_backtest_allowed":package_ready,
      },
      {
        "id":"HISTORICAL-ACCEPTANCE-001",
        "status":"READY" if acceptance_ready else "BLOCKED_MISSING_REJECTED_OR_EXPIRED_OFFER_DENOMINATOR",
        "observation":"Completed trades are positive choices, not an accepted/rejected offer sample. Literal acceptance-probability validation remains unavailable.",
        "authoritative_backtest_allowed":acceptance_ready,
      },
      {
        "id":"PROSPECTIVE-SNAPSHOT-001",
        "status":"ACTIVE" if snapshots else "NO_ARCHIVED_SIMULATOR_SNAPSHOT_FOUND",
        "snapshot_count":len(snapshots),
        "observation":"Prospective simulator snapshots can support future no-hindsight validation. Their existence does not retroactively create historical forecasts.",
        "authoritative_backtest_allowed":False,
      },
    ]
    payload={
      "model_version":MODEL_VERSION,
      "policy":{
        "current_value_backfill_for_historical_grade_forbidden":True,
        "current_projection_backfill_for_historical_grade_forbidden":True,
        "missing_frozen_inputs_return_not_graded":True,
        "realized_outcomes_are_separate_hindsight_layer":True,
        "mechanical_regression_success_is_not_empirical_backtest_success":True,
      },
      "summary":{
        "completed_trade_count":len(completed),
        "frozen_historical_gm3_bundle_count":frozen,
        "historical_projection_artifact_count":projections,
        "prospective_simulator_snapshot_count":len(snapshots),
        "authoritative_gm3_historical_backtest_ready":bool(frozen),
        "authoritative_projection_backtest_ready":bool(projections),
        "authoritative_package_residual_fit_ready":package_ready,
        "authoritative_acceptance_fit_ready":acceptance_ready,
      },
      "findings":findings,
    }
    (OUT/"historical_benchmark_readiness_audit.json").write_text(json.dumps(payload,indent=2),encoding="utf-8")
    print(json.dumps(payload["summary"],indent=2))
    if frozen==0 and any(x["authoritative_backtest_allowed"] for x in findings if x["id"]=="HISTORICAL-GM3-BACKTEST-001"):
        raise SystemExit("Historical GM3 backtest incorrectly promoted")
if __name__=="__main__":main()
