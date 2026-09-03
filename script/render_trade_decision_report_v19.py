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
from reporting import label, acceptance_fit, action, magnitude_word, competitive_context, roster_change_context, position_need_change_chart, probability_change_chart

MODEL_VERSION='FSFFL-Trade-Decision-Report-1.12'
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


def decision_channel(row,name):
    attr=row.get('decision_attribution') or {}
    for item in attr.get('channels') or []:
        if str(item.get('channel') or '')==str(name):
            return item
    return {}

def overall_decision_value(row):
    attr=row.get('decision_attribution') or {}
    return sf(attr.get('final_shared_decision_utility'),sf(row.get('shared_decision_utility_score')))

def future_asset_value(row):
    return sf(decision_channel(row,'future').get('primitive_value'))

def package_prior_profile(row):
    attr=row.get('decision_attribution') or {}
    scores=attr.get('package_concentration_prior_scores') or {}
    return {
        'mild':scores.get('mild'),
        'center':scores.get('center'),
        'strong':scores.get('strong'),
        'robustness':attr.get('package_concentration_prior_range_decision_robustness'),
    }

def package_robustness_text(row):
    p=package_prior_profile(row)
    r=str(p.get('robustness') or '')
    if r=='SENSITIVE_TO_PRIOR_RANGE':
        return "Package-value uncertainty is material: the overall decision changes sign somewhere between the mild and strong governed assumptions."
    if r=='ROBUST_POSITIVE_ACROSS_PRIOR_RANGE':
        return "The overall decision remains positive across the full governed package-value range."
    if r=='ROBUST_NEGATIVE_ACROSS_PRIOR_RANGE':
        return "The overall decision remains negative across the full governed package-value range."
    return ''

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
    sim=cur.get('simulation') or {}
    d=sim.get('focus_delta') or {}
    st=sim.get('strategic') or {}
    title=sf(d.get('championship_probability'))
    wins=sf(d.get('expected_wins'))
    raw_dyn=sf(st.get('market_dynasty_delta'))
    future=future_asset_value(cur)
    overall=overall_decision_value(cur)
    liq=sf(st.get('liquidity_value_delta'))
    parts=[]

    if title >= .02:
        parts.append("This is a meaningful win-now improvement, not a cosmetic roster move: the simulation shows a material increase in championship equity.")
    elif title >= .005 or wins >= .10:
        parts.append("The move improves the current-season outlook, though the competitive gain is more incremental than transformative.")
    elif title <= -.01 or wins <= -.10:
        parts.append("The move weakens the current-season outlook enough that the return must compensate elsewhere.")

    if raw_dyn > 0 and future < 0:
        parts.append(
            "Raw additive dynasty market value looks positive, but the authoritative Future Asset Value turns negative after accounting for package concentration and any separate roster-legalization effects. "
            "In plain English, several smaller assets do not fully substitute for the concentrated value being surrendered."
        )
    elif raw_dyn < 0 and future > 0:
        parts.append(
            "Raw additive dynasty market value looks negative, but the authoritative Future Asset Value is positive after the governed package treatment and separate non-trade effects are applied."
        )
    elif future <= -500:
        parts.append("Future Asset Value is a major cost of the trade after the package is valued as a package rather than as a simple sum.")
    elif future >= 500:
        parts.append("Future Asset Value is a meaningful strength of the trade even after the package-concentration adjustment.")

    if liq > 0 and future < 0:
        parts.append("The deal may add moveability while still losing Future Asset Value; those are different economic questions and are not counted as the same thing.")

    if overall <= -75 and (title > 0 or wins > 0):
        parts.append("That creates a real tension between improving the current roster and paying more than the model considers worthwhile overall.")
    elif overall >= 75:
        parts.append("The authoritative current and future effects combine into a positive overall decision value.")
    else:
        parts.append("The overall trade-off is close enough that roster fit, uncertainty and available alternatives deserve extra weight in the decision.")

    pr=package_robustness_text(cur)
    if pr:
        parts.append(pr)

    comp=competitive_context(r.get('focus_user_id'))
    if comp:
        parts.append(comp)

    rc=roster_change_context(sim.get('roster_diagnosis'))
    if rc:
        parts.append(rc)

    sn=synergy_note(cur,r.get('focus_user_id'))
    if sn:
        parts.append(sn)
    return " ".join(parts)

def what_could_change_answer(r,cur):
    action_code=str(r.get('recommended_next_action') or '')
    cs=r.get('suggested_counteroffers') or []
    ms=r.get('market_sweep_alternatives') or []
    if action_code=='ACCEPT_NOW':
        if cs or ms:
            return "The answer would change if one of the stronger alternatives becomes genuinely available on comparable terms; otherwise the current offer is the best actionable choice the model found."
        picks=r.get('future_pick_outlook') or []
        if picks:
            p=picks[0]
            return f"The answer would become less attractive if {clean(p.get('name'))} projects earlier than the current {str(p.get('post_trade_projected_tier') or '').upper()} range, if the incoming player's expected role falls, or if a required roster cut becomes more expensive than modeled."
        return "The answer would change if the price increases, a required roster cut becomes more expensive than modeled, or new player information materially changes the short- or long-term outlook."
    if action_code=='SHOP_BEFORE_ACCEPTING':
        return "If the better alternatives are not actually available, the current offer becomes much more attractive. The recommendation is to test the market, not to reject a reasonable deal automatically."
    if action_code=='COUNTER_CURRENT_OFFEROR':
        if (r.get('offer_context') or {}).get('direction')=='INCOMING_OFFER':
            if ms:
                return "If the counter is rejected, keep the original incoming offer as the fallback if it is still available, then compare that fallback with any stronger outside option rather than countering indefinitely."
            return "If the counter is rejected, the original incoming offer remains the fallback if it is still available. The recommendation is to try for better terms once, not to lose a beneficial deal by countering indefinitely."
        return "If the other manager will not improve the return, compare the original offer directly with the strongest outside option rather than countering indefinitely."
    if action_code=='DECLINE':
        return "The answer would change if the return improves enough to close the value or winning-impact gap, or if this team's competitive window changes materially."
    return "A clearer edge in either current-season winning value or long-term roster value could change the recommendation."

def value_metric_explanation(r,cur):
    ctx=(r.get('value_metric_context') or {})
    future_ctx=ctx.get('future_asset_value') or {}
    raw_ctx=ctx.get('raw_additive_dynasty_market_value') or {}
    pkg=ctx.get('package_concentration') or {}
    liq=(ctx.get('incremental_asset_liquidity') or {})
    delta=sf(liq.get('delta'))
    received=liq.get('received_components') or []
    sent=liq.get('sent_components') or []
    rec=max(received,key=lambda x:sf(x.get('incremental_liquidity_contribution')),default={})
    snd=max(sent,key=lambda x:sf(x.get('incremental_liquidity_contribution')),default={})
    future=sf(future_ctx.get('value'),future_asset_value(cur))
    raw=sf(raw_ctx.get('value'),sf(((cur.get('simulation') or {}).get('strategic') or {}).get('market_dynasty_delta')))
    parts=[
        f"<b>Future Asset Value</b> is {future:+,.0f}. This is the long-term value actually used by the decision model.",
        f"<b>Raw Additive Market Reference</b> is {raw:+,.0f}. It is shown for context, but it is not the final future-value input when a multi-asset package transform applies.",
    ]
    if pkg.get('applied'):
        raw_pkg=sf(pkg.get('raw_trade_package_future_value'))
        eff_pkg=sf(pkg.get('package_effective_trade_future_value'))
        nontrade=sf(pkg.get('non_trade_future_value_preserved'))
        parts.append(
            f"For the negotiated assets, raw additive package value is {raw_pkg:+,.0f} and package-adjusted value is {eff_pkg:+,.0f}; "
            f"separate non-trade future effects contribute {nontrade:+,.0f} and are preserved once."
        )
    p=(pkg.get('prior') or {})
    if p.get('robustness')=='SENSITIVE_TO_PRIOR_RANGE':
        parts.append(
            f"<b>Important:</b> mild/center/strong overall values are {sf(p.get('mild_score')):+,.0f}, "
            f"{sf(p.get('center_score')):+,.0f}, and {sf(p.get('strong_score')):+,.0f}. "
            "The decision is sensitive to the provisional package assumption."
        )
    elif p.get('robustness') in {'ROBUST_POSITIVE_ACROSS_PRIOR_RANGE','ROBUST_NEGATIVE_ACROSS_PRIOR_RANGE'}:
        parts.append(
            f"Mild/center/strong overall values are {sf(p.get('mild_score')):+,.0f}, "
            f"{sf(p.get('center_score')):+,.0f}, and {sf(p.get('strong_score')):+,.0f}; the sign is stable across the governed package range."
        )
    parts.append("<b>Incremental Asset Liquidity</b> is separate: it measures additional moveability beyond value already represented elsewhere when that residual channel is authorized.")
    if rec or snd:
        parts.append(
            f"In this trade the incremental-liquidity diagnostic is {delta:+,.0f}; "
            f"{clean(rec.get('name') or 'incoming assets')} contributes about {sf(rec.get('incremental_liquidity_contribution')):,.0f} "
            f"versus about {sf(snd.get('incremental_liquidity_contribution')):,.0f} from {clean(snd.get('name') or 'the sent non-pick assets')}."
        )
    if any(x.get('basis')=='PICK_LIQUIDITY_ALREADY_EMBEDDED_IN_MARKET_VALUE' for x in sent+received):
        parts.append("Future picks receive no separate moveability bonus here because counting the same liquidity again would double count it.")
    return " ".join(parts)

def pick_outlook_text(row):
    name=clean(row.get('name'))
    owner=clean(row.get('original_owner_team') or 'original owner')
    tier=str(row.get('post_trade_projected_tier') or 'unknown').upper()
    rng=clean(row.get('post_trade_tier_slot_range') or '')
    ew=sf(row.get('post_trade_expected_wins'))
    pre=str(row.get('pre_trade_projected_tier') or 'unknown').upper()
    changed=bool(row.get('trade_changes_original_owner_projection'))
    movement=(f"The trade moves the directional tier from {pre} to {tier}." if changed and pre!=tier else
              "The trade changes the owner's simulated outlook but not the directional pick tier." if changed else
              "The original owner is not directly changed by this trade; the post-trade league ordering leaves the directional tier unchanged.")
    return (
        f"<b>{name}</b> - original owner: {owner}. Post-trade outlook: <b>{tier}</b> "
        f"({rng} directional range), with the original owner at {ew:.2f} expected wins. {movement} "
        "This is a tier/range estimate from the Simulator ordering, not an exact rookie-draft slot probability."
    )

def offeror_note(r,cur):
    oc=r.get('offer_context') or {}
    if oc.get('direction')!='INCOMING_OFFER':
        return ''
    partner=((cur.get('simulation') or {}).get('buyer_before') or {}).get('team_name') or 'The other manager'
    return (
        f"<b>Offer origin:</b> {clean(partner)} made the current offer, so willingness to the current terms is observed. "
        "That does not mean a counter will be accepted, but it is stronger local evidence than an absolute buyer-utility estimate and the model uses it to test modest target-preserving concessions."
    )

def comparison_sentence(row):
    c=row.get('comparison_to_current_offer') or {}; v=str(c.get('verdict_vs_current_offer') or 'MIXED')
    delta=sf(c.get('post_sim_score_delta_vs_current_offer'))
    if v=='BETTER': return f"The model prefers this to the current offer."
    if v=='WORSE': return f"The model prefers the current offer."
    return "This is a different trade-off, but not clearly better or worse than the current offer."

def option_text(row,i,market=False):
    sim=row.get('simulation') or {}; d=sim.get('focus_delta') or {}
    prefix=f"<b>{i}. "
    if market: prefix+=f"{clean(row.get('buyer_team'))}: "
    prefix+=f"Send {names(row.get('outgoing_asset_names'))}; receive {names(row.get('return_asset_names'))}.</b>"
    overall=overall_decision_value(row)
    future=future_asset_value(row)
    txt=f"{prefix} Expected wins {sf(d.get('expected_wins')):+.2f}; championship odds {sf(d.get('championship_probability'))*100:+.1f} points; Future Asset Value {future:+,.0f}; overall decision value {overall:+,.0f}. {comparison_sentence(row)}"
    pr=package_robustness_text(row)
    if pr: txt+=f" {pr}"
    fit=row.get('acceptance_likelihood')
    if fit: txt+=f" {acceptance_fit(fit)}."
    return txt

def sequence(r):
    a=str(r.get('recommended_next_action') or ''); cs=r.get('suggested_counteroffers') or []; ms=r.get('market_sweep_alternatives') or []
    if a=='ACCEPT_NOW':
        oc=r.get('offer_context') or {}
        tested=int((r.get('candidate_counts') or {}).get('offeror_concession_candidates_simulated') or 0)
        if oc.get('direction')=='INCOMING_OFFER' and tested:
            return 'Accept the original offer if it is still available. The model also tested whether the offeror could be pressed for a smaller price; no superior counter survived the final decision comparison.'
        return 'Accept if the offer is still available. Do not add more unless the other manager rejects it.'
    if a=='SHOP_BEFORE_ACCEPTING':return 'Keep this offer alive while checking the strongest alternatives. If none is actually available, coming back to this deal is reasonable.'
    if a=='COUNTER_CURRENT_OFFEROR':return 'Lead with Counter 1. Only move to another structure if the first counter is rejected.'
    if a=='DECLINE' and cs:return 'Decline the current version and send Counter 1 instead.'
    if a=='DECLINE' and ms:return 'Decline and pursue the strongest outside option.'
    return 'Hold rather than forcing a deal.'

def render(r,out):
    cur=r.get('current_offer_evaluation') or {}; sim=cur.get('simulation') or {}; before=sim.get('focus_before') or {}; after=sim.get('focus_after') or {}; st=sim.get('strategic') or {}; overall=overall_decision_value(cur); future=future_asset_value(cur)
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
      card('OVERALL DECISION VALUE',f"{overall:+,.0f}",overall>=0),
      card('FUTURE ASSET VALUE',f"{future:+,.0f}",future>=0),
      card('INCREMENTAL ASSET LIQUIDITY',f"{sf(st.get('liquidity_value_delta')):+,.0f}",sf(st.get('liquidity_value_delta'))>=0),
    ]
    grid=Table([cards[:3],cards[3:]],colWidths=[2.47*inch]*3,rowHeights=[.56*inch,.56*inch]);grid.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),1),('RIGHTPADDING',(0,0),(-1,-1),1)]))
    profile=r.get('recommendation_profile') or {}
    story += [grid,Spacer(1,2),
              P("Season-impact figures come from the canonical vectorized Simulator with 50,000-run final confirmation for the current offer and actionable finalists.",'S19'),
              P(f"<b>Decision profile:</b> {clean(profile.get('label') or '')}. {clean(profile.get('basis') or '')}",'S19'),
              Spacer(1,5),P('ANALYST VIEW','H19'),P(why(r,cur)),
              P('HOW TO READ THE VALUE SIGNALS','H19'),P(value_metric_explanation(r,cur),'S19')]
    visuals=[x for x in (
        position_need_change_chart(sim.get('roster_diagnosis')),
        probability_change_chart(before,after),
    ) if x is not None]
    if visuals:
        story += [P('AT A GLANCE','H19')]
        if len(visuals)==2:
            vt=Table([[visuals[0],visuals[1]]],colWidths=[3.70*inch,3.70*inch])
            vt.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0)]))
            story += [vt]
        else:
            story += visuals
    on=offeror_note(r,cur)
    if on:
        story += [Spacer(1,2),P(on,'S19')]
    else:
        br=cur.get('buyer_rationality') or {}
        if br.get('heuristic_acceptance_fit'):
            story += [Spacer(1,2),P(f"<b>Other manager:</b> {acceptance_fit(br.get('heuristic_acceptance_fit'))}. This is a fit estimate, not a literal acceptance probability.",'S19')]
    picks=r.get('future_pick_outlook') or []
    if picks:
        story += [P('FUTURE PICK OUTLOOK','H19')]
        for x in picks:
            story += [P(pick_outlook_text(x),'S19'),Spacer(1,2)]
    story += [P(f'POSSIBLE COUNTERS ({len(cs)})','H19')]
    if cs:
        for i,x in enumerate(cs,1):story += [P(option_text(x,i)),Spacer(1,2)]
    else:
        tested=int((r.get('candidate_counts') or {}).get('offeror_concession_candidates_simulated') or 0)
        if (r.get('offer_context') or {}).get('direction')=='INCOMING_OFFER' and tested:
            story += [P(f'The model tested {tested} target-preserving concession structure(s) around the offeror\'s observed terms, but none was clearly better after the final comparison.')]
        else:
            story += [P('The model did not find a worthwhile counter with this owner.')]
    story += [P(f'OTHER TRADE OPTIONS ({len(ms)})','H19')]
    if ms:
        for i,x in enumerate(ms,1):story += [P(option_text(x,i,True)),Spacer(1,2)]
    else:story += [P('No outside trade option clearly beat the current choice.')]
    story += [P('WHAT TO DO NEXT','H19'),P(sequence(r)),
              P('WHAT COULD CHANGE THE ANSWER','H19'),P(what_could_change_answer(r,cur)),
              Spacer(1,3),P("How to read the value numbers: Future Asset Value is the authoritative long-term input after any governed package-concentration adjustment and separate non-trade future effects. Raw additive dynasty market value is reference context only for multi-asset packages. Overall Decision Value is the single four-channel Shared Decision Utility used for the recommendation; package-prior sensitivity is shown when it could matter.",'S19')]
    doc.build(story,onFirstPage=foot,onLaterPages=foot)

def main():
    a=argparse.ArgumentParser();a.add_argument('--input',required=True);a.add_argument('--output',required=True);x=a.parse_args();r=json.loads(Path(x.input).read_text());render(r,Path(x.output));print(json.dumps({'renderer_model_version':MODEL_VERSION,'source_model_version':r.get('model_version'),'pdf':x.output},indent=2))
if __name__=='__main__':main()
