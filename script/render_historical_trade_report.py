#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.pdfbase.pdfmetrics import stringWidth

MODEL_VERSION = "FSFFL-GM-Historical-Trade-Report-1.2"


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


def fmt_pp(v):
    return f"{float(v or 0)*100:+.1f} pp"


def render(data,out):
    W,H=letter; c=canvas.Canvas(str(out),pagesize=letter)
    ink=colors.HexColor("#172536"); navy=colors.HexColor("#14263D"); muted=colors.HexColor("#667485"); panel=colors.HexColor("#F2F5F8"); line=colors.HexColor("#D7DEE6")
    c.setFillColor(ink); c.setFont("Helvetica-Bold",20); c.drawString(40,748,"FSFFL HISTORICAL TRADE ANALYSIS")
    c.setFillColor(muted); c.setFont("Helvetica",8.5); c.drawString(40,733,f"Trade date: {safe(data.get('trade_time_utc'))} | Point-in-time reconstruction | GM 3.0 time-travel wrapper")

    gm3=data.get("gm3_evaluation") or {}
    graded=gm3.get("status")=="GRADED_BY_GM3_CORE"
    hero="GM 3.0 HISTORICAL EVALUATION COMPLETE" if graded else "DECISION GRADE WITHHELD - HISTORICAL INPUTS INCOMPLETE"
    c.setFillColor(navy); c.roundRect(34,660,544,54,2,fill=1,stroke=0)
    c.setFillColor(colors.white); c.setFont("Helvetica-Bold",12.5); c.drawString(48,691,hero)
    c.setFont("Helvetica",8.2)
    sub=("The trade is evaluated by the same GM 3.0 core used for current decisions."
         if graded else "The old standalone historical score is retired; no grade is emitted until time-frozen GM 3.0 inputs are available.")
    c.drawString(48,673,sub)

    sides=list(data.get("sides",{}).values())
    xcols=[42,312]; boxw=258; y0=416; boxh=226
    team_results=gm3.get("team_results") or {}
    for x,s in zip(xcols,sides[:2]):
        c.setFillColor(panel); c.setStrokeColor(line); c.roundRect(x,y0,boxw,boxh,2,fill=1,stroke=1)
        c.setFillColor(ink); c.setFont("Helvetica-Bold",11); c.drawString(x+10,y0+boxh-18,safe(s.get("team_name")))
        tr=team_results.get(str(s.get("user_id"))) or {}
        if graded:
            dec=tr.get("decision") or {}
            c.setFont("Helvetica-Bold",13); c.drawString(x+10,y0+boxh-42,safe(dec.get("band","GM3 evaluated")).replace("_"," ").title())
            c.setFillColor(muted); c.setFont("Helvetica",7.6); c.drawString(x+10,y0+boxh-55,f"Team state: {safe(tr.get('team_state'))}")
        else:
            c.setFont("Helvetica-Bold",13); c.drawString(x+10,y0+boxh-42,"NOT GRADED")
            c.setFillColor(muted); c.setFont("Helvetica",7.6); c.drawString(x+10,y0+boxh-55,"Waiting for complete time-frozen GM 3.0 inputs")

        ta=s.get("trade_assets",{})
        y=y0+boxh-76
        y=draw_lines(c,"Receives: "+", ".join(ta.get("received_player_names",[])+ta.get("received_picks",[])),x+10,y,boxw-20,"Helvetica-Bold",7.7,9.0,3)
        y=draw_lines(c,"Gives up: "+", ".join(ta.get("sent_player_names",[])+ta.get("sent_picks",[])),x+10,y-2,boxw-20,"Helvetica-Bold",7.7,9.0,3)

        if graded:
            d=tr.get("delta") or {}; st=tr.get("strategic") or {}
            y=y0+91; c.setFillColor(muted); c.setFont("Helvetica-Bold",7.2)
            c.drawString(x+10,y,"Expected wins change"); c.drawString(x+135,y,"Championship odds")
            c.setFillColor(ink); c.setFont("Helvetica-Bold",9.8)
            c.drawString(x+10,y-13,f"{float(d.get('expected_wins') or 0):+.2f}")
            c.drawString(x+135,y-13,fmt_pp(d.get("championship_probability")))
            y-=39; c.setFillColor(muted); c.setFont("Helvetica-Bold",7.2)
            c.drawString(x+10,y,"Package-Effective Value"); c.drawString(x+135,y,"Value to This Team")
            c.setFillColor(ink); c.setFont("Helvetica-Bold",9.8)
            c.drawString(x+10,y-13,f"{float(st.get('package_effective_value_delta', st.get('intrinsic_dynasty_delta')) or 0):+,.0f}")
            c.drawString(x+135,y-13,f"{float(st.get('base_franchise_value_delta') or 0):+,.0f}")
        else:
            pre=s.get("pretrade_roster",{}).get("summary",{})
            y=y0+91; c.setFillColor(muted); c.setFont("Helvetica-Bold",7.2)
            c.drawString(x+10,y,"Reconstructed roster size"); c.drawString(x+135,y,"Roster-quality coverage")
            c.setFillColor(ink); c.setFont("Helvetica-Bold",9.8)
            c.drawString(x+10,y-13,str(pre.get("roster_size","?")))
            c.drawString(x+135,y-13,f"{float(pre.get('quality_coverage',0))*100:.0f}%")
            y-=39; c.setFillColor(muted); c.setFont("Helvetica-Bold",7.2)
            c.drawString(x+10,y,"Decision model"); c.drawString(x+135,y,"Current values used")
            c.setFillColor(ink); c.setFont("Helvetica-Bold",9.2)
            c.drawString(x+10,y-13,"GM 3.0 only")
            c.drawString(x+135,y-13,"NO")

        outc=s.get("realized_outcome",{}); y=y0+21
        c.setFillColor(muted); c.setFont("Helvetica-Bold",7.2); c.drawString(x+10,y,"Realized acquired-player output")
        c.setFillColor(ink); c.setFont("Helvetica-Bold",9.8); c.drawString(x+10,y-13,f"{float(outc.get('acquired_player_fsffl_points_after_trade',0)):,.1f} FSFFL pts")

    y=392; c.setFillColor(ink); c.setFont("Helvetica-Bold",10.5); c.drawString(42,y,"ARCHITECTURE")
    y-=14
    txt=("Historical Trade Analysis now reconstructs the league at the transaction timestamp, then delegates decision evaluation to the canonical GM 3.0 Decision Lab and Simulator path. "
         "It no longer owns a separate pick/need/player-quality grading formula. Intrinsic FSFFL value is model-generated; market values are retained only as sanity checks. Canonical nonlinear package economics are used in the final trade evaluation. If required frozen inputs are missing, the model returns NOT GRADED rather than inventing replacements.")
    y=draw_lines(c,txt,42,y,526,size=8.1,leading=9.6)

    y-=5; c.setFont("Helvetica-Bold",10.5); c.drawString(42,y,"CURRENT STATUS")
    y-=14
    if graded:
        txt=f"GM 3.0 evaluation completed with {gm3.get('n_sims')} paired simulations. Current-day values were not used."
    else:
        miss=", ".join(gm3.get("missing_time_frozen_inputs") or [])
        txt=f"Historical state is reconstructed, but this trade is intentionally ungraded. Missing time-frozen GM 3.0 inputs: {miss}."
    y=draw_lines(c,txt,42,y,526,size=8.1,leading=9.6)

    y-=5; c.setFont("Helvetica-Bold",10.5); c.drawString(42,y,"PROCESS VS. OUTCOME")
    y-=14
    for s in sides[:2]:
        pts=float((s.get("realized_outcome") or {}).get("acquired_player_fsffl_points_after_trade",0))
        label="GM 3.0 process evaluation available" if graded else "process grade withheld"
        c.setFont("Helvetica",8.1); c.drawString(50,y,f"{safe(s.get('team_name'))}: {label}; tracked acquired-player production: {pts:,.1f} FSFFL points."); y-=11

    hp=data.get("historical_state_provider",{})
    c.setStrokeColor(line); c.line(42,48,570,48); c.setFillColor(muted); c.setFont("Helvetica",6.7)
    c.drawString(42,35,f"{MODEL_VERSION} | {data.get('model_version')} | historical state {safe(hp.get('reconstruction_confidence'))} | standalone v1 score retired")
    c.save()


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--input",required=True); ap.add_argument("--output",required=True); a=ap.parse_args()
    data=json.loads(Path(a.input).read_text(encoding="utf-8")); render(data,Path(a.output)); print(a.output)

if __name__=="__main__": main()
