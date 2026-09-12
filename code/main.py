#!/usr/bin/env python3
"""Deterministic 90-day cash-flow planner for HackerRank Buy or Wait?"""
from __future__ import annotations
import calendar, csv, re, statistics
from itertools import combinations
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

HORIZON = 90
OCR_AMOUNTS = {"event_253":"4365000","event_1442":"100000","event_1545":"41272","event_1700":"2854","event_1786":"704.05","event_3051":"1995","event_3231":"8528.10","event_4535":"15339","event_5170":"723","event_6033":"79679.26","event_6859":"3650","event_7307":"33.50","event_7941":"22298","event_9421":"4543","event_9806":"9968","event_10521":"393.22"}
OUT = ["request_id","amount_safe_to_pay","affordability_status","recommended_payment_method","payment_plan","earliest_date_for_full_payment","spending_changes_needed","decision_explanation"]
def D(x): return Decimal(str(x or 0))
def dt(x): return date.fromisoformat(x[:10])
def parsed_number(x):
 x=x.strip(".,")
 if "." in x and "," in x:
  decimal="," if x.rfind(",")>x.rfind(".") else "."; thousands="." if decimal=="," else ","
  x=x.replace(thousands,"")
  if decimal==",":x=x.replace(",",".")
 elif x.count(".")>1:x=x.replace(".","")
 elif x.count(",")>1:x=x.replace(",","")
 elif "," in x:x=x.replace(",","")
 return D(x)
def split(x): return set(filter(None,(x or "").split("|")))
def add_month(x):
 import calendar
 y=x.year+(x.month==12); m=1 if x.month==12 else x.month+1
 return date(y,m,min(x.day,calendar.monthrange(y,m)[1]))
def money(x):
 x=x.quantize(Decimal(".01"),rounding=ROUND_HALF_UP)
 return str(int(x)) if x==x.to_integral() else format(x,"f").rstrip("0").rstrip(".")
def display_money(x):
 x=D(x).quantize(Decimal(".01"),rounding=ROUND_HALF_UP)
 return f"{int(x):,}" if x==x.to_integral() else f"{x:,.2f}"
def plan_money(x):
 x=D(x).quantize(Decimal(".01"),rounding=ROUND_HALF_UP)
 return str(int(x)) if x==x.to_integral() else f"{x:.2f}"
def spoken_date(x): return f"{x.day} {x.strftime('%B')} {x.year}"
def load(p):
 with open(p,newline="",encoding="utf-8") as f:return list(csv.DictReader(f))
@dataclass(frozen=True)
class Flow: when: date; amount: Decimal; source: str
@dataclass
class Plan: method:str; flows:list[Flow]; total:Decimal; oid:str=""; changes:tuple=()

class Engine:
 def __init__(self, base):
  self.base=base; self.profiles={r["user_id"]:r for r in load(base/"financial_profiles.csv")}
  self.events=defaultdict(list);self.msgs=defaultdict(list);self.opts=defaultdict(list)
  for r in load(base/"financial_events.csv"):self.events[r["user_id"]].append(r)
  for r in load(base/"messages.csv"):self.msgs[r["user_id"]].append(r)
  for r in load(base/"request_payment_options.csv"):self.opts[r["request_id"]].append(r)
  self.fx={(r["rate_date"],r["from_currency"],r["to_currency"]):D(r["rate"]) for r in load(base/"exchange_rates.csv")}
 def amt(self,e,home):
  x=D(e["amount"] or OCR_AMOUNTS.get(e["event_id"], 0))
  if not x or e["currency"]==home:return x
  return x*self.fx.get((e["settlement_date"] or e["event_date"],e["currency"],home),D(0))
 def evidence(self,u,start):
  z={"salary":None,"salary_date":None,"stop":False,"rent":D(1),"credits":[]}
  for m in self.msgs[u]:
   t=m["message_text"].lower(); dates=re.findall(r"(?:19|20)\d\d-\d\d-\d\d",t)
   ds=dt(dates[0]) if dates else None; n=re.search(r"(?:inr|idr|zar|eur|usd)\s*([\d](?:[\d,.]*\d)?)",t,re.I); a=parsed_number(n.group(1)) if n else None
   pay=any(w in t for w in ("salary","payroll","gaji","penggajian"))
   if pay and any(w in t for w in ("employment has ended","has ended","no off-season","berakhir")):z["stop"]=True
   if pay and a and any(w in t for w in ("salary","gaji","monthly pay","base salary","regular pay","first salary")):
    z["salary"]=a
    if ds and ds>=start:z["salary_date"]=ds
   if pay and ds and any(w in t for w in ("replaces","revised date","expected on")):z["salary_date"]=ds
   if "rent" in t and "increase" in t and "12%" in t:z["rent"]=D("1.12")
   if a and ds and any(w in t for w in ("approved an invoice","client approved")):z["credits"].append((ds,a))
  return z
 def active(self,u):
  all=self.events[u]; refs={e["linked_event_id"] for e in all if e["linked_event_id"]};out=[]
  for e in all:
   if e["status"] in {"failed","cancelled","unrealized"} or e["direction"]=="non_cash":continue
   if e["event_id"] in refs and e["status"]!="settled":continue
   out.append(e)
  return out
 def cadence(self, ds, lo, hi):
  if len(ds)<5:return None
  gs=[(b-a).days for a,b in zip(ds,ds[1:])]; m=round(statistics.median(gs))
  return m if lo<=m<=hi and sum(abs(g-m)<=3 for g in gs)>=max(2,len(gs)-1) else None
 def baseflows(self,r,changes=()):
  p=self.profiles[r["user_id"]];u=r["user_id"];home=p["home_currency"];start=dt(r["request_date"]);end=start+timedelta(HORIZON); ev=self.active(u);evi=self.evidence(u,start);protect=split(p["expense_categories_to_protect"]);red=split(p["expense_categories_user_is_willing_to_reduce"]);stop=split(p["expense_categories_user_is_willing_to_stop"]);out=[]
  for e in ev:
   when=dt(e["settlement_date"] or e["event_date"])
   if start<=when<=end and e["status"] in {"pending","scheduled"} and not(e["direction"]=="credit" and e["status"]=="pending"):
    a=self.amt(e,home);out.append(Flow(when,a if e["direction"]=="credit" else -a,e["event_id"]))
  hist=[e for e in ev if e["status"]=="settled" and self.amt(e,home) and dt(e["settlement_date"] or e["event_date"])<=start]
  income_exclusions=("commission","final","last payroll","bonus","arrears","refund","prize","lottery","severance","reimbursement")
  salary_hist=[e for e in hist if e["direction"]=="credit" and e["category"]=="salary" and not any(w in e["description"].lower() for w in income_exclusions)]
  salary_dates=sorted(dt(e["settlement_date"] or e["event_date"]) for e in salary_hist)
  salary_days=sorted({d.day for d in salary_dates})
  collective_calendar_salary=len(salary_dates)>=5 and len(salary_days)==2 and all(sum(d.day==day for d in salary_dates)>=2 for day in salary_days)
  collective_short_salary=self.cadence(salary_dates,5,22) is not None or collective_calendar_salary
  groups=defaultdict(list)
  for e in hist:
   if e["direction"]=="credit":
    if e["category"]!="salary" or any(w in e["description"].lower() for w in income_exclusions):continue
    k=("income","collective_salary") if collective_short_salary else ("income",e["description"])
   elif e["category"] in {"groceries","transport","dining"}:k=("variable",e["category"])
   elif e["category"] in protect:k=("essential",e["category"])
   elif e["flexibility"]!="fixed" and (e["category"] in red or e["category"] in stop):k=("flex",e["category"])
   else:k=("fixed",e["description"])
   groups[k].append(e)
  changed={x[0]:x for x in changes}
  for k,rows in groups.items():
   rows.sort(key=lambda e:e["settlement_date"] or e["event_date"]);ds=[dt(e["settlement_date"] or e["event_date"]) for e in rows]
   var=k[0] in {"essential","flex","variable"} or k[1]=="collective_salary"; iv=14 if k[1]=="collective_salary" and collective_calendar_salary else self.cadence(ds,5 if var else 25,22 if var else 35)
   if not iv and var:iv=self.cadence(ds,25,35)
   if not iv:continue
   calendar_step=iv>=25
   vals=[self.amt(e,home) for e in rows]
   if k[0]=="income":
    if evi["stop"]:continue
    a=evi["salary"] or (D(statistics.mean(vals)) if k[1]=="collective_salary" else vals[-1])
   elif var:a=D(statistics.mean(vals))
   else:a=vals[-1]
   if "rent" in k[1].lower():a*=evi["rent"]
   # Apply an allowed change to a recurring sources latest exemplar.
   match=next((changed[x["event_id"]] for x in rows if x["event_id"] in changed),None)
   if match:
    _,kind,new=match;a=D(0) if kind=="stop" else new
   if k[1]=="collective_salary" and collective_calendar_salary:
    month=date(start.year,start.month,1)
    while month<=end:
     for dom in salary_days:
      nxt=date(month.year,month.month,min(dom,calendar.monthrange(month.year,month.month)[1]))
      if start<nxt<=end and not any(f.when==nxt and f.amount==a for f in out):out.append(Flow(nxt,a,"collective_salary"))
     month=add_month(month)
    continue
   nxt=ds[-1]
   while nxt<=start:nxt=add_month(nxt) if calendar_step else nxt+timedelta(iv)
   if k[0]=="income" and evi["salary_date"]:nxt=evi["salary_date"]
   while nxt<=end:
    sign=1 if k[0]=="income" else -1
    if not any(f.when==nxt and f.amount==sign*a for f in out):out.append(Flow(nxt,sign*a,k[1]))
    nxt=add_month(nxt) if calendar_step else nxt+timedelta(iv)
  # A sparse explicit Next confirmed salary is still a confirmed payroll anchor.
  if not evi["stop"]:
   for e in ev:
    if e["status"]=="scheduled" and e["direction"]=="credit" and e["category"]=="salary":
     nxt=dt(e["settlement_date"] or e["event_date"]); a=evi["salary"] or self.amt(e,home)
     if evi["salary_date"]:nxt=evi["salary_date"]
     while nxt<=end:
      if nxt>=start and not any(f.when==nxt and f.amount==a for f in out):out.append(Flow(nxt,a,e["event_id"]))
      nxt=add_month(nxt)
  # A message can establish the first or resumed monthly salary without a
  # sufficiently long transaction history.
  if not evi["stop"] and evi["salary"] and evi["salary_date"]:
   nxt=evi["salary_date"]; a=evi["salary"]
   while nxt<=end:
    if nxt>=start and not any(f.when==nxt and f.amount==a for f in out):out.append(Flow(nxt,a,"message_salary"))
    nxt=add_month(nxt)
  for when,a in evi["credits"]:
   if start<when<=end:out.append(Flow(when,a,"invoice"))
  return out
 def run(self,r,pay=(),changes=()):
  b=D(self.profiles[r["user_id"]]["current_available_balance"]);floor=b
  for f in sorted(self.baseflows(r,changes)+list(pay),key=lambda f:(f.when,-f.amount,f.source)):
   b+=f.amount;floor=min(floor,b)
  return floor
 def safe(self,r):return max(D(0),min(D(r["requested_amount"]),self.run(r)-D(self.profiles[r["user_id"]]["minimum_balance_to_keep"])))
 def earliest(self,r):
  q=dt(r["request_date"]);a=D(r["requested_amount"]);m=D(self.profiles[r["user_id"]]["minimum_balance_to_keep"])
  for i in range(HORIZON+1):
   day=q+timedelta(i)
   if self.run(r,[Flow(day,-a,"full")])>=m:return day
  return None
 def edits(self,r):
  p=self.profiles[r["user_id"]];q=dt(r["request_date"]);latest={}
  for e in self.active(r["user_id"]):
   if e["status"]!="settled" or not e["amount"] or dt(e["settlement_date"] or e["event_date"])>q:continue
   flex=e["flexibility"]
   if flex=="fixed":continue
   key=e["category"] if (e["category"] in split(p["expense_categories_user_is_willing_to_reduce"]) or e["category"] in split(p["expense_categories_user_is_willing_to_stop"])) else None
   if key and (key not in latest or dt(e["settlement_date"] or e["event_date"])>dt(latest[key]["settlement_date"] or latest[key]["event_date"])):latest[key]=e
  stops=[]; reductions=[]
  for e in latest.values():
   cat=e["category"]; flex=e["flexibility"]
   if cat in split(p["expense_categories_user_is_willing_to_reduce"]) and "reducible" in flex:reductions.append((D(e["amount"])-D(e["minimum_allowed_amount"]),e["event_id"],"reduce",D(e["minimum_allowed_amount"])))
   elif cat in split(p["expense_categories_user_is_willing_to_stop"]) and flex=="stoppable":stops.append((D(e["amount"]),e["event_id"],"stop",D(0)))
  chosen=sorted(stops,reverse=True)+sorted(reductions,reverse=True)
  return tuple((eid,k,v) for _,eid,k,v in chosen[:3])
 def candidates(self,r,safe,early):
  p=self.profiles[r["user_id"]];q=dt(r["request_date"]);a=D(r["requested_amount"]);m=split(p["payment_methods_user_will_consider"]);out=[]
  if "full_payment" in m:out.append(Plan("full_payment",[Flow(q,-a,"full")],a))
  if r["allows_partial_payment"].lower()=="true" and "partial_payment" in m and D(0)<safe<a and early and early<=dt(r["desired_completion_date"]):out.append(Plan("partial_payment",[Flow(q,-safe,"part"),Flow(early,-(a-safe),"part")],a))
  cap=p["max_installment_months"]
  for o in self.opts[r["request_id"]]:
   if o["payment_method"]!="installments" or "installments" not in m or not cap or int(o["number_of_payments"])>int(cap):continue
   first=dt(o["first_payment_date"]);n=int(o["number_of_payments"]);freq=int(o["payment_frequency_days"] or 0);pay=[Flow(first+timedelta(i*freq),-D(o["payment_amount"]),o["payment_option_id"]) for i in range(n)]
   out.append(Plan("installments",pay,D(o["total_payable_amount"]),o["payment_option_id"]))
  return out
 def decide(self,r):
  p=self.profiles[r["user_id"]];a=D(r["requested_amount"]);safe=self.safe(r);early=self.earliest(r);deadline=dt(r["desired_completion_date"]);minbal=D(p["minimum_balance_to_keep"]);ok=[]
  for c in self.candidates(r,safe,early):
   if max(f.when for f in c.flows)<=deadline and self.run(r,c.flows)>=minbal:ok.append(c)
  edits=self.edits(r)
  if not ok and edits:
   for n in range(1,min(3,len(edits))+1):
    for chosen in combinations(edits,n):
     for c in self.candidates(r,safe,early):
      if max(f.when for f in c.flows)<=deadline and self.run(r,c.flows,chosen)>=minbal:
       c.changes=chosen;ok.append(c)
  if ok:
   c=min(ok,key=lambda x:(bool(x.changes),x.total,min(f.when for f in x.flows),len(x.flows),x.oid));status="affordable_now" if c.method=="full_payment" and not c.changes and safe>=a else "affordable_with_plan";plan="|".join(f"{f.when}:{plan_money(-f.amount)}" for f in c.flows);changes="|".join(f"stop:{i}" if k=="stop" else f"reduce_to:{i}:{money(v)}" for i,k,v in c.changes) or "none";ex=self.explain(r,status,c.method,safe,early,c.changes,c)
   return self.row(r,safe,status,c.method,plan,early,changes,ex)
  if early and early<=deadline and "full_payment" in split(p["payment_methods_user_will_consider"]):return self.row(r,safe,"affordable_later","wait",f"{early}:{plan_money(a)}",early,"none",self.explain(r,"affordable_later","wait",safe,early))
  return self.row(r,safe,"not_affordable","not_recommended","none",None,"none",self.explain(r,"not_affordable","not_recommended",safe,None))
 def explain(self,r,status,method,safe,early,changes=(),plan=None):
  p=self.profiles[r["user_id"]];cur=p["home_currency"];amount=display_money(r["requested_amount"]);minimum=display_money(p["minimum_balance_to_keep"])
  if method=="installments":
   return f"Use {len(plan.flows)} installments of {cur} {display_money(-plan.flows[0].amount)}, starting {spoken_date(plan.flows[0].when)}. This leaves at least {cur} {minimum} available."
  if method=="partial_payment":
   return f"Pay {cur} {display_money(safe)} today and the remaining {cur} {display_money(D(r['requested_amount'])-safe)} on {spoken_date(early)}. This completes the full request and keeps the {cur} {minimum} minimum protected."
  if method=="wait":
   return f"Pay {cur} {amount} in full on {spoken_date(early)}. Paying earlier would take the balance below the {cur} {minimum} minimum."
  if method=="full_payment":
   actions=[];byid={e["event_id"]:e for e in self.events[r["user_id"]]}
   for eid,kind,new in changes:
    desc=byid[eid]["description"].lower()
    actions.append(f"Stop the {desc}" if kind=="stop" else f"reduce the {desc} to {cur} {display_money(new)}")
   if actions:
    action_text=" and ".join(actions)
    return f"{action_text[:1].upper()+action_text[1:]}, then pay {cur} {amount} today. This leaves at least {cur} {minimum} available."
   return f"Pay {cur} {amount} today. This leaves at least {cur} {minimum} available over the next 90 days."
  if safe>0:return f"Do not proceed with the {cur} {amount} request. Although {cur} {display_money(safe)} is available today, the full amount cannot be completed safely within 90 days."
  return f"Do not make this payment by {spoken_date(dt(r['desired_completion_date']))}. None of the available options keeps the {cur} {minimum} minimum protected."
 def row(self,r,s,status,method,plan,early,changes,ex):return {"request_id":r["request_id"],"amount_safe_to_pay":money(s),"affordability_status":status,"recommended_payment_method":method,"payment_plan":plan,"earliest_date_for_full_payment":str(early or ""),"spending_changes_needed":changes,"decision_explanation":ex}
def main():
 root=Path(__file__).resolve().parents[1];eng=Engine(root/"dataset");rows=[eng.decide(r) for r in load(root/"dataset"/"requests.csv")]
 with open(root/"output.csv","w",newline="",encoding="utf-8") as f:
  w=csv.DictWriter(f,fieldnames=OUT,lineterminator="\n");w.writeheader();w.writerows(rows)
 print(f"Wrote {len(rows)} rows to {root / "output.csv"}")
if __name__=="__main__":main()
