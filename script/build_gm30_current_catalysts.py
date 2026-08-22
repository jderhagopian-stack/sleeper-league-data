#!/usr/bin/env python3
"""
FSFFL GM 3.0 — Current Catalyst Ingestion

Augments data/football_intelligence_signals.json with CURRENT-season evidence:
- Sleeper add/drop velocity
- Sleeper injury/status and local depth-chart order changes
- nflverse/ESPN depth chart position and movement
- opportunity created by injuries ahead on the same NFL depth chart
- recent public FantasyPros player-news keyword corroboration

Persistent comparison state lives outside data/gm/ so GM output cleanup does not erase it.

Outputs:
  data/football_intelligence_signals.json
  data/gm3_current_catalyst_state.json
"""
from __future__ import annotations

import csv
import html
import io
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

DATA = Path("data")
INTEL = DATA / "football_intelligence_signals.json"
STATE = DATA / "gm3_current_catalyst_state.json"
POSITIONS = {"QB", "RB", "WR", "TE"}

SLEEPER_TREND = "https://api.sleeper.app/v1/players/nfl/trending/{kind}?lookback_hours={hours}&limit={limit}"
DEPTH_URL = "https://github.com/nflverse/nflverse-data/releases/download/depth_charts/depth_charts_{season}.csv"
FANTASYPROS_NEWS = "https://www.fantasypros.com/nfl/"

POSITIVE_NEWS = {
    "starter": "starter_reps",
    "starting": "starter_reps",
    "first-team": "starter_reps",
    "first team": "starter_reps",
    "impress": "camp_buzz",
    "standout": "camp_buzz",
    "breakout": "camp_buzz",
    "strong camp": "camp_buzz",
    "seeing year two jump": "camp_buzz",
    "bigger role": "preseason_role",
    "expanded role": "preseason_role",
    "more work": "preseason_role",
    "promoted": "depth_chart_rise",
    "moving up": "depth_chart_rise",
}
NEGATIVE_NEWS = {
    "demoted": "depth_chart_fall",
    "moving down": "depth_chart_fall",
    "struggl": "role_loss",
    "lost the job": "role_loss",
    "sidelined": "injury_concern",
    "injury": "injury_concern",
    "out for": "injury_concern",
    "placed on ir": "injury_concern",
    "season-ending": "injury_concern",
}


def load(path, default):
    try:
        with Path(path).open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)


def fetch_bytes(url, timeout=30):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "FSFFL-GM30/1.0",
            "Accept": "*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_json(url):
    return json.loads(fetch_bytes(url).decode("utf-8"))


def fetch_csv(url):
    return list(csv.DictReader(io.StringIO(fetch_bytes(url).decode("utf-8"))))


def norm_name(x):
    x = html.unescape(str(x or "")).lower()
    x = re.sub(r"[^a-z0-9 ]+", "", x)
    return re.sub(r"\s+", " ", x).strip()


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        if data and data.strip():
            self.parts.append(data.strip())


def public_news_text():
    try:
        raw = fetch_bytes(FANTASYPROS_NEWS, timeout=30).decode("utf-8", "ignore")
        parser = TextExtractor()
        parser.feed(raw)
        return " ".join(parser.parts), None
    except Exception as e:
        return "", str(e)


def sleeper_trends():
    adds, drops = {}, {}
    errors = []
    for hours, weight in ((24, 1.0), (72, 0.55)):
        for kind, dest in (("add", adds), ("drop", drops)):
            try:
                rows = fetch_json(SLEEPER_TREND.format(kind=kind, hours=hours, limit=200))
                for row in rows or []:
                    pid = str(row.get("player_id"))
                    count = float(row.get("count") or 0)
                    dest[pid] = dest.get(pid, 0.0) + count * weight
            except Exception as e:
                errors.append(f"SLEEPER_{kind.upper()}_{hours}H_FAILED")
    return adds, drops, errors


def latest_depth_rows(season):
    try:
        rows = fetch_csv(DEPTH_URL.format(season=season))
    except Exception as e:
        return {}, f"DEPTH_CHART_FETCH_FAILED: {e}"

    # 2025+ schema: dt, team, player_name, pos_grp, pos_rank, ...
    # Keep latest record per player name.
    latest = {}
    for r in rows:
        name = norm_name(r.get("player_name") or r.get("full_name") or "")
        if not name:
            continue
        dt = str(r.get("dt") or r.get("date") or "")
        rank_raw = r.get("pos_rank") or r.get("depth_team") or r.get("depth_chart_order")
        try:
            rank = int(float(rank_raw))
        except Exception:
            rank = None
        row = {
            "date": dt,
            "team": r.get("team") or r.get("club_code"),
            "pos_group": r.get("pos_grp") or r.get("position"),
            "pos_name": r.get("pos_name") or r.get("depth_position"),
            "rank": rank,
        }
        old = latest.get(name)
        if old is None or dt >= str(old.get("date") or ""):
            latest[name] = row
    return latest, None


def players_map():
    x = load(DATA / "players.json", {}) or {}
    if isinstance(x, list):
        x = {str(r.get("player_id")): r for r in x if isinstance(r, dict)}
    return x


def rostered_ids():
    out = set()
    for r in load(DATA / "rosters.json", []) or []:
        out.update(str(x) for x in (r.get("players") or []))
    return out


def base_record():
    return {
        "depth_chart_rise": False,
        "starter_reps": False,
        "camp_buzz": False,
        "preseason_role": False,
        "injury_opportunity": False,
        "coach_praise": False,
        "depth_chart_fall": False,
        "injury_concern": False,
        "role_loss": False,
        "evidence": [],
    }


def add_evidence(rec, signal, source, detail, strength):
    rec[signal] = True
    rec["evidence"].append({
        "signal": signal,
        "source": source,
        "detail": detail,
        "strength": strength,
    })


def main():
    intel = load(INTEL, {}) or {}
    season = int(intel.get("active_season") or (load(DATA / "league.json", {}) or {}).get("season"))
    phase = str(intel.get("season_phase") or "UNKNOWN")

    players = players_map()
    rostered = rostered_ids()
    prior_state = load(STATE, {}) or {}
    prior_players = prior_state.get("players") or {}

    adds, drops, trend_errors = sleeper_trends()
    depth, depth_error = latest_depth_rows(season)
    news_text, news_error = public_news_text()
    news_norm = norm_name(news_text)

    manual = {}
    preseason_usage = dict(intel.get("preseason_usage") or {})
    current_state = {}

    # Build a same-team position-group map so an injury ahead can create opportunity.
    team_groups = {}
    for pid, p in players.items():
        if not isinstance(p, dict):
            continue
        pos = str(p.get("position") or "").upper()
        if pos not in POSITIONS:
            continue
        d = depth.get(norm_name(p.get("full_name")))
        if not d:
            continue
        key = (str(d.get("team") or p.get("team") or ""), str(d.get("pos_group") or pos))
        team_groups.setdefault(key, []).append((pid, p, d))

    for key in team_groups:
        team_groups[key].sort(key=lambda x: x[2].get("rank") if x[2].get("rank") is not None else 99)

    for pid, p in players.items():
        if not isinstance(p, dict):
            continue
        pos = str(p.get("position") or "").upper()
        if pos not in POSITIONS or p.get("active") is False:
            continue

        name = p.get("full_name") or p.get("name")
        if not name:
            continue
        nn = norm_name(name)
        d = depth.get(nn) or {}
        injury = p.get("injury_status")
        local_order = p.get("depth_chart_order")
        add_score = float(adds.get(str(pid), 0))
        drop_score = float(drops.get(str(pid), 0))
        prior = prior_players.get(str(pid), {}) or {}
        rec = base_record()

        current_state[str(pid)] = {
            "name": name,
            "team": p.get("team"),
            "injury_status": injury,
            "sleeper_depth_chart_order": local_order,
            "external_depth_rank": d.get("rank"),
            "external_depth_date": d.get("date"),
            "trending_add_score": round(add_score, 2),
            "trending_drop_score": round(drop_score, 2),
        }

        # 1) Depth chart movement: strongest automatable role-change signal.
        old_rank = prior.get("external_depth_rank")
        new_rank = d.get("rank")
        if old_rank is not None and new_rank is not None:
            if int(new_rank) < int(old_rank):
                add_evidence(rec, "depth_chart_rise", "nflverse_depth_chart",
                             f"depth rank improved {old_rank}->{new_rank}", 0.90)
            elif int(new_rank) > int(old_rank):
                add_evidence(rec, "depth_chart_fall", "nflverse_depth_chart",
                             f"depth rank fell {old_rank}->{new_rank}", 0.90)

        old_local = prior.get("sleeper_depth_chart_order")
        if old_local is not None and local_order is not None:
            try:
                if int(local_order) < int(old_local):
                    add_evidence(rec, "depth_chart_rise", "sleeper_player_metadata",
                                 f"Sleeper depth order improved {old_local}->{local_order}", 0.75)
                elif int(local_order) > int(old_local):
                    add_evidence(rec, "depth_chart_fall", "sleeper_player_metadata",
                                 f"Sleeper depth order fell {old_local}->{local_order}", 0.75)
            except Exception:
                pass

        # 2) Injury change for the player.
        old_injury = prior.get("injury_status")
        if injury and injury != old_injury:
            add_evidence(rec, "injury_concern", "sleeper_player_metadata",
                         f"injury status changed to {injury}", 0.90)
        elif old_injury and not injury:
            add_evidence(rec, "preseason_role", "sleeper_player_metadata",
                         "cleared prior injury designation", 0.65)

        # 3) Opportunity from an injured player ahead in the same depth group.
        if d:
            key = (str(d.get("team") or p.get("team") or ""), str(d.get("pos_group") or pos))
            for ahead_pid, ahead_p, ahead_d in team_groups.get(key, []):
                if ahead_pid == str(pid):
                    continue
                arank, prank = ahead_d.get("rank"), d.get("rank")
                if arank is None or prank is None or int(arank) >= int(prank):
                    continue
                if ahead_p.get("injury_status"):
                    add_evidence(rec, "injury_opportunity", "depth_chart_plus_sleeper_injury",
                                 f"player ahead ({ahead_p.get('full_name')}) has injury status {ahead_p.get('injury_status')}",
                                 0.88)
                    break

        # 4) Sleeper market velocity is supporting corroboration, not sufficient alone.
        # Strong adds plus top-two depth position can qualify as preseason role evidence.
        if add_score >= 150 and (new_rank is not None and int(new_rank) <= 2):
            add_evidence(rec, "preseason_role", "sleeper_trending",
                         f"strong add velocity score {round(add_score,1)} with depth rank {new_rank}", 0.62)
        if drop_score >= 150 and drop_score > add_score * 1.25:
            add_evidence(rec, "role_loss", "sleeper_trending",
                         f"strong drop velocity score {round(drop_score,1)}", 0.45)

        # 5) Public news corroboration. Require the player's full name in page text,
        # then only store matched signal keywords; do not reproduce article prose.
        if len(nn.split()) >= 2 and nn in news_norm:
            window_start = max(0, news_norm.find(nn) - 180)
            window_end = min(len(news_norm), news_norm.find(nn) + len(nn) + 240)
            window = news_norm[window_start:window_end]
            for phrase, signal in POSITIVE_NEWS.items():
                if norm_name(phrase) in window:
                    add_evidence(rec, signal, "fantasypros_public_news",
                                 f"recent public news matched signal keyword: {phrase}", 0.55)
            for phrase, signal in NEGATIVE_NEWS.items():
                if norm_name(phrase) in window:
                    add_evidence(rec, signal, "fantasypros_public_news",
                                 f"recent public news matched signal keyword: {phrase}", 0.55)

        if rec["evidence"]:
            rec["player_id"] = str(pid)
            rec["name"] = name
            rec["team"] = p.get("team")
            rec["position"] = pos
            rec["fsffl_rostered"] = str(pid) in rostered
            rec["trending_add_score"] = round(add_score, 2)
            rec["trending_drop_score"] = round(drop_score, 2)
            rec["depth_rank"] = new_rank
            rec["injury_status"] = injury
            rec["evidence_count"] = len(rec["evidence"])
            rec["max_evidence_strength"] = max(x["strength"] for x in rec["evidence"])
            manual[str(pid)] = rec

    # Preserve any manually curated records by merging them over automatic records.
    old_manual = intel.get("manual_intelligence") or {}
    for pid, old in old_manual.items():
        if pid not in manual:
            manual[pid] = old
        elif isinstance(old, dict):
            # Keep explicit human flags/evidence without deleting automatic evidence.
            for k, v in old.items():
                if k == "evidence" and isinstance(v, list):
                    manual[pid].setdefault("evidence", []).extend(v)
                elif k not in manual[pid] or (isinstance(v, bool) and v):
                    manual[pid][k] = v

    intel["manual_intelligence"] = manual
    intel["manual_intelligence_records"] = len(manual)
    intel["preseason_usage"] = preseason_usage
    intel["preseason_usage_records"] = len(preseason_usage)
    intel["current_catalyst_ingestion"] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "season": season,
        "season_phase": phase,
        "automatic_catalyst_records": len(manual),
        "sleeper_trending_add_players": len(adds),
        "sleeper_trending_drop_players": len(drops),
        "depth_chart_players_matched": len(depth),
        "public_news_available": bool(news_text),
        "sources": {
            "sleeper_trending": "Sleeper API trending add/drop",
            "sleeper_player_metadata": "local data/players.json",
            "depth_charts": "nflverse depth_charts release (ESPN-derived for current seasons)",
            "public_news": "FantasyPros public NFL feed page",
        },
    }

    warnings = [w for w in (intel.get("warnings") or [])
                if w not in {"CAMP_NEWS_INTELLIGENCE_NOT_YET_INGESTED",
                             "PRESEASON_USAGE_NOT_YET_INGESTED"}]
    warnings.extend(trend_errors)
    if depth_error:
        warnings.append("CURRENT_DEPTH_CHART_UNAVAILABLE")
    if news_error:
        warnings.append("PUBLIC_CAMP_NEWS_UNAVAILABLE")
    if not manual:
        warnings.append("NO_CURRENT_CATALYSTS_DETECTED")
    if phase == "PRESEASON" and not preseason_usage:
        warnings.append("STRUCTURED_PRESEASON_USAGE_NOT_AVAILABLE")
    intel["warnings"] = sorted(set(warnings))

    save(INTEL, intel)
    save(STATE, {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "season": season,
        "phase": phase,
        "players": current_state,
    })

    print(f"Current Catalyst Ingestion: {len(manual)} player records")
    print(f"Sleeper trend universe: {len(adds)} adds / {len(drops)} drops")
    print(f"Depth chart records: {len(depth)}")
    print(f"Public news available: {bool(news_text)}")
    if intel["warnings"]:
        print("Warnings:", ", ".join(intel["warnings"]))


if __name__ == "__main__":
    main()
