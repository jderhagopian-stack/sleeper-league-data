#!/usr/bin/env python3
"""FSFFL GM 3.0 Team Improvement Lab 1.5.

Consumes the canonical full fantasy-relevant projection universe produced by
build_fsffl_full_projection_universe.py. Waiver/free-agent discovery uses a
scale-free multi-lane search over independent governed signals rather than a
fixed cross-unit weighted pre-screen. Trade discovery preserves upstream GM3
package scores while broadening target/package coverage. Trade evaluation,
roster legalization, common-objective ranking, deep confirmation, and HOLD
benchmarking remain inherited from the stable Team Improvement application.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent / "run_team_improvement_lab.py"
MODEL_VERSION = "FSFFL-GM-Team-Improvement-Lab-1.5"
PROJECTION_MODEL_VERSION = "FSFFL-Full-Projection-Universe-1.0"
DEFAULT_TRADE_PACKAGES_PER_TARGET = 5


def load_base():
    spec = importlib.util.spec_from_file_location("team_improvement_lab_base13", BASE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {BASE}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _pop_cli_int(name, default):
    if name not in sys.argv:
        return int(default)
    i = sys.argv.index(name)
    if i + 1 >= len(sys.argv):
        raise ValueError(f"{name} requires an integer value")
    value = int(sys.argv[i + 1])
    del sys.argv[i:i + 2]
    return value


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


def _rank_map(rows, key, reverse=True, eligible=None):
    eligible = eligible or (lambda _: True)
    ordered = sorted((x for x in rows if eligible(x)), key=key, reverse=reverse)
    return {str(((x.get("target") or {}).get("asset_id"))): i + 1 for i, x in enumerate(ordered)}, ordered


def _round_robin_discovery(lanes, limit):
    """Select a diverse candidate set without creating a cross-unit utility score."""
    selected = []
    seen = set()
    cursors = {name: 0 for name in lanes}
    while len(selected) < int(limit):
        progressed = False
        for name, lane in lanes.items():
            cursor = cursors[name]
            while cursor < len(lane):
                row = lane[cursor]
                cursor += 1
                aid = str(((row.get("target") or {}).get("asset_id")) or "")
                if aid and aid not in seen:
                    out = copy.deepcopy(row)
                    out["discovery_lane"] = name
                    out["pre_screen_rank"] = len(selected) + 1
                    out["pre_screen_score"] = None
                    selected.append(out)
                    seen.add(aid)
                    progressed = True
                    break
            cursors[name] = cursor
            if len(selected) >= int(limit):
                break
        if not progressed:
            break
    return selected


def trade_candidates(base, focus_uid, catalog, limit, packages_per_target):
    """Broaden GM3 trade discovery while preserving the upstream package score."""
    doc = base.team_doc(focus_uid, "trade_opportunities")
    rows = []
    for opp in doc.get("opportunities") or []:
        target_id = str(opp.get("target_asset_id") or "")
        seller = str(opp.get("seller_user_id") or "")
        target = catalog.get(target_id)
        if not target or not seller or seller == str(focus_uid):
            continue
        packages = list(opp.get("best_candidate_packages") or [])[: max(1, int(packages_per_target))]
        for package_ordinal, pkg in enumerate(packages, 1):
            aids = [str(x) for x in (pkg.get("focal_outgoing_asset_ids") or [])]
            outgoing = [catalog.get(x) for x in aids]
            if not aids or any(x is None for x in outgoing):
                continue
            acceptance = base.sf(pkg.get("acceptance_fit_score"))
            seller_utility = base.sf(pkg.get("seller_strategic_utility"))
            rows.append({
                "channel": "TRADE",
                "seller_user_id": seller,
                "seller_team": opp.get("seller_team"),
                "target": target,
                "outgoing": outgoing,
                "pre_screen_score": base.sf(pkg.get("gm30_decision_score"), base.sf(pkg.get("decision_score"))),
                "acceptance_fit_score": acceptance,
                "seller_strategic_utility_precomputed": seller_utility,
                "source_recommendation_band": pkg.get("recommendation_band"),
                "target_focal_value": base.sf(opp.get("focal_value")),
                "target_market_dynasty": base.sf(opp.get("market_dynasty")),
                "target_market_redraft": base.sf(opp.get("market_redraft")),
                "focal_position_need": base.sf(opp.get("focal_position_need")),
                "seller_motivation_score": base.sf(opp.get("seller_motivation_score")),
                "source_package_ordinal_for_target": package_ordinal,
                "source_package_score_owned_by_gm3": True,
            })

    rows.sort(key=lambda x: (x["pre_screen_score"], x["acceptance_fit_score"]), reverse=True)
    deduped = []
    seen = set()
    for row in rows:
        key = (
            row["seller_user_id"],
            row["target"]["asset_id"],
            tuple(sorted(x["asset_id"] for x in row["outgoing"])),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)

    # The first pass maximizes unique-target coverage. If budget remains, fill with
    # additional upstream-ranked packages. This is search coverage, not rescoring.
    first_for_target = []
    target_seen = set()
    for row in deduped:
        target_id = str((row.get("target") or {}).get("asset_id") or "")
        if target_id in target_seen:
            continue
        target_seen.add(target_id)
        first_for_target.append(row)

    selected = list(first_for_target[: max(0, int(limit))])
    selected_keys = {
        (
            x["seller_user_id"],
            x["target"]["asset_id"],
            tuple(sorted(a["asset_id"] for a in x["outgoing"])),
        )
        for x in selected
    }
    if len(selected) < int(limit):
        for row in deduped:
            key = (
                row["seller_user_id"],
                row["target"]["asset_id"],
                tuple(sorted(x["asset_id"] for x in row["outgoing"])),
            )
            if key in selected_keys:
                continue
            selected.append(row)
            selected_keys.add(key)
            if len(selected) >= int(limit):
                break

    for i, row in enumerate(selected, 1):
        row["trade_discovery_rank"] = i
        row["trade_discovery_target_diversity_pass"] = True
    return selected


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
        rows.append({
            "channel": "WAIVER",
            "target": asset,
            "projected_weekly_mean": round(projected, 3),
            "preseason_ecr": None if target_ecr >= 9999 else target_ecr,
            "waiver_discovery_source": "canonical_full_projection_universe_scale_free_multilane",
            "projection_source_model": full.get("model_version"),
            "native_full_projection": copy.deepcopy(profile),
        })

    projection_ranks, projection_lane = _rank_map(rows, lambda x: float(x.get("projected_weekly_mean") or 0.0), reverse=True)
    ecr_ranks, ecr_lane = _rank_map(
        rows,
        lambda x: float(x.get("preseason_ecr") or 9999.0),
        reverse=False,
        eligible=lambda x: x.get("preseason_ecr") is not None,
    )
    market_ranks, market_lane = _rank_map(rows, lambda x: float(((x.get("target") or {}).get("market_dynasty")) or 0.0), reverse=True)
    fsffl_ranks, fsffl_lane = _rank_map(rows, lambda x: float(((x.get("target") or {}).get("fsffl_value")) or 0.0), reverse=True)

    for row in rows:
        aid = str(((row.get("target") or {}).get("asset_id")) or "")
        row["discovery_signal_ranks"] = {
            "projected_weekly_mean": projection_ranks.get(aid),
            "preseason_ecr": ecr_ranks.get(aid),
            "market_dynasty": market_ranks.get(aid),
            "fsffl_value": fsffl_ranks.get(aid),
        }
        row["pre_screen_weighted_score_used"] = False

    return _round_robin_discovery(
        {
            "projection": projection_lane,
            "preseason_ecr": ecr_lane,
            "market_dynasty": market_lane,
            "fsffl_value": fsffl_lane,
        },
        max(1, int(limit)),
    )


def simulate_actions_protect_add(base, dl, lineupopt, rosteraware, model_inputs,
                                 baseline_lineups, baseline, focus_uid, actions, sims, seed):
    """Current Team Improvement action-bundle simulation with protected adds."""
    simmod, league, canonical_rosters, users, players, season, projections, raw_schedule = model_inputs
    hypothetical, _ = dl.apply_actions(canonical_rosters, actions)
    touched = dl.touched_users(focus_uid, actions)
    protected = {}
    for action in actions:
        if str(action.get("type") or "").lower() == "add":
            uid = str(action.get("user_id"))
            ids = action.get("players") or (
                [action.get("player_id")] if action.get("player_id") is not None else []
            )
            protected.setdefault(uid, set()).update(str(x) for x in ids)
    legal, resolutions, cut_actions = rosteraware.legalize_trade_rosters(
        dl, canonical_rosters, hypothetical, touched, league, players,
        protected_player_ids_by_uid=protected,
    )
    effective_actions = list(actions) + list(cut_actions)
    lineups, reoptimized = base.fast_reoptimize(
        lineupopt, dl, simmod, baseline_lineups, legal, touched, league, users, players, projections
    )
    hyp = dl.simulate_from_lineups(
        simmod, league, legal, users, raw_schedule, lineups, sims, seed
    )
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


def main():
    trade_packages_per_target = _pop_cli_int(
        "--trade-packages-per-target", DEFAULT_TRADE_PACKAGES_PER_TARGET
    )
    out = output_path_from_argv()
    base = load_base()
    base.MODEL_VERSION = MODEL_VERSION
    saved_evaluate = base.evaluate_row

    base.trade_candidates = lambda focus_uid, catalog, limit: trade_candidates(
        base, focus_uid, catalog, limit, trade_packages_per_target
    )
    base.waiver_candidates = lambda focus_uid, players_catalog, model_inputs, limit: waiver_candidates(
        base, focus_uid, players_catalog, model_inputs, limit
    )
    base.simulate_actions = lambda dl, lineupopt, rosteraware, model_inputs, baseline_lineups, baseline, focus_uid, actions, sims, seed: simulate_actions_protect_add(
        base, dl, lineupopt, rosteraware, model_inputs, baseline_lineups, baseline,
        focus_uid, actions, sims, seed
    )

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
        report.setdefault("search_summary", {})["trade_packages_per_target_considered"] = int(trade_packages_per_target)
        report.setdefault("policy", {})["waiver_candidates_use_canonical_full_projection_universe"] = True
        report["policy"]["waiver_pre_screen_uses_fixed_cross_unit_coefficients"] = False
        report["policy"]["waiver_discovery_is_scale_free_multilane"] = True
        report["policy"]["trade_discovery_prioritizes_unique_target_coverage"] = True
        report["policy"]["trade_package_pre_screen_score_owned_by_upstream_gm3"] = True
        report["ranking_calibration"] = {
            "version": "shared-decision-utility-2.0",
            "principle": "Team Improvement and Trade Decision use the same continuous primitive utility",
            "shared_utility_model": "FSFFL-Shared-Decision-Utility-2.0",
            "categorical_state_weights_active": False,
            "legacy_championship_diminishing_return_rule_active": False,
            "legacy_dynasty_value_guardrail_authoritative": False,
            "scale_status": "DATA_DERIVED_LEAGUE_RELATIVE_NO_FIXED_UNIT_CONVERSION_COEFFICIENTS",
            "notes": "Displayed football outcomes remain raw Simulator results. Recommendation ranking uses one shared current/future/liquidity/resilience utility; acceptance remains separate. Waiver discovery uses independent-signal lanes rather than a weighted cross-unit pre-screen; trade discovery broadens target/package coverage without a new score.",
        }
        out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    main()
