#!/usr/bin/env python3
"""Render raw FSFFL market and football-intelligence movement as a one-page report."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle
from fsffl_report_style import *

MODEL_VERSION='FSFFL-Market-Movement-Report-1.1'
ASSETS=Path('data/fsffl_asset_values.json')

def load(p): return json.loads(Path(p).read_text(encoding='utf-8'))
def render(input_path,output):
    d=load(input_path); players=d.get('players') or []; s=styles()
    doc=SimpleDocTemplate(str(output),pagesize=letter,leftMargin=.42*inch,rightMargin=.42*inch,topMargin=.4*inch,bottomMargin=.44*inch)
    story=[P(s,'FSFFL MARKET MOVEMENT & INTELLIGENCE REPORT','FS_Title'),P(s,f"Market movement plus football news, role and usage adjustments | {d.get('model_version')}",'FS_Sub'),Spacer(1,5)]
    risers=sorted(players,key=lambda x:safe_float(x.get('trend_30_day')),reverse=True)[:5]
    fallers=sorted(players,key=lambda x:safe_float(x.get('trend_30_day')))[:5]
    intel_up=sorted(players,key=lambda x:safe_float((x.get('football_intelligence') or {}).get('total_adjustment')),reverse=True)[:5]
    intel_down=sorted(players,key=lambda x:safe_float((x.get('football_intelligence') or {}).get('total_adjustment')))[:5]
    cards=[kpi_card(s,'Largest Market Riser',clean(risers[0].get('name')) if risers else '-','positive',1.75*inch),kpi_card(s,'30-Day Gain',f"{safe_float(risers[0].get('trend_30_day')):+,.0f}" if risers else '-','positive',1.75*inch),kpi_card(s,'Biggest Football Boost',clean(intel_up[0].get('name')) if intel_up else '-','blue',1.75*inch),kpi_card(s,'Football Adj.',f"{safe_float((intel_up[0].get('football_intelligence') or {}).get('total_adjustment')):+.3f}" if intel_up else '-','positive',1.75*inch)]
    ct=Table([cards],colWidths=[1.86*inch]*4); ct.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),1),('RIGHTPADDING',(0,0),(-1,-1),1)])); story += [ct,Spacer(1,6)]
    def block(title,rows,mode):
        data=[[P(s,'PLAYER','FS_CardLabel'),P(s,'POS','FS_CardLabel'),P(s,'VALUE','FS_CardLabel'),P(s,'OWNER','FS_CardLabel')]]
        for x in rows:
            val=safe_float(x.get('trend_30_day')) if mode=='trend' else safe_float((x.get('football_intelligence') or {}).get('total_adjustment'))
            txt=f'{val:+,.0f}' if mode=='trend' else f'{val:+.3f}'
            data.append([P(s,x.get('name'),'FS_Body'),P(s,x.get('position'),'FS_Body'),P(s,txt,'FS_Body'),P(s,x.get('current_owner_team') or 'FA','FS_Small')])
        t=Table(data,colWidths=[1.35*inch,.42*inch,.65*inch,1.12*inch]); t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),LIGHT_GRAY),('GRID',(0,0),(-1,-1),.35,MID_GRAY),('LEFTPADDING',(0,0),(-1,-1),4),('RIGHTPADDING',(0,0),(-1,-1),4)])); return [P(s,title,'FS_Section'),t]
    left=[]; left+=block('TOP 30-DAY MARKET RISERS',risers,'trend'); left += [Spacer(1,5)]; left+=block('TOP 30-DAY MARKET FALLERS',fallers,'trend')
    right=[]; right+=block('BIGGEST FOOTBALL-SITUATION BOOSTS',intel_up,'intel'); right += [Spacer(1,5)]; right+=block('BIGGEST FOOTBALL-SITUATION DOWNGRADES',intel_down,'intel'); right += [Spacer(1,5),P(s,'READING GUIDE','FS_Section'),P(s,'These lists show which players moved most in the market and which players were helped or hurt most by recent football information such as role, usage, injuries and news. They are signals, not automatic buy/sell labels.','FS_Small')]
    cols=Table([[left,right]],colWidths=[3.62*inch,3.82*inch]); cols.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('RIGHTPADDING',(0,0),(0,0),9),('LEFTPADDING',(1,0),(1,0),9),('LINEBEFORE',(1,0),(1,0),.7,MID_GRAY),('LEFTPADDING',(0,0),(0,0),0),('RIGHTPADDING',(1,0),(1,0),0)])); story.append(cols)
    doc.build(story,onFirstPage=lambda c,d: footer(c,f'{MODEL_VERSION} | Market + football information | Plain-English presentation'))
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--input',default=str(ASSETS)); ap.add_argument('--output',required=True); a=ap.parse_args(); render(a.input,Path(a.output))
if __name__=='__main__': main()
