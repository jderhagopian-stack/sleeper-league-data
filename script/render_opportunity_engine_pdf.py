#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle, PageBreak
from fsffl_report_style import *
MODEL_VERSION='FSFFL-Opportunity-Engine-Report-1.0'

def load(p): return json.loads(Path(p).read_text(encoding='utf-8'))
def desc(m): return str((m or {}).get('description') or 'No opportunity identified')
def score(m): return safe_float((m or {}).get('team_improvement_score'))
def pct(v): return f'{safe_float(v)*100:.1f}%'
def sim_delta(m,key): return safe_float(((m or {}).get('simulation') or {}).get('focus_delta',{}).get(key))
def td_decision(board):
    reviews=board.get('trade_decision_reviews') or []
    if not reviews: return 'NOT REVIEWED'
    td=(reviews[0].get('trade_decision') or {})
    return str(td.get('recommended_next_action') or td.get('decision') or td.get('recommendation') or td.get('action') or 'REVIEWED')
def row_table(s, rows, widths, header=True):
    t=Table(rows,colWidths=widths,repeatRows=1 if header else 0,hAlign='LEFT')
    cmds=[('VALIGN',(0,0),(-1,-1),'TOP'),('GRID',(0,0),(-1,-1),.35,MID_GRAY),('LEFTPADDING',(0,0),(-1,-1),5),('RIGHTPADDING',(0,0),(-1,-1),5),('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),4)]
    if header: cmds += [('BACKGROUND',(0,0),(-1,0),LIGHT_GRAY)]
    t.setStyle(TableStyle(cmds)); return t

def render(board_path, output):
    b=load(board_path); s=styles(); output=Path(output); output.parent.mkdir(parents=True,exist_ok=True)
    doc=SimpleDocTemplate(str(output),pagesize=letter,leftMargin=.48*inch,rightMargin=.48*inch,topMargin=.42*inch,bottomMargin=.45*inch,title=f"FSFFL Opportunity Engine - {b.get('team_name')}")
    best=b.get('best_move_available') or {}; besttrade=b.get('best_trade_opportunity') or best; port=(b.get('portfolio_optimization') or {}).get('best_portfolio') or {}; td=td_decision(b)
    story=[P(s,'FSFFL OPPORTUNITY ENGINE','FS_Title'),P(s,f"{b.get('team_name')} | Team state: {str(b.get('team_state') or '').replace('_',' ')} | {b.get('model_version')}",'FS_Sub'),Spacer(1,6)]
    banner=Table([[P(s,'TOP ACTION','FS_WhiteLabel'),P(s,f"<b>{desc(best)}</b><br/>GM3 franchise-improvement score: <b>+{score(best):,.1f}</b> | Trade Decision: <b>{td}</b>",'FS_Body')]],colWidths=[1.15*inch,6.29*inch])
    banner.setStyle(TableStyle([('BACKGROUND',(0,0),(0,0),NAVY),('BACKGROUND',(1,0),(1,0),LIGHT_GREEN if td in {'ACCEPT_NOW','ACCEPT'} else LIGHT_GOLD),('BOX',(0,0),(-1,-1),.7,MID_GRAY),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7)])); story += [banner,Spacer(1,7)]
    cards=[kpi_card(s,'GM3 Improvement',f'+{score(best):,.0f}','positive',1.78*inch),kpi_card(s,'Expected Wins',f'+{sim_delta(besttrade,"expected_wins"):.2f}','positive',1.78*inch),kpi_card(s,'Championship Odds',f'+{pct(sim_delta(besttrade,"championship_probability"))}','positive',1.78*inch),kpi_card(s,'Playoff Odds',f'+{pct(sim_delta(besttrade,"playoff_probability"))}','positive',1.78*inch)]
    ct=Table([cards],colWidths=[1.86*inch]*4); ct.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),1),('RIGHTPADDING',(0,0),(-1,-1),1)])); story += [ct,Spacer(1,7)]
    story += [P(s,'GM MORNING BRIEFING','FS_Section'),P(s,f"The engine's strongest reviewed single move is {desc(best)}. This is the only generated trade in this run that has been routed through Trade Decision, which returned {td}. The multi-move portfolio below scores higher on GM3's governed franchise-improvement utility, but its individual trade legs still require Trade Decision review before execution advice.",'FS_Body'),Spacer(1,6)]
    if port:
        story += [P(s,'BEST MULTI-MOVE PORTFOLIO','FS_Section'),P(s,str(port.get('description') or ''),'FS_Body'),Spacer(1,3)]
        pt=Table([[P(s,'Portfolio GM3 score','FS_CardLabel'),P(s,'vs. best single move','FS_CardLabel'),P(s,'Preference','FS_CardLabel')],[P(s,f"+{safe_float(port.get('team_improvement_score')):,.1f}",'FS_Body'),P(s,f"+{safe_float(port.get('incremental_score_vs_best_single_step_same_precision')):,.1f}",'FS_Body'),P(s,'PREFERRED' if port.get('preferred_to_best_single_step_on_same_gm3_utility') else 'NOT PREFERRED','FS_Body')]],colWidths=[2.48*inch]*3)
        pt.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),LIGHT_BLUE),('GRID',(0,0),(-1,-1),.35,MID_GRAY),('ALIGN',(0,0),(-1,-1),'CENTER'),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)])); story += [pt,Spacer(1,4),P(s,'Execution guardrail: recheck live ownership/availability immediately before acting. Portfolio trade legs remain subject to Trade Decision review.','FS_Small')]
    story += [PageBreak(),P(s,'RANKED OPPORTUNITY BOARD','FS_Title'),P(s,'Governed order from GM3 Team Improvement; Opportunity Engine searches and presents rather than rescoring.','FS_Sub'),Spacer(1,6)]
    rows=[[P(s,'#','FS_CardLabel'),P(s,'OPPORTUNITY','FS_CardLabel'),P(s,'GM3 IMPROVEMENT','FS_CardLabel'),P(s,'NEGOTIATION FIT','FS_CardLabel')]]
    for i,m in enumerate((b.get('ranked_single_step_opportunities') or [])[:10],1): rows.append([P(s,str(i),'FS_Body'),P(s,desc(m),'FS_Body'),P(s,f"+{score(m):,.1f}",'FS_Body'),P(s,str(m.get('acceptance_fit') or 'n/a').replace('_',' '),'FS_Small')])
    story += [row_table(s,rows,[.28*inch,5.15*inch,1.05*inch,.96*inch]),Spacer(1,8)]
    views=b.get('specialized_views') or {}; labels=[('best_buy_low_candidate','Buy-low signal'),('best_model_vs_market_acquisition','Model-vs-market'),('best_negotiation_ready_trade','Negotiation-ready'),('best_current_season_upgrade','Current-season upgrade'),('best_long_term_value_move','Long-term value'),('best_emerging_value_opportunity','Emerging value'),('best_draft_intelligence_opportunity','Draft intelligence')]
    vrows=[[P(s,'VIEW','FS_CardLabel'),P(s,'HIGHEST GOVERNED MATCH','FS_CardLabel')]]
    for k,labeltxt in labels:
        m=views.get(k); vrows.append([P(s,labeltxt,'FS_Body'),P(s,desc(m) if m else 'None identified','FS_Small')])
    story += [P(s,'SPECIALIST VIEWS','FS_Section'),row_table(s,vrows,[1.55*inch,5.89*inch]),Spacer(1,7)]
    sells=b.get('market_test_sell_high_candidates') or []; sr=[[P(s,'ASSET TO MARKET-TEST','FS_CardLabel'),P(s,'BEST MODELED BUYER','FS_CardLabel'),P(s,'PREMIUM','FS_CardLabel')]]
    for x in sells[:5]:
        buyer=x.get('best_buyer') or {}; sr.append([P(s,str(x.get('asset') or ''),'FS_Body'),P(s,str(buyer.get('buyer_team') or ''),'FS_Body'),P(s,f"+{safe_float(buyer.get('premium_vs_break_glass')):,.0f}",'FS_Body')])
    if sells: story += [P(s,'MARKET-TEST / SELL-HIGH CANDIDATES','FS_Section'),row_table(s,sr,[3.5*inch,2.45*inch,1.49*inch])]
    story += [PageBreak(),P(s,'EXECUTION, UNCERTAINTY & GOVERNANCE','FS_Title'),P(s,'Decision-support controls for interpreting this board correctly.','FS_Sub'),Spacer(1,6)]
    pfs=((b.get('negotiation_frontier') or {}).get('target_price_frontiers') or [])[:4]
    story += [P(s,'NEGOTIATION PRICE FRONTIER','FS_Section')]
    if pfs:
        pr=[[P(s,'TARGET','FS_CardLabel'),P(s,'STATUS','FS_CardLabel'),P(s,'OPEN','FS_CardLabel'),P(s,'SELLER FLOOR','FS_CardLabel'),P(s,'OUR CEILING','FS_CardLabel')]]
        for pf in pfs:
            target=pf.get('target') or {}; op=pf.get('opening_package') or {}; fl=pf.get('seller_clearing_floor') or {}; ce=pf.get('rational_focal_ceiling') or {}
            pr.append([P(s,str(target.get('name') or target.get('asset_id') or 'Target'),'FS_Small'),P(s,str(pf.get('status') or 'UNKNOWN').replace('_',' '),'FS_Small'),P(s,str(op.get('description') or 'none'),'FS_Small'),P(s,str(fl.get('description') or 'not found'),'FS_Small'),P(s,str(ce.get('description') or 'none'),'FS_Small')])
        story += [row_table(s,pr,[1.05*inch,1.25*inch,1.75*inch,1.75*inch,1.64*inch]),Spacer(1,4),P(s,'Discrete frontier over packages actually evaluated by GM3. Seller floor uses governed counterparty utility; our ceiling uses GM3 franchise-improvement utility. This is not an acceptance probability or an invented elite-player premium.','FS_Small'),Spacer(1,6)]
    else:
        story += [P(s,'No evaluated trade-package frontier was available for this run.','FS_Body'),Spacer(1,6)]
    rob=b.get('robustness') or {}; enabled=bool((rob.get('best_single_step') or {}).get('enabled'))
    story += [P(s,'UNCERTAINTY / ROBUSTNESS','FS_Section'),P(s,'Independent-seed robustness diagnostics were enabled for this run.' if enabled else 'Independent-seed robustness diagnostics were disabled for this production run. The ranking is therefore the governed point estimate from the configured search/simulation budget, not a claim that the top option dominates under every plausible simulation seed.','FS_Body'),Spacer(1,6)]
    sc=b.get('search_configuration') or {}; story += [P(s,'SEARCH & SIMULATION BUDGET','FS_Section'),P(s,f"Trade screen: {sc.get('trade_candidates',50)} | Waiver screen: {sc.get('waiver_candidates',50)} | Packages/target: {sc.get('trade_packages_per_target',8)} | Single-step sims: {sc.get('quick_sims',500)} screen / {sc.get('confirm_sims',2500)} confirm | Portfolio max moves: {sc.get('portfolio_max_moves',3)} | Beam width: {sc.get('portfolio_beam_width',10)}. These are computational search budgets, not valuation weights.",'FS_Body'),Spacer(1,6)]
    pv=b.get('prospective_validation') or {}; story += [P(s,'PROSPECTIVE VALIDATION','FS_Section'),P(s,f"Snapshot: {pv.get('generated_at_utc') or 'recorded'}<br/>Input fingerprint: {pv.get('source_input_sha256') or 'recorded'}<br/>This board is intended to be graded later without backfilling future information into the original recommendation.",'FS_Small'),Spacer(1,6)]
    gov=["Opportunity Engine owns search, routing, sequencing and explanation - not player/pick valuation or a competing utility.","GM3 Team Improvement owns cross-channel franchise-improvement and portfolio scores.","Simulator remains authoritative for competitive outcomes.","Trade Decision remains authoritative for generated-trade execution review and negotiation policy.","Behavioral Intelligence supplies evidence, not acceptance probability."]
    story += [P(s,'GOVERNANCE','FS_Section')]+[P(s,f"- {x}",'FS_Body') for x in gov]
    doc.build(story,onFirstPage=lambda c,d: footer(c,f'{MODEL_VERSION} | Presentation-only report'),onLaterPages=lambda c,d: footer(c,f'{MODEL_VERSION} | Presentation-only report'))

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--input',required=True); ap.add_argument('--output',required=True); a=ap.parse_args(); render(a.input,a.output)
if __name__=='__main__': main()
