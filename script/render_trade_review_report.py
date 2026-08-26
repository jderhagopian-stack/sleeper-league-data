#!/usr/bin/env python3
"""Render one-page FSFFL GM Trade Review Report 1.0."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

MODEL_VERSION = "FSFFL-GM-Trade-Review-Report-1.0"
NAVY = colors.HexColor("#132238")
LIGHT = colors.HexColor("#F2F5F8")
MID = colors.HexColor("#D9E1E8")
INK = colors.HexColor("#18222C")
MUTED = colors.HexColor("#5D6A75")
WHITE = colors.white


def sf(v, d=0.0):
    try: return float(v)
    except (TypeError, ValueError): return d


def pct(v): return f"{sf(v)*100:+.1f} pts"
def num(v, d=2): return f"{sf(v):+.{d}f}"
def val(v): return f"{sf(v):+,.0f}"

def clean(x, n=100):
    s=str(x or "").replace("\u2013","-").replace("\u2014","-").replace("\u2019","'")
    s=s.encode("ascii","ignore").decode("ascii")
    s=s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
    return s if len(s)<=n else s[:n-1].rstrip()+"..."


def styles():
    return {
      "title": ParagraphStyle("title",fontName="Helvetica-Bold",fontSize=17,leading=19,textColor=NAVY),
      "sub": ParagraphStyle("sub",fontName="Helvetica",fontSize=8.2,leading=10,textColor=MUTED),
      "section": ParagraphStyle("section",fontName="Helvetica-Bold",fontSize=8.8,leading=10.5,textColor=NAVY,spaceBefore=2,spaceAfter=3),
      "body": ParagraphStyle("body",fontName="Helvetica",fontSize=7.5,leading=9.3,textColor=INK),
      "small": ParagraphStyle("small",fontName="Helvetica",fontSize=6.5,leading=8,textColor=MUTED),
      "hero": ParagraphStyle("hero",fontName="Helvetica-Bold",fontSize=10,leading=12,textColor=WHITE),
      "hero2": ParagraphStyle("hero2",fontName="Helvetica",fontSize=7.3,leading=9.2,textColor=WHITE),
      "team": ParagraphStyle("team",fontName="Helvetica-Bold",fontSize=9,leading=11,textColor=NAVY),
    }


def assets_for(report, uid):
    sent=[]; rec=[]
    reviews=report.get("team_reviews") or {}
    st=(reviews.get(str(uid)) or {}).get("strategic") or {}
    for x in st.get("sent") or []: sent.append(x.get("name") or x.get("asset_id"))
    for x in st.get("received") or []: rec.append(x.get("name") or x.get("asset_id"))
    return sent,rec


def side_card(uid,row,report,s):
    d=row.get("focus_delta") or {}; st=row.get("strategic") or {}; rr=row.get("roster_resolution") or {}
    sent,rec=assets_for(report,uid)
    cuts=[x.get("name") for x in (rr.get("selected_cuts") or []) if x.get("name")]
    asset_line=f"<b>Gets:</b> {clean(', '.join(rec) or 'None',115)}<br/><b>Sends:</b> {clean(', '.join(sent) or 'None',115)}"
    metrics=[
      ["Wins",num(d.get("expected_wins"),2),"Title",pct(d.get("championship_probability"))],
      ["Playoffs",pct(d.get("playoff_probability")),"Dynasty",val(st.get("market_dynasty_delta"))],
      ["PF",num(d.get("expected_points_for"),1),"Franchise",val(st.get("base_franchise_value_delta"))],
      ["State utility",f"{sf(row.get('state_aware_utility_delta')):+,.0f}","Roster cost", clean(', '.join(cuts) if cuts else 'None',45)],
    ]
    t=Table([
      [Paragraph(clean(row.get("team_name") or row.get("manager") or uid,55),s["team"])],
      [Paragraph(f"{clean(str(row.get('team_state') or 'unknown').replace('_',' ').title(),30)} | {asset_line}",s["body"])],
      [Table(metrics,colWidths=[0.62*inch,0.72*inch,0.68*inch,0.92*inch],style=TableStyle([
        ("FONTNAME",(0,0),(-1,-1),"Helvetica"),("FONTSIZE",(0,0),(-1,-1),6.8),("TEXTCOLOR",(0,0),(-1,-1),INK),
        ("FONTNAME",(0,0),(0,-1),"Helvetica-Bold"),("FONTNAME",(2,0),(2,-1),"Helvetica-Bold"),
        ("TOPPADDING",(0,0),(-1,-1),2),("BOTTOMPADDING",(0,0),(-1,-1),2),
      ]))],
    ],colWidths=[3.55*inch],style=TableStyle([
      ("BACKGROUND",(0,0),(-1,-1),LIGHT),("BOX",(0,0),(-1,-1),0.5,MID),
      ("LEFTPADDING",(0,0),(-1,-1),8),("RIGHTPADDING",(0,0),(-1,-1),8),
      ("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),
    ]))
    return t


def build(report,out):
    s=styles(); doc=SimpleDocTemplate(str(out),pagesize=letter,leftMargin=.42*inch,rightMargin=.42*inch,topMargin=.34*inch,bottomMargin=.30*inch)
    story=[]; reviews=report.get("team_reviews") or {}; uids=list(report.get("participant_user_ids") or reviews.keys()); ass=report.get("bilateral_assessment") or {}
    story += [Paragraph("FSFFL GM TRADE REVIEW",s["title"]),Paragraph("Completed-transaction retrospective | Same GM 3.0 intelligence, bilateral lens",s["sub"]),Spacer(1,.08*inch)]
    verdict=clean(ass.get("classification","TRADE REVIEW").replace("_"," "),80)
    hero=Table([[Paragraph(verdict,s["hero"])],[Paragraph(clean(ass.get("summary"),180),s["hero2"])],[Paragraph(f"Dynasty value: <b>{clean(ass.get('pure_dynasty_value_winner'),45)}</b> &nbsp;&nbsp; Title equity: <b>{clean(ass.get('current_title_equity_winner'),45)}</b> &nbsp;&nbsp; State-aware: <b>{clean(ass.get('state_aware_utility_winner'),45)}</b>",s["hero2"])]],colWidths=[7.62*inch],style=TableStyle([
      ("BACKGROUND",(0,0),(-1,-1),NAVY),("LEFTPADDING",(0,0),(-1,-1),11),("RIGHTPADDING",(0,0),(-1,-1),11),("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),5)
    ]))
    story += [hero,Spacer(1,.09*inch)]
    if len(uids)>=2:
      story.append(Table([[side_card(uids[0],reviews[uids[0]],report,s),side_card(uids[1],reviews[uids[1]],report,s)]],colWidths=[3.72*inch,3.72*inch],style=TableStyle([("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(-1,-1),5)])))
    story += [Spacer(1,.08*inch),Paragraph("MODEL READ",s["section"])]
    if len(uids)>=2:
      a,b=reviews[uids[0]],reviews[uids[1]]
      text=(f"{clean(a.get('team_name'),45)} changes title odds by <b>{pct((a.get('focus_delta') or {}).get('championship_probability'))}</b> and dynasty value by <b>{val((a.get('strategic') or {}).get('market_dynasty_delta'))}</b>; "
            f"{clean(b.get('team_name'),45)} changes title odds by <b>{pct((b.get('focus_delta') or {}).get('championship_probability'))}</b> and dynasty value by <b>{val((b.get('strategic') or {}).get('market_dynasty_delta'))}</b>. "
            "The state-aware score applies each franchise's own contender/retool/rebuild weighting rather than forcing both sides into the same objective.")
      story.append(Paragraph(text,s["body"]))
    story += [Spacer(1,.06*inch),Paragraph("ROSTER & METHOD",s["section"])]
    sim=report.get("simulation") or {}; cuts=report.get("automatic_roster_cut_actions") or []
    method=(f"The completed trade is applied ephemerally to the pre-trade canonical snapshot. Both touched teams are roster-legalized, weekly lineups are re-optimized, and the same random seed is used for paired before/after simulation. "
            f"Run depth: <b>{sim.get('n_sims',0):,} simulations</b>. Automatic roster-cut actions: <b>{len(cuts)}</b>. Canonical league state is not mutated.")
    story.append(Paragraph(method,s["body"]))
    story += [Spacer(1,.06*inch),Paragraph("HOW TO READ THE WINNER LABELS",s["section"]),Paragraph("Dynasty winner = largest modeled long-term market-value gain. Title-equity winner = largest change in championship probability. State-aware winner = largest improvement under that team's own GM 3.0 competitive-window objective. A trade can rationally have different winners by lens.",s["small"]),Spacer(1,.05*inch)]
    story.append(Paragraph(f"{MODEL_VERSION} | {report.get('model_version')} | roster resolver {sim.get('roster_resolution_model_version')} | model-generated presentation",s["small"]))
    doc.build(story)


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--input",required=True); ap.add_argument("--output",required=True); a=ap.parse_args()
    r=json.loads(Path(a.input).read_text(encoding="utf-8")); out=Path(a.output); out.parent.mkdir(parents=True,exist_ok=True); build(r,out)
    print(json.dumps({"report_model_version":MODEL_VERSION,"output":str(out)},indent=2))

if __name__=="__main__": main()
