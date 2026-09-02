#!/usr/bin/env python3
"""Build an exact-site candidate inventory for governed FSFFL parameters."""
from __future__ import annotations
import argparse, ast, hashlib, json
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parent.parent

HINTS = (
    "weight","threshold","multiplier","scale","factor","penalty","premium","discount",
    "prior","alpha","beta","gamma","sigma","variance","prob","rate","blend","shrink",
    "floor","cap","limit","offset","margin","band","cutoff","decay","horizon",
    "simulation","sample","percentile","quantile","replacement","scarcity","liquidity",
    "resilience","current","future","acceptance","confidence","anchor","bound",
    "adjustment","smoothing","games","history","window"
)

def flatten_strings(x):
    out=set()
    if isinstance(x,str): out.add(x)
    elif isinstance(x,dict):
        for v in x.values(): out |= flatten_strings(v)
    elif isinstance(x,list):
        for v in x: out |= flatten_strings(v)
    return out

def governed_paths(root,reg):
    arch_path=root/"data/model_governance/application_architecture.json"
    arch=json.loads(arch_path.read_text())
    paths={p for p in flatten_strings(arch) if str(p).endswith((".py",".json"))}
    for fam in reg.get("parameters",[]):
        paths.update(
            str(p) for p in fam.get("paths",[])
            if str(p).endswith((".py",".json"))
        )
    # Governance metadata describes authority but is not itself a runtime
    # coefficient/config payload. Avoid recursively treating the registries as
    # the model they govern.
    excluded={
        "data/model_parameter_registry.json",
        "data/model_governance/application_architecture.json",
        "data/model_governance/provisional_estimation_policy.json",
        "data/model_governance/coefficient_provenance_policy.json",
        "data/model_governance/authoritative_parameter_site_registry.json",
        "data/model_governance/coefficient_provenance_seed_findings.json",
    }
    return sorted(p for p in paths if p not in excluded and (root/p).is_file())

def family_ids(path,reg):
    return [str(f["id"]) for f in reg.get("parameters",[]) if path in f.get("paths",[])]

def literal(node):
    if isinstance(node,ast.Constant) and isinstance(node.value,(int,float,bool,str)):
        return node.value
    if isinstance(node,ast.UnaryOp) and isinstance(node.op,ast.USub):
        v=literal(node.operand)
        return -v if isinstance(v,(int,float)) else None
    if isinstance(node,(ast.List,ast.Tuple)):
        vals=[literal(v) for v in node.elts]
        return vals if all(v is not None for v in vals) else None
    if isinstance(node,ast.Dict):
        ks=[literal(v) for v in node.keys]; vs=[literal(v) for v in node.values]
        return dict(zip(ks,vs)) if all(v is not None for v in ks+vs) else None
    return None

def name(node):
    if isinstance(node,ast.Name): return node.id
    if isinstance(node,ast.Attribute): return node.attr
    return None

def screening(n,line):
    t=(n+" "+line).lower()
    if any(x in t for x in ("display","report","round","precision","label")):
        return "POSSIBLY_DESCRIPTIVE"
    if any(x in t for x in ("simulation","sample","iteration","top_k","budget")):
        return "RUNTIME_BUDGET_OR_PRECISION"
    if any(x in t for x in HINTS):
        return "LIKELY_MODEL_PARAMETER"
    return "REVIEW_REQUIRED"

def signature(path,kind,n,value,line):
    normalized=" ".join(line.strip().split())
    raw=json.dumps([path,kind,n,value,normalized],sort_keys=True,separators=(",",":"))
    return hashlib.sha1(raw.encode()).hexdigest()

def add(rows,path,lineno,kind,n,value,line,fids,provenance_source="STATIC_CODE_SITE"):
    if value is None or isinstance(value,(bool,str)): return
    sig=signature(path,kind,n,value,line)
    rows.append({
        "parameter_id":f"AUTO-{sig[:12]}",
        "site_signature":sig,
        "module":path,
        "file_path":path,
        "line":lineno,
        "parameter_name":n,
        "current_value_or_function":value,
        "site_kind":kind,
        "runtime_authority":"REVIEW_REQUIRED",
        "downstream_consumers":[],
        "existing_family_registry_ids":fids,
        "evidence_classification":"UNCLASSIFIED",
        "provenance_source":provenance_source,
        "originally_hand_set":None,
        "empirically_validated":None,
        "simulation_derived":None,
        "externally_anchored":None,
        "duplicated_elsewhere":None,
        "uncertainty_status":"UNREVIEWED",
        "sensitivity_level":"UNREVIEWED",
        "estimated_decision_impact":"UNREVIEWED",
        "replacement_feasibility":"UNREVIEWED",
        "identifiability_class":"UNREVIEWED",
        "recommended_action":"UNREVIEWED",
        "evidence_needed_for_further_promotion":"Manual provenance and authority review required",
        "screening_class":screening(n,line),
        "source_excerpt":line.strip()[:240],
        "review_status":"CANDIDATE_NOT_YET_ADJUDICATED"
    })

class Visitor(ast.NodeVisitor):
    def __init__(self,path,source,fids):
        self.path=path; self.lines=source.splitlines(); self.fids=fids; self.rows=[]
    def line(self,node):
        n=getattr(node,"lineno",0)
        return self.lines[n-1] if n and n<=len(self.lines) else ""
    def visit_Assign(self,node):
        v=literal(node.value)
        if v is not None:
            for t in node.targets:
                add(self.rows,self.path,node.lineno,"assignment",name(t) or "assignment",v,self.line(node),self.fids)
        self.generic_visit(node)
    def visit_AnnAssign(self,node):
        v=literal(node.value) if node.value else None
        if v is not None:
            add(self.rows,self.path,node.lineno,"annotated_assignment",name(node.target) or "assignment",v,self.line(node),self.fids)
        self.generic_visit(node)
    def visit_FunctionDef(self,node):
        args=list(node.args.posonlyargs)+list(node.args.args)
        defs=[None]*(len(args)-len(node.args.defaults))+list(node.args.defaults)
        for a,d in zip(args,defs):
            if d is not None:
                v=literal(d)
                if v is not None:
                    add(self.rows,self.path,node.lineno,"function_default",f"{node.name}.{a.arg}",v,self.line(node),self.fids)
        for a,d in zip(node.args.kwonlyargs,node.args.kw_defaults):
            if d is not None:
                v=literal(d)
                if v is not None:
                    add(self.rows,self.path,node.lineno,"kwonly_default",f"{node.name}.{a.arg}",v,self.line(node),self.fids)
        self.generic_visit(node)
    def visit_Compare(self,node):
        for c in node.comparators:
            v=literal(c)
            if isinstance(v,(int,float)) and not isinstance(v,bool):
                add(self.rows,self.path,node.lineno,"comparison_threshold",name(node.left) or ast.unparse(node.left)[:80],v,self.line(node),self.fids)
        self.generic_visit(node)
    def visit_Call(self,node):
        for kw in node.keywords:
            if kw.arg and any(h in kw.arg.lower() for h in HINTS):
                v=literal(kw.value)
                if isinstance(v,(int,float)) and not isinstance(v,bool):
                    add(self.rows,self.path,node.lineno,"keyword_argument",f"{ast.unparse(node.func)[:60]}.{kw.arg}",v,self.line(node),self.fids)
        self.generic_visit(node)

def json_pointer_escape(part):
    return str(part).replace("~","~0").replace("/","~1")

def scan_json(path,payload,fids):
    rows=[]
    def walk(node,pointer="",key_context="root"):
        if isinstance(node,dict):
            for key,value in node.items():
                child=f"{pointer}/{json_pointer_escape(key)}"
                walk(value,child,str(key))
            return
        if isinstance(node,list):
            for i,value in enumerate(node):
                child=f"{pointer}/{i}"
                walk(value,child,key_context)
            return
        if isinstance(node,(int,float)) and not isinstance(node,bool):
            parameter_name=pointer or "/"
            # JSON pointer is both the stable source coordinate and excerpt.
            add(
                rows,path,0,"json_numeric_leaf",parameter_name,node,
                f"{parameter_name}={node}",fids,
                provenance_source="GOVERNED_JSON_SITE"
            )
    walk(payload)
    return rows

def build(root:Path):
    reg_path=root/"data/model_parameter_registry.json"
    reg=json.loads(reg_path.read_text())
    rows=[]; errors=[]; paths=governed_paths(root,reg)
    py_count=json_count=0
    for path in paths:
        full=root/path
        if path.endswith(".py"):
            py_count+=1
            src=full.read_text()
            try: tree=ast.parse(src,filename=path)
            except SyntaxError as e:
                errors.append({"path":path,"error":str(e)}); continue
            v=Visitor(path,src,family_ids(path,reg)); v.visit(tree); rows.extend(v.rows)
        elif path.endswith(".json"):
            json_count+=1
            try:
                payload=json.loads(full.read_text(encoding="utf-8"))
            except (OSError,ValueError,TypeError) as e:
                errors.append({"path":path,"error":str(e)}); continue
            rows.extend(scan_json(path,payload,family_ids(path,reg)))
    rows.sort(key=lambda r:(r["file_path"],r["line"],r["parameter_name"],r["site_kind"]))
    # A semantic signature intentionally excludes absolute line numbers so simple
    # insertions do not churn registry IDs. Identical repeated statements can
    # nevertheless share that semantic signature. Disambiguate only those
    # collisions by stable source order rather than weakening duplicate-ID CI.
    signature_totals={}
    for row in rows:
        sig=row["site_signature"]
        signature_totals[sig]=signature_totals.get(sig,0)+1
    signature_seen={}
    for row in rows:
        base_sig=row["site_signature"]
        if signature_totals.get(base_sig,0) <= 1:
            continue
        occurrence=signature_seen.get(base_sig,0)+1
        signature_seen[base_sig]=occurrence
        unique_sig=hashlib.sha1(
            f"{base_sig}:semantic_occurrence:{occurrence}".encode()
        ).hexdigest()
        row["site_signature_base"]=base_sig
        row["site_semantic_occurrence"]=occurrence
        row["site_signature"]=unique_sig
        row["parameter_id"]=f"AUTO-{unique_sig[:12]}"
    summary={
        "production_behavior_changed":False,
        "governed_paths_scanned":len(paths),
        "governed_python_paths_scanned":py_count,
        "governed_json_paths_scanned":json_count,
        "candidate_parameter_sites":len(rows),
        "likely_model_parameter_sites":sum(r["screening_class"]=="LIKELY_MODEL_PARAMETER" for r in rows),
        "runtime_budget_or_precision_sites":sum(r["screening_class"]=="RUNTIME_BUDGET_OR_PRECISION" for r in rows),
        "possibly_descriptive_sites":sum(r["screening_class"]=="POSSIBLY_DESCRIPTIVE" for r in rows),
        "unreviewed_sites":len(rows),
        "parse_errors":len(errors),
        "family_registry_parameters":len(reg.get("parameters",[]))
    }
    return {
        "schema_version":"1.2",
        "model_version":"FSFFL-Coefficient-Provenance-Audit-1.2",
        "purpose":"Candidate-site inventory beneath the existing family-level parameter registry, including governed Python and JSON parameter sites.",
        "authority":"AUDIT_ONLY_NON_AUTHORITATIVE",
        "policy":{
            "numeric_literal_is_not_automatically_a_model_coefficient":True,
            "every_candidate_requires_manual_authority_and_provenance_adjudication":True,
            "runtime_budgets_and_descriptive_thresholds_are_separate_from_economic_coefficients":True,
            "governed_json_numeric_leaves_are_first_class_parameter_sites":True,
            "governance_metadata_is_not_scanned_as_runtime_economics":True,
            "inventory_confers_promotion_authority":False,
            "site_signature_is_line_number_independent":True
        },
        "summary":summary,
        "parse_errors":errors,
        "parameters":rows
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",default=str(DEFAULT_ROOT))
    ap.add_argument("--output",default=None)
    args=ap.parse_args()
    root=Path(args.root).resolve()
    output=Path(args.output).resolve() if args.output else root/"data/audit/authoritative_parameter_inventory.json"
    artifact=build(root)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(artifact,indent=2)+"\n")
    print(json.dumps(artifact["summary"],indent=2))

if __name__=="__main__":
    main()
