#!/usr/bin/env python3
"""Stable GM3 Team Improvement application-area entry point."""
from __future__ import annotations
import copy, importlib.util, sys
from pathlib import Path
MODEL_VERSION='FSFFL-GM-Team-Improvement-Application-1.0'
EXPECTED_IMPLEMENTATION_VERSION='FSFFL-GM-Team-Improvement-Lab-1.6'
SCRIPT=Path(__file__).resolve().parent.parent; IMPLEMENTATION=SCRIPT/'run_team_improvement_lab_v16.py'
if str(SCRIPT) not in sys.path: sys.path.insert(0,str(SCRIPT))
def _load_current():
    spec=importlib.util.spec_from_file_location('fsffl_gm3_team_improvement_current',IMPLEMENTATION); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod
class PortfolioEvaluator:
    def __init__(self,focus_user_id,simulations=1000,seed=20260821,strategic_posture='AUTO'):
        current=_load_current()
        if current.MODEL_VERSION!=EXPECTED_IMPLEMENTATION_VERSION: raise RuntimeError(f'Unexpected Team Improvement implementation: {current.MODEL_VERSION}')
        base=current.load_base(); base.MODEL_VERSION=current.MODEL_VERSION; self.focus_user_id=str(focus_user_id); dl=base.load_module(SCRIPT/'run_roster_decision_lab.py','gm3_portfolio_dl'); state=base.load_module(SCRIPT/'decision_lab_state_aware.py','gm3_portfolio_state_aware'); self.strategic_posture=str(strategic_posture or 'AUTO'); self.dl=state.install(dl,strategic_posture=self.strategic_posture,owner_override_user_id=self.focus_user_id); self.lineupopt=base.load_module(SCRIPT/'lineup_optimizer.py','gm3_portfolio_lineup'); self.rosteraware=base.load_module(SCRIPT/'roster_aware_trade.py','gm3_portfolio_roster'); self.base=base; self.current=current; self.simulations=int(simulations); self.seed=int(seed); self.model_inputs=self.dl.load_model_inputs(); simmod,league,rosters,users,players,season,projections,raw_schedule=self.model_inputs; self.baseline_lineups=self.dl.load_cached_lineups(season); self.baseline=self.dl.simulate_from_lineups(simmod,league,rosters,users,raw_schedule,self.baseline_lineups,self.simulations,self.seed)
    def _actions_for_row(self,row):
        c=str(row.get('channel') or '')
        if c=='TRADE':return self.base.trade_actions(self.focus_user_id,row)
        if c=='WAIVER':return self.base.waiver_actions(self.focus_user_id,row)
        if c=='HOLD':return []
        raise ValueError(f'Unsupported portfolio channel: {c}')
    def _inputs_with_waiver_projections(self,rows):
        mi=list(self.model_inputs); p=copy.deepcopy(mi[6]); changed=False; added=[]
        native_ids={str(x) for x in (p.get('players') or {})}
        for row in rows:
            if str(row.get('channel') or '')!='WAIVER':continue
            t=row.get('target') or {}; pid=str(t.get('player_id') or ''); profile=row.get('native_full_projection')
            if pid and profile:
                if pid not in native_ids: added.append(pid)
                p.setdefault('players',{})[pid]=copy.deepcopy(profile); changed=True
        if changed:
            p['_decision_lab_projection_augmentation']={
                'source_model':'FSFFL-Full-Projection-Universe-1.0',
                'added_player_ids':sorted(set(added)),
                'native_player_count':len(native_ids),
                'final_player_count':len(p.get('players') or {}),
                'unrelated_full_universe_players_added':False,
            }
            mi[6]=p
        return tuple(mi)
    def evaluate(self,rows):
        rows=[copy.deepcopy(x) for x in rows if str(x.get('channel') or '')!='HOLD']; actions=[]
        for row in rows:actions.extend(self._actions_for_row(row))
        if not actions:return {'team_improvement_score':0.0,'simulation':{'focus_delta':{k:0.0 for k in ['expected_wins','expected_points_for','playoff_probability','bye_probability','championship_probability']},'strategic':{'market_dynasty_delta':0.0,'base_franchise_value_delta':0.0,'break_glass_delta':0.0}},'actions':[]}
        sim=self.current.simulate_actions_protect_add(self.base,self.dl,self.lineupopt,self.rosteraware,self._inputs_with_waiver_projections(rows),self.baseline_lineups,self.baseline,self.focus_user_id,actions,self.simulations,self.seed)
        attribution=self.base.load_module(SCRIPT/'decision_attribution.py','gm3_portfolio_decision_attribution').reconcile(sim)
        return {'team_improvement_score':self.base.unified_score(self.focus_user_id,sim),'simulation':sim,'decision_attribution':attribution,'actions':sim.get('effective_actions') or actions,'source_rows':rows,'authority':'GM3 Team Improvement','shared_decision_utility':'FSFFL-Shared-Decision-Utility-2.0','bundle_simulation_source':'current Team Improvement implementation via stable GM3 facade'}

    def evaluate_player_context(self,player_id):
        """Publish GM3-owned marginal roster context for one player.

        This is a zero-price counterfactual used for analytics, not a trade or
        waiver recommendation. An externally rostered player is transferred to
        the focal roster; a focal-roster player is removed; and an unrostered
        player is added. Roster legalization and lineup reoptimization remain
        authoritative GM3/Simulator operations.
        """
        player_id=str(player_id)
        _,_,rosters,_,players,_,projections,_=self.model_inputs
        if player_id not in (players or {}):
            raise ValueError(f'Unknown player_id: {player_id}')
        if player_id not in ((projections or {}).get('players') or {}):
            raise ValueError(f'Player lacks canonical Simulator projection: {player_id}')
        owner_by_player=self.base.owner_map(rosters)
        owner_user_id=str(owner_by_player.get(player_id) or '')
        if owner_user_id==self.focus_user_id:
            scenario='FOCAL_ROSTER_REMOVAL'
            actions=[{'type':'cut','user_id':self.focus_user_id,'players':[player_id]}]
        elif owner_user_id:
            scenario='ZERO_PRICE_ROSTER_TRANSFER'
            actions=[{'type':'trade','from_user_id':owner_user_id,'to_user_id':self.focus_user_id,'players':[player_id],'picks':[]}]
        else:
            scenario='ZERO_PRICE_UNROSTERED_ADD'
            actions=[{'type':'add','user_id':self.focus_user_id,'players':[player_id]}]
        sim=self.current.simulate_actions_protect_add(
            self.base,self.dl,self.lineupopt,self.rosteraware,self.model_inputs,
            self.baseline_lineups,self.baseline,self.focus_user_id,actions,
            self.simulations,self.seed,
        )
        utility=self.base.load_module(SCRIPT/'decision_utility.py','gm3_team_context_decision_utility')
        attribution=self.base.load_module(SCRIPT/'decision_attribution.py','gm3_team_context_decision_attribution')
        def perspective(uid,payload):
            scored=utility.score(payload)
            return {
                'user_id':str(uid),
                'shared_decision_utility':scored,
                'decision_attribution':attribution.reconcile(payload),
                'simulator_delta':copy.deepcopy(payload.get('focus_delta') or {}),
                'strategic_context':copy.deepcopy(payload.get('strategic') or {}),
                'roster_resolution':copy.deepcopy((payload.get('roster_resolution') or {}).get(str(uid)) or {}),
            }
        focal=perspective(self.focus_user_id,sim)
        owner=None
        if owner_user_id and owner_user_id!=self.focus_user_id:
            owner=perspective(owner_user_id,sim.get('counterparty') or {})
        elif owner_user_id==self.focus_user_id:
            owner=copy.deepcopy(focal)
        return {
            'authority':'GM3 Team Improvement',
            'model_version':MODEL_VERSION,
            'shared_decision_utility':'FSFFL-Shared-Decision-Utility-2.0',
            'scenario':scenario,
            'player_id':player_id,
            'focal_user_id':self.focus_user_id,
            'current_owner_user_id':owner_user_id or None,
            'focal_team_context':focal,
            'current_owner_context':owner,
            'simulation_count':self.simulations,
            'seed':self.seed,
            'zero_price_counterfactual':True,
            'creates_trade_price':False,
            'creates_acceptance_probability':False,
            'recommendation':False,
            'interpretation':(
                'Gross marginal roster utility under an explicitly defined zero-price '
                'counterfactual; not fair value or willingness to pay.'
            ),
        }
def portfolio_evaluator(focus_user_id,simulations=1000,seed=20260821,strategic_posture='AUTO'):return PortfolioEvaluator(focus_user_id,simulations,seed,strategic_posture)
def main():
    current=_load_current()
    if current.MODEL_VERSION!=EXPECTED_IMPLEMENTATION_VERSION:raise RuntimeError(f'Unexpected Team Improvement implementation: {current.MODEL_VERSION}')
    current.main()
if __name__=='__main__':main()
