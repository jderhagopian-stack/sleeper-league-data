import json
import os
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

STARTING_LEAGUE_ID = "1312071960615731200"
BASE_URL = "https://api.sleeper.app/v1"
OUTPUT_DIR = Path("data")
MAX_HISTORY_SEASONS = 10
TARGET_USERNAME = "jimmygoodjob"


def get_json(url):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "sleeper-league-data/1.0"},
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def sleeper(path):
    url = f"{BASE_URL}{path}"
    print(f"Fetching {url}")
    return get_json(url)


def collect_player_ids(obj, found=None):
    if found is None:
        found = set()

    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in {"players", "starters", "reserve", "taxi"} and isinstance(value, list):
                for item in value:
                    if item is not None:
                        found.add(str(item))

            elif key in {"adds", "drops"} and isinstance(value, dict):
                for player_id in value.keys():
                    found.add(str(player_id))

            elif key == "player_id" and value is not None:
                found.add(str(value))

            collect_player_ids(value, found)

    elif isinstance(obj, list):
        for item in obj:
            collect_player_ids(item, found)

    return found


def fetch_transactions(league_id):
    transactions = []

    for round_number in range(0, 19):
        try:
            result = sleeper(
                f"/league/{league_id}/transactions/{round_number}"
            )

            if result:
                transactions.extend(result)

        except Exception as exc:
            print(
                f"Transaction round {round_number} unavailable "
                f"for {league_id}: {exc}"
            )

        time.sleep(0.05)

    unique = {}

    for transaction in transactions:
        transaction_id = transaction.get("transaction_id")

        if transaction_id:
            unique[transaction_id] = transaction
        else:
            key = json.dumps(transaction, sort_keys=True)
            unique[key] = transaction

    return sorted(
        unique.values(),
        key=lambda x: x.get("created", 0),
        reverse=True,
    )


def fetch_drafts(league_id):
    drafts = sleeper(f"/league/{league_id}/drafts")

    results = []

    for draft in drafts:
        draft_id = draft.get("draft_id")

        record = {
            "draft": draft,
            "picks": [],
            "traded_picks": [],
        }

        if draft_id:
            try:
                record["picks"] = sleeper(f"/draft/{draft_id}/picks")
            except Exception as exc:
                print(f"Could not fetch picks for draft {draft_id}: {exc}")

            try:
                record["traded_picks"] = sleeper(
                    f"/draft/{draft_id}/traded_picks"
                )
            except Exception as exc:
                print(
                    f"Could not fetch traded picks for draft "
                    f"{draft_id}: {exc}"
                )

        results.append(record)

    return results


def fetch_league_season(league_id):
    league = sleeper(f"/league/{league_id}")

    return {
        "league": league,
        "users": sleeper(f"/league/{league_id}/users"),
        "rosters": sleeper(f"/league/{league_id}/rosters"),
        "traded_picks": sleeper(
            f"/league/{league_id}/traded_picks"
        ),
        "drafts": fetch_drafts(league_id),
        "transactions": fetch_transactions(league_id),
    }


def build_history():
    history = []
    seen = set()
    league_id = STARTING_LEAGUE_ID

    while league_id and league_id != "0":
        if league_id in seen:
            break

        if len(history) >= MAX_HISTORY_SEASONS:
            break

        seen.add(league_id)

        print(f"\nCollecting league season {league_id}")
        season_data = fetch_league_season(league_id)
        history.append(season_data)

        previous = season_data["league"].get(
            "previous_league_id"
        )

        if not previous or previous == league_id:
            break

        league_id = str(previous)

    return history


def build_compact_player_map(history):
    player_ids = collect_player_ids(history)

    print(
        f"\nNeed metadata for {len(player_ids)} "
        f"players/assets"
    )

    print("Fetching Sleeper NFL player database")
    all_players = sleeper("/players/nfl")

    compact = {}

    for player_id in sorted(player_ids):
        player = all_players.get(player_id)

        if not player:
            compact[player_id] = {
                "player_id": player_id,
                "full_name": player_id,
            }
            continue

        full_name = player.get("full_name")

        if not full_name:
            first = player.get("first_name") or ""
            last = player.get("last_name") or ""
            full_name = f"{first} {last}".strip()

        compact[player_id] = {
            "player_id": player_id,
            "full_name": full_name,
            "first_name": player.get("first_name"),
            "last_name": player.get("last_name"),
            "position": player.get("position"),
            "team": player.get("team"),
            "age": player.get("age"),
            "years_exp": player.get("years_exp"),
            "status": player.get("status"),
            "active": player.get("active"),
            "injury_status": player.get("injury_status"),
            "fantasy_positions": player.get(
                "fantasy_positions"
            ),
        }

    return compact


def build_trade_analytics(history, players):
    ledger = []
    owner_stats = defaultdict(lambda: {
        "total_trades": 0,
        "seasons": Counter(),
        "partners": Counter(),
        "players_acquired": 0,
        "players_sent": 0,
        "picks_acquired": 0,
        "picks_sent": 0,
        "faab_acquired": 0,
        "faab_sent": 0,
    })
    owner_directory = {}
    pair_counts = Counter()
    target_user_id = None

    for season_data in history:
        league = season_data.get("league", {})
        league_id = str(league.get("league_id") or "")
        season = str(league.get("season") or "unknown")
        users = season_data.get("users", [])
        rosters = season_data.get("rosters", [])

        users_by_id = {
            str(user.get("user_id")): user
            for user in users
            if user.get("user_id") is not None
        }
        roster_to_user = {}

        for roster in rosters:
            roster_id = roster.get("roster_id")
            owner_id = roster.get("owner_id")
            if roster_id is not None and owner_id is not None:
                roster_to_user[str(roster_id)] = str(owner_id)

        for user_id, user in users_by_id.items():
            display_name = user.get("display_name") or user.get("username") or user_id
            username = user.get("username") or user.get("display_name") or user_id
            metadata = user.get("metadata") or {}
            team_name = metadata.get("team_name") or display_name

            if user_id not in owner_directory:
                owner_directory[user_id] = {
                    "user_id": user_id,
                    "username": username,
                    "display_name": display_name,
                    "team_name": team_name,
                    "aliases": [],
                }

            aliases = owner_directory[user_id]["aliases"]
            for alias in [username, display_name, team_name]:
                if alias and alias not in aliases:
                    aliases.append(alias)

            if str(username).lower() == TARGET_USERNAME.lower():
                target_user_id = user_id

        for transaction in season_data.get("transactions", []):
            if transaction.get("type") != "trade":
                continue
            if transaction.get("status") not in {None, "complete", "completed"}:
                continue

            roster_ids = [
                str(roster_id)
                for roster_id in (transaction.get("roster_ids") or [])
            ]
            participant_user_ids = [
                roster_to_user.get(roster_id)
                for roster_id in roster_ids
                if roster_to_user.get(roster_id)
            ]

            adds = transaction.get("adds") or {}
            drops = transaction.get("drops") or {}
            draft_picks = transaction.get("draft_picks") or []
            waiver_budget = transaction.get("waiver_budget") or []

            sides = {}
            for roster_id in roster_ids:
                user_id = roster_to_user.get(roster_id)
                user = owner_directory.get(user_id, {})
                sides[roster_id] = {
                    "roster_id": roster_id,
                    "user_id": user_id,
                    "manager": user.get("display_name") or user_id or roster_id,
                    "team_name": user.get("team_name"),
                    "received_players": [],
                    "sent_players": [],
                    "received_picks": [],
                    "sent_picks": [],
                    "faab_received": 0,
                    "faab_sent": 0,
                }

            for player_id, receiving_roster in adds.items():
                receiving_roster = str(receiving_roster)
                player = players.get(str(player_id), {})
                asset = {
                    "player_id": str(player_id),
                    "name": player.get("full_name") or str(player_id),
                    "position": player.get("position"),
                }
                if receiving_roster in sides:
                    sides[receiving_roster]["received_players"].append(asset)

                sending_roster = drops.get(player_id)
                if sending_roster is not None:
                    sending_roster = str(sending_roster)
                    if sending_roster in sides:
                        sides[sending_roster]["sent_players"].append(asset)

            for pick in draft_picks:
                pick_asset = {
                    "season": str(pick.get("season")) if pick.get("season") is not None else None,
                    "round": pick.get("round"),
                    "original_roster_id": str(pick.get("roster_id")) if pick.get("roster_id") is not None else None,
                }
                receiver = pick.get("owner_id")
                sender = pick.get("previous_owner_id")
                if receiver is not None and str(receiver) in sides:
                    sides[str(receiver)]["received_picks"].append(pick_asset)
                if sender is not None and str(sender) in sides:
                    sides[str(sender)]["sent_picks"].append(pick_asset)

            for budget in waiver_budget:
                sender = budget.get("sender")
                receiver = budget.get("receiver")
                amount = budget.get("amount") or 0
                if sender is not None and str(sender) in sides:
                    sides[str(sender)]["faab_sent"] += amount
                if receiver is not None and str(receiver) in sides:
                    sides[str(receiver)]["faab_received"] += amount

            created = transaction.get("created")
            ledger_entry = {
                "transaction_id": transaction.get("transaction_id"),
                "league_id": league_id,
                "season": season,
                "created": created,
                "created_utc": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ",
                    time.gmtime(created / 1000),
                ) if created else None,
                "status": transaction.get("status"),
                "creator": transaction.get("creator"),
                "roster_ids": roster_ids,
                "participant_user_ids": participant_user_ids,
                "sides": list(sides.values()),
            }
            ledger.append(ledger_entry)

            unique_users = sorted(set(participant_user_ids))
            for user_id in unique_users:
                stats = owner_stats[user_id]
                stats["total_trades"] += 1
                stats["seasons"][season] += 1

                for partner_id in unique_users:
                    if partner_id != user_id:
                        stats["partners"][partner_id] += 1

            for i, user_a in enumerate(unique_users):
                for user_b in unique_users[i + 1:]:
                    pair_counts[(user_a, user_b)] += 1

            for side in sides.values():
                user_id = side.get("user_id")
                if not user_id:
                    continue
                stats = owner_stats[user_id]
                stats["players_acquired"] += len(side["received_players"])
                stats["players_sent"] += len(side["sent_players"])
                stats["picks_acquired"] += len(side["received_picks"])
                stats["picks_sent"] += len(side["sent_picks"])
                stats["faab_acquired"] += side["faab_received"]
                stats["faab_sent"] += side["faab_sent"]

    ledger.sort(key=lambda item: item.get("created") or 0, reverse=True)

    summary = []
    for user_id, stats in owner_stats.items():
        owner = owner_directory.get(user_id, {})
        partners = []
        for partner_id, count in stats["partners"].most_common():
            partner = owner_directory.get(partner_id, {})
            partners.append({
                "user_id": partner_id,
                "manager": partner.get("display_name") or partner_id,
                "team_name": partner.get("team_name"),
                "trades_together": count,
            })

        summary.append({
            "user_id": user_id,
            "username": owner.get("username"),
            "manager": owner.get("display_name") or user_id,
            "team_name": owner.get("team_name"),
            "aliases": owner.get("aliases", []),
            "total_trades": stats["total_trades"],
            "trades_by_season": dict(sorted(stats["seasons"].items())),
            "unique_trade_partners": len(stats["partners"]),
            "trade_partners": partners,
            "players_acquired": stats["players_acquired"],
            "players_sent": stats["players_sent"],
            "picks_acquired": stats["picks_acquired"],
            "picks_sent": stats["picks_sent"],
            "faab_acquired": stats["faab_acquired"],
            "faab_sent": stats["faab_sent"],
        })

    summary.sort(key=lambda item: (-item["total_trades"], item["manager"]))
    for rank, owner in enumerate(summary, start=1):
        owner["trade_activity_rank"] = rank

    pairs = []
    for (user_a, user_b), count in pair_counts.most_common():
        owner_a = owner_directory.get(user_a, {})
        owner_b = owner_directory.get(user_b, {})
        pairs.append({
            "user_a_id": user_a,
            "user_a": owner_a.get("display_name") or user_a,
            "user_a_team": owner_a.get("team_name"),
            "user_b_id": user_b,
            "user_b": owner_b.get("display_name") or user_b,
            "user_b_team": owner_b.get("team_name"),
            "trades_together": count,
        })

    target_history = []
    if target_user_id:
        target_history = [
            trade for trade in ledger
            if target_user_id in trade.get("participant_user_ids", [])
        ]

    return {
        "trade_ledger": ledger,
        "owner_trade_summary": summary,
        "trade_pairs": pairs,
        "target_user_id": target_user_id,
        "target_trade_history": target_history,
    }



def build_owner_directory(history):
    owners = {}
    season_rosters = {}

    for season_data in history:
        league = season_data.get("league", {})
        season = str(league.get("season") or "unknown")
        roster_to_user = {}

        for roster in season_data.get("rosters", []):
            roster_id = roster.get("roster_id")
            owner_id = roster.get("owner_id")
            if roster_id is not None and owner_id is not None:
                roster_to_user[str(roster_id)] = str(owner_id)

        season_rosters[season] = roster_to_user

        for user in season_data.get("users", []):
            user_id = user.get("user_id")
            if user_id is None:
                continue
            user_id = str(user_id)
            display_name = user.get("display_name") or user.get("username") or user_id
            username = user.get("username") or user.get("display_name") or user_id
            metadata = user.get("metadata") or {}
            team_name = metadata.get("team_name") or display_name

            if user_id not in owners:
                owners[user_id] = {
                    "user_id": user_id,
                    "username": username,
                    "manager": display_name,
                    "team_name": team_name,
                    "aliases": [],
                }

            for alias in [username, display_name, team_name]:
                if alias and alias not in owners[user_id]["aliases"]:
                    owners[user_id]["aliases"].append(alias)

    return owners, season_rosters


def build_draft_analytics(history, players):
    owners, season_rosters = build_owner_directory(history)
    ledger = []
    stats = defaultdict(lambda: {
        "total_picks": 0,
        "picks_by_season": Counter(),
        "picks_by_round": Counter(),
        "positions": Counter(),
        "first_round_positions": Counter(),
        "second_round_positions": Counter(),
        "third_round_positions": Counter(),
        "pick_numbers": [],
    })

    for season_data in history:
        league = season_data.get("league", {})
        season = str(league.get("season") or "unknown")
        roster_to_user = season_rosters.get(season, {})

        for draft_record in season_data.get("drafts", []):
            draft = draft_record.get("draft") or {}
            draft_id = str(draft.get("draft_id") or "")
            draft_type = draft.get("type")
            status = draft.get("status")

            for pick in draft_record.get("picks", []):
                player_id = str(pick.get("player_id")) if pick.get("player_id") is not None else None
                roster_id = pick.get("roster_id")
                picked_by = pick.get("picked_by")

                user_id = None
                if picked_by is not None and str(picked_by) in owners:
                    user_id = str(picked_by)
                elif roster_id is not None:
                    user_id = roster_to_user.get(str(roster_id))

                player = players.get(player_id, {}) if player_id else {}
                round_number = pick.get("round")
                pick_no = pick.get("pick_no")
                draft_slot = pick.get("draft_slot")

                ledger.append({
                    "season": season,
                    "draft_id": draft_id,
                    "draft_type": draft_type,
                    "draft_status": status,
                    "pick_no": pick_no,
                    "round": round_number,
                    "draft_slot": draft_slot,
                    "roster_id": str(roster_id) if roster_id is not None else None,
                    "user_id": user_id,
                    "manager": owners.get(user_id, {}).get("manager") if user_id else None,
                    "team_name": owners.get(user_id, {}).get("team_name") if user_id else None,
                    "player_id": player_id,
                    "player_name": player.get("full_name") or player_id,
                    "position": player.get("position"),
                    "nfl_team": player.get("team"),
                })

                if not user_id:
                    continue

                s = stats[user_id]
                s["total_picks"] += 1
                s["picks_by_season"][season] += 1
                if round_number is not None:
                    s["picks_by_round"][str(round_number)] += 1
                if pick_no is not None:
                    s["pick_numbers"].append(pick_no)

                position = player.get("position") or "UNKNOWN"
                s["positions"][position] += 1
                if round_number == 1:
                    s["first_round_positions"][position] += 1
                elif round_number == 2:
                    s["second_round_positions"][position] += 1
                elif round_number == 3:
                    s["third_round_positions"][position] += 1

    ledger.sort(key=lambda x: (str(x.get("season")), x.get("pick_no") or 9999), reverse=True)

    summary = []
    for user_id, owner in owners.items():
        s = stats[user_id]
        total = s["total_picks"]
        summary.append({
            **owner,
            "total_draft_picks": total,
            "picks_by_season": dict(sorted(s["picks_by_season"].items())),
            "picks_by_round": dict(sorted(s["picks_by_round"].items())),
            "positions_drafted": dict(s["positions"].most_common()),
            "first_round_positions": dict(s["first_round_positions"].most_common()),
            "second_round_positions": dict(s["second_round_positions"].most_common()),
            "third_round_positions": dict(s["third_round_positions"].most_common()),
            "average_overall_pick": (
                round(sum(s["pick_numbers"]) / len(s["pick_numbers"]), 2)
                if s["pick_numbers"] else None
            ),
        })

    summary.sort(key=lambda x: (-x["total_draft_picks"], x["manager"]))
    return {"draft_ledger": ledger, "owner_draft_summary": summary}


def build_acquisition_analytics(history, players):
    owners, season_rosters = build_owner_directory(history)
    ledger = []
    stats = defaultdict(lambda: {
        "total_acquisitions": 0,
        "waiver_claims": 0,
        "free_agent_adds": 0,
        "adds_by_season": Counter(),
        "positions_added": Counter(),
        "faab_spent": 0,
        "faab_bids": [],
        "players_dropped": 0,
    })

    for season_data in history:
        league = season_data.get("league", {})
        season = str(league.get("season") or "unknown")
        roster_to_user = season_rosters.get(season, {})

        for transaction in season_data.get("transactions", []):
            ttype = transaction.get("type")
            status = transaction.get("status")

            if ttype not in {"waiver", "free_agent"}:
                continue
            if status not in {None, "complete", "completed"}:
                continue

            created = transaction.get("created")
            settings = transaction.get("settings") or {}
            waiver_bid = settings.get("waiver_bid")
            adds = transaction.get("adds") or {}
            drops = transaction.get("drops") or {}

            # Group adds by receiving roster so one transaction can be represented cleanly.
            by_roster = defaultdict(list)
            for player_id, roster_id in adds.items():
                by_roster[str(roster_id)].append(str(player_id))

            for roster_id, player_ids in by_roster.items():
                user_id = roster_to_user.get(roster_id)
                owner = owners.get(user_id, {})
                added_players = []

                for player_id in player_ids:
                    player = players.get(player_id, {})
                    added_players.append({
                        "player_id": player_id,
                        "name": player.get("full_name") or player_id,
                        "position": player.get("position"),
                        "nfl_team": player.get("team"),
                    })

                    if user_id:
                        s = stats[user_id]
                        s["total_acquisitions"] += 1
                        s["adds_by_season"][season] += 1
                        s["positions_added"][player.get("position") or "UNKNOWN"] += 1
                        if ttype == "waiver":
                            s["waiver_claims"] += 1
                        else:
                            s["free_agent_adds"] += 1

                dropped_players = []
                for player_id, drop_roster in drops.items():
                    if str(drop_roster) != roster_id:
                        continue
                    player = players.get(str(player_id), {})
                    dropped_players.append({
                        "player_id": str(player_id),
                        "name": player.get("full_name") or str(player_id),
                        "position": player.get("position"),
                    })
                    if user_id:
                        stats[user_id]["players_dropped"] += 1

                bid = waiver_bid if ttype == "waiver" and waiver_bid is not None else 0
                if user_id and bid:
                    stats[user_id]["faab_spent"] += bid
                    stats[user_id]["faab_bids"].append(bid)

                ledger.append({
                    "transaction_id": transaction.get("transaction_id"),
                    "season": season,
                    "created": created,
                    "created_utc": time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ",
                        time.gmtime(created / 1000),
                    ) if created else None,
                    "type": ttype,
                    "status": status,
                    "roster_id": roster_id,
                    "user_id": user_id,
                    "manager": owner.get("manager"),
                    "team_name": owner.get("team_name"),
                    "faab_bid": bid,
                    "players_added": added_players,
                    "players_dropped": dropped_players,
                })

    ledger.sort(key=lambda x: x.get("created") or 0, reverse=True)

    summary = []
    for user_id, owner in owners.items():
        s = stats[user_id]
        bids = s["faab_bids"]
        summary.append({
            **owner,
            "total_acquisitions": s["total_acquisitions"],
            "waiver_claims": s["waiver_claims"],
            "free_agent_adds": s["free_agent_adds"],
            "players_dropped": s["players_dropped"],
            "adds_by_season": dict(sorted(s["adds_by_season"].items())),
            "positions_added": dict(s["positions_added"].most_common()),
            "faab_spent_on_recorded_waiver_claims": s["faab_spent"],
            "average_faab_bid_when_positive": (
                round(sum(bids) / len(bids), 2) if bids else 0
            ),
            "largest_recorded_faab_bid": max(bids) if bids else 0,
        })

    summary.sort(key=lambda x: (-x["total_acquisitions"], x["manager"]))
    return {
        "acquisition_ledger": ledger,
        "owner_waiver_summary": summary,
    }

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    nfl_state = sleeper("/state/nfl")
    history = build_history()
    players = build_compact_player_map(history)
    trade_analytics = build_trade_analytics(history, players)
    draft_analytics = build_draft_analytics(history, players)
    acquisition_analytics = build_acquisition_analytics(history, players)

    current = history[0] if history else None

    output = {
        "generated_at_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime(),
        ),
        "source": "Sleeper public API",
        "starting_league_id": STARTING_LEAGUE_ID,
        "nfl_state": nfl_state,
        "current": current,
        "league_history": history,
        "players": players,
    }

    def write_json(filename, data):
        path = OUTPUT_DIR / filename
        with path.open("w", encoding="utf-8") as file:
            json.dump(data, file, indent=2, sort_keys=True)
        print(f"Saved {path}")

    write_json("latest.json", output)
    write_json("players.json", players)
    write_json("league_history.json", history)
    write_json("nfl_state.json", nfl_state)
    write_json("trade_ledger.json", trade_analytics["trade_ledger"])
    write_json("owner_trade_summary.json", trade_analytics["owner_trade_summary"])
    write_json("trade_pairs.json", trade_analytics["trade_pairs"])
    write_json("jimmy_trade_history.json", trade_analytics["target_trade_history"])
    write_json("draft_ledger.json", draft_analytics["draft_ledger"])
    write_json("owner_draft_summary.json", draft_analytics["owner_draft_summary"])
    write_json("acquisition_ledger.json", acquisition_analytics["acquisition_ledger"])
    write_json("owner_waiver_summary.json", acquisition_analytics["owner_waiver_summary"])

    if current:
        write_json("league.json", current.get("league", {}))
        write_json("users.json", current.get("users", []))
        write_json("rosters.json", current.get("rosters", []))
        write_json(
            "traded_picks.json",
            current.get("traded_picks", []),
        )
        write_json(
            "transactions.json",
            current.get("transactions", []),
        )
        write_json("drafts.json", current.get("drafts", []))

    print(
        f"\nLeague seasons captured: "
        f"{len(history)}"
    )

    print(
        f"Player records captured: "
        f"{len(players)}"
    )

    print(
        f"Completed trades captured: "
        f"{len(trade_analytics['trade_ledger'])}"
    )

    print(
        f"Draft picks captured: "
        f"{len(draft_analytics['draft_ledger'])}"
    )

    print(
        f"Waiver/free-agent acquisition records captured: "
        f"{len(acquisition_analytics['acquisition_ledger'])}"
    )


if __name__ == "__main__":
    main()
