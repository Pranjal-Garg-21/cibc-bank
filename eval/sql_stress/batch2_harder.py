"""Stress test the SQL fallback: unseen questions, each with an independently written reference query."""
import datetime as dt, duckdb, json, sys
from trustissues.common.llm import LLM
from trustissues.ask.sql_fallback import run_fallback
from trustissues.ask.access import query_policy
from trustissues.ask.types import UserContext

DB = "warehouse/ask_dev/trustissues_ask_dev.duckdb"
AS_OF = dt.date(2026, 9, 28)
CASES = [
 ("How many outbound calls were made on cases in each queue in September 2026?", "select c.queue, count(*) from gold.semantic_contacts k join gold.semantic_cases c using(case_id) where k.channel='call' and k.direction='outbound' and k.contact_ts_local>='2026-09-01' and k.contact_ts_local<'2026-09-29' and c.queue is not null group by 1"),
 ("Which primary product has the highest share of cases with a hardship flag among cases opened in 2026?", "select primary_product, avg(case when hardship_flag then 1.0 else 0 end) r from gold.semantic_cases where case_open_date>='2026-01-01' and primary_product is not null group by 1 order by r desc limit 1"),
 ("Compare the number of calls made between 1-14 September 2026 and 15-28 September 2026", "select sum(case when contact_ts_local<'2026-09-15' then 1 else 0 end), sum(case when contact_ts_local>='2026-09-15' then 1 else 0 end) from gold.semantic_contacts where channel='call' and contact_ts_local>='2026-09-01' and contact_ts_local<'2026-09-29'"),
 ("How many promises created in July 2026 ended up kept, broken and partially kept?", "select ptp_status, count(*) from gold.semantic_promises where ptp_created_ts>='2026-07-01' and ptp_created_ts<'2026-08-01' and ptp_status in ('kept','broken','partially_kept') group by 1"),
 ("What is the average amount paid per kept promise by queue?", "select queue, avg(amount_paid_against) from gold.semantic_promises where ptp_status='kept' and queue is not null group by 1"),
 ("Which agent sites made more than 5000 outbound calls in August 2026?", "select agent_site, count(*) n from gold.semantic_contacts where channel='call' and direction='outbound' and contact_ts_local>='2026-08-01' and contact_ts_local<'2026-09-01' and agent_site is not null group by 1 having count(*)>5000"),
 ("Which day of the week had the most calls in September 2026?", "select dayname(contact_ts_local) d, count(*) n from gold.semantic_contacts where channel='call' and contact_ts_local>='2026-09-01' and contact_ts_local<'2026-09-29' group by 1 order by n desc limit 1"),
 ("What share of calls were within permitted hours by province in September 2026?", "select province, avg(case when within_permitted_hours_flag then 1.0 else 0 end) from gold.semantic_contacts where channel='call' and contact_ts_local>='2026-09-01' and contact_ts_local<'2026-09-29' and province is not null group by 1"),
 ("How many cases opened in 2026 have had no contact at all?", "select count(*) from gold.semantic_cases c where case_open_date>='2026-01-01' and not exists (select 1 from gold.semantic_contacts k where k.case_id=c.case_id)"),
 ("What is the average number of days from case open to cure for cured cases by primary product?", "select primary_product, avg(date_diff('day', case_open_date, cure_date)) from gold.semantic_cases where outcome='cured' and cure_date is not null and primary_product is not null group by 1"),
 ("What are the top 3 provinces by number of charged-off cases?", "select province, count(*) n from gold.semantic_cases where outcome='charged_off' and province is not null group by 1 order by n desc limit 3"),
 ("How many customers have more than one open case?", "select count(*) from (select golden_customer_id from gold.semantic_cases where outcome is null group by 1 having count(*)>1)"),
 ("By how much did the number of newly opened cases change from August to September 2026?", "select sum(case when case_open_date>='2026-09-01' then 1 else 0 end) - sum(case when case_open_date<'2026-09-01' then 1 else 0 end) from gold.semantic_cases where case_open_date>='2026-08-01' and case_open_date<'2026-09-29'"),
 ("What is the total overdue amount by product for accounts more than 90 days past due?", "select product, sum(overdue_amount) from gold.semantic_accounts where dpd>90 and product is not null group by 1"),
 ("How many distinct cases with a broken promise also have the hardship flag set?", "select count(distinct c.case_id) from gold.semantic_cases c join gold.semantic_promises p using(case_id) where p.ptp_status='broken' and c.hardship_flag"),
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
        print("   sql :", (sql or "").replace("\n"," ")[:400]); print("   got :", (got or [])[:4], st if got is None else ""); print("   want:", want[:4])
print(f"\n{ok}/{len(out)} match the reference")
