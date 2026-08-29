#!/usr/bin/env python3
"""Compare FSFFL-native and external preseason projections on a common raw-stat cohort.

Inputs are long-form CSV rows:
  season,position,player_name,stat,projection
for native/external forecasts and:
  season,position,player_name,stat,actual
for realized outcomes.

The benchmark intentionally compares underlying football statistics rather than
fantasy points, keeping projection quality independent of any league scoring.
"""
from __future__ import annotations

import argparse, csv, json, re, unicodedata
from collections import defaultdict
from pathlib import Path


def norm_name(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch)).lower()
    value = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", value)
    return re.sub(r"[^a-z0-9]+", "", value)


def read(path: Path, value_field: str) -> dict:
    out = {}
    with path.open(newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            key = (int(r["season"]), r["position"].upper(), norm_name(r["player_name"]), r["stat"])
            if key in out:
                raise ValueError(f"duplicate row {key}")
            out[key] = float(r[value_field])
    return out


def mae(vals):
    return sum(vals) / len(vals) if vals else None


def compare(native: dict, external: dict, actual: dict) -> dict:
    keys = sorted(set(native) & set(external) & set(actual))
    groups = defaultdict(list)
    for k in keys:
        season, pos, _, stat = k
        groups[(season, pos, stat)].append(k)
    detail = {}
    wins = {"native": 0, "external": 0, "ties": 0}
    weighted_native = weighted_external = 0.0
    weighted_n = 0
    for group, gkeys in sorted(groups.items()):
        nerr = mae([abs(native[k] - actual[k]) for k in gkeys])
        eerr = mae([abs(external[k] - actual[k]) for k in gkeys])
        winner = "native" if nerr < eerr else "external" if eerr < nerr else "tie"
        wins["ties" if winner == "tie" else winner] += 1
        season, pos, stat = group
        detail[f"{season}|{pos}|{stat}"] = {
            "n": len(gkeys), "native_mae": nerr, "external_mae": eerr,
            "native_improvement_vs_external_pct": 100.0 * (eerr - nerr) / eerr if eerr else 0.0,
            "winner": winner,
        }
        weighted_native += nerr * len(gkeys)
        weighted_external += eerr * len(gkeys)
        weighted_n += len(gkeys)
    return {
        "schema_version": "1.0", "status": "PASS", "common_stat_rows": len(keys),
        "group_count": len(groups), "group_wins": wins,
        "weighted_common_cohort_mae": {
            "native": weighted_native / weighted_n if weighted_n else None,
            "external": weighted_external / weighted_n if weighted_n else None,
        },
        "detail": detail,
        "governance": {
            "fantasy_scoring_used": False,
            "comparison_population": "exact common player-season-position-stat cohort",
            "promotion_rule": "external superiority/inferiority must be interpreted by position/stat stability, not aggregate alone"
        }
    }


def self_test():
    actual={(2024,"WR","a","receiving_yards"):1000,(2024,"WR","b","receiving_yards"):800}
    native={(2024,"WR","a","receiving_yards"):980,(2024,"WR","b","receiving_yards"):820}
    external={(2024,"WR","a","receiving_yards"):900,(2024,"WR","b","receiving_yards"):700}
    r=compare(native,external,actual)
    assert r["group_count"]==1 and r["group_wins"]["native"]==1
    print("native-vs-external raw-stat benchmark self-test: PASS")


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--native",type=Path); p.add_argument("--external",type=Path); p.add_argument("--actual",type=Path)
    p.add_argument("--output",type=Path); p.add_argument("--self-test",action="store_true")
    a=p.parse_args()
    if a.self_test: self_test(); return
    if not all([a.native,a.external,a.actual,a.output]): p.error("--native --external --actual --output required")
    r=compare(read(a.native,"projection"),read(a.external,"projection"),read(a.actual,"actual"))
    a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(r,indent=2,sort_keys=True)+"\n")
    print(json.dumps({"status":"PASS","groups":r["group_count"],"rows":r["common_stat_rows"]},indent=2))

if __name__=="__main__": main()
