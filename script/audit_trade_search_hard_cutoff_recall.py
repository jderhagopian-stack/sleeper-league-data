#!/usr/bin/env python3
"""Stage-1 shadow screen for GM3 proactive-discovery hard-cutoff omission risk.

Targets excluded only by the legacy dynasty-value/need rule are passed through
existing GM3 targeted price discovery. Upstream package bands identify
candidates for canonical Shared Decision Utility confirmation; they do not
establish final franchise improvement. No production search policy is changed.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "script"
OUT = ROOT / "data" / "audit" / "trade_search_hard_cutoff_recall.json"


def loadmod(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    engine = loadmod(SCRIPT / "build_fsffl_gm_engine.py", "cutoff_recall_engine")
    high = loadmod(
        SCRIPT / "nonprojection_high_priority_overrides.py",
        "cutoff_recall_high",
    )
    gov = loadmod(
        SCRIPT / "gm30_nonprojection_governance.py",
        "cutoff_recall_gov",
    )
    high.install(engine)
    gov.install(engine)

    ctx = engine._u_load_context()
    uids = sorted(map(str, ctx.get("owners") or {}))
    profiles = {
        uid: engine._u_profile_map(
            engine.build_strategic_asset_profiles_for_team(uid, ctx)
        )
        for uid in uids
    }

    team_rows = []
    total_excluded = 0
    fully_tested = 0
    candidate_risk_rows = []

    for uid in uids:
        team = ctx["teams"].get(uid, {})
        need_map = team.get("position_need") or {}
        excluded = []

        for aid, meta in ctx["player_meta"].items():
            seller = str(meta.get("current_owner_user_id") or "")
            if not seller or seller == uid or seller not in ctx["owners"]:
                continue
            if engine.safe_float(ctx["owner_vals"].get(uid, {}).get(aid)) <= 0:
                continue
            if engine.safe_float(ctx["owner_vals"].get(seller, {}).get(aid)) <= 0:
                continue

            dyn = engine.safe_float(meta.get("market_dynasty"))
            need = engine.safe_float(need_map.get(meta.get("position")), 0.5)
            if dyn < 1000 and need < 0.68:
                excluded.append(
                    {
                        "asset_id": aid,
                        "name": meta.get("name"),
                        "seller_user_id": seller,
                        "position": meta.get("position"),
                        "market_dynasty": dyn,
                        "position_need": need,
                        "focal_owner_value": engine.safe_float(
                            ctx["owner_vals"].get(uid, {}).get(aid)
                        ),
                    }
                )

        # Bounded stage-1 screen: test the excluded targets the focal model values
        # most highly. This is not exhaustive recall evidence.
        excluded.sort(
            key=lambda x: (x["focal_owner_value"], x["market_dynasty"]),
            reverse=True,
        )
        test_rows = excluded[:8]
        target_ids = [x["asset_id"] for x in test_rows]

        result = (
            engine.build_targeted_trade_price_curves(
                uid,
                target_ids,
                max_packages_per_target=16,
                ctx=ctx,
                profile_by_uid=profiles,
            )
            if target_ids
            else {"targets": []}
        )
        by_target = {
            str(x.get("target_asset_id")): x
            for x in result.get("targets") or []
        }

        tested = []
        for meta in test_rows:
            payload = by_target.get(meta["asset_id"]) or {}
            packages = payload.get("price_frontier_candidate_packages") or []
            upstream_viable = [
                p
                for p in packages
                if str(p.get("recommendation_band") or "")
                in {"mutual_value_candidate", "negotiation_candidate"}
                or (
                    engine.safe_float(p.get("focal_strategic_utility"), -999) > 0
                    and engine.safe_float(
                        p.get("seller_strategic_utility"), -999
                    )
                    >= 0
                )
            ]
            row = {
                **meta,
                "packages_fully_evaluated": len(packages),
                "upstream_viable_package_count": len(upstream_viable),
                "best_upstream_viable_package": (
                    upstream_viable[0] if upstream_viable else None
                ),
                "candidate_recall_risk": bool(upstream_viable),
                "canonical_shared_utility_confirmed": False,
            }
            tested.append(row)
            if upstream_viable:
                candidate_risk_rows.append({"focal_user_id": uid, **row})

        total_excluded += len(excluded)
        fully_tested += len(test_rows)
        team_rows.append(
            {
                "focal_user_id": uid,
                "team_name": (ctx["owners"].get(uid) or {}).get("team_name"),
                "excluded_target_count": len(excluded),
                "excluded_targets_tested": len(test_rows),
                "candidate_recall_risk_count": sum(
                    bool(x["candidate_recall_risk"]) for x in tested
                ),
                "tested_targets": tested,
            }
        )

    report = {
        "model_version": "FSFFL-Trade-Search-Hard-Cutoff-Recall-1.1",
        "authority": "SHADOW_RESEARCH_NON_AUTHORITATIVE",
        "production_behavior_changed": False,
        "scope": "GM3 proactive discovery only; not Trade Decision prescreen authority",
        "hard_cutoff": {
            "market_dynasty_less_than": 1000.0,
            "position_need_less_than": 0.68,
        },
        "method": (
            "Top focal-valued targets excluded solely by the GM3 discovery hard "
            "cutoff are routed through existing targeted price discovery."
        ),
        "summary": {
            "teams_tested": len(team_rows),
            "targets_excluded_by_cutoff": total_excluded,
            "excluded_targets_shadow_tested": fully_tested,
            "candidate_recall_risk_targets_found": len(candidate_risk_rows),
            "canonical_false_negative_targets_confirmed": 0,
            "hard_cutoff_recall_failure_detected": False,
            "production_change_authorized_by_this_artifact": False,
        },
        "candidate_recall_risk_targets": candidate_risk_rows,
        "teams": team_rows,
        "interpretation": {
            "upstream_viable_excluded_target_requires_canonical_confirmation": True,
            "upstream_viability_alone_is_not_promotion_evidence": True,
            "no_candidate_risk_found_is_not_proof_of_perfect_recall": True,
            "targeted_price_discovery_remains_existing_gm3_search_economics": True,
            "trade_decision_current_prescreen_is_separate": True,
            "hurts_so_good_rankings_used_as_ground_truth": False,
        },
        "next_action": (
            "RUN_CANONICAL_SHARED_UTILITY_CONFIRMATION"
            if candidate_risk_rows
            else "EXPAND_RECALL_TEST_BEFORE_DECIDING"
        ),
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
