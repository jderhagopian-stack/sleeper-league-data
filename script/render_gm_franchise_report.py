#!/usr/bin/env python3
"""Render GM 3.0 raw franchise outputs as a polished one-page PDF."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib import colors
from fsffl_report_style import *
from report_language import label, team_state

MODEL_VERSION='FSFFL-GM-Franchise-Report-1.1'

def load(p): return json.loads(Path(p).read_text(encoding='utf-8'))

def find_team(index, uid=None, slug=None):
    for t in index.get('teams') or []:
        if uid and str(t.get('user_id'))==str(uid): return t
        if slug and str(t.get('slug'))==str(slug): return t
    raise ValueError('Team not found in franchise_index.json')

def franchise_story(cc,state):
    contender=safe_float(cc.get("contender_score"))
    dynasty=safe_float(cc.get("dynasty_roster_score"))
    needs=cc.get("biggest_position_needs") or []
    need=", ".join(str(x.get("position")) for x in needs[:2] if x.get("position")) or "no clearly dominant position"
    if "Contender" in state and dynasty>=.6:
        lead="The roster is built to compete now without sacrificing much long-term strength."
    elif "Contender" in state:
        lead="The roster is positioned to compete now, but some of that strength comes at the expense of long-term depth or flexibility."
    elif dynasty>=.6:
        lead="The roster has a strong long-term base, but its current-season profile is not yet that of a top contender."
    else:
        lead="The roster needs improvement in both current-season strength and long-term asset quality."
    return f"{lead} Win-now readiness is {contender:.3f} and long-term roster strength is {dynasty:.3f}; the most visible roster pressure is at {need}."

def render(index_path, uid, slug, output):
    index=load(index_path); team=find_team(index,uid,slug)
    cc=load(team['paths']['command_center'])
    s=styles(); doc=SimpleDocTemplate(str(output),pagesize=letter,leftMargin=.48*inch,rightMargin=.48*inch,topMargin=.42*inch,bottomMargin=.44*inch)
    story=[P(s,'FSFFL GM 3.0 FRANCHISE REPORT','FS_Title'),P(s,f"{cc.get('focal_team')} | Manager: {cc.get('focal_manager')} | Model: {cc.get('model_version')}",'FS_Sub'),Spacer(1,5)]
    state=team_state(cc.get('team_state'))
    state_bg=LIGHT_GREEN if 'Contender' in state else LIGHT_BLUE
    banner=Table([[P(s,'TEAM OUTLOOK','FS_WhiteLabel'),P(s,f'<b>{state}</b> &nbsp;&nbsp; Win-Now Readiness {safe_float(cc.get("contender_score")):.3f} | Long-Term Roster Strength {safe_float(cc.get("dynasty_roster_score")):.3f}','FS_Body')]],colWidths=[1.5*inch,5.94*inch])
    banner.setStyle(TableStyle([('BACKGROUND',(0,0),(0,0),NAVY),('BACKGROUND',(1,0),(1,0),state_bg),('BOX',(0,0),(-1,-1),.7,MID_GRAY),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6)]))
    story += [banner,Spacer(1,6),P(s,'TEAM STORY','FS_Section'),P(s,franchise_story(cc,state),'FS_Body'),Spacer(1,5)]
    cards=[
        kpi_card(s,'2026 Starter Strength',f"{safe_float(cc.get('starter_redraft_value')):,.0f}",'blue'),
        kpi_card(s,'Long-Term Starter Strength',f"{safe_float(cc.get('starter_dynasty_value')):,.0f}",'blue'),
        kpi_card(s,'Win-Now Readiness',f"{safe_float(cc.get('contender_score')):.3f}",'positive' if safe_float(cc.get('contender_score'))>=.6 else 'neutral'),
        kpi_card(s,'Long-Term Roster Strength',f"{safe_float(cc.get('dynasty_roster_score')):.3f}",'positive' if safe_float(cc.get('dynasty_roster_score'))>=.6 else 'neutral'),
    ]
    ct=Table([cards],colWidths=[1.86*inch]*4); ct.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),1),('RIGHTPADDING',(0,0),(-1,-1),1)])); story += [ct,Spacer(1,6)]
    needs=cc.get('biggest_position_needs') or []
    need_rows=[[P(s,'POSITION','FS_CardLabel'),P(s,'HOW URGENT','FS_CardLabel')]]+[[P(s,x.get('position'),'FS_Body'),P(s,f"{safe_float(x.get('need_score')):.3f}",'FS_Body')] for x in needs[:4]]
    need_tbl=Table(need_rows,colWidths=[1.05*inch,1.05*inch]); need_tbl.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),LIGHT_GRAY),('GRID',(0,0),(-1,-1),.35,MID_GRAY),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),5),('RIGHTPADDING',(0,0),(-1,-1),5)]))
    weights=cc.get('objective_weights') or {}
    wtxt=' | '.join(f"{k.title()} {safe_float(v)*100:.0f}%" for k,v in weights.items())
    left=[P(s,'ROSTER NEEDS','FS_Section'),need_tbl,Spacer(1,5),P(s,'WHAT MATTERS MOST FOR THIS TEAM','FS_Section'),P(s,wtxt,'FS_Body')]
    assets=cc.get('highest_break_glass_assets') or []
    asset_rows=[[P(s,'ASSET','FS_CardLabel'),P(s,'STATUS','FS_CardLabel'),P(s,'MIN. PRICE','FS_CardLabel'),P(s,'EASE','FS_CardLabel')]]
    for a in assets[:6]:
        asset_rows.append([P(s,a.get('name'),'FS_Body'),P(s,str(a.get('core_status') or '').replace('_',' '),'FS_Small'),P(s,f"{safe_float(a.get('break_glass_value')):,.0f}",'FS_Body'),P(s,f"{safe_float(a.get('liquidity_score')):.2f}",'FS_Body')])
    asset_tbl=Table(asset_rows,colWidths=[1.55*inch,1.05*inch,.8*inch,.52*inch]); asset_tbl.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),LIGHT_GRAY),('GRID',(0,0),(-1,-1),.35,MID_GRAY),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),4),('RIGHTPADDING',(0,0),(-1,-1),4)]))
    right=[P(s,'HARDEST ASSETS TO JUSTIFY MOVING','FS_Section'),asset_tbl,Spacer(1,4),P(s,'READING GUIDE','FS_Section'),P(s,"Minimum Price is the model's estimate of what it should take to justify moving an important asset. Ease shows how readily that asset could be turned into useful value in another deal. The report explains the model's existing output; it does not change the underlying grades.",'FS_Small')]
    cols=Table([[left,right]],colWidths=[2.35*inch,5.09*inch]); cols.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(0,0),0),('RIGHTPADDING',(0,0),(0,0),9),('LEFTPADDING',(1,0),(1,0),9),('RIGHTPADDING',(1,0),(1,0),0),('LINEBEFORE',(1,0),(1,0),.7,MID_GRAY)])); story.append(cols)
    doc.build(story,onFirstPage=lambda c,d: footer(c,f'{MODEL_VERSION} | Model output | Plain-English presentation'))

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--franchise-index',default='data/gm/franchise_index.json'); ap.add_argument('--user-id'); ap.add_argument('--slug'); ap.add_argument('--output',required=True); a=ap.parse_args(); render(a.franchise_index,a.user_id,a.slug,Path(a.output))
if __name__=='__main__': main()
