#!/usr/bin/env python3
"""Render the one-page FSFFL GM Action Report 1.0 from Team Improvement Lab JSON."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

MODEL_VERSION = "FSFFL-GM-Action-Report-1.1"
NAVY = colors.HexColor("#132238")
LIGHT = colors.HexColor("#F2F5F8")
MID = colors.HexColor("#D9E1E8")
INK = colors.HexColor("#18222C")
MUTED = colors.HexColor("#5D6A75")
WHITE = colors.white


def sf(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def pct(v): return f"{sf(v)*100:+.1f} pts"
def num(v, digits=2): return f"{sf(v):+.{digits}f}"
def money(v): return f"{sf(v):+,.0f}"


def clean(text, n=110):
    text = str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return text if len(text) <= n else text[:n-1].rstrip() + "..."


def styles():
    return {
        "title": ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=18, leading=20, textColor=NAVY, spaceAfter=2),
        "sub": ParagraphStyle("sub", fontName="Helvetica", fontSize=8.5, leading=11, textColor=MUTED),
        "section": ParagraphStyle("section", fontName="Helvetica-Bold", fontSize=9, leading=11, textColor=NAVY, spaceBefore=2, spaceAfter=4),
        "body": ParagraphStyle("body", fontName="Helvetica", fontSize=7.8, leading=10, textColor=INK),
        "small": ParagraphStyle("small", fontName="Helvetica", fontSize=6.8, leading=8.3, textColor=MUTED),
        "white": ParagraphStyle("white", fontName="Helvetica-Bold", fontSize=10.5, leading=13, textColor=WHITE),
        "white_small": ParagraphStyle("white_small", fontName="Helvetica", fontSize=7.5, leading=9.5, textColor=WHITE),
        "alt": ParagraphStyle("alt", fontName="Helvetica", fontSize=7.2, leading=8.7, textColor=INK),
    }


def metric_cards(rec, s):
    sim = rec.get("simulation") or {}; d = sim.get("focus_delta") or {}; st = sim.get("strategic") or {}
    cards = [("Expected wins", num(d.get("expected_wins"),2)), ("Playoff odds", pct(d.get("playoff_probability"))),
             ("Championship odds", pct(d.get("championship_probability"))), ("Expected points", num(d.get("expected_points_for"),1)),
             ("Long-term trade value", money(st.get("market_dynasty_delta"))), ("Value to this team", money(st.get("base_franchise_value_delta")))]
    data=[]
    for i in range(0,6,3):
        row=[]
        for label,value in cards[i:i+3]:
            row.append(Table([[Paragraph(label,s["small"])],[Paragraph(f"<b>{value}</b>",ParagraphStyle("metric",parent=s["body"],fontSize=12,leading=14,textColor=NAVY))]],
                             colWidths=[1.75*inch], rowHeights=[0.22*inch,0.35*inch], style=TableStyle([
                                 ("BACKGROUND",(0,0),(-1,-1),LIGHT),("BOX",(0,0),(-1,-1),0.5,MID),
                                 ("LEFTPADDING",(0,0),(-1,-1),8),("RIGHTPADDING",(0,0),(-1,-1),8),
                                 ("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),3)])))
        data.append(row)
    return Table(data,colWidths=[1.82*inch]*3,hAlign="LEFT",style=TableStyle([
        ("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(-1,-1),6),
        ("TOPPADDING",(0,0),(-1,-1),0),("BOTTOMPADDING",(0,0),(-1,-1),5)]))


def rationale(rec):
    channel=rec.get("channel") or "HOLD"; d=((rec.get("simulation") or {}).get("focus_delta") or {})
    if channel=="HOLD": return "The model did not find a move that was clearly better than simply keeping the roster as it is."
    gains=[]
    if abs(sf(d.get("championship_probability")))>=.01: gains.append(f"{pct(d.get('championship_probability'))} championship probability")
    if abs(sf(d.get("expected_wins")))>=.05: gains.append(f"{num(d.get('expected_wins'),2)} expected wins")
    if abs(sf(d.get("expected_points_for")))>=5: gains.append(f"{num(d.get('expected_points_for'),1)} expected points")
    if channel=="TRADE" and rec.get("acceptance_fit"): gains.append(f"{str(rec.get('acceptance_fit')).lower()} acceptance fit")
    return "Model preference is driven by "+", ".join(gains[:4])+"." if gains else "This move produced the best overall result for this team's current goals."


def alternative_table(report,s):
    rows=[[Paragraph("Rank",s["small"]),Paragraph("Alternative",s["small"]),Paragraph("Title",s["small"]),Paragraph("Wins",s["small"]),Paragraph("Overall Fit",s["small"])]]
    rec_desc=(report.get("recommended_action") or {}).get("description")
    alts=[x for x in (report.get("top_cross_channel_options") or []) if x.get("description")!=rec_desc][:4]
    if not alts: rows.append(["-",Paragraph("No superior alternative cleared the model threshold.",s["alt"]),"-","-","-"])
    else:
        for i,row in enumerate(alts,2):
            d=((row.get("simulation") or {}).get("focus_delta") or {})
            rows.append([str(i),Paragraph(clean(row.get("description"),92),s["alt"]),pct(d.get("championship_probability")),num(d.get("expected_wins"),2),f"{sf(row.get('team_improvement_score')):,.0f}"])
    t=Table(rows,colWidths=[0.45*inch,3.48*inch,0.78*inch,0.62*inch,0.72*inch],repeatRows=1)
    t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),LIGHT),("TEXTCOLOR",(0,0),(-1,0),NAVY),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
                           ("FONTSIZE",(0,1),(-1,-1),7),("GRID",(0,0),(-1,-1),0.35,MID),("VALIGN",(0,0),(-1,-1),"MIDDLE"),
                           ("LEFTPADDING",(0,0),(-1,-1),5),("RIGHTPADDING",(0,0),(-1,-1),5),("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4)]))
    return t


def build(report,output):
    s=styles(); doc=SimpleDocTemplate(str(output),pagesize=letter,rightMargin=.45*inch,leftMargin=.45*inch,topMargin=.38*inch,bottomMargin=.34*inch)
    story=[]; team=report.get("team_name") or "FSFFL Team"; state=str(report.get("team_state") or "").replace("_"," ").title(); rec=report.get("recommended_action") or {}; channel=rec.get("channel") or "HOLD"
    story += [Paragraph("FSFFL GM ACTION REPORT",s["title"]),Paragraph(f"{clean(team,70)} | {state} | What should this team do next?",s["sub"]),Spacer(1,.10*inch)]
    hero=Table([[Paragraph(f"MODEL ACTION: {channel}",s["white"])],[Paragraph(clean(rec.get("description") or "Hold current roster",150),s["white"])],[Paragraph(rationale(rec),s["white_small"])]],colWidths=[7.55*inch],style=TableStyle([
        ("BACKGROUND",(0,0),(-1,-1),NAVY),("LEFTPADDING",(0,0),(-1,-1),12),("RIGHTPADDING",(0,0),(-1,-1),12),("TOPPADDING",(0,0),(-1,-1),7),("BOTTOMPADDING",(0,0),(-1,-1),6)]))
    story += [hero,Spacer(1,.10*inch),metric_cards(rec,s),Paragraph("WHY THIS RANKS FIRST",s["section"])]
    sim=rec.get("simulation") or {}; rr=sim.get("roster_resolution") or {}; focus_uid=str(report.get("generated_for_user_id") or ""); focal_rr=rr.get(focus_uid) or {}; cuts=[x.get("name") for x in (focal_rr.get("selected_cuts") or []) if x.get("name")]
    score=sf(rec.get("team_improvement_score")); text=f"Overall team-fit score: <b>{score:,.0f}</b>. The move was compared with simply holding the roster. The model included any required cuts, rebuilt the best lineup, and simulated the season before ranking the options."
    if cuts: text += f" Required roster move for {clean(team,35)}: <b>{clean(', '.join(cuts),65)}</b>."
    if rec.get("channel")=="TRADE" and rec.get("acceptance_fit"): text += f" The offer's fit for the other manager is <b>{rec.get('acceptance_fit')}</b> This is a guide to how well the deal matches that manager, not a literal acceptance probability."
    story += [Paragraph(text,s["body"]),Spacer(1,.07*inch),Paragraph("NEXT-BEST OPTIONS",s["section"]),alternative_table(report,s),Spacer(1,.07*inch)]
    summary=report.get("search_summary") or {}; pu=report.get("projection_universe") or {}; cov=pu.get("coverage") or {}; waiver_count=len(report.get("best_waiver_options") or [])
    footer=(f"Search: {summary.get('trade_candidates_screened',0)} trades + {summary.get('waiver_candidates_screened',0)} waiver candidates; top {summary.get('deep_confirmed_candidates',0)} confirmed at {summary.get('deep_confirm_sims',0)} simulations. "
            f"Waivers use {pu.get('model_version') or 'the full projection universe'} with {cov.get('final_projection_players','?')} projected players. Actionable waiver recommendations: {waiver_count}. Keeping the roster unchanged is always included as an option.")
    story += [Paragraph(footer,s["small"]),Spacer(1,.05*inch),Paragraph(f"{MODEL_VERSION} | {report.get('model_version','Team Improvement Lab')} | plain-English model presentation",s["small"])]
    doc.build(story)


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--input",required=True); ap.add_argument("--output",required=True); a=ap.parse_args()
    report=json.loads(Path(a.input).read_text(encoding="utf-8")); out=Path(a.output); out.parent.mkdir(parents=True,exist_ok=True); build(report,out)
    print(json.dumps({"report_model_version":MODEL_VERSION,"output":str(out)},indent=2))

if __name__=="__main__": main()
