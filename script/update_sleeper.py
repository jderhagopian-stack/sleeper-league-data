import json
import os
import time
import urllib.request
from pathlib import Path

STARTING_LEAGUE_ID = "1312071960615731200"
BASE_URL = "https://api.sleeper.app/v1"
OUTPUT_DIR = Path("data")
MAX_HISTORY_SEASONS = 10


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

    # Sleeper transaction "round" corresponds to the fantasy week.
    # Round 0 is included because some offseason activity can appear there.
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

    # De-duplicate transactions in case Sleeper exposes one more than once.
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
            # Team defenses or unknown/retired IDs can appear in rosters.
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


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    nfl_state = sleeper("/state/nfl")
    history = build_history()
    players = build_compact_player_map(history)

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


if __name__ == "__main__":
    main()
