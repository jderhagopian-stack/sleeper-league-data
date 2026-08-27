#!/usr/bin/env python3
"""Render one player from raw FSFFL asset / GM data as a one-page dossier."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle
from fsffl_report_style import *

MODEL_VERSION='FSFFL-Player-Dossier-Report-1.1'
ASSETS=Path('data/fsffl_asset_values.json'); INDEX=Path('data/gm/franchise_index.json')

def load(p): return json.loads(Path(p).read_text(encoding='utf-8'))
def player_row(pid):
    d=load(ASSETS)
    for x in d.get('players') or []:
        if str(x.get('player_id'))==str(pid): return x
    raise ValueError(f'Player {pid} not found')
def strategic(pid, owner):
    if not owner or not INDEX.exists(): return {}
    idx=load(INDEX)
    for t in idx.get('teams') or []:
        if str(t.get('user_id'))==str(owner):
            p=(t.get('paths') or {}).get('strategic_asset_profiles')
            if not p or not Path(p).exists(): return {}
            d=load(p)
            for a in d.get('assets') or []:
                if str(a.get('player_id'))==str(pid) or str(a.get('asset_id'))==f'player:{pid}': return a
    return {}
def render(pid,output):
    x=player_row(pid); owner=str(x.get('current_owner_user_id') or ''); g=strategic(pid,owner); fi=x.get('football_intelligence') or {}; s=styles()
    doc=SimpleDocTemplate(str(output),pagesize=letter,leftMargin=.48*inch,rightMargin=.48*inch,topMargin=.42*inch,bottomMargin=.44*inch)
    title=f"{x.get('name')} - FSFFL PLAYER DOSSIER"; sub=f"{x.get('position')} | {x.get('nfl_team')} | Age {x.get('age')} | Owner: {x.get('current_owner_team') or 'Free Agent'}"
    story=[P(s,title,'FS_Title'),P(s,sub,'FS_Sub'),Spacer(1,5)]
    trend=safe_float(x.get('trend_30_day')); adj=safe_float(fi.get('total_adjustment'))
    cards=[kpi_card(s,'Dynasty Value',f"{safe_float(x.get('market_dynasty')):,.0f}",'blue'),kpi_card(s,'Redraft Value',f"{safe_float(x.get('market_redraft')):,.0f}",'blue'),kpi_card(s,'Market Rank',f"#{x.get('market_rank') or '-'}",'neutral'),kpi_card(s,'Pos. Rank',f"#{x.get('position_rank') or '-'}",'neutral'),kpi_card(s,'30-Day Trend',f"{trend:+,.0f}",'positive' if trend>0 else 'negative' if trend<0 else 'neutral'),kpi_card(s,'Football Boost / Penalty',f"{adj:+.3f}",'positive' if adj>0 else 'negative' if adj<0 else 'neutral')]
    ct=Table([cards[:3],cards[3:]],colWidths=[2.47*inch]*3); ct.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),1),('RIGHTPADDING',(0,0),(-1,-1),1)])); story += [ct,Spacer(1,6)]
    injury=fi.get('injury') or {}; momentum=fi.get('market_momentum') or {}; usage=fi.get('usage_and_snaps') or {}; news=fi.get('manual_news_signal') or {}
    signal_rows=[[P(s,'SIGNAL','FS_CardLabel'),P(s,'MODEL READING','FS_CardLabel')],
                 [P(s,'Injury','FS_Body'),P(s,str(injury.get('status') or x.get('injury_status') or 'None'),'FS_Body')],
                 [P(s,'Market movement','FS_Body'),P(s,f"signal {safe_float(momentum.get('signal')):+.3f}",'FS_Body')],
                 [P(s,'Playing time / usage','FS_Body'),P(s,f"adjustment {safe_float(usage.get('adjustment')):+.3f}",'FS_Body')],
                 [P(s,'News / role information','FS_Body'),P(s,f"signal {safe_float(news.get('signal')):+.3f}; conf {safe_float(news.get('confidence')):.2f}",'FS_Body')]]
    sig=Table(signal_rows,colWidths=[1.4*inch,1.5*inch]); sig.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),LIGHT_GRAY),('GRID',(0,0),(-1,-1),.35,MID_GRAY),('LEFTPADDING',(0,0),(-1,-1),5),('RIGHTPADDING',(0,0),(-1,-1),5)]))
    left=[P(s,'RECENT FOOTBALL CONTEXT','FS_Section'),sig]
    if news.get('reason'): left += [Spacer(1,4),P(s,'NEWS / ROLE NOTE','FS_Section'),P(s,news.get('reason'),'FS_Body')]
    if g:
        f=g.get('future_distribution') or {}; sc=g.get('scarcity') or {}
        rows=[[P(s,'WHAT IT MEANS','FS_CardLabel'),P(s,'VALUE','FS_CardLabel')],
              [P(s,'Importance to roster','FS_Body'),P(s,str(g.get('core_status') or '').replace('_',' '),'FS_Body')],
              [P(s,'Value to this team','FS_Body'),P(s,f"{safe_float(g.get('base_franchise_value')):,.0f}",'FS_Body')],
              [P(s,'Minimum price to move','FS_Body'),P(s,f"{safe_float(g.get('break_glass_value')):,.0f}",'FS_Body')],
              [P(s,'Ease of trading','FS_Body'),P(s,f"{safe_float(g.get('liquidity_score')):.3f}",'FS_Body')],
              [P(s,'Positional scarcity','FS_Body'),P(s,f"{safe_float(sc.get('scarcity_score')):.3f}",'FS_Body')],
              [P(s,'Upside room','FS_Body'),P(s,f"{safe_float(f.get('upside_optionality')):.3f}",'FS_Body')],
              [P(s,'Downside risk','FS_Body'),P(s,f"{safe_float(f.get('downside_risk')):.3f}",'FS_Body')]]
        gt=Table(rows,colWidths=[1.55*inch,1.35*inch]); gt.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),LIGHT_GRAY),('GRID',(0,0),(-1,-1),.35,MID_GRAY),('LEFTPADDING',(0,0),(-1,-1),5),('RIGHTPADDING',(0,0),(-1,-1),5)]))
        right=[P(s,'PLAYER VALUE PROFILE','FS_Section'),gt,Spacer(1,4),P(s,'READING GUIDE','FS_Section'),P(s,"This page summarizes the model's existing player value, recent football context and roster-specific value in everyday fantasy-football terms. It does not create a new grade or change the underlying model.",'FS_Small')]
    else:
        right=[P(s,'PLAYER VALUE PROFILE','FS_Section'),P(s,'No roster-specific value profile is available for this player.','FS_Body'),Spacer(1,4),P(s,'READING GUIDE','FS_Section'),P(s,"This page summarizes the player's existing market value and recent football context without changing the underlying model.",'FS_Small')]
    cols=Table([[left,right]],colWidths=[3.45*inch,3.99*inch]); cols.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('RIGHTPADDING',(0,0),(0,0),9),('LEFTPADDING',(1,0),(1,0),9),('LINEBEFORE',(1,0),(1,0),.7,MID_GRAY),('LEFTPADDING',(0,0),(0,0),0),('RIGHTPADDING',(1,0),(1,0),0)])); story.append(cols)
    doc.build(story,onFirstPage=lambda c,d: footer(c,f'{MODEL_VERSION} | Model output | Plain-English presentation'))
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--player-id',required=True); ap.add_argument('--output',required=True); a=ap.parse_args(); render(a.player_id,Path(a.output))
if __name__=='__main__': main()
