#!/usr/bin/env python3
"""Regression tests for competitive-state / strategic-posture separation."""
from __future__ import annotations
import importlib.util
from pathlib import Path

SCRIPT=Path(__file__).resolve().parent

def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod

weighting=load(SCRIPT/'gm_state_weighting.py','test_posture_weighting')
posture=load(SCRIPT/'strategic_posture.py','test_strategic_posture')
cal=weighting.load_calibration()
calculated_weights=weighting.interpolate(.90,cal.get('anchor_points') or [])
wr={
    'state':'elite_contender',
    'weights':calculated_weights,
    'inputs':{'competitive_strength_score':.90},
}

auto=posture.resolve(wr,'AUTO',weighting,cal)
push=posture.resolve(wr,'PUSH_CHIPS_IN',weighting,cal)
balanced=posture.resolve(wr,'BALANCED_CONTENDER',weighting,cal)
preserve=posture.resolve(wr,'PRESERVE_FUTURE_VALUE',weighting,cal)
retool=posture.resolve(wr,'RETOOL',weighting,cal)
rebuild=posture.resolve(wr,'REBUILD',weighting,cal)

assert auto['competitive_state']=='elite_contender'
assert auto['active_weights']==auto['calculated_state_weights']
assert auto['posture_source']=='MODEL_DEFAULT'
for row in (push,balanced,preserve,retool,rebuild):
    assert row['competitive_state']=='elite_contender'
    assert row['competitive_state_is_modified_by_owner_override'] is False
    assert row['posture_source']=='OWNER_OVERRIDE'
    assert row['uses_existing_governed_weight_curve'] is True
    assert row['new_valuation_coefficients_introduced'] is False

thresholds=cal.get('classification_thresholds') or {}
assert balanced['posture_curve_score']==round(float(thresholds.get('contender',.55)),6)
assert preserve['posture_curve_score']==balanced['posture_curve_score']
assert retool['posture_curve_score']==round(float(thresholds.get('retool',.35)),6)
assert push['posture_curve_score']==max(
    round(float(x.get('competitive_strength_score',x.get('contender_score',0))),6)
    for x in cal.get('anchor_points') or []
)
assert rebuild['posture_curve_score']==min(
    round(float(x.get('competitive_strength_score',x.get('contender_score',0))),6)
    for x in cal.get('anchor_points') or []
)
assert 'immediate_current_value' in push['search_lane_order']
assert push['search_lane_order'][0]=='immediate_current_value'
for row in (preserve,retool,rebuild):
    assert row['search_lane_order'][0]=='future_value_preservation'

# The shared Decision Lab overlay must scope an owner override to one user.
overlay=(SCRIPT/'decision_lab_state_aware.py').read_text(encoding='utf-8')
assert 'effective_selection = selected_posture if (override_uid and uid == override_uid) else "AUTO"' in overlay
assert 'owner_override_user_id=None' in overlay

# Opportunity Engine must carry posture through both single-step and portfolio paths.
oe=(SCRIPT/'opportunity_engine'/'application_v21.py').read_text(encoding='utf-8')
assert "--strategic-posture" in oe
assert "a.strategic_posture" in oe
gm=(SCRIPT/'gm3'/'team_improvement.py').read_text(encoding='utf-8')
assert "strategic_posture=self.strategic_posture" in gm

print('Strategic posture separation regressions passed')
