#!/usr/bin/env python3
"""
FSFFL GM 3.0 — Current Catalyst Ingestion v3.1

Current-season evidence:
- Sleeper add/drop velocity as market context only
- Sleeper injury/status and depth-order changes
- nflverse depth-chart movement
- DIRECT next-man-up opportunity only when the player ahead is materially unavailable
- article-bounded public-news corroboration

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
FANTASYPROS_NEWS = "https://www.fantasypros.com/nfl/"

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


class NewsBlockExtractor(HTMLParser):
    """Extract bounded, news-like DOM blocks without flattening the whole page.

    FantasyPros may render news cards as <div>, <section>, <li>, or <a> rather
    than literal <article> elements. We capture only semantically news-like
    containers and individual links, then later require the player's own name
    and the signal phrase to occur locally inside the same bounded block.
    """

    BLOCK_TAGS = {"article", "section", "li", "div"}
    HINTS = (
        "news",
        "article",
        "story",
        "post",
        "card",
        "player",
        "headline",
        "update",
        "feed",
        "analysis",
    )

    def __init__(self):
        super().__init__()
        self.stack = []
        self.blocks = []
        self.anchor_stack = []

    @staticmethod
    def attrs_text(attrs):
        vals = []
        for k, v in attrs:
            if k in {"class", "id", "role", "data-testid", "data-cy"} and v:
                vals.append(str(v).lower())
        return " ".join(vals)

    @classmethod
    def news_like_container(cls, tag, attrs):
        if tag == "article":
            return True
        attr_text = cls.attrs_text(attrs)
        return any(h in attr_text for h in cls.HINTS)

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()

        # Advance nesting depth of already-open captured blocks.
        for block in self.stack:
            block["depth"] += 1

        if tag in self.BLOCK_TAGS and self.news_like_container(tag, attrs):
            self.stack.append({"tag": tag, "depth": 1, "parts": []})

        if tag == "a":
            href = ""
            for k, v in attrs:
                if k == "href" and v:
                    href = str(v)
                    break
            self.anchor_stack.append(
                {
                    "href": href,
                    "parts": [],
                }
            )

    def handle_endtag(self, tag):
        tag = tag.lower()

        if tag == "a" and self.anchor_stack:
            anchor = self.anchor_stack.pop()
            txt = " ".join(anchor["parts"]).strip()
            href = anchor["href"].lower()
            if txt and (
                "/nfl/" in href
                or "news" in href
                or "player" in href
                or "fantasy" in href
            ):
                self.blocks.append(txt)

        closing = []
        for i, block in enumerate(self.stack):
            block["depth"] -= 1
            if block["depth"] == 0:
                closing.append(i)

        for i in reversed(closing):
            block = self.stack.pop(i)
            txt = " ".join(block["parts"]).strip()
            if txt:
                self.blocks.append(txt)

    def handle_data(self, data):
        if not data or not data.strip():
            return
        s = data.strip()

        for block in self.stack:
            block["parts"].append(s)

        for anchor in self.anchor_stack:
            anchor["parts"].append(s)


def dedupe_news_blocks(blocks):
    """Normalize, bound, and de-duplicate extracted news chunks."""
    seen = set()
    out = []

    for raw in blocks:
        norm = norm_name(raw)
        if not norm:
            continue

        # Reject giant page/feed containers and tiny navigation fragments.
        if len(norm) < 25 or len(norm) > 1800:
            continue

        if norm in seen:
            continue
        seen.add(norm)
        out.append(norm)

    return out


def public_news_blocks():
    try:
        raw = fetch_bytes(FANTASYPROS_NEWS, 30).decode("utf-8", "ignore")
        p = NewsBlockExtractor()
        p.feed(raw)
        blocks = dedupe_news_blocks(p.blocks)

        if not blocks:
            return [], "NO_BOUNDED_PUBLIC_NEWS_BLOCKS_FOUND"

        return blocks, None
    except Exception as e:
        return [], str(e)


def player_local_windows(block, player_name, radius=260):
    """Return only local context around this player's own name inside a block."""
    out = []
    start = 0

    while True:
        idx = block.find(player_name, start)
        if idx < 0:
            break

        lo = max(0, idx - radius)
        hi = min(len(block), idx + len(player_name) + radius)
        out.append(block[lo:hi])
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
    news_blocks, news_error = public_news_blocks()

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

        # Public-news evidence must be inside a bounded news-like DOM block,
        # and the signal phrase must also be local to THIS player's own name.
        # This is much stricter than page-wide proximity, but robust to sites
        # that render cards as div/section/li/a instead of literal article tags.
        if len(nn.split()) >= 2:
            player_blocks = [
                block
                for block in news_blocks
                if nn in block
            ]

            for block_idx, block in enumerate(player_blocks):
                local_windows = player_local_windows(block, nn)

                for window_idx, window in enumerate(local_windows):
                    source = (
                        f"fantasypros_player_block_"
                        f"{block_idx + 1}_{window_idx + 1}"
                    )

                    for phrase, signal in POSITIVE_NEWS.items():
                        if norm_name(phrase) in window:
                            add_evidence(
                                rec,
                                signal,
                                source,
                                (
                                    "player-local bounded news block matched "
                                    f"positive signal keyword: {phrase}"
                                ),
                                0.55,
                            )

                    for phrase, signal in NEGATIVE_NEWS.items():
                        if norm_name(phrase) in window:
                            add_evidence(
                                rec,
                                signal,
                                source,
                                (
                                    "player-local bounded news block matched "
                                    f"negative signal keyword: {phrase}"
                                ),
                                0.55,
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
        "model_version": "FSFFL-GM-3.0-Current-Catalysts-v3.1-Bounded-News-Blocks",
        "season": season,
        "season_phase": phase,
        "automatic_catalyst_records": len(manual),
        "sleeper_trending_add_players": len(adds),
        "sleeper_trending_drop_players": len(drops),
        "depth_chart_players_matched": len(depth),
        "public_news_blocks": len(news_blocks),
        "public_news_available": bool(news_blocks),
        "injury_opportunity_policy": (
            "DIRECT_NEXT_MAN_UP_AND_MATERIALLY_UNAVAILABLE_ONLY"
        ),
        "sleeper_add_velocity_policy": (
            "MARKET_CONTEXT_ONLY_NOT_ROLE_EVIDENCE"
        ),
        "injury_clearance_policy": (
            "HEALTH_UPDATE_ONLY_NOT_ROLE_EVIDENCE"
        ),
        "public_news_policy": "BOUNDED_DOM_BLOCK_PLUS_PLAYER_LOCAL_WINDOW",
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
        f"Current Catalyst v3.1: {len(manual)} records; "
        "audited evidence rules active."
    )
    if intel["warnings"]:
        print("Warnings:", ", ".join(intel["warnings"]))


if __name__ == "__main__":
    main()
