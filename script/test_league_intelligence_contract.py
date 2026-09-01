#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from league_intelligence.contract import (
    FORBIDDEN_OPERATIONS,
    ViewContract,
    first_release_contracts,
)


def main() -> None:
    contracts = first_release_contracts()
    assert {c.view_id for c in contracts} == {
        "player-value-rankings",
        "team-strength-heat-map",
        "value-disagreement-trade-partner-map",
    }
    for c in contracts:
        assert set(c.forbidden_operations) == FORBIDDEN_OPERATIONS
        assert "presentation_composite_rerank" in c.forbidden_operations
        assert c.discovery_safe is True
        assert c.presentation_safe is True

    weakened = ViewContract(
        view_id="bad-view",
        version="1.0",
        purpose="Should fail because governance was weakened.",
        upstream_authorities=("GM3",),
        governed_fields=("team_need",),
        forbidden_operations=tuple(sorted(FORBIDDEN_OPERATIONS - {"new_cross_channel_utility"})),
    )
    try:
        weakened.validate()
    except ValueError:
        pass
    else:
        raise AssertionError("weakened authority boundary was accepted")

    print("League Intelligence governance contract tests passed")


if __name__ == "__main__":
    main()
