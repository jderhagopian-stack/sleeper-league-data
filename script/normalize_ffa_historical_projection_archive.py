#!/usr/bin/env python3
"""Normalize Fantasy Football Analytics historical provider CSVs to FSFFL raw-stat long form.

Research utility only. It does not download files, establish reuse rights, or
promote any source. Input must be an already acquired provider CSV whose
provider-season slice is tracked in projection archive qualification metadata.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

STAT_MAP={
    "passAtt":"attempts",
    "passComp":"completions",
    "passYds":"passing_yards",
    "passTds":"passing_tds",
    "passInt":"interceptions",
    "rushAtt":"rushing_attempts",
    "rushYds":"rushing_yards",
    "rushTds":"rushing_tds",
    "rec":"receptions",
    "recYds":"receiving_yards",
    "recTds":"receiving_tds",
}


def fnum(v):
    try: return float(v)
    except (TypeError,ValueError): return None


def detect_suffix(fieldnames,source):
    source=source.lower()
    aliases={
        "fantasypros":["fp"],
        "fftoday":["fftoday"],
        "espn":["espn"],
        "cbs":["cbs"],
    }
    candidates=aliases.get(source,[source])
    for suffix in candidates:
        if any(name==f"passYds_{suffix}" or name==f"recYds_{suffix}" for name in fieldnames):
            return suffix
    raise SystemExit(f"unable to detect provider suffix for {source}")


def normalize(input_path:Path,source:str,season:int):
    with input_path.open(newline="",encoding="utf-8-sig") as fh:
        reader=csv.DictReader(fh)
        fields=reader.fieldnames or []
        suffix=detect_suffix(fields,source)
        out=[]
        for raw in reader:
            pos=str(raw.get("pos") or "").upper().strip()
            player=str(raw.get(f"name_{suffix}") or raw.get("name") or "").strip()
            team=str(raw.get(f"team_{suffix}") or "").strip()
            if not player or pos not in {"QB","RB","WR","TE"}:
                continue
            for stem,stat in STAT_MAP.items():
                value=fnum(raw.get(f"{stem}_{suffix}"))
                if value is None:
                    continue
                out.append({
                    "season":season,
                    "position":pos,
                    "player_name":player,
                    "team":team,
                    "stat":stat,
                    "source":source,
                    "projection":value,
                })
        return out


def write(rows,output:Path):
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open("w",newline="",encoding="utf-8") as fh:
        writer=csv.DictWriter(fh,fieldnames=["season","position","player_name","team","stat","source","projection"])
        writer.writeheader(); writer.writerows(rows)


def self_test():
    import tempfile
    sample='''name,pos,name_fp,team_fp,passYds_fp,passTds_fp,rushYds_fp,rec_fp,recYds_fp,recTds_fp\nA,QB,A QB,AAA,4000,30,200,NA,NA,NA\nB,WR,B WR,BBB,NA,NA,20,80,1100,8\n'''
    with tempfile.TemporaryDirectory() as td:
        inp=Path(td)/"x.csv"; out=Path(td)/"y.csv"; inp.write_text(sample)
        rows=normalize(inp,"FantasyPros",2014); write(rows,out)
        assert any(r["stat"]=="passing_yards" and r["projection"]==4000 for r in rows)
        assert any(r["stat"]=="receiving_yards" and r["projection"]==1100 for r in rows)
        assert not any(r["stat"]=="receiving_yards" and r["player_name"]=="A QB" for r in rows)
    print("FFA historical projection normalizer self-test: PASS")


def main():
    p=argparse.ArgumentParser()
    p.add_argument("input",nargs="?")
    p.add_argument("--source")
    p.add_argument("--season",type=int)
    p.add_argument("--output")
    p.add_argument("--self-test",action="store_true")
    a=p.parse_args()
    if a.self_test:
        self_test(); return
    if not all([a.input,a.source,a.season,a.output]):
        p.error("input --source --season --output required unless --self-test")
    rows=normalize(Path(a.input),a.source,a.season)
    write(rows,Path(a.output))
    print(f"normalized {len(rows)} raw projection rows")


if __name__=="__main__":
    main()
