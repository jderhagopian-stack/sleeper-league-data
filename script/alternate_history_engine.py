#!/usr/bin/env python3
"""Fantasy Alternate History Engine 0.1.

Read-only counterfactual replay framework.

Design invariants:
1. Completed real-world NFL outcomes are immutable. Counterfactuals only alter
   fantasy-league ownership, lineups, standings, draft capital and decisions.
2. The core is league-agnostic. League/platform assumptions live in adapters.
3. Canonical inputs under data/ are read-only. All generated artifacts are
   restricted to data/alternate_history/.
4. Historical reconstruction reports coverage/uncertainty instead of silently
   inventing unsupported state.

0.1 capabilities:
- reconstruct historical player ownership by reversing completed Sleeper
  transactions from the current roster state;
- reconstruct traded-pick ownership from Sleeper draft_picks transaction data;
- apply declarative player add/drop counterfactual forks;
- compute affected-event dependencies for incremental replay;
- cache scenario state in the isolated alternate_history namespace;
- provide hooks for Simulator 1.0 and GM 3.0 without modifying either model.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Protocol, Set, Tuple

DATA = Path("data")
AH_ROOT = DATA / "alternate_history"
MODEL_VERSION = "Fantasy-Alternate-History-0.1"


class AlternateHistoryError(RuntimeError):
    pass


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_isolated_json(relative_path: str, payload: Any) -> Path:
    """Write only beneath data/alternate_history/."""
    target = (AH_ROOT / relative_path).resolve()
    root = AH_ROOT.resolve()
    if root != target and root not in target.parents:
        raise AlternateHistoryError(f"Refusing non-isolated write: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return target


def stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class LeagueState:
    league_key: str
    timestamp_ms: int
    roster_players: Dict[str, Set[str]]
    roster_taxi: Dict[str, Set[str]] = field(default_factory=dict)
    roster_reserve: Dict[str, Set[str]] = field(default_factory=dict)
    pick_owners: Dict[str, str] = field(default_factory=dict)
    faab: Dict[str, float] = field(default_factory=dict)
    reconstruction: Dict[str, Any] = field(default_factory=dict)

    def serializable(self) -> Dict[str, Any]:
        return {
            "league_key": self.league_key,
            "timestamp_ms": self.timestamp_ms,
            "roster_players": {k: sorted(v) for k, v in self.roster_players.items()},
            "roster_taxi": {k: sorted(v) for k, v in self.roster_taxi.items()},
            "roster_reserve": {k: sorted(v) for k, v in self.roster_reserve.items()},
            "pick_owners": dict(sorted(self.pick_owners.items())),
            "faab": self.faab,
            "reconstruction": self.reconstruction,
        }


@dataclass(frozen=True)
class ForkAction:
    action_type: str
    roster_id: str
    add_player_id: Optional[str] = None
    drop_player_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Scenario:
    scenario_id: str
    league_profile: str
    fork_timestamp_ms: int
    focus_roster_id: str
    actions: List[ForkAction]
    title: str = ""
    notes: str = ""


class LeagueAdapter(Protocol):
    """Minimal contract required by the league-agnostic engine."""

    profile_name: str

    def current_state(self) -> LeagueState: ...
    def completed_events(self) -> List[Dict[str, Any]]: ...
    def player_id(self, name_or_id: str) -> str: ...
    def roster_id_for_owner(self, owner: str) -> Optional[str]: ...
    def league_rules(self) -> Dict[str, Any]: ...


class SleeperJsonAdapter:
    """Generic Sleeper adapter backed by canonical JSON files.

    Despite the profile name passed by the caller, this class contains no
    FSFFL-specific lineup/scoring assumptions. Those are read from league.json.
    """

    def __init__(self, data_dir: Path = DATA, profile_name: str = "sleeper"):
        self.data_dir = Path(data_dir)
        self.profile_name = profile_name
        self.league = load_json(self.data_dir / "league.json", {}) or {}
        self.rosters = load_json(self.data_dir / "rosters.json", []) or []
        self.transactions = load_json(self.data_dir / "transactions.json", []) or []
        self.users = load_json(self.data_dir / "users.json", []) or []
        self.players = load_json(self.data_dir / "players.json", {}) or {}
        self._player_name_index = self._build_player_name_index()
        self._owner_index = self._build_owner_index()

    def _build_player_name_index(self) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for pid, row in self.players.items():
            names = {
                str(row.get("full_name") or "").strip(),
                f"{row.get('first_name') or ''} {row.get('last_name') or ''}".strip(),
            }
            for name in names:
                if name:
                    out[name.casefold()] = str(pid)
        return out

    def _build_owner_index(self) -> Dict[str, str]:
        out: Dict[str, str] = {}
        user_by_id = {str(u.get("user_id")): u for u in self.users}
        for roster in self.rosters:
            rid = str(roster.get("roster_id"))
            uid = str(roster.get("owner_id"))
            out[uid.casefold()] = rid
            user = user_by_id.get(uid) or {}
            for key in ("display_name", "username"):
                value = str(user.get(key) or "").strip()
                if value:
                    out[value.casefold()] = rid
        return out

    def player_id(self, name_or_id: str) -> str:
        value = str(name_or_id).strip()
        if value in self.players:
            return value
        pid = self._player_name_index.get(value.casefold())
        if pid:
            return pid
        raise AlternateHistoryError(f"Unknown player: {name_or_id}")

    def roster_id_for_owner(self, owner: str) -> Optional[str]:
        return self._owner_index.get(str(owner).strip().casefold())

    def league_rules(self) -> Dict[str, Any]:
        return {
            "league_id": str(self.league.get("league_id") or ""),
            "season": str(self.league.get("season") or ""),
            "roster_positions": list(self.league.get("roster_positions") or []),
            "scoring_settings": dict(self.league.get("scoring_settings") or {}),
            "settings": dict(self.league.get("settings") or {}),
        }

    def completed_events(self) -> List[Dict[str, Any]]:
        events = [x for x in self.transactions if x.get("status") == "complete"]
        return sorted(events, key=lambda x: int(x.get("created") or 0))

    def current_state(self) -> LeagueState:
        roster_players: Dict[str, Set[str]] = {}
        roster_taxi: Dict[str, Set[str]] = {}
        roster_reserve: Dict[str, Set[str]] = {}
        faab: Dict[str, float] = {}
        latest = 0
        for r in self.rosters:
            rid = str(r.get("roster_id"))
            roster_players[rid] = {str(x) for x in (r.get("players") or [])}
            roster_taxi[rid] = {str(x) for x in (r.get("taxi") or [])}
            roster_reserve[rid] = {str(x) for x in (r.get("reserve") or [])}
            settings = r.get("settings") or {}
            faab[rid] = float(settings.get("waiver_budget_used") or 0.0)
        for event in self.completed_events():
            latest = max(latest, int(event.get("created") or 0))

        # Current owner for every pick observed in the transaction history.
        # Untraded picks need not be enumerated to reconstruct ownership changes;
        # a future adapter can expand the full draft-capital inventory.
        pick_owners: Dict[str, str] = {}
        for event in self.completed_events():
            for p in event.get("draft_picks") or []:
                key = pick_key(p)
                if key:
                    pick_owners[key] = str(p.get("owner_id"))

        return LeagueState(
            league_key=str(self.league.get("league_id") or self.profile_name),
            timestamp_ms=latest,
            roster_players=roster_players,
            roster_taxi=roster_taxi,
            roster_reserve=roster_reserve,
            pick_owners=pick_owners,
            faab=faab,
            reconstruction={"source": "canonical_current_state", "coverage": 1.0},
        )


def pick_key(pick: Dict[str, Any]) -> Optional[str]:
    season = pick.get("season")
    round_ = pick.get("round")
    original = pick.get("roster_id")
    if season is None or round_ is None or original is None:
        return None
    return f"pick:{season}:R{round_}:orig{original}"


def _ensure_roster(state: LeagueState, rid: str) -> Set[str]:
    return state.roster_players.setdefault(str(rid), set())


def reverse_event(state: LeagueState, event: Dict[str, Any]) -> Dict[str, int]:
    """Reverse one completed Sleeper transaction in place."""
    changes = {"player_moves": 0, "pick_moves": 0, "faab_moves": 0}

    # To reverse: remove players that the transaction added, then restore drops.
    for pid, rid in (event.get("adds") or {}).items():
        _ensure_roster(state, str(rid)).discard(str(pid))
        changes["player_moves"] += 1
    for pid, rid in (event.get("drops") or {}).items():
        _ensure_roster(state, str(rid)).add(str(pid))
        changes["player_moves"] += 1

    for p in event.get("draft_picks") or []:
        key = pick_key(p)
        previous = p.get("previous_owner_id")
        if key and previous is not None:
            state.pick_owners[key] = str(previous)
            changes["pick_moves"] += 1

    # Sleeper waiver_budget rows describe amount transferred sender -> receiver.
    # Reversing subtracts from receiver and restores to sender. We preserve the
    # ledger dimension generically even though some leagues do not use FAAB.
    for row in event.get("waiver_budget") or []:
        amount = float(row.get("amount") or 0.0)
        sender, receiver = str(row.get("sender")), str(row.get("receiver"))
        state.faab[receiver] = float(state.faab.get(receiver, 0.0)) - amount
        state.faab[sender] = float(state.faab.get(sender, 0.0)) + amount
        changes["faab_moves"] += 1

    return changes


def reconstruct_state(adapter: LeagueAdapter, timestamp_ms: int) -> LeagueState:
    """Rewind from canonical current state to immediately before timestamp_ms."""
    state = copy.deepcopy(adapter.current_state())
    events = adapter.completed_events()
    reversed_count = 0
    player_moves = pick_moves = faab_moves = 0
    unsupported: Dict[str, int] = {}

    for event in reversed(events):
        created = int(event.get("created") or 0)
        if created < int(timestamp_ms):
            break
        event_type = str(event.get("type") or "unknown")
        if event_type not in {"trade", "waiver", "free_agent"}:
            unsupported[event_type] = unsupported.get(event_type, 0) + 1
        counts = reverse_event(state, event)
        reversed_count += 1
        player_moves += counts["player_moves"]
        pick_moves += counts["pick_moves"]
        faab_moves += counts["faab_moves"]

    state.timestamp_ms = int(timestamp_ms)
    # Transaction coverage is exact for ownership changes present in the local
    # Sleeper feed. Taxi/reserve slot placement is preserved only when snapshots
    # exist, so 0.1 reports that limitation explicitly.
    coverage = 1.0 if not unsupported else max(0.0, 1.0 - 0.05 * len(unsupported))
    state.reconstruction = {
        "source": "reverse_completed_sleeper_transactions",
        "reversed_events": reversed_count,
        "player_moves_reversed": player_moves,
        "pick_moves_reversed": pick_moves,
        "faab_moves_reversed": faab_moves,
        "unsupported_event_types": unsupported,
        "ownership_coverage": round(coverage, 3),
        "known_limitation": "taxi/reserve historical slot placement requires archived snapshots",
    }
    return state


def apply_fork(state: LeagueState, scenario: Scenario) -> LeagueState:
    out = copy.deepcopy(state)
    for action in scenario.actions:
        if action.action_type != "player_swap":
            raise AlternateHistoryError(f"Unsupported 0.1 fork action: {action.action_type}")
        roster = _ensure_roster(out, action.roster_id)
        if action.drop_player_id:
            roster.discard(action.drop_player_id)
        if action.add_player_id:
            # A player can have only one fantasy owner. Remove from every roster
            # before assigning the counterfactual owner.
            for rid, players in out.roster_players.items():
                if rid != action.roster_id:
                    players.discard(action.add_player_id)
            roster.add(action.add_player_id)
    out.reconstruction = dict(out.reconstruction)
    out.reconstruction["counterfactual_fork_applied"] = scenario.scenario_id
    return out


def dependency_events(adapter: LeagueAdapter, scenario: Scenario) -> List[Dict[str, Any]]:
    """Return downstream events that touch assets/rosters changed at the fork.

    This is the first pruning layer: unaffected events remain historical facts
    and do not need expensive reevaluation.
    """
    dirty_players: Set[str] = set()
    dirty_rosters: Set[str] = {scenario.focus_roster_id}
    for a in scenario.actions:
        dirty_rosters.add(a.roster_id)
        if a.add_player_id:
            dirty_players.add(a.add_player_id)
        if a.drop_player_id:
            dirty_players.add(a.drop_player_id)

    affected = []
    for event in adapter.completed_events():
        if int(event.get("created") or 0) < scenario.fork_timestamp_ms:
            continue
        roster_ids = {str(x) for x in (event.get("roster_ids") or [])}
        asset_ids = {str(x) for x in (event.get("adds") or {}).keys()}
        asset_ids |= {str(x) for x in (event.get("drops") or {}).keys()}
        if roster_ids & dirty_rosters or asset_ids & dirty_players:
            affected.append(event)
            dirty_rosters |= roster_ids
            dirty_players |= asset_ids
    return affected


def scenario_from_json(adapter: LeagueAdapter, payload: Dict[str, Any]) -> Scenario:
    owner = str(payload.get("focus_owner") or payload.get("focus_roster_id") or "")
    rid = str(payload.get("focus_roster_id") or adapter.roster_id_for_owner(owner) or "")
    if not rid:
        raise AlternateHistoryError(f"Unable to resolve focus owner/roster: {owner}")

    actions: List[ForkAction] = []
    for raw in payload.get("actions") or []:
        action_type = str(raw.get("type") or "player_swap")
        action_rid = str(raw.get("roster_id") or rid)
        add = raw.get("add_player") or raw.get("add_player_id")
        drop = raw.get("drop_player") or raw.get("drop_player_id")
        actions.append(
            ForkAction(
                action_type=action_type,
                roster_id=action_rid,
                add_player_id=adapter.player_id(str(add)) if add else None,
                drop_player_id=adapter.player_id(str(drop)) if drop else None,
                metadata=dict(raw.get("metadata") or {}),
            )
        )

    base_for_id = {
        "league_profile": payload.get("league_profile") or adapter.profile_name,
        "fork_timestamp_ms": int(payload["fork_timestamp_ms"]),
        "focus_roster_id": rid,
        "actions": [asdict(x) for x in actions],
    }
    scenario_id = str(payload.get("scenario_id") or stable_hash(base_for_id)[:16])
    return Scenario(
        scenario_id=scenario_id,
        league_profile=str(payload.get("league_profile") or adapter.profile_name),
        fork_timestamp_ms=int(payload["fork_timestamp_ms"]),
        focus_roster_id=rid,
        actions=actions,
        title=str(payload.get("title") or scenario_id),
        notes=str(payload.get("notes") or ""),
    )


def build_manifest(adapter: LeagueAdapter, scenario: Scenario) -> Dict[str, Any]:
    historical = reconstruct_state(adapter, scenario.fork_timestamp_ms)
    alternate = apply_fork(historical, scenario)
    affected = dependency_events(adapter, scenario)
    historical_serial = historical.serializable()
    alternate_serial = alternate.serializable()

    return {
        "model_version": MODEL_VERSION,
        "scenario": {
            "scenario_id": scenario.scenario_id,
            "title": scenario.title,
            "league_profile": scenario.league_profile,
            "fork_timestamp_ms": scenario.fork_timestamp_ms,
            "focus_roster_id": scenario.focus_roster_id,
            "actions": [asdict(x) for x in scenario.actions],
            "notes": scenario.notes,
        },
        "design_invariants": {
            "completed_nfl_history_is_immutable": True,
            "fantasy_league_state_is_counterfactual": True,
            "league_agnostic_core": True,
            "canonical_data_is_read_only": True,
        },
        "league_rules_hash": stable_hash(adapter.league_rules()),
        "historical_state_hash": stable_hash(historical_serial),
        "alternate_state_hash": stable_hash(alternate_serial),
        "historical_state": historical_serial,
        "alternate_state_at_fork": alternate_serial,
        "dependency_summary": {
            "affected_downstream_events": len(affected),
            "affected_transaction_ids": [str(x.get("transaction_id")) for x in affected],
        },
        "next_stage_hooks": {
            "completed_season_replay": "use actual historical NFL fantasy points; do not resimulate NFL outcomes",
            "gm_policy": "GM 3.0 evaluates only affected/conditional downstream decisions",
            "current_future_simulation": "Simulator 1.0 runs only after the replay reaches the current/future boundary",
            "branching": "screen -> prune -> cluster -> confirm high-impact branches",
        },
    }


def run_scenario(path: Path, profile: str = "fsffl") -> Path:
    payload = load_json(path, {}) or {}
    adapter = SleeperJsonAdapter(DATA, profile_name=profile)
    scenario = scenario_from_json(adapter, payload)
    manifest = build_manifest(adapter, scenario)
    out = write_isolated_json(f"results/{scenario.scenario_id}/manifest.json", manifest)
    # Cache fork state separately so later narrative/report requests can avoid
    # replaying the full transaction history.
    write_isolated_json(
        f"cache/{scenario.scenario_id}/fork_state.json",
        manifest["alternate_state_at_fork"],
    )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Fantasy Alternate History Engine 0.1")
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--profile", default="fsffl")
    args = parser.parse_args()
    path = run_scenario(args.scenario, args.profile)
    print(path)


if __name__ == "__main__":
    main()
