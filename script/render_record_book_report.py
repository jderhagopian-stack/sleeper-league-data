#!/usr/bin/env python3
"""Render FSFFL competition record-book data as a standardized one-page historical report."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle
from fsffl_report_style import *

MODEL_VERSION='FSFFL-Record-Book-Report-1.0'
DEFAULT=Path('data/record_book/competition_records.json')

def load(p): return json.loads(Path(p).read_text(encoding='utf-8'))
def render(input_path,output):
    d=load(input_path); teams=d.get('franchise_regular_season') or []; teams=sorted(teams,key=lambda x:(safe_float(x.get('win_pct')),safe_float(x.get('points_for'))),reverse=True); s=styles(); seasons=(d.get('methodology') or {}).get('seasons') or []
    doc=SimpleDocTemplate(str(output),pagesize=letter,leftMargin=.42*inch,rightMargin=.42*inch,topMargin=.4*inch,bottomMargin=.44*inch)
    story=[P(s,'FSFFL HISTORICAL RECORD BOOK','FS_Title'),P(s,f"Regular season history | Seasons {', '.join(seasons)} | {(d.get('counts') or {}).get('regular_season_games',0)} games",'FS_Sub'),Spacer(1,5)]
    if teams:
        best=teams[0]; pts=max(teams,key=lambda x:safe_float(x.get('points_for'))); high=max(teams,key=lambda x:int(x.get('weekly_high_score_finishes') or 0)); diff=max(teams,key=lambda x:safe_float(x.get('point_diff')))
        cards=[kpi_card(s,'Best Win %',clean(best.get('team_name')),'positive',1.75*inch),kpi_card(s,'Most Points',clean(pts.get('team_name')),'blue',1.75*inch),kpi_card(s,'Most Weekly Highs',clean(high.get('team_name')),'blue',1.75*inch),kpi_card(s,'Best Point Diff',clean(diff.get('team_name')),'positive',1.75*inch)]
        ct=Table([cards],colWidths=[1.86*inch]*4); ct.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),1),('RIGHTPADDING',(0,0),(-1,-1),1)])); story += [ct,Spacer(1,6)]
    rows=[[P(s,'#','FS_CardLabel'),P(s,'FRANCHISE','FS_CardLabel'),P(s,'W-L','FS_CardLabel'),P(s,'WIN %','FS_CardLabel'),P(s,'PF','FS_CardLabel'),P(s,'DIFF','FS_CardLabel'),P(s,'AVG','FS_CardLabel'),P(s,'HIGH WK','FS_CardLabel')]]
    for i,t in enumerate(teams,1):
        rows.append([P(s,str(i),'FS_Body'),P(s,t.get('team_name'),'FS_Body'),P(s,f"{t.get('wins')}-{t.get('losses')}",'FS_Body'),P(s,f"{safe_float(t.get('win_pct'))*100:.1f}%",'FS_Body'),P(s,f"{safe_float(t.get('points_for')):,.0f}",'FS_Body'),P(s,f"{safe_float(t.get('point_diff')):+,.0f}",'FS_Body'),P(s,f"{safe_float(t.get('avg_points')):.1f}",'FS_Body'),P(s,str(t.get('weekly_high_score_finishes') or 0),'FS_Body')])
    tbl=Table(rows,colWidths=[.28*inch,2.1*inch,.62*inch,.64*inch,.72*inch,.68*inch,.62*inch,.65*inch]); tbl.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),NAVY),('TEXTCOLOR',(0,0),(-1,0),WHITE),('GRID',(0,0),(-1,-1),.35,MID_GRAY),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),3),('RIGHTPADDING',(0,0),(-1,-1),3),('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),4)])); story += [P(s,'ALL-TIME REGULAR-SEASON TABLE','FS_Section'),tbl,Spacer(1,5)]
    story += [P(s,'METHODOLOGY','FS_Section'),P(s,f"{(d.get('methodology') or {}).get('head_to_head_and_streaks','')} | Franchise identity: {(d.get('methodology') or {}).get('franchise_identity','')}. This report formats the record-book dataset only.",'FS_Small')]
    doc.build(story,onFirstPage=lambda c,x: footer(c,f'{MODEL_VERSION} | Raw competition record-book dataset | Presentation-only'))
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--input',default=str(DEFAULT)); ap.add_argument('--output',required=True); a=ap.parse_args(); render(a.input,Path(a.output))
if __name__=='__main__': main()
