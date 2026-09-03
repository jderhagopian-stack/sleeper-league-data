#!/usr/bin/env python3
"""Regression guard: quick Trade Market Sweep candidates must receive the same
optimized-starter current-season evidence family as confirmed finalists.

The roster diagnosis is deterministic and must never be conditioned on Monte
Carlo simulation count.
"""
from pathlib import Path

SRC=Path(__file__).resolve().parent/"run_trade_market_sweep_v13.py"

def main():
    text=SRC.read_text(encoding="utf-8")
    assert "if sims >= 50000 else {}" not in text
    assert "_GM_BASELINE_POSITION_NEED_CACHE" in text
    assert "needs_after = position_need_snapshot(engine, hypothetical_rosters, focus_uid)" in text
    assert "Deterministic roster diagnosis is valid at every simulation budget." in text
    print("trade market current-evidence parity regression passed")

if __name__=="__main__":
    main()
