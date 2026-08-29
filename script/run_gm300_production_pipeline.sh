#!/usr/bin/env bash
set -euo pipefail

# FSFFL GM 3.0.0 canonical production runner.
# Assumes fresh league/projection/simulator data already exists in data/.
# Both the manual GM build and the daily Sleeper refresh call this file so
# the production decision pipeline has a single source of truth.

: "${GM30_FOCAL_MANAGER:=jimmygoodjob}"
: "${GM30_CF_SCREEN_SIMS:=2500}"
: "${GM30_CF_FOCAL_SCREEN_SIMS:=5000}"
: "${GM30_CF_FINAL_SIMS:=15000}"
: "${GM30_CF_GENERAL_TARGETS:=3}"
: "${GM30_CF_GENERAL_PACKAGES:=1}"
: "${GM30_CF_FOCAL_TARGETS:=6}"
: "${GM30_CF_FOCAL_PACKAGES:=2}"
: "${GM30_CF_FOCAL_FINALISTS:=3}"

export GM30_FOCAL_MANAGER \
  GM30_CF_SCREEN_SIMS \
  GM30_CF_FOCAL_SCREEN_SIMS \
  GM30_CF_FINAL_SIMS \
  GM30_CF_GENERAL_TARGETS \
  GM30_CF_GENERAL_PACKAGES \
  GM30_CF_FOCAL_TARGETS \
  GM30_CF_FOCAL_PACKAGES \
  GM30_CF_FOCAL_FINALISTS

echo "========================================"
echo "FSFFL GM 3.0.0 PRODUCTION PIPELINE"
echo "========================================"

echo "[1/10] Clean GM production output"
rm -rf data/gm
mkdir -p data/gm

echo "[2/10] Build automatic prospect inputs"
python script/build_gm30_prospect_inputs.py

echo "[3/10] Enrich prospect features"
python script/build_gm30_prospect_features.py

echo "[4/10] Build prospect intelligence"
python script/build_gm30_prospect_engine.py
if [[ -f data/gm/gm30_prospect_radar.json ]]; then
  cp data/gm/gm30_prospect_radar.json data/gm/prospect_board.json
fi

echo "[5/10] Build calibration audit"
python script/build_gm30_calibration.py

echo "[6/10] Build phase-aware football intelligence"
python script/build_gm30_football_intelligence.py

echo "[7/10] Build current catalysts"
python script/build_gm30_current_catalysts.py

echo "[8/10] Build emerging-value intelligence"
python script/build_gm30_emerging_value.py

echo "[9/10] Run governed simulator-integrated counterfactual GM"
python script/run_fsffl_gm30_counterfactual_governed.py

echo "[10/10] Run consolidated GM 3.0 validation"
python script/validate_gm30.py

python - <<'PY'
import json
import os
from pathlib import Path

focal_manager = os.environ.get("GM30_FOCAL_MANAGER", "jimmygoodjob")
idx = json.load(open("data/gm/franchise_index.json"))
focal = next(
    x for x in idx["teams"]
    if x.get("manager") == focal_manager
)
trade_path = Path(focal["paths"]["trade_opportunities"])
trade = json.load(trade_path.open())

simulated = []
for opp in trade.get("opportunities") or []:
    for pkg in opp.get("best_candidate_packages") or []:
        if pkg.get("counterfactual_simulation_status") in {
            "SCREENED",
            "FINAL_CONFIRMED",
        }:
            simulated.append(pkg)

if not simulated:
    raise SystemExit("No counterfactual-simulated focal trade packages found")

required = {
    "expected_points_delta",
    "expected_wins_delta",
    "playoff_probability_delta",
    "bye_probability_delta",
    "championship_probability_delta",
}

for pkg in simulated:
    summary = pkg.get("gm30_simulation_summary") or {}
    if required.issubset(summary):
        break
else:
    raise SystemExit(
        "Counterfactual simulation missing required outcome deltas"
    )

validation = json.load(open("data/gm/validation_report.json"))
if not validation.get("passed"):
    raise SystemExit("GM 3.0 consolidated validation did not pass")

print(
    "GM 3.0.0 production validation: PASS — "
    f"{len(simulated)} focal packages simulated; "
    f"{len(validation.get('warnings') or [])} warnings."
)
PY

echo ""
echo "GM 3.0.0 output tree:"
find data/gm -type f -print | sort
