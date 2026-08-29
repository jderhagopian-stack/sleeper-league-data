#!/usr/bin/env python3
"""Verify current nflverse depth charts support a leakage-safe dated preseason snapshot."""
from __future__ import annotations
import argparse,csv,io,json,urllib.request
from pathlib import Path

URL="https://github.com/nflverse/nflverse-data/releases/download/depth_charts/depth_charts_{season}.csv"

def main():
    p=argparse.ArgumentParser(); p.add_argument("--season",type=int,default=2026); p.add_argument("--output",type=Path,default=Path("data/model_validation/current_depth_chart_schema_probe.json")); a=p.parse_args()
    req=urllib.request.Request(URL.format(season=a.season),headers={"User-Agent":"FSFFL-current-depth-schema/1.0"})
    with urllib.request.urlopen(req,timeout=90) as r: rows=list(csv.DictReader(io.StringIO(r.read().decode("utf-8-sig"))))
    cols=list(rows[0]) if rows else []
    dt_col=next((c for c in ("dt","timestamp","date") if c in cols),None)
    pos_col=next((c for c in ("position","pos_grp","position_group") if c in cols),None)
    rank_col=next((c for c in ("pos_rank","depth_team","rank") if c in cols),None)
    values=sorted({str(x.get(dt_col) or "") for x in rows if dt_col and x.get(dt_col)})
    d={"schema_version":"1.0","season":a.season,"row_count":len(rows),"columns":cols,"timestamp_column":dt_col,"timestamp_min":values[0] if values else None,"timestamp_max":values[-1] if values else None,"position_column":pos_col,"rank_column":rank_col,"dated_snapshot_available":bool(dt_col and values),"production_role_source_candidate":bool(dt_col and values and pos_col and rank_col),"commercial_dependency_approved":False,"replacement_note":"Current nflverse role source remains provisional for commercial dependency pending a free commercially suitable replacement/rights clearance."}
    a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(d,indent=2,sort_keys=True)+"\n"); print(json.dumps(d,indent=2))
if __name__=="__main__": main()
