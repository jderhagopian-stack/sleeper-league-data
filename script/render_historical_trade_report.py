#!/usr/bin/env python3
"""Render FSFFL Historical Trade Review 2.0.

The report deliberately separates:
1) ANALYSIS AT THE TIME - only information available at the transaction date,
2) DEAL IN HINDSIGHT - factual asset lineage and observed outcomes after the trade.

Internal model terminology remains in JSON for auditability; the PDF is written
as a fantasy-football decision product.
"""
from __future__ import annotations
import argparse, json
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether
)

MODEL_VERSION="FSFFL-GM-Historical-Trade-Report-2.0"
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

def fit_label(v):
    v=sf(v); a=abs(v)
    if a>=2500:return "Major positive" if v>0 else "Major negative"
    if a>=900:return "Meaningful positive" if v>0 else "Meaningful negative"
    if a>=250:return "Slight positive" if v>0 else "Slight negative"
    return "Near neutral"

def plain_reason(row):
    d=row.get("delta") or {}; st=row.get("strategic") or {}
    wins=sf(d.get("expected_wins")); playoff=sf(d.get("playoff_probability"))
    pkg=sf(st.get("package_effective_value_delta"),sf(st.get("intrinsic_dynasty_delta")))
    fit=sf(row.get("state_aware_utility_delta"))
    bits=[]
    if wins>=0.5:
        bits.append(f"adds about {wins:.1f} expected wins")
    elif wins<=-0.5:
        bits.append(f"costs about {abs(wins):.1f} expected wins")
    if playoff>=0.03:
        bits.append(f"raises playoff odds by {playoff*100:.1f} points")
    elif playoff<=-0.03:
        bits.append(f"lowers playoff odds by {abs(playoff)*100:.1f} points")
    if pkg>=1000:
        bits.append(f"wins the package-value exchange by about {pkg:,.0f}")
    elif pkg<=-1000:
        bits.append(f"pays about {abs(pkg):,.0f} more package value than it receives")
    if not bits:
        bits.append("lands near neutral across the model's main trade drivers")
    return "; ".join(bits)+f". Overall Team Fit: {fit:+,.0f} ({fit_label(fit).lower()})."

def at_time_card(uid,row,side,s):
    st=row.get("strategic") or {}; d=row.get("delta") or {}
    dec=(row.get("decision") or {}).get("band")
    rec=[x.get("name") or x.get("asset_id") for x in st.get("received") or []]
    sent=[x.get("name") or x.get("asset_id") for x in st.get("sent") or []]
    metrics=[
        [Paragraph("Expected wins",s["label"]),Paragraph(num(d.get("expected_wins"),2),s["metric"]),
         Paragraph("Playoff odds",s["label"]),Paragraph(pp(d.get("playoff_probability")),s["metric"])],
        [Paragraph("Championship odds",s["label"]),Paragraph(pp(d.get("championship_probability")),s["metric"]),
         Paragraph("Overall Team Fit",s["label"]),Paragraph(f"{sf(row.get('state_aware_utility_delta')):+,.0f}",s["metric"])],
        [Paragraph("Package value",s["label"]),Paragraph(val(st.get("package_effective_value_delta",st.get("intrinsic_dynasty_delta"))),s["metric"]),
         Paragraph("Value to this team",s["label"]),Paragraph(val(st.get("base_franchise_value_delta")),s["metric"])],
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
        [Paragraph("<b>Why:</b> "+clean(plain_reason(row),260),s["body"])],
    ]
    return Table(body,colWidths=[3.70*inch],style=TableStyle([
        ("BACKGROUND",(0,0),(-1,-1),LIGHT),("BOX",(0,0),(-1,-1),.55,MID),
        ("LEFTPADDING",(0,0),(-1,-1),7),("RIGHTPADDING",(0,0),(-1,-1),7),
        ("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),
    ]))

def event_sentence(ev):
    return clean(ev.get("description") or "",180)

def lineage_card(uid,side,s):
    h=(side.get("hindsight") or {})
    lin=h.get("asset_lineage") or {}
    roots=", ".join(x.get("label") or x.get("asset_key") for x in lin.get("root_assets") or [])
    terminals=", ".join(
        f"{x.get('label')} ({sf(x.get('current_intrinsic_value')):,.0f})"
        for x in lin.get("terminal_assets") or []
    ) or "No tracked descendant player/pick remains in the lineage."
    events=lin.get("events") or []
    bullets="<br/>".join(f"- {event_sentence(e)}" for e in events[:7]) or "- No downstream transformation was recorded."
    if len(events)>7:
        bullets+=f"<br/>- ...plus {len(events)-7} additional lineage events."
    ro=side.get("realized_outcome") or {}
    direct_pts=sf(ro.get("acquired_player_fsffl_points_after_trade"))
    mixed=int(lin.get("mixed_attribution_events") or 0)
    warning=(f"<br/><b>Attribution note:</b> {mixed} downstream trade(s) mixed lineage assets with unrelated assets, so the report tracks the full return but does not claim the original trade deserves 100% of that return."
             if mixed else "")
    return Table([
        [Paragraph(team_label(uid,{str(uid):side}),s["team"])],
        [Paragraph(f"<b>Original return:</b> {clean(roots,170)}<br/>"
                   f"<b>Direct acquired-player production:</b> {direct_pts:,.1f} FSFFL points<br/>"
                   f"<b>Tracked descendant value today:</b> {sf(lin.get('terminal_current_intrinsic_value')):,.0f}<br/>"
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
            outcome=f"{sf(x.get('observed_post_trade_points')):,.1f} observed FSFFL points after the trade"
        elif x.get("drafted_player"):
            outcome=f"slot became {clean(x.get('drafted_player'),45)} (pick {x.get('pick_no')})"
        else:
            outcome="no completed draft conversion yet"
        rows.append(f"<b>{clean(x.get('label'),55)}:</b> {outcome}")
    text="<br/>".join(rows) or "No keep-reference assets were available."
    return Paragraph(text+"<br/><font size='6.3'>"+clean(ref.get("note"),280)+"</font>",s["body"])

def build(report,out):
    s=styles()
    doc=SimpleDocTemplate(str(out),pagesize=letter,leftMargin=.40*inch,rightMargin=.40*inch,topMargin=.32*inch,bottomMargin=.30*inch)
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
        Paragraph(
            "This is a process grade, not a prediction of who will look smartest years later. GM 3.0 weighs the immediate lineup and playoff effect against intrinsic long-term value, team-specific value, roster fit and nonlinear package economics. A future result can be great even when the price was aggressive, or poor even when the original process was sound.",
            s["body"]),
        Spacer(1,.05*inch),
        Paragraph(f"{MODEL_VERSION} | {report.get('model_version')} | {gm3.get('n_sims',0):,} paired simulations | current-day values excluded from the at-the-time grade",s["small"]),
        PageBreak(),
        Paragraph("DEAL IN HINDSIGHT",s["title"]),
        Paragraph("What actually happened to the assets after the trade | This section never changes the original GM 3.0 grade",s["sub"]),
        Spacer(1,.07*inch),
        Table([[Paragraph("HINDSIGHT IS AN ASSET TREE, NOT JUST A POINT TOTAL",s["hero"])],
               [Paragraph("The report follows draft-pick conversions and later trades. When an acquired asset is packaged with unrelated assets, the downstream return is shown with mixed attribution instead of pretending the original trade alone created it.",s["hero2"])]],
              colWidths=[7.7*inch],style=TableStyle([
                  ("BACKGROUND",(0,0),(-1,-1),NAVY),("LEFTPADDING",(0,0),(-1,-1),11),
                  ("RIGHTPADDING",(0,0),(-1,-1),11),("TOPPADDING",(0,0),(-1,-1),7),
                  ("BOTTOMPADDING",(0,0),(-1,-1),6)])),
        Spacer(1,.08*inch)
    ]
    if len(uids)>=2:
        story.append(Table([[lineage_card(uids[0],sides.get(uids[0]) or {},s),
                             lineage_card(uids[1],sides.get(uids[1]) or {},s)]],
                           colWidths=[3.78*inch,3.78*inch],style=TableStyle([
                               ("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),0),
                               ("RIGHTPADDING",(0,0),(-1,-1),4)])))
    story += [Spacer(1,.08*inch),Paragraph("WHAT IF THEY HAD JUST KEPT WHAT THEY TRADED?",s["section"])]
    for uid in uids:
        story.append(KeepTogether([
            Paragraph(f"<b>{team_label(uid,sides)}</b>",s["body"]),
            keep_reference(sides.get(uid) or {},s),
            Spacer(1,.04*inch),
        ]))
    story += [
        Paragraph("HOW TO READ THE KEEP REFERENCE",s["section"]),
        Paragraph(
            "The keep reference reports what the surrendered players actually produced after the trade and what surrendered draft slots actually became. It is deliberately not labeled a full alternate-history simulation: if the original trade never happened, later trades, draft choices, waiver moves and lineup decisions could also have changed. The asset-lineage section is factual; the keep reference is a disciplined comparison point.",
            s["body"]),
        Spacer(1,.05*inch),
        Paragraph("PROCESS VS. OUTCOME",s["section"]),
        Paragraph(
            "The final retrospective judgment should preserve both answers: Was the decision sensible with the information available at the time? And what did the franchise ultimately turn those assets into? Those answers are allowed to disagree.",
            s["body"]),
        Spacer(1,.04*inch),
        Paragraph(f"{MODEL_VERSION} | asset-lineage hindsight | mixed attribution explicitly flagged | no hindsight leakage into the at-the-time grade",s["small"])
    ]
    doc.build(story)

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
