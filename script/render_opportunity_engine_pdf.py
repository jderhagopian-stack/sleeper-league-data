#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle, PageBreak
from fsffl_report_style import *

MODEL_VERSION='FSFFL-Opportunity-Engine-Report-1.2'

def load(p): return json.loads(Path(p).read_text(encoding='utf-8'))
def desc(m): return str((m or {}).get('description') or 'None identified')
def score(m): return safe_float((m or {}).get('team_improvement_score'))
def sim_delta(m,key): return safe_float(((m or {}).get('simulation') or {}).get('focus_delta',{}).get(key))
def asset_price(a): return safe_float(a.get('market_dynasty') if a.get('market_dynasty') is not None else a.get('fsffl_value'))
def package_price(v): return safe_float((v or {}).get('package_market_value_coordinate'))
def target_name(pf):
    t=(pf or {}).get('target') or {}
    return str(t.get('name') or t.get('asset_id') or 'Target')

def td_decision(board):
    reviews=board.get('trade_decision_reviews') or []
    if not reviews: return 'N/A'
    td=(reviews[0].get('trade_decision') or {})
    return str(td.get('recommended_next_action') or td.get('decision') or td.get('recommendation') or td.get('action') or 'REVIEWED')

def row_table(s, rows, widths, header=True):
    t=Table(rows,colWidths=widths,repeatRows=1 if header else 0,hAlign='LEFT')
    cmds=[('VALIGN',(0,0),(-1,-1),'TOP'),('GRID',(0,0),(-1,-1),.35,MID_GRAY),('LEFTPADDING',(0,0),(-1,-1),5),('RIGHTPADDING',(0,0),(-1,-1),5),('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),4)]
    if header: cmds += [('BACKGROUND',(0,0),(-1,0),LIGHT_GRAY)]
    t.setStyle(TableStyle(cmds))
    return t


def package_prior_text(row):
    pc=(row or {}).get('package_prior_confidence') or {}
    cls=pc.get('classification')
    if cls=='PROVISIONAL_PARAMETER_SENSITIVE':
        return (
            f"<br/><b>Package-prior confidence:</b> SENSITIVE; mild/center/strong "
            f"{safe_float(pc.get('mild_score')):+,.0f} / {safe_float(pc.get('center_score')):+,.0f} / "
            f"{safe_float(pc.get('strong_score')):+,.0f}. Confidence only; score/actionability unchanged."
        )
    if cls=='ROBUST_ACROSS_GOVERNED_PRIOR_RANGE':
        return (
            f"<br/><b>Package-prior confidence:</b> ROBUST across mild/center/strong "
            f"({safe_float(pc.get('mild_score')):+,.0f} / {safe_float(pc.get('center_score')):+,.0f} / "
            f"{safe_float(pc.get('strong_score')):+,.0f})."
        )
    return ''

def action_box(s,label,row,empty_text,tone='blue'):
    if not row:
        body=P(s,empty_text,'FS_Body')
    else:
        nf=(row.get('negotiation_frontier') or {})
        posture=nf.get('negotiation_posture')
        extra=f"<br/><b>Next step:</b> {str(posture).replace('_',' ')}" if posture else ''
        body=P(s,f"<b>{desc(row)}</b><br/>Overall Decision Value: <b>{score(row):+,.1f}</b>{extra}{package_prior_text(row)}",'FS_Body')
    bg={'blue':LIGHT_BLUE,'green':LIGHT_GREEN,'gold':LIGHT_GOLD,'gray':LIGHT_GRAY}.get(tone,LIGHT_GRAY)
    t=Table([[P(s,label,'FS_CardLabel'),body]],colWidths=[1.35*inch,6.09*inch])
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(0,0),NAVY),('TEXTCOLOR',(0,0),(0,0),WHITE),('BACKGROUND',(1,0),(1,0),bg),('BOX',(0,0),(-1,-1),.6,MID_GRAY),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6)]))
    return t

def next_action_text(b):
    actionable=b.get('best_actionable_trade') or {}
    explore=b.get('best_trade_to_explore') or {}
    if actionable:
        target=(actionable.get('target') or {}).get('asset_id')
        pfs=(b.get('negotiation_frontier') or {}).get('target_price_frontiers') or []
        pf=next((x for x in pfs if (x.get('target') or {}).get('asset_id')==target and x.get('price_overlap_exists')),None)
        opener=(pf or {}).get('opening_package') or {}
        ceiling=(pf or {}).get('rational_focal_ceiling') or {}
        if opener:
            return f"Open with {opener.get('description')}. Do not move beyond the governed focal ceiling ({ceiling.get('description') or 'current positive-utility boundary'}) without new information."
        return f"Open negotiations on {desc(actionable)} and stay inside the evaluated positive-utility deal zone."
    if explore:
        return f"Continue package discovery for {desc(explore)} before treating it as executable."
    gaps=b.get('high_impact_price_gap_targets') or []
    if gaps:
        closest=gaps[0]
        return f"Do not force a trade. The current board has no mutually beneficial evaluated package. Revisit {target_name(closest)} only if the seller price or our roster economics change; keep searching lower-cost alternatives."
    return "Hold the roster and continue scanning for positive-utility waivers or trades that clear both sides."

def render(board_path, output):
    b=load(board_path); s=styles(); output=Path(output); output.parent.mkdir(parents=True,exist_ok=True)
    doc=SimpleDocTemplate(str(output),pagesize=letter,leftMargin=.48*inch,rightMargin=.48*inch,topMargin=.42*inch,bottomMargin=.45*inch,title=f"FSFFL Opportunity Engine - {b.get('team_name')}")
    best=b.get('best_move_available') or {}
    actionable=b.get('best_actionable_trade') or {}
    explore=b.get('best_trade_to_explore') or {}
    gaps=b.get('high_impact_price_gap_targets') or []
    port=(b.get('portfolio_optimization') or {}).get('best_portfolio') or {}
    td=td_decision(b)
    story=[
        P(s,'FSFFL OPPORTUNITY ENGINE','FS_Title'),
        P(s,f"{b.get('team_name')} | Team state: {str(b.get('team_state') or '').replace('_',' ')} | {b.get('model_version')}",'FS_Sub'),
        Spacer(1,6)
    ]

    tone=LIGHT_GREEN if best.get('channel')!='HOLD' else LIGHT_GRAY
    banner=Table([[P(s,'CURRENT DECISION','FS_WhiteLabel'),P(s,f"<b>{desc(best)}</b><br/>Overall Decision Value: <b>{score(best):+,.1f}</b>{package_prior_text(best)}",'FS_Body')]],colWidths=[1.35*inch,6.09*inch])
    banner.setStyle(TableStyle([('BACKGROUND',(0,0),(0,0),NAVY),('BACKGROUND',(1,0),(1,0),tone),('BOX',(0,0),(-1,-1),.7,MID_GRAY),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7)]))
    story += [banner,Spacer(1,6)]

    cards=[
        kpi_card(s,'GM3 Improvement',f'{score(best):+,.0f}','positive' if score(best)>0 else 'neutral',1.78*inch),
        kpi_card(s,'Expected Wins',f'{sim_delta(best,"expected_wins"):+.2f}','positive' if sim_delta(best,"expected_wins")>0 else 'neutral',1.78*inch),
        kpi_card(s,'Championship Odds',f'{sim_delta(best,"championship_probability")*100:+.1f}%','positive' if sim_delta(best,"championship_probability")>0 else 'neutral',1.78*inch),
        kpi_card(s,'Playoff Odds',f'{sim_delta(best,"playoff_probability")*100:+.1f}%','positive' if sim_delta(best,"playoff_probability")>0 else 'neutral',1.78*inch)
    ]
    ct=Table([cards],colWidths=[1.86*inch]*4)
    ct.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),1),('RIGHTPADDING',(0,0),(-1,-1),1)]))
    story += [ct,Spacer(1,7)]

    story += [P(s,'PURSUE NOW','FS_Section'),action_box(s,'PURSUE',actionable,'No generated trade currently clears both seller utility and positive focal GM3 utility.','green'),Spacer(1,6)]
    story += [P(s,'EXPLORE PRICE','FS_Section'),action_box(s,'EXPLORE',explore,'No incomplete-but-economically-promising frontier is currently promoted.','gold'),Spacer(1,6)]

    story += [P(s,'PRICE GAP TOO WIDE','FS_Section')]
    if gaps:
        rows=[[P(s,'TARGET','FS_CardLabel'),P(s,'OUR CEILING','FS_CardLabel'),P(s,'SELLER FLOOR','FS_CardLabel'),P(s,'OBSERVED GAP','FS_CardLabel')]]
        for pf in gaps[:5]:
            ce=pf.get('rational_focal_ceiling') or {}; fl=pf.get('seller_clearing_floor') or {}
            gap=(package_price(fl)-package_price(ce)) if ce and fl else None
            rows.append([
                P(s,target_name(pf),'FS_Body'),
                P(s,str(ce.get('description') or 'No positive price found'),'FS_Small'),
                P(s,str(fl.get('description') or 'Not found'),'FS_Small'),
                P(s,f"{gap:+,.0f}" if gap is not None else 'n/a','FS_Body')
            ])
        story += [row_table(s,rows,[1.05*inch,2.55*inch,2.55*inch,1.29*inch]),Spacer(1,3),P(s,'Observed gap is only the difference between evaluated package price coordinates. It is not an acceptance probability or a recommendation score.','FS_Small'),Spacer(1,6)]
    else:
        story += [P(s,'No high-impact target currently sits outside the mutually beneficial price zone.','FS_Body'),Spacer(1,6)]

    story += [P(s,'WHAT TO DO NEXT','FS_Section'),P(s,next_action_text(b),'FS_Body')]

    story += [PageBreak(),P(s,'RANKED OPPORTUNITY BOARD / BEST ALTERNATIVES','FS_Title'),P(s,'Only executable trades and positive-utility waiver moves appear here. Attractive but uneconomic targets stay in the price-gap section.','FS_Sub'),Spacer(1,6)]
    ranked=b.get('ranked_single_step_opportunities') or []
    rows=[[P(s,'#','FS_CardLabel'),P(s,'OPPORTUNITY','FS_CardLabel'),P(s,'CHANNEL','FS_CardLabel'),P(s,'GM3 IMPROVEMENT','FS_CardLabel')]]
    for i,m in enumerate(ranked[:10],1):
        rows.append([P(s,str(i),'FS_Body'),P(s,desc(m),'FS_Body'),P(s,str(m.get('channel') or ''),'FS_Small'),P(s,f"{score(m):+,.1f}",'FS_Body')])
    if len(rows)==1:
        rows.append([P(s,'-','FS_Body'),P(s,'No positive executable single-step move cleared the governed benchmark.','FS_Body'),P(s,'HOLD','FS_Small'),P(s,'+0.0','FS_Body')])
    story += [row_table(s,rows,[.28*inch,5.38*inch,.75*inch,1.03*inch]),Spacer(1,8)]

    views=b.get('specialized_views') or {}
    labels=[
        ('best_buy_low_candidate','Buy-low signal'),
        ('best_model_vs_market_acquisition','Model-vs-market'),
        ('best_negotiation_ready_trade','Negotiation-ready'),
        ('best_current_season_upgrade','Current-season upgrade'),
        ('best_long_term_value_move','Long-term value'),
        ('best_emerging_value_opportunity','Emerging value'),
        ('best_draft_intelligence_opportunity','Draft intelligence')
    ]
    vrows=[[P(s,'VIEW','FS_CardLabel'),P(s,'HIGHEST EXECUTABLE GOVERNED MATCH','FS_CardLabel')]]
    for k,labeltxt in labels:
        m=views.get(k)
        vrows.append([P(s,labeltxt,'FS_Body'),P(s,desc(m) if m else 'None currently executable','FS_Small')])
    story += [P(s,'SPECIALIST VIEWS','FS_Section'),row_table(s,vrows,[1.55*inch,5.89*inch]),Spacer(1,7)]

    sells=b.get('market_test_sell_high_candidates') or []
    if sells:
        sr=[[P(s,'ASSET TO MARKET-TEST','FS_CardLabel'),P(s,'BEST MODELED BUYER','FS_CardLabel'),P(s,'PREMIUM','FS_CardLabel')]]
        for x in sells[:5]:
            buyer=x.get('best_buyer') or {}
            sr.append([P(s,str(x.get('asset') or ''),'FS_Body'),P(s,str(buyer.get('buyer_team') or ''),'FS_Body'),P(s,f"{safe_float(buyer.get('premium_vs_break_glass')):+,.0f}",'FS_Body')])
        story += [P(s,'MARKET-TEST / SELL-HIGH','FS_Section'),row_table(s,sr,[3.5*inch,2.45*inch,1.49*inch]),Spacer(1,6)]

    if port:
        story += [P(s,'BEST MULTI-MOVE PORTFOLIO','FS_Section'),P(s,str(port.get('description') or ''),'FS_Body'),Spacer(1,3)]
        pt=Table([[P(s,'Portfolio GM3','FS_CardLabel'),P(s,'vs. best single','FS_CardLabel'),P(s,'Preference','FS_CardLabel')],[P(s,f"{safe_float(port.get('team_improvement_score')):+,.1f}",'FS_Body'),P(s,f"{safe_float(port.get('incremental_score_vs_best_single_step_same_precision')):+,.1f}",'FS_Body'),P(s,'PREFERRED' if port.get('preferred_to_best_single_step_on_same_gm3_utility') else 'NOT PREFERRED','FS_Body')]],colWidths=[2.48*inch]*3)
        pt.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),LIGHT_BLUE),('GRID',(0,0),(-1,-1),.35,MID_GRAY),('ALIGN',(0,0),(-1,-1),'CENTER'),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]))
        story += [pt,Spacer(1,3),P(s,'Portfolio trade legs still require Trade Decision review before execution.','FS_Small')]

    story += [PageBreak(),P(s,'EXECUTION, UNCERTAINTY & GOVERNANCE','FS_Title'),P(s,'Supporting detail. The decision pages above are the primary operating view.','FS_Sub'),Spacer(1,6)]
    pfs=((b.get('negotiation_frontier') or {}).get('target_price_frontiers') or [])[:6]
    story += [P(s,'NEGOTIATION PRICE FRONTIER','FS_Section')]
    if pfs:
        pr=[[P(s,'TARGET','FS_CardLabel'),P(s,'STATUS','FS_CardLabel'),P(s,'OPEN','FS_CardLabel'),P(s,'SELLER FLOOR','FS_CardLabel'),P(s,'OUR CEILING','FS_CardLabel')]]
        for pf in pfs:
            op=pf.get('opening_package') or {}; fl=pf.get('seller_clearing_floor') or {}; ce=pf.get('rational_focal_ceiling') or {}
            pr.append([P(s,target_name(pf),'FS_Small'),P(s,str(pf.get('status') or 'UNKNOWN').replace('_',' '),'FS_Small'),P(s,str(op.get('description') or 'none'),'FS_Small'),P(s,str(fl.get('description') or 'not found'),'FS_Small'),P(s,str(ce.get('description') or 'none'),'FS_Small')])
        story += [row_table(s,pr,[1.0*inch,1.15*inch,1.78*inch,1.78*inch,1.73*inch]),Spacer(1,4),P(s,'Seller floor uses governed counterparty utility. Our ceiling uses GM3 franchise-improvement utility. The frontier contains evaluated discrete packages only.','FS_Small'),Spacer(1,6)]
    else:
        story += [P(s,'No evaluated trade-package frontier was available for this run.','FS_Body'),Spacer(1,6)]

    rob=b.get('robustness') or {}
    enabled=bool((rob.get('best_single_step') or {}).get('enabled'))
    story += [P(s,'UNCERTAINTY / ROBUSTNESS','FS_Section'),P(s,'Independent-seed robustness diagnostics were enabled for this run.' if enabled else 'Independent-seed robustness diagnostics were disabled. Rankings are governed point estimates at the configured simulation budget, not a claim of certainty.','FS_Body'),Spacer(1,6)]

    sc=b.get('search_configuration') or {}
    story += [P(s,'SEARCH & SIMULATION BUDGET','FS_Section'),P(s,f"Trade screen: {sc.get('trade_candidates',50)} | Waiver screen: {sc.get('waiver_candidates',50)} | Legacy packages/target: {sc.get('trade_packages_per_target',8)} | Frontier packages/target: {sc.get('price_frontier_packages_per_target',18)} | Frontier targets: {sc.get('price_frontier_targets',16)} | Sims: {sc.get('quick_sims',500)} screen / {sc.get('confirm_sims',2500)} confirm. These are computational budgets, not football-value weights.",'FS_Body'),Spacer(1,6)]

    pv=b.get('prospective_validation') or {}
    story += [P(s,'PROSPECTIVE VALIDATION','FS_Section'),P(s,f"Snapshot: {pv.get('generated_at_utc') or 'recorded'}<br/>Input fingerprint: {pv.get('source_input_sha256') or 'recorded'}<br/>This recommendation can be graded later without backfilling future information.",'FS_Small'),Spacer(1,6)]

    gov=[
        "Simulator owns competitive outcomes.",
        "GM3 Team Improvement owns cross-channel franchise-improvement utility.",
        "Trade Decision owns generated-trade bilateral feasibility and negotiation policy.",
        "Behavioral Intelligence supplies evidence only; it does not create acceptance probability.",
        "Opportunity Engine searches, routes, sequences and presents; it does not create valuation or a competing recommendation score."
    ]
    story += [P(s,'GOVERNANCE','FS_Section')]+[P(s,f"- {x}",'FS_Body') for x in gov]

    doc.build(story,onFirstPage=lambda c,d: footer(c,f'{MODEL_VERSION} | Presentation-only report'),onLaterPages=lambda c,d: footer(c,f'{MODEL_VERSION} | Presentation-only report'))

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--input',required=True); ap.add_argument('--output',required=True); a=ap.parse_args(); render(a.input,a.output)

if __name__=='__main__':
    main()
