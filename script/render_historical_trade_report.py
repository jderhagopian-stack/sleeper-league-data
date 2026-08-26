#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.pdfbase.pdfmetrics import stringWidth

MODEL_VERSION = "FSFFL-GM-Historical-Trade-Report-1.0"


def safe(s):
    return str(s or "").replace("—", "-").replace("–", "-").replace("🍎", "")


def wrap(text, font, size, maxw):
    words=safe(text).split(); lines=[]; cur=""
    for w in words:
        t=(cur+" "+w).strip()
        if stringWidth(t,font,size)<=maxw: cur=t
        else:
            if cur: lines.append(cur)
            cur=w
    if cur: lines.append(cur)
    return lines


def draw_lines(c,text,x,y,w,font="Helvetica",size=8,leading=9.5,max_lines=None):
    lines=wrap(text,font,size,w)
    if max_lines and len(lines)>max_lines:
        lines=lines[:max_lines]
        last=lines[-1]
        while stringWidth(last+"...",font,size)>w and last: last=last[:-1]
        lines[-1]=last.rstrip()+"..."
    for line in lines:
        c.setFont(font,size); c.drawString(x,y,line); y-=leading
    return y


def render(data,out):
    W,H=letter; c=canvas.Canvas(str(out),pagesize=letter)
    ink=colors.HexColor("#172536"); navy=colors.HexColor("#14263D"); muted=colors.HexColor("#667485"); panel=colors.HexColor("#F2F5F8"); line=colors.HexColor("#D7DEE6")
    c.setFillColor(ink); c.setFont("Helvetica-Bold",20); c.drawString(40,748,"FSFFL HISTORICAL TRADE ANALYSIS")
    c.setFillColor(muted); c.setFont("Helvetica",8.5); c.drawString(40,733,f"Trade date: {safe(data.get('trade_time_utc'))} | Point-in-time reconstruction | No hindsight in process grade")
    sides=list(data.get("sides",{}).values())
    grades=[s.get("decision_quality_at_time",{}).get("score",0) for s in sides]
    winner=None
    if len(sides)==2 and abs(grades[0]-grades[1])>=4: winner=sides[0] if grades[0]>grades[1] else sides[1]
    hero="PROCESS EDGE: "+safe(winner.get("team_name")) if winner else "PROCESS VERDICT: MIXED / CLOSE"
    c.setFillColor(navy); c.roundRect(34,660,544,54,2,fill=1,stroke=0)
    c.setFillColor(colors.white); c.setFont("Helvetica-Bold",13); c.drawString(48,691,hero)
    c.setFont("Helvetica",8.4); c.drawString(48,673,"Decision quality is graded from information available before the trade; realized outcomes are shown separately.")
    xcols=[42,312]; boxw=258; y0=416; boxh=226
    for x,s in zip(xcols,sides[:2]):
        c.setFillColor(panel); c.setStrokeColor(line); c.roundRect(x,y0,boxw,boxh,2,fill=1,stroke=1)
        c.setFillColor(ink); c.setFont("Helvetica-Bold",11); c.drawString(x+10,y0+boxh-18,safe(s.get("team_name")))
        dq=s.get("decision_quality_at_time",{}); c.setFont("Helvetica-Bold",15); c.drawString(x+10,y0+boxh-43,f"Grade {dq.get('grade','?')} - {safe(dq.get('label'))}")
        c.setFillColor(muted); c.setFont("Helvetica",7.6); c.drawString(x+10,y0+boxh-56,f"Process score {dq.get('score',0):+.1f} | confidence {float(dq.get('confidence',0))*100:.0f}%")
        ta=s.get("trade_assets",{})
        y=y0+boxh-76
        y=draw_lines(c,"Receives: "+", ".join(ta.get("received_player_names",[])+ta.get("received_picks",[])),x+10,y,boxw-20,"Helvetica-Bold",7.7,9.0,3)
        y=draw_lines(c,"Gives up: "+", ".join(ta.get("sent_player_names",[])+ta.get("sent_picks",[])),x+10,y-2,boxw-20,"Helvetica-Bold",7.7,9.0,3)
        pre=s.get("pretrade_roster",{}).get("summary",{}); comp=dq.get("components",{})
        y=y0+91; c.setFillColor(muted); c.setFont("Helvetica-Bold",7.2)
        c.drawString(x+10,y,"Pre-trade roster size"); c.drawString(x+135,y,"Roster-quality coverage")
        c.setFillColor(ink); c.setFont("Helvetica-Bold",9.8); c.drawString(x+10,y-13,str(pre.get("roster_size","?"))); c.drawString(x+135,y-13,f"{float(pre.get('quality_coverage',0))*100:.0f}%")
        y-=39; c.setFillColor(muted); c.setFont("Helvetica-Bold",7.2)
        c.drawString(x+10,y,"Need fit of acquisitions"); c.drawString(x+135,y,"Structural pick delta")
        fit=comp.get("acquisition_need_fit"); pd=comp.get("structural_pick_capital_delta")
        c.setFillColor(ink); c.setFont("Helvetica-Bold",9.8); c.drawString(x+10,y-13,"n/a" if fit is None else f"{float(fit)*100:.0f}%"); c.drawString(x+135,y-13,f"{float(pd or 0):+.2f}")
        outc=s.get("realized_outcome",{}); y-=39; c.setFillColor(muted); c.setFont("Helvetica-Bold",7.2); c.drawString(x+10,y,"Realized acquired-player output")
        c.setFillColor(ink); c.setFont("Helvetica-Bold",9.8); c.drawString(x+10,y-13,f"{float(outc.get('acquired_player_fsffl_points_after_trade',0)):,.1f} FSFFL pts")
    y=392; c.setFillColor(ink); c.setFont("Helvetica-Bold",10.5); c.drawString(42,y,"HOW TO READ THIS")
    y-=14; txt=("The process grade uses prior-completed-season player quality, the team's exact reconstructed pre-trade roster needs, and the structure of draft capital exchanged. "
                     "It does not use same-season future performance, current player values, or later pick outcomes. The outcome section is deliberately separate so a lucky result cannot turn weak process into strong process - or vice versa.")
    y=draw_lines(c,txt,42,y,526,size=8.1,leading=9.6)
    y-=5; c.setFont("Helvetica-Bold",10.5); c.drawString(42,y,"RECONSTRUCTION CONFIDENCE")
    y-=14; hp=data.get("historical_state_provider",{}); txt=(f"Historical roster and pick ownership source: {safe(hp.get('source'))}. Structural reconstruction confidence: {safe(hp.get('reconstruction_confidence'))}. "
        "Where exact historical dynasty-market prices are unavailable, this report does not fabricate them; that limitation reduces confidence in the process grade.")
    y=draw_lines(c,txt,42,y,526,size=8.1,leading=9.6)
    y-=5; c.setFont("Helvetica-Bold",10.5); c.drawString(42,y,"PROCESS VS. OUTCOME")
    y-=14
    for s in sides[:2]:
        dq=s.get("decision_quality_at_time",{}); pts=float((s.get("realized_outcome") or {}).get("acquired_player_fsffl_points_after_trade",0))
        verdict="Strong process" if dq.get("grade") in {"A","B"} else "Mixed process" if dq.get("grade")=="C" else "Weak process"
        c.setFont("Helvetica",8.1); c.drawString(50,y,f"{safe(s.get('team_name'))}: {verdict}; realized acquired-player production tracked so far: {pts:,.1f} FSFFL points."); y-=11
    c.setStrokeColor(line); c.line(42,48,570,48); c.setFillColor(muted); c.setFont("Helvetica",6.7)
    c.drawString(42,35,f"{MODEL_VERSION} | {data.get('model_version')} | historical state {safe(hp.get('reconstruction_confidence'))} | no same-season future leakage")
    c.save()


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--input",required=True); ap.add_argument("--output",required=True); a=ap.parse_args()
    data=json.loads(Path(a.input).read_text(encoding="utf-8")); render(data,Path(a.output)); print(a.output)

if __name__=="__main__": main()
