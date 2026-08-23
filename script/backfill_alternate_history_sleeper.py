#!/usr/bin/env python3
"""Backfill authoritative raw Sleeper history for Alternate History.

Reuses the repository's existing Sleeper previous-league-chain ingestion rather
than introducing a second API client. Writes ONLY beneath
`data/alternate_history/source_history/` so canonical production data remains
untouched.

The cache contains the raw per-season league/users/rosters/traded-picks/drafts
and completed transaction payloads needed for historical ownership, draft-order
and event replay. Completed historical NFL outcomes are not simulated here.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List

import alternate_history_engine as ah
import update_sleeper as sleeper_source


def compact_season(row: Dict[str, Any]) -> Dict[str, Any]:
    league = row.get("league") or {}
    return {
        "league": league,
        "users": row.get("users") or [],
        "rosters": row.get("rosters") or [],
        "traded_picks": row.get("traded_picks") or [],
        "drafts": row.get("drafts") or [],
        "transactions": [
            tx for tx in (row.get("transactions") or [])
            if tx.get("status") in {None, "complete", "completed"}
        ],
    }


def run() -> Path:
    history = sleeper_source.build_history()
    compact = [compact_season(x) for x in history]
    compact.sort(
        key=lambda x: int((x.get("league") or {}).get("season") or 0),
    )
    manifest = {
        "source": "Sleeper public API via existing update_sleeper.build_history",
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
    out = ah.write_isolated_json("source_history/sleeper_history.json", manifest)
    print(out)
    print(json.dumps({
        "seasons": manifest["seasons"],
        "transaction_counts": manifest["transaction_counts"],
        "draft_counts": manifest["draft_counts"],
    }, indent=2, sort_keys=True))
    return out


if __name__ == "__main__":
    run()
