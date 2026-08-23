#!/usr/bin/env python3
"""FSFFL profile runner for Fantasy Alternate History Engine 0.1.

This adapter merges the current-season Sleeper transaction feed with the
repo's historical acquisition/trade ledgers so the league-agnostic core can
rewind across seasons. It does not change NFL outcomes or canonical data.
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
        # trade_ledger exposes FAAB sent/received totals per side. Derive a
        # bilateral transfer only where it is unambiguous.
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

    def completed_events(self) -> List[Dict[str, Any]]:
        # Prefer raw Sleeper events where available; fill historical gaps from
        # derived ledgers. Deduplication is transaction-id based.
        by_id: Dict[str, Dict[str, Any]] = {}
        for event in self.transactions:
            if event.get("status") == "complete":
                by_id[str(event.get("transaction_id"))] = dict(event, source="transactions_json")

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


def run(path: Path) -> Path:
    payload = ah.load_json(path, {}) or {}
    adapter = FSFFLHistoricalAdapter()
    scenario = ah.scenario_from_json(adapter, payload)
    manifest = ah.build_manifest(adapter, scenario)
    manifest["adapter"] = {
        "name": "FSFFLHistoricalAdapter",
        "profile": "fsffl",
        "event_sources": ["transactions.json", "acquisition_ledger.json", "trade_ledger.json"],
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
