#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from pathlib import Path
from typing import Any, Dict
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

MODEL_VERSION='FSFFL-Trade-Decision-Report-1.1'
NAVY=colors.HexColor('#14213D'); RED=colors.HexColor('#C23B36'); GREEN=colors.HexColor('#2F7D4A'); GRAY=colors.HexColor('#5F6B76')
LIGHT_RED=colors.HexColor('#FBEDEC'); LIGHT_GREEN=colors.HexColor('#EAF5EE'); LIGHT_BLUE=colors.HexColor('#EAF2F8'); LIGHT_GRAY=colors.HexColor('#F3F5F7'); MID_GRAY=colors.HexColor('#D8DDE3'); BLACK=colors.HexColor('#1C1F23'); WHITE=colors.white

def sf(v,d=0.0):
    try:return float(v)
    except:return d

def clean(v):
    s=str(v or '').replace('—','-').replace('–','-')
    return ''.join(ch for ch in s if ord(ch)<0x10000 and not (0x2600<=ord(ch)<=0x27BF))

def verdict(a):
    return {'ACCEPT_NOW':'ACCEPT','COUNTER_CURRENT_OFFEROR':'COUNTER','SHOP_BEFORE_ACCEPTING':'SHOP FIRST','DECLINE':'DECLINE'}.get(str(a or '').upper(),clean(a).replace('_',' ').upper() or 'REVIEW')

def fmt_num(v): return f"{sf(v):+,.0f}"

def asset_driver(strat:Dict[str,Any]):
    sent=strat.get('sent') or []; rec=strat.get('received') or []
    if not sent:return None
    s=max(sent,key=lambda x:(sf(x.get('break_glass_value')),sf(x.get('liquidity_score'))))
    return {'name':clean(s.get('name')),'core':clean(s.get('core_status')).replace('_',' '),'break_glass':sf(s.get('break_glass_value')),'liquidity':sf(s.get('liquidity_score')),'received_max_bg':max([sf(x.get('break_glass_value')) for x in rec] or [0])}

def deal_breaker(report,current):
    sim=current.get('simulation') or {}; fd=sim.get('focus_delta') or {}; st=sim.get('strategic') or {}; state=clean(st.get('objective_state') or (report.get('focus_team_state') or {}).get('state'))
    parts=[]; sv=sf(st.get('strategic_value_delta')); bg=sf(st.get('break_glass_delta')); liq=sf(st.get('liquidity_value_delta')); opt=sf(st.get('optionality_value_delta')); dyn=sf(st.get('market_dynasty_delta')); wins=sf(fd.get('expected_wins'))
    if str(report.get('recommended_next_action')).upper()=='DECLINE':
        if wins>0 and sv<0: parts.append(f"The trade improves the short-term projection ({wins:+.2f} expected wins) but reduces state-aware strategic value {sv:+,.0f}; for a {state or 'non-contender'} roster, that is the wrong trade-off.")
        elif sv<0: parts.append(f"State-aware strategic value falls {sv:+,.0f} for the focal team's {state or 'current'} competitive state.")
        neg=[]
        for label,val in [('break-glass value',bg),('liquidity',liq),('dynasty market value',dyn),('optionality',opt)]:
            if val<0:neg.append((val,label))
        neg.sort()
        if neg: parts.append('The largest structural losses are '+', '.join(f"{label} {val:+,.0f}" for val,label in neg[:3])+'.')
        drv=asset_driver(st)
        if drv and drv['core']=='core high hold':
            more=f" Its break-glass value ({drv['break_glass']:,.0f}) is more than any single incoming asset ({drv['received_max_bg']:,.0f} max)." if drv['break_glass']>drv['received_max_bg'] and drv['received_max_bg']>0 else ''
            parts.append(f"{drv['name']} is the key concentration asset: {drv['core']}, liquidity {drv['liquidity']:.2f}.{more}")
    else: parts.append(f"The current structure changes expected wins {wins:+.2f} and state-aware strategic value {sv:+,.0f}.")
    return parts[:3]

def same_partner_section(report,current):
    row=report.get('best_same_partner') or None
    if not row:return None
    if set(row.get('return_assets') or [])==set(current.get('return_assets') or []):return None
    br=row.get('buyer_rationality') or {}; acceptance=br.get('heuristic_acceptance_fit'); plaus=clean(row.get('plausibility')) or 'UNRATED'; st=(row.get('simulation') or {}).get('strategic') or {}; fd=(row.get('simulation') or {}).get('focus_delta') or {}
    status=f"{clean(acceptance)} acceptance fit" if acceptance else f"{plaus} structural plausibility; buyer acceptance not fully validated"
    return {'buyer':clean(row.get('buyer_team')),'send':', '.join(clean(x) for x in row.get('outgoing_asset_names') or []),'receive':', '.join(clean(x) for x in row.get('return_asset_names') or []),'status':status,'strategic':sf(st.get('strategic_value_delta')),'dynasty':sf(st.get('market_dynasty_delta')),'wins':sf(fd.get('expected_wins'))}

def render(report:Dict[str,Any],output:Path):
    current=report.get('current_offer_evaluation') or {}; sim=current.get('simulation') or {}; before=sim.get('focus_before') or {}; after=sim.get('focus_after') or {}; delta=sim.get('focus_delta') or {}; st=sim.get('strategic') or {}; action=str(report.get('recommended_next_action') or 'REVIEW'); v=verdict(action)
    sent=', '.join(clean(x) for x in current.get('outgoing_asset_names') or []); recv=', '.join(clean(x) for x in current.get('return_asset_names') or []); buyer=clean(current.get('buyer_team')); alts=list(report.get('top_5_alternatives') or [])[:5]; same=same_partner_section(report,current)
    styles=getSampleStyleSheet(); styles.add(ParagraphStyle(name='T',parent=styles['Title'],fontName='Helvetica-Bold',fontSize=18,leading=20,textColor=NAVY,spaceAfter=2)); styles.add(ParagraphStyle(name='S',parent=styles['Normal'],fontSize=8,leading=10,textColor=GRAY)); styles.add(ParagraphStyle(name='H',parent=styles['Heading2'],fontName='Helvetica-Bold',fontSize=10.5,leading=12,textColor=NAVY,spaceBefore=4,spaceAfter=3)); styles.add(ParagraphStyle(name='B',parent=styles['BodyText'],fontSize=8.2,leading=10.3,textColor=BLACK)); styles.add(ParagraphStyle(name='Sm',parent=styles['BodyText'],fontSize=7,leading=8.6,textColor=GRAY)); styles.add(ParagraphStyle(name='V',parent=styles['Normal'],fontName='Helvetica-Bold',fontSize=14,leading=15,textColor=WHITE,alignment=1)); styles.add(ParagraphStyle(name='CardL',parent=styles['Normal'],fontName='Helvetica-Bold',fontSize=6.8,leading=8,textColor=GRAY,alignment=1)); styles.add(ParagraphStyle(name='CardV',parent=styles['Normal'],fontName='Helvetica-Bold',fontSize=10.8,leading=12,textColor=BLACK,alignment=1)); styles.add(ParagraphStyle(name='BottomLabel',parent=styles['Normal'],fontName='Helvetica-Bold',fontSize=7.0,leading=8.5,textColor=WHITE,alignment=1))
    P=lambda t,s='B':Paragraph(clean(t),styles[s])
    def card(label,value,good=True):
        t=Table([[P(label,'CardL')],[P(value,'CardV')]],colWidths=[1.22*inch],rowHeights=[.22*inch,.32*inch]);t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),LIGHT_GREEN if good else LIGHT_RED),('BOX',(0,0),(-1,-1),.5,MID_GRAY),('VALIGN',(0,0),(-1,-1),'MIDDLE')]));return t
    def footer(c,d):
        c.saveState();c.setFont('Helvetica',6.2);c.setFillColor(GRAY);c.drawString(.5*inch,.28*inch,f"{MODEL_VERSION} | {report.get('model_version','')} | decision support; acceptance fit is heuristic");c.drawRightString(8*inch,.28*inch,'FSFFL');c.restoreState()
    doc=SimpleDocTemplate(str(output),pagesize=letter,leftMargin=.48*inch,rightMargin=.48*inch,topMargin=.38*inch,bottomMargin=.42*inch);story=[P('FSFFL TRADE DECISION REPORT','T'),P(f"{buyer} offer - Send: {sent}",'S'),Spacer(1,4)]
    vc=GREEN if v=='ACCEPT' else RED if v=='DECLINE' else NAVY;vt=Table([[Paragraph(f"MODEL VERDICT:<br/>{v}",styles['V']),P(f"<b>Receive:</b> {recv}")]],colWidths=[2.0*inch,5.42*inch],rowHeights=[.58*inch]);vt.setStyle(TableStyle([('BACKGROUND',(0,0),(0,0),vc),('BACKGROUND',(1,0),(1,0),LIGHT_GRAY),('BOX',(0,0),(-1,-1),.7,MID_GRAY),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8)]));story+=[vt,Spacer(1,5)]
    cards=[card('Expected Wins',f"{sf(before.get('expected_wins')):.2f} -> {sf(after.get('expected_wins')):.2f}",sf(delta.get('expected_wins'))>=0),card('Expected PF',f"{sf(before.get('expected_points_for')):.0f} -> {sf(after.get('expected_points_for')):.0f}",sf(delta.get('expected_points_for'))>=0),card('Playoff Odds',f"{sf(before.get('playoff_probability'))*100:.1f}% -> {sf(after.get('playoff_probability'))*100:.1f}%",sf(delta.get('playoff_probability'))>=0),card('Champ Odds',f"{sf(before.get('championship_probability'))*100:.1f}% -> {sf(after.get('championship_probability'))*100:.1f}%",sf(delta.get('championship_probability'))>=0),card('Strategic Value',fmt_num(st.get('strategic_value_delta')),sf(st.get('strategic_value_delta'))>=0),card('Break-Glass',fmt_num(st.get('break_glass_delta')),sf(st.get('break_glass_delta'))>=0)]
    ct=Table([cards[:3],cards[3:]],colWidths=[2.47*inch]*3,rowHeights=[.59*inch,.59*inch]);ct.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),1),('RIGHTPADDING',(0,0),(-1,-1),1)]));story+=[ct,Spacer(1,4),P('WHY THE MODEL SAYS THIS','H')]
    for x in deal_breaker(report,current):story+=[P('• '+x),Spacer(1,2)]
    br=current.get('buyer_rationality') or {};ob=br.get('owner_behavior') or {};story+=[P(f"<b>Counterparty:</b> {clean(br.get('heuristic_acceptance_fit') or 'unrated')} acceptance fit. {clean(ob.get('reason') or br.get('reason') or '')}"),Spacer(1,4),P('SAME-PARTNER COUNTER','H')]
    if same:story+=[P(f"<b>{same['buyer']}</b> - Send {same['send']} | Receive {same['receive']}"),P(f"<b>Status:</b> {same['status']}. Focal impact: {same['wins']:+.2f} wins, dynasty {same['dynasty']:+,.0f}, state-aware strategic value {same['strategic']:+,.0f}.",'Sm')]
    else:story.append(P('No same-partner counter was identified by the sweep.'))
    header='MARKET SWEEP - NO ALTERNATIVES CLEARED FILTERS' if not alts else f"MARKET SWEEP - {len(alts)} ALTERNATIVE"+('S' if len(alts)!=1 else '')
    story+=[Spacer(1,5),P(header,'H')]
    for i,r in enumerate(alts,1):story+=[P(f"<b>{i}. {clean(r.get('buyer_team'))}</b> [{clean(r.get('report_role')).replace('_',' ').title()}; {clean(r.get('acceptance_likelihood') or 'unrated')} acceptance fit] - Send {', '.join(clean(x) for x in r.get('outgoing_asset_names') or [])}; Receive {', '.join(clean(x) for x in r.get('return_asset_names') or [])}"),Spacer(1,2)]
    if not alts:story.append(P('No league-wide alternative cleared the current market-sweep filters.'))
    if action=='DECLINE':rec='Decline the current structure. If the same-partner counter above has not cleared buyer acceptance, treat it as a negotiation probe rather than a recommendation; otherwise hold and shop selectively.'
    elif action=='COUNTER_CURRENT_OFFEROR':rec='Counter the current offeror with the best validated same-partner path before shopping elsewhere.'
    elif action=='SHOP_BEFORE_ACCEPTING':rec='Keep the offer open and shop the listed alternatives before accepting.'
    else:rec='Accept if still available; no sufficiently superior actionable path cleared the sweep.'
    bt=Table([[Paragraph('RECOMMENDED<br/>MOVE',styles['BottomLabel']),P(rec)]],colWidths=[1.35*inch,6.08*inch]);bt.setStyle(TableStyle([('BACKGROUND',(0,0),(0,0),NAVY),('BACKGROUND',(1,0),(1,0),LIGHT_BLUE),('BOX',(0,0),(-1,-1),.6,MID_GRAY),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]));story+=[Spacer(1,5),bt];doc.build(story,onFirstPage=footer,onLaterPages=footer)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--input',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();r=json.loads(Path(a.input).read_text(encoding='utf-8'));render(r,Path(a.output));print(json.dumps({'renderer_model_version':MODEL_VERSION,'pdf':a.output,'source_model_version':r.get('model_version')},indent=2))
if __name__=='__main__':main()
