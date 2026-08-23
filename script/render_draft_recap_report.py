#!/usr/bin/env python3
"""Render a completed Sleeper rookie draft as a standardized one-page recap."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle
from fsffl_report_style import *

MODEL_VERSION='FSFFL-Draft-Recap-Report-1.0'
DRAFTS=Path('data/drafts.json'); INDEX=Path('data/gm/franchise_index.json')

def load(p): return json.loads(Path(p).read_text(encoding='utf-8'))
def team_map():
    if not INDEX.exists(): return {}
    d=load(INDEX); return {str(x.get('user_id')):x.get('team_name') for x in d.get('teams') or []}
def select_draft(ds,season=None):
    rows=[x for x in ds if str((x.get('draft') or {}).get('status'))=='complete']
    if season: rows=[x for x in rows if str((x.get('draft') or {}).get('season'))==str(season)]
    if not rows: raise ValueError('No completed draft found')
    return sorted(rows,key=lambda x:str((x.get('draft') or {}).get('season') or ''),reverse=True)[0]
def pname(p):
    m=p.get('metadata') or {}; return ' '.join(x for x in [m.get('first_name'),m.get('last_name')] if x) or str(p.get('player_id'))
def render(input_path,season,user_id,output):
    ds=load(input_path); row=select_draft(ds,season); dr=row.get('draft') or {}; picks=row.get('picks') or []; teams=team_map(); s=styles(); season=str(dr.get('season'))
    doc=SimpleDocTemplate(str(output),pagesize=letter,leftMargin=.42*inch,rightMargin=.42*inch,topMargin=.4*inch,bottomMargin=.44*inch)
    story=[P(s,f'FSFFL {season} ROOKIE DRAFT RECAP','FS_Title'),P(s,f"{(dr.get('metadata') or {}).get('name') or 'FSFFL Dynasty'} | {dr.get('settings',{}).get('rounds',3)} rounds | {dr.get('settings',{}).get('teams',12)} teams | Status: {dr.get('status')}",'FS_Sub'),Spacer(1,5)]
    cards=[kpi_card(s,'Draft Season',season,'blue',1.75*inch),kpi_card(s,'Total Picks',str(len(picks)),'neutral',1.75*inch),kpi_card(s,'1.01',pname(min(picks,key=lambda x:x.get('pick_no',999))) if picks else '-','positive',1.75*inch),kpi_card(s,'Format',str((dr.get('metadata') or {}).get('scoring_type') or 'dynasty'),'neutral',1.75*inch)]
    ct=Table([cards],colWidths=[1.86*inch]*4); ct.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),1),('RIGHTPADDING',(0,0),(-1,-1),1)])); story += [ct,Spacer(1,6)]
    first=[p for p in picks if int(p.get('round') or 0)==1]
    rows=[[P(s,'PICK','FS_CardLabel'),P(s,'PLAYER','FS_CardLabel'),P(s,'POS','FS_CardLabel'),P(s,'TEAM','FS_CardLabel')]]
    for p in sorted(first,key=lambda x:x.get('pick_no',999)):
        m=p.get('metadata') or {}; rows.append([P(s,f"1.{int(p.get('draft_slot') or 0):02d}",'FS_Body'),P(s,pname(p),'FS_Body'),P(s,m.get('position'),'FS_Body'),P(s,teams.get(str(p.get('picked_by')),str(p.get('picked_by'))),'FS_Small')])
    first_tbl=Table(rows,colWidths=[.55*inch,1.65*inch,.45*inch,1.5*inch]); first_tbl.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),NAVY),('TEXTCOLOR',(0,0),(-1,0),WHITE),('GRID',(0,0),(-1,-1),.35,MID_GRAY),('LEFTPADDING',(0,0),(-1,-1),4),('RIGHTPADDING',(0,0),(-1,-1),4),('TOPPADDING',(0,0),(-1,-1),3),('BOTTOMPADDING',(0,0),(-1,-1),3)]))
    left=[P(s,'FIRST ROUND','FS_Section'),first_tbl]
    focus=[]
    if user_id: focus=[p for p in picks if str(p.get('picked_by'))==str(user_id)]
    if not focus and user_id:
        # traded rookie picks can still roster to the drafting roster; do not infer ownership beyond picked_by
        pass
    frows=[[P(s,'PICK','FS_CardLabel'),P(s,'PLAYER','FS_CardLabel'),P(s,'POS','FS_CardLabel')]]
    for p in sorted(focus,key=lambda x:x.get('pick_no',999)):
        m=p.get('metadata') or {}; frows.append([P(s,f"{int(p.get('round') or 0)}.{int(p.get('draft_slot') or 0):02d}",'FS_Body'),P(s,pname(p),'FS_Body'),P(s,m.get('position'),'FS_Body')])
    if len(frows)==1: frows.append([P(s,'-','FS_Body'),P(s,'No picks found for selected user','FS_Body'),P(s,'','FS_Body')])
    ft=Table(frows,colWidths=[.6*inch,1.7*inch,.5*inch]); ft.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),LIGHT_GRAY),('GRID',(0,0),(-1,-1),.35,MID_GRAY),('LEFTPADDING',(0,0),(-1,-1),4),('RIGHTPADDING',(0,0),(-1,-1),4)]))
    pos={}
    for p in picks:
        pos[(p.get('metadata') or {}).get('position')]=pos.get((p.get('metadata') or {}).get('position'),0)+1
    pos_txt=' | '.join(f'{k} {v}' for k,v in sorted(pos.items(),key=lambda kv:(str(kv[0]))))
    right=[P(s,'SELECTED TEAM PICKS','FS_Section'),P(s,teams.get(str(user_id),str(user_id or 'not selected')),'FS_Sub'),Spacer(1,3),ft,Spacer(1,5),P(s,'POSITION MIX - FULL DRAFT','FS_Section'),P(s,pos_txt,'FS_Body'),Spacer(1,5),P(s,'READING GUIDE','FS_Section'),P(s,'This recap formats completed Sleeper draft results only. It does not grade selections, infer value won/lost, or reconstruct traded-pick ownership beyond the recorded drafter. Those judgments belong in conversational analysis or a separate draft model.','FS_Small')]
    cols=Table([[left,right]],colWidths=[4.28*inch,3.16*inch]); cols.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('RIGHTPADDING',(0,0),(0,0),9),('LEFTPADDING',(1,0),(1,0),9),('LINEBEFORE',(1,0),(1,0),.7,MID_GRAY),('LEFTPADDING',(0,0),(0,0),0),('RIGHTPADDING',(1,0),(1,0),0)])); story.append(cols)
    doc.build(story,onFirstPage=lambda c,d: footer(c,f'{MODEL_VERSION} | Raw completed Sleeper draft | Presentation-only'))
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--input',default=str(DRAFTS)); ap.add_argument('--season'); ap.add_argument('--user-id'); ap.add_argument('--output',required=True); a=ap.parse_args(); render(a.input,a.season,a.user_id,Path(a.output))
if __name__=='__main__': main()
