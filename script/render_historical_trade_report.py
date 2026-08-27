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
            "One side leads in production already realized while the other leads in remaining descendant value. "
            "The model does not force those different dimensions into one artificial score."
        )
    if cls=="NEAR_EVEN_HINDSIGHT":
        return "HINDSIGHT RESULT: NEAR EVEN", (
            "The two sides are within the model's materiality band on both realized production and remaining descendant value."
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

def at_time_bottom_line(uids,results,sides,winner):
    if not winner or winner=="TIE" or len(uids)<2:
        return (
            "At the time, GM 3.0 viewed the deal as essentially even once immediate competitive impact, "
            "team fit and long-term asset value were weighed together."
        )
    winner_uid=str(winner)
    loser_uid=next((str(u) for u in uids if str(u)!=winner_uid),None)
    if loser_uid is None:
        return f"At the time, GM 3.0 gave the overall edge to {team_label(winner_uid,sides)}."
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
    pieces=[f"At the time, GM 3.0 gave the overall edge to {winner_name}."]
    if wins>=0.5:
        pieces.append(
            f"{loser_name} still acquired a meaningful competitive upgrade, adding about {wins:.2f} expected wins "
            f"({win_context(wins)})"
            + (f" and {playoff*100:.1f} percentage points of playoff probability ({odds_context(playoff)})" if playoff>=0.03 else "")
            + "."
        )
    elif wins<=-0.5:
        pieces.append(f"{loser_name} gave up about {abs(wins):.2f} expected wins in the exchange.")
    if pkg<=-1000:
        pieces.append(
            f"The problem was the price: {loser_name} gave up about {abs(pkg):,.0f} more long-term value points than it received "
            f"({value_label(pkg).lower()} on the model's relative value scale). "
            "That scale is not fantasy points or dollars; it measures the size of the value gap between the two sides. "
            "GM 3.0 judged the competitive benefit insufficient to justify that premium."
        )
    elif pkg>=1000:
        pieces.append(
            f"{loser_name} also gained about {pkg:,.0f} long-term value points ({value_label(pkg).lower()} on the model's relative value scale), "
            "making the case more balanced than the headline verdict alone suggests."
        )
    if winner_wins<=-0.5 and winner_pkg>=1000:
        pieces.append(
            f"{winner_name} accepted a short-term hit of about {abs(winner_wins):.2f} expected wins ({win_context(winner_wins)}), "
            f"but was compensated with roughly {winner_pkg:,.0f} long-term value points "
            f"({value_label(winner_pkg).lower()} on the model's relative value scale)."
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
                   f"<b>Team situation:</b> {clean(str(row.get('team_state') or 'unknown').replace('_',' ').title(),35)}<br/>"
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

def lineage_card(uid,side,s):
    h=(side.get("hindsight") or {})
    lin=h.get("asset_lineage") or {}
    roots=", ".join(pick_only_label(x.get("label") or x.get("asset_key")) for x in lin.get("root_assets") or [])
    root_labels={str(x.get("asset_key")):str(x.get("label") or "") for x in lin.get("root_assets") or []}
    terminals=", ".join(
        f"{x.get('label')} ({sf(x.get('current_intrinsic_value')):,.0f} current value pts)"
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
    top_text=", ".join(
        f"{clean(x.get('player_name'),24)} {sf(x.get('fsffl_points_while_rostered')):,.1f}"
        for x in top[:4]
    ) or "No recorded lineage-player production."
    mixed=int(lin.get("mixed_attribution_events") or 0)
    warning=(f"<br/><b>Attribution note:</b> {mixed} downstream trade(s) mixed lineage assets with unrelated assets. The full return is shown, but the original trade is not credited with 100% of that package."
             if mixed else "")
    return Table([
        [Paragraph(team_label(uid,{str(uid):side}),s["team"])],
        [Paragraph(f"<b>Original return:</b> {clean(roots,175)}<br/>"
                   f"<b>Production actually captured:</b> {total_pts:,.1f} FSFFL points ({started_pts:,.1f} while started)<br/>"
                   f"<b>Top lineage contributors:</b> {clean(top_text,180)}<br/>"
                   f"<b>Long-term value still owned from the return:</b> {sf(lin.get('terminal_current_intrinsic_value')):,.0f} model pts<br/>"
                   f"<b>Where the assets ended up:</b> {clean(terminals,220)}",s["body"])],
        [Paragraph("<b>Asset trail</b><br/>"+bullets+warning,s["body"])],
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
            outcome=(f"{sf(x.get('observed_post_trade_points')):,.1f} observed FSFFL points after the trade; "
                     f"current long-term value {sf(x.get('current_intrinsic_value')):,.0f} model pts")
        elif x.get("drafted_player"):
            outcome=(f"slot became {clean(x.get('drafted_player'),38)}; "
                     f"{sf(x.get('observed_drafted_player_points')):,.1f} FSFFL points since drafted; "
                     f"current long-term value {sf(x.get('current_intrinsic_value')):,.0f} model pts")
        else:
            outcome="no completed draft conversion yet"
        rows.append(f"<b>{clean(pick_only_label(x.get('label')),75)}:</b> {outcome}")
    text="<br/>".join(rows) or "No keep-reference assets were available."
    summary=(f"<br/><b>Hold-reference totals:</b> {sf(ref.get('observed_reference_points')):,.1f} observed FSFFL points; "
             f"{sf(ref.get('current_reference_intrinsic_value')):,.0f} current long-term value points.")
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
        Paragraph(clean(at_time_bottom_line(uids,results,sides,winner),620),s["body"]),
        Spacer(1,.05*inch),
        PageBreak(),
        Paragraph("DEAL IN HINDSIGHT",s["title"]),
        Paragraph("What actually happened to the assets after the trade | This section never changes the original GM 3.0 grade",s["sub"]),
        Spacer(1,.07*inch),
        Table([[Paragraph(hindsight_verdict(report,sides)[0],s["hero"])],
               [Paragraph(hindsight_verdict(report,sides)[1],s["hero2"])],
               [Paragraph("The report follows draft-pick conversions, later trades, actual production captured from descendant players, and value still remaining in the asset tree. Mixed-package attribution is explicitly flagged.",s["hero2"])]],
              colWidths=[7.7*inch],style=TableStyle([
                  ("BACKGROUND",(0,0),(-1,-1),NAVY),("LEFTPADDING",(0,0),(-1,-1),11),
                  ("RIGHTPADDING",(0,0),(-1,-1),11),("TOPPADDING",(0,0),(-1,-1),7),
                  ("BOTTOMPADDING",(0,0),(-1,-1),6)])),
        Spacer(1,.08*inch),
        Paragraph("ASSET GENEALOGY & REALIZED PRODUCTION",s["section"])
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
            "The hold reference reports what the surrendered players actually produced after the trade and, for surrendered draft picks, the player actually selected at that slot. It is not a full alternate-history simulation: without the original trade, later trades, draft choices, waiver moves and lineup decisions could also have changed. Asset genealogy is factual; this hold reference is a disciplined comparison point.",
            s["body"]),
        Spacer(1,.05*inch),
        Paragraph("FINAL RETROSPECTIVE VERDICT",s["section"]),
        Paragraph(
            (
                f"<b>At the time:</b> GM 3.0 gave the overall edge to {winner_name}. "
                f"<b>In hindsight:</b> {clean(hindsight_verdict(report,sides)[0].replace('HINDSIGHT EDGE: ','').replace('HINDSIGHT RESULT: ',''),70)}. "
                + (
                    f"{team_label(next((u for u in uids if str(u)!=str(winner)),uids[0]),sides)} still received the immediate competitive improvement it paid for "
                    f"({sf((results.get(next((u for u in uids if str(u)!=str(winner)),uids[0])) or {}).get('delta',{}).get('expected_wins')):+.2f} expected wins), "
                    f"but {winner_name} won both the process grade and the realized asset-tree outcome."
                    if winner and winner!="TIE" and str((report.get("hindsight_assessment") or {}).get("winner_user_id"))==str(winner)
                    else "The process grade and realized outcome remain intentionally separate so later results never rewrite the original decision."
                )
            ),
            s["body"]),
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
