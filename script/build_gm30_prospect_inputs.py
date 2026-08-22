#!/usr/bin/env python3
"""
FSFFL GM 3.0 — Automatic Prospect Input Builder v1

Purpose
-------
Populate data/gm3_prospect_inputs.json automatically so the prospect engine is
never dependent on hand-entering the current rookie class.

Lifecycle
---------
- Current NFL rookies (years_exp == 0, or draft_year == active season) are
  automatically included.
- Any previously supplied richer prospect metrics are merged and preserved.
- Future-class/manual prospect rows already present in the input file are kept.
- A frozen prior archive is maintained in data/gm3_prospect_priors.json so a
  player's prospect profile can later be referenced by the Year 2-4 emerging
  value model even after he stops being an active rookie.

Market rookie rank
------------------
Derived from the existing FSFFL dynasty market feed, ranking only the current
rookie QB/RB/WR/TE cohort by market_dynasty. This is a market-comparison field,
not a prospect-quality feature.

Data integrity
--------------
We NEVER substitute FSFFL rookie-draft position for NFL draft capital.
NFL draft capital is used only when players.json explicitly provides a valid
NFL draft pick/round.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

DATA = Path("data")
LEAGUE = DATA / "league.json"
PLAYERS = DATA / "players.json"
VALUES = DATA / "fsffl_asset_values.json"
INPUTS = DATA / "gm3_prospect_inputs.json"
PRIORS = DATA / "gm3_prospect_priors.json"

POSITIONS = {"QB", "RB", "WR", "TE"}


def load(path, default):
    try:
        with Path(path).open("r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def save(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)


def as_int(x):
    try:
        if x is None or isinstance(x, bool):
            return None
        return int(float(x))
    except (TypeError, ValueError):
        return None


def player_dict(payload):
    if isinstance(payload, dict):
        return {
            str(k): v
            for k, v in payload.items()
            if isinstance(v, dict)
        }
    if isinstance(payload, list):
        return {
            str(x.get("player_id")): x
            for x in payload
            if isinstance(x, dict) and x.get("player_id") is not None
        }
    return {}


def existing_rows(payload):
    if not isinstance(payload, dict):
        return []
    rows = payload.get("prospects") or []
    return [dict(x) for x in rows if isinstance(x, dict)]


def row_key(row):
    pid = row.get("player_id") or row.get("sleeper_id")
    if pid is not None:
        return f"id:{pid}"
    name = str(row.get("name") or "").strip().lower()
    cls = str(row.get("class") or "")
    return f"name:{name}|class:{cls}"


def is_current_rookie(p, season):
    pos = str(p.get("position") or "").upper()
    if pos not in POSITIONS:
        return False

    years = as_int(p.get("years_exp"))
    draft_year = as_int(p.get("draft_year"))

    # Sleeper years_exp == 0 is the strongest current-rookie indicator.
    if years == 0:
        return True

    # Fallback for feeds that populate draft year before years_exp is updated.
    if draft_year == season:
        return True

    return False


def nfl_draft_capital(p, season):
    """Return explicit NFL overall pick only when the source looks trustworthy."""
    draft_year = as_int(p.get("draft_year"))
    draft_round = as_int(p.get("draft_round"))
    draft_pick = as_int(p.get("draft_pick"))

    if draft_year is not None and draft_year != season:
        return None, draft_round

    if draft_pick is not None and 1 <= draft_pick <= 257:
        return draft_pick, draft_round

    return None, draft_round


def market_maps(values_payload):
    rows = (
        values_payload.get("players") or []
        if isinstance(values_payload, dict)
        else []
    )
    by_id = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        pid = row.get("player_id")
        if pid is None:
            continue
        by_id[str(pid)] = row
    return by_id


def main():
    league = load(LEAGUE, {}) or {}
    season = as_int(league.get("season"))
    if season is None:
        raise SystemExit("Cannot resolve active season from data/league.json")

    players = player_dict(load(PLAYERS, {}))
    values_by_id = market_maps(load(VALUES, {}))

    previous_payload = load(INPUTS, {"prospects": []})
    previous = existing_rows(previous_payload)
    previous_by_key = {row_key(x): x for x in previous}

    # Keep future/manual rows even if Sleeper does not know them yet.
    preserved_future = []
    for row in previous:
        cls = as_int(row.get("class"))
        pid = row.get("player_id") or row.get("sleeper_id")
        p = players.get(str(pid)) if pid is not None else None

        if cls is not None and cls > season:
            preserved_future.append(dict(row))
        elif p is None and cls is not None and cls >= season:
            preserved_future.append(dict(row))

    rookies = []
    for pid, p in players.items():
        if not is_current_rookie(p, season):
            continue

        pos = str(p.get("position") or "").upper()
        name = p.get("full_name") or p.get("name")
        if not name:
            continue

        draft_pick, draft_round = nfl_draft_capital(p, season)
        market = values_by_id.get(str(pid), {})

        base = {
            "player_id": str(pid),
            "sleeper_id": str(pid),
            "name": name,
            "position": pos,
            "class": season,
            "prospect_stage": "NFL_ROOKIE_ACTIVE_PRIOR",
            "rookie_eligibility_basis": (
                "years_exp_0"
                if as_int(p.get("years_exp")) == 0
                else "draft_year_matches_active_season"
            ),
            "nfl_team": p.get("team"),
            "age": p.get("age"),
            "birth_date": p.get("birth_date"),
            "draft_capital_pick": draft_pick,
            "nfl_draft_round": draft_round,
            "market_dynasty": market.get("market_dynasty"),
            "market_redraft": market.get("market_redraft"),
            "market_overall_rank": market.get("market_rank"),
            "market_source": (
                "data/fsffl_asset_values.json"
                if market
                else None
            ),
            "automatic_source_fields": [
                "Sleeper player metadata",
                "FSFFL dynasty market feed",
            ],
        }

        # Preserve richer metrics previously supplied for this exact player.
        prior = previous_by_key.get(f"id:{pid}")
        if prior:
            merged = dict(base)
            for k, v in prior.items():
                if v is not None and k not in {
                    "market_rookie_rank",
                    "market_dynasty",
                    "market_redraft",
                    "market_overall_rank",
                    "nfl_team",
                    "age",
                    "birth_date",
                }:
                    merged[k] = v
            base = merged

        rookies.append(base)

    # Market rookie rank is explicitly cohort-relative.
    market_ranked = sorted(
        [
            x for x in rookies
            if x.get("market_dynasty") is not None
        ],
        key=lambda x: float(x.get("market_dynasty") or 0.0),
        reverse=True,
    )
    for i, row in enumerate(market_ranked, 1):
        row["market_rookie_rank"] = i

    # Rows with no market value remain present; they simply lack a market rank.
    ranked_ids = {str(x.get("player_id")) for x in market_ranked}
    for row in rookies:
        if str(row.get("player_id")) not in ranked_ids:
            row["market_rookie_rank"] = None

    # De-duplicate preserved future rows against current rookies.
    current_keys = {row_key(x) for x in rookies}
    preserved_future = [
        x for x in preserved_future
        if row_key(x) not in current_keys
    ]

    prospects = rookies + preserved_future

    output = {
        "_instructions": {
            "purpose": (
                "Automatically generated prospect-input contract. Current NFL "
                "rookies are included automatically; richer/future prospect "
                "metrics may be merged into existing rows and will be preserved."
            ),
            "lifecycle": (
                "Current rookies remain prospect priors during their rookie "
                "season. Frozen priors are archived for later Year 2-4 models."
            ),
            "draft_capital_policy": (
                "Never infer NFL draft capital from FSFFL rookie draft position."
            ),
        },
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_version": "FSFFL-GM-3.0-Prospect-Input-Builder-v1",
        "season": season,
        "current_rookie_count": len(rookies),
        "preserved_future_prospect_count": len(preserved_future),
        "prospects": prospects,
    }
    save(INPUTS, output)

    # Frozen prior archive: append/update current rookies; never discard history.
    prior_payload = load(
        PRIORS,
        {
            "model_version": "FSFFL-GM-3.0-Prospect-Prior-Archive-v1",
            "prospects": {},
        },
    )
    if not isinstance(prior_payload, dict):
        prior_payload = {}
    archive = prior_payload.get("prospects")
    if not isinstance(archive, dict):
        archive = {}

    now = datetime.now(timezone.utc).isoformat()
    for row in rookies:
        pid = str(row.get("player_id"))
        existing = archive.get(pid, {})
        entry = dict(existing) if isinstance(existing, dict) else {}

        # First snapshot stays available as the true frozen baseline.
        if "first_snapshot" not in entry:
            entry["first_snapshot"] = dict(row)
            entry["first_snapshot_at_utc"] = now

        entry["latest_snapshot"] = dict(row)
        entry["latest_snapshot_at_utc"] = now
        entry["rookie_class"] = season
        archive[pid] = entry

    prior_payload.update(
        {
            "generated_at_utc": now,
            "model_version": "FSFFL-GM-3.0-Prospect-Prior-Archive-v1",
            "prospect_count": len(archive),
            "prospects": archive,
        }
    )
    save(PRIORS, prior_payload)

    print(
        "GM 3.0 prospect inputs: "
        f"{len(rookies)} current rookies + "
        f"{len(preserved_future)} preserved future/manual prospects "
        f"-> {INPUTS}"
    )
    print(
        f"Frozen prospect prior archive: {len(archive)} players -> {PRIORS}"
    )


if __name__ == "__main__":
    main()
