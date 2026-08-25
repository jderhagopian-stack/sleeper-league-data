#!/usr/bin/env python3
"""FSFFL GM 3.0 Team Improvement Lab 1.2.

Adds true waiver/free-agent simulation. The canonical Simulator 1.0 projection
file intentionally covers FSFFL-rostered players only, so unowned players are
discovered from the full normalized FantasyPros redraft ranking source and are
given an ephemeral, audit-friendly weekly projection calibrated from nearby
rostered players at the same position and similar preseason ECR.

No canonical projection file is modified. During waiver evaluation, the added
candidate is protected from automatic roster legalization so the optimizer
compares ADD candidate + DROP weakest incumbent against HOLD, rather than
trivially adding and immediately cutting the same player.
"""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

BASE = Path(__file__).resolve().parent / "run_team_improvement_lab.py"
MODEL_VERSION = "FSFFL-GM-Team-Improvement-Lab-1.2"


def load_base():
    spec = importlib.util.spec_from_file_location("team_improvement_lab_base12", BASE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {BASE}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def redraft_rows(base, season):
    path = base.DATA / "simulator" / str(season) / "sources" / "normalized_draft_rankings.json"
    doc = base.load_json(path, {}) or {}
    best = {}
    priority = {"redraft-op": 0, "redraft-overall": 1}
    for row in doc.get("players") or []:
        sid = str(row.get("sleeper_id") or "")
        page = str(row.get("page_type") or "")
        ecr = base.sf(row.get("ecr"), 9999)
        pos = str(row.get("position") or "")
        if not sid or page not in priority or pos not in {"QB", "RB", "WR", "TE"} or ecr >= 9999:
            continue
        key = (priority[page], ecr)
        if sid not in best or key < best[sid][0]:
            best[sid] = (key, row)
    return {sid: row for sid, (_, row) in best.items()}


def calibrated_projection(base, pid, row, redraft, owned, players, projections):
    pos = str(row.get("position") or ((players or {}).get(pid) or {}).get("position") or "")
    target_ecr = base.sf(row.get("ecr"), 9999)
    source_players = (projections or {}).get("players") or {}
    comparables = []
    for cid, crow in redraft.items():
        cid = str(cid)
        if cid not in owned or cid not in source_players:
            continue
        cpos = str(crow.get("position") or ((players or {}).get(cid) or {}).get("position") or "")
        if cpos != pos:
            continue
        cecr = base.sf(crow.get("ecr"), 9999)
        if cecr >= 9999:
            continue
        comparables.append((abs(cecr - target_ecr), cid, cecr))
    comparables.sort()
    comparables = comparables[:5]
    if not comparables:
        return None
    weeks = {}
    all_weeks = sorted({w for _, cid, _ in comparables for w in ((source_players.get(cid) or {}).get("weeks") or {}).keys()}, key=lambda x: int(x))
    for week in all_weeks:
        vals = []
        for dist, cid, cecr in comparables:
            wr = (((source_players.get(cid) or {}).get("weeks") or {}).get(str(week)) or {})
            if not wr:
                continue
            weight = 1.0 / (1.0 + dist)
            vals.append((weight, wr))
        if not vals:
            continue
        sw = sum(x[0] for x in vals)
        def avg(key, default=0.0):
            return sum(w * base.sf(r.get(key), default) for w, r in vals) / max(sw, 1e-9)
        mean = avg("mean", avg("median"))
        sd = max(.1, avg("sd", max(2.0, mean * .32)))
        active = max(0.0, min(1.0, avg("active_probability", 1.0)))
        weeks[str(week)] = {"mean": round(mean, 4), "median": round(mean, 4), "sd": round(sd, 4), "active_probability": round(active, 5)}
    if not weeks:
        return None
    name = row.get("player_name") or ((players or {}).get(pid) or {}).get("full_name") or f"player:{pid}"
    return {
        "name": name,
        "position": pos,
        "weeks": weeks,
        "calibration": {
            "method": "same_position_nearest_preseason_ecr_inverse_distance",
            "target_ecr": target_ecr,
            "source_page_type": row.get("page_type"),
            "comparables": [{"player_id": cid, "ecr": cecr, "ecr_distance": dist} for dist, cid, cecr in comparables],
            "ephemeral_only": True,
        },
    }


def waiver_candidates(base, focus_uid, players_catalog, model_inputs, limit):
    _, _, rosters, _, players, season, projections, _ = model_inputs
    owned = base.owner_map(rosters)
    redraft = redraft_rows(base, season)
    rows = []
    for pid, rr in redraft.items():
        pid = str(pid)
        if pid in owned:
            continue
        profile = calibrated_projection(base, pid, rr, redraft, owned, players, projections)
        if not profile:
            continue
        catalog = players_catalog.get(f"player:{pid}") or {}
        pos = profile["position"]
        means = [base.sf(x.get("mean")) * base.sf(x.get("active_probability"), 1.0) for x in profile["weeks"].values()]
        projected = sum(means) / max(1, len(means))
        ecr = base.sf(rr.get("ecr"), 9999)
        asset = {
            "asset_id": f"player:{pid}", "asset_type": "player", "player_id": pid,
            "name": catalog.get("name") or profile.get("name") or f"player:{pid}", "position": pos,
            "market_dynasty": base.sf(catalog.get("market_dynasty")), "market_redraft": base.sf(catalog.get("market_redraft")),
            "fsffl_value": base.sf(catalog.get("fsffl_value")), "owner_user_id": None,
            "market_value_available": bool(catalog),
        }
        ecr_signal = max(0.0, 350.0 - min(350.0, ecr))
        screen = projected * 240 + ecr_signal * 3.0 + base.sf(asset.get("market_dynasty")) * .25
        rows.append({
            "channel": "WAIVER", "target": asset,
            "projected_weekly_mean": round(projected, 3), "preseason_ecr": ecr,
            "pre_screen_score": round(screen, 2),
            "waiver_discovery_source": "normalized_full_redraft_rankings",
            "synthetic_projection": profile,
        })
    rows.sort(key=lambda x: x["pre_screen_score"], reverse=True)
    return rows[:limit]


def main():
    base = load_base()
    base.MODEL_VERSION = MODEL_VERSION
    saved_evaluate = base.evaluate_row

    base.waiver_candidates = lambda focus_uid, players_catalog, model_inputs, limit: waiver_candidates(
        base, focus_uid, players_catalog, model_inputs, limit
    )

    def simulate_actions_protect_add(dl, v13, rosteraware, model_inputs, baseline_lineups, baseline,
                                     focus_uid, actions, sims, seed):
        simmod, league, canonical_rosters, users, players, season, projections, raw_schedule = model_inputs
        hypothetical, _ = dl.apply_actions(canonical_rosters, actions)
        touched = dl.touched_users(focus_uid, actions)
        protected = {}
        for action in actions:
            if str(action.get("type") or "").lower() == "add":
                uid = str(action.get("user_id"))
                ids = action.get("players") or ([action.get("player_id")] if action.get("player_id") is not None else [])
                protected.setdefault(uid, set()).update(str(x) for x in ids)
        legal, resolutions, cut_actions = rosteraware.legalize_trade_rosters(
            dl, canonical_rosters, hypothetical, touched, league, players,
            protected_player_ids_by_uid=protected,
        )
        effective_actions = list(actions) + list(cut_actions)
        lineups, reoptimized = base.fast_reoptimize(
            v13, dl, simmod, baseline_lineups, legal, touched, league, users, players, projections
        )
        hyp = dl.simulate_from_lineups(simmod, league, legal, users, raw_schedule, lineups, sims, seed)
        bidx, hidx = base.team_index(baseline), base.team_index(hyp)
        b, h = bidx[str(focus_uid)], hidx[str(focus_uid)]
        st = dl.strategic_summary(str(focus_uid), effective_actions)
        return {
            "focus_before": b,
            "focus_after": h,
            "focus_delta": {
                "expected_wins": base.delta(b.get("expected_wins"), h.get("expected_wins")),
                "expected_points_for": base.delta(b.get("expected_points_for"), h.get("expected_points_for")),
                "playoff_probability": base.delta(b.get("playoff_probability"), h.get("playoff_probability")),
                "bye_probability": base.delta(b.get("bye_probability"), h.get("bye_probability")),
                "championship_probability": base.delta(b.get("championship_probability"), h.get("championship_probability")),
            },
            "strategic": st,
            "roster_resolution": resolutions,
            "effective_actions": effective_actions,
            "teams_reoptimized": reoptimized,
            "simulation_count": sims,
        }

    base.simulate_actions = simulate_actions_protect_add

    def evaluate_with_waiver_projection(row, focus_uid, dl, v13, rosteraware, model_inputs, baseline_lineups, baseline, sims, seed):
        if row.get("channel") != "WAIVER" or not row.get("synthetic_projection"):
            return saved_evaluate(row, focus_uid, dl, v13, rosteraware, model_inputs, baseline_lineups, baseline, sims, seed)
        mi = list(model_inputs)
        projections = copy.deepcopy(mi[6])
        projections.setdefault("players", {})[str(row["target"]["player_id"])] = copy.deepcopy(row["synthetic_projection"])
        mi[6] = projections
        return saved_evaluate(row, focus_uid, dl, v13, rosteraware, tuple(mi), baseline_lineups, baseline, sims, seed)

    base.evaluate_row = evaluate_with_waiver_projection
    base.main()


if __name__ == "__main__":
    main()
