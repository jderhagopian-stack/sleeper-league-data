#!/usr/bin/env python3
"""Canonical negotiation-family identity for trade candidates.

Mechanical extraction of the current production v1.15 semantics. Trivial pick
sweetener variants of the same buyer / focal outgoing / primary incoming player
package collapse into one negotiation family. Pick-only offers remain distinct
by their pick package.
"""
from __future__ import annotations

MODEL_VERSION = "FSFFL-Negotiation-Family-1.0"


def is_pick(asset_id):
    return str(asset_id).startswith("pick:")


def family_key(row):
    buyer = str(row.get("buyer_user_id") or "")
    outgoing = tuple(sorted(str(x) for x in (row.get("outgoing_assets") or [])))
    returns = [str(x) for x in (row.get("return_assets") or [])]
    primary_players = tuple(sorted(x for x in returns if not is_pick(x)))
    pick_only = (
        tuple(sorted(x for x in returns if is_pick(x)))
        if not primary_players
        else ()
    )
    return buyer, outgoing, primary_players, pick_only
