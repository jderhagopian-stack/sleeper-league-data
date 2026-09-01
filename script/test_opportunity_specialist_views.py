#!/usr/bin/env python3
"""Regression tests for governed specialist-view preservation."""
from opportunity_engine.application_v21 import _annotate_price_frontier_routing, _preserve_specialized_views, _trade_signature

base_trade={
    "channel":"TRADE",
    "description":"Trade picks for Example Star",
    "seller_user_id":"seller",
    "target":{"asset_id":"star","name":"Example Star"},
    "outgoing":[{"asset_id":"pick1","name":"2028 1st"}],
    "team_improvement_score":500.0,
    "counterparty_shared_decision_utility_score":-250.0,
    "view_basis":"positive canonical Simulator current-season outcome delta",
}
views={
    "best_current_season_upgrade":dict(base_trade),
    "best_long_term_value_move":dict(base_trade),
    "best_buy_low_candidate":None,
}
out=_preserve_specialized_views(views,set(),set())
assert out["best_current_season_upgrade"] is not None
assert out["best_current_season_upgrade"]["opportunity_routing_status"]=="THEORETICAL_COUNTERPARTY_FAILURE"
assert out["best_current_season_upgrade"]["specialist_view_is_recommendation"] is False
assert out["best_current_season_upgrade"]["specialist_view_preserved_despite_non_actionable_status"] is True
assert out["best_buy_low_candidate"] is None

sig=_trade_signature(base_trade)
actionable=_preserve_specialized_views({"v":dict(base_trade)},{sig},set())
assert actionable["v"]["opportunity_routing_status"]=="ACTIONABLE"
assert actionable["v"]["specialist_view_preserved_despite_non_actionable_status"] is False

unstable=_preserve_specialized_views({"v":dict(base_trade)},set(),{sig})
assert unstable["v"]["opportunity_routing_status"]=="SIMULATION_SENSITIVE"
assert unstable["v"]["specialist_view_preserved_despite_non_actionable_status"] is True

waiver={
    "channel":"WAIVER",
    "description":"Add Example Player",
    "team_improvement_score":10.0,
}
w=_preserve_specialized_views({"v":waiver},set(),set())
assert w["v"]["opportunity_routing_status"]=="ACTIONABLE"
assert w["v"]["specialist_view_is_recommendation"] is False

print("Specialist view preservation regressions passed")


frontier={
    "target_price_frontiers":[{
        "status":"ACTIONABLE_PRICE_OVERLAP",
        "price_overlap_exists":True,
        "opening_package":{
            "description":base_trade["description"],
            "seller_user_id":base_trade["seller_user_id"],
            "target":base_trade["target"],
            "outgoing":base_trade["outgoing"],
            "incoming":[],
            "focal_team_improvement_utility":500.0,
            "counterparty_shared_utility":10.0,
        },
    }],
    "policy":{},
}
overlay=_annotate_price_frontier_routing(frontier,set(),{sig})
pf=overlay["target_price_frontiers"][0]
assert pf["trade_decision_price_frontier_status"]=="ACTIONABLE_PRICE_OVERLAP"
assert pf["opportunity_routing_status"]=="SIMULATION_SENSITIVE"
assert pf["opportunity_engine_price_frontier_status"]=="SIMULATION_SENSITIVE_PRICE_OVERLAP"
assert overlay["policy"]["opportunity_routing_overlay_changes_trade_decision_price_frontier_authority"] is False
assert overlay["policy"]["simulation_sensitive_opening_packages_are_not_presented_as_actionable"] is True
