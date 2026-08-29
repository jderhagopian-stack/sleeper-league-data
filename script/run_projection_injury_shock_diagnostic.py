#!/usr/bin/env python3
"""Runner that attaches opening-role features and throttles FFToday access."""
from __future__ import annotations

import time
import urllib.error

import diagnose_external_benchmark_injury_shocks as diagnostic
import run_native_vs_fftoday_historical_benchmark as fftsrc
from run_native_projection_opening_role_by_position_benchmark import attach

_original_native_predictions = diagnostic.native_predictions
_original_fetch_html = fftsrc.fetch_html
_cache = {}
_last_request_at = [0.0]


def native_predictions_with_roles(rows, target_season, position):
    key = id(rows)
    if key not in _cache:
        seasons = sorted({int(r["season"]) for r in rows})
        _cache[key] = attach(rows, seasons)[0]
    return _original_native_predictions(_cache[key], target_season, position)


def throttled_fetch_html(url: str) -> str:
    # FFToday permits the public historical pages but intermittently rate-limits
    # GitHub-hosted runners when pages are requested in a burst. Keep the exact
    # URL/data unchanged while spacing requests and backing off on 403/429.
    for attempt in range(6):
        elapsed = time.monotonic() - _last_request_at[0]
        if elapsed < 1.25:
            time.sleep(1.25 - elapsed)
        try:
            out = _original_fetch_html(url)
            _last_request_at[0] = time.monotonic()
            return out
        except urllib.error.HTTPError as exc:
            _last_request_at[0] = time.monotonic()
            if exc.code not in {403, 429} or attempt == 5:
                raise
            time.sleep(4.0 * (attempt + 1))
    raise RuntimeError("unreachable FFToday retry state")


fftsrc.fetch_html = throttled_fetch_html
diagnostic.native_predictions = native_predictions_with_roles

if __name__ == "__main__":
    diagnostic.main()
