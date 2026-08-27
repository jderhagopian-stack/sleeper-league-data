#!/usr/bin/env python3
"""Render league-wide simulator standings as a polished one-page competitive landscape PDF."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle
from fsffl_report_style import *

MODEL_VERSION='FSFFL-League-Power-Report-1.2'

def load(p): return json.loads(Path(p).read_text(encoding='utf-8'))

def render(input_path,output):
    d=load(input_path); teams=sorted(d.get('teams') or [],key=lambda x:(safe_float(x.get('championship_probability')),safe_float(x.get('expected_wins'))),reverse=True); s=styles()
    doc=SimpleDocTemplate(str(output),pagesize=letter,leftMargin=.42*inch,rightMargin=.42*inch,topMargin=.4*inch,bottomMargin=.44*inch)
    story=[P(s,'FSFFL LEAGUE COMPETITIVE LANDSCAPE','FS_Title'),P(s,f"Season {d.get('season')} | {d.get('simulator_model_version')} | Ranked by championship probability, then expected wins",'FS_Sub'),Spacer(1,5)]
    if teams:
        lead=teams[0]; cards=[kpi_card(s,'Highest Title Odds',clean(lead.get('team_name')),'blue',1.75*inch),kpi_card(s,'Top Title Odds',f"{safe_float(lead.get('championship_probability'))*100:.1f}%",'positive',1.75*inch),kpi_card(s,'Top Expected Wins',f"{max(safe_float(x.get('expected_wins')) for x in teams):.2f}",'positive',1.75*inch),kpi_card(s,'League Teams',str(len(teams)),'neutral',1.75*inch)]
        ct=Table([cards],colWidths=[1.86*inch]*4); ct.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),1),('RIGHTPADDING',(0,0),(-1,-1),1)])); story += [ct,Spacer(1,6)]
    rows=[[P(s,'#','FS_CardLabel'),P(s,'TEAM','FS_CardLabel'),P(s,'WINS','FS_CardLabel'),P(s,'POINTS','FS_CardLabel'),P(s,'PLAYOFF','FS_CardLabel'),P(s,'1ST-RD BYE','FS_CardLabel'),P(s,'DIVISION','FS_CardLabel'),P(s,'TITLE','FS_CardLabel')]]
    for i,t in enumerate(teams,1):
        rows.append([P(s,str(i),'FS_Body'),P(s,t.get('team_name'),'FS_Body'),P(s,f"{safe_float(t.get('expected_wins')):.2f}",'FS_Body'),P(s,f"{safe_float(t.get('expected_points_for')):.0f}",'FS_Body'),P(s,f"{safe_float(t.get('playoff_probability'))*100:.0f}%",'FS_Body'),P(s,f"{safe_float(t.get('bye_probability'))*100:.0f}%",'FS_Body'),P(s,f"{safe_float(t.get('division_probability'))*100:.0f}%",'FS_Body'),P(s,f"{safe_float(t.get('championship_probability'))*100:.1f}%",'FS_Body')])
    tbl=Table(rows,colWidths=[.28*inch,2.35*inch,.62*inch,.66*inch,.78*inch,.62*inch,.72*inch,.67*inch],repeatRows=1)
    tbl.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),NAVY),('TEXTCOLOR',(0,0),(-1,0),WHITE),('GRID',(0,0),(-1,-1),.35,MID_GRAY),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),3),('RIGHTPADDING',(0,0),(-1,-1),3),('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),4)])); story += [P(s,'PROJECTED LEAGUE RANKING','FS_Section'),tbl,Spacer(1,5)]
    val=d.get('validation') or {}; story += [P(s,'HOW MUCH TO TRUST THIS','FS_Section'),P(s,f"Model checks passed: {val.get('validation_passed')}. This ranking comes directly from the season simulation. The report does not invent a separate power ranking or add subjective grades.",'FS_Small')]
    doc.build(story,onFirstPage=lambda c,x: footer(c,f'{MODEL_VERSION} | Simulator output | Plain-English presentation'))

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--input',default='data/gm/league/simulator_context.json'); ap.add_argument('--output',required=True); a=ap.parse_args(); render(a.input,Path(a.output))
if __name__=='__main__': main()
