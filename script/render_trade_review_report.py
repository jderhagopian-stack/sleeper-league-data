#!/usr/bin/env python3
"""Render one-page FSFFL GM Trade Review Report 1.3.

Plain-English model presentation with before/after context. Probability totals
are shown as percentages; changes are shown as percentage-point deltas. Current-
season totals show the post-trade total plus the modeled change. Strategic value
metrics retain percentage context, and roster impact includes simulated cut
selection when available.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

MODEL_VERSION = "FSFFL-GM-Trade-Review-Report-1.3"
NAVY = colors.HexColor("#132238")
LIGHT = colors.HexColor("#F2F5F8")
MID = colors.HexColor("#D9E1E8")
INK = colors.HexColor("#18222C")
MUTED = colors.HexColor("#5D6A75")
WHITE = colors.white


def sf(v, d=0.0):
    try: return float(v)
    except (TypeError, ValueError): return d


def delta_num(v, d=2): return f"{sf(v):+.{d}f}"
def val(v): return f"{sf(v):+,.0f}"
def pct_change(delta, base):
    return None if abs(sf(base)) < 1e-9 else 100.0 * sf(delta) / sf(base)
def pct_label(v): return "n/a" if v is None else f"{v:+.1f}%"
def prob_total(v): return f"{sf(v)*100:.1f}%"
def prob_delta(v): return f"{sf(v)*100:+.1f} pp"
def total_delta(total, delta, digits=2): return f"{sf(total):.{digits}f} ({sf(delta):+.{digits}f})"
def points_total(total, delta): return f"{sf(total):,.1f} ({sf(delta):+,.1f})"


def clean(x, n=140):
    s=str(x or "").replace("\u2013","-").replace("\u2014","-").replace("\u2019","'")
    s=s.encode("ascii","ignore").decode("ascii")
    s=s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
    return s if len(s)<=n else s[:n-1].rstrip()+"..."


def styles():
    return {
      "title": ParagraphStyle("title",fontName="Helvetica-Bold",fontSize=17,leading=19,textColor=NAVY),
      "sub": ParagraphStyle("sub",fontName="Helvetica",fontSize=8.1,leading=9.8,textColor=MUTED),
      "section": ParagraphStyle("section",fontName="Helvetica-Bold",fontSize=8.45,leading=9.9,textColor=NAVY,spaceBefore=2,spaceAfter=2),
      "body": ParagraphStyle("body",fontName="Helvetica",fontSize=6.95,leading=8.45,textColor=INK),
      "small": ParagraphStyle("small",fontName="Helvetica",fontSize=5.95,leading=7.15,textColor=MUTED),
      "hero": ParagraphStyle("hero",fontName="Helvetica-Bold",fontSize=10.5,leading=12.2,textColor=WHITE),
      "hero2": ParagraphStyle("hero2",fontName="Helvetica",fontSize=7.15,leading=8.8,textColor=WHITE),
      "team": ParagraphStyle("team",fontName="Helvetica-Bold",fontSize=8.8,leading=10.4,textColor=NAVY),
      "metric_label": ParagraphStyle("metric_label",fontName="Helvetica-Bold",fontSize=5.8,leading=6.7,textColor=MUTED),
      "metric_value": ParagraphStyle("metric_value",fontName="Helvetica-Bold",fontSize=7.0,leading=8.2,textColor=INK),
    }


def effective_cost_bases(row):
    st=row.get("strategic") or {}; sent=st.get("sent") or []
    dynasty=sum(sf(x.get("market_dynasty"), sf(x.get("dynasty_value"))) for x in sent)
    franchise=sum(sf(x.get("base_franchise_value"), sf(x.get("strategic_value"))) for x in sent)
    return dynasty, franchise


def fit_label(v):
    v=sf(v); a=abs(v)
    if a >= 750: return "Major improvement" if v>0 else "Major setback"
    if a >= 250: return "Meaningful improvement" if v>0 else "Meaningful setback"
    if a >= 75: return "Modest improvement" if v>0 else "Modest setback"
    return "Near neutral"


def metric_box(label, value, s):
    return Table([
        [Paragraph(clean(label,35),s["metric_label"])],
        [Paragraph(clean(value,55),s["metric_value"])],
    ], colWidths=[1.72*inch], style=TableStyle([
        ("BACKGROUND",(0,0),(-1,-1),colors.white),
        ("BOX",(0,0),(-1,-1),0.35,MID),
        ("LEFTPADDING",(0,0),(-1,-1),5),("RIGHTPADDING",(0,0),(-1,-1),5),
        ("TOPPADDING",(0,0),(-1,-1),3),("BOTTOMPADDING",(0,0),(-1,-1),3),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
    ]))


def side_card(uid,row,s):
    d=row.get("focus_delta") or {}; before=row.get("focus_before") or {}; after=row.get("focus_after") or {}
    st=row.get("strategic") or {}; rr=row.get("roster_resolution") or {}
    sent=[x.get("name") or x.get("asset_id") for x in st.get("sent") or []]
    rec=[x.get("name") or x.get("asset_id") for x in st.get("received") or []]
    dyn_base, team_base=effective_cost_bases(row)
    dyn_pct=pct_change(st.get("market_dynasty_delta"),dyn_base); team_pct=pct_change(st.get("base_franchise_value_delta"),team_base)
    cuts=[x.get("name") for x in rr.get("selected_cuts") or [] if x.get("name")]
    roster="CUT " + ", ".join(cuts) if cuts else "No additional cut"

    metrics = [
      ("Expected wins", total_delta(after.get("expected_wins"), d.get("expected_wins"),2)),
      ("Expected points", points_total(after.get("expected_points_for"), d.get("expected_points_for"))),
      ("Playoff odds", f"{prob_total(after.get('playoff_probability'))} ({prob_delta(d.get('playoff_probability'))})"),
      ("Championship odds", f"{prob_total(after.get('championship_probability'))} ({prob_delta(d.get('championship_probability'))})"),
      ("Long-term trade value", f"{val(st.get('market_dynasty_delta'))} ({pct_label(dyn_pct)})"),
      ("Value to this team", f"{val(st.get('base_franchise_value_delta'))} ({pct_label(team_pct)})"),
      ("Overall team fit", f"{sf(row.get('state_aware_utility_delta')):+,.0f} - {fit_label(row.get('state_aware_utility_delta'))}"),
      ("Roster impact", roster),
    ]
    grid=[]
    for i in range(0,len(metrics),2):
        grid.append([metric_box(*metrics[i],s), metric_box(*metrics[i+1],s)])
    metric_grid=Table(grid,colWidths=[1.79*inch,1.79*inch],style=TableStyle([
        ("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(-1,-1),4),
        ("TOPPADDING",(0,0),(-1,-1),1),("BOTTOMPADDING",(0,0),(-1,-1),2),
    ]))
    return Table([
      [Paragraph(clean(row.get("team_name") or row.get("manager") or uid,55),s["team"])],
      [Paragraph(f"<b>Team situation:</b> {clean(str(row.get('team_state') or 'unknown').replace('_',' ').title(),30)}<br/><b>Receives:</b> {clean(', '.join(rec) or 'None',115)}<br/><b>Gives up:</b> {clean(', '.join(sent) or 'None',115)}",s["body"])],
      [metric_grid],
    ],colWidths=[3.66*inch],style=TableStyle([
      ("BACKGROUND",(0,0),(-1,-1),LIGHT),("BOX",(0,0),(-1,-1),0.5,MID),
      ("LEFTPADDING",(0,0),(-1,-1),6),("RIGHTPADDING",(0,0),(-1,-1),6),
      ("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),
    ]))


def roster_paragraph(uid,row,report,s):
    rr=row.get("roster_resolution") or {}; cuts=rr.get("selected_cuts") or []
    name=clean(row.get("team_name") or row.get("manager"),45)
    if not cuts:
        return Paragraph(f"<b>{name}:</b> no additional active-roster cut is required by this trade.",s["body"])
    names=', '.join(clean(x.get('name'),35) for x in cuts)
    dyn=sum(sf(x.get("market_dynasty")) for x in cuts); franchise=sum(sf(x.get("base_franchise_value")) for x in cuts)
    selection=(report.get("cut_selection_analysis") or {}).get(str(uid)) or {}
    candidates=[clean(x.get("name"),30) for x in selection.get("candidates") or [] if x.get("name")]
    tested = f" The model tested <b>{', '.join(candidates)}</b>; cutting <b>{names}</b> produced the strongest legal post-trade roster." if candidates else ""
    confirm = " A close top-two result was rechecked at full simulation depth." if selection.get("confirmation_triggered") else ""
    return Paragraph(
      f"<b>{name}:</b> must free one active-roster spot, so the modeled follow-up is <b>CUT {names}</b>. "
      f"The cut costs about <b>{dyn:,.0f} long-term trade value</b> and <b>{franchise:,.0f} value to this team</b>, already included in the result."
      f"{tested}{confirm} The player would then enter the league's normal waiver/free-agent process; this is not evidence the manager has already made the cut in Sleeper.",s["body"])


def build(report,out):
    s=styles(); doc=SimpleDocTemplate(str(out),pagesize=letter,leftMargin=.38*inch,rightMargin=.38*inch,topMargin=.28*inch,bottomMargin=.25*inch)
    reviews=report.get("team_reviews") or {}; uids=list(report.get("participant_user_ids") or reviews.keys()); ass=report.get("bilateral_assessment") or {}; sim=report.get("simulation") or {}
    winner=clean(ass.get("state_aware_utility_winner") or "TIE",55)
    story=[Paragraph("FSFFL GM TRADE REVIEW",s["title"]),Paragraph("Completed-trade review | Post-trade total shown first; change from pre-trade baseline in parentheses",s["sub"]),Spacer(1,.055*inch)]
    summary=clean(ass.get("summary"),190)
    hero=Table([[Paragraph(f"OVERALL WINNER: {winner.upper()}",s["hero"])],[Paragraph(summary,s["hero2"])],[Paragraph(f"Long-term value winner: <b>{clean(ass.get('pure_dynasty_value_winner'),40)}</b> &nbsp;&nbsp; Championship-odds winner: <b>{clean(ass.get('current_title_equity_winner'),40)}</b> &nbsp;&nbsp; Overall winner: <b>{winner}</b>",s["hero2"])]],colWidths=[7.74*inch],style=TableStyle([
      ("BACKGROUND",(0,0),(-1,-1),NAVY),("LEFTPADDING",(0,0),(-1,-1),10),("RIGHTPADDING",(0,0),(-1,-1),10),("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),4)]))
    story += [hero,Spacer(1,.06*inch)]
    if len(uids)>=2:
        story.append(Table([[side_card(uids[0],reviews[uids[0]],s),side_card(uids[1],reviews[uids[1]],s)]],colWidths=[3.78*inch,3.78*inch],style=TableStyle([("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(-1,-1),4)])))
    story += [Spacer(1,.035*inch),Paragraph("HOW TO READ THE METRICS",s["section"]),Paragraph("For <b>Expected Wins, Expected Points, Playoff Odds</b> and <b>Championship Odds</b>, the first number is the modeled <b>post-trade total</b>; the number in parentheses is the change from the pre-trade baseline. Odds totals are percentages, while their changes are correctly expressed in <b>percentage points (pp)</b>. Long-Term Trade Value and Value to This Team show the model-value change plus the percentage change relative to effective assets surrendered.",s["body"]),Spacer(1,.025*inch),Paragraph("ROSTER IMPACT",s["section"])]
    for uid in uids: story.append(roster_paragraph(uid,reviews[uid],report,s))
    story += [Spacer(1,.025*inch),Paragraph("FORCED-CUT METHOD",s["section"]),Paragraph("Only trades creating an incremental roster spot trigger extra work. Newly acquired players are protected. The model prescreens the three lowest-retention-cost incumbents, simulates each legal post-trade roster and selects the cut producing the best Overall Team Fit; close top-two results are confirmed at full simulation depth.",s["body"]),Spacer(1,.02*inch),Paragraph(f"{MODEL_VERSION} | {report.get('model_version')} | {sim.get('n_sims',0):,} paired simulations | roster resolver {sim.get('roster_resolution_model_version')} | model-generated presentation",s["small"])]
    doc.build(story)


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--input",required=True); ap.add_argument("--output",required=True); a=ap.parse_args()
    r=json.loads(Path(a.input).read_text(encoding="utf-8")); out=Path(a.output); out.parent.mkdir(parents=True,exist_ok=True); build(r,out)
    print(json.dumps({"report_model_version":MODEL_VERSION,"output":str(out)},indent=2))

if __name__=="__main__": main()
