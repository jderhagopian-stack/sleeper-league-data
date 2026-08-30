#!/usr/bin/env python3
"""Evidence-grounded roster context for user-facing FSFFL reports.

This module does not score or re-rank anything. It connects authoritative GM3
and Simulator outputs into analyst-style explanatory sentences.
"""
from __future__ import annotations
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
INDEX=ROOT/"data"/"gm"/"franchise_index.json"
ASSETS=ROOT/"data"/"fsffl_asset_values.json"
SIM=ROOT/"data"/"gm"/"league"/"simulator_context.json"


def _load(path,default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError,json.JSONDecodeError,TypeError):
        return default


def _sf(v,d=0.0):
    try: return float(v)
    except (TypeError,ValueError): return d


def _team_entry(uid):
    idx=_load(INDEX,{}) or {}
    return next((x for x in idx.get("teams") or [] if str(x.get("user_id"))==str(uid)),None)


def _player_position_map():
    d=_load(ASSETS,{}) or {}
    out={}
    for x in d.get("players") or []:
        name=str(x.get("name") or "").strip()
        if name:
            out[name]={"position":x.get("position"),"player_id":str(x.get("player_id") or "")}
    return out


def team_context(uid):
    entry=_team_entry(uid)
    if not entry:
        return {}
    paths=entry.get("paths") or {}
    command=_load(ROOT/(paths.get("command_center") or ""),{}) or {}
    profile=_load(ROOT/(paths.get("strategic_asset_profiles") or ""),{}) or {}
    players=profile.get("players") or profile.get("assets") or []
    need_rows=command.get("biggest_position_needs") or []
    needs={str(x.get("position")):_sf(x.get("need_score")) for x in need_rows if x.get("position")}
    sim=_load(SIM,{}) or {}
    sim_teams=sim.get("teams") or []
    sim_row=next((x for x in sim_teams if str(x.get("user_id"))==str(uid)),{})
    title_rows=sorted(sim_teams,key=lambda x:_sf(x.get("championship_probability")),reverse=True)
    title_rank=next((i for i,x in enumerate(title_rows,1) if str(x.get("user_id"))==str(uid)),None)
    return {
        "entry":entry,
        "command":command,
        "players":players,
        "needs":needs,
        "sim":sim_row,
        "title_rank":title_rank,
        "league_size":len(sim_teams),
    }


def competitive_context(uid):
    """Describe team strength continuously; categorical state thresholds are provisional."""
    ctx=team_context(uid)
    if not ctx:
        return ""
    command=ctx.get("command") or {}
    score=_sf(command.get("contender_score"),0.5)
    dynasty=_sf(command.get("dynasty_roster_score"),0.5)
    sim=ctx.get("sim") or {}
    title=_sf(sim.get("championship_probability"))*100
    rank=ctx.get("title_rank"); total=ctx.get("league_size")
    cal=_load(STATE_CALIBRATION,{}) or {}
    thresholds=cal.get("classification_thresholds") or {}
    contender=_sf(thresholds.get("contender"),0.55)
    delta=score-contender
    parts=[f"GM3's contender score is {score:.3f} and long-term roster-strength score is {dynasty:.3f}."]
    if abs(delta)<=.05:
        side="above" if delta>=0 else "below"
        parts.append(
            f"The contender score is only {abs(delta):.3f} {side} the model's provisional contender boundary, so the categorical state label should be treated as borderline rather than definitive."
        )
    else:
        parts.append("GM3's categorical state thresholds remain a governed expert prior rather than an empirically validated classification.")
    if title>0:
        if rank and total:
            parts.append(f"The current Simulator gives this team {title:.1f}% championship odds, ranking #{rank} of {total}.")
        else:
            parts.append(f"The current Simulator gives this team {title:.1f}% championship odds.")
    return " ".join(parts)


def roster_change_context(roster_diagnosis):
    """Explain what a hypothetical transaction fixes and what remains afterward."""
    rd=roster_diagnosis or {}
    before=(rd.get("before") or {}).get("position_need") or {}
    after=(rd.get("after") or {}).get("position_need") or {}
    if not before or not after:
        return ""
    positions=("QB","RB","WR","TE")
    rows=[]
    for pos in positions:
        b=_sf(before.get(pos)); a=_sf(after.get(pos))
        rows.append((pos,b,a,a-b))
    biggest_before=max(rows,key=lambda x:x[1])
    biggest_after=max(rows,key=lambda x:x[2])
    improved=sorted([x for x in rows if x[3] < -.02], key=lambda x:x[3])
    worsened=sorted([x for x in rows if x[3] > .02], key=lambda x:x[3], reverse=True)
    parts=[]
    if improved:
        pos,b,a,d=improved[0]
        parts.append(f"The biggest structural improvement is at {pos}: need falls from {b:.3f} to {a:.3f}.")
    elif all(abs(x[3])<=.02 for x in rows):
        parts.append("The transaction changes player quality but does not materially alter the roster's positional-need profile.")
    if biggest_before[0] != biggest_after[0]:
        parts.append(
            f"Before the move, {biggest_before[0]} is the largest need; afterward, {biggest_after[0]} becomes the largest remaining need at {biggest_after[2]:.3f}."
        )
    else:
        parts.append(
            f"{biggest_after[0]} remains the largest need after the move at {biggest_after[2]:.3f}, so the trade improves the roster without fully solving that weakness."
        )
    if worsened:
        pos,b,a,d=worsened[0]
        parts.append(f"The main new cost is at {pos}, where need rises from {b:.3f} to {a:.3f}.")
    else:
        parts.append("No other position becomes materially weaker under the GM3 need calculation.")
    return " ".join(parts)


def analyst_roster_context(uid,outgoing_names=None,incoming_names=None):
    """Return reporter-style roster context supported entirely by existing outputs."""
    ctx=team_context(uid)
    if not ctx:
        return ""
    outgoing={str(x).strip() for x in (outgoing_names or []) if x}
    incoming={str(x).strip() for x in (incoming_names or []) if x}
    profile_by_name={str(x.get("name") or "").strip():x for x in ctx["players"] if x.get("name")}
    global_players=_player_position_map()
    needs=ctx["needs"]
    sentences=[]

    comp=competitive_context(uid)
    if comp:
        sentences.append(comp)

    incoming_positions=[]
    for name in incoming:
        row=profile_by_name.get(name) or global_players.get(name) or {}
        pos=row.get("position")
        if pos:
            incoming_positions.append((name,str(pos),_sf(needs.get(str(pos)),0.5)))
    if incoming_positions:
        best=max(incoming_positions,key=lambda x:x[2])
        name,pos,need=best
        if need>=.80:
            sentences.append(
                f"{pos} is a major roster weakness (need score {need:.3f}), so acquiring {name} directly attacks one of the team's clearest problems."
            )
        elif need>=.60:
            sentences.append(
                f"{pos} is one of the team's more important needs (need score {need:.3f}), which makes {name} especially relevant to this roster."
            )
        elif need<=.30:
            sentences.append(
                f"{pos} is already a relative strength (need score {need:.3f}), so {name} must create value through quality rather than simply filling a hole."
            )

    for name in outgoing:
        row=profile_by_name.get(name)
        if not row or row.get("asset_type") not in (None,"player"):
            continue
        pos=str(row.get("position") or "")
        starter=bool(row.get("is_current_optimal_starter"))
        need=_sf(needs.get(pos),0.5)
        same=[x for x in ctx["players"] if str(x.get("position") or "")==pos and x.get("asset_type") in (None,"player")]
        starters=[x for x in same if x.get("is_current_optimal_starter")]
        if not starter and need<=.35:
            sentences.append(
                f"{name} does not currently make the optimized starting lineup, and {pos} is a relative strength with {len(same)} rostered options and {len(starters)} current optimized starters. That reduces the weekly opportunity cost of moving him."
            )
        elif not starter:
            sentences.append(
                f"{name} does not currently make the optimized starting lineup, so his value to this roster is driven more by depth and future value than immediate weekly scoring."
            )
        elif starter and need>=.65:
            sentences.append(
                f"{name} is currently an optimized starter at a position where the roster is already thin, so moving him creates a real lineup/depth cost that the return has to overcome."
            )

    # Explain relative positional construction when both sides involve players.
    if incoming_positions:
        in_need=max(x[2] for x in incoming_positions)
        out_positions=[]
        for name in outgoing:
            row=profile_by_name.get(name) or global_players.get(name) or {}
            pos=row.get("position")
            if pos:
                out_positions.append((name,str(pos),_sf(needs.get(str(pos)),0.5)))
        if out_positions:
            weakest_out=min(out_positions,key=lambda x:x[2])
            if in_need-weakest_out[2]>=.35:
                sentences.append(
                    f"Structurally, the deal shifts value from a stronger position group toward a substantially weaker one, which is why the same market value can be more useful after the trade."
                )
    return " ".join(sentences)


def canonical_simulator_team(uid):
    """Return the published canonical Simulator row for a team, if available."""
    sim=_load(SIM,{}) or {}
    return next(
        (x for x in sim.get("teams") or [] if str(x.get("user_id"))==str(uid)),
        {}
    )
