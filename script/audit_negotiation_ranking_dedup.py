#!/usr/bin/env python3
"""Compare the removed duplicate-behavior ranking against the de-duplicated ranking.

This is an ablation/sensitivity audit. It does not prove the new weights are
empirically optimal; it measures how much decision leverage the duplicate path had.
"""
from __future__ import annotations
import argparse,json,importlib.util
from pathlib import Path

MODEL_VERSION="FSFFL-Negotiation-Ranking-Dedup-Ablation-2.0"

def f(x,d=0.0):
    try:return float(x)
    except (TypeError,ValueError):return d

def key(row):
    return (
      str(row.get("buyer_user_id") or ""),
      tuple(sorted(map(str,row.get("outgoing_assets") or []))),
      tuple(sorted(map(str,row.get("return_assets") or row.get("incoming_assets") or []))),
    )

def discordance(a,b):
    p={x:i for i,x in enumerate(b)}; common=[x for x in a if x in p]
    pairs=flips=0
    for i in range(len(common)):
      for j in range(i+1,len(common)):
        pairs+=1
        if p[common[i]]>p[common[j]]: flips+=1
    return {"pair_count":pairs,"discordant_pairs":flips,"discordance_rate":round(flips/pairs,4) if pairs else 0.0}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--report",required=True); ap.add_argument("--output",required=True); args=ap.parse_args()
    report=json.loads(Path(args.report).read_text(encoding="utf-8"))
    rows=list(report.get("ranked_finalists") or report.get("top_5_alternatives") or [])
    audited=[]
    for r in rows:
      nr=r.get("negotiation_ranking") or {}
      if not nr: continue
      strategic=f(nr.get("focal_strategic_gain_component"))
      acceptance=f(nr.get("acceptance_fit_component"))
      behavior=f(nr.get("owner_behavior_match_component"),.5)
      current=f(nr.get("score"))
      legacy=.50*strategic+.30*acceptance+.20*behavior
      dedup=strategic
      audited.append({
        "candidate_key":repr(key(r)),
        "current_report_score":round(current,4),
        "recomputed_deduplicated_score":round(dedup,4),
        "removed_duplicate_behavior_score":round(legacy,4),
        "strategic_component":round(strategic,4),
        "acceptance_component":round(acceptance,4),
        "behavior_diagnostic_component":round(behavior,4),
        "legacy_minus_deduplicated":round(legacy-dedup,4),
      })
    fixture_fallback_used=False
    if not audited:
      fixture_fallback_used=True
      script_dir=Path(__file__).resolve().parent
      spec=importlib.util.spec_from_file_location("negotiation_ranking_fixture",script_dir/"negotiation_ranking.py")
      nrmod=importlib.util.module_from_spec(spec); spec.loader.exec_module(nrmod)
      fixture_components=[(.80,.45,.20),(.62,.70,.85),(.35,.55,.10)]
      for i,(strategic,acceptance,behavior) in enumerate(fixture_components,1):
        nr=nrmod.compose(strategic,acceptance,behavior)
        current=f(nr.get("score"))
        legacy=.50*strategic+.30*acceptance+.20*behavior
        dedup=strategic
        audited.append({
          "candidate_key":f"fixture:{i}",
          "current_report_score":round(current,4),
          "recomputed_deduplicated_score":round(dedup,4),
          "removed_duplicate_behavior_score":round(legacy,4),
          "strategic_component":round(strategic,4),
          "acceptance_component":round(acceptance,4),
          "behavior_diagnostic_component":round(behavior,4),
          "legacy_minus_deduplicated":round(legacy-dedup,4),
        })
    current_order=[x["candidate_key"] for x in sorted(audited,key=lambda x:(x["recomputed_deduplicated_score"],x["candidate_key"]),reverse=True)]
    legacy_order=[x["candidate_key"] for x in sorted(audited,key=lambda x:(x["removed_duplicate_behavior_score"],x["candidate_key"]),reverse=True)]
    score_matches=all(abs(x["current_report_score"]-x["recomputed_deduplicated_score"])<=0.00011 for x in audited)
    comp=discordance(legacy_order,current_order)
    payload={
      "model_version":MODEL_VERSION,
      "source_report_model_version":report.get("model_version"),
      "interpretation":{
        "historical_validation":False,
        "coefficient_tuning":False,
        "structural_double_count_ablation":True,
        "canonical_ranking_is_focal_decision_utility_only":True,
        "rank_change_proves_material_leverage_not_empirical_superiority":True,
      },
      "summary":{
        "candidate_count":len(audited),
        "source_report_ranked_candidate_count":len(rows),
        "fixture_fallback_used":fixture_fallback_used,
        "current_report_matches_deduplicated_formula":score_matches,
        "top_candidate_changes_vs_removed_duplicate_formula":legacy_order[0]!=current_order[0],
        "exact_order_changes_vs_removed_duplicate_formula":legacy_order!=current_order,
        **comp,
        "max_absolute_score_change":round(max(abs(x["legacy_minus_deduplicated"]) for x in audited),4),
      },
      "deduplicated_order":current_order,
      "removed_duplicate_behavior_order":legacy_order,
      "candidates":audited,
    }
    Path(args.output).write_text(json.dumps(payload,indent=2),encoding="utf-8")
    print(json.dumps(payload["summary"],indent=2))
    if not score_matches: raise SystemExit("Production report does not match canonical de-duplicated ranking formula")
if __name__=="__main__": main()
