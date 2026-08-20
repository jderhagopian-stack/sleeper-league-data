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
            "birth_date": player.get("birth_date"),
            "draft_year": player.get("draft_year"),
            "draft_round": player.get("draft_round"),
            "draft_pick": player.get("draft_pick"),
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


def build_advanced_owner_profiles(history, players):
    owners, season_rosters = build_owner_directory(history)

    def fresh_stats():
        return {
            "trade_total": 0,
            "trade_initiated": 0,
            "trade_seasons": Counter(),
            "recent_trades_2025_2026": 0,
            "players_in": Counter(),
            "players_out": Counter(),
            "player_positions_in": Counter(),
            "player_positions_out": Counter(),
            "picks_in": Counter(),
            "picks_out": Counter(),
            "firsts_in": 0,
            "firsts_out": 0,
            "seconds_in": 0,
            "seconds_out": 0,
            "thirds_in": 0,
            "thirds_out": 0,
            "faab_in": 0,
            "faab_out": 0,
            "one_for_one_trades": 0,
            "multi_asset_trades": 0,
            "trade_partners": Counter(),
            "rookie_picks_total": 0,
            "rookie_picks_by_season": Counter(),
            "rookie_picks_by_round": Counter(),
            "rookie_positions": Counter(),
            "rookie_first_round_positions": Counter(),
            "rookie_second_round_positions": Counter(),
            "rookie_third_round_positions": Counter(),
            "waiver_claims": 0,
            "free_agent_adds": 0,
            "waiver_positions": Counter(),
            "faab_spent": 0,
            "positive_faab_bids": [],
        }

    stats = defaultdict(fresh_stats)

    # Completed trades
    for season_data in history:
        league = season_data.get("league", {})
        season = str(league.get("season") or "unknown")
        roster_to_user = season_rosters.get(season, {})

        for transaction in season_data.get("transactions", []):
            if transaction.get("type") != "trade":
                continue
            if transaction.get("status") not in {None, "complete", "completed"}:
                continue

            roster_ids = [str(x) for x in (transaction.get("roster_ids") or [])]
            users = [roster_to_user.get(r) for r in roster_ids if roster_to_user.get(r)]
            users = list(dict.fromkeys(users))
            creator = str(transaction.get("creator")) if transaction.get("creator") is not None else None

            adds = transaction.get("adds") or {}
            drops = transaction.get("drops") or {}
            picks = transaction.get("draft_picks") or []
            waiver_budget = transaction.get("waiver_budget") or []

            # Count total assets moved on each side for complexity.
            assets_by_roster = Counter()
            for _, recv_roster in adds.items():
                assets_by_roster[str(recv_roster)] += 1
            for p in picks:
                if p.get("owner_id") is not None:
                    assets_by_roster[str(p.get("owner_id"))] += 1
            for b in waiver_budget:
                if b.get("receiver") is not None and (b.get("amount") or 0) > 0:
                    assets_by_roster[str(b.get("receiver"))] += 1

            for user_id in users:
                s = stats[user_id]
                s["trade_total"] += 1
                s["trade_seasons"][season] += 1
                if season in {"2025", "2026"}:
                    s["recent_trades_2025_2026"] += 1
                if creator == user_id:
                    s["trade_initiated"] += 1

                for partner_id in users:
                    if partner_id != user_id:
                        s["trade_partners"][partner_id] += 1

                user_rosters = [r for r in roster_ids if roster_to_user.get(r) == user_id]
                received_asset_count = sum(assets_by_roster[r] for r in user_rosters)
                if len(users) == 2 and all(assets_by_roster[r] == 1 for r in roster_ids):
                    s["one_for_one_trades"] += 1
                else:
                    s["multi_asset_trades"] += 1

            # Player directions
            for player_id, recv_roster in adds.items():
                recv_roster = str(recv_roster)
                recv_user = roster_to_user.get(recv_roster)
                player = players.get(str(player_id), {})
                name = player.get("full_name") or str(player_id)
                pos = player.get("position") or "UNKNOWN"
                if recv_user:
                    stats[recv_user]["players_in"][name] += 1
                    stats[recv_user]["player_positions_in"][pos] += 1

                send_roster = drops.get(player_id)
                if send_roster is not None:
                    send_user = roster_to_user.get(str(send_roster))
                    if send_user:
                        stats[send_user]["players_out"][name] += 1
                        stats[send_user]["player_positions_out"][pos] += 1

            # Pick directions
            for p in picks:
                rnd = p.get("round")
                season_pick = str(p.get("season")) if p.get("season") is not None else "unknown"
                label = f"{season_pick} R{rnd}" if rnd is not None else season_pick
                recv = p.get("owner_id")
                send = p.get("previous_owner_id")

                if recv is not None:
                    recv_user = roster_to_user.get(str(recv))
                    if recv_user:
                        stats[recv_user]["picks_in"][label] += 1
                        if rnd == 1: stats[recv_user]["firsts_in"] += 1
                        if rnd == 2: stats[recv_user]["seconds_in"] += 1
                        if rnd == 3: stats[recv_user]["thirds_in"] += 1

                if send is not None:
                    send_user = roster_to_user.get(str(send))
                    if send_user:
                        stats[send_user]["picks_out"][label] += 1
                        if rnd == 1: stats[send_user]["firsts_out"] += 1
                        if rnd == 2: stats[send_user]["seconds_out"] += 1
                        if rnd == 3: stats[send_user]["thirds_out"] += 1

            for b in waiver_budget:
                amount = b.get("amount") or 0
                sender = b.get("sender")
                receiver = b.get("receiver")
                if sender is not None:
                    u = roster_to_user.get(str(sender))
                    if u: stats[u]["faab_out"] += amount
                if receiver is not None:
                    u = roster_to_user.get(str(receiver))
                    if u: stats[u]["faab_in"] += amount

    # Rookie drafts only; exclude 2022 startup.
    for season_data in history:
        league = season_data.get("league", {})
        season = str(league.get("season") or "unknown")
        if season == "2022":
            continue
        roster_to_user = season_rosters.get(season, {})

        for draft_record in season_data.get("drafts", []):
            draft = draft_record.get("draft") or {}
            # This league's post-startup drafts are linear rookie drafts.
            for pick in draft_record.get("picks", []):
                roster_id = pick.get("roster_id")
                picked_by = pick.get("picked_by")
                user_id = None
                if picked_by is not None and str(picked_by) in owners:
                    user_id = str(picked_by)
                elif roster_id is not None:
                    user_id = roster_to_user.get(str(roster_id))
                if not user_id:
                    continue

                player_id = str(pick.get("player_id")) if pick.get("player_id") is not None else None
                player = players.get(player_id, {}) if player_id else {}
                pos = player.get("position") or "UNKNOWN"
                rnd = pick.get("round")

                s = stats[user_id]
                s["rookie_picks_total"] += 1
                s["rookie_picks_by_season"][season] += 1
                if rnd is not None:
                    s["rookie_picks_by_round"][str(rnd)] += 1
                s["rookie_positions"][pos] += 1
                if rnd == 1:
                    s["rookie_first_round_positions"][pos] += 1
                elif rnd == 2:
                    s["rookie_second_round_positions"][pos] += 1
                elif rnd == 3:
                    s["rookie_third_round_positions"][pos] += 1

    # Waiver/free-agent behavior
    for season_data in history:
        league = season_data.get("league", {})
        season = str(league.get("season") or "unknown")
        roster_to_user = season_rosters.get(season, {})

        for transaction in season_data.get("transactions", []):
            ttype = transaction.get("type")
            if ttype not in {"waiver", "free_agent"}:
                continue
            if transaction.get("status") not in {None, "complete", "completed"}:
                continue

            settings = transaction.get("settings") or {}
            bid = settings.get("waiver_bid")
            adds = transaction.get("adds") or {}

            for player_id, recv_roster in adds.items():
                user_id = roster_to_user.get(str(recv_roster))
                if not user_id:
                    continue
                player = players.get(str(player_id), {})
                pos = player.get("position") or "UNKNOWN"
                s = stats[user_id]
                if ttype == "waiver":
                    s["waiver_claims"] += 1
                    if bid is not None and bid > 0:
                        s["faab_spent"] += bid
                        s["positive_faab_bids"].append(bid)
                else:
                    s["free_agent_adds"] += 1
                s["waiver_positions"][pos] += 1

    profiles = []
    for user_id, owner in owners.items():
        s = stats[user_id]
        total_trades = s["trade_total"]
        total_acq = s["waiver_claims"] + s["free_agent_adds"]
        bids = s["positive_faab_bids"]

        # Simple descriptive flags, not value judgments.
        trade_activity = (
            "very_high" if total_trades >= 35 else
            "high" if total_trades >= 24 else
            "moderate" if total_trades >= 15 else
            "low"
        )
        initiation_rate = round(s["trade_initiated"] / total_trades, 3) if total_trades else 0
        complexity_rate = round(s["multi_asset_trades"] / total_trades, 3) if total_trades else 0
        waiver_aggression = (
            "high" if (len(bids) >= 20 and (sum(bids)/len(bids) if bids else 0) >= 12) else
            "active" if s["waiver_claims"] >= 25 else
            "selective"
        )

        profiles.append({
            **owner,
            "trade_profile": {
                "activity_band": trade_activity,
                "total_trades": total_trades,
                "trades_initiated": s["trade_initiated"],
                "initiation_rate": initiation_rate,
                "recent_trades_2025_2026": s["recent_trades_2025_2026"],
                "trades_by_season": dict(sorted(s["trade_seasons"].items())),
                "one_for_one_trades": s["one_for_one_trades"],
                "multi_asset_trades": s["multi_asset_trades"],
                "multi_asset_rate": complexity_rate,
                "player_positions_acquired": dict(s["player_positions_in"].most_common()),
                "player_positions_sent": dict(s["player_positions_out"].most_common()),
                "firsts_acquired": s["firsts_in"],
                "firsts_sent": s["firsts_out"],
                "seconds_acquired": s["seconds_in"],
                "seconds_sent": s["seconds_out"],
                "thirds_acquired": s["thirds_in"],
                "thirds_sent": s["thirds_out"],
                "faab_acquired_in_trades": s["faab_in"],
                "faab_sent_in_trades": s["faab_out"],
                "top_trade_partners": [
                    {
                        "user_id": pid,
                        "manager": owners.get(pid, {}).get("manager") or pid,
                        "team_name": owners.get(pid, {}).get("team_name"),
                        "trades": count,
                    }
                    for pid, count in s["trade_partners"].most_common(5)
                ],
            },
            "rookie_draft_profile": {
                "rookie_picks_made_2023_plus": s["rookie_picks_total"],
                "picks_by_season": dict(sorted(s["rookie_picks_by_season"].items())),
                "picks_by_round": dict(sorted(s["rookie_picks_by_round"].items())),
                "positions": dict(s["rookie_positions"].most_common()),
                "first_round_positions": dict(s["rookie_first_round_positions"].most_common()),
                "second_round_positions": dict(s["rookie_second_round_positions"].most_common()),
                "third_round_positions": dict(s["rookie_third_round_positions"].most_common()),
            },
            "waiver_profile": {
                "aggression_band": waiver_aggression,
                "waiver_claims": s["waiver_claims"],
                "free_agent_adds": s["free_agent_adds"],
                "total_acquisitions": total_acq,
                "positions_added": dict(s["waiver_positions"].most_common()),
                "recorded_faab_spent": s["faab_spent"],
                "average_positive_bid": (
                    round(sum(bids) / len(bids), 2) if bids else 0
                ),
                "largest_positive_bid": max(bids) if bids else 0,
            },
        })

    profiles.sort(
        key=lambda x: (
            -x["trade_profile"]["total_trades"],
            x["manager"]
        )
    )

    # Jimmy-specific counterparty history
    target_id = None
    for uid, owner in owners.items():
        if str(owner.get("username", "")).lower() == TARGET_USERNAME.lower():
            target_id = uid
            break

    counterparties = []
    if target_id:
        for other_id, owner in owners.items():
            if other_id == target_id:
                continue

            shared = []
            for season_data in history:
                league = season_data.get("league", {})
                season = str(league.get("season") or "unknown")
                roster_to_user = season_rosters.get(season, {})

                for transaction in season_data.get("transactions", []):
                    if transaction.get("type") != "trade":
                        continue
                    if transaction.get("status") not in {None, "complete", "completed"}:
                        continue
                    roster_ids = [str(x) for x in (transaction.get("roster_ids") or [])]
                    participants = {
                        roster_to_user.get(r)
                        for r in roster_ids
                        if roster_to_user.get(r)
                    }
                    if target_id in participants and other_id in participants:
                        shared.append({
                            "season": season,
                            "created": transaction.get("created"),
                            "creator": str(transaction.get("creator")) if transaction.get("creator") is not None else None,
                            "transaction_id": transaction.get("transaction_id"),
                        })

            shared.sort(key=lambda x: x.get("created") or 0, reverse=True)
            if shared:
                initiated_by_jimmy = sum(1 for t in shared if t["creator"] == target_id)
                initiated_by_other = sum(1 for t in shared if t["creator"] == other_id)
                counterparties.append({
                    "user_id": other_id,
                    "manager": owner.get("manager"),
                    "team_name": owner.get("team_name"),
                    "completed_trades_with_jimmy": len(shared),
                    "initiated_by_jimmy": initiated_by_jimmy,
                    "initiated_by_counterparty": initiated_by_other,
                    "seasons": dict(Counter(t["season"] for t in shared)),
                    "transaction_ids": [t["transaction_id"] for t in shared],
                })

        counterparties.sort(
            key=lambda x: (-x["completed_trades_with_jimmy"], x["manager"])
        )

    return {
        "owner_behavior_profiles": profiles,
        "jimmy_counterparty_profiles": counterparties,
    }


def classify_transaction_phase(created_ms):
    if not created_ms:
        return "unknown"
    tm = time.gmtime(created_ms / 1000)
    month = tm.tm_mon
    if month in {1, 2, 3, 4}:
        return "early_offseason"
    if month in {5, 6}:
        return "rookie_draft_offseason"
    if month in {7, 8}:
        return "camp_preseason"
    if month in {9, 10}:
        return "early_regular_season"
    if month == 11:
        return "trade_deadline_window"
    return "late_regular_season_playoffs"


def player_age_on_date(player, created_ms):
    birth_date = player.get("birth_date")
    if not birth_date or not created_ms:
        return None
    try:
        birth = time.strptime(birth_date, "%Y-%m-%d")
        event = time.gmtime(created_ms / 1000)
        age = event.tm_year - birth.tm_year
        if (event.tm_mon, event.tm_mday) < (birth.tm_mon, birth.tm_mday):
            age -= 1
        return age
    except Exception:
        return None


def make_pick_key(season, round_number, original_roster_id):
    if season is None or round_number is None or original_roster_id is None:
        return None
    return f"pick:{season}:R{round_number}:orig{original_roster_id}"


def make_player_key(player_id):
    if player_id is None:
        return None
    return f"player:{player_id}"


def pick_asset_from_trade(pick):
    season = str(pick.get("season")) if pick.get("season") is not None else None
    round_number = pick.get("round")
    original_roster_id = (
        str(pick.get("original_roster_id"))
        if pick.get("original_roster_id") is not None
        else (
            str(pick.get("roster_id"))
            if pick.get("roster_id") is not None
            else None
        )
    )
    return {
        "asset_key": make_pick_key(season, round_number, original_roster_id),
        "asset_type": "pick",
        "season": season,
        "round": round_number,
        "original_roster_id": original_roster_id,
        "label": (
            f"{season} Round {round_number} "
            f"(original roster {original_roster_id})"
        ),
    }


def player_asset(player_id, players):
    player_id = str(player_id)
    player = players.get(player_id, {})
    return {
        "asset_key": make_player_key(player_id),
        "asset_type": "player",
        "player_id": player_id,
        "label": player.get("full_name") or player_id,
        "position": player.get("position"),
        "nfl_team": player.get("team"),
    }


def build_pick_provenance(history):
    owners, season_rosters = build_owner_directory(history)
    records = {}

    for season_data in reversed(history):
        league = season_data.get("league", {})
        league_season = str(league.get("season") or "unknown")
        roster_to_user = season_rosters.get(league_season, {})

        for pick in season_data.get("traded_picks", []):
            pick_season = (
                str(pick.get("season"))
                if pick.get("season") is not None
                else None
            )
            round_number = pick.get("round")
            original_roster_id = (
                str(pick.get("roster_id"))
                if pick.get("roster_id") is not None
                else None
            )
            owner_roster_id = (
                str(pick.get("owner_id"))
                if pick.get("owner_id") is not None
                else None
            )
            previous_roster_id = (
                str(pick.get("previous_owner_id"))
                if pick.get("previous_owner_id") is not None
                else None
            )
            key = make_pick_key(
                pick_season,
                round_number,
                original_roster_id,
            )
            if not key:
                continue

            record = records.setdefault(key, {
                "asset_key": key,
                "season": pick_season,
                "round": round_number,
                "original_roster_id": original_roster_id,
                "observed_transfers": [],
                "latest_observed_owner_roster_id": None,
                "latest_observed_owner_user_id": None,
                "latest_observed_owner_manager": None,
                "latest_observed_owner_team": None,
            })

            owner_user_id = roster_to_user.get(owner_roster_id)
            previous_user_id = roster_to_user.get(previous_roster_id)

            record["observed_transfers"].append({
                "league_season": league_season,
                "from_roster_id": previous_roster_id,
                "from_user_id": previous_user_id,
                "from_manager": (
                    owners.get(previous_user_id, {}).get("manager")
                    if previous_user_id else None
                ),
                "to_roster_id": owner_roster_id,
                "to_user_id": owner_user_id,
                "to_manager": (
                    owners.get(owner_user_id, {}).get("manager")
                    if owner_user_id else None
                ),
            })

            record["latest_observed_owner_roster_id"] = owner_roster_id
            record["latest_observed_owner_user_id"] = owner_user_id
            record["latest_observed_owner_manager"] = (
                owners.get(owner_user_id, {}).get("manager")
                if owner_user_id else None
            )
            record["latest_observed_owner_team"] = (
                owners.get(owner_user_id, {}).get("team_name")
                if owner_user_id else None
            )

    result = list(records.values())
    result.sort(
        key=lambda x: (
            str(x.get("season")),
            x.get("round") or 99,
            x.get("original_roster_id") or "",
        )
    )
    return result


def reconstruct_pretrade_roster_context(history, players):
    owners, season_rosters = build_owner_directory(history)
    contexts = []

    for season_data in history:
        league = season_data.get("league", {})
        season = str(league.get("season") or "unknown")
        roster_to_user = season_rosters.get(season, {})

        roster_sets = {}
        for roster in season_data.get("rosters", []):
            roster_id = str(roster.get("roster_id"))
            roster_sets[roster_id] = {
                str(pid)
                for pid in (roster.get("players") or [])
                if pid is not None
            }

        transactions = sorted(
            season_data.get("transactions", []),
            key=lambda x: x.get("created") or 0,
            reverse=True,
        )

        for transaction in transactions:
            status = transaction.get("status")
            if status not in {None, "complete", "completed"}:
                continue

            adds = transaction.get("adds") or {}
            drops = transaction.get("drops") or {}

            # Reverse the completed transaction so roster_sets becomes the
            # approximate roster immediately BEFORE the transaction.
            for player_id, receiving_roster in adds.items():
                receiving_roster = str(receiving_roster)
                roster_sets.setdefault(receiving_roster, set()).discard(
                    str(player_id)
                )

            for player_id, sending_roster in drops.items():
                sending_roster = str(sending_roster)
                roster_sets.setdefault(sending_roster, set()).add(
                    str(player_id)
                )

            if transaction.get("type") != "trade":
                continue

            created = transaction.get("created")
            participant_context = []

            for roster_id in [
                str(x)
                for x in (transaction.get("roster_ids") or [])
            ]:
                user_id = roster_to_user.get(roster_id)
                pids = roster_sets.get(roster_id, set())
                positions = Counter()
                ages = []
                for pid in pids:
                    player = players.get(pid, {})
                    positions[player.get("position") or "UNKNOWN"] += 1
                    age = player_age_on_date(player, created)
                    if age is not None:
                        ages.append(age)

                participant_context.append({
                    "roster_id": roster_id,
                    "user_id": user_id,
                    "manager": (
                        owners.get(user_id, {}).get("manager")
                        if user_id else None
                    ),
                    "team_name": (
                        owners.get(user_id, {}).get("team_name")
                        if user_id else None
                    ),
                    "approx_pretrade_roster_size": len(pids),
                    "approx_pretrade_position_counts": dict(
                        positions.most_common()
                    ),
                    "approx_pretrade_average_age": (
                        round(sum(ages) / len(ages), 2)
                        if ages else None
                    ),
                })

            contexts.append({
                "transaction_id": transaction.get("transaction_id"),
                "season": season,
                "created": created,
                "created_utc": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ",
                    time.gmtime(created / 1000),
                ) if created else None,
                "phase": classify_transaction_phase(created),
                "participants": participant_context,
                "context_quality": (
                    "approximate_reconstruction_from_final_rosters_"
                    "and_completed_transactions"
                ),
            })

    contexts.sort(
        key=lambda x: x.get("created") or 0,
        reverse=True,
    )
    return contexts


def build_asset_lineage_graph(history, players):
    owners, season_rosters = build_owner_directory(history)
    nodes = {}
    edges = []

    def register(asset):
        if not asset or not asset.get("asset_key"):
            return
        nodes.setdefault(asset["asset_key"], asset)

    for season_data in history:
        league = season_data.get("league", {})
        season = str(league.get("season") or "unknown")
        roster_to_user = season_rosters.get(season, {})

        for transaction in season_data.get("transactions", []):
            if transaction.get("type") != "trade":
                continue
            if transaction.get("status") not in {
                None, "complete", "completed"
            }:
                continue

            created = transaction.get("created")
            adds = transaction.get("adds") or {}
            drops = transaction.get("drops") or {}
            draft_picks = transaction.get("draft_picks") or []

            roster_ids = [
                str(x)
                for x in (transaction.get("roster_ids") or [])
            ]

            for roster_id in roster_ids:
                user_id = roster_to_user.get(roster_id)
                sent_assets = []
                received_assets = []

                for player_id, receiving_roster in adds.items():
                    if str(receiving_roster) == roster_id:
                        received_assets.append(
                            player_asset(player_id, players)
                        )
                    sending_roster = drops.get(player_id)
                    if (
                        sending_roster is not None
                        and str(sending_roster) == roster_id
                    ):
                        sent_assets.append(
                            player_asset(player_id, players)
                        )

                for pick in draft_picks:
                    asset = pick_asset_from_trade(pick)
                    if (
                        pick.get("owner_id") is not None
                        and str(pick.get("owner_id")) == roster_id
                    ):
                        received_assets.append(asset)
                    if (
                        pick.get("previous_owner_id") is not None
                        and str(pick.get("previous_owner_id")) == roster_id
                    ):
                        sent_assets.append(asset)

                for asset in sent_assets + received_assets:
                    register(asset)

                if not sent_assets or not received_assets:
                    continue

                mixed = len(sent_assets) > 1
                for source in sent_assets:
                    for target in received_assets:
                        edges.append({
                            "from_asset_key": source["asset_key"],
                            "to_asset_key": target["asset_key"],
                            "owner_user_id": user_id,
                            "owner_manager": (
                                owners.get(user_id, {}).get("manager")
                                if user_id else None
                            ),
                            "transaction_id": transaction.get(
                                "transaction_id"
                            ),
                            "season": season,
                            "created": created,
                            "created_utc": time.strftime(
                                "%Y-%m-%dT%H:%M:%SZ",
                                time.gmtime(created / 1000),
                            ) if created else None,
                            "phase": classify_transaction_phase(created),
                            "lineage_note": (
                                "mixed_inputs_return_not_fully_attributable"
                                if mixed else
                                "single_input_trade_return"
                            ),
                        })

        # Pick -> drafted player edges.
        for draft_record in season_data.get("drafts", []):
            draft = draft_record.get("draft") or {}
            slot_to_roster = draft.get("slot_to_roster_id") or {}
            draft_time = (
                draft.get("start_time")
                or draft.get("created")
                or 0
            )

            for pick in draft_record.get("picks", []):
                picked_by = pick.get("picked_by")
                user_id = (
                    str(picked_by)
                    if picked_by is not None
                    else None
                )
                draft_slot = pick.get("draft_slot")
                original_roster_id = None

                if draft_slot is not None:
                    original_roster_id = (
                        slot_to_roster.get(str(draft_slot))
                        or slot_to_roster.get(draft_slot)
                    )
                if original_roster_id is None:
                    # Fallback only; draft_slot mapping is preferred.
                    original_roster_id = pick.get("roster_id")

                pick_key = make_pick_key(
                    season,
                    pick.get("round"),
                    str(original_roster_id)
                    if original_roster_id is not None
                    else None,
                )
                player_id = pick.get("player_id")
                if not pick_key or player_id is None:
                    continue

                pick_asset = {
                    "asset_key": pick_key,
                    "asset_type": "pick",
                    "season": season,
                    "round": pick.get("round"),
                    "original_roster_id": (
                        str(original_roster_id)
                        if original_roster_id is not None
                        else None
                    ),
                    "label": (
                        f"{season} Round {pick.get('round')} "
                        f"(original roster {original_roster_id})"
                    ),
                }
                drafted_asset = player_asset(player_id, players)
                register(pick_asset)
                register(drafted_asset)

                edges.append({
                    "from_asset_key": pick_asset["asset_key"],
                    "to_asset_key": drafted_asset["asset_key"],
                    "owner_user_id": user_id,
                    "owner_manager": (
                        owners.get(user_id, {}).get("manager")
                        if user_id else None
                    ),
                    "transaction_id": None,
                    "season": season,
                    "created": draft_time,
                    "created_utc": time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ",
                        time.gmtime(draft_time / 1000),
                    ) if draft_time else None,
                    "phase": "rookie_draft",
                    "lineage_note": "pick_used_to_select_player",
                })

    return {
        "nodes": list(nodes.values()),
        "edges": edges,
        "methodology": {
            "trade_edges": (
                "For each owner-side of a trade, every asset sent is "
                "connected to every asset received. Multi-asset trades "
                "are marked as mixed inputs because exact economic "
                "attribution is unknowable."
            ),
            "draft_edges": (
                "Draft-pick assets are connected to the player selected "
                "with that pick using Sleeper draft slot-to-roster "
                "mapping when available."
            ),
        },
    }


def build_named_owner_trade_tree(
    history,
    players,
    owner_username,
    root_player_name,
):
    owners, season_rosters = build_owner_directory(history)
    target_user_id = None

    for user_id, owner in owners.items():
        if str(owner.get("username") or "").lower() == (
            owner_username.lower()
        ):
            target_user_id = user_id
            break

    if not target_user_id:
        return {
            "error": f"Owner {owner_username} not found",
        }

    events = []

    for season_data in history:
        league = season_data.get("league", {})
        season = str(league.get("season") or "unknown")
        roster_to_user = season_rosters.get(season, {})

        for transaction in season_data.get("transactions", []):
            if transaction.get("type") != "trade":
                continue
            if transaction.get("status") not in {
                None, "complete", "completed"
            }:
                continue

            roster_ids = [
                str(x)
                for x in (transaction.get("roster_ids") or [])
            ]
            target_rosters = [
                r for r in roster_ids
                if roster_to_user.get(r) == target_user_id
            ]
            if not target_rosters:
                continue

            roster_id = target_rosters[0]
            adds = transaction.get("adds") or {}
            drops = transaction.get("drops") or {}
            sent = []
            received = []

            for player_id, receiving_roster in adds.items():
                if str(receiving_roster) == roster_id:
                    received.append(player_asset(player_id, players))
                sending_roster = drops.get(player_id)
                if (
                    sending_roster is not None
                    and str(sending_roster) == roster_id
                ):
                    sent.append(player_asset(player_id, players))

            for pick in transaction.get("draft_picks") or []:
                asset = pick_asset_from_trade(pick)
                if (
                    pick.get("owner_id") is not None
                    and str(pick.get("owner_id")) == roster_id
                ):
                    received.append(asset)
                if (
                    pick.get("previous_owner_id") is not None
                    and str(pick.get("previous_owner_id")) == roster_id
                ):
                    sent.append(asset)

            events.append({
                "event_type": "trade",
                "created": transaction.get("created") or 0,
                "season": season,
                "transaction_id": transaction.get("transaction_id"),
                "sent": sent,
                "received": received,
            })

        for draft_record in season_data.get("drafts", []):
            draft = draft_record.get("draft") or {}
            slot_to_roster = draft.get("slot_to_roster_id") or {}
            draft_time = (
                draft.get("start_time")
                or draft.get("created")
                or 0
            )

            for pick in draft_record.get("picks", []):
                picked_by = pick.get("picked_by")
                if str(picked_by) != target_user_id:
                    continue

                draft_slot = pick.get("draft_slot")
                original_roster_id = None
                if draft_slot is not None:
                    original_roster_id = (
                        slot_to_roster.get(str(draft_slot))
                        or slot_to_roster.get(draft_slot)
                    )
                if original_roster_id is None:
                    original_roster_id = pick.get("roster_id")

                pick_key = make_pick_key(
                    season,
                    pick.get("round"),
                    str(original_roster_id)
                    if original_roster_id is not None
                    else None,
                )
                player = player_asset(
                    pick.get("player_id"),
                    players,
                )
                events.append({
                    "event_type": "draft",
                    "created": draft_time,
                    "season": season,
                    "transaction_id": None,
                    "pick_asset_key": pick_key,
                    "selected_player": player,
                })

    events.sort(key=lambda x: x.get("created") or 0)

    root_trade = None
    for event in events:
        if event["event_type"] != "trade":
            continue
        for asset in event["sent"]:
            if (
                asset.get("asset_type") == "player"
                and str(asset.get("label") or "").lower()
                == root_player_name.lower()
            ):
                root_trade = event
                break
        if root_trade:
            break

    if not root_trade:
        return {
            "error": (
                f"No completed trade found where {owner_username} "
                f"sent {root_player_name}"
            )
        }

    descendant_keys = {
        asset["asset_key"]
        for asset in root_trade["received"]
        if asset.get("asset_key")
    }
    held_keys = set(descendant_keys)
    node_map = {}
    tree_edges = []

    root_player_id = None
    for event in events:
        if event is root_trade:
            for asset in event["sent"]:
                if (
                    asset.get("asset_type") == "player"
                    and str(asset.get("label") or "").lower()
                    == root_player_name.lower()
                ):
                    root_player_id = asset.get("player_id")
                    node_map[asset["asset_key"]] = asset
            for asset in event["received"]:
                node_map[asset["asset_key"]] = asset
                tree_edges.append({
                    "from_asset_key": make_player_key(root_player_id),
                    "to_asset_key": asset["asset_key"],
                    "event_type": "root_trade_return",
                    "season": event["season"],
                    "created": event["created"],
                    "transaction_id": event["transaction_id"],
                    "attribution": "direct",
                })
            break

    root_time = root_trade["created"]

    for event in events:
        if event["created"] <= root_time:
            continue

        if event["event_type"] == "draft":
            pick_key = event.get("pick_asset_key")
            if pick_key not in held_keys:
                continue

            player = event["selected_player"]
            node_map[player["asset_key"]] = player
            tree_edges.append({
                "from_asset_key": pick_key,
                "to_asset_key": player["asset_key"],
                "event_type": "draft_conversion",
                "season": event["season"],
                "created": event["created"],
                "transaction_id": None,
                "attribution": "direct",
            })
            held_keys.discard(pick_key)
            held_keys.add(player["asset_key"])
            descendant_keys.add(player["asset_key"])
            continue

        sent_descendants = [
            asset
            for asset in event["sent"]
            if asset.get("asset_key") in held_keys
        ]
        if not sent_descendants:
            continue

        total_sent = len(event["sent"])
        attribution = (
            "direct"
            if total_sent == len(sent_descendants)
            else "mixed_with_non_lineage_assets"
        )

        for source in sent_descendants:
            held_keys.discard(source["asset_key"])

        for received in event["received"]:
            node_map[received["asset_key"]] = received
            descendant_keys.add(received["asset_key"])
            held_keys.add(received["asset_key"])

            for source in sent_descendants:
                tree_edges.append({
                    "from_asset_key": source["asset_key"],
                    "to_asset_key": received["asset_key"],
                    "event_type": "downstream_trade",
                    "season": event["season"],
                    "created": event["created"],
                    "transaction_id": event["transaction_id"],
                    "attribution": attribution,
                })

    nodes = list(node_map.values())
    for node in nodes:
        node["currently_held_in_lineage"] = (
            node["asset_key"] in held_keys
        )

    return {
        "owner_username": owner_username,
        "owner_user_id": target_user_id,
        "root_player": root_player_name,
        "root_transaction_id": root_trade["transaction_id"],
        "root_created": root_trade["created"],
        "root_created_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime(root_trade["created"] / 1000),
        ) if root_trade["created"] else None,
        "direct_return_assets": root_trade["received"],
        "nodes": nodes,
        "edges": tree_edges,
        "currently_held_descendant_asset_keys": sorted(held_keys),
        "methodology_note": (
            "When a lineage asset was packaged with unrelated assets, "
            "all return assets are retained in the tree but marked "
            "mixed_with_non_lineage_assets; exact economic attribution "
            "cannot be known from Sleeper transaction data alone."
        ),
    }


def build_outcome_proxies(history, players, draft_analytics, acquisition_analytics):
    owners, _ = build_owner_directory(history)
    current = history[0] if history else {}
    current_rosters = current.get("rosters", [])

    player_to_current_user = {}
    roster_to_current_user = {}

    for roster in current_rosters:
        owner_id = roster.get("owner_id")
        roster_id = roster.get("roster_id")
        if owner_id is None:
            continue
        user_id = str(owner_id)
        roster_to_current_user[str(roster_id)] = user_id
        for pid in roster.get("players") or []:
            player_to_current_user[str(pid)] = user_id

    draft_rows = []
    draft_owner_stats = defaultdict(lambda: {
        "rookie_picks": 0,
        "still_rostered_in_league": 0,
        "still_with_drafter": 0,
        "active_nfl": 0,
    })

    for pick in draft_analytics["draft_ledger"]:
        if str(pick.get("season")) == "2022":
            continue
        user_id = pick.get("user_id")
        player_id = pick.get("player_id")
        if not user_id or not player_id:
            continue

        player = players.get(str(player_id), {})
        current_user = player_to_current_user.get(str(player_id))
        in_league = current_user is not None
        retained = current_user == user_id
        active = bool(player.get("active"))

        s = draft_owner_stats[user_id]
        s["rookie_picks"] += 1
        s["still_rostered_in_league"] += int(in_league)
        s["still_with_drafter"] += int(retained)
        s["active_nfl"] += int(active)

        draft_rows.append({
            "season": pick.get("season"),
            "round": pick.get("round"),
            "pick_no": pick.get("pick_no"),
            "user_id": user_id,
            "manager": pick.get("manager"),
            "player_id": player_id,
            "player_name": pick.get("player_name"),
            "position": pick.get("position"),
            "currently_rostered_in_league": in_league,
            "current_owner_user_id": current_user,
            "current_owner_manager": (
                owners.get(current_user, {}).get("manager")
                if current_user else None
            ),
            "still_with_original_drafter": retained,
            "currently_active_nfl": active,
            "proxy_warning": (
                "Retention is not the same as fantasy value or draft hit."
            ),
        })

    draft_summary = []
    for user_id, s in draft_owner_stats.items():
        total = s["rookie_picks"]
        draft_summary.append({
            "user_id": user_id,
            "manager": owners.get(user_id, {}).get("manager"),
            "team_name": owners.get(user_id, {}).get("team_name"),
            **s,
            "league_roster_retention_rate": (
                round(s["still_rostered_in_league"] / total, 3)
                if total else None
            ),
            "same_team_retention_rate": (
                round(s["still_with_drafter"] / total, 3)
                if total else None
            ),
        })

    waiver_owner_stats = defaultdict(lambda: {
        "unique_add_events": 0,
        "currently_rostered_in_league": 0,
        "currently_with_acquirer": 0,
    })
    waiver_rows = []

    for event in acquisition_analytics["acquisition_ledger"]:
        user_id = event.get("user_id")
        if not user_id:
            continue

        for asset in event.get("players_added", []):
            pid = str(asset.get("player_id"))
            current_user = player_to_current_user.get(pid)
            in_league = current_user is not None
            retained = current_user == user_id

            s = waiver_owner_stats[user_id]
            s["unique_add_events"] += 1
            s["currently_rostered_in_league"] += int(in_league)
            s["currently_with_acquirer"] += int(retained)

            waiver_rows.append({
                "transaction_id": event.get("transaction_id"),
                "season": event.get("season"),
                "created": event.get("created"),
                "type": event.get("type"),
                "faab_bid": event.get("faab_bid"),
                "user_id": user_id,
                "manager": event.get("manager"),
                "player_id": pid,
                "player_name": asset.get("name"),
                "position": asset.get("position"),
                "currently_rostered_in_league": in_league,
                "current_owner_user_id": current_user,
                "current_owner_manager": (
                    owners.get(current_user, {}).get("manager")
                    if current_user else None
                ),
                "still_with_original_acquirer": retained,
                "proxy_warning": (
                    "Current retention is a weak outcome proxy and "
                    "does not measure points or historical peak value."
                ),
            })

    waiver_summary = []
    for user_id, s in waiver_owner_stats.items():
        total = s["unique_add_events"]
        waiver_summary.append({
            "user_id": user_id,
            "manager": owners.get(user_id, {}).get("manager"),
            "team_name": owners.get(user_id, {}).get("team_name"),
            **s,
            "league_roster_retention_rate": (
                round(s["currently_rostered_in_league"] / total, 3)
                if total else None
            ),
            "same_team_retention_rate": (
                round(s["currently_with_acquirer"] / total, 3)
                if total else None
            ),
        })

    return {
        "draft_outcome_proxy_ledger": draft_rows,
        "owner_draft_outcome_proxy": draft_summary,
        "waiver_outcome_proxy_ledger": waiver_rows,
        "owner_waiver_outcome_proxy": waiver_summary,
    }


def build_league_market_summary(trade_analytics):
    structures = Counter()
    side_profiles = Counter()
    pick_rounds_moved = Counter()
    player_positions_moved = Counter()
    phase_counts = Counter()
    observations = []

    for trade in trade_analytics["trade_ledger"]:
        phase = classify_transaction_phase(trade.get("created"))
        phase_counts[phase] += 1
        sides = trade.get("sides", [])

        side_asset_counts = []
        for side in sides:
            players_in = side.get("received_players", [])
            picks_in = side.get("received_picks", [])
            players_out = side.get("sent_players", [])
            picks_out = side.get("sent_picks", [])

            received_types = Counter(
                [p.get("position") or "PLAYER" for p in players_in]
                + [f"R{p.get('round')}" for p in picks_in]
            )
            sent_types = Counter(
                [p.get("position") or "PLAYER" for p in players_out]
                + [f"R{p.get('round')}" for p in picks_out]
            )

            for p in players_in:
                player_positions_moved[
                    p.get("position") or "UNKNOWN"
                ] += 1
            for p in picks_in:
                pick_rounds_moved[f"R{p.get('round')}"] += 1

            side_asset_counts.append(
                len(players_in)
                + len(picks_in)
                + int((side.get("faab_received") or 0) > 0)
            )

            profile_key = (
                "recv:" + ",".join(
                    f"{k}x{v}"
                    for k, v in sorted(received_types.items())
                )
                + "|send:" + ",".join(
                    f"{k}x{v}"
                    for k, v in sorted(sent_types.items())
                )
            )
            side_profiles[profile_key] += 1

            observations.append({
                "transaction_id": trade.get("transaction_id"),
                "season": trade.get("season"),
                "created": trade.get("created"),
                "phase": phase,
                "manager": side.get("manager"),
                "team_name": side.get("team_name"),
                "received_positions": dict(received_types),
                "sent_positions": dict(sent_types),
                "faab_received": side.get("faab_received") or 0,
                "faab_sent": side.get("faab_sent") or 0,
            })

        structures[
            "x".join(str(x) for x in sorted(side_asset_counts))
        ] += 1

    return {
        "completed_trade_count": len(
            trade_analytics["trade_ledger"]
        ),
        "trade_structure_counts": dict(
            structures.most_common()
        ),
        "trade_side_template_counts": dict(
            side_profiles.most_common()
        ),
        "pick_rounds_received_across_trade_sides": dict(
            pick_rounds_moved.most_common()
        ),
        "player_positions_received_across_trade_sides": dict(
            player_positions_moved.most_common()
        ),
        "trades_by_phase": dict(phase_counts.most_common()),
        "observations": observations,
        "interpretation_warning": (
            "This is observed league pricing structure, not a dollar "
            "or universal dynasty-value model. Exact asset values require "
            "an external or historical valuation source."
        ),
    }


def build_analysis_manifest():
    return {
        "capabilities": [
            "live_current_rosters_and_pick_ownership",
            "historical_completed_trade_ledger",
            "owner_trade_behavior_profiles",
            "rookie_only_draft_tendencies_2023_plus",
            "waiver_and_free_agent_behavior",
            "owner_specific_history_with_jimmy",
            "asset_lineage_trade_trees",
            "future_pick_provenance",
            "transaction_phase_tags",
            "approximate_pretrade_roster_context",
            "player_age_and_draft_stage_metadata",
            "draft_retention_outcome_proxies",
            "waiver_retention_outcome_proxies",
            "league_specific_trade_structure_market_summary",
            "patrick_mahomes_mocha_trade_tree",
        ],
        "important_limitations": [
            (
                "Sleeper exposes completed transactions, not a historical "
                "archive of rejected or expired offers."
            ),
            (
                "Multi-asset trade lineage cannot assign exact economic "
                "weight to each input; mixed trades are explicitly marked."
            ),
            (
                "Draft and waiver retention are outcome proxies, not true "
                "fantasy-value hit rates."
            ),
            (
                "Historical market-value-at-time-of-transaction requires "
                "an external historical dynasty valuation source."
            ),
            (
                "Pre-trade roster context is reconstructed from final "
                "season rosters plus completed transactions and is approximate."
            ),
        ],
        "recommended_manual_layer": (
            "Log rejected offers, counters, verbal asking prices, and "
            "untouchable-player statements going forward."
        ),
    }


def build_draft_pick_conversion_index(history, players):
    """
    Generic pick -> drafted player resolver.

    Sleeper rookie drafts expose draft_order as:
        user_id -> original draft slot

    We combine that with that season's roster map:
        user_id -> roster_id

    This lets us resolve a traded asset such as:
        2026 Round 1, original roster 10
    into the exact player ultimately selected with that original pick.
    """
    owners, season_rosters = build_owner_directory(history)
    conversions = []
    by_pick_key = {}

    for season_data in history:
        league = season_data.get("league", {})
        season = str(league.get("season") or "unknown")
        roster_to_user = season_rosters.get(season, {})
        user_to_roster = {
            str(user_id): str(roster_id)
            for roster_id, user_id in roster_to_user.items()
        }

        for draft_record in season_data.get("drafts", []):
            draft = draft_record.get("draft") or {}
            draft_order = draft.get("draft_order") or {}

            # Sleeper: user_id -> slot. Invert to slot -> original user.
            slot_to_original_user = {
                str(slot): str(user_id)
                for user_id, slot in draft_order.items()
                if slot is not None
            }

            for pick in draft_record.get("picks", []):
                slot = pick.get("draft_slot")
                round_number = pick.get("round")
                player_id = (
                    str(pick.get("player_id"))
                    if pick.get("player_id") is not None
                    else None
                )

                if slot is None or round_number is None or not player_id:
                    continue

                original_user_id = slot_to_original_user.get(str(slot))
                original_roster_id = (
                    user_to_roster.get(original_user_id)
                    if original_user_id
                    else None
                )

                if original_roster_id is None:
                    # No silent guess: unresolved conversions remain explicit.
                    continue

                pick_key = make_pick_key(
                    season,
                    round_number,
                    original_roster_id,
                )
                player = players.get(player_id, {})
                drafted_by_user_id = (
                    str(pick.get("picked_by"))
                    if pick.get("picked_by") is not None
                    else roster_to_user.get(str(pick.get("roster_id")))
                )

                row = {
                    "pick_asset_key": pick_key,
                    "season": season,
                    "round": round_number,
                    "draft_slot": slot,
                    "pick_no": pick.get("pick_no"),
                    "original_roster_id": original_roster_id,
                    "original_owner_user_id": original_user_id,
                    "original_owner_manager": (
                        owners.get(original_user_id, {}).get("manager")
                        if original_user_id else None
                    ),
                    "drafted_by_user_id": drafted_by_user_id,
                    "drafted_by_manager": (
                        owners.get(drafted_by_user_id, {}).get("manager")
                        if drafted_by_user_id else None
                    ),
                    "drafted_by_team": (
                        owners.get(drafted_by_user_id, {}).get("team_name")
                        if drafted_by_user_id else None
                    ),
                    "player_asset_key": make_player_key(player_id),
                    "player_id": player_id,
                    "player_name": player.get("full_name") or player_id,
                    "position": player.get("position"),
                    "nfl_team": player.get("team"),
                    "draft_id": draft.get("draft_id"),
                }
                conversions.append(row)
                by_pick_key[pick_key] = row

    conversions.sort(
        key=lambda x: (
            str(x.get("season")),
            x.get("pick_no") or 9999,
        )
    )

    return {
        "conversions": conversions,
        "by_pick_key": by_pick_key,
    }


def build_trade_asset_index(history, players):
    """
    Normalizes every completed trade into owner-side sent/received asset keys.
    This is deliberately generic so any trade can become a lineage root.
    """
    owners, season_rosters = build_owner_directory(history)
    trades = []
    by_transaction_id = {}

    for season_data in history:
        league = season_data.get("league", {})
        season = str(league.get("season") or "unknown")
        roster_to_user = season_rosters.get(season, {})

        for transaction in season_data.get("transactions", []):
            if transaction.get("type") != "trade":
                continue
            if transaction.get("status") not in {
                None, "complete", "completed"
            }:
                continue

            adds = transaction.get("adds") or {}
            drops = transaction.get("drops") or {}
            sides = []

            for roster_id in [
                str(x) for x in (transaction.get("roster_ids") or [])
            ]:
                user_id = roster_to_user.get(roster_id)
                sent = []
                received = []

                for player_id, receiving_roster in adds.items():
                    if str(receiving_roster) == roster_id:
                        received.append(player_asset(player_id, players))

                    sending_roster = drops.get(player_id)
                    if (
                        sending_roster is not None
                        and str(sending_roster) == roster_id
                    ):
                        sent.append(player_asset(player_id, players))

                for pick in transaction.get("draft_picks") or []:
                    asset = pick_asset_from_trade(pick)

                    if (
                        pick.get("owner_id") is not None
                        and str(pick.get("owner_id")) == roster_id
                    ):
                        received.append(asset)

                    if (
                        pick.get("previous_owner_id") is not None
                        and str(pick.get("previous_owner_id")) == roster_id
                    ):
                        sent.append(asset)

                side = {
                    "roster_id": roster_id,
                    "user_id": user_id,
                    "manager": (
                        owners.get(user_id, {}).get("manager")
                        if user_id else None
                    ),
                    "team_name": (
                        owners.get(user_id, {}).get("team_name")
                        if user_id else None
                    ),
                    "sent_assets": sent,
                    "received_assets": received,
                }
                sides.append(side)

            row = {
                "transaction_id": transaction.get("transaction_id"),
                "season": season,
                "created": transaction.get("created"),
                "created_utc": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ",
                    time.gmtime(transaction.get("created") / 1000),
                ) if transaction.get("created") else None,
                "phase": classify_transaction_phase(
                    transaction.get("created")
                ),
                "creator_user_id": (
                    str(transaction.get("creator"))
                    if transaction.get("creator") is not None
                    else None
                ),
                "sides": sides,
            }
            trades.append(row)
            by_transaction_id[str(row["transaction_id"])] = row

    trades.sort(key=lambda x: x.get("created") or 0)
    return {
        "trades": trades,
        "by_transaction_id": by_transaction_id,
    }


def build_generic_asset_lineage(
    history,
    players,
    trade_asset_index,
    draft_conversion_index,
):
    """
    Universal graph:
      player/pick -> trade return assets -> drafted player -> later returns ...

    It supports both forward descendants and backward ancestry.

    Mixed trades are intentionally marked; the graph does not pretend that
    one lineage asset economically created 100% of a return when unrelated
    assets were bundled with it.
    """
    nodes = {}
    edges = []

    def register(asset):
        if not asset:
            return
        key = asset.get("asset_key")
        if not key:
            return
        if key not in nodes:
            nodes[key] = dict(asset)

    # Register every trade asset and create owner-side exchange edges.
    for trade in trade_asset_index["trades"]:
        for side in trade.get("sides", []):
            sent = side.get("sent_assets") or []
            received = side.get("received_assets") or []

            for asset in sent + received:
                register(asset)

            if not sent or not received:
                continue

            attribution = (
                "direct_exchange"
                if len(sent) == 1
                else "mixed_input_exchange"
            )

            for source in sent:
                for target in received:
                    edges.append({
                        "from_asset_key": source["asset_key"],
                        "to_asset_key": target["asset_key"],
                        "edge_type": "trade_exchange",
                        "transaction_id": trade.get("transaction_id"),
                        "season": trade.get("season"),
                        "created": trade.get("created"),
                        "created_utc": trade.get("created_utc"),
                        "phase": trade.get("phase"),
                        "owner_user_id": side.get("user_id"),
                        "owner_manager": side.get("manager"),
                        "attribution": attribution,
                        "sent_asset_count": len(sent),
                        "received_asset_count": len(received),
                    })

    # Register every pick -> player conversion using the corrected resolver.
    for conversion in draft_conversion_index["conversions"]:
        pick_asset = {
            "asset_key": conversion["pick_asset_key"],
            "asset_type": "pick",
            "season": conversion["season"],
            "round": conversion["round"],
            "original_roster_id": conversion["original_roster_id"],
            "label": (
                f"{conversion['season']} Round "
                f"{conversion['round']} "
                f"(original roster "
                f"{conversion['original_roster_id']})"
            ),
        }
        drafted_asset = player_asset(
            conversion["player_id"],
            players,
        )
        register(pick_asset)
        register(drafted_asset)

        edges.append({
            "from_asset_key": pick_asset["asset_key"],
            "to_asset_key": drafted_asset["asset_key"],
            "edge_type": "draft_conversion",
            "transaction_id": None,
            "draft_id": conversion.get("draft_id"),
            "season": conversion.get("season"),
            "created": None,
            "created_utc": None,
            "phase": "rookie_draft",
            "owner_user_id": conversion.get("drafted_by_user_id"),
            "owner_manager": conversion.get("drafted_by_manager"),
            "attribution": "exact_pick_conversion",
            "pick_no": conversion.get("pick_no"),
            "draft_slot": conversion.get("draft_slot"),
        })

    # Current player ownership.
    owners, season_rosters = build_owner_directory(history)
    current = history[0] if history else {}
    current_season = str(
        (current.get("league") or {}).get("season") or "unknown"
    )
    current_roster_to_user = season_rosters.get(current_season, {})

    for roster in current.get("rosters", []):
        roster_id = str(roster.get("roster_id"))
        user_id = current_roster_to_user.get(roster_id)
        for pid in roster.get("players") or []:
            key = make_player_key(str(pid))
            if key in nodes:
                nodes[key]["current_owner_user_id"] = user_id
                nodes[key]["current_owner_manager"] = (
                    owners.get(user_id, {}).get("manager")
                    if user_id else None
                )
                nodes[key]["current_owner_team"] = (
                    owners.get(user_id, {}).get("team_name")
                    if user_id else None
                )
                nodes[key]["current_status"] = "currently_rostered_player"

    # Current traded-pick ownership for future picks.
    for pick in current.get("traded_picks", []):
        season = (
            str(pick.get("season"))
            if pick.get("season") is not None else None
        )
        round_number = pick.get("round")
        original_roster_id = (
            str(pick.get("roster_id"))
            if pick.get("roster_id") is not None else None
        )
        key = make_pick_key(
            season,
            round_number,
            original_roster_id,
        )
        if not key or key not in nodes:
            continue

        owner_roster = (
            str(pick.get("owner_id"))
            if pick.get("owner_id") is not None else None
        )
        user_id = current_roster_to_user.get(owner_roster)

        nodes[key]["current_owner_user_id"] = user_id
        nodes[key]["current_owner_manager"] = (
            owners.get(user_id, {}).get("manager")
            if user_id else None
        )
        nodes[key]["current_owner_team"] = (
            owners.get(user_id, {}).get("team_name")
            if user_id else None
        )
        nodes[key]["current_status"] = "currently_held_future_pick"

    # Historical picks that have converted into players are NOT current assets.
    converted_pick_keys = {
        c["pick_asset_key"]
        for c in draft_conversion_index["conversions"]
    }
    for key in converted_pick_keys:
        if key in nodes:
            nodes[key]["current_status"] = "consumed_in_draft"

    # Build adjacency indexes for fast forward/backward traversal.
    forward = defaultdict(list)
    backward = defaultdict(list)

    for i, edge in enumerate(edges):
        edge["edge_id"] = i
        forward[edge["from_asset_key"]].append(i)
        backward[edge["to_asset_key"]].append(i)

    return {
        "nodes": list(nodes.values()),
        "edges": edges,
        "forward_edge_index": dict(forward),
        "backward_edge_index": dict(backward),
        "methodology": {
            "draft_resolution": (
                "Exact original-pick identity is derived from Sleeper "
                "draft_order (user_id -> slot) plus the season-specific "
                "user-to-roster map."
            ),
            "trade_resolution": (
                "Each owner-side asset sent is linked to each return asset. "
                "If multiple assets were sent, edges are marked mixed-input."
            ),
            "query_support": [
                "descendants_of_any_player_or_pick",
                "ancestry_of_any_player_or_pick",
                "all_assets_from_any_trade",
                "current_endpoints_of_any_lineage",
            ],
        },
    }


def trace_asset_descendants(
    root_asset_key,
    lineage_graph,
    max_depth=25,
):
    nodes = {
        n["asset_key"]: n
        for n in lineage_graph.get("nodes", [])
    }
    edges = lineage_graph.get("edges", [])
    forward = lineage_graph.get("forward_edge_index", {})

    visited_assets = {root_asset_key}
    visited_edges = set()
    frontier = [(root_asset_key, 0)]
    terminal_assets = []

    while frontier:
        asset_key, depth = frontier.pop(0)
        outgoing_ids = forward.get(asset_key, [])

        if depth >= max_depth or not outgoing_ids:
            terminal_assets.append(asset_key)
            continue

        progressed = False
        for edge_id in outgoing_ids:
            if edge_id in visited_edges:
                continue
            visited_edges.add(edge_id)
            edge = edges[edge_id]
            target = edge["to_asset_key"]
            progressed = True
            if target not in visited_assets:
                visited_assets.add(target)
                frontier.append((target, depth + 1))

        if not progressed:
            terminal_assets.append(asset_key)

    return {
        "root_asset_key": root_asset_key,
        "descendant_asset_keys": sorted(
            visited_assets - {root_asset_key}
        ),
        "terminal_asset_keys": sorted(set(terminal_assets)),
        "terminal_assets": [
            nodes.get(key, {"asset_key": key})
            for key in sorted(set(terminal_assets))
        ],
        "edge_ids": sorted(visited_edges),
    }


def trace_asset_ancestry(
    target_asset_key,
    lineage_graph,
    max_depth=25,
):
    nodes = {
        n["asset_key"]: n
        for n in lineage_graph.get("nodes", [])
    }
    edges = lineage_graph.get("edges", [])
    backward = lineage_graph.get("backward_edge_index", {})

    visited_assets = {target_asset_key}
    visited_edges = set()
    frontier = [(target_asset_key, 0)]
    roots = []

    while frontier:
        asset_key, depth = frontier.pop(0)
        incoming_ids = backward.get(asset_key, [])

        if depth >= max_depth or not incoming_ids:
            roots.append(asset_key)
            continue

        progressed = False
        for edge_id in incoming_ids:
            if edge_id in visited_edges:
                continue
            visited_edges.add(edge_id)
            edge = edges[edge_id]
            source = edge["from_asset_key"]
            progressed = True
            if source not in visited_assets:
                visited_assets.add(source)
                frontier.append((source, depth + 1))

        if not progressed:
            roots.append(asset_key)

    return {
        "target_asset_key": target_asset_key,
        "ancestor_asset_keys": sorted(
            visited_assets - {target_asset_key}
        ),
        "root_asset_keys": sorted(set(roots)),
        "root_assets": [
            nodes.get(key, {"asset_key": key})
            for key in sorted(set(roots))
        ],
        "edge_ids": sorted(visited_edges),
    }


def build_trade_lineage_index(
    trade_asset_index,
    lineage_graph,
):
    result = []

    for trade in trade_asset_index["trades"]:
        side_rows = []

        for side in trade.get("sides", []):
            roots = [
                a["asset_key"]
                for a in side.get("sent_assets", [])
                if a.get("asset_key")
            ]
            traces = [
                trace_asset_descendants(
                    root,
                    lineage_graph,
                )
                for root in roots
            ]

            side_rows.append({
                "user_id": side.get("user_id"),
                "manager": side.get("manager"),
                "team_name": side.get("team_name"),
                "sent_assets": side.get("sent_assets"),
                "received_assets": side.get("received_assets"),
                "sent_asset_descendant_traces": traces,
            })

        result.append({
            "transaction_id": trade.get("transaction_id"),
            "season": trade.get("season"),
            "created": trade.get("created"),
            "created_utc": trade.get("created_utc"),
            "phase": trade.get("phase"),
            "sides": side_rows,
        })

    return result


def build_lineage_validation(
    draft_conversion_index,
    lineage_graph,
):
    node_map = {
        n["asset_key"]: n
        for n in lineage_graph.get("nodes", [])
    }

    conversions = draft_conversion_index["conversions"]
    unresolved_past_picks = []

    # Every resolved past pick should explicitly be consumed in a draft.
    for c in conversions:
        key = c["pick_asset_key"]
        node = node_map.get(key, {})
        if node.get("current_status") != "consumed_in_draft":
            unresolved_past_picks.append(key)

    return {
        "resolved_pick_to_player_conversions": len(conversions),
        "unresolved_resolved_pick_status_errors": unresolved_past_picks,
        "validation_passed": not unresolved_past_picks,
        "note": (
            "This validates that every pick for which we found an exact "
            "draft conversion is no longer represented as a live pick."
        ),
    }

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    nfl_state = sleeper("/state/nfl")
    history = build_history()
    players = build_compact_player_map(history)
    trade_analytics = build_trade_analytics(history, players)
    draft_analytics = build_draft_analytics(history, players)
    acquisition_analytics = build_acquisition_analytics(history, players)
    advanced_profiles = build_advanced_owner_profiles(history, players)
    pick_provenance = build_pick_provenance(history)
    pretrade_context = reconstruct_pretrade_roster_context(
        history, players
    )
    asset_lineage = build_asset_lineage_graph(history, players)
    outcome_proxies = build_outcome_proxies(
        history,
        players,
        draft_analytics,
        acquisition_analytics,
    )
    league_market = build_league_market_summary(trade_analytics)
    mahomes_tree = build_named_owner_trade_tree(
        history,
        players,
        "MochaSmev",
        "Patrick Mahomes",
    )
    analysis_manifest = build_analysis_manifest()
    draft_pick_conversion_index = build_draft_pick_conversion_index(
        history,
        players,
    )
    trade_asset_index = build_trade_asset_index(
        history,
        players,
    )
    universal_lineage = build_generic_asset_lineage(
        history,
        players,
        trade_asset_index,
        draft_pick_conversion_index,
    )
    trade_lineage_index = build_trade_lineage_index(
        trade_asset_index,
        universal_lineage,
    )
    lineage_validation = build_lineage_validation(
        draft_pick_conversion_index,
        universal_lineage,
    )

    # Generic Mahomes trace is now only a demonstration/query result,
    # not special lineage logic.
    mahomes_asset_key = None
    for pid, player in players.items():
        if (
            str(player.get("full_name") or "").lower()
            == "patrick mahomes"
        ):
            mahomes_asset_key = make_player_key(pid)
            break

    generic_mahomes_trace = (
        trace_asset_descendants(
            mahomes_asset_key,
            universal_lineage,
        )
        if mahomes_asset_key
        else {"error": "Patrick Mahomes player key not found"}
    )

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
    write_json("owner_behavior_profiles.json", advanced_profiles["owner_behavior_profiles"])
    write_json("jimmy_counterparty_profiles.json", advanced_profiles["jimmy_counterparty_profiles"])
    write_json("pick_provenance.json", pick_provenance)
    write_json("pretrade_roster_context.json", pretrade_context)
    write_json("asset_lineage_graph.json", asset_lineage)
    write_json(
        "draft_outcome_proxy_ledger.json",
        outcome_proxies["draft_outcome_proxy_ledger"],
    )
    write_json(
        "owner_draft_outcome_proxy.json",
        outcome_proxies["owner_draft_outcome_proxy"],
    )
    write_json(
        "waiver_outcome_proxy_ledger.json",
        outcome_proxies["waiver_outcome_proxy_ledger"],
    )
    write_json(
        "owner_waiver_outcome_proxy.json",
        outcome_proxies["owner_waiver_outcome_proxy"],
    )
    write_json("league_market_summary.json", league_market)
    write_json("patrick_mahomes_mocha_trade_tree.json", mahomes_tree)
    write_json("analysis_manifest.json", analysis_manifest)
    write_json(
        "draft_pick_conversion_index.json",
        draft_pick_conversion_index["conversions"],
    )
    write_json(
        "trade_asset_index.json",
        trade_asset_index["trades"],
    )
    write_json(
        "asset_lineage_nodes.json",
        universal_lineage["nodes"],
    )
    write_json(
        "asset_lineage_edges.json",
        universal_lineage["edges"],
    )
    write_json(
        "asset_lineage_forward_index.json",
        universal_lineage["forward_edge_index"],
    )
    write_json(
        "asset_lineage_backward_index.json",
        universal_lineage["backward_edge_index"],
    )
    write_json(
        "trade_lineage_index.json",
        trade_lineage_index,
    )
    write_json(
        "lineage_validation.json",
        lineage_validation,
    )
    write_json(
        "patrick_mahomes_generic_trace.json",
        generic_mahomes_trace,
    )

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

    print(
        f"Asset-lineage edges captured: "
        f"{len(asset_lineage['edges'])}"
    )

    print(
        f"Pick provenance records captured: "
        f"{len(pick_provenance)}"
    )

    if mahomes_tree.get("error"):
        print(
            f"Mahomes trade tree warning: "
            f"{mahomes_tree['error']}"
        )
    else:
        print(
            f"Mahomes tree descendant nodes: "
            f"{len(mahomes_tree.get('nodes', []))}"
        )

    print(
        f"Exact draft pick->player conversions: "
        f"{len(draft_pick_conversion_index['conversions'])}"
    )
    print(
        f"Universal lineage nodes/edges: "
        f"{len(universal_lineage['nodes'])}/"
        f"{len(universal_lineage['edges'])}"
    )
    print(
        f"Lineage validation passed: "
        f"{lineage_validation['validation_passed']}"
    )


if __name__ == "__main__":
    main()
