#!/usr/bin/env python3
"""Sample evaluator and deterministic output-contract validator."""
from __future__ import annotations
import argparse, csv, importlib.util, sys
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

FIELDS = ["request_id","amount_safe_to_pay","affordability_status","recommended_payment_method","payment_plan","earliest_date_for_full_payment","spending_changes_needed","decision_explanation"]
STATUSES = {"affordable_now","affordable_with_plan","affordable_later","not_affordable"}
METHODS = {"full_payment","partial_payment","installments","wait","not_recommended"}
EPS = Decimal("0.005")

def rows(path):
    with open(path, newline="", encoding="utf-8") as f: return list(csv.DictReader(f))
def dec(x, label, errors, blank=False):
    x = (x or "").strip()
    if not x:
        if not blank: errors.append(f"{label}: blank decimal")
        return None
    try: y = Decimal(x)
    except InvalidOperation:
        errors.append(f"{label}: invalid decimal {x!r}"); return None
    if not y.is_finite(): errors.append(f"{label}: non-finite decimal")
    return y
def day(x, label, errors, blank=False):
    x = (x or "").strip()
    if not x:
        if not blank: errors.append(f"{label}: blank date")
        return None
    try: y = date.fromisoformat(x)
    except ValueError:
        errors.append(f"{label}: invalid ISO date {x!r}"); return None
    if y.isoformat() != x: errors.append(f"{label}: date is not YYYY-MM-DD")
    return y
def eq(a,b): return a is not None and b is not None and abs(a-b) <= EPS
def plan(text, label, errors):
    text=(text or "").strip()
    if text=="none": return []
    if not text: errors.append(f"{label}: empty; use none"); return []
    out=[]; prev=None
    for i, item in enumerate(text.split("|"),1):
        if item.count(":") != 1: errors.append(f"{label}: malformed item {i}"); continue
        d,a=item.split(":",1); d=day(d,f"{label}[{i}]",errors); a=dec(a,f"{label}[{i}]",errors)
        if a is not None and a<=0: errors.append(f"{label}[{i}]: amount must be positive")
        if d is not None and prev is not None and d<prev: errors.append(f"{label}: not chronological")
        if d is not None: prev=d
        if d is not None and a is not None: out.append((d,a))
    return out
def changes(text, label, errors):
    text=(text or "").strip()
    if text=="none": return []
    if not text: errors.append(f"{label}: empty; use none"); return []
    out=[]
    for item in text.split("|"):
        if item.startswith("stop:") and item.count(":")==1 and item[5:]: out.append((item[5:],"stop",None))
        elif item.startswith("reduce_to:") and item.count(":")==2:
            _,eid,a=item.split(":",2); a=dec(a,f"{label} {eid}",errors)
            if not eid: errors.append(f"{label}: empty event")
            elif a is not None: out.append((eid,"reduce",a))
        else: errors.append(f"{label}: malformed action {item!r}")
    if len(out)>3: errors.append(f"{label}: >3 actions")
    if len({x[0] for x in out}) != len(out): errors.append(f"{label}: event changed twice")
    return out
def option_plan(o):
    first=date.fromisoformat(o["first_payment_date"]); n=int(o["number_of_payments"])
    freq=int(o["payment_frequency_days"] or "0"); amount=Decimal(o["payment_amount"])
    return [(first+timedelta(days=i*freq),amount) for i in range(n)]

class Validator:
    def __init__(self, repo):
        self.repo=Path(repo); d=self.repo/"dataset"
        self.requests=rows(d/"requests.csv")
        self.profiles={r["user_id"]:r for r in rows(d/"financial_profiles.csv")}
        self.events={r["event_id"]:r for r in rows(d/"financial_events.csv")}
        self.options=defaultdict(list)
        for o in rows(d/"request_payment_options.csv"): self.options[o["request_id"]].append(o)
        self.byid={r["request_id"]:r for r in self.requests}
    def validate(self, data, label="output"):
        errors=[]
        if not data: return [f"{label}: no rows"]
        if list(data[0]) != FIELDS: errors.append(f"{label}: columns differ from required order")
        expected=[r["request_id"] for r in self.requests]; got=[r.get("request_id","") for r in data]
        if len(data)!=len(expected): errors.append(f"{label}: expected {len(expected)} rows, got {len(data)}")
        if len(set(got)) != len(got): errors.append(f"{label}: duplicate request_id")
        if got != expected: errors.append(f"{label}: request IDs differ/order mismatch")
        for i,r in enumerate(data,1):
            rid=r.get("request_id",""); req=self.byid.get(rid); pfx=f"{label} row {i} ({rid or '<missing>'})"
            if not req: errors.append(f"{pfx}: unknown request_id"); continue
            prof=self.profiles.get(req["user_id"])
            if not prof: errors.append(f"{pfx}: missing profile"); continue
            amount=dec(r.get("amount_safe_to_pay",""),pfx+" amount",errors); requested=Decimal(req["requested_amount"])
            if amount is not None and not (0<=amount<=requested): errors.append(f"{pfx}: amount outside [0, requested]")
            status=r.get("affordability_status",""); method=r.get("recommended_payment_method","")
            if status not in STATUSES: errors.append(f"{pfx}: bad status {status!r}")
            if method not in METHODS: errors.append(f"{pfx}: bad method {method!r}")
            pe=[]; pp=plan(r.get("payment_plan",""),pfx+" plan",pe); errors.extend(pe)
            ce=[]; cc=changes(r.get("spending_changes_needed",""),pfx+" changes",ce); errors.extend(ce)
            earliest=day(r.get("earliest_date_for_full_payment",""),pfx+" earliest",errors,blank=True)
            q=date.fromisoformat(req["request_date"]); deadline=date.fromisoformat(req["desired_completion_date"])
            accepted=set(filter(None,prof["payment_methods_user_will_consider"].split("|")))
            if method in {"full_payment","partial_payment","installments"} and method not in accepted: errors.append(f"{pfx}: unaccepted {method}")
            if method=="wait" and "full_payment" not in accepted: errors.append(f"{pfx}: wait requires full_payment acceptance")
            if status=="affordable_now" and (method!="full_payment" or earliest!=q): errors.append(f"{pfx}: affordable_now contract violation")
            if status=="affordable_now" and cc: errors.append(f"{pfx}: affordable_now cannot require spending changes")
            if status=="affordable_with_plan" and method=="full_payment" and not cc: errors.append(f"{pfx}: with_plan full_payment requires spending changes")
            if method=="installments" and pp and pp[-1][0]>deadline: errors.append(f"{pfx}: installment plan exceeds completion deadline")
            if status=="not_affordable" and (method!="not_recommended" or pp or earliest is not None): errors.append(f"{pfx}: not_affordable contract violation")
            if status=="affordable_later" and (method!="wait" or len(pp)!=1 or earliest is None or earliest>deadline): errors.append(f"{pfx}: affordable_later contract violation")
            if status=="affordable_with_plan" and method=="wait": errors.append(f"{pfx}: with_plan cannot wait")
            if method=="full_payment" and (len(pp)!=1 or pp[0][0]!=q or not eq(pp[0][1],requested)): errors.append(f"{pfx}: bad full_payment plan")
            if method=="wait" and (earliest is None or len(pp)!=1 or pp[0][0]!=earliest or not eq(pp[0][1],requested)): errors.append(f"{pfx}: bad wait plan")
            if method=="partial_payment":
                if req["allows_partial_payment"].lower()!="true": errors.append(f"{pfx}: request disallows partial")
                if amount is None or not (0<amount<requested): errors.append(f"{pfx}: partial amount must be interior")
                if earliest is None or earliest>deadline: errors.append(f"{pfx}: partial exceeds deadline")
                rem=requested-amount if amount is not None else None
                if len(pp)!=2 or pp[0][0]!=q or earliest is None or pp[1][0]!=earliest: errors.append(f"{pfx}: partial must have two dated payments")
                elif not eq(pp[0][1],amount) or not eq(pp[1][1],rem): errors.append(f"{pfx}: partial amounts do not sum")
            if method=="installments":
                valid=[]
                for o in self.options[rid]:
                    cap=prof.get("max_installment_months","")
                    if o["payment_method"]=="installments" and (not cap or int(o["number_of_payments"])<=int(cap)) and pp==option_plan(o): valid.append(o)
                if not valid: errors.append(f"{pfx}: installments do not match eligible option")
            if method=="not_recommended" and pp: errors.append(f"{pfx}: not_recommended has plan")
            seen=set()
            for eid,action,new in cc:
                if eid in seen: continue
                seen.add(eid); e=self.events.get(eid)
                if e and (e["status"]!="settled" or e["event_type"] not in {"expense","subscription","debt_payment"} or date.fromisoformat(e["settlement_date"] or e["event_date"])>q): errors.append(f"{pfx}: change must target a settled recurring expense {eid}")
                if not e or e["user_id"]!=req["user_id"]: errors.append(f"{pfx}: change wrong event/user {eid}"); continue
                if e["direction"]!="debit" or not e["amount"] or e["flexibility"]=="fixed": errors.append(f"{pfx}: change non-flexible/non-debit {eid}"); continue
                if action=="stop":
                    allowed=e["category"] in set(filter(None,prof["expense_categories_user_is_willing_to_stop"].split("|")))
                    if "stoppable" not in e["flexibility"] or not allowed: errors.append(f"{pfx}: stop not permitted {eid}")
                else:
                    allowed=e["category"] in set(filter(None,prof["expense_categories_user_is_willing_to_reduce"].split("|")))
                    minimum=Decimal(e["minimum_allowed_amount"] or "0"); current=Decimal(e["amount"])
                    if "reducible" not in e["flexibility"] or not allowed: errors.append(f"{pfx}: reduce not permitted {eid}")
                    if new is None or new<minimum or new>current: errors.append(f"{pfx}: reduce amount invalid {eid}")
            if not r.get("decision_explanation","").strip(): errors.append(f"{pfx}: blank explanation")
        return errors

def evaluate_samples(repo, engine):
    samples=rows(Path(repo)/"dataset"/"sample_requests.csv"); mismatches=[]; counts=Counter()
    for sample in samples:
        actual=engine.decide(sample)
        for f in FIELDS:
            a,e=actual.get(f,""),sample.get(f,"")
            same=eq(Decimal(a),Decimal(e)) if f=="amount_safe_to_pay" else a==e
            if not same: counts[f]+=1; mismatches.append(f"{sample['request_id']} {f}: expected={e!r} actual={a!r}")
    return samples,counts,mismatches
def self_test(v):
    req=v.requests[0]; good={"request_id":req["request_id"],"amount_safe_to_pay":"0","affordability_status":"not_affordable","recommended_payment_method":"not_recommended","payment_plan":"none","earliest_date_for_full_payment":"","spending_changes_needed":"none","decision_explanation":"test"}
    old,oldmap=v.requests,v.byid; v.requests=[req]; v.byid={req["request_id"]:req}; failures=[]
    try:
        if v.validate([good]): failures.append("valid synthetic row rejected")
        bad=deepcopy(good); bad["amount_safe_to_pay"]="999999999";
        if not any("outside" in e for e in v.validate([bad])): failures.append("amount mutation undetected")
        bad=deepcopy(good); bad["affordability_status"]="affordable_with_plan"; bad["recommended_payment_method"]="partial_payment"; bad["payment_plan"]="2024-03-03:0|2024-03-04:1"
        if not v.validate([bad]): failures.append("partial mutation undetected")
    finally: v.requests,v.byid=old,oldmap
    return failures
def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--output",default="output.csv"); ap.add_argument("--self-test",action="store_true"); args=ap.parse_args()
    repo=Path(__file__).resolve().parents[2]; v=Validator(repo); errors=[]
    out=repo/args.output
    if out.exists(): errors.extend(v.validate(rows(out),str(out.relative_to(repo))))
    else: errors.append(f"missing {out}")
    spec=importlib.util.spec_from_file_location("engine",repo/"code/main.py"); mod=importlib.util.module_from_spec(spec); sys.modules[spec.name]=mod; spec.loader.exec_module(mod)
    samples,counts,mismatches=evaluate_samples(repo,mod.Engine(repo/"dataset"))
    print(f"sample requests evaluated: {len(samples)}"); print(f"sample exact mismatches: {sum(counts.values())}"); print("mismatches by field:",dict(counts))
    for x in mismatches[:100]: print("MISMATCH",x)
    if args.self_test:
        se=self_test(v); errors.extend("self-test: "+x for x in se)
        if not se: print("self-tests: passed")
    if errors:
        print(f"contract errors: {len(errors)}")
        for x in errors[:120]: print("ERROR",x)
    else: print("contract validation: passed")
    return 1 if errors else 0
if __name__=="__main__": raise SystemExit(main())
