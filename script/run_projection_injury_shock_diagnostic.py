#!/usr/bin/env python3
"""Runner that attaches the benchmark's opening-role features before prediction."""
from __future__ import annotations

import diagnose_external_benchmark_injury_shocks as diagnostic
from run_native_projection_opening_role_by_position_benchmark import attach

_original_native_predictions = diagnostic.native_predictions
_cache = {}


def native_predictions_with_roles(rows, target_season, position):
    key = id(rows)
    if key not in _cache:
        seasons = sorted({int(r["season"]) for r in rows})
        _cache[key] = attach(rows, seasons)[0]
    return _original_native_predictions(_cache[key], target_season, position)


diagnostic.native_predictions = native_predictions_with_roles

if __name__ == "__main__":
    diagnostic.main()
