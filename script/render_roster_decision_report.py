#!/usr/bin/env python3
"""Render a generic Roster Decision Lab JSON result as a one-page PDF."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle
from fsffl_report_style import *
from report_language import probability_change, value_change

MODEL_VERSION='FSFFL-Roster-Decision-Report-1.2'
ASSET_VALUES=Path('data/fsffl_asset_values.json')

def load(p): return json.loads(Path(p).read_text(encoding='utf-8'))
def pct(x): return f'{safe_float(x)*100:+.1f} pts'
def arr(b,a,percent=False): return f'{safe_float(b)*100:.1f}% -> {safe_float(a)*100:.1f}%' if percent else f'{safe_float(b):.2f} -> {safe_float(a):.2f}'

def asset_names():
    out={}
    if not ASSET_VALUES.exists(): return out
    d=load(ASSET_VALUES)
    for x in d.get('players') or []:
        pid=str(x.get('player_id'))
        if pid: out[pid]=x.get('name') or pid; out[f'player:{pid}']=x.get('name') or pid
    for x in d.get('picks') or []:
        aid=str(x.get('asset_id') or '')
        if aid: out[aid]=x.get('name') or aid
    return out

def action_summary(actions,names):
    parts=[]
    for a in actions or []:
        typ=str(a.get('type') or '').replace('_',' ').title()
        vals=[]
        vals += [names.get(str(x),str(x)) for x in a.get('players') or []]
        vals += [names.get(str(x),str(x)) for x in a.get('picks') or []]
        detail=', '.join(vals) or 'roster change'
        direction=''
        if a.get('from_user_id') or a.get('to_user_id'):
            direction=f" ({a.get('from_user_id','?')} -> {a.get('to_user_id','?')})"
        parts.append(f'{typ}{direction}: {detail}')
    return ' | '.join(parts)[:420]

def roster_narrative(before,after,delta,strat,rec,unresolved):
    if unresolved:
        return "The model can measure the roster and season effects, but it cannot make a confident final recommendation until the team's competitive direction is resolved."
    pieces=[
        probability_change("Playoff odds",before.get("playoff_probability"),after.get("playoff_probability")),
        probability_change("Championship odds",before.get("championship_probability"),after.get("championship_probability")),
        value_change("Long-term trade value",strat.get("market_dynasty_delta")),
        value_change("Value to this team",strat.get("base_franchise_delta")),
    ]
    wins=safe_float(delta.get("expected_wins"))
    direction="adds" if wins>0 else "costs" if wins<0 else "does not change"
    pieces.insert(0,f"The move {direction} {abs(wins):.2f} expected wins.")
    return " ".join(pieces)

def render(input_path,output):
    d=load(input_path); uid=str(d.get('focus_user_id')); cmp=(d.get('team_comparisons') or {}).get(uid) or {}
    before=cmp.get('before') or {}; after=cmp.get('after') or {}; delta=cmp.get('delta') or {}; strat=cmp.get('strategic') or {}; rec=d.get('recommendation') or {}; ext=d.get('competitive_externality') or {}
    s=styles(); doc=SimpleDocTemplate(str(output),pagesize=letter,leftMargin=.48*inch,rightMargin=.48*inch,topMargin=.42*inch,bottomMargin=.44*inch)
    story=[P(s,'FSFFL ROSTER DECISION REPORT','FS_Title'),P(s,f"{cmp.get('team_name')} | {d.get('description') or d.get('scenario_id')} | {d.get('model_version')}",'FS_Sub'),Spacer(1,5)]
    raw_band=str(rec.get('band') or 'needs_context').replace('_',' ').upper(); team_state=str(rec.get('team_state') or 'unknown')
    unresolved=team_state.lower()=='unknown'
    band='UNRESOLVED - TEAM STATE REQUIRED' if unresolved else raw_band
    tone=GOLD if unresolved else GREEN if 'ACCEPT' in band else RED if 'REJECT' in band else NAVY
    band_note=f"<b>{band}</b> &nbsp;&nbsp; Team state: {team_state.replace('_',' ').title()}"
    if unresolved: band_note += f" &nbsp;&nbsp; Raw rule band: {raw_band}"
    banner=Table([[P(s,'MODEL VERDICT','FS_WhiteLabel'),P(s,band_note,'FS_Body')]],colWidths=[1.4*inch,6.04*inch]); banner.setStyle(TableStyle([('BACKGROUND',(0,0),(0,0),tone),('BACKGROUND',(1,0),(1,0),LIGHT_GOLD if unresolved else LIGHT_GRAY),('BOX',(0,0),(-1,-1),.7,MID_GRAY),('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6)])); story += [banner,Spacer(1,5),P(s,f'<b>Scenario:</b> {action_summary(d.get("actions"),asset_names())}','FS_Body'),Spacer(1,5)]
    cards=[
        kpi_card(s,'Expected Wins',arr(before.get('expected_wins'),after.get('expected_wins')),'positive' if safe_float(delta.get('expected_wins'))>=0 else 'negative'),
        kpi_card(s,'Expected Points',f"{safe_float(before.get('expected_points_for')):,.0f} -> {safe_float(after.get('expected_points_for')):,.0f}",'positive' if safe_float(delta.get('expected_points_for'))>=0 else 'negative'),
        kpi_card(s,'Playoff Odds',arr(before.get('playoff_probability'),after.get('playoff_probability'),True),'positive' if safe_float(delta.get('playoff_probability'))>=0 else 'negative'),
        kpi_card(s,'First-Round Bye Odds',arr(before.get('bye_probability'),after.get('bye_probability'),True),'positive' if safe_float(delta.get('bye_probability'))>=0 else 'negative'),
        kpi_card(s,'Championship Odds',arr(before.get('championship_probability'),after.get('championship_probability'),True),'positive' if safe_float(delta.get('championship_probability'))>=0 else 'negative'),
        kpi_card(s,'Division-Win Odds',arr(before.get('division_probability'),after.get('division_probability'),True),'positive' if safe_float(delta.get('division_probability'))>=0 else 'negative'),
    ]; ct=Table([cards[:3],cards[3:]],colWidths=[2.47*inch]*3); ct.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),1),('RIGHTPADDING',(0,0),(-1,-1),1)])); story += [ct,Spacer(1,6)]
    strategic_rows=[[P(s,'STRATEGIC METRIC','FS_CardLabel'),P(s,'DELTA','FS_CardLabel')]]
    for label,key in [('Long-Term Trade Value','market_dynasty_delta'),('2026 Playing Value','market_redraft_delta'),('Value to This Team','base_franchise_delta'),('Resale Safety','break_glass_delta')]: strategic_rows.append([P(s,label,'FS_Body'),P(s,f"{safe_float(strat.get(key)):+,.0f}",'FS_Body')])
    st=Table(strategic_rows,colWidths=[1.45*inch,1.1*inch]); st.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),LIGHT_GRAY),('GRID',(0,0),(-1,-1),.35,MID_GRAY),('LEFTPADDING',(0,0),(-1,-1),5),('RIGHTPADDING',(0,0),(-1,-1),5)]))
    left=[P(s,'VALUE CHANGES','FS_Section'),st,Spacer(1,5),P(s,'EFFECT ON THE REST OF THE LEAGUE','FS_Section'),P(s,f"Your championship-odds change {pct(ext.get('focus_championship_probability_delta'))}; combined championship-odds gain for opponents {pct(ext.get('opponent_positive_championship_probability_delta_sum'))}; net effect after accounting for opponents {pct(ext.get('net_title_equity_swing_against_focus'))}.",'FS_Body')]
    touched=d.get('team_comparisons') or {}; rows=[[P(s,'TEAM','FS_CardLabel'),P(s,'WINS Δ','FS_CardLabel'),P(s,'PLAYOFF Δ','FS_CardLabel'),P(s,'TITLE Δ','FS_CardLabel')]]
    for _,x in touched.items():
        dd=x.get('delta') or {}; rows.append([P(s,x.get('team_name'),'FS_Body'),P(s,f"{safe_float(dd.get('expected_wins')):+.2f}",'FS_Body'),P(s,pct(dd.get('playoff_probability')),'FS_Body'),P(s,pct(dd.get('championship_probability')),'FS_Body')])
    tt=Table(rows,colWidths=[2.1*inch,.65*inch,.78*inch,.7*inch]); tt.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),LIGHT_GRAY),('GRID',(0,0),(-1,-1),.35,MID_GRAY),('LEFTPADDING',(0,0),(-1,-1),4),('RIGHTPADDING',(0,0),(-1,-1),4)]))
    guide="This page explains the model's existing comparison in everyday fantasy-football language. It does not change the simulation or the underlying values."
    if unresolved: guide='The model could not confidently determine whether this team is contending or rebuilding, so it does not present the recommendation as final. ' + guide
    right=[P(s,'AFFECTED TEAMS','FS_Section'),tt,Spacer(1,5),P(s,'READING GUIDE','FS_Section'),P(s,guide,'FS_Small')]
    cols=Table([[left,right]],colWidths=[2.85*inch,4.59*inch]); cols.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('RIGHTPADDING',(0,0),(0,0),9),('LEFTPADDING',(1,0),(1,0),9),('LINEBEFORE',(1,0),(1,0),.7,MID_GRAY),('LEFTPADDING',(0,0),(0,0),0),('RIGHTPADDING',(1,0),(1,0),0)])); story.append(cols)
    doc.build(story,onFirstPage=lambda c,x: footer(c,f'{MODEL_VERSION} | Model output | Plain-English presentation'))

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--input',required=True); ap.add_argument('--output',required=True); a=ap.parse_args(); render(a.input,Path(a.output))
if __name__=='__main__': main()
