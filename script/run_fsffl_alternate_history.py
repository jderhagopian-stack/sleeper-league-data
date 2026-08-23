#!/usr/bin/env python3
"""FSFFL profile runner for Fantasy Alternate History Engine.

Source precedence:
1. isolated raw Sleeper historical cache under data/alternate_history/source_history;
2. current-season canonical transactions.json;
3. derived acquisition/trade ledgers only as gap-filling fallback.

The core remains league-agnostic and completed NFL history remains immutable.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, List, Tuple

import alternate_history_engine as ah


class FSFFLHistoricalAdapter(ah.SleeperJsonAdapter):
    def __init__(self, data_dir: Path = ah.DATA):
        super().__init__(data_dir=data_dir, profile_name="fsffl")
        self.acquisition_ledger = ah.load_json(self.data_dir / "acquisition_ledger.json", []) or []
        self.trade_ledger = ah.load_json(self.data_dir / "trade_ledger.json", []) or []
        self.raw_history_manifest = ah.load_json(
            self.data_dir / "alternate_history" / "source_history" / "sleeper_history.json",
            {},
        ) or {}

    @staticmethod
    def _pick_tuple(row: Dict[str, Any]) -> Tuple[str, str, str]:
        return (
            str(row.get("season") or ""),
            str(row.get("round") or ""),
            str(row.get("original_roster_id") or row.get("roster_id") or ""),
        )

    def _normalize_acquisition(self, row: Dict[str, Any]) -> Dict[str, Any]:
        rid = str(row.get("roster_id"))
        return {
            "transaction_id": str(row.get("transaction_id")),
            "created": int(row.get("created") or 0),
            "type": str(row.get("type") or "free_agent"),
            "status": str(row.get("status") or "complete"),
            "roster_ids": [rid],
            "adds": {
                str(p.get("player_id")): int(rid)
                for p in (row.get("players_added") or [])
                if p.get("player_id") is not None
            } or None,
            "drops": {
                str(p.get("player_id")): int(rid)
                for p in (row.get("players_dropped") or [])
                if p.get("player_id") is not None
            } or None,
            "draft_picks": [],
            "waiver_budget": [],
            "source": "acquisition_ledger",
        }

    def _normalize_trade(self, row: Dict[str, Any]) -> Dict[str, Any]:
        adds: Dict[str, int] = {}
        drops: Dict[str, int] = {}
        roster_ids: List[int] = []
        sent_pick_owner: Dict[Tuple[str, str, str], int] = {}

        for side in row.get("sides") or []:
            rid = int(side.get("roster_id"))
            roster_ids.append(rid)
            for p in side.get("sent_players") or []:
                if p.get("player_id") is not None:
                    drops[str(p.get("player_id"))] = rid
            for p in side.get("received_players") or []:
                if p.get("player_id") is not None:
                    adds[str(p.get("player_id"))] = rid
            for p in side.get("sent_picks") or []:
                sent_pick_owner[self._pick_tuple(p)] = rid

        draft_picks = []
        for side in row.get("sides") or []:
            receiver = int(side.get("roster_id"))
            for p in side.get("received_picks") or []:
                key = self._pick_tuple(p)
                previous = sent_pick_owner.get(key)
                draft_picks.append(
                    {
                        "season": str(p.get("season")),
                        "round": int(p.get("round")),
                        "roster_id": int(p.get("original_roster_id")),
                        "owner_id": receiver,
                        "previous_owner_id": previous,
                    }
                )

        waiver_budget = []
        sides = row.get("sides") or []
        if len(sides) == 2:
            a, b = sides
            a_sent = float(a.get("faab_sent") or 0)
            b_sent = float(b.get("faab_sent") or 0)
            if a_sent > 0:
                waiver_budget.append({"amount": a_sent, "sender": int(a["roster_id"]), "receiver": int(b["roster_id"])})
            if b_sent > 0:
                waiver_budget.append({"amount": b_sent, "sender": int(b["roster_id"]), "receiver": int(a["roster_id"])})

        return {
            "transaction_id": str(row.get("transaction_id")),
            "created": int(row.get("created") or 0),
            "type": "trade",
            "status": str(row.get("status") or "complete"),
            "roster_ids": roster_ids,
            "adds": adds or None,
            "drops": drops or None,
            "draft_picks": draft_picks,
            "waiver_budget": waiver_budget,
            "source": "trade_ledger",
        }

    def raw_history_seasons(self) -> List[Dict[str, Any]]:
        rows = self.raw_history_manifest.get("history") or []
        return rows if isinstance(rows, list) else []

    def completed_events(self) -> List[Dict[str, Any]]:
        # Deduplicate by transaction ID. Raw authoritative history wins over
        # current-season and derived fallback sources.
        by_id: Dict[str, Dict[str, Any]] = {}

        for season_data in self.raw_history_seasons():
            season = str((season_data.get("league") or {}).get("season") or "unknown")
            for raw in season_data.get("transactions") or []:
                if raw.get("status") not in {None, "complete", "completed"}:
                    continue
                txid = str(raw.get("transaction_id") or "")
                if txid:
                    by_id[txid] = dict(raw, source="raw_sleeper_history", source_season=season)

        for event in self.transactions:
            if event.get("status") == "complete":
                txid = str(event.get("transaction_id"))
                by_id.setdefault(txid, dict(event, source="transactions_json"))

        for row in self.acquisition_ledger:
            if str(row.get("status") or "complete") != "complete":
                continue
            event = self._normalize_acquisition(row)
            by_id.setdefault(str(event["transaction_id"]), event)

        for row in self.trade_ledger:
            if str(row.get("status") or "complete") != "complete":
                continue
            event = self._normalize_trade(row)
            by_id.setdefault(str(event["transaction_id"]), event)

        return sorted(by_id.values(), key=lambda x: int(x.get("created") or 0))


def _mark_history_confidence(manifest: Dict[str, Any], adapter: FSFFLHistoricalAdapter) -> None:
    fork_ms = int((manifest.get("scenario") or {}).get("fork_timestamp_ms") or 0)
    has_raw = bool(adapter.raw_history_seasons())
    if fork_ms < 1767225600000:
        for key in ("historical_state", "alternate_state_at_fork"):
            reconstruction = (manifest.get(key) or {}).setdefault("reconstruction", {})
            if has_raw:
                reconstruction["validation_status"] = "RAW_SLEEPER_HISTORY_AVAILABLE"
                reconstruction["confidence"] = "high"
                reconstruction["confidence_note"] = (
                    "Pre-current ownership replay prefers raw Sleeper completed transactions from the linked league-season chain."
                )
            else:
                reconstruction["validation_status"] = "PROVISIONAL_DERIVED_HISTORY_FALLBACK"
                reconstruction["confidence"] = "medium"
                reconstruction["ownership_coverage"] = min(float(reconstruction.get("ownership_coverage") or 1.0), 0.85)
                reconstruction["confidence_note"] = (
                    "Raw Sleeper historical cache unavailable; pre-current ownership uses merged derived ledgers and must expose reconstruction gaps."
                )


def run(path: Path) -> Path:
    payload = ah.load_json(path, {}) or {}
    adapter = FSFFLHistoricalAdapter()
    scenario = ah.scenario_from_json(adapter, payload)
    manifest = ah.build_manifest(adapter, scenario)
    _mark_history_confidence(manifest, adapter)
    has_raw = bool(adapter.raw_history_seasons())
    manifest["adapter"] = {
        "name": "FSFFLHistoricalAdapter",
        "profile": "fsffl",
        "event_sources": (
            ["alternate_history/source_history/sleeper_history.json", "transactions.json", "acquisition_ledger.json", "trade_ledger.json"]
            if has_raw
            else ["transactions.json", "acquisition_ledger.json", "trade_ledger.json"]
        ),
        "raw_historical_cache_available": has_raw,
    }
    out = ah.write_isolated_json(f"results/{scenario.scenario_id}/manifest.json", manifest)
    ah.write_isolated_json(f"cache/{scenario.scenario_id}/fork_state.json", manifest["alternate_state_at_fork"])
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Run FSFFL alternate-history scenario")
    parser.add_argument("scenario", type=Path)
    args = parser.parse_args()
    print(run(args.scenario))


if __name__ == "__main__":
    main()
