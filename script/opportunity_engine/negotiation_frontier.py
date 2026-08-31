#!/usr/bin/env python3
"""Compatibility adapter for Opportunity Engine negotiation-frontier routing.

Decision authority lives in trade_decision.negotiation_frontier. Opportunity
Engine may call and present that interpretation but may not implement or alter it.
"""
from trade_decision.negotiation_frontier import (  # noqa: F401
    AUTHORITY,
    MODEL_VERSION,
    build,
    classify_trade,
)

DEPRECATED_AUTHORITY_LOCATION = True
AUTHORITY_OWNER = "Trade Decision"
