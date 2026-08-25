#!/usr/bin/env python3
"""Build the canonical full fantasy-relevant weekly projection universe.

Simulator 1.0's existing player_weekly_projections.json remains the validated
league-roster projection set. This companion builder preserves those native
projections and extends coverage to mapped QB/RB/WR/TE players in the current
redraft source so waiver/free-agent decisions use a stable canonical input
instead of per-query synthetic projections.

Unrostered-player weekly distributions are calibrated from same-position
players with nearby preseason ECR. Existing rostered projections are copied
unchanged. The output is deterministic, audited, and season-scoped.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

DATA = Path("data")
MODEL_VERSION = "FSFFL-Full-Projection-Universe-1.0"
SUPPORTED_POSITIONS = {"QB", "RB", "WR", "TE"}
DEFAULT_MAX_ECR = 450.0
DEFAULT_COMPARABLES = 7


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def sf(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def select_redraft_rows(doc: Dict[str, Any], max_ecr: float) -> Dict[str, Dict[str, Any]]:
    best: Dict[str, Tuple[Tuple[int, float], Dict[str, Any]]] = {}
    priority = {"redraft-op": 0, "redraft-overall": 1}
    for row in doc.get("players") or []:
        sid = str(row.get("sleeper_id") or "")
        pos = str(row.get("position") or "")
        page = str(row.get("page_type") or "")
        ecr = sf(row.get("ecr"), 9999.0)
        if not sid or pos not in SUPPORTED_POSITIONS or page not in priority or ecr > max_ecr:
            continue
        key = (priority[page], ecr)
        if sid not in best or key < best[sid][0]:
            best[sid] = (key, row)
    return {sid: row for sid, (_, row) in best.items()}


def ecr_index(redraft: Dict[str, Dict[str, Any]]) -> Dict[str, float]:
    return {sid: sf(row.get("ecr"), 9999.0) for sid, row in redraft.items()}


def build_profile(pid: str, row: Dict[str, Any], redraft: Dict[str, Dict[str, Any]],
                  native_players: Dict[str, Dict[str, Any]], players: Dict[str, Any], n_comp: int):
    pos = str(row.get("position") or ((players.get(pid) or {}).get("position")) or "")
    target_ecr = sf(row.get("ecr"), 9999.0)
    ecrs = ecr_index(redraft)
    comps: List[Tuple[float, str, float]] = []
    for cid, profile in native_players.items():
        if cid == pid or cid not in ecrs:
            continue
        cpos = str(profile.get("position") or ((players.get(cid) or {}).get("position")) or "")
        if cpos != pos or not (profile.get("weeks") or {}):
            continue
        cecr = ecrs[cid]
        if cecr >= 9999:
            continue
        comps.append((abs(cecr - target_ecr), cid, cecr))
    comps.sort(key=lambda x: (x[0], x[2], x[1]))
    comps = comps[:max(3, n_comp)]
    if not comps:
        return None

    all_weeks = sorted({str(w) for _, cid, _ in comps for w in (native_players[cid].get("weeks") or {}).keys()}, key=lambda x: int(x))
    weeks = {}
    for week in all_weeks:
        vals = []
        for dist, cid, _ in comps:
            wr = (native_players[cid].get("weeks") or {}).get(week) or {}
            if not wr:
                continue
            weight = 1.0 / (1.0 + dist)
            vals.append((weight, wr))
        if not vals:
            continue
        sw = sum(w for w, _ in vals)
        def avg(key, default=0.0):
            return sum(w * sf(r.get(key), default) for w, r in vals) / max(sw, 1e-9)
        mean = avg("mean", avg("median"))
        median = avg("median", mean)
        sd = max(0.1, avg("sd", max(2.0, abs(mean) * 0.32)))
        active = max(0.0, min(1.0, avg("active_probability", 1.0)))
        weeks[week] = {
            "mean": round(mean, 4),
            "median": round(median, 4),
            "sd": round(sd, 4),
            "active_probability": round(active, 5),
        }
    if not weeks:
        return None
    name = row.get("player_name") or (players.get(pid) or {}).get("full_name") or f"player:{pid}"
    return {
        "name": name,
        "position": pos,
        "weeks": weeks,
        "projection_provenance": {
            "method": "same_position_nearest_preseason_ecr_inverse_distance",
            "target_ecr": target_ecr,
            "source_page_type": row.get("page_type"),
            "source_ecr_type": row.get("ecr_type"),
            "comparables": [
                {"player_id": cid, "ecr": cecr, "ecr_distance": round(dist, 4)}
                for dist, cid, cecr in comps
            ],
            "native_rostered_projection": False,
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-ecr", type=float, default=DEFAULT_MAX_ECR)
    ap.add_argument("--comparables", type=int, default=DEFAULT_COMPARABLES)
    ap.add_argument("--output")
    ap.add_argument("--audit-output")
    a = ap.parse_args()

    league = load_json(DATA / "league.json", {}) or {}
    players = load_json(DATA / "players.json", {}) or {}
    season = str(league.get("season") or "")
    if not season:
        raise RuntimeError("Missing active season in data/league.json")
    root = DATA / "simulator" / season
    native_path = root / "inputs" / "player_weekly_projections.json"
    ranks_path = root / "sources" / "normalized_draft_rankings.json"
    native = load_json(native_path, {}) or {}
    ranks = load_json(ranks_path, {}) or {}
    native_players = native.get("players") or {}
    if not native_players:
        raise RuntimeError(f"Missing native projection players: {native_path}")

    redraft = select_redraft_rows(ranks, a.max_ecr)
    full_players = json.loads(json.dumps(native_players))
    added, missing = [], []
    for pid, row in sorted(redraft.items(), key=lambda kv: (sf(kv[1].get("ecr"), 9999), kv[0])):
        if pid in full_players:
            full_players[pid].setdefault("projection_provenance", {"native_rostered_projection": True})
            continue
        profile = build_profile(pid, row, redraft, native_players, players, a.comparables)
        if profile:
            full_players[pid] = profile
            added.append(pid)
        else:
            missing.append(pid)

    generated = now_utc()
    output = {
        "model_version": MODEL_VERSION,
        "season": season,
        "generated_at_utc": generated,
        "source": "validated rostered Simulator projections + normalized redraft ECR calibrated full-universe extension",
        "players": full_players,
        "coverage": {
            "native_projection_players": len(native_players),
            "fantasy_relevant_redraft_players": len(redraft),
            "added_full_universe_players": len(added),
            "final_projection_players": len(full_players),
            "unresolved_players": len(missing),
            "max_ecr": a.max_ecr,
        },
        "policy": {
            "native_rostered_projections_preserved_unchanged": True,
            "dynasty_market_value_not_used_as_scoring_projection": True,
            "full_universe_is_canonical_season_input": True,
        },
    }
    audit = {
        "model_version": MODEL_VERSION,
        "season": season,
        "generated_at_utc": generated,
        "coverage": output["coverage"],
        "added_player_ids": added,
        "unresolved_player_ids": missing,
    }
    out = Path(a.output) if a.output else root / "inputs" / "player_weekly_projections_full.json"
    audit_out = Path(a.audit_output) if a.audit_output else root / "outputs" / "full_projection_universe_audit.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    audit_out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, indent=2, sort_keys=True), encoding="utf-8")
    audit_out.write_text(json.dumps(audit, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"model_version": MODEL_VERSION, "season": season, **output["coverage"], "output": str(out)}, indent=2))


if __name__ == "__main__":
    main()
