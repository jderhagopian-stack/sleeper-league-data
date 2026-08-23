#!/usr/bin/env python3
"""Backfill authoritative completed-season Sleeper history for Alternate History.

Reuses the repository's existing Sleeper previous-league-chain ingestion. Writes
ONLY beneath `data/alternate_history/source_history/`.

Performance rule: only completed seasons are persisted in this cache. The active
season remains sourced from canonical current Sleeper artifacts, so the cached
history is immutable and safe to reuse across runs without repeated API calls.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict

import alternate_history_engine as ah
import update_sleeper as sleeper_source

CACHE_REL = "source_history/sleeper_history.json"
CACHE_PATH = ah.AH_ROOT / CACHE_REL


def compact_season(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "league": row.get("league") or {},
        "users": row.get("users") or [],
        "rosters": row.get("rosters") or [],
        "traded_picks": row.get("traded_picks") or [],
        "drafts": row.get("drafts") or [],
        "transactions": [
            tx for tx in (row.get("transactions") or [])
            if tx.get("status") in {None, "complete", "completed"}
        ],
    }


def active_season() -> int:
    league = ah.load_json(ah.DATA / "league.json", {}) or {}
    return int(league.get("season") or 0)


def existing_cache_is_complete(cache: Dict[str, Any], current: int) -> bool:
    expected = {str(year) for year in range(2022, current)}
    observed = {str(x) for x in (cache.get("seasons") or [])}
    return bool(expected) and expected.issubset(observed) and bool(cache.get("history"))


def run(force: bool = False) -> Path:
    current = active_season()
    existing = ah.load_json(CACHE_PATH, {}) or {}
    if not force and existing_cache_is_complete(existing, current):
        print(CACHE_PATH)
        print(json.dumps({
            "cache_status": "HIT",
            "seasons": existing.get("seasons") or [],
            "transaction_counts": existing.get("transaction_counts") or {},
            "draft_counts": existing.get("draft_counts") or {},
        }, indent=2, sort_keys=True))
        return CACHE_PATH

    history = sleeper_source.build_history()
    compact = [
        compact_season(x)
        for x in history
        if int((x.get("league") or {}).get("season") or 0) < current
    ]
    compact.sort(key=lambda x: int((x.get("league") or {}).get("season") or 0))
    manifest = {
        "source": "Sleeper public API via existing update_sleeper.build_history",
        "cache_scope": "completed_seasons_only",
        "active_season_excluded": str(current),
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "starting_league_id": sleeper_source.STARTING_LEAGUE_ID,
        "seasons": [str((x.get("league") or {}).get("season")) for x in compact],
        "league_ids": [str((x.get("league") or {}).get("league_id")) for x in compact],
        "transaction_counts": {
            str((x.get("league") or {}).get("season")): len(x.get("transactions") or [])
            for x in compact
        },
        "draft_counts": {
            str((x.get("league") or {}).get("season")): len(x.get("drafts") or [])
            for x in compact
        },
        "history": compact,
    }
    out = ah.write_isolated_json(CACHE_REL, manifest)
    print(out)
    print(json.dumps({
        "cache_status": "MISS_FILLED",
        "seasons": manifest["seasons"],
        "transaction_counts": manifest["transaction_counts"],
        "draft_counts": manifest["draft_counts"],
    }, indent=2, sort_keys=True))
    return out


if __name__ == "__main__":
    run()
