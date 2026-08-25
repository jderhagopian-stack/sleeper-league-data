#!/usr/bin/env python3
"""Trade Decision Report 1.5: tighter prose, standardized confidence, and Bottom Line."""
from __future__ import annotations
import argparse, importlib.util, json
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
BASE=Path(__file__).resolve().parent/'render_trade_decision_report_v14.py'
MODEL_VERSION='FSFFL-Trade-Decision-Report-1.5'
NAVY=colors.HexColor('#14213D');RED=colors.HexColor('#C23B36');GREEN=colors.HexColor('#2F7D4A');GRAY=colors.HexColor('#5F6B76');LIGHT=colors.HexColor('#F3F5F7');GOOD=colors.HexColor('#EAF5EE');BAD=colors.HexColor('#FBEDEC');MID=colors.HexColor('#D8DDE3');WHITE=colors.white;BLACK=colors.HexColor('#1C1F23')
def load():
 s=importlib.util.spec_from_file_location('trade_report_v14_base',BASE);m=importlib.util.module_from_spec(s);assert s.loader;s.loader.exec_module(m);return m
b=load();sf=b.sf;clean=b.clean;names=b.names;verdict=b.verdict;focus_name=b.focus_name

def confidence_phrase(v):
 return {'HIGH':'High confidence this owner would seriously consider it','MEDIUM':'Moderate confidence this owner would seriously consider it','LOW':'Low confidence this owner would seriously consider it','VERY_LOW':'Very low confidence this owner would seriously consider it'}.get(str(v or '').upper(),'Acceptance confidence is uncertain')
def bottom_line(r,cur):
 a=str(r.get('recommended_next_action') or '');team=focus_name(r,cur);sent=names(cur.get('outgoing_asset_names'));rec=names(cur.get('return_asset_names'))
 if a=='DECLINE':return f'Decline: {rec} does not compensate {team} enough for giving up {sent}, even though the deal improves the short-term projection.'
 if a=='COUNTER_CURRENT_OFFEROR':return f'Counter: the framework is workable, but {team} should improve the return before moving {sent}.'
 if a=='SHOP_BEFORE_ACCEPTING':return f'Shop first: this offer is viable, but the model found better ways for {team} to use the same trade assets.'
 if a=='ACCEPT_NOW':return f'Accept: the return fits {team}\'s competitive window and no clearly better realistic alternative surfaced.'
 return 'Review: the model does not have a sufficiently clear action signal.'
def narrative(r,cur):
 sim=cur.get('simulation') or {};d=sim.get('focus_delta') or {};st=sim.get('strategic') or {};state=clean(st.get('objective_state')).lower();team=focus_name(r,cur);wins=sf(d.get('expected_wins'));pf=sf(d.get('expected_points_for'));overall=sf(st.get('strategic_value_delta'));liq=sf(st.get('liquidity_value_delta'));play=sf(d.get('playoff_probability'));champ=sf(d.get('championship_probability'));sent=names(cur.get('outgoing_asset_names'));rec=names(cur.get('return_asset_names'));a=str(r.get('recommended_next_action') or '')
 if a!='DECLINE':
  return {'COUNTER_CURRENT_OFFEROR':f'This is close enough to keep negotiating, but the model prefers a better version of the deal with the same owner. The counters below are ranked by benefit to {team} and by how well they fit the other owner.','SHOP_BEFORE_ACCEPTING':f'This offer is workable, but the model found stronger uses of the same assets elsewhere. Keep the offer alive if possible and test the best outside options first.','ACCEPT_NOW':f'This offer fits what {team} is trying to accomplish and clears the major competitive and roster-value checks. No clearly better realistic alternative surfaced.'}.get(a,'Review the offer and the alternatives below.')
 if state=='rebuild':p=f'This trade improves {team} in 2026, but not enough to advance the rebuild. Swapping {sent} for {rec} adds {wins:+.2f} expected wins and {pf:+.0f} projected points, while reducing overall franchise value by {abs(overall):,.0f}.'
 else:p=f'This trade helps {team} now, but the gain is not large enough to justify the longer-term cost. It adds {wins:+.2f} expected wins while reducing overall franchise value by {abs(overall):,.0f}.'
 if liq<0:p+=f' It also reduces future trade flexibility by about {abs(liq):,.0f}.'
 sent_assets=st.get('sent') or []
 if sent_assets:
  x=max(sent_assets,key=lambda z:(sf(z.get('break_glass_value')),sf(z.get('liquidity_score')),sf(z.get('dynasty_value'))))
  if clean(x.get('core_status')).replace('_',' ')=='core high hold':p+=f" {clean(x.get('name'))} is the key piece. He is a premium keeper and one of the easiest assets on the roster to move later if plans change, so several good pieces do not fully replace the value of keeping that cornerstone."
 p+=f' The short-term gain remains modest: playoff odds change {play*100:+.1f} points and championship odds {champ*100:+.1f} points.'
 return p
def counterparty(cur):
 buyer=clean(cur.get('buyer_team') or 'The other owner');br=cur.get('buyer_rationality') or {};return f'{buyer}: {confidence_phrase(br.get("heuristic_acceptance_fit"))}.'
def counter_text(row,i):
 sim=row.get('simulation') or {};d=sim.get('focus_delta') or {};st=sim.get('strategic') or {};validated=row.get('counter_validation_status')=='VALIDATED_ACCEPTANCE';conf=confidence_phrase(row.get('acceptance_likelihood')) if validated else 'Strategically sensible, but the model does not yet have enough evidence to rate acceptance confidence.'
 return f'<b>{i}. Send {names(row.get("outgoing_asset_names"))}; receive {names(row.get("return_asset_names"))}.</b> Impact on your team: {sf(d.get("expected_wins")):+.2f} expected wins, {sf(st.get("market_dynasty_delta")):+,.0f} dynasty value, and {sf(st.get("strategic_value_delta")):+,.0f} overall franchise impact. <font color="#5F6B76">{conf}</font>'
def market_text(row,i):
 sim=row.get('simulation') or {};d=sim.get('focus_delta') or {};st=sim.get('strategic') or {};return f'<b>{i}. {clean(row.get("buyer_team"))}</b> - send {names(row.get("outgoing_asset_names"))}; receive {names(row.get("return_asset_names"))}. Impact on your team: {sf(d.get("expected_wins")):+.2f} expected wins and {sf(st.get("strategic_value_delta")):+,.0f} overall franchise impact. <font color="#5F6B76">{confidence_phrase(row.get("acceptance_likelihood"))}.</font>'
def sequence(r):
 a=str(r.get('recommended_next_action') or '');cs=r.get('suggested_counteroffers') or [];ms=r.get('market_sweep_alternatives') or []
 if a=='DECLINE':
  if cs and len(cs)>1:return 'Decline the current offer. Lead with Counter 1. If the owner stays engaged but rejects it, try Counter 2 as a different structure rather than as a weaker concession. If neither works, move to the best outside options.'
  if cs:return 'Decline the current offer and lead with Counter 1. If it is rejected, do not automatically weaken it; move to the best outside options if the conversation stalls.'
  if ms:return 'Decline the current offer and move directly to the best outside options. No worthwhile counter with this owner cleared the model.'
  return 'Decline and hold. No better counter or outside trade cleared the model.'
 if a=='COUNTER_CURRENT_OFFEROR':return 'Keep negotiating and lead with Counter 1. Use Counter 2 only if it offers a genuinely different path to agreement.'
 if a=='SHOP_BEFORE_ACCEPTING':return 'Keep the current offer available while testing the strongest outside options.'
 return 'Accept if the offer remains available; no clearly better realistic path surfaced.'
def render(r,out):
 cur=r.get('current_offer_evaluation') or {};sim=cur.get('simulation') or {};before=sim.get('focus_before') or {};after=sim.get('focus_after') or {};st=sim.get('strategic') or {};v=verdict(r.get('recommended_next_action'));cs=(r.get('suggested_counteroffers') or [])[:2];ms=(r.get('market_sweep_alternatives') or [])[:5]
 ss=getSampleStyleSheet();ss.add(ParagraphStyle(name='T15',parent=ss['Title'],fontName='Helvetica-Bold',fontSize=18,leading=20,textColor=NAVY));ss.add(ParagraphStyle(name='H15',parent=ss['Heading2'],fontName='Helvetica-Bold',fontSize=10.6,leading=12.5,textColor=NAVY,spaceBefore=6,spaceAfter=3));ss.add(ParagraphStyle(name='B15',parent=ss['BodyText'],fontSize=8.35,leading=10.6,textColor=BLACK));ss.add(ParagraphStyle(name='S15',parent=ss['BodyText'],fontSize=7.1,leading=8.8,textColor=GRAY));ss.add(ParagraphStyle(name='BL15',parent=ss['BodyText'],fontName='Helvetica-Bold',fontSize=8.7,leading=10.8,textColor=NAVY));ss.add(ParagraphStyle(name='V15',parent=ss['Normal'],fontName='Helvetica-Bold',fontSize=14,leading=15,textColor=WHITE,alignment=1));ss.add(ParagraphStyle(name='CL15',parent=ss['Normal'],fontName='Helvetica-Bold',fontSize=6.8,leading=8,textColor=GRAY,alignment=1));ss.add(ParagraphStyle(name='CV15',parent=ss['Normal'],fontName='Helvetica-Bold',fontSize=10.5,leading=12,textColor=BLACK,alignment=1));P=lambda t,s='B15':Paragraph(clean(t),ss[s])
 def card(label,value,good):
  t=Table([[P(label,'CL15')],[P(value,'CV15')]],colWidths=[2.38*inch],rowHeights=[.22*inch,.30*inch]);t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),GOOD if good else BAD),('BOX',(0,0),(-1,-1),.5,MID),('VALIGN',(0,0),(-1,-1),'MIDDLE')]));return t
 def foot(c,d):c.saveState();c.setFont('Helvetica',6.2);c.setFillColor(GRAY);c.drawString(.5*inch,.28*inch,f'{MODEL_VERSION} | {r.get("model_version","")} | plain-English presentation');c.drawRightString(8*inch,.28*inch,'FSFFL');c.restoreState()
 doc=SimpleDocTemplate(str(out),pagesize=letter,leftMargin=.48*inch,rightMargin=.48*inch,topMargin=.38*inch,bottomMargin=.42*inch);story=[P('FSFFL TRADE DECISION REPORT','T15'),P(f'{clean(cur.get("buyer_team"))} offer - Send: {names(cur.get("outgoing_asset_names"))}','S15'),Spacer(1,4)]
 vc=GREEN if v=='ACCEPT' else RED if v=='DECLINE' else NAVY;box=Table([[Paragraph(f'MODEL VERDICT:<br/>{v}',ss['V15']),P(f'<b>Receive:</b> {names(cur.get("return_asset_names"))}')]],colWidths=[2*inch,5.42*inch],rowHeights=[.56*inch]);box.setStyle(TableStyle([('BACKGROUND',(0,0),(0,0),vc),('BACKGROUND',(1,0),(1,0),LIGHT),('BOX',(0,0),(-1,-1),.7,MID),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8)]));story += [box,Spacer(1,4),P('BOTTOM LINE','H15'),P(bottom_line(r,cur),'BL15'),Spacer(1,4)]
 cards=[card('EXPECTED WINS',f'{sf(before.get("expected_wins")):.2f} -> {sf(after.get("expected_wins")):.2f}',sf(after.get('expected_wins'))>=sf(before.get('expected_wins'))),card('PLAYOFF ODDS',f'{sf(before.get("playoff_probability"))*100:.1f}% -> {sf(after.get("playoff_probability"))*100:.1f}%',sf(after.get('playoff_probability'))>=sf(before.get('playoff_probability'))),card('CHAMPIONSHIP ODDS',f'{sf(before.get("championship_probability"))*100:.1f}% -> {sf(after.get("championship_probability"))*100:.1f}%',sf(after.get('championship_probability'))>=sf(before.get('championship_probability'))),card('OVERALL FRANCHISE IMPACT',f'{sf(st.get("strategic_value_delta")):+,.0f}',sf(st.get('strategic_value_delta'))>=0),card('DYNASTY VALUE',f'{sf(st.get("market_dynasty_delta")):+,.0f}',sf(st.get('market_dynasty_delta'))>=0),card('FUTURE TRADE FLEXIBILITY',f'{sf(st.get("liquidity_value_delta")):+,.0f}',sf(st.get('liquidity_value_delta'))>=0)]
 grid=Table([cards[:3],cards[3:]],colWidths=[2.47*inch]*3,rowHeights=[.56*inch,.56*inch]);grid.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),1),('RIGHTPADDING',(0,0),(-1,-1),1),('TOPPADDING',(0,0),(-1,-1),1),('BOTTOMPADDING',(0,0),(-1,-1),1)]));story += [grid,Spacer(1,4),P('WHY THE MODEL SAYS THIS','H15'),P(narrative(r,cur)),Spacer(1,3),P(counterparty(cur),'S15'),P(f'SUGGESTED COUNTEROFFERS - {len(cs)} IDENTIFIED','H15')]
 if cs:
  for i,x in enumerate(cs,1):story += [P(counter_text(x,i)),Spacer(1,2)]
 else:story += [P('No worthwhile counter with this owner cleared the model. Do not invent a compromise just to keep the negotiation alive.')]
 story += [P(f'MARKET SWEEP - {len(ms)} OTHER-OWNER OPTION'+('S' if len(ms)!=1 else ''),'H15')]
 if ms:
  for i,x in enumerate(ms,1):story += [P(market_text(x,i)),Spacer(1,2)]
 else:story += [P('No trade with another owner cleared the current model filters.')]
 story += [P('WHAT I WOULD DO NEXT','H15'),P(sequence(r))];doc.build(story,onFirstPage=foot,onLaterPages=foot)
def main():
 a=argparse.ArgumentParser();a.add_argument('--input',required=True);a.add_argument('--output',required=True);x=a.parse_args();r=json.loads(Path(x.input).read_text());render(r,Path(x.output));print(json.dumps({'renderer_model_version':MODEL_VERSION,'source_model_version':r.get('model_version'),'pdf':x.output},indent=2))
if __name__=='__main__':main()
