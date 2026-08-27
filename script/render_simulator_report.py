#!/usr/bin/env python3
"""Render Season Simulator raw output as a polished one-page team outlook PDF."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle
from fsffl_report_style import *

MODEL_VERSION='FSFFL-Season-Simulator-Report-1.1'

def load(p): return json.loads(Path(p).read_text(encoding='utf-8'))

def find_team(data, uid=None, name=None):
    for t in data.get('teams') or []:
        if uid and str(t.get('user_id'))==str(uid): return t
        if name and str(t.get('team_name'))==str(name): return t
    raise ValueError('Team not found in simulator context')

def rank(teams,key,uid):
    rows=sorted(teams,key=lambda x:safe_float(x.get(key)),reverse=True)
    for i,t in enumerate(rows,1):
        if str(t.get('user_id'))==str(uid): return i
    return None

def render(input_path,uid,name,output):
    data=load(input_path); t=find_team(data,uid,name); teams=data.get('teams') or []; s=styles()
    doc=SimpleDocTemplate(str(output),pagesize=letter,leftMargin=.48*inch,rightMargin=.48*inch,topMargin=.42*inch,bottomMargin=.44*inch)
    story=[P(s,'FSFFL SEASON SIMULATOR OUTLOOK','FS_Title'),P(s,f"{t.get('team_name')} | Manager: {t.get('manager')} | Season {data.get('season')} | {data.get('simulator_model_version')}",'FS_Sub'),Spacer(1,5)]
    uid=t.get('user_id'); wins=safe_float(t.get('expected_wins')); pf=safe_float(t.get('expected_points_for')); po=safe_float(t.get('playoff_probability')); bye=safe_float(t.get('bye_probability')); title=safe_float(t.get('championship_probability')); div=safe_float(t.get('division_probability'))
    cards=[
        kpi_card(s,'Expected Wins',f'{wins:.2f}','positive' if wins>=8 else 'neutral'),
        kpi_card(s,'Expected Points',f'{pf:,.0f}','blue'),
        kpi_card(s,'Playoff Odds',f'{po*100:.1f}%','positive' if po>=.65 else 'warning'),
        kpi_card(s,'First-Round Bye Odds',f'{bye*100:.1f}%','positive' if bye>=.25 else 'neutral'),
        kpi_card(s,'Championship Odds',f'{title*100:.1f}%','positive' if title>=.12 else 'neutral'),
        kpi_card(s,'Division-Win Odds',f'{div*100:.1f}%','blue'),
    ]
    ct=Table([cards[:3],cards[3:]],colWidths=[2.47*inch]*3); ct.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),1),('RIGHTPADDING',(0,0),(-1,-1),1)])); story += [ct,Spacer(1,6)]
    ranks=[('Expected Wins',rank(teams,'expected_wins',uid)),('Points For',rank(teams,'expected_points_for',uid)),('Playoff Odds',rank(teams,'playoff_probability',uid)),('Championship Odds',rank(teams,'championship_probability',uid))]
    rr=[[P(s,'CATEGORY','FS_CardLabel'),P(s,'LEAGUE RANK','FS_CardLabel')]]+[[P(s,a,'FS_Body'),P(s,f'#{b} of {len(teams)}','FS_Body')] for a,b in ranks]
    rt=Table(rr,colWidths=[1.5*inch,1.1*inch]); rt.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),LIGHT_GRAY),('GRID',(0,0),(-1,-1),.35,MID_GRAY),('LEFTPADDING',(0,0),(-1,-1),5),('RIGHTPADDING',(0,0),(-1,-1),5)]))
    seeds=t.get('seed_probabilities') or {}; likely=sorted(((int(k),safe_float(v)) for k,v in seeds.items()),key=lambda x:x[1],reverse=True)[:5]
    sr=[[P(s,'SEED','FS_CardLabel'),P(s,'PROBABILITY','FS_CardLabel')]]+[[P(s,str(k),'FS_Body'),P(s,f'{v*100:.1f}%','FS_Body')] for k,v in likely]
    st=Table(sr,colWidths=[.8*inch,1.15*inch]); st.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),LIGHT_GRAY),('GRID',(0,0),(-1,-1),.35,MID_GRAY),('LEFTPADDING',(0,0),(-1,-1),5),('RIGHTPADDING',(0,0),(-1,-1),5)]))
    left=[P(s,'LEAGUE POSITION','FS_Section'),rt,Spacer(1,5),P(s,'MOST LIKELY SEEDS','FS_Section'),st]
    leaders=sorted(teams,key=lambda x:safe_float(x.get('championship_probability')),reverse=True)[:6]
    lr=[[P(s,'TEAM','FS_CardLabel'),P(s,'WINS','FS_CardLabel'),P(s,'PLAYOFF','FS_CardLabel'),P(s,'TITLE','FS_CardLabel')]]
    for x in leaders:
        lr.append([P(s,x.get('team_name'),'FS_Body'),P(s,f"{safe_float(x.get('expected_wins')):.2f}",'FS_Body'),P(s,f"{safe_float(x.get('playoff_probability'))*100:.0f}%",'FS_Body'),P(s,f"{safe_float(x.get('championship_probability'))*100:.1f}%",'FS_Body')])
    lt=Table(lr,colWidths=[2.1*inch,.62*inch,.72*inch,.68*inch]); lt.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),LIGHT_GRAY),('GRID',(0,0),(-1,-1),.35,MID_GRAY),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),4),('RIGHTPADDING',(0,0),(-1,-1),4)]))
    validation=data.get('validation') or {}; coverage='; '.join(str(x.get('message')) for x in validation.get('checks') or [] if x.get('code') in {'ROSTER_PROJECTION_COVERAGE','PLAYOFF_PROJECTION_COVERAGE'})
    right=[P(s,'CHAMPIONSHIP RACE','FS_Section'),lt,Spacer(1,5),P(s,'HOW MUCH TO TRUST THIS','FS_Section'),P(s,f"Model checks passed: {validation.get('validation_passed')}. {coverage}",'FS_Small'),Spacer(1,3),P(s,'READING GUIDE','FS_Section'),P(s,"These are the simulator's direct results. The report simply puts them into readable football terms; it does not change the simulation or add new judgments.",'FS_Small')]
    cols=Table([[left,right]],colWidths=[2.9*inch,4.54*inch]); cols.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(0,0),0),('RIGHTPADDING',(0,0),(0,0),9),('LEFTPADDING',(1,0),(1,0),9),('RIGHTPADDING',(1,0),(1,0),0),('LINEBEFORE',(1,0),(1,0),.7,MID_GRAY)])); story.append(cols)
    doc.build(story,onFirstPage=lambda c,d: footer(c,f'{MODEL_VERSION} | Simulator output | Plain-English presentation'))

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--input',default='data/gm/league/simulator_context.json'); ap.add_argument('--user-id'); ap.add_argument('--team-name'); ap.add_argument('--output',required=True); a=ap.parse_args(); render(a.input,a.user_id,a.team_name,Path(a.output))
if __name__=='__main__': main()
