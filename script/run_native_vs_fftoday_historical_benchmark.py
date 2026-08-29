#!/usr/bin/env python3
"""Run the real leakage-safe FSFFL-native vs FFToday preseason raw-stat benchmark.

Only source/season/position snapshots explicitly marked ELIGIBLE_PRESEASON in the
versioned inventory are admitted. Native models are trained using seasons strictly
before each target season. Both systems are scored on the exact common
player-season-position-stat cohort against realized nflverse regular-season stats.
"""
from __future__ import annotations

import argparse, json, re, sys, unicodedata, urllib.parse, urllib.request
from collections import defaultdict
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_projection_challenger import RidgeModel, choose_alpha_temporally
from run_native_projection_nflverse_benchmark import (
    FEATURES as BASE_FEATURES, POSITIONS, TARGETS, fetch_csv, make_lagged_rows, normalize_season
)
from run_native_projection_core_context_benchmark import AGE, DURABILITY, enrich, fetch_players
from benchmark_native_vs_external_raw_stats import compare

SELECTED = {
    "QB": list(DURABILITY["QB"]),
    "RB": [],
    "WR": list(AGE["WR"]),
    "TE": list(AGE["TE"]),
}
POS_ID = {"QB": 10, "RB": 20, "WR": 30, "TE": 40}
TEAM_CODES = {
    "ARI","ATL","BAL","BUF","CAR","CHI","CIN","CLE","DAL","DEN","DET","GB","HOU","IND",
    "JAC","JAX","KC","LAC","LAR","LV","MIA","MIN","NE","NO","NYG","NYJ","PHI","PIT",
    "SEA","SF","TB","TEN","WAS"
}
LAYOUT = {
    "QB": [
        ("completions",1),("attempts",2),("passing_yards",3),("passing_tds",4),
        ("interceptions",5),("rushing_attempts",6),("rushing_yards",7),("rushing_tds",8)
    ],
    "RB": [
        ("carries",1),("rushing_yards",2),("rushing_tds",3),("receptions",4),
        ("receiving_yards",5),("receiving_tds",6)
    ],
    "WR": [
        ("receptions",1),("receiving_yards",2),("receiving_tds",3),
        ("rushing_attempts",4),("rushing_yards",5),("rushing_tds",6)
    ],
    "TE": [("receptions",1),("receiving_yards",2),("receiving_tds",3)],
}
NATIVE_TARGET = {
    "attempts":"next_attempts","passing_yards":"next_passing_yards","passing_tds":"next_passing_tds",
    "interceptions":"next_interceptions","rushing_yards":"next_rushing_yards","rushing_tds":"next_rushing_tds",
    "carries":"next_carries","receptions":"next_receptions","receiving_yards":"next_receiving_yards",
    "receiving_tds":"next_receiving_tds",
}


def norm_name(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch)).lower()
    value = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", value)
    return re.sub(r"[^a-z0-9]+", "", value)


def num(value: str) -> float:
    s = (value or "").replace(",", "").strip()
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        raise ValueError(f"not numeric: {value!r}")
    return float(m.group(0))


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.row, self.cell = [], None, None
        self.in_cell = False
    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        elif tag in ("td","th") and self.row is not None:
            self.cell = {"text": [], "anchors": []}
            self.in_cell = True
        elif tag == "a" and self.in_cell:
            self.cell["_anchor"] = []
    def handle_data(self, data):
        if self.in_cell and self.cell is not None:
            self.cell["text"].append(data)
            if "_anchor" in self.cell:
                self.cell["_anchor"].append(data)
    def handle_endtag(self, tag):
        if tag == "a" and self.in_cell and self.cell is not None and "_anchor" in self.cell:
            txt = " ".join(" ".join(self.cell.pop("_anchor")).split())
            if txt:
                self.cell["anchors"].append(txt)
        elif tag in ("td","th") and self.in_cell and self.row is not None and self.cell is not None:
            self.cell["text"] = " ".join(" ".join(self.cell["text"]).split())
            self.row.append(self.cell)
            self.cell = None
            self.in_cell = False
        elif tag == "tr" and self.row is not None:
            if self.row:
                self.rows.append(self.row)
            self.row = None


def fetch_html(url: str) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
        "Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language":"en-US,en;q=0.9",
        "Referer":"https://www.fftoday.com/",
        "Connection":"close",
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("latin-1", errors="replace")


def player_from_cell(cell: dict) -> str:
    bad = {"upside","risk","player","sort first","last"}
    for a in cell.get("anchors", []):
        x = " ".join(a.split())
        if any(ch.isalpha() for ch in x) and x.lower() not in bad and len(x) > 2:
            return x
    text = cell.get("text","")
    text = re.split(r"\b(?:Upside|Risk):?", text, maxsplit=1)[0]
    text = re.sub(r"\bImage:?\s*$", "", text).strip()
    return text


def parse_fftoday_page(html: str, position: str) -> list[dict]:
    p = TableParser(); p.feed(html)
    out = []
    for row in p.rows:
        cells = [c.get("text","").strip() for c in row]
        team_i = next((i for i,x in enumerate(cells) if x in TEAM_CODES), None)
        if team_i is None or team_i < 1 or team_i + 2 >= len(cells):
            continue
        player = player_from_cell(row[team_i-1])
        if not player or not any(ch.isalpha() for ch in player):
            continue
        tail = cells[team_i+1:]  # bye then stat columns
        try:
            vals = {"player_name":player}
            for stat, idx in LAYOUT[position]:
                vals[stat] = num(tail[idx])
        except (IndexError, ValueError):
            continue
        out.append(vals)
    dedup = {}
    for r in out:
        dedup[norm_name(r["player_name"])] = r
    return list(dedup.values())


def fetch_fftoday(season: int, position: str, expected_date: str) -> list[dict]:
    all_rows, seen = [], set()
    for page in range(0, 10):
        q = urllib.parse.urlencode({
            "LeagueID":1,"PosID":POS_ID[position],"Season":season,
            "cur_page":page,"order_by":"FName","sort_order":"ASC",
        })
        html = fetch_html("https://www.fftoday.com/rankings/playerproj.php?" + q)
        if page == 0:
            m = re.search(r"Updated:\s*(\d{1,2}/\d{1,2}/\d{4})", re.sub(r"<[^>]+>"," ",html), re.I)
            if not m:
                raise ValueError(f"{season} {position}: FFToday update date not found")
            actual_date = datetime.strptime(m.group(1), "%m/%d/%Y").date().isoformat()
            if actual_date != expected_date:
                raise ValueError(f"{season} {position}: expected {expected_date}, page shows {actual_date}")
        rows = parse_fftoday_page(html, position)
        new = 0
        for r in rows:
            k = norm_name(r["player_name"])
            if k not in seen:
                seen.add(k); all_rows.append(r); new += 1
        if page > 0 and new == 0:
            break
    if not all_rows:
        raise ValueError(f"{season} {position}: no FFToday rows parsed")
    return all_rows


def eligible_inventory(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [
        r for r in payload.get("sources",[])
        if r.get("source") == "FFToday" and r.get("status") == "ELIGIBLE_PRESEASON"
        and r.get("position") in POSITIONS and r.get("raw_stat_projection_available")
    ]


def native_predictions(rows: list[dict], target_season: int, position: str) -> dict:
    train = [r for r in rows if r["position"] == position and int(r["season"]) < target_season]
    test = [r for r in rows if r["position"] == position and int(r["season"]) == target_season]
    if not train or not test:
        raise ValueError(f"{target_season} {position}: empty native train/test")
    features = list(BASE_FEATURES[position]) + list(SELECTED[position])
    out = {}
    allowed_targets = set(TARGETS[position])
    for stat, target in NATIVE_TARGET.items():
        if target not in allowed_targets:
            continue
        alpha, _ = choose_alpha_temporally(train, features, target)
        X = [[float(r[f]) for f in features] for r in train]
        y = [float(r[target]) for r in train]
        model = RidgeModel(alpha).fit(X,y)
        for r, pred in zip(test, model.predict([[float(r[f]) for f in features] for r in test])):
            out[(norm_name(r["player_name"]),stat)] = float(pred)
    return out


def run(inventory_path: Path, start_season: int = 2016) -> dict:
    inv = eligible_inventory(inventory_path)
    if not inv:
        raise ValueError("no eligible FFToday snapshots")
    max_season = max(int(r["season"]) for r in inv)
    season_rows = []
    for season in range(start_season, max_season + 1):
        season_rows.extend(normalize_season(fetch_csv(season), season))
    lagged = enrich(make_lagged_rows(season_rows), season_rows, fetch_players())

    native, external, actual = {}, {}, {}
    coverage = []
    for item in inv:
        season, pos = int(item["season"]), item["position"]
        fft = fetch_fftoday(season, pos, item["snapshot_date"])
        nproj = native_predictions(lagged, season, pos)
        actual_rows = [r for r in lagged if int(r["season"]) == season and r["position"] == pos]
        actual_index = {norm_name(r["player_name"]): r for r in actual_rows}
        fft_index = {norm_name(r["player_name"]): r for r in fft}
        common_players = sorted(set(actual_index) & set(fft_index))
        common_stats = sorted(
            set(NATIVE_TARGET) & set(dict(LAYOUT[pos]).keys()) &
            {t.removeprefix("next_") for t in TARGETS[pos]}
        )
        matched_stat_rows = 0
        for name in common_players:
            ar = actual_index[name]; er = fft_index[name]
            for stat in common_stats:
                nk = (name,stat)
                target = NATIVE_TARGET[stat]
                if nk not in nproj or target not in ar or stat not in er:
                    continue
                key = (season,pos,name,stat)
                native[key] = nproj[nk]
                external[key] = float(er[stat])
                actual[key] = float(ar[target])
                matched_stat_rows += 1
        coverage.append({
            "season":season,"position":pos,"fftoday_players":len(fft),
            "native_actual_players":len(actual_rows),"common_players":len(common_players),
            "common_stats":common_stats,"common_stat_rows":matched_stat_rows,
            "snapshot_date":item["snapshot_date"],
        })

    result = compare(native, external, actual)
    result["experiment"] = "selected_fsffl_native_vs_fftoday_preseason_raw_stats"
    result["coverage"] = coverage
    result["native_model"] = {
        "base":"lag1+lag2 position-specific ridge",
        "selected_features":SELECTED,
        "training_rule":"strictly seasons before each target season",
        "alpha_selection":"temporal inner validation on pre-target seasons only",
    }
    result["external_source"] = {
        "name":"FFToday","eligibility":"only ELIGIBLE_PRESEASON inventory snapshots",
        "fantasy_points_used":False,
    }
    result["limitations"] = [
        "Common cohort excludes players not forecastable by both systems, including rookies absent from the native veteran-history path.",
        "FFToday does not expose every native target statistic; only shared raw football statistics are scored.",
        "This is one independent external source and therefore is diagnostic, not sufficient by itself for source/blend promotion.",
    ]
    return result


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--inventory",type=Path,default=Path("data/model_validation/historical_projection_source_inventory.json"))
    p.add_argument("--start-season",type=int,default=2016)
    p.add_argument("--output",type=Path,default=Path("data/model_validation/native_vs_fftoday_historical_benchmark.json"))
    a=p.parse_args()
    r=run(a.inventory,a.start_season)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(r,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps({
        "status":r["status"],"rows":r["common_stat_rows"],"groups":r["group_count"],
        "wins":r["group_wins"],"weighted_mae":r["weighted_common_cohort_mae"],
    },indent=2))

if __name__=="__main__":
    main()
