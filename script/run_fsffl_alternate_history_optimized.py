#!/usr/bin/env python3
"""Launch an Alternate History script with validated runtime optimizations."""

from __future__ import annotations

import runpy
import sys

import alternate_history_performance_runtime as perf


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: run_fsffl_alternate_history_optimized.py <script-path> [args...]")
    target = sys.argv[1]
    sys.argv = [target, *sys.argv[2:]]
    perf.install()
    runpy.run_path(target, run_name="__main__")


if __name__ == "__main__":
    main()
