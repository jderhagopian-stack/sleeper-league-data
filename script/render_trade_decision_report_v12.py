#!/usr/bin/env python3
"""Render Market Sweep output as a one-page, natural-language trade report.

Presentation only: no scoring or simulation occurs here. Narrative statements are
constructed deterministically from already-computed model fields so the prose is
readable while remaining auditable and reproducible.
"""
from __future__ import annotations
import argparse, json
from pathlib import Path
from typing import Any, Dict
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

MODEL_VERSION='FSFFL-Trade-Decision-Report-1.2'
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

def fmt_num(v):return f"{sf(v):+,.0f}"

def fmt_pct(v):return f"{sf(v)*100:+.1f} points"

def join_names(items):
    xs=[clean(x) for x in items or [] if clean(x)]
    if not xs:return 'the package'
    if len(xs)==1:return xs[0]
    if len(xs)==2:return f"{xs[0]} and {xs[1]}"
    return ', '.join(xs[:-1])+f", and {xs[-1]}"

def asset_driver(st:Dict[str,Any]):
    sent=st.get('sent') or []; rec=st.get('received') or []
    if not sent:return None
    s=max(sent,key=lambda x:(sf(x.get('break_glass_value')),sf(x.get('liquidity_score')),sf(x.get('dynasty_value'))))
    return {'name':clean(s.get('name')),'core':clean(s.get('core_status')).replace('_',' '),'break_glass':sf(s.get('break_glass_value')),'liquidity':sf(s.get('liquidity_score')),'dynasty':sf(s.get('dynasty_value')),'received_max_bg':max([sf(x.get('break_glass_value')) for x in rec] or [0])}

def natural_narrative(report:Dict[str,Any], current:Dict[str,Any])->str:
    """Create a short GM-style explanation from model outputs only."""
    sim=current.get('simulation') or {}; fd=sim.get('focus_delta') or {}; st=sim.get('strategic') or {}
    action=str(report.get('recommended_next_action') or '').upper(); state=clean(st.get('objective_state') or (report.get('focus_team_state') or {}).get('state') or 'current')
    wins=sf(fd.get('expected_wins')); pf=sf(fd.get('expected_points_for')); playoffs=sf(fd.get('playoff_probability')); champ=sf(fd.get('championship_probability'))
    sv=sf(st.get('strategic_value_delta')); bg=sf(st.get('break_glass_delta')); liq=sf(st.get('liquidity_value_delta')); opt=sf(st.get('optionality_value_delta')); dyn=sf(st.get('market_dynasty_delta'))
    sent=join_names(current.get('outgoing_asset_names')); rec=join_names(current.get('return_asset_names'))
    drv=asset_driver(st)

    if action=='DECLINE':
        if wins>0 and sv<0:
            p1=f"This deal makes {state} team stronger in the short term, but not in the way this roster needs most. Moving {sent} for {rec} adds {wins:+.2f} expected wins and {pf:+.0f} projected points, yet the state-aware franchise score falls {abs(sv):,.0f}. In other words, the model sees real 2026 improvement, but not enough to justify what is being surrendered for this team's competitive timeline."
        elif sv<0:
            p1=f"The problem is not that the return is worthless; it is that the exchange is a poor fit for this roster's {state} state. The state-aware franchise score falls {abs(sv):,.0f}, so the package gives up more strategically useful value than it receives."
        else:
            p1=f"The offer does not create enough advantage for the focal team to justify moving {sent}. The current simulation and franchise-value layers do not produce a strong enough positive case to accept as structured."
        losses=[]
        for label,val in [('break-glass value',bg),('liquidity',liq),('dynasty-market value',dyn),('optionality',opt)]:
            if val<0:losses.append((abs(val),label))
        losses.sort(reverse=True)
        if losses:
            lead=', '.join(f"{label} ({amt:,.0f})" for amt,label in losses[:2])
            p2=f"The biggest structural losses are {lead}."
        else:p2='The issue is primarily fit and value concentration rather than a single headline metric.'
        if drv and drv['core']=='core high hold':
            p2+=f" {drv['name']} is the piece that most clearly breaks the deal. The model classifies {drv['name']} as a core high-hold asset with {drv['liquidity']:.2f} liquidity and {drv['break_glass']:,.0f} of break-glass value"
            if drv['received_max_bg']>0 and drv['break_glass']>drv['received_max_bg']:
                p2+=f", well above the strongest single incoming asset at {drv['received_max_bg']:,.0f}"
            p2+='; matching aggregate value across several pieces does not fully replace that concentration of quality.'
        if champ>0 or playoffs>0:
            p3=f"The competitive gain is still modest in context: playoff odds move {fmt_pct(playoffs)} and championship odds {fmt_pct(champ)}. That is not enough for the model to trade away the stronger long-term asset profile."
        else:
            p3="That leaves the focal team better served by holding the premium asset or demanding a return that creates a clear surplus rather than merely matching calculator value."
        return ' '.join([p1,p2,p3])

    if action=='COUNTER_CURRENT_OFFEROR':
        return f"The current offer is close enough to keep the conversation alive, but it does not maximize the focal team's leverage. The model prefers changing the structure with this same manager rather than accepting {rec} as offered. The present deal moves expected wins {wins:+.2f} and strategic value {sv:+,.0f}; the counter section below shows the stronger same-partner construction identified by the sweep."
    if action=='SHOP_BEFORE_ACCEPTING':
        return f"The offer is viable, but the market sweep found a better use of the assets before committing. The current structure moves expected wins {wins:+.2f} and strategic value {sv:+,.0f}; because another realistic path scores better, the model recommends preserving the offer while testing the market first."
    return f"The offer fits the focal team's current competitive state and clears the model's strategic guardrails. It changes expected wins {wins:+.2f}, projected points {pf:+.0f}, and state-aware strategic value {sv:+,.0f}. The market sweep did not find a sufficiently superior actionable alternative to justify passing on it."

def counterparty_narrative(current:Dict[str,Any])->str:
    br=current.get('buyer_rationality') or {}; ob=br.get('owner_behavior') or {}; fit=clean(br.get('heuristic_acceptance_fit') or 'unrated')
    reason=clean(ob.get('reason') or br.get('reason') or '')
    if reason:return f"The other side grades as {fit} acceptance fit. {reason}"
    return f"The other side grades as {fit} acceptance fit under the model's bilateral and behavioral checks."

def same_partner_section(report,current):
    row=report.get('best_same_partner') or None
    if not row:return None
    if set(row.get('return_assets') or [])==set(current.get('return_assets') or []):return None
    br=row.get('buyer_rationality') or {}; acceptance=br.get('heuristic_acceptance_fit'); plaus=clean(row.get('plausibility')) or 'UNRATED'; st=(row.get('simulation') or {}).get('strategic') or {}; fd=(row.get('simulation') or {}).get('focus_delta') or {}
    status=f"{clean(acceptance)} acceptance fit" if acceptance else f"{plaus} structural plausibility; buyer acceptance not fully validated"
    return {'buyer':clean(row.get('buyer_team')),'send':join_names(row.get('outgoing_asset_names')),'receive':join_names(row.get('return_asset_names')),'status':status,'strategic':sf(st.get('strategic_value_delta')),'dynasty':sf(st.get('market_dynasty_delta')),'wins':sf(fd.get('expected_wins'))}

def render(report:Dict[str,Any],output:Path):
    current=report.get('current_offer_evaluation') or {}; sim=current.get('simulation') or {}; before=sim.get('focus_before') or {}; after=sim.get('focus_after') or {}; delta=sim.get('focus_delta') or {}; st=sim.get('strategic') or {}; action=str(report.get('recommended_next_action') or 'REVIEW'); v=verdict(action)
    sent=join_names(current.get('outgoing_asset_names')); recv=join_names(current.get('return_asset_names')); buyer=clean(current.get('buyer_team')); alts=list(report.get('top_5_alternatives') or [])[:5]; same=same_partner_section(report,current)
    styles=getSampleStyleSheet();styles.add(ParagraphStyle(name='T',parent=styles['Title'],fontName='Helvetica-Bold',fontSize=18,leading=20,textColor=NAVY,spaceAfter=2));styles.add(ParagraphStyle(name='S',parent=styles['Normal'],fontSize=8,leading=10,textColor=GRAY));styles.add(ParagraphStyle(name='H',parent=styles['Heading2'],fontName='Helvetica-Bold',fontSize=10.4,leading=12,textColor=NAVY,spaceBefore=4,spaceAfter=3));styles.add(ParagraphStyle(name='B',parent=styles['BodyText'],fontSize=8.15,leading=10.4,textColor=BLACK));styles.add(ParagraphStyle(name='Sm',parent=styles['BodyText'],fontSize=7,leading=8.6,textColor=GRAY));styles.add(ParagraphStyle(name='V',parent=styles['Normal'],fontName='Helvetica-Bold',fontSize=14,leading=15,textColor=WHITE,alignment=1));styles.add(ParagraphStyle(name='CardL',parent=styles['Normal'],fontName='Helvetica-Bold',fontSize=6.8,leading=8,textColor=GRAY,alignment=1));styles.add(ParagraphStyle(name='CardV',parent=styles['Normal'],fontName='Helvetica-Bold',fontSize=10.8,leading=12,textColor=BLACK,alignment=1));styles.add(ParagraphStyle(name='BottomLabel',parent=styles['Normal'],fontName='Helvetica-Bold',fontSize=7.0,leading=8.5,textColor=WHITE,alignment=1))
    P=lambda t,s='B':Paragraph(clean(t),styles[s])
    def card(label,value,good=True):
        t=Table([[P(label,'CardL')],[P(value,'CardV')]],colWidths=[1.22*inch],rowHeights=[.22*inch,.32*inch]);t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),LIGHT_GREEN if good else LIGHT_RED),('BOX',(0,0),(-1,-1),.5,MID_GRAY),('VALIGN',(0,0),(-1,-1),'MIDDLE')]));return t
    def footer(c,d):
        c.saveState();c.setFont('Helvetica',6.2);c.setFillColor(GRAY);c.drawString(.5*inch,.28*inch,f"{MODEL_VERSION} | {report.get('model_version','')} | deterministic narrative from model outputs");c.drawRightString(8*inch,.28*inch,'FSFFL');c.restoreState()
    doc=SimpleDocTemplate(str(output),pagesize=letter,leftMargin=.48*inch,rightMargin=.48*inch,topMargin=.38*inch,bottomMargin=.42*inch);story=[P('FSFFL TRADE DECISION REPORT','T'),P(f"{buyer} offer - Send: {sent}",'S'),Spacer(1,4)]
    vc=GREEN if v=='ACCEPT' else RED if v=='DECLINE' else NAVY;vt=Table([[Paragraph(f"MODEL VERDICT:<br/>{v}",styles['V']),P(f"<b>Receive:</b> {recv}")]],colWidths=[2.0*inch,5.42*inch],rowHeights=[.58*inch]);vt.setStyle(TableStyle([('BACKGROUND',(0,0),(0,0),vc),('BACKGROUND',(1,0),(1,0),LIGHT_GRAY),('BOX',(0,0),(-1,-1),.7,MID_GRAY),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8)]));story+=[vt,Spacer(1,5)]
    cards=[card('Expected Wins',f"{sf(before.get('expected_wins')):.2f} -> {sf(after.get('expected_wins')):.2f}",sf(delta.get('expected_wins'))>=0),card('Expected PF',f"{sf(before.get('expected_points_for')):.0f} -> {sf(after.get('expected_points_for')):.0f}",sf(delta.get('expected_points_for'))>=0),card('Playoff Odds',f"{sf(before.get('playoff_probability'))*100:.1f}% -> {sf(after.get('playoff_probability'))*100:.1f}%",sf(delta.get('playoff_probability'))>=0),card('Champ Odds',f"{sf(before.get('championship_probability'))*100:.1f}% -> {sf(after.get('championship_probability'))*100:.1f}%",sf(delta.get('championship_probability'))>=0),card('Strategic Value',fmt_num(st.get('strategic_value_delta')),sf(st.get('strategic_value_delta'))>=0),card('Break-Glass',fmt_num(st.get('break_glass_delta')),sf(st.get('break_glass_delta'))>=0)]
    ct=Table([cards[:3],cards[3:]],colWidths=[2.47*inch]*3,rowHeights=[.59*inch,.59*inch]);ct.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),1),('RIGHTPADDING',(0,0),(-1,-1),1)]));story+=[ct,Spacer(1,4),P('MODEL READ','H'),P(natural_narrative(report,current)),Spacer(1,3),P(counterparty_narrative(current),'Sm'),Spacer(1,4),P('SAME-PARTNER NEGOTIATION','H')]
    if same:
        story+=[P(f"A better structure with {same['buyer']} is to send {same['send']} and receive {same['receive']}."),P(f"This candidate adds {same['wins']:+.2f} expected wins, {same['dynasty']:+,.0f} dynasty value, and {same['strategic']:+,.0f} state-aware strategic value. Its current status is {same['status']}, so it should be treated as {'an actionable counter' if 'HIGH acceptance' in same['status'] or 'MEDIUM acceptance' in same['status'] else 'a negotiation probe, not a validated recommendation'}." ,'Sm')]
    else:story.append(P('The sweep did not identify a better construction with this same manager that was distinct from the current offer.'))
    header='MARKET SWEEP - NO ALTERNATIVES CLEARED FILTERS' if not alts else f"MARKET SWEEP - {len(alts)} ALTERNATIVE"+('S' if len(alts)!=1 else '')
    story+=[Spacer(1,5),P(header,'H')]
    for i,r in enumerate(alts,1):
        story+=[P(f"<b>{i}. {clean(r.get('buyer_team'))}</b> [{clean(r.get('report_role')).replace('_',' ').title()}; {clean(r.get('acceptance_likelihood') or 'unrated')} acceptance fit] - Send {join_names(r.get('outgoing_asset_names'))}; receive {join_names(r.get('return_asset_names'))}."),Spacer(1,2)]
    if not alts:story.append(P('No league-wide alternative cleared the current market-sweep filters.'))
    if action=='DECLINE':rec='Decline the current structure. Use any same-partner candidate above only at its stated confidence level; otherwise hold the premium asset and shop selectively rather than forcing a deal.'
    elif action=='COUNTER_CURRENT_OFFEROR':rec='Counter the current offeror with the best validated same-partner path before shopping elsewhere.'
    elif action=='SHOP_BEFORE_ACCEPTING':rec='Keep the offer open and shop the listed alternatives before accepting.'
    else:rec='Accept if still available; no sufficiently superior actionable path cleared the sweep.'
    bt=Table([[Paragraph('RECOMMENDED<br/>MOVE',styles['BottomLabel']),P(rec)]],colWidths=[1.35*inch,6.08*inch]);bt.setStyle(TableStyle([('BACKGROUND',(0,0),(0,0),NAVY),('BACKGROUND',(1,0),(1,0),LIGHT_BLUE),('BOX',(0,0),(-1,-1),.6,MID_GRAY),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]));story+=[Spacer(1,5),bt];doc.build(story,onFirstPage=footer,onLaterPages=footer)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--input',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();r=json.loads(Path(a.input).read_text(encoding='utf-8'));render(r,Path(a.output));print(json.dumps({'renderer_model_version':MODEL_VERSION,'pdf':a.output,'source_model_version':r.get('model_version')},indent=2))
if __name__=='__main__':main()
