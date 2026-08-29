#!/usr/bin/env python3
"""Compatibility shim for dynamically imported FSFFL modules.

Canonical implementation lives in script/league_rules.py. Some legacy test and
runtime loaders import modules from repository root with importlib, which means
the script directory is not automatically on sys.path. Keep one implementation
and re-export it here rather than duplicating league rules.
"""
from script.league_rules import *  # noqa: F401,F403
