#!/usr/bin/env python3
"""Render FSFFL Historical Trade Review 2.0.

The report deliberately separates:
1) ANALYSIS AT THE TIME - only information available at the transaction date,
2) DEAL IN HINDSIGHT - factual asset lineage and observed outcomes after the trade.

Internal model terminology remains in JSON for auditability; the PDF is written
as a fantasy-football decision product.
"""
from __future__ import annotations
import argparse, json, re
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether
)

MODEL_VERSION="FSFFL-GM-Historical-Trade-Report-2.2"
NAVY=colors.HexColor("#132238")
LIGHT=colors.HexColor("#F2F5F8")
MID=colors.HexColor("#D9E1E8")
INK=colors.HexColor("#18222C")
MUTED=colors.HexColor("#5D6A75")
WHITE=colors.white


def sf(v,d=0.0):
    try:return float(v)
    except (TypeError,ValueError):return d

def pp(v):return f"{sf(v)*100:+.1f} pts"
def num(v,d=2):return f"{sf(v):+.{d}f}"
def val(v):return f"{sf(v):+,.0f}"

def clean(x,n=180):
    s=str(x or "").replace("\u2013","-").replace("\u2014","-").replace("\u2019","'")
    s=s.encode("ascii","ignore").decode("ascii")
    s=s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
    return s if len(s)<=n else s[:n-1].rstrip()+"..."

def styles():
    return {
        "title":ParagraphStyle("title",fontName="Helvetica-Bold",fontSize=18,leading=20,textColor=NAVY),
        "sub":ParagraphStyle("sub",fontName="Helvetica",fontSize=8.3,leading=10,textColor=MUTED),
        "hero":ParagraphStyle("hero",fontName="Helvetica-Bold",fontSize=12,leading=14,textColor=WHITE),
        "hero2":ParagraphStyle("hero2",fontName="Helvetica",fontSize=8,leading=10,textColor=WHITE),
        "section":ParagraphStyle("section",fontName="Helvetica-Bold",fontSize=10.2,leading=12,textColor=NAVY,spaceBefore=3,spaceAfter=4),
        "body":ParagraphStyle("body",fontName="Helvetica",fontSize=7.5,leading=9.3,textColor=INK),
        "small":ParagraphStyle("small",fontName="Helvetica",fontSize=6.4,leading=7.8,textColor=MUTED),
        "team":ParagraphStyle("team",fontName="Helvetica-Bold",fontSize=10,leading=12,textColor=NAVY),
        "label":ParagraphStyle("label",fontName="Helvetica-Bold",fontSize=7,leading=8.2,textColor=MUTED),
        "metric":ParagraphStyle("metric",fontName="Helvetica-Bold",fontSize=9.3,leading=10.5,textColor=INK),
    }

DECISION_LABELS={
    "accept":"TAKE THE DEAL",
    "lean_accept":"LEAN ACCEPT",
    "accept_retool_value":"TAKE THE VALUE",
    "competitive_upgrade_at_premium":"BIG UPGRADE, BUT EXPENSIVE",
    "lean_reject_value":"PASS AT THIS PRICE",
    "lean_reject":"LEAN PASS",
    "reject_competitive_damage":"PASS - HURTS THE WINDOW",
    "needs_context":"TOO CLOSE TO CALL",
}

def team_label(uid,sides):
    x=sides.get(str(uid)) or {}
    return clean(x.get("team_name") or x.get("manager") or uid,60)

def pick_only_label(label):
    """Return a draft asset without revealing the player it later became."""
    s=str(label or "")
    if re.match(r"^20\d{2} \d+\.\d{2} - ",s):
        return s.split(" - ",1)[0]
    return s

def team_state_label(raw):
    x=str(raw or "unknown").lower()
    labels={
        "contender":"CONTENDER - Prioritizing winning now",
        "retool":"RETOOL - Balancing current competitiveness with future value",
        "rebuild":"REBUILD - Prioritizing future value",
        "unknown":"NOT CLEARLY CLASSIFIED - Team direction is uncertain",
    }
    return labels.get(x,f"{str(raw or 'Not clearly classified').replace('_',' ').upper()} - Team direction as classified by GM 3.0")

def display_asset_name(raw,side):
    name=str(raw or "")
    if not name.startswith("pick:"):
        return name
    h=side.get("hindsight") or {}
    candidates=[]
    candidates.extend(((h.get("asset_lineage") or {}).get("root_assets") or []))
    candidates.extend(((h.get("asset_lineage") or {}).get("terminal_assets") or []))
    candidates.extend(((h.get("keep_assets_reference") or {}).get("assets") or []))
    for x in candidates:
        if str(x.get("asset_key"))==name and x.get("label"):
            return pick_only_label(x.get("label"))
    return name


def hindsight_verdict(report,sides):
    h=report.get("hindsight_assessment") or {}
    cls=str(h.get("classification") or "")
    winner=h.get("winner_user_id")
    if cls=="CLEAR_HINDSIGHT_EDGE" and winner:
        return f"HINDSIGHT EDGE: {team_label(winner,sides).upper()}", (
            f"{team_label(winner,sides)} leads both in fantasy production actually received from the trade "
            "and in the long-term value still owned from the players and picks that came from it."
        )
    if cls=="SPLIT_HINDSIGHT_RESULT":
        return "HINDSIGHT RESULT: SPLIT", (
            "One side has received more fantasy production so far, while the other still owns more long-term value from what the trade eventually became. "
            "Those are different kinds of wins, so the report does not force them into one combined score."
        )
    if cls=="NEAR_EVEN_HINDSIGHT":
        return "HINDSIGHT RESULT: NEAR EVEN", (
            "The two sides are close both in fantasy production received so far and in the long-term value they still own from the trade."
        )
    return "HINDSIGHT RESULT: STILL DEVELOPING", (
        "Too many of the players and picks from the trade are still unresolved to name a clear hindsight winner."
    )


def fit_label(v):
    v=sf(v); a=abs(v)
    if a>=2500:return "Major positive" if v>0 else "Major negative"
    if a>=900:return "Meaningful positive" if v>0 else "Meaningful negative"
    if a>=250:return "Slight positive" if v>0 else "Slight negative"
    return "Near neutral"

def value_label(v):
    v=sf(v); a=abs(v)
    if a>=2500:return "Major bargain" if v>0 else "Major overpay"
    if a>=900:return "Meaningful bargain" if v>0 else "Meaningful overpay"
    if a>=250:return "Slight bargain" if v>0 else "Slight overpay"
    return "Roughly even value"

def win_context(v):
    a=abs(sf(v))
    if a>=2.0:return "massive season-long swing"
    if a>=1.0:return "major season-long swing"
    if a>=0.5:return "meaningful season-long swing"
    if a>=0.2:return "small season-long swing"
    return "minimal season-long effect"

def odds_context(v):
    a=abs(sf(v))
    if a>=.10:return "major change"
    if a>=.05:return "meaningful change"
    if a>=.02:return "small change"
    return "minimal change"

def score_context(v):
    v=sf(v); a=abs(v)
    if a>=2500:return "major positive overall effect" if v>0 else "major negative overall effect"
    if a>=900:return "meaningful positive overall effect" if v>0 else "meaningful negative overall effect"
    if a>=250:return "slight positive overall effect" if v>0 else "slight negative overall effect"
    return "roughly neutral overall effect"

def current_value_context(v):
    a=abs(sf(v))
    if a>=12000:return "very high remaining value"
    if a>=8000:return "high remaining value"
    if a>=4000:return "strong remaining value"
    if a>=1500:return "moderate remaining value"
    if a>0:return "limited remaining value"
    return "no remaining model value"

def at_time_bottom_line(uids,results,sides,winner):
    if not winner or winner=="TIE" or len(uids)<2:
        return "At the time, the deal graded as essentially even after balancing immediate competitive impact against long-term value."
    winner_uid=str(winner)
    loser_uid=next((str(u) for u in uids if str(u)!=winner_uid),None)
    if loser_uid is None:
        return f"At the time, the overall edge went to {team_label(winner_uid,sides)}."
    wr=results.get(winner_uid) or {}
    lr=results.get(loser_uid) or {}
    wd=wr.get("delta") or {}; ld=lr.get("delta") or {}
    ws=wr.get("strategic") or {}; ls=lr.get("strategic") or {}
    loser_name=team_label(loser_uid,sides)
    winner_name=team_label(winner_uid,sides)
    wins=sf(ld.get("expected_wins"))
    playoff=sf(ld.get("playoff_probability"))
    pkg=sf(ls.get("package_effective_value_delta",ls.get("intrinsic_dynasty_delta")))
    winner_wins=sf(wd.get("expected_wins"))
    winner_pkg=sf(ws.get("package_effective_value_delta",ws.get("intrinsic_dynasty_delta")))
    pieces=[f"At the time, the overall edge went to {winner_name}."]
    if wins>=0.5:
        pieces.append(
            f"{loser_name} gained about {wins:.2f} expected wins ({win_context(wins)})"
            + (f" and {playoff*100:.1f} percentage points of playoff probability ({odds_context(playoff)})" if playoff>=0.03 else "")
            + "."
        )
    elif wins<=-0.5:
        pieces.append(f"{loser_name} lost about {abs(wins):.2f} expected wins ({win_context(wins)}).")
    if pkg<=-1000:
        pieces.append(
            f"But {loser_name} gave up about {abs(pkg):,.0f} more long-term value points than it received - "
            f"a {value_label(pkg).lower()} on the model's relative value scale."
        )
    elif pkg>=1000:
        pieces.append(
            f"{loser_name} also gained about {pkg:,.0f} long-term value points - "
            f"a {value_label(pkg).lower()} on the model's relative value scale."
        )
    if winner_wins<=-0.5 and winner_pkg>=1000:
        pieces.append(
            f"{winner_name} accepted a short-term cost of about {abs(winner_wins):.2f} expected wins, "
            "but received enough long-term value to justify that sacrifice."
        )
    return " ".join(pieces)

def plain_reason(row):
    d=row.get("delta") or {}; st=row.get("strategic") or {}
    wins=sf(d.get("expected_wins")); playoff=sf(d.get("playoff_probability"))
    pkg=sf(st.get("package_effective_value_delta"),sf(st.get("intrinsic_dynasty_delta")))
    fit=sf(row.get("state_aware_utility_delta"))
    bits=[]
    if wins>=0.5:
        bits.append(f"adds about {wins:.1f} expected wins ({win_context(wins)})")
    elif wins<=-0.5:
        bits.append(f"costs about {abs(wins):.1f} expected wins ({win_context(wins)})")
    if playoff>=0.03:
        bits.append(f"raises playoff odds by {playoff*100:.1f} percentage points ({odds_context(playoff)})")
    elif playoff<=-0.03:
        bits.append(f"lowers playoff odds by {abs(playoff)*100:.1f} percentage points ({odds_context(playoff)})")
    if pkg>=1000:
        bits.append(f"comes out ahead by about {pkg:,.0f} long-term value points ({value_label(pkg).lower()})")
    elif pkg<=-1000:
        bits.append(f"gives up about {abs(pkg):,.0f} more long-term value points than it receives ({value_label(pkg).lower()})")
    if not bits:
        bits.append("lands near neutral across the model's main trade drivers")
    explanation="; ".join(bits)+f". Taken together, the deal has a {score_context(fit)} for this roster."
    if wins>=0.5 and pkg<=-1000:
        explanation+=" The competitive gain is real, but GM 3.0 judges the premium paid to be larger than the incremental upgrade justifies."
    elif wins<=-0.5 and pkg>=1000:
        explanation+=" The near-term hit is real, but GM 3.0 judges the value captured as sufficient compensation for that competitive cost."
    return explanation

def at_time_card(uid,row,side,s):
    st=row.get("strategic") or {}; d=row.get("delta") or {}
    dec=(row.get("decision") or {}).get("band")
    rec=[display_asset_name(x.get("name") or x.get("asset_id"),side) for x in st.get("received") or []]
    sent=[display_asset_name(x.get("name") or x.get("asset_id"),side) for x in st.get("sent") or []]
    metrics=[
        [Paragraph("Expected wins",s["label"]),Paragraph(
             f"{num(d.get('expected_wins'),2)}<br/><font size='6.0'>{win_context(d.get('expected_wins'))}</font>",s["metric"]),
         Paragraph("Playoff odds",s["label"]),Paragraph(
             f"{pp(d.get('playoff_probability'))}<br/><font size='6.0'>{odds_context(d.get('playoff_probability'))}</font>",s["metric"])],
        [Paragraph("Championship odds",s["label"]),Paragraph(
             f"{pp(d.get('championship_probability'))}<br/><font size='6.0'>{odds_context(d.get('championship_probability'))}</font>",s["metric"]),
         Paragraph("Overall deal effect",s["label"]),Paragraph(
             f"{score_context(row.get('state_aware_utility_delta')).title()}",s["body"])],
        [Paragraph("Long-term trade value",s["label"]),Paragraph(
             f"{val(st.get('package_effective_value_delta',st.get('intrinsic_dynasty_delta')))} model pts<br/><font size='6.0'>{value_label(st.get('package_effective_value_delta',st.get('intrinsic_dynasty_delta')))}</font>",s["body"]),
         Paragraph("Fit for this roster",s["label"]),Paragraph(
             f"{score_context(st.get('base_franchise_value_delta')).title()}",s["body"])],
    ]
    body=[
        [Paragraph(team_label(uid,{str(uid):side}),s["team"])],
        [Paragraph(f"<b>Model verdict:</b> {DECISION_LABELS.get(str(dec),clean(str(dec).replace('_',' ').upper(),50))}<br/>"
                   f"<b>Team situation:</b> {clean(team_state_label(row.get('team_state')),70)}<br/>"
                   f"<b>Receives:</b> {clean(', '.join(rec) or 'None',145)}<br/>"
                   f"<b>Gives up:</b> {clean(', '.join(sent) or 'None',145)}",s["body"])],
        [Table(metrics,colWidths=[.89*inch,.86*inch,.88*inch,.93*inch],style=TableStyle([
            ("VALIGN",(0,0),(-1,-1),"MIDDLE"),("LEFTPADDING",(0,0),(-1,-1),2),
            ("RIGHTPADDING",(0,0),(-1,-1),2),("TOPPADDING",(0,0),(-1,-1),2),
            ("BOTTOMPADDING",(0,0),(-1,-1),2)
        ]))],
        [Paragraph("<b>Why:</b> "+clean(plain_reason(row),520)+
                   "<br/><font size='6.1'>Model points are relative trade-value scores, not fantasy points or dollars; the label beside each score shows whether the gap is small, meaningful or major.</font>",s["body"])],
    ]
    return Table(body,colWidths=[3.70*inch],style=TableStyle([
        ("BACKGROUND",(0,0),(-1,-1),LIGHT),("BOX",(0,0),(-1,-1),.55,MID),
        ("LEFTPADDING",(0,0),(-1,-1),7),("RIGHTPADDING",(0,0),(-1,-1),7),
        ("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),
    ]))

def event_sentence(ev,root_labels=None):
    text=str(ev.get("description") or "")
    for key,label in (root_labels or {}).items():
        if str(key).startswith("pick:") and label:
            text=text.replace(str(label),pick_only_label(label))
    return clean(text,180)

def hindsight_scorecard(report,sides,uids,s):
    rows=[]
    metrics=[]
    for uid in uids:
        lin=((sides.get(uid) or {}).get("hindsight") or {}).get("asset_lineage") or {}
        prod=lin.get("captured_production") or {}
        impact=prod.get("replacement_adjusted_impact") or {}
        metrics.append({
            "uid":uid,
            "team":team_label(uid,sides),
            "points":sf(prod.get("captured_fsffl_points")),
            "remaining":sf(lin.get("terminal_current_intrinsic_value")),
            "wins":sf(impact.get("estimated_wins_added_vs_average_starter")),
        })
    if len(metrics)<2:
        return Paragraph("Hindsight scorecard unavailable.",s["body"])
    a,b=metrics[0],metrics[1]
    def lead(key,fmt):
        if abs(a[key]-b[key]) < 1e-9:
            return "Even"
        x=a if a[key]>b[key] else b
        return f"{x['team']} ({fmt(x[key])})"
    hw=(report.get("hindsight_assessment") or {}).get("winner_user_id")
    overall="Still developing" if not hw else team_label(str(hw),sides)
    data=[
        [Paragraph("<b>HINDSIGHT SCORECARD</b>",s["label"]),Paragraph("<b>LEADER</b>",s["label"])],
        [Paragraph("Fantasy production received",s["body"]),Paragraph(lead("points",lambda v:f"{v:,.0f} pts"),s["body"])],
        [Paragraph("Estimated wins added vs. average starter",s["body"]),Paragraph(lead("wins",lambda v:f"{v:+.2f} wins"),s["body"])],
        [Paragraph("Long-term value still owned",s["body"]),Paragraph(lead("remaining",lambda v:f"{v:,.0f} value pts"),s["body"])],
        [Paragraph("Overall hindsight result",s["body"]),Paragraph(overall,s["body"])],
    ]
    return Table(data,colWidths=[3.65*inch,4.05*inch],style=TableStyle([
        ("BACKGROUND",(0,0),(-1,0),LIGHT),("BOX",(0,0),(-1,-1),.45,MID),
        ("INNERGRID",(0,0),(-1,-1),.25,MID),("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("LEFTPADDING",(0,0),(-1,-1),6),("RIGHTPADDING",(0,0),(-1,-1),6),
        ("TOPPADDING",(0,0),(-1,-1),3),("BOTTOMPADDING",(0,0),(-1,-1),3),
    ]))


def verdict_change_story(report,sides,uids,winner):
    h=report.get("hindsight_assessment") or {}
    hw=h.get("winner_user_id")
    if not hw:
        return "The trade is still too unresolved to say what ultimately reinforced or changed the original verdict."
    at_name=team_label(str(winner),sides) if winner and winner!="TIE" else "Neither side"
    hi_name=team_label(str(hw),sides)
    leader_bits=[]
    for uid in uids:
        lin=((sides.get(uid) or {}).get("hindsight") or {}).get("asset_lineage") or {}
        impact=((lin.get("captured_production") or {}).get("replacement_adjusted_impact") or {})
        top=(impact.get("player_rows") or [])
        if top:
            x=top[0]
            leader_bits.append(
                f"{team_label(uid,sides)}'s biggest on-field contributor was {x.get('player_name')} "
                f"at an estimated {sf(x.get('estimated_wins_added')):+.2f} wins versus an average starter"
            )
    if winner and winner!="TIE" and str(hw)==str(winner):
        lead=f"The original verdict held: {at_name} was preferred at the time and still leads in hindsight."
    else:
        lead=f"The hindsight result changed from the original at-time edge ({at_name}) to {hi_name}."
    if leader_bits:
        lead+=" "+"; ".join(leader_bits)+"."
    return lead


def lineage_card(uid,side,s):
    h=(side.get("hindsight") or {})
    lin=h.get("asset_lineage") or {}
    roots=", ".join(pick_only_label(x.get("label") or x.get("asset_key")) for x in lin.get("root_assets") or [])
    root_labels={str(x.get("asset_key")):str(x.get("label") or "") for x in lin.get("root_assets") or []}
    terminals=", ".join(
        f"{x.get('label')} ({sf(x.get('current_intrinsic_value')):,.0f} value pts; {current_value_context(x.get('current_intrinsic_value'))})"
        for x in lin.get("terminal_assets") or []
    ) or "No tracked descendant player/pick remains in the lineage."
    events=lin.get("events") or []
    bullets="<br/>".join(f"- {event_sentence(e,root_labels)}" for e in events[:6]) or "- No downstream transformation was recorded."
    if len(events)>6:
        bullets+=f"<br/>- ...plus {len(events)-6} additional lineage events."
    prod=(lin.get("captured_production") or {})
    total_pts=sf(prod.get("captured_fsffl_points"))
    started_pts=sf(prod.get("captured_started_points"))
    top=prod.get("player_rows") or []
    impact=prod.get("replacement_adjusted_impact") or {}
    impact_rows=impact.get("player_rows") or []
    biggest=(impact_rows[0] if impact_rows else None)
    top_text=", ".join(
        f"{clean(x.get('player_name'),24)} {sf(x.get('fsffl_points_while_rostered')):,.1f}"
        for x in top[:4]
    ) or "No recorded lineage-player production."
    mixed=int(lin.get("mixed_attribution_events") or 0)
    warning=(f"<br/><b>Trade-chain note:</b> {mixed} later trade(s) combined assets from this deal with unrelated pieces. The full return is shown, but this original trade is not given credit for the entire later package."
             if mixed else "")
    return Table([
        [Paragraph(team_label(uid,{str(uid):side}),s["team"])],
        [Paragraph(f"<b>Original return:</b> {clean(roots,175)}<br/>"
                   f"<b>Fantasy production received since the trade:</b> {total_pts:,.1f} FSFFL points ({started_pts:,.1f} scored while in the starting lineup)<br/>"
                   f"<b>Estimated wins added vs. average starter:</b> {sf(impact.get('estimated_wins_added_vs_average_starter')):+.2f} "
                   f"(from {sf(impact.get('points_above_average_starter')):+.1f} started points above the seasonal position benchmark)<br/>"
                   + (f"<b>Biggest hindsight contributor:</b> {clean(biggest.get('player_name'),28)} "
                      f"({sf(biggest.get('estimated_wins_added')):+.2f} estimated wins above average starter)<br/>" if biggest else "")
                   + f"<b>Top trade-derived scorers:</b> {clean(top_text,180)}<br/>"
                   f"<b>Long-term value still owned from the return:</b> {sf(lin.get('terminal_current_intrinsic_value')):,.0f} model pts ({current_value_context(lin.get('terminal_current_intrinsic_value'))})<br/>"
                   f"<b>Where the assets ended up:</b> {clean(terminals,220)}",s["body"])],
        [Paragraph("<b>What the original assets became</b><br/>"+bullets+warning,s["body"])],
    ],colWidths=[3.70*inch],style=TableStyle([
        ("BACKGROUND",(0,0),(-1,-1),LIGHT),("BOX",(0,0),(-1,-1),.55,MID),
        ("LEFTPADDING",(0,0),(-1,-1),7),("RIGHTPADDING",(0,0),(-1,-1),7),
        ("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),
    ]))


def keep_reference(side,s):
    ref=((side.get("hindsight") or {}).get("keep_assets_reference") or {})
    rows=[]
    for x in ref.get("assets") or []:
        if x.get("reference_type")=="observed_player_output_after_trade":
            outcome=(f"{sf(x.get('observed_post_trade_points')):,.1f} league fantasy points after the trade; "
                     f"current long-term value {sf(x.get('current_intrinsic_value')):,.0f} model pts "
                     f"({current_value_context(x.get('current_intrinsic_value'))})")
        elif x.get("drafted_player"):
            outcome=(f"slot became {clean(x.get('drafted_player'),38)}; "
                     f"{sf(x.get('observed_drafted_player_points')):,.1f} league fantasy points since drafted; "
                     f"current long-term value {sf(x.get('current_intrinsic_value')):,.0f} model pts "
                     f"({current_value_context(x.get('current_intrinsic_value'))})")
        else:
            outcome="no completed draft conversion yet"
        rows.append(f"<b>{clean(pick_only_label(x.get('label')),75)}:</b> {outcome}")
    text="<br/>".join(rows) or "No keep-reference assets were available."
    summary=(f"<br/><b>Hold-reference totals:</b> {sf(ref.get('observed_reference_points')):,.1f} league fantasy points; "
             f"{sf(ref.get('current_reference_intrinsic_value')):,.0f} current long-term value points "
             f"({current_value_context(ref.get('current_reference_intrinsic_value'))}).")
    note=clean(ref.get("note"),300).replace("keep-the-original-assets","hold-the-original-assets").replace("keep-reference","hold reference")
    return Paragraph(text+summary+"<br/><font size='6.3'>"+note+"</font>",s["body"])


def audit_footer(canvas,doc):
    if canvas.getPageNumber()!=2:
        return
    canvas.saveState()
    y=.20*inch
    canvas.setStrokeColor(MID)
    canvas.setLineWidth(.35)
    canvas.line(.40*inch,y+.19*inch,8.10*inch,y+.19*inch)
    canvas.setFont("Helvetica",5.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(.40*inch,y,"AUDIT / METHODOLOGY NOTES | Point-in-time GM 3.0 reconstruction; paired simulations; current-day values excluded from at-the-time grade;")
    canvas.drawString(.40*inch,y-.08*inch,"asset-lineage hindsight kept separate; mixed-package attribution explicitly flagged. "+MODEL_VERSION)
    canvas.restoreState()

def build(report,out):
    s=styles()
    doc=SimpleDocTemplate(str(out),pagesize=letter,leftMargin=.40*inch,rightMargin=.40*inch,topMargin=.32*inch,bottomMargin=.48*inch)
    gm3=report.get("gm3_evaluation") or {}; sides=report.get("sides") or {}
    uids=list(report.get("participant_user_ids") or sides.keys())
    results=gm3.get("team_results") or {}
    ass=gm3.get("bilateral_assessment") or {}
    winner=ass.get("state_aware_utility_winner_user_id")
    winner_name="TIE" if winner=="TIE" or not winner else team_label(winner,sides)

    story=[
        Paragraph("FSFFL HISTORICAL TRADE REVIEW",s["title"]),
        Paragraph(f"Trade date: {clean(report.get('trade_time_utc'),45)} | GM 3.0 point-in-time reconstruction",s["sub"]),
        Spacer(1,.07*inch),
        Table([[Paragraph("ANALYSIS AT THE TIME",s["hero"])],
               [Paragraph(f"GM 3.0's at-the-time overall edge: <b>{winner_name}</b>. This grade uses only information available when the trade happened; later results are excluded.",s["hero2"])]],
              colWidths=[7.7*inch],style=TableStyle([
                  ("BACKGROUND",(0,0),(-1,-1),NAVY),("LEFTPADDING",(0,0),(-1,-1),11),
                  ("RIGHTPADDING",(0,0),(-1,-1),11),("TOPPADDING",(0,0),(-1,-1),7),
                  ("BOTTOMPADDING",(0,0),(-1,-1),6)])),
        Spacer(1,.08*inch)
    ]
    if len(uids)>=2:
        story.append(Table([[at_time_card(uids[0],results.get(uids[0]) or {},sides.get(uids[0]) or {},s),
                             at_time_card(uids[1],results.get(uids[1]) or {},sides.get(uids[1]) or {},s)]],
                           colWidths=[3.78*inch,3.78*inch],style=TableStyle([
                               ("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),0),
                               ("RIGHTPADDING",(0,0),(-1,-1),4)])))
    story += [
        Spacer(1,.08*inch),
        Paragraph("BOTTOM LINE AT THE TIME",s["section"]),
        Paragraph(clean(at_time_bottom_line(uids,results,sides,winner),760),s["body"]),
        Spacer(1,.05*inch),
        PageBreak(),
        Paragraph("DEAL IN HINDSIGHT",s["title"]),
        Paragraph("What actually happened to the assets after the trade | This section never changes the original GM 3.0 grade",s["sub"]),
        Spacer(1,.07*inch),
        Table([[Paragraph(hindsight_verdict(report,sides)[0],s["hero"])],
               [Paragraph(hindsight_verdict(report,sides)[1],s["hero2"])],
               [Paragraph("This section follows what each original player or pick eventually became, the fantasy points those resulting players produced, and the long-term value that still remains. If a later trade mixed these assets with unrelated pieces, the original deal is not given credit for the entire return.",s["hero2"])]],
              colWidths=[7.7*inch],style=TableStyle([
                  ("BACKGROUND",(0,0),(-1,-1),NAVY),("LEFTPADDING",(0,0),(-1,-1),11),
                  ("RIGHTPADDING",(0,0),(-1,-1),11),("TOPPADDING",(0,0),(-1,-1),7),
                  ("BOTTOMPADDING",(0,0),(-1,-1),6)])),
        Spacer(1,.06*inch),
        hindsight_scorecard(report,sides,uids,s),
        Spacer(1,.06*inch),
        Paragraph("WHAT THE ASSETS BECAME & WHAT THEY PRODUCED",s["section"])
    ]
    if len(uids)>=2:
        story.append(Table([[lineage_card(uids[0],sides.get(uids[0]) or {},s),
                             lineage_card(uids[1],sides.get(uids[1]) or {},s)]],
                           colWidths=[3.78*inch,3.78*inch],style=TableStyle([
                               ("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),0),
                               ("RIGHTPADDING",(0,0),(-1,-1),4)])))
    story += [Spacer(1,.08*inch),Paragraph("HOLD-THE-ORIGINAL-ASSETS REFERENCE",s["section"])]
    for uid in uids:
        story.append(KeepTogether([
            Paragraph(f"<b>{team_label(uid,sides)}</b>",s["body"]),
            keep_reference(sides.get(uid) or {},s),
            Spacer(1,.04*inch),
        ]))
    story += [
        Paragraph("HOW TO READ THE HOLD REFERENCE",s["section"]),
        Paragraph(
            "The hold reference shows what the surrendered players actually produced after the trade and, for surrendered draft picks, the player actually selected at that slot. It is not a claim about exactly what would have happened if the trade had never occurred, because later trades, draft choices, waiver moves and lineup decisions could also have changed. It is simply a useful reference point.",
            s["body"]),
        Spacer(1,.05*inch),
        Paragraph("WHAT REINFORCED OR CHANGED THE VERDICT",s["section"]),
        Paragraph(clean(verdict_change_story(report,sides,uids,winner),620),s["body"]),
    ]
    doc.build(story,onFirstPage=audit_footer,onLaterPages=audit_footer)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--input",required=True)
    ap.add_argument("--output",required=True)
    a=ap.parse_args()
    report=json.loads(Path(a.input).read_text(encoding="utf-8"))
    out=Path(a.output); out.parent.mkdir(parents=True,exist_ok=True)
    build(report,out)
    print(json.dumps({"report_model_version":MODEL_VERSION,"output":str(out)},indent=2))

if __name__=="__main__":
    main()
