#!/usr/bin/env python3
"""Exact per-season ledger fingerprinting for Alternate History performance mode.

The base performance runtime memoizes a hash of each complete multi-season
ledger object. Weekly copy-on-write creates a new outer ledger on every scoring
step even though completed season rows are shared and immutable. This layer
reuses the existing stable hash for each season row by object identity, so only
the row that actually changed needs to be hashed again.

It changes no state, decision, probability, scoring result, or Simulator input.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

import alternate_history_engine as ah
import alternate_history_performance_runtime as perf

_SEASON_ROW_HASH_CACHE: Dict[int, str] = {}
_SOURCE_ROWS: Dict[int, Any] = {}


def _season_row_hash(row: Any) -> str:
    identity = id(row)
    cached = _SEASON_ROW_HASH_CACHE.get(identity)
    if cached is None:
        # Retain the object so Python cannot reuse its id for a different row.
        _SOURCE_ROWS[identity] = row
        cached = ah.stable_hash(row)
        _SEASON_ROW_HASH_CACHE[identity] = cached
    return cached


def ledger_fingerprint(ledger: Dict[str, Any]) -> Tuple[Tuple[str, str], ...]:
    """Canonical equality key using the same stable hash per season row."""
    return tuple(
        (str(season), _season_row_hash(row))
        for season, row in sorted((ledger or {}).items(), key=lambda item: str(item[0]))
    )


def install() -> None:
    # _state_key_with_memo resolves this global at call time, so replacing only
    # the ledger-key function leaves every other state-key dimension untouched.
    perf._ledger_hash = ledger_fingerprint


def cache_stats() -> Dict[str, int]:
    return {"season_row_hash_cache": len(_SEASON_ROW_HASH_CACHE)}
