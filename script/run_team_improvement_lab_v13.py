#!/usr/bin/env python3
"""FSFFL GM 3.0 Team Improvement Lab 1.4.

Consumes the canonical full fantasy-relevant projection universe produced by
build_fsffl_full_projection_universe.py. Waiver/free-agent candidates therefore
use stable season-scoped player projections rather than per-query synthetic
projection generation. Trade evaluation, roster legalization, common-objective
ranking, deep confirmation, and HOLD benchmarking remain inherited from 1.0.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent / "run_team_improvement_lab.py"
MODEL_VERSION = "FSFFL-GM-Team-Improvement-Lab-1.4"
PROJECTION_MODEL_VERSION = "FSFFL-Full-Projection-Universe-1.0"


def load_base():
    spec = importlib.util.spec_from_file_location("team_improvement_lab_base13", BASE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {BASE}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def output_path_from_argv():
    if "--output" not in sys.argv:
        return None
    i = sys.argv.index("--output")
    return Path(sys.argv[i + 1]) if i + 1 < len(sys.argv) else None


def full_projection_doc(base, season):
    path = base.DATA / "simulator" / str(season) / "inputs" / "player_weekly_projections_full.json"
    doc = base.load_json(path, {}) or {}
    if doc.get("model_version") != PROJECTION_MODEL_VERSION:
        raise RuntimeError(f"Expected {PROJECTION_MODEL_VERSION} at {path}; run build_fsffl_full_projection_universe.py first")
    if not (doc.get("players") or {}):
        raise RuntimeError(f"Full projection universe is empty: {path}")
    return doc, path


def waiver_candidates(base, focus_uid, players_catalog, model_inputs, limit):
    _, _, rosters, _, players, season, _, _ = model_inputs
    owned = base.owner_map(rosters)
    full, _ = full_projection_doc(base, season)
    projection_players = full.get("players") or {}
    rows = []
    for pid, profile in projection_players.items():
        pid = str(pid)
        if pid in owned:
            continue
        pos = str(profile.get("position") or ((players or {}).get(pid) or {}).get("position") or "")
        if pos not in {"QB", "RB", "WR", "TE"}:
            continue
        weeks = profile.get("weeks") or {}
        if not weeks:
            continue
        means = [base.sf(v.get("mean", v.get("median"))) * base.sf(v.get("active_probability"), 1.0) for v in weeks.values()]
        projected = sum(means) / max(1, len(means))
        catalog = players_catalog.get(f"player:{pid}") or {}
        asset = {
            "asset_id": f"player:{pid}", "asset_type": "player", "player_id": pid,
            "name": catalog.get("name") or profile.get("name") or ((players or {}).get(pid) or {}).get("full_name") or f"player:{pid}",
            "position": pos,
            "market_dynasty": base.sf(catalog.get("market_dynasty")),
            "market_redraft": base.sf(catalog.get("market_redraft")),
            "fsffl_value": base.sf(catalog.get("fsffl_value")),
            "owner_user_id": None,
            "market_value_available": bool(catalog),
        }
        provenance = profile.get("projection_provenance") or {}
        target_ecr = base.sf(provenance.get("target_ecr"), 9999)
        ecr_signal = max(0.0, 350.0 - min(350.0, target_ecr)) if target_ecr < 9999 else 0.0
        screen = projected * 250 + ecr_signal * 2.5 + base.sf(asset.get("market_dynasty")) * .25
        rows.append({
            "channel": "WAIVER",
            "target": asset,
            "projected_weekly_mean": round(projected, 3),
            "preseason_ecr": None if target_ecr >= 9999 else target_ecr,
            "pre_screen_score": round(screen, 2),
            "waiver_discovery_source": "canonical_full_projection_universe",
            "projection_source_model": full.get("model_version"),
            "native_full_projection": copy.deepcopy(profile),
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

    def evaluate_with_native_projection(row, focus_uid, dl, v13, rosteraware, model_inputs, baseline_lineups, baseline, sims, seed):
        if row.get("channel") != "WAIVER" or not row.get("native_full_projection"):
            return saved_evaluate(row, focus_uid, dl, v13, rosteraware, model_inputs, baseline_lineups, baseline, sims, seed)
        mi = list(model_inputs)
        projections = copy.deepcopy(mi[6])
        projections.setdefault("players", {})[str(row["target"]["player_id"])] = copy.deepcopy(row["native_full_projection"])
        mi[6] = projections
        return saved_evaluate(row, focus_uid, dl, v13, rosteraware, tuple(mi), baseline_lineups, baseline, sims, seed)

    base.evaluate_row = evaluate_with_native_projection
    base.main()

    out = output_path_from_argv()
    if out and out.exists():
        report = json.loads(out.read_text(encoding="utf-8"))
        league = base.load_json(base.DATA / "league.json", {}) or {}
        season = str(league.get("season") or "")
        full, path = full_projection_doc(base, season)
        report["model_version"] = MODEL_VERSION
        report["projection_universe"] = {
            "model_version": full.get("model_version"),
            "path": str(path),
            "coverage": full.get("coverage") or {},
            "waiver_candidates_use_canonical_full_projection": True,
        }
        report.setdefault("policy", {})["waiver_candidates_use_canonical_full_projection_universe"] = True
        report["ranking_calibration"] = {
            "version": "shared-decision-utility-1.0",
            "principle": "Team Improvement and Trade Decision use the same continuous primitive utility",
            "shared_utility_model": "FSFFL-Shared-Decision-Utility-1.0",
            "categorical_state_weights_active": False,
            "legacy_championship_diminishing_return_rule_active": False,
            "legacy_dynasty_value_guardrail_authoritative": False,
            "scale_status": "PROVISIONAL_CENTRALIZED_PENDING_EVIDENCE_BASED_SCALING",
            "notes": "Displayed football outcomes remain raw Simulator results. Recommendation ranking uses one shared current/future/liquidity/resilience utility; acceptance remains separate.",
        }
        out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    main()
