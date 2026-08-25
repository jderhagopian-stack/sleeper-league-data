#!/usr/bin/env python3
"""Trade Decision Report 1.4: plain-English labels/prose and 2x3 headline cards."""
from __future__ import annotations
import argparse, importlib.util, json
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
BASE=Path(__file__).resolve().parent/'render_trade_decision_report_v13.py'
MODEL_VERSION='FSFFL-Trade-Decision-Report-1.4'
NAVY=colors.HexColor('#14213D');RED=colors.HexColor('#C23B36');GREEN=colors.HexColor('#2F7D4A');GRAY=colors.HexColor('#5F6B76');LIGHT=colors.HexColor('#F3F5F7');GOOD=colors.HexColor('#EAF5EE');BAD=colors.HexColor('#FBEDEC');MID=colors.HexColor('#D8DDE3');WHITE=colors.white;BLACK=colors.HexColor('#1C1F23')
def load():
 s=importlib.util.spec_from_file_location('trade_report_v13_base',BASE);m=importlib.util.module_from_spec(s);assert s.loader;s.loader.exec_module(m);return m
b=load();sf=b.sf;clean=b.clean;names=b.names;verdict=b.verdict

def owner_fit(v):
 return {'HIGH':"Strong match for this owner's tendencies",'MEDIUM':"Reasonable match for this owner's tendencies",'LOW':"Weak match for this owner's tendencies",'VERY_LOW':"Very poor match for this owner's tendencies"}.get(str(v or '').upper(),"Owner interest is uncertain")
def focus_name(report,cur):
 n=clean(report.get('focus_team') or report.get('focus_team_name'))
 if n:return n
 return 'Hungry Dawgs' if clean(cur.get('buyer_team'))=='Chef Niners' else 'This team'
def narrative(report,cur):
 sim=cur.get('simulation') or {};d=sim.get('focus_delta') or {};st=sim.get('strategic') or {};state=clean(st.get('objective_state')).lower();team=focus_name(report,cur);wins=sf(d.get('expected_wins'));pf=sf(d.get('expected_points_for'));overall=sf(st.get('strategic_value_delta'));liq=sf(st.get('liquidity_value_delta'));bg=sf(st.get('break_glass_delta'));play=sf(d.get('playoff_probability'));champ=sf(d.get('championship_probability'));sent=names(cur.get('outgoing_asset_names'));rec=names(cur.get('return_asset_names'));action=str(report.get('recommended_next_action') or '')
 if action!='DECLINE':
  return {'COUNTER_CURRENT_OFFEROR':f'The offer is close enough to keep negotiating, but the model prefers a different structure with this same owner. The counters below are ordered by benefit to {team} and fit with the other owner.','SHOP_BEFORE_ACCEPTING':f'The offer is workable, but the model found better uses of these assets elsewhere. Keep it alive if possible, then test the strongest outside options before committing.','ACCEPT_NOW':f'The offer fits what {team} is trying to accomplish and clears the major roster-value and competitive checks. The market search did not find a clearly better realistic path.'}.get(action,'Review the offer and the alternatives below.')
 if state=='rebuild':p=f'This deal makes {team} better in 2026, but not in a way that meaningfully advances the rebuild. Moving {sent} for {rec} adds {wins:+.2f} expected wins and {pf:+.0f} projected points, while the overall value of the roster falls {abs(overall):,.0f} under the model.'
 else:p=f'This deal helps {team} in the short term, but the immediate gain is not large enough to justify the longer-term cost. It adds {wins:+.2f} expected wins while reducing overall roster value by {abs(overall):,.0f}.'
 costs=[]
 if liq<0:costs.append(f'{abs(liq):,.0f} of future trade flexibility')
 if bg<0:costs.append(f'{abs(bg):,.0f} of fallback trade value')
 if costs:p+=' The biggest costs are '+' and '.join(costs[:2])+'.'
 sent_assets=st.get('sent') or []
 if sent_assets:
  x=max(sent_assets,key=lambda z:(sf(z.get('break_glass_value')),sf(z.get('liquidity_score')),sf(z.get('dynasty_value'))))
  if clean(x.get('core_status')).replace('_',' ')=='core high hold':p+=f" {clean(x.get('name'))} is the piece that most clearly breaks the deal. He is one of the roster's premium keepers, is relatively easy to trade ({sf(x.get('liquidity_score')):.2f} on the model's 0-to-1 tradability scale), and retains about {sf(x.get('break_glass_value')):,.0f} of value even if the team suddenly needs to pivot. Several good assets can add up to similar calculator value without fully replacing that kind of cornerstone."
 p+=f' The short-term improvement is real but limited: playoff odds move {play*100:+.1f} percentage points and championship odds {champ*100:+.1f}. That is not enough to justify the loss of premium long-term value.'
 return p
def counterparty(cur):
 buyer=clean(cur.get('buyer_team') or 'The other owner');br=cur.get('buyer_rationality') or {};ob=br.get('owner_behavior') or {};reason=clean(ob.get('reason') or br.get('reason') or '').replace('manager ','').replace('Manager ','');txt=f'{buyer}: {owner_fit(br.get("heuristic_acceptance_fit"))}.'
 return txt+(f' The behavioral history supports that read: this owner {reason}.' if reason else '')
def counter_text(row,i):
 sim=row.get('simulation') or {};d=sim.get('focus_delta') or {};st=sim.get('strategic') or {};validated=row.get('counter_validation_status')=='VALIDATED_ACCEPTANCE';conf=owner_fit(row.get('acceptance_likelihood'))+'.' if validated else 'This makes sense for both rosters on paper, but the model cannot yet say confidently how likely the other owner is to accept it.'
 return f'<b>{i}. Send {names(row.get("outgoing_asset_names"))}; receive {names(row.get("return_asset_names"))}.</b> For your team: {sf(d.get("expected_wins")):+.2f} expected wins, {sf(st.get("market_dynasty_delta")):+,.0f} dynasty value, and {sf(st.get("strategic_value_delta")):+,.0f} overall franchise impact. <font color="#5F6B76">{conf}</font>'
def market_text(row,i):
 sim=row.get('simulation') or {};d=sim.get('focus_delta') or {};st=sim.get('strategic') or {};return f'<b>{i}. {clean(row.get("buyer_team"))}</b> - send {names(row.get("outgoing_asset_names"))}; receive {names(row.get("return_asset_names"))}. For your team: {sf(d.get("expected_wins")):+.2f} expected wins and {sf(st.get("strategic_value_delta")):+,.0f} overall franchise impact. <font color="#5F6B76">{owner_fit(row.get("acceptance_likelihood"))}.</font>'
def sequence(r):
 a=str(r.get('recommended_next_action') or '');cs=r.get('suggested_counteroffers') or [];ms=r.get('market_sweep_alternatives') or []
 if a=='DECLINE':
  if cs:return 'Decline the current offer and lead with Counter 1. If it is rejected, do not automatically water it down. If the conversation stalls, move to the best outside options rather than forcing a deal.' if len(cs)==1 else 'Decline the current offer and lead with Counter 1. If it is rejected but the conversation stays active, try Counter 2 as a different structure - not simply as a concession. If neither works, move to the best outside options.'
  if ms:return 'Decline the current offer and move directly to the best outside options; no worthwhile same-owner counter cleared the model.'
  return 'Decline and hold. No better counter or outside trade cleared the model, so there is no reason to manufacture a deal.'
 if a=='COUNTER_CURRENT_OFFEROR':return 'Keep negotiating and lead with Counter 1. Use Counter 2 only if it represents a genuinely different way to make the deal work.'
 if a=='SHOP_BEFORE_ACCEPTING':return 'Keep the current offer available while testing the strongest outside options.'
 return 'Accept if the offer remains available; the model did not identify a clearly better realistic path.'
def render(r,out):
 cur=r.get('current_offer_evaluation') or {};sim=cur.get('simulation') or {};before=sim.get('focus_before') or {};after=sim.get('focus_after') or {};st=sim.get('strategic') or {};v=verdict(r.get('recommended_next_action'));cs=(r.get('suggested_counteroffers') or [])[:2];ms=(r.get('market_sweep_alternatives') or [])[:5]
 ss=getSampleStyleSheet();ss.add(ParagraphStyle(name='T14',parent=ss['Title'],fontName='Helvetica-Bold',fontSize=18,leading=20,textColor=NAVY));ss.add(ParagraphStyle(name='H14',parent=ss['Heading2'],fontName='Helvetica-Bold',fontSize=10.6,leading=12.5,textColor=NAVY,spaceBefore=6,spaceAfter=3));ss.add(ParagraphStyle(name='B14',parent=ss['BodyText'],fontSize=8.35,leading=10.6,textColor=BLACK));ss.add(ParagraphStyle(name='S14',parent=ss['BodyText'],fontSize=7.1,leading=8.8,textColor=GRAY));ss.add(ParagraphStyle(name='V14',parent=ss['Normal'],fontName='Helvetica-Bold',fontSize=14,leading=15,textColor=WHITE,alignment=1));ss.add(ParagraphStyle(name='CL14',parent=ss['Normal'],fontName='Helvetica-Bold',fontSize=6.8,leading=8,textColor=GRAY,alignment=1));ss.add(ParagraphStyle(name='CV14',parent=ss['Normal'],fontName='Helvetica-Bold',fontSize=10.5,leading=12,textColor=BLACK,alignment=1));P=lambda t,s='B14':Paragraph(clean(t),ss[s])
 def card(label,value,good):
  t=Table([[P(label,'CL14')],[P(value,'CV14')]],colWidths=[2.38*inch],rowHeights=[.22*inch,.30*inch]);t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),GOOD if good else BAD),('BOX',(0,0),(-1,-1),.5,MID),('VALIGN',(0,0),(-1,-1),'MIDDLE')]));return t
 def foot(c,d):c.saveState();c.setFont('Helvetica',6.2);c.setFillColor(GRAY);c.drawString(.5*inch,.28*inch,f'{MODEL_VERSION} | {r.get("model_version","")} | plain-English presentation');c.drawRightString(8*inch,.28*inch,'FSFFL');c.restoreState()
 doc=SimpleDocTemplate(str(out),pagesize=letter,leftMargin=.48*inch,rightMargin=.48*inch,topMargin=.38*inch,bottomMargin=.42*inch);story=[P('FSFFL TRADE DECISION REPORT','T14'),P(f'{clean(cur.get("buyer_team"))} offer - Send: {names(cur.get("outgoing_asset_names"))}','S14'),Spacer(1,4)]
 vc=GREEN if v=='ACCEPT' else RED if v=='DECLINE' else NAVY;box=Table([[Paragraph(f'MODEL VERDICT:<br/>{v}',ss['V14']),P(f'<b>Receive:</b> {names(cur.get("return_asset_names"))}')]],colWidths=[2*inch,5.42*inch],rowHeights=[.56*inch]);box.setStyle(TableStyle([('BACKGROUND',(0,0),(0,0),vc),('BACKGROUND',(1,0),(1,0),LIGHT),('BOX',(0,0),(-1,-1),.7,MID),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8)]));story += [box,Spacer(1,5)]
 cards=[card('EXPECTED WINS',f'{sf(before.get("expected_wins")):.2f} -> {sf(after.get("expected_wins")):.2f}',sf(after.get('expected_wins'))>=sf(before.get('expected_wins'))),card('PLAYOFF ODDS',f'{sf(before.get("playoff_probability"))*100:.1f}% -> {sf(after.get("playoff_probability"))*100:.1f}%',sf(after.get('playoff_probability'))>=sf(before.get('playoff_probability'))),card('CHAMPIONSHIP ODDS',f'{sf(before.get("championship_probability"))*100:.1f}% -> {sf(after.get("championship_probability"))*100:.1f}%',sf(after.get('championship_probability'))>=sf(before.get('championship_probability'))),card('OVERALL FRANCHISE IMPACT',f'{sf(st.get("strategic_value_delta")):+,.0f}',sf(st.get('strategic_value_delta'))>=0),card('DYNASTY VALUE',f'{sf(st.get("market_dynasty_delta")):+,.0f}',sf(st.get('market_dynasty_delta'))>=0),card('FUTURE TRADE FLEXIBILITY',f'{sf(st.get("liquidity_value_delta")):+,.0f}',sf(st.get('liquidity_value_delta'))>=0)]
 grid=Table([cards[:3],cards[3:]],colWidths=[2.47*inch]*3,rowHeights=[.56*inch,.56*inch]);grid.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),1),('RIGHTPADDING',(0,0),(-1,-1),1),('TOPPADDING',(0,0),(-1,-1),1),('BOTTOMPADDING',(0,0),(-1,-1),1)]));story += [grid,Spacer(1,4),P('WHY THE MODEL SAYS THIS','H14'),P(narrative(r,cur)),Spacer(1,3),P(counterparty(cur),'S14'),P(f'SUGGESTED COUNTEROFFERS - {len(cs)} IDENTIFIED','H14')]
 if cs:
  for i,x in enumerate(cs,1):story += [P(counter_text(x,i)),Spacer(1,2)]
 else:story += [P('No worthwhile counter with this same owner cleared the model. Do not invent a compromise just to keep the negotiation alive.')]
 story += [P(f'MARKET SWEEP - {len(ms)} OTHER-OWNER OPTION'+('S' if len(ms)!=1 else ''),'H14')]
 if ms:
  for i,x in enumerate(ms,1):story += [P(market_text(x,i)),Spacer(1,2)]
 else:story += [P('No trade with another owner cleared the current model filters.')]
 story += [P('WHAT I WOULD DO NEXT','H14'),P(sequence(r))];doc.build(story,onFirstPage=foot,onLaterPages=foot)
def main():
 a=argparse.ArgumentParser();a.add_argument('--input',required=True);a.add_argument('--output',required=True);x=a.parse_args();r=json.loads(Path(x.input).read_text());render(r,Path(x.output));print(json.dumps({'renderer_model_version':MODEL_VERSION,'source_model_version':r.get('model_version'),'pdf':x.output},indent=2))
if __name__=='__main__':main()
