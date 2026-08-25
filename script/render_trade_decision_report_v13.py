#!/usr/bin/env python3
"""Trade Decision Report 1.3: natural-language verdict + 0-2 counters + 0-5 market alternatives."""
from __future__ import annotations
import argparse,importlib.util,json
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle,getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,PageBreak
BASE=Path(__file__).resolve().parent/'render_trade_decision_report_v12.py'
MODEL_VERSION='FSFFL-Trade-Decision-Report-1.3'
NAVY=colors.HexColor('#14213D');RED=colors.HexColor('#C23B36');GREEN=colors.HexColor('#2F7D4A');GRAY=colors.HexColor('#5F6B76');LIGHT=colors.HexColor('#F3F5F7');BLUE=colors.HexColor('#EAF2F8');MID=colors.HexColor('#D8DDE3');WHITE=colors.white

def load():
 s=importlib.util.spec_from_file_location('trade_report_v12_base',BASE);m=importlib.util.module_from_spec(s);assert s.loader;s.loader.exec_module(m);return m

def sf(v,d=0):
 try:return float(v)
 except:return d

def clean(v):return str(v or '').replace('—','-').replace('–','-')
def names(xs):
 x=[clean(i) for i in xs or [] if i]
 return ', '.join(x) if x else 'the package'
def verdict(a):return {'ACCEPT_NOW':'ACCEPT','COUNTER_CURRENT_OFFEROR':'COUNTER','SHOP_BEFORE_ACCEPTING':'SHOP FIRST','DECLINE':'DECLINE'}.get(str(a or ''),str(a or 'REVIEW').replace('_',' '))
def counter_text(row,i):
 sim=row.get('simulation') or {};d=sim.get('focus_delta') or {};st=sim.get('strategic') or {};status=clean(row.get('counter_confidence_note') or 'acceptance not fully validated')
 return f"<b>{i}. Send {names(row.get('outgoing_asset_names'))}; receive {names(row.get('return_asset_names'))}.</b> This version changes expected wins {sf(d.get('expected_wins')):+.2f}, dynasty value {sf(st.get('market_dynasty_delta')):+,.0f}, and state-aware strategic value {sf(st.get('strategic_value_delta')):+,.0f}. <font color='#5F6B76'>{status}.</font>"
def market_text(row,i):
 sim=row.get('simulation') or {};d=sim.get('focus_delta') or {};st=sim.get('strategic') or {};fit=clean(row.get('acceptance_likelihood') or 'unrated')
 return f"<b>{i}. {clean(row.get('buyer_team'))}</b> — send {names(row.get('outgoing_asset_names'))}; receive {names(row.get('return_asset_names'))}. Expected wins {sf(d.get('expected_wins')):+.2f}; strategic value {sf(st.get('strategic_value_delta')):+,.0f}. <font color='#5F6B76'>{fit} acceptance fit.</font>"
def sequence(report):
 action=str(report.get('recommended_next_action') or '');cs=report.get('suggested_counteroffers') or [];ms=report.get('market_sweep_alternatives') or []
 if action=='DECLINE':
  if cs:return 'Decline the current offer and start with Counter 1. If that structure is rejected, use Counter 2 only if it exists and remains strategically attractive. If the same-owner negotiation stalls, move to the market-sweep options rather than weakening the counter just to get a deal done.'
  if ms:return 'Decline the current offer. The model did not find a credible same-owner counter, so do not force the negotiation; move directly to the market-sweep alternatives.'
  return 'Decline and hold. No same-owner counter or league-wide alternative cleared the current model filters, so there is no reason to manufacture a deal.'
 if action=='COUNTER_CURRENT_OFFEROR':return 'Keep the negotiation alive and lead with Counter 1. Counter 2 is an alternate structure, not a required concession path. Shop elsewhere only if the same-owner conversation fails.'
 if action=='SHOP_BEFORE_ACCEPTING':return 'Keep the current offer available while testing the highest-ranked market alternatives. Use a same-owner counter only if it improves the current structure without sacrificing the focal team’s strategic edge.'
 return 'Accept if the offer remains available. The model did not identify a sufficiently superior actionable path to justify delaying the deal.'
def render(report,out):
 b=load();cur=report.get('current_offer_evaluation') or {};sim=cur.get('simulation') or {};before=sim.get('focus_before') or {};after=sim.get('focus_after') or {};st=sim.get('strategic') or {};action=str(report.get('recommended_next_action') or '');v=verdict(action);cs=(report.get('suggested_counteroffers') or [])[:2];ms=(report.get('market_sweep_alternatives') or [])[:5]
 ss=getSampleStyleSheet();ss.add(ParagraphStyle(name='T2',parent=ss['Title'],fontName='Helvetica-Bold',fontSize=18,textColor=NAVY,leading=20));ss.add(ParagraphStyle(name='H2x',parent=ss['Heading2'],fontName='Helvetica-Bold',fontSize=11,textColor=NAVY,leading=13,spaceBefore=7,spaceAfter=4));ss.add(ParagraphStyle(name='Bx',parent=ss['BodyText'],fontSize=8.5,leading=11));ss.add(ParagraphStyle(name='Smx',parent=ss['BodyText'],fontSize=7,leading=9,textColor=GRAY));ss.add(ParagraphStyle(name='Vx',parent=ss['Normal'],fontName='Helvetica-Bold',fontSize=15,textColor=WHITE,alignment=1,leading=16))
 P=lambda t,s='Bx':Paragraph(clean(t),ss[s])
 def foot(c,d):c.saveState();c.setFont('Helvetica',6.2);c.setFillColor(GRAY);c.drawString(.5*inch,.28*inch,f"{MODEL_VERSION} | {report.get('model_version','')} | deterministic narrative from model outputs");c.drawRightString(8*inch,.28*inch,'FSFFL');c.restoreState()
 doc=SimpleDocTemplate(str(out),pagesize=letter,leftMargin=.48*inch,rightMargin=.48*inch,topMargin=.4*inch,bottomMargin=.42*inch);story=[P('FSFFL TRADE DECISION REPORT','T2'),P(f"{clean(cur.get('buyer_team'))} offer — Send: {names(cur.get('outgoing_asset_names'))}",'Smx'),Spacer(1,5)]
 vc=GREEN if v=='ACCEPT' else RED if v=='DECLINE' else NAVY;box=Table([[Paragraph(f"MODEL VERDICT:<br/>{v}",ss['Vx']),P(f"<b>Receive:</b> {names(cur.get('return_asset_names'))}")]],colWidths=[2*inch,5.42*inch]);box.setStyle(TableStyle([('BACKGROUND',(0,0),(0,0),vc),('BACKGROUND',(1,0),(1,0),LIGHT),('BOX',(0,0),(-1,-1),.7,MID),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7)]));story += [box,Spacer(1,6)]
 metrics=[['Expected wins',f"{sf(before.get('expected_wins')):.2f} → {sf(after.get('expected_wins')):.2f}"],['Playoff odds',f"{sf(before.get('playoff_probability'))*100:.1f}% → {sf(after.get('playoff_probability'))*100:.1f}%"],['Champ odds',f"{sf(before.get('championship_probability'))*100:.1f}% → {sf(after.get('championship_probability'))*100:.1f}%"],['Strategic value',f"{sf(st.get('strategic_value_delta')):+,.0f}"],['Dynasty value',f"{sf(st.get('market_dynasty_delta')):+,.0f}"],['Break-glass',f"{sf(st.get('break_glass_delta')):+,.0f}"]]
 mt=Table(metrics,colWidths=[1.25*inch,1.18*inch]*3);mt.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),LIGHT),('BOX',(0,0),(-1,-1),.5,MID),('GRID',(0,0),(-1,-1),.25,MID),('FONTNAME',(0,0),(-1,-1),'Helvetica'),('FONTSIZE',(0,0),(-1,-1),7.2),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),4)]));story += [mt,P('MODEL READ','H2x'),P(b.natural_narrative(report,cur)),Spacer(1,3),P(b.counterparty_narrative(cur),'Smx')]
 story += [P(f"SUGGESTED COUNTEROFFERS — {len(cs)} IDENTIFIED",'H2x')]
 if cs:
  for i,r in enumerate(cs,1):story += [P(counter_text(r,i)),Spacer(1,3)]
 else:story += [P('No credible same-owner counter was identified. The model prefers holding, accepting, or shopping elsewhere rather than inventing a compromise package.')]
 story += [P(f"MARKET SWEEP — {len(ms)} ALTERNATIVE"+('S' if len(ms)!=1 else ''),'H2x')]
 if ms:
  for i,r in enumerate(ms,1):story += [P(market_text(r,i)),Spacer(1,3)]
 else:story += [P('No other-owner alternative cleared the current market-sweep filters.')]
 story += [P('RECOMMENDED NEGOTIATION SEQUENCE','H2x'),P(sequence(report))]
 doc.build(story,onFirstPage=foot,onLaterPages=foot)
def main():
 a=argparse.ArgumentParser();a.add_argument('--input',required=True);a.add_argument('--output',required=True);x=a.parse_args();r=json.loads(Path(x.input).read_text());render(r,Path(x.output));print(json.dumps({'renderer_model_version':MODEL_VERSION,'source_model_version':r.get('model_version'),'pdf':x.output},indent=2))
if __name__=='__main__':main()
