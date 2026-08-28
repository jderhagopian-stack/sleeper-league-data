#!/usr/bin/env python3
"""Assess whether FSFFL draft history can identify future-pick economics.

This audit intentionally does NOT fit a pick-value curve.  It inventories cohort
comparability, follow-up/censoring and realized scoring coverage first.  A
small or mixed draft sample must not silently become a calibration dataset.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = DATA / "audit"
OUT.mkdir(parents=True, exist_ok=True)

MODEL_VERSION = "FSFFL-Pick-Outcome-Readiness-1.0"
LAST_COMPLETED_SEASON = 2025


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_season_rows(year: int):
    p = DATA / "stats" / "fsffl" / str(year) / "player_season_fsffl.json"
    if not p.exists() or p.stat().st_size == 0:
        return []
    obj = load_json(p)
    return obj if isinstance(obj, list) else []


def player_points_by_year(year: int):
    rows = load_season_rows(year)
    out = {}
    for r in rows:
        pid = str(r.get("player_id") or "")
        if not pid or pid.startswith("TEAM_"):
            continue
        out[pid] = {
            "points": float(r.get("fsffl_points") or 0.0),
            "ppg": float(r.get("fsffl_ppg") or 0.0),
            "games": int(r.get("games_with_stats") or 0),
            "position": r.get("position"),
        }
    return out


def main():
    draft_path = DATA / "draft_ledger.json"
    drafts = load_json(draft_path) if draft_path.exists() else []
    drafts = drafts if isinstance(drafts, list) else []

    seasons = sorted({int(r["season"]) for r in drafts if str(r.get("season", "")).isdigit()})
    stats = {y: player_points_by_year(y) for y in range(min(seasons or [2022]) - 1, LAST_COMPLETED_SEASON + 1)}

    by_season = defaultdict(list)
    for r in drafts:
        s = str(r.get("season") or "")
        if s.isdigit():
            by_season[int(s)].append(r)

    cohort_rows = []
    eligible_one_year = 0
    eligible_two_year = 0
    eligible_three_year = 0
    mixed_prior_production = 0
    all_completed_draft_rows = 0

    for season in seasons:
        rows = by_season[season]
        completed = [r for r in rows if str(r.get("draft_status") or "").lower() == "complete"]
        all_completed_draft_rows += len(completed)
        player_ids = [str(r.get("player_id") or "") for r in completed if r.get("player_id")]
        prior = stats.get(season - 1, {})
        prior_producers = [pid for pid in player_ids if prior.get(pid, {}).get("games", 0) > 0]
        mixed_prior_production += len(prior_producers)

        followups = {}
        for horizon in (0, 1, 2, 3):
            y = season + horizon
            if y > LAST_COMPLETED_SEASON:
                followups[str(horizon)] = {"season": y, "censored": True, "matched_players": 0}
                continue
            table = stats.get(y, {})
            matched = sum(1 for pid in player_ids if pid in table)
            followups[str(horizon)] = {
                "season": y,
                "censored": False,
                "matched_players": matched,
                "drafted_players": len(player_ids),
                "coverage": round(matched / len(player_ids), 4) if player_ids else 0.0,
            }

        if season + 1 <= LAST_COMPLETED_SEASON:
            eligible_one_year += len(player_ids)
        if season + 2 <= LAST_COMPLETED_SEASON:
            eligible_two_year += len(player_ids)
        if season + 3 <= LAST_COMPLETED_SEASON:
            eligible_three_year += len(player_ids)

        cohort_rows.append({
            "season": season,
            "draft_ids": sorted({str(r.get("draft_id") or "") for r in completed}),
            "pick_count": len(completed),
            "rounds": sorted({int(r.get("round")) for r in completed if isinstance(r.get("round"), int)}),
            "draft_types": dict(Counter(str(r.get("draft_type") or "UNKNOWN") for r in completed)),
            "positions": dict(Counter(str(r.get("position") or "UNKNOWN") for r in completed)),
            "prior_season_producer_count": len(prior_producers),
            "prior_season_producer_share": round(len(prior_producers) / len(player_ids), 4) if player_ids else 0.0,
            "followup": followups,
        })

    comparable_completed = [x for x in cohort_rows if x["season"] <= LAST_COMPLETED_SEASON]
    one_year_cohorts = [x for x in comparable_completed if x["season"] + 1 <= LAST_COMPLETED_SEASON]
    two_year_cohorts = [x for x in comparable_completed if x["season"] + 2 <= LAST_COMPLETED_SEASON]

    # Governance rule: do not fit/publish a league-specific pick curve unless
    # there are at least three independent completed cohorts with one-year
    # follow-up AND two cohorts with two-year follow-up.  This is a minimum
    # readiness gate, not evidence that the eventual model is valid.
    minimum_temporal_structure = len(one_year_cohorts) >= 3 and len(two_year_cohorts) >= 2

    # If players with prior-season NFL fantasy production appear in a cohort,
    # the draft ledger alone does not establish that the cohort is a pure
    # rookie-draft sample.  A rookie/veteran eligibility source is required
    # before using slot outcomes as rookie-pick economics.
    mixed_or_unverified = mixed_prior_production > 0 or any(
        not x["draft_ids"] or not x["draft_types"] for x in cohort_rows
    )

    finding = {
        "id": "PICK-OUTCOME-READINESS-001",
        "severity": "HIGH",
        "status": "READY_FOR_MODEL_SELECTION" if minimum_temporal_structure and not mixed_or_unverified else "NOT_READY_FOR_AUTHORITATIVE_CALIBRATION",
        "observation": (
            "FSFFL draft picks can be joined to realized season scoring, but authoritative league-specific slot economics require adequate independent temporal cohorts and verified comparable rookie-draft eligibility. "
            "Prior-season producers in the ledger are treated as evidence that the draft history may include veteran-eligible selections or otherwise mixed cohorts; they are not silently pooled with rookies."
        ),
        "minimum_temporal_structure_met": minimum_temporal_structure,
        "rookie_cohort_purity_verified": not mixed_or_unverified,
        "authoritative_empirical_claim_allowed": bool(minimum_temporal_structure and not mixed_or_unverified),
    }

    payload = {
        "model_version": MODEL_VERSION,
        "purpose": "Audit draft-outcome sample structure before fitting future-pick coefficients.",
        "policy": {
            "retention_proxy_is_not_draft_success": True,
            "future_seasons_are_right_censored": True,
            "rookie_and_veteran_eligible_cohorts_must_not_be_silently_pooled": True,
            "readiness_does_not_equal_validation": True,
        },
        "summary": {
            "draft_rows": len(drafts),
            "completed_draft_rows": all_completed_draft_rows,
            "draft_seasons": seasons,
            "completed_outcome_cutoff": LAST_COMPLETED_SEASON,
            "completed_cohort_count": len(comparable_completed),
            "one_year_followup_cohort_count": len(one_year_cohorts),
            "two_year_followup_cohort_count": len(two_year_cohorts),
            "one_year_followup_pick_count": eligible_one_year,
            "two_year_followup_pick_count": eligible_two_year,
            "three_year_followup_pick_count": eligible_three_year,
            "prior_season_producer_rows": mixed_prior_production,
        },
        "cohorts": cohort_rows,
        "finding": finding,
    }
    (OUT / "pick_outcome_readiness_audit.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload["summary"], indent=2))
    print(json.dumps(finding, indent=2))


if __name__ == "__main__":
    main()
