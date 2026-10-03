"""Stress test the SQL fallback: unseen questions, each with an independently written reference query."""
import datetime as dt, duckdb, json, sys
from trustissues.common.llm import LLM
from trustissues.ask.sql_fallback import run_fallback
from trustissues.ask.access import query_policy
from trustissues.ask.types import UserContext

DB = "warehouse/ask_dev/trustissues_ask_dev.duckdb"
AS_OF = dt.date(2026, 9, 28)
CASES = [
 ("How many open cases are there in each queue?", "select queue, count(*) from gold.semantic_cases where outcome is null and queue is not null group by 1"),
 ("How many cases were opened in each month of 2026?", "select strftime(case_open_date,'%Y-%m'), count(*) from gold.semantic_cases where case_open_date>='2026-01-01' group by 1"),
 ("What is the total number of SMS contacts sent in September 2026?", "select count(*) from gold.semantic_contacts where channel='sms' and contact_ts_local>='2026-09-01' and contact_ts_local<'2026-09-29'"),
 ("How many charged-off cases are there by primary product?", "select primary_product, count(*) from gold.semantic_cases where outcome='charged_off' and primary_product is not null group by 1"),
 ("What is the average current days past due for open cases in each province?", "select province, avg(current_dpd) from gold.semantic_cases where outcome is null and province is not null group by 1"),
 ("How many promises were created in August 2026 by agent site?", "select agent_site, count(*) from gold.semantic_promises where ptp_created_ts>='2026-08-01' and ptp_created_ts<'2026-09-01' and agent_site is not null group by 1"),
 ("What total amount was paid against promises in July 2026?", "select sum(amount_paid_against) from gold.semantic_promises where paid_date>='2026-07-01' and paid_date<'2026-08-01'"),
 ("How many cases have the hardship flag set, by queue?", "select queue, count(*) from gold.semantic_cases where hardship_flag and queue is not null group by 1"),
 ("What is the total contact cost for calls in September 2026?", "select sum(contact_cost_cad) from gold.semantic_contacts where channel='call' and contact_ts_local>='2026-09-01' and contact_ts_local<'2026-09-29'"),
 ("How many distinct customers had a call in September 2026?", "select count(distinct golden_customer_id) from gold.semantic_contacts where channel='call' and contact_ts_local>='2026-09-01' and contact_ts_local<'2026-09-29'"),
 ("Which five agent sites made the most outbound contacts in August 2026?", "select agent_site, count(*) n from gold.semantic_contacts where direction='outbound' and contact_ts_local>='2026-08-01' and contact_ts_local<'2026-09-01' and agent_site is not null group by 1 order by n desc limit 5"),
 ("How many cases cured in 2026 by primary product?", "select primary_product, count(*) from gold.semantic_cases where outcome='cured' and cure_date>='2026-01-01' and primary_product is not null group by 1"),
 ("What share of open cases have a hardship flag?", "select avg(case when hardship_flag then 1.0 else 0 end) from gold.semantic_cases where outcome is null"),
 ("How many accounts are in each account status?", "select account_status, count(*) from gold.semantic_accounts where account_status is not null group by 1"),
 ("What is the highest balance among accounts over 90 days past due?", "select max(balance) from gold.semantic_accounts where dpd>90"),
]
llm = LLM(); con = duckdb.connect(DB, read_only=True)
pol = query_policy(UserContext("stress", "analyst"))
out = []
for q, ref in CASES:
    try:
        fb = run_fallback(q, llm=llm, policy=pol, as_of=AS_OF, database=DB, user_id="stress")
    except Exception as e:
        out.append((q, "EXC", repr(e), None, None)); continue
    want = con.execute(ref).fetchall()
    got = fb.result.rows if fb.result else None
    out.append((q, fb.reason if got is None else "ran", fb.sql, got, want))
def norm(rows):
    return sorted(tuple(round(float(x),3) if isinstance(x,(int,float)) and not isinstance(x,bool) else str(x) for x in r) for r in rows)
ok = 0
for q, st, sql, got, want in out:
    if got is None: verdict = "NOANS"
    else:
        a, b = norm(got), norm(want)
        # compare the numeric content and group labels; extra columns (counts next to rates) allowed
        verdict = "OK" if all(any(set(x)<=set(g) for g in a) for x in b) and len(a)==len(b) else "DIFF"
    ok += verdict == "OK"
    print(f"[{verdict}] {q}")
    if verdict != "OK":
        print("   sql :", (sql or "").replace("\n"," ")[:300]); print("   got :", (got or [])[:4], st if got is None else ""); print("   want:", want[:4])
print(f"\n{ok}/{len(out)} match the reference")
