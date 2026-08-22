#!/usr/bin/env python3
"""
FSFFL GM 3.0 — Current Catalyst Ingestion v4

Current-season evidence:
- Sleeper add/drop velocity as market context only
- Sleeper injury/status and depth-order changes
- nflverse depth-chart movement
- DIRECT next-man-up opportunity only when the player ahead is materially unavailable
- CBS Sports player-specific public-news corroboration

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
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

DATA = Path("data")
INTEL = DATA / "football_intelligence_signals.json"
STATE = DATA / "gm3_current_catalyst_state.json"
POSITIONS = {"QB", "RB", "WR", "TE"}

SLEEPER_TREND = (
    "https://api.sleeper.app/v1/players/nfl/trending/"
    "{kind}?lookback_hours={hours}&limit={limit}"
)
DEPTH_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "depth_charts/depth_charts_{season}.csv"
)
CBS_PLAYER_NEWS_URLS = (
    "https://www.cbssports.com/fantasy/football/players/news/all/",
    "https://new.cbssports.com/fantasy/football/players/news/all/",
)

# Only phrases that can reasonably describe a player's own current role.
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

# Avoid the generic word "injury"; it created too much false association.
NEGATIVE_NEWS = {
    "demoted": "depth_chart_fall",
    "moving down": "depth_chart_fall",
    "struggl": "role_loss",
    "lost the job": "role_loss",
    "sidelined": "injury_concern",
    "out for": "injury_concern",
    "placed on ir": "injury_concern",
    "season-ending": "injury_concern",
}

# "Questionable" is deliberately excluded. A direct player ahead being merely
# questionable is not enough to create a fantasy-relevant opportunity catalyst.
MATERIALLY_UNAVAILABLE = {
    "out",
    "ir",
    "injured reserve",
    "pup",
    "physically unable to perform",
    "nfi",
    "non-football injury",
    "doubtful",
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
        headers={"User-Agent": "FSFFL-GM30/1.0", "Accept": "*/*"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_json(url):
    return json.loads(fetch_bytes(url).decode("utf-8"))


def fetch_csv(url):
    return list(
        csv.DictReader(
            io.StringIO(fetch_bytes(url).decode("utf-8"))
        )
    )


def norm_name(x):
    x = html.unescape(str(x or "")).lower()
    x = re.sub(r"[^a-z0-9 ]+", "", x)
    return re.sub(r"\s+", " ", x).strip()


def norm_status(x):
    return norm_name(x)


def materially_unavailable(status):
    s = norm_status(status)
    if not s:
        return False
    return any(token == s or token in s for token in MATERIALLY_UNAVAILABLE)


class CBSPlayerNewsExtractor(HTMLParser):
    """Extract ordered headings/text from CBS NFL Player News.

    CBS exposes a player-by-player fantasy news feed. We use heading boundaries
    to create one local news entry at a time instead of flattening the page.
    """

    HEADING_TAGS = {"h2", "h3", "h4", "h5"}
    TEXT_TAGS = {"p", "a", "span", "div"}

    def __init__(self):
        super().__init__()
        self.heading_depth = 0
        self.heading_parts = []
        self.text_depth = 0
        self.text_parts = []
        self.items = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in self.HEADING_TAGS:
            self.heading_depth += 1
            if self.heading_depth == 1:
                self.heading_parts = []
        elif tag in self.TEXT_TAGS:
            self.text_depth += 1
            if self.text_depth == 1:
                self.text_parts = []

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in self.HEADING_TAGS and self.heading_depth:
            self.heading_depth -= 1
            if self.heading_depth == 0:
                txt = " ".join(self.heading_parts).strip()
                if txt:
                    self.items.append(("heading", txt))
                self.heading_parts = []
        elif tag in self.TEXT_TAGS and self.text_depth:
            self.text_depth -= 1
            if self.text_depth == 0:
                txt = " ".join(self.text_parts).strip()
                if txt:
                    self.items.append(("text", txt))
                self.text_parts = []

    def handle_data(self, data):
        if not data or not data.strip():
            return
        s = data.strip()
        if self.heading_depth:
            self.heading_parts.append(s)
        if self.text_depth:
            self.text_parts.append(s)


def dedupe_ordered_items(items):
    out = []
    last = None
    for kind, raw in items:
        norm = norm_name(raw)
        if not norm:
            continue
        if len(norm) < 8 or len(norm) > 1400:
            continue
        key = (kind, norm)
        if key == last:
            continue
        out.append(key)
        last = key
    return out


def build_cbs_news_entries(items):
    """Create heading-bounded player-news entries."""
    entries = []
    current = None

    for kind, txt in items:
        if kind == "heading":
            if current and current["parts"]:
                entries.append(current)
            current = {"heading": txt, "parts": [txt]}
            continue

        if current is not None and len(current["parts"]) < 7:
            current["parts"].append(txt)

    if current and current["parts"]:
        entries.append(current)

    bounded = []
    seen = set()
    for entry in entries:
        combined = " ".join(entry["parts"])
        if len(combined) < 25 or len(combined) > 2200:
            continue
        if combined in seen:
            continue
        seen.add(combined)
        bounded.append(
            {
                "heading": entry["heading"],
                "text": combined,
            }
        )
    return bounded


def public_player_news_entries():
    errors = []
    for url in CBS_PLAYER_NEWS_URLS:
        try:
            raw = fetch_bytes(url, 30).decode("utf-8", "ignore")
            p = CBSPlayerNewsExtractor()
            p.feed(raw)
            items = dedupe_ordered_items(p.items)
            entries = build_cbs_news_entries(items)
            if entries:
                return entries, None, url
            errors.append(f"{url}: NO_PLAYER_NEWS_ENTRIES")
        except Exception as e:
            errors.append(f"{url}: {e}")

    return [], " | ".join(errors), None


def player_news_entries(entries, player_name):
    """Require the player's full normalized name inside the bounded entry."""
    return [
        entry
        for entry in entries
        if player_name in entry.get("text", "")
    ]


def player_local_windows(entry_text, player_name, radius=340):
    """Signal phrases must be local to the named player within the entry."""
    out = []
    start = 0

    while True:
        idx = entry_text.find(player_name, start)
        if idx < 0:
            break

        lo = max(0, idx - radius)
        hi = min(
            len(entry_text),
            idx + len(player_name) + radius,
        )
        out.append(entry_text[lo:hi])
        start = idx + len(player_name)

    return out

def sleeper_trends():
    adds, drops, errors = {}, {}, []
    for hours, weight in ((24, 1.0), (72, 0.55)):
        for kind, dest in (("add", adds), ("drop", drops)):
            try:
                url = SLEEPER_TREND.format(
                    kind=kind,
                    hours=hours,
                    limit=200,
                )
                for row in fetch_json(url) or []:
                    pid = str(row.get("player_id"))
                    count = float(row.get("count") or 0)
                    dest[pid] = dest.get(pid, 0.0) + count * weight
            except Exception:
                errors.append(
                    f"SLEEPER_{kind.upper()}_{hours}H_FAILED"
                )
    return adds, drops, errors


def latest_depth_rows(season):
    try:
        rows = fetch_csv(DEPTH_URL.format(season=season))
    except Exception as e:
        return {}, f"DEPTH_CHART_FETCH_FAILED: {e}"

    latest = {}
    for r in rows:
        name = norm_name(
            r.get("player_name")
            or r.get("full_name")
            or ""
        )
        if not name:
            continue

        dt = str(r.get("dt") or r.get("date") or "")
        raw = (
            r.get("pos_rank")
            or r.get("depth_team")
            or r.get("depth_chart_order")
        )
        try:
            rank = int(float(raw))
        except Exception:
            rank = None

        row = {
            "date": dt,
            "team": r.get("team") or r.get("club_code"),
            "pos_group": r.get("pos_grp") or r.get("position"),
            "pos_name": r.get("pos_name") or r.get("depth_position"),
            "rank": rank,
        }

        if (
            name not in latest
            or dt >= str(latest[name].get("date") or "")
        ):
            latest[name] = row

    return latest, None


def players_map():
    x = load(DATA / "players.json", {}) or {}
    if isinstance(x, list):
        x = {
            str(r.get("player_id")): r
            for r in x
            if isinstance(r, dict)
        }
    return x


def rostered_ids():
    out = set()
    for r in load(DATA / "rosters.json", []) or []:
        out.update(str(x) for x in (r.get("players") or []))
    return out


def base_record():
    return {
        k: False
        for k in (
            "depth_chart_rise",
            "starter_reps",
            "camp_buzz",
            "preseason_role",
            "injury_opportunity",
            "coach_praise",
            "depth_chart_fall",
            "injury_concern",
            "role_loss",
        )
    } | {"evidence": []}


def add_evidence(rec, signal, source, detail, strength):
    # One source should not artificially "corroborate" itself via several
    # matching synonyms from the same article/data record.
    for existing in rec["evidence"]:
        if (
            existing.get("signal") == signal
            and existing.get("source") == source
        ):
            if strength > float(existing.get("strength") or 0):
                existing["strength"] = strength
                existing["detail"] = detail
            rec[signal] = True
            return

    rec[signal] = True
    rec["evidence"].append(
        {
            "signal": signal,
            "source": source,
            "detail": detail,
            "strength": strength,
        }
    )


def main():
    intel = load(INTEL, {}) or {}
    season = int(
        intel.get("active_season")
        or (load(DATA / "league.json", {}) or {}).get("season")
    )
    phase = str(intel.get("season_phase") or "UNKNOWN")

    players = players_map()
    rostered = rostered_ids()
    prior_state = load(STATE, {}) or {}
    prior_players = prior_state.get("players") or {}

    adds, drops, trend_errors = sleeper_trends()
    depth, depth_error = latest_depth_rows(season)
    news_entries, news_error, news_source_url = public_player_news_entries()

    manual = {}
    preseason_usage = dict(intel.get("preseason_usage") or {})
    current_state = {}

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

        key = (
            str(d.get("team") or p.get("team") or ""),
            str(d.get("pos_group") or pos),
        )
        team_groups.setdefault(key, []).append((str(pid), p, d))

    for key in team_groups:
        team_groups[key].sort(
            key=lambda x: (
                x[2].get("rank")
                if x[2].get("rank") is not None
                else 99
            )
        )

    for pid, p in players.items():
        if not isinstance(p, dict):
            continue

        pos = str(p.get("position") or "").upper()
        if pos not in POSITIONS or p.get("active") is False:
            continue

        name = p.get("full_name") or p.get("name")
        if not name:
            continue

        pid = str(pid)
        nn = norm_name(name)
        d = depth.get(nn) or {}

        injury = p.get("injury_status")
        local_order = p.get("depth_chart_order")
        add_score = float(adds.get(pid, 0))
        drop_score = float(drops.get(pid, 0))

        prior = prior_players.get(pid, {}) or {}
        rec = base_record()

        current_state[pid] = {
            "name": name,
            "team": p.get("team"),
            "injury_status": injury,
            "sleeper_depth_chart_order": local_order,
            "external_depth_rank": d.get("rank"),
            "external_depth_date": d.get("date"),
            "trending_add_score": round(add_score, 2),
            "trending_drop_score": round(drop_score, 2),
        }

        # Objective depth-chart movement between runs.
        old_rank = prior.get("external_depth_rank")
        new_rank = d.get("rank")
        if old_rank is not None and new_rank is not None:
            if int(new_rank) < int(old_rank):
                add_evidence(
                    rec,
                    "depth_chart_rise",
                    "nflverse_depth_chart",
                    f"depth rank improved {old_rank}->{new_rank}",
                    0.90,
                )
            elif int(new_rank) > int(old_rank):
                add_evidence(
                    rec,
                    "depth_chart_fall",
                    "nflverse_depth_chart",
                    f"depth rank fell {old_rank}->{new_rank}",
                    0.90,
                )

        old_local = prior.get("sleeper_depth_chart_order")
        if old_local is not None and local_order is not None:
            try:
                if int(local_order) < int(old_local):
                    add_evidence(
                        rec,
                        "depth_chart_rise",
                        "sleeper_player_metadata",
                        f"Sleeper depth order improved {old_local}->{local_order}",
                        0.75,
                    )
                elif int(local_order) > int(old_local):
                    add_evidence(
                        rec,
                        "depth_chart_fall",
                        "sleeper_player_metadata",
                        f"Sleeper depth order fell {old_local}->{local_order}",
                        0.75,
                    )
            except Exception:
                pass

        # Injury status on the player is risk evidence. Clearing an injury is
        # intentionally NOT labeled "preseason_role"; health != role expansion.
        old_injury = prior.get("injury_status")
        if injury and injury != old_injury:
            add_evidence(
                rec,
                "injury_concern",
                "sleeper_player_metadata",
                f"injury status changed to {injury}",
                0.90,
            )

        # DIRECT NEXT-MAN-UP ONLY, and only when that direct player is materially
        # unavailable. A questionable designation does not create opportunity.
        if d:
            key = (
                str(d.get("team") or p.get("team") or ""),
                str(d.get("pos_group") or pos),
            )
            group = team_groups.get(key, [])
            idx = next(
                (i for i, x in enumerate(group) if x[0] == pid),
                None,
            )

            if idx is not None and idx > 0:
                ahead_pid, ahead_p, ahead_d = group[idx - 1]
                ahead_status = ahead_p.get("injury_status")
                if materially_unavailable(ahead_status):
                    add_evidence(
                        rec,
                        "injury_opportunity",
                        "direct_next_man_up",
                        (
                            f"direct player ahead "
                            f"({ahead_p.get('full_name')}) is materially "
                            f"unavailable: {ahead_status}"
                        ),
                        0.88,
                    )

        # Sleeper trends are market/context information, not proof of a football
        # role. They remain in current_state and output metadata but no longer
        # manufacture preseason_role evidence.
        if (
            drop_score >= 150
            and drop_score > add_score * 1.25
        ):
            add_evidence(
                rec,
                "role_loss",
                "sleeper_trending",
                f"strong drop velocity score {round(drop_score, 1)}",
                0.45,
            )

        # CBS Player News is organized story-by-story. Require the player's
        # full name in the bounded entry and a signal phrase in a local window
        # around that same name.
        if len(nn.split()) >= 2:
            matched_entries = player_news_entries(news_entries, nn)

            for entry_idx, entry in enumerate(matched_entries):
                local_windows = player_local_windows(
                    entry["text"],
                    nn,
                )

                for window_idx, window in enumerate(local_windows):
                    source = (
                        f"cbs_player_news_"
                        f"{entry_idx + 1}_{window_idx + 1}"
                    )

                    for phrase, signal in POSITIVE_NEWS.items():
                        if norm_name(phrase) in window:
                            add_evidence(
                                rec,
                                signal,
                                source,
                                (
                                    "CBS player-news entry matched "
                                    f"positive signal keyword: {phrase}"
                                ),
                                0.60,
                            )

                    for phrase, signal in NEGATIVE_NEWS.items():
                        if norm_name(phrase) in window:
                            add_evidence(
                                rec,
                                signal,
                                source,
                                (
                                    "CBS player-news entry matched "
                                    f"negative signal keyword: {phrase}"
                                ),
                                0.60,
                            )

        if rec["evidence"]:
            rec.update(
                {
                    "player_id": pid,
                    "name": name,
                    "team": p.get("team"),
                    "position": pos,
                    "fsffl_rostered": pid in rostered,
                    "trending_add_score": round(add_score, 2),
                    "trending_drop_score": round(drop_score, 2),
                    "depth_rank": new_rank,
                    "injury_status": injury,
                    "evidence_count": len(rec["evidence"]),
                    "max_evidence_strength": max(
                        x["strength"] for x in rec["evidence"]
                    ),
                }
            )
            manual[pid] = rec

    # Preserve only truly curated/manual evidence from previous intelligence.
    old_manual = intel.get("manual_intelligence") or {}
    for pid, old in old_manual.items():
        if not isinstance(old, dict):
            continue

        old_evidence = old.get("evidence") or []
        curated = [
            x
            for x in old_evidence
            if (
                isinstance(x, dict)
                and str(x.get("source", "")).startswith("manual")
            )
        ]
        if not curated:
            continue

        if pid not in manual:
            manual[pid] = old
            manual[pid]["evidence"] = curated
        else:
            for e in curated:
                add_evidence(
                    manual[pid],
                    str(e.get("signal") or ""),
                    str(e.get("source") or "manual"),
                    str(e.get("detail") or "manual curated evidence"),
                    float(e.get("strength") or 0),
                )

    intel["manual_intelligence"] = manual
    intel["manual_intelligence_records"] = len(manual)
    intel["preseason_usage"] = preseason_usage
    intel["preseason_usage_records"] = len(preseason_usage)

    intel["current_catalyst_ingestion"] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_version": "FSFFL-GM-3.0-Current-Catalysts-v4-CBS-Player-News",
        "season": season,
        "season_phase": phase,
        "automatic_catalyst_records": len(manual),
        "sleeper_trending_add_players": len(adds),
        "sleeper_trending_drop_players": len(drops),
        "depth_chart_players_matched": len(depth),
        "public_news_entries": len(news_entries),
        "public_news_available": bool(news_entries),
        "public_news_source": news_source_url,
        "injury_opportunity_policy": (
            "DIRECT_NEXT_MAN_UP_AND_MATERIALLY_UNAVAILABLE_ONLY"
        ),
        "sleeper_add_velocity_policy": (
            "MARKET_CONTEXT_ONLY_NOT_ROLE_EVIDENCE"
        ),
        "injury_clearance_policy": (
            "HEALTH_UPDATE_ONLY_NOT_ROLE_EVIDENCE"
        ),
        "public_news_policy": "CBS_PLAYER_ENTRY_PLUS_PLAYER_LOCAL_WINDOW",
    }

    warnings = [
        w
        for w in (intel.get("warnings") or [])
        if w
        not in {
            "CAMP_NEWS_INTELLIGENCE_NOT_YET_INGESTED",
            "PRESEASON_USAGE_NOT_YET_INGESTED",
        }
    ]
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
    save(
        STATE,
        {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "season": season,
            "phase": phase,
            "players": current_state,
        },
    )

    print(
        f"Current Catalyst v4: {len(manual)} records; "
        "audited evidence rules active."
    )
    if intel["warnings"]:
        print("Warnings:", ", ".join(intel["warnings"]))


if __name__ == "__main__":
    main()
