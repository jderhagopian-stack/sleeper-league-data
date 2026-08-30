#!/usr/bin/env python3
"""Trade Decision Report 1.9 — plain-language presentation.

This renderer changes presentation only. The underlying trade analysis,
simulation, roster resolution, market search, and values are unchanged.
"""
from __future__ import annotations
import argparse, json
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from report_language import label, acceptance_fit, action, probability_change, value_change, magnitude_word

MODEL_VERSION='FSFFL-Trade-Decision-Report-1.10'
NAVY=colors.HexColor('#14213D');RED=colors.HexColor('#C23B36');GREEN=colors.HexColor('#2F7D4A');GRAY=colors.HexColor('#5F6B76');LIGHT=colors.HexColor('#F3F5F7');GOOD=colors.HexColor('#EAF5EE');BAD=colors.HexColor('#FBEDEC');MID=colors.HexColor('#D8DDE3');WHITE=colors.white;BLACK=colors.HexColor('#1C1F23')

def sf(v,d=0.0):
    try:return float(v)
    except:return d

def clean(x,n=None):
    # Preserve the small set of ReportLab paragraph tags used by this renderer
    # while sanitizing names/text to fonts that are guaranteed to render.
    import re
    s=str(x or '')
    tags={}
    def hold(m):
        key=f"__TAG{len(tags)}__"
        tags[key]=m.group(0)
        return key
    s=re.sub(r'<(?:/?b|br\s*/?|font\b[^>]*|/font)>',hold,s,flags=re.I)
    s=(s.replace('\u2192','->').replace('\u2014','-').replace('\u2013','-')
         .replace('\u2019',"'").replace('\u2018',"'").replace('\u201c','"').replace('\u201d','"'))
    s=s.encode('ascii','ignore').decode('ascii')
    s=s.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
    for key,tag in tags.items():
        s=s.replace(key,tag)
    # Never truncate user-facing prose. ReportLab is allowed to wrap content
    # and grow the document to additional pages when needed.
    return s

def names(xs):
    vals=[str(x) for x in (xs or []) if x]
    return ', '.join(vals) if vals else 'nothing'

def team_name(r,cur):
    for k in ('focus_team','focus_team_name','team_name'):
        if r.get(k): return str(r[k])
    sim=cur.get('simulation') or {}
    before=sim.get('focus_before') or {}
    if before.get('team_name'): return str(before['team_name'])
    return 'This team'

def verdict(r):
    return action(r.get('recommended_next_action'))

def roster_note(row,focus_uid):
    sim=row.get('simulation') or {}; res=sim.get('roster_resolution') or {}
    x=res.get(str(focus_uid)) or {}
    n=int(x.get('required_cuts') or 0)
    if not n:return 'No extra roster cut is required.'
    cuts=', '.join(str(c.get('name')) for c in (x.get('selected_cuts') or []) if c.get('name')) or 'a roster player'
    return f"The trade would require {n} roster cut{'s' if n!=1 else ''}: {cuts}. The model already included that cost."

def synergy_note(row,focus_uid):
    sim=row.get('simulation') or {}; ri=sim.get('roster_interactions') or {}; teams=ri.get('teams') or {}
    f=teams.get(str(focus_uid)) or {}; delta=sf(f.get('roster_interaction_value_delta'))
    if abs(delta)<.5:return ''
    direction='adds' if delta>0 else 'costs'
    pairs=((f.get('after') or {}).get('same_team_position_pairs') or [])
    if pairs and delta>0:
        p=max(pairs,key=lambda x:sf(x.get('insurance_value')))
        return f"Roster fit {direction} about {abs(delta):,.0f} points of value. The biggest reason is the added insurance/coverage from owning {clean(p.get('primary'),45)} and {clean(p.get('secondary'),45)} together."
    return f"Roster fit {direction} about {abs(delta):,.0f} points of value because of how the incoming and outgoing players interact with the rest of the roster."

def bottom_line(r,cur):
    a=str(r.get('recommended_next_action') or '')
    team=team_name(r,cur)
    sent=names(cur.get('outgoing_asset_names')); rec=names(cur.get('return_asset_names'))
    if a=='ACCEPT_NOW': return f"Accept. {team} gets enough benefit from {rec} to justify giving up {sent}, and the model did not find a clearly better realistic use of those assets."
    if a=='SHOP_BEFORE_ACCEPTING': return f"Do not reject it, but shop around first. The offer is reasonable; the model found at least one potentially better way for {team} to use the same assets."
    if a=='COUNTER_CURRENT_OFFEROR': return f"Counter. The basic deal works, but the model thinks {team} should improve the return before agreeing."
    if a=='DECLINE': return f"Decline. The return does not make up for what {team} gives away once short-term winning chances and long-term value are considered together."
    return 'The model does not have a clear enough edge to recommend an immediate move.'

def why(r,cur):
    sim=cur.get('simulation') or {}; d=sim.get('focus_delta') or {}; st=sim.get('strategic') or {}
    wins=sf(d.get('expected_wins')); champ=sf(d.get('championship_probability'))*100; play=sf(d.get('playoff_probability'))*100
    dyn=sf(st.get('market_dynasty_delta')); overall=sf(st.get('strategic_value_delta')); liq=sf(st.get('liquidity_value_delta'))
    parts=[]
    if abs(wins)>=.03: parts.append(f"expected wins move {wins:+.2f}")
    if abs(play)>=.5: parts.append(f"playoff odds move {play:+.1f} percentage points")
    if abs(champ)>=.5: parts.append(f"championship odds move {champ:+.1f} percentage points")
    if abs(dyn)>=50: parts.append(f"long-term trade value changes {dyn:+,.0f}")
    if abs(liq)>=50: parts.append(f"future trade flexibility changes {liq:+,.0f}")
    text="The main trade-offs are: "+'; '.join(parts[:5])+'. '
    text+=probability_change("Championship odds", (sim.get('focus_before') or {}).get('championship_probability'), (sim.get('focus_after') or {}).get('championship_probability'))+" "
    text+=value_change("Long-term trade value", dyn)+" "
    if overall>=75:text+=f" Taken together, the model sees this as a meaningful net positive for this specific roster ({overall:+,.0f} overall franchise impact)."
    elif overall<=-75:text+=f" Taken together, the model sees this as a meaningful net negative for this specific roster ({overall:+,.0f} overall franchise impact)."
    else:text+=" Taken together, the overall roster impact is close to neutral."
    sn=synergy_note(cur,r.get('focus_user_id'))
    if sn:text+=' '+sn
    text+=' '+roster_note(cur,r.get('focus_user_id'))
    return text

def what_could_change_answer(r,cur):
    action_code=str(r.get('recommended_next_action') or '')
    cs=r.get('suggested_counteroffers') or []
    ms=r.get('market_sweep_alternatives') or []
    if action_code=='ACCEPT_NOW':
        if cs or ms:
            return "The answer would change if one of the stronger alternatives becomes genuinely available on comparable terms; otherwise the current offer is the best actionable choice the model found."
        return "The answer would change if the price increases, a required roster cut becomes more expensive than modeled, or new player information materially changes the short- or long-term outlook."
    if action_code=='SHOP_BEFORE_ACCEPTING':
        return "If the better alternatives are not actually available, the current offer becomes much more attractive. The recommendation is to test the market, not to reject a reasonable deal automatically."
    if action_code=='COUNTER_CURRENT_OFFEROR':
        return "If the other manager will not improve the return, compare the original offer directly with the strongest outside option rather than countering indefinitely."
    if action_code=='DECLINE':
        return "The answer would change if the return improves enough to close the value or winning-impact gap, or if this team's competitive window changes materially."
    return "A clearer edge in either current-season winning value or long-term roster value could change the recommendation."

def comparison_sentence(row):
    c=row.get('comparison_to_current_offer') or {}; v=str(c.get('verdict_vs_current_offer') or 'MIXED')
    delta=sf(c.get('post_sim_score_delta_vs_current_offer'))
    if v=='BETTER': return f"The model prefers this to the current offer."
    if v=='WORSE': return f"The model prefers the current offer."
    return "This is a different trade-off, but not clearly better or worse than the current offer."

def option_text(row,i,market=False):
    sim=row.get('simulation') or {}; d=sim.get('focus_delta') or {}; st=sim.get('strategic') or {}
    prefix=f"<b>{i}. "
    if market: prefix+=f"{clean(row.get('buyer_team'))}: "
    prefix+=f"Send {names(row.get('outgoing_asset_names'))}; receive {names(row.get('return_asset_names'))}.</b>"
    txt=f"{prefix} Expected wins {sf(d.get('expected_wins')):+.2f}; championship odds {sf(d.get('championship_probability'))*100:+.1f} points; overall franchise impact {sf(st.get('strategic_value_delta')):+,.0f}. {comparison_sentence(row)}"
    fit=row.get('acceptance_likelihood')
    if fit: txt+=f" {acceptance_fit(fit)}."
    return txt

def sequence(r):
    a=str(r.get('recommended_next_action') or ''); cs=r.get('suggested_counteroffers') or []; ms=r.get('market_sweep_alternatives') or []
    if a=='ACCEPT_NOW':return 'Accept if the offer is still available. Do not add more unless the other manager rejects it.'
    if a=='SHOP_BEFORE_ACCEPTING':return 'Keep this offer alive while checking the strongest alternatives. If none is actually available, coming back to this deal is reasonable.'
    if a=='COUNTER_CURRENT_OFFEROR':return 'Lead with Counter 1. Only move to another structure if the first counter is rejected.'
    if a=='DECLINE' and cs:return 'Decline the current version and send Counter 1 instead.'
    if a=='DECLINE' and ms:return 'Decline and pursue the strongest outside option.'
    return 'Hold rather than forcing a deal.'

def render(r,out):
    cur=r.get('current_offer_evaluation') or {}; sim=cur.get('simulation') or {}; before=sim.get('focus_before') or {}; after=sim.get('focus_after') or {}; st=sim.get('strategic') or {}
    v=verdict(r); cs=(r.get('suggested_counteroffers') or [])[:2]; ms=(r.get('market_sweep_alternatives') or [])[:5]
    ss=getSampleStyleSheet()
    ss.add(ParagraphStyle(name='T19',parent=ss['Title'],fontName='Helvetica-Bold',fontSize=18,leading=20,textColor=NAVY))
    ss.add(ParagraphStyle(name='H19',parent=ss['Heading2'],fontName='Helvetica-Bold',fontSize=10.5,leading=12,textColor=NAVY,spaceBefore=6,spaceAfter=3))
    ss.add(ParagraphStyle(name='B19',parent=ss['BodyText'],fontSize=8.4,leading=10.7,textColor=BLACK))
    ss.add(ParagraphStyle(name='S19',parent=ss['BodyText'],fontSize=7.1,leading=8.8,textColor=GRAY))
    ss.add(ParagraphStyle(name='BL19',parent=ss['BodyText'],fontName='Helvetica-Bold',fontSize=8.8,leading=11,textColor=NAVY))
    ss.add(ParagraphStyle(name='V19',parent=ss['Normal'],fontName='Helvetica-Bold',fontSize=14,leading=15,textColor=WHITE,alignment=1))
    ss.add(ParagraphStyle(name='CL19',parent=ss['Normal'],fontName='Helvetica-Bold',fontSize=6.8,leading=8,textColor=GRAY,alignment=1))
    ss.add(ParagraphStyle(name='CV19',parent=ss['Normal'],fontName='Helvetica-Bold',fontSize=10.5,leading=12,textColor=BLACK,alignment=1))
    P=lambda t,s='B19':Paragraph(clean(t),ss[s])
    def card(lbl,val,good):
        t=Table([[P(lbl,'CL19')],[P(val,'CV19')]],colWidths=[2.38*inch],rowHeights=[.22*inch,.30*inch])
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),GOOD if good else BAD),('BOX',(0,0),(-1,-1),.5,MID),('VALIGN',(0,0),(-1,-1),'MIDDLE')]))
        return t
    def foot(c,d):
        c.saveState();c.setFont('Helvetica',6.1);c.setFillColor(GRAY);c.drawString(.5*inch,.28*inch,f'{MODEL_VERSION} | Model details preserved in source JSON');c.drawRightString(8*inch,.28*inch,'FSFFL');c.restoreState()
    doc=SimpleDocTemplate(str(out),pagesize=letter,leftMargin=.48*inch,rightMargin=.48*inch,topMargin=.38*inch,bottomMargin=.42*inch)
    story=[P('FSFFL TRADE DECISION REPORT','T19'),P(f"{team_name(r,cur)} | Send: {names(cur.get('outgoing_asset_names'))} | Receive: {names(cur.get('return_asset_names'))}",'S19'),Spacer(1,4)]
    vc=GREEN if v=='ACCEPT' else RED if v=='DECLINE' else NAVY
    box=Table([[Paragraph(f'MODEL VERDICT:<br/>{v}',ss['V19']),P(bottom_line(r,cur),'BL19')]],colWidths=[2*inch,5.42*inch],rowHeights=[.62*inch])
    box.setStyle(TableStyle([('BACKGROUND',(0,0),(0,0),vc),('BACKGROUND',(1,0),(1,0),LIGHT),('BOX',(0,0),(-1,-1),.7,MID),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8)]))
    story += [box,Spacer(1,5)]
    cards=[
      card('EXPECTED WINS',f"{sf(before.get('expected_wins')):.2f} -> {sf(after.get('expected_wins')):.2f}",sf(after.get('expected_wins'))>=sf(before.get('expected_wins'))),
      card('PLAYOFF ODDS',f"{sf(before.get('playoff_probability'))*100:.1f}% → {sf(after.get('playoff_probability'))*100:.1f}%",sf(after.get('playoff_probability'))>=sf(before.get('playoff_probability'))),
      card('CHAMPIONSHIP ODDS',f"{sf(before.get('championship_probability'))*100:.1f}% → {sf(after.get('championship_probability'))*100:.1f}%",sf(after.get('championship_probability'))>=sf(before.get('championship_probability'))),
      card(label('strategic_value_delta').upper(),f"{sf(st.get('strategic_value_delta')):+,.0f}",sf(st.get('strategic_value_delta'))>=0),
      card(label('market_dynasty_delta').upper(),f"{sf(st.get('market_dynasty_delta')):+,.0f}",sf(st.get('market_dynasty_delta'))>=0),
      card(label('liquidity_value_delta').upper(),f"{sf(st.get('liquidity_value_delta')):+,.0f}",sf(st.get('liquidity_value_delta'))>=0),
    ]
    grid=Table([cards[:3],cards[3:]],colWidths=[2.47*inch]*3,rowHeights=[.56*inch,.56*inch]);grid.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),1),('RIGHTPADDING',(0,0),(-1,-1),1)]))
    story += [grid,Spacer(1,5),P('WHY THIS DOES OR DOES NOT MAKE SENSE','H19'),P(why(r,cur))]
    br=cur.get('buyer_rationality') or {}
    if br.get('heuristic_acceptance_fit'):
        story += [Spacer(1,2),P(f"<b>Other manager:</b> {acceptance_fit(br.get('heuristic_acceptance_fit'))}. This is a fit estimate, not a literal acceptance probability.",'S19')]
    story += [P(f'POSSIBLE COUNTERS ({len(cs)})','H19')]
    if cs:
        for i,x in enumerate(cs,1):story += [P(option_text(x,i)),Spacer(1,2)]
    else:story += [P('The model did not find a worthwhile counter with this owner.')]
    story += [P(f'OTHER TRADE OPTIONS ({len(ms)})','H19')]
    if ms:
        for i,x in enumerate(ms,1):story += [P(option_text(x,i,True)),Spacer(1,2)]
    else:story += [P('No outside trade option clearly beat the current choice.')]
    story += [P('WHAT TO DO NEXT','H19'),P(sequence(r)),
              P('WHAT COULD CHANGE THE ANSWER','H19'),P(what_could_change_answer(r,cur)),
              Spacer(1,3),P("How to read the value numbers: long-term trade value is league-wide dynasty value; overall franchise impact is the model's bottom-line judgment for this specific roster after winning chances, future value, roster fit and flexibility are considered together.",'S19')]
    doc.build(story,onFirstPage=foot,onLaterPages=foot)

def main():
    a=argparse.ArgumentParser();a.add_argument('--input',required=True);a.add_argument('--output',required=True);x=a.parse_args();r=json.loads(Path(x.input).read_text());render(r,Path(x.output));print(json.dumps({'renderer_model_version':MODEL_VERSION,'source_model_version':r.get('model_version'),'pdf':x.output},indent=2))
if __name__=='__main__':main()
