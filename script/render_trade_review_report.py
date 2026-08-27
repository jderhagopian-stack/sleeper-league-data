#!/usr/bin/env python3
"""Render one-page FSFFL GM Trade Review Report 1.2.

User-facing language is plain English. Raw model fields remain unchanged.
Value deltas include percentage context versus the effective assets surrendered,
and roster impact explicitly states any model-required cut and its consequences.
When Trade Review 1.1 supplies simulated cut-selection analysis, the report
shows the shortlisted cuts and explains that the chosen legal roster won the
conditional simulation rather than merely the retention-cost prescreen.
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


def pct(v): return f"{sf(v)*100:+.1f} pts"
def num(v, d=2): return f"{sf(v):+.{d}f}"
def val(v): return f"{sf(v):+,.0f}"
def pct_change(delta, base):
    return None if abs(sf(base)) < 1e-9 else 100.0 * sf(delta) / sf(base)
def pct_label(v): return "n/a" if v is None else f"{v:+.1f}%"


def clean(x, n=140):
    s=str(x or "").replace("\u2013","-").replace("\u2014","-").replace("\u2019","'")
    s=s.encode("ascii","ignore").decode("ascii")
    s=s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
    return s if len(s)<=n else s[:n-1].rstrip()+"..."


def styles():
    return {
      "title": ParagraphStyle("title",fontName="Helvetica-Bold",fontSize=17,leading=19,textColor=NAVY),
      "sub": ParagraphStyle("sub",fontName="Helvetica",fontSize=8.2,leading=10,textColor=MUTED),
      "section": ParagraphStyle("section",fontName="Helvetica-Bold",fontSize=8.6,leading=10.1,textColor=NAVY,spaceBefore=2,spaceAfter=3),
      "body": ParagraphStyle("body",fontName="Helvetica",fontSize=7.15,leading=8.8,textColor=INK),
      "small": ParagraphStyle("small",fontName="Helvetica",fontSize=6.15,leading=7.5,textColor=MUTED),
      "hero": ParagraphStyle("hero",fontName="Helvetica-Bold",fontSize=10.7,leading=12.5,textColor=WHITE),
      "hero2": ParagraphStyle("hero2",fontName="Helvetica",fontSize=7.3,leading=9.1,textColor=WHITE),
      "team": ParagraphStyle("team",fontName="Helvetica-Bold",fontSize=9,leading=11,textColor=NAVY),
    }


def effective_cost_bases(row):
    st=row.get("strategic") or {}; sent=st.get("sent") or []
    dynasty=sum(sf(x.get("market_dynasty"), sf(x.get("dynasty_value"))) for x in sent)
    franchise=sum(sf(x.get("base_franchise_value"), sf(x.get("strategic_value"))) for x in sent)
    return dynasty, franchise


def fit_label(v):
    v=sf(v); a=abs(v)
    if a >= 750: return "Big improvement" if v>0 else "Big setback"
    if a >= 250: return "Meaningful improvement" if v>0 else "Meaningful setback"
    if a >= 75: return "Modest improvement" if v>0 else "Modest setback"
    return "Near neutral"


def side_card(uid,row,s):
    d=row.get("focus_delta") or {}; st=row.get("strategic") or {}; rr=row.get("roster_resolution") or {}
    sent=[x.get("name") or x.get("asset_id") for x in st.get("sent") or []]
    rec=[x.get("name") or x.get("asset_id") for x in st.get("received") or []]
    dyn_base, team_base=effective_cost_bases(row)
    dyn_pct=pct_change(st.get("market_dynasty_delta"),dyn_base); team_pct=pct_change(st.get("base_franchise_value_delta"),team_base)
    cuts=[x.get("name") for x in rr.get("selected_cuts") or [] if x.get("name")]
    roster="CUT " + ", ".join(cuts) if cuts else "No additional cut"
    metrics=[
      ["Expected wins",num(d.get("expected_wins"),2),"Championship odds",pct(d.get("championship_probability"))],
      ["Playoff odds",pct(d.get("playoff_probability")),"Expected points",num(d.get("expected_points_for"),1)],
      ["Long-term trade value",f"{val(st.get('market_dynasty_delta'))} ({pct_label(dyn_pct)})","Value to this team",f"{val(st.get('base_franchise_value_delta'))} ({pct_label(team_pct)})"],
      ["Overall Team Fit",f"{sf(row.get('state_aware_utility_delta')):+,.0f} - {fit_label(row.get('state_aware_utility_delta'))}","Roster impact",clean(roster,50)],
    ]
    return Table([
      [Paragraph(clean(row.get("team_name") or row.get("manager") or uid,55),s["team"])],
      [Paragraph(f"<b>Team situation:</b> {clean(str(row.get('team_state') or 'unknown').replace('_',' ').title(),30)}<br/><b>Receives:</b> {clean(', '.join(rec) or 'None',120)}<br/><b>Gives up:</b> {clean(', '.join(sent) or 'None',120)}",s["body"])],
      [Table(metrics,colWidths=[0.91*inch,0.94*inch,0.92*inch,0.97*inch],style=TableStyle([
        ("FONTNAME",(0,0),(-1,-1),"Helvetica"),("FONTSIZE",(0,0),(-1,-1),6.15),("TEXTCOLOR",(0,0),(-1,-1),INK),
        ("FONTNAME",(0,0),(0,-1),"Helvetica-Bold"),("FONTNAME",(2,0),(2,-1),"Helvetica-Bold"),
        ("TOPPADDING",(0,0),(-1,-1),2),("BOTTOMPADDING",(0,0),(-1,-1),2),("VALIGN",(0,0),(-1,-1),"MIDDLE"),
      ]))],
    ],colWidths=[3.70*inch],style=TableStyle([
      ("BACKGROUND",(0,0),(-1,-1),LIGHT),("BOX",(0,0),(-1,-1),0.5,MID),
      ("LEFTPADDING",(0,0),(-1,-1),7),("RIGHTPADDING",(0,0),(-1,-1),7),
      ("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),
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
    tested = f" The model prescreened and simulated <b>{', '.join(candidates)}</b>; cutting <b>{names}</b> produced the strongest legal post-trade roster." if candidates else ""
    confirm = " A close top-two result was rechecked at the full simulation depth." if selection.get("confirmation_triggered") else ""
    return Paragraph(
      f"<b>{name}:</b> must free one active-roster spot, so the model selects <b>CUT {names}</b>. "
      f"That player is removed from the active roster and would become available through the league's normal waiver/free-agent process. "
      f"The cut costs about <b>{dyn:,.0f} long-term trade value</b> and <b>{franchise:,.0f} value to this team</b>, already included in the trade result."
      f"{tested}{confirm} This is a modeled follow-up move, not evidence the manager has already made the cut in Sleeper.",s["body"])


def build(report,out):
    s=styles(); doc=SimpleDocTemplate(str(out),pagesize=letter,leftMargin=.40*inch,rightMargin=.40*inch,topMargin=.32*inch,bottomMargin=.28*inch)
    reviews=report.get("team_reviews") or {}; uids=list(report.get("participant_user_ids") or reviews.keys()); ass=report.get("bilateral_assessment") or {}; sim=report.get("simulation") or {}
    winner=clean(ass.get("state_aware_utility_winner") or "TIE",55)
    story=[Paragraph("FSFFL GM TRADE REVIEW",s["title"]),Paragraph("Completed-trade review | Plain-English model presentation",s["sub"]),Spacer(1,.07*inch)]
    summary=clean(ass.get("summary"),200)
    hero=Table([[Paragraph(f"OVERALL WINNER: {winner.upper()}",s["hero"])],[Paragraph(summary,s["hero2"])],[Paragraph(f"Long-term value winner: <b>{clean(ass.get('pure_dynasty_value_winner'),45)}</b> &nbsp;&nbsp; Championship-odds winner: <b>{clean(ass.get('current_title_equity_winner'),45)}</b> &nbsp;&nbsp; Overall winner: <b>{winner}</b>",s["hero2"])]],colWidths=[7.7*inch],style=TableStyle([
      ("BACKGROUND",(0,0),(-1,-1),NAVY),("LEFTPADDING",(0,0),(-1,-1),11),("RIGHTPADDING",(0,0),(-1,-1),11),("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),5)]))
    story += [hero,Spacer(1,.08*inch)]
    if len(uids)>=2:
        story.append(Table([[side_card(uids[0],reviews[uids[0]],s),side_card(uids[1],reviews[uids[1]],s)]],colWidths=[3.78*inch,3.78*inch],style=TableStyle([("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(-1,-1),4)])))
    story += [Spacer(1,.05*inch),Paragraph("VALUE CONTEXT",s["section"]),Paragraph("The percentage beside <b>Long-Term Trade Value</b> and <b>Value to This Team</b> shows how large the gain or loss is compared with what that team gave up. Any required cuts are already included. <b>Overall Team Fit</b> is the model's bottom-line view of how well the trade serves that roster, so it is shown as a score change plus a simple size label rather than a misleading percentage.",s["body"]),Spacer(1,.04*inch),Paragraph("ROSTER IMPACT - WHAT ACTUALLY HAPPENS",s["section"])]
    for uid in uids: story.append(roster_paragraph(uid,reviews[uid],report,s))
    story += [Spacer(1,.04*inch),Paragraph("HOW A FORCED CUT IS CHOSEN",s["section"]),Paragraph("Only trades that create an incremental roster spot trigger extra work. Newly acquired players are protected. If the trade creates an extra roster spot, the model first protects the newly acquired players and important starters. It then identifies the most expendable current players and tests the three most reasonable cut choices. The cut that leaves the strongest overall roster is used in the final trade result. If two choices are very close, the model runs a deeper check before deciding.",s["body"]),Spacer(1,.035*inch),Paragraph(f"{MODEL_VERSION} | {report.get('model_version')} | {sim.get('n_sims',0):,} paired simulations | roster resolver {sim.get('roster_resolution_model_version')} | plain-English model presentation",s["small"])]
    doc.build(story)


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--input",required=True); ap.add_argument("--output",required=True); a=ap.parse_args()
    r=json.loads(Path(a.input).read_text(encoding="utf-8")); out=Path(a.output); out.parent.mkdir(parents=True,exist_ok=True); build(r,out)
    print(json.dumps({"report_model_version":MODEL_VERSION,"output":str(out)},indent=2))

if __name__=="__main__": main()
