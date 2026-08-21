import json
import urllib.request
from pathlib import Path

LEAGUE_ID = "1312071960615731200"
SEASON = "2026"
REGULAR_SEASON_WEEKS = 14
BASE_URL = "https://api.sleeper.app/v1"
OUTPUT_PATH = Path("data") / "stats" / "fsffl" / SEASON / "league_matchups_raw.json"


def get_json(url):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "sleeper-league-data/1.0"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_week(week):
    url = f"{BASE_URL}/league/{LEAGUE_ID}/matchups/{week}"
    print(f"Fetching full schedule week {week}: {url}")
    payload = get_json(url)
    return payload if isinstance(payload, list) else []


def main():
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    schedule = {}
    populated_weeks = 0

    for week in range(1, REGULAR_SEASON_WEEKS + 1):
        rows = fetch_week(week)
        schedule[str(week)] = rows
        if rows:
            populated_weeks += 1

    OUTPUT_PATH.write_text(
        json.dumps(schedule, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    print(
        f"Wrote full FSFFL 2026 schedule to {OUTPUT_PATH} "
        f"({populated_weeks}/{REGULAR_SEASON_WEEKS} weeks populated)"
    )

    # Fail loudly if Sleeper unexpectedly stops returning the future schedule.
    if populated_weeks < REGULAR_SEASON_WEEKS:
        missing = [
            str(w)
            for w in range(1, REGULAR_SEASON_WEEKS + 1)
            if not schedule.get(str(w))
        ]
        raise RuntimeError(
            "Sleeper did not return all scheduled weeks. "
            f"Missing/empty weeks: {', '.join(missing)}"
        )


if __name__ == "__main__":
    main()
