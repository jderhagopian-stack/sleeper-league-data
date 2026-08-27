#!/usr/bin/env python3
"""Render Simulator multiverse outliers as a standardized one-page report."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle
from fsffl_report_style import *

MODEL_VERSION='FSFFL-Multiverse-Outliers-Report-1.1'
DEFAULT=Path('data/simulator/2026/outputs/multiverse_outliers.json')

def load(p): return json.loads(Path(p).read_text(encoding='utf-8'))
def render(input_path,output):
    d=load(input_path); s=styles(); p=d.get('player_extremes') or {}; t=d.get('team_extremes') or {}; po=d.get('playoff_extremes') or {}
    doc=SimpleDocTemplate(str(output),pagesize=letter,leftMargin=.42*inch,rightMargin=.42*inch,topMargin=.4*inch,bottomMargin=.44*inch)
    story=[P(s,'FSFFL SIMULATION EXTREMES','FS_Title'),P(s,f"Season {d.get('season')} | {int(safe_float(d.get('simulations'))):,} simulated seasons | {d.get('model_version')}",'FS_Sub'),Spacer(1,5)]
    superstar=p.get('unexpected_superstar_season') or {}; best=t.get('best_regular_season_record') or {}; rare=po.get('rarest_champion') or {}; high=t.get('highest_single_week_score') or {}
    cards=[kpi_card(s,'Unexpected Superstar',clean(superstar.get('player')),'positive',1.75*inch),kpi_card(s,'Best Record',clean(best.get('record')),'blue',1.75*inch),kpi_card(s,'Rarest Champion',clean(rare.get('team')),'warning',1.75*inch),kpi_card(s,'Highest Team Week',f"{safe_float(high.get('score')):.1f}",'positive',1.75*inch)]
    ct=Table([cards],colWidths=[1.86*inch]*4); ct.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),1),('RIGHTPADDING',(0,0),(-1,-1),1)])); story += [ct,Spacer(1,6)]
    pr=[[P(s,'PLAYER EXTREME','FS_CardLabel'),P(s,'RESULT','FS_CardLabel')]]
    for label,key in [('Highest season total','highest_regular_season_total'),('Highest single week','highest_single_week'),('Unexpected superstar','unexpected_superstar_season')]:
        x=p.get(key) or {}; detail=f"{x.get('player')} ({x.get('position')}, {x.get('fsffl_team')}) - "
        detail += f"{safe_float(x.get('season_points')):.1f} season pts" if x.get('season_points') is not None else f"{safe_float(x.get('points')):.1f} pts in Week {x.get('week')}"
        pr.append([P(s,label,'FS_Body'),P(s,detail,'FS_Body')])
    pt=Table(pr,colWidths=[1.45*inch,2.15*inch]); pt.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),LIGHT_GRAY),('GRID',(0,0),(-1,-1),.35,MID_GRAY),('LEFTPADDING',(0,0),(-1,-1),4),('RIGHTPADDING',(0,0),(-1,-1),4)]))
    tr=[[P(s,'TEAM EXTREME','FS_CardLabel'),P(s,'RESULT','FS_CardLabel')]]
    for label,key in [('Best record','best_regular_season_record'),('Most regular-season PF','highest_regular_season_points'),('Best team to miss playoffs','best_team_to_miss_playoffs'),('Largest margin','biggest_margin_of_victory'),('Lowest team week','lowest_single_week_score')]:
        x=t.get(key) or {}
        if key=='biggest_margin_of_victory': detail=f"{x.get('winner')} over {x.get('loser')} by {safe_float(x.get('margin')):.1f}"
        elif key in {'highest_single_week_score','lowest_single_week_score'}: detail=f"{x.get('team')} - {safe_float(x.get('score')):.1f}"
        else: detail=f"{x.get('team')} - {x.get('record') or ''} {safe_float(x.get('points_for')):.1f} PF".strip()
        tr.append([P(s,label,'FS_Body'),P(s,detail,'FS_Body')])
    tt=Table(tr,colWidths=[1.35*inch,2.3*inch]); tt.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),LIGHT_GRAY),('GRID',(0,0),(-1,-1),.35,MID_GRAY),('LEFTPADDING',(0,0),(-1,-1),4),('RIGHTPADDING',(0,0),(-1,-1),4)]))
    left=[P(s,'PLAYER EXTREMES','FS_Section'),pt,Spacer(1,5),P(s,'PLAYOFF EXTREMES','FS_Section'),P(s,f"Highest-seed champion: {(po.get('highest_seed_champion') or {}).get('team')} as seed {(po.get('highest_seed_champion') or {}).get('seed')}. Rarest champion: {rare.get('team')} with modeled title rate {safe_float(rare.get('title_rate'))*100:.3f}%.",'FS_Body')]
    right=[P(s,'TEAM EXTREMES','FS_Section'),tt,Spacer(1,5),P(s,'READING GUIDE','FS_Section'),P(s,'These are unusual outcomes that happened in at least one simulated season. They are possibilities, not the most likely forecast. Historical league records must still be checked separately before calling any simulated result an FSFFL record.','FS_Small')]
    cols=Table([[left,right]],colWidths=[3.7*inch,3.74*inch]); cols.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('RIGHTPADDING',(0,0),(0,0),9),('LEFTPADDING',(1,0),(1,0),9),('LINEBEFORE',(1,0),(1,0),.7,MID_GRAY),('LEFTPADDING',(0,0),(0,0),0),('RIGHTPADDING',(1,0),(1,0),0)])); story.append(cols)
    doc.build(story,onFirstPage=lambda c,x: footer(c,f'{MODEL_VERSION} | Simulation extremes | Plain-English presentation'))
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--input',default=str(DEFAULT)); ap.add_argument('--output',required=True); a=ap.parse_args(); render(a.input,Path(a.output))
if __name__=='__main__': main()
