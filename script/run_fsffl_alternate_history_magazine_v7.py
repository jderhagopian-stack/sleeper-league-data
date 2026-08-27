#!/usr/bin/env python3
"""Alternate History magazine v7: V6 semantics with exact runtime caches."""
from __future__ import annotations

import alternate_history_runtime_optimizations as runtime_opt

runtime_opt.install()

from run_fsffl_alternate_history_magazine_v6 import *  # noqa: F401,F403,E402
from run_fsffl_alternate_history_magazine_v6 import main as _main  # noqa: E402


if __name__ == "__main__":
    _main()
