# Identity resolution and the golden record

**Status: built and run on the real data (2026-10-03).** Every one of the 3,487,885 source customers is in `gold.customer_xref` exactly once, resolved to **1,013,152 people**. `gold.c360_customer` (one row per person) and `gold.c360_account` (1,844,500 accounts) are built from it. The numbers below come from [`../reports/identity_report.md`](../reports/identity_report.md), which is regenerated on every run; if they disagree, the report is right.

Design of record: [`p1_design_sections.md`](p1_design_sections.md) §4–5 and [`implementation_plan.md`](implementation_plan.md) §3.3–3.4. This file records what was built, what the data showed, and where the build departs from the design.

## 1. Run it

```bash
python -m trustissues.factory.identity.build              # curated.* -> identity.* -> gold.customer_xref (~4 min)
python -m trustissues.factory.identity.build --from cluster   # reuse Splink scores and LLM verdicts (~1 min)
python -m trustissues.factory.gold.build_c360             # gold.c360_account, gold.c360_customer (~45 s)
python -m trustissues.factory.identity.evaluate           # reports/identity_report.md
python -m trustissues.factory.gold.publish                # contract gate: gold_stage -> gold
python -m trustissues.factory.pipeline --from identity    # all of the above, in order
```

Needs the curated build first, and `splink`, `scipy`, `pyarrow` (in `requirements.txt`). The LLM step needs Anthropic credentials (`ANTHROPIC_API_KEY` or an `ant auth login` profile); without them it is skipped safely (§4).

## 2. Steps

| Step | Module | Output | Real data |
|---|---|---|---|
| Nodes | `identity/nodes.py` | `identity.nodes`: one row per source **customer** (`crm\|`, `card\|`, `loan\|`, `deposit\|`, `bureau\|`) | 1,017,885 CRM, 550,000 card, 340,000 loan, 580,000 deposit CIFs, 1,000,000 bureau files. 12 s |
| Exact rules | `identity/rules.py` | `identity.rule_links` | CRM pointer 5,928; national ID 12,929; CIF 580,000; name + DOB + phone 1.08M; DOB + name + postal 1.22M; 638 ambiguous; 3 blocked. 12 s |
| Splink | `identity/splink_model.py` | `identity.splink_pairs` (≥ 0.80, with every field's comparison level) | 284,311 links, 3,343 grey. 2 min 15 s. Model saved to `reports/identity_splink_model.json` |
| LLM second opinion | `identity/second_opinion.py` | `identity.llm_verdicts`, `gold.audit_log` | Not run on this machine (no credentials): all grey pairs went to review |
| Relationships | `identity/relationships.py` | `identity.relationship_pairs`, `gold.customer_relationship` | 2,364 joint holders, 207 co-borrowers. Never merged |
| Human decisions | `identity/review_queue.py` | `identity.human_decisions` from `data/review/identity_decisions.csv` | Empty so far |
| Cluster | `identity/cluster.py` | `identity.clusters`, `identity.golden_id_registry`, `gold.customer_xref` | 3.03M links accepted, 2 refused for conflicts. 56 s |
| Review queue | `identity/review_queue.py` | `gold.identity_review_queue` | 4,154 open pairs |
| Gold | `gold/build_c360.py` | `gold.c360_account`, `gold.c360_customer` | 1,844,500 accounts, 1,013,152 people. 42 s |
| Report | `identity/evaluate.py` | `reports/identity_report.md` | |

## 3. What the data showed

| Finding | Evidence | What we did |
|---|---|---|
| Deposit `src_customer_ref` (as `cif10`) and bureau `bank_subject_ref` are the same CIF | All 580,000 deposit CIFs exist in the bureau table; surnames agree in 100% | Rule `cif` (the plan's "tier 0", §9 open item now confirmed) |
| `national_id_hash` and `duplicate_of_crm_id` never contradict each other or the DOB | 18,426 hash pairs, all with the same DOB; pointers never disagree with the hash | Both are trusted CRM ↔ CRM rules, still checked for DOB conflicts |
| Exact rules settle most records | 84% of card and 94% of loan customers link by an exact rule | Splink only scores the rest (214k records) |
| **Some collections cases name the wrong customer** | A case's own fields disagree: the accounts it lists belong to one person, its `coll_customer_ref` names a different, real customer. In every such account–case pair (about 600) the two CRM IDs differ by one character (`STS-4183` holds the card; the case says `STS-4153`, Stanley Stanley, a different person). Where the two customers live in different provinces (439 cases), the case's own `province_code` matches the account holder in 433 and the named customer in 3. So the case almost certainly belongs to the account holder and the ID was mistyped; this is an inference, not proven. The two customers are never merged | Not used for matching. `gold.case_customer` records both readings and flags the mismatch (`case_customer_mismatch`); `c360_customer` takes the case's safety flags from both people (protective) |
| Some cards write ALL-CAPS names surname-first | "YOUNG CONNOR" for CRM "CONNOR YOUNG" | Splink level "first and last swapped", and a blocking rule for it |
| Compound surnames are split differently by each system | "DE LEON P C" (deposit) vs "PRINCESS D LEON" (CRM) | Splink level "same name words, split differently" |
| Family members share surname, phone and address | Splink scored "RAVI R DUNCAN, 2006" vs "G DUNCAN, 1968" at 0.999 | The family guard (D14) and a DOB-weight fix (D15) |
| Some customers appear to have changed surname | Pairs Splink scored ≥ 0.95 with the same first initial, exact DOB, same postal code and a matching phone, but a different surname: of the 1,110 that have case evidence, the evidence names the same person in 1,107. By contrast, same surname + different first name + same DOB and address: 1 of 62; same surname and phone, different first name and DOB: 0 of 66. "Changed surname" and "family member" are readings of the pattern, not facts in the data; the 3 disagreements were not inspected | Surname-change pairs link only with an exact DOB plus a shared phone or postal code; the family patterns never link automatically |
| The card day-first DOB convention (D11) holds | Of 237,096 cards whose DOB was settled by the convention and that are linked to a CRM person, the day-first reading equals a CRM DOB in 236,237 (99.6%), the month-first reading in 701 (0.3%). Partly circular (DOB is matching evidence), but Splink accepts swapped DOBs, so a wrong convention would have shown up as alternative-reading matches | The inference is confirmed |
| 3.9% of bureau files have no name | 8,626 linked on exact DOB + exact postal code | Allowed, with the ambiguity check (two "FOSTER"s with the same DOB and address go to review) |

## 4. Matching rules and safeguards

1. **Exact rules first** (`rules.py`). Name rules need an exact DOB, the same surname, and a compatible first name (equal, or an initial that fits). Phone or postal code alone never links.
2. **A conflict on a strong identifier blocks weaker links.** Different national ID hashes, or DOBs more than 400 days apart (a day/month swap excepted), mark the pair `blocked_conflict`.
3. **One record that fits two different people is not linked.** "Different" is judged after CRM dedupe. Rule matches are checked in `rules.py`; rule and Splink matches together are checked in `cluster.py`.
4. **Splink** (`splink_model.py`) compares only pairs with a CRM side where at least one side still needs a match. It compares name (one comparison: exact, swapped, initial, close, split differently, surname changed, first name differs), DOB (exact, swapped, within a month, one digit), phone (full, or masked digits that fit), postal code, and employer. Weights are learned by EM; no labels are needed.
5. **Routing.** ≥ 0.95 **and** the family guard passes → link. 0.80–0.95, or ≥ 0.95 failing the guard → grey zone. < 0.80 → kept separate.
6. **LLM second opinion** on grey pairs only. A pair links only if the LLM says match; a "no" goes to review with both explanations. Missing credentials, an error, or more than `llm_max_pairs` pairs (6,000) → review. Verdicts are cached and logged to `gold.audit_log`.
7. **Clustering is a constrained union.** Links go in strongest first. A link is refused if the resulting person would have two national ID hashes, DOBs more than 400 days apart, more than 5 CRM records, a joint-holder or co-borrower pair, or a pair a person marked `no_match`. Refused links go to review, so a cluster splits along its weakest links.
8. **Stable golden IDs.** `G-` plus 7 digits (more than 999,999 people). A cluster keeps the ID of the registered anchor it contains. A new cluster's anchor is its oldest CRM record, numbered in a deterministic order, so a rebuild from scratch gives the same IDs.
9. **Human decisions are re-applied** from `data/review/identity_decisions.csv` (`node_a, node_b, decision, reviewer, decided_ts, evidence_key, note`; `match | no_match | unresolved`). A decision is tied to an `evidence_key` (a hash of both records). If either record changes, the decision lapses and the pair can come back.

`gold.customer_xref.status`: `linked` (in a person with other records), `singleton` (matched nothing), `in_review` (alone, with an open review item), `blocked_conflict`, `unresolved` (a person could not decide).

## 5. How good is it

There are no identity labels in the release. The independent check is the collections data: every case lists the card, loan and deposit accounts it covers under a CRM ID. Matching never uses that link, so it measures the matcher.

- **Every rule and Splink method agrees with the case evidence on 100% of evaluable pairs**, once the cases with one-character CRM ID typos are allowed for. Across 294,908 account–case pairs, 99.61% agree outright.
- Remaining disagreements: 12 deposit CIF pairs, plus 524 card and loan accounts whose holder stayed a singleton (missed links, mostly in the review queue). A missed link is recoverable; a false merge is not.
- All six identity contract checks pass: unique golden ID; every source record exactly once; no link below 0.80, and 0.80–0.95 only through the LLM or a person; no person with two national IDs; no person with more than 5 CRM records; joint holders never merged.

## 6. Gold

`gold.c360_account`: one row per account (card, loan, deposit), with the stub columns plus `amount_owed`, `credit_limit`, `hardship_flag`, `dispute_flag`, `chargeoff_flag` and the source key. Each account has one snapshot in this release.

`gold.c360_customer`: the stub columns plus `consent_push`, `consent_autodialer`, `product_count`, `payroll_delay_days`, `full_name_source/_as_of`, `date_of_birth_source/_as_of`, `survivor_crm_customer_id`, `dq_status`.

- **Survivorship.** CRM wins. Among a person's CRM records the KYC-complete one wins, then the most recently updated. Without CRM, the most recent product record wins; bureau identity is the last resort.
- **Safety flags: any source is enough.** Cease contact: CRM, case, or contact history. Deceased: CRM, case, or bureau. Bankruptcy: CRM or bureau. Open dispute: case or card. Hardship: case, card, or loan.
- **Consent: the most restrictive value** across a person's CRM records. The stub's `consent_phone` and `consent_mail` hold CRM `consent_call` and `consent_letter` (interface open question 3).
- **`total_balance` is money owed:** card balance, loan outstanding, and deposit overdraft in use. Deposit credit balances are assets and are not netted. `max_dpd` and `worst_bucket` cover open accounts only.
- `dq_status = identity_in_review` on 4,857 people with an open review item.

## 7. Open

- **Run the LLM second opinion** with credentials (`identity.llm_model`, default `claude-opus-5-5`; the plan allows a smaller model). 3,343 pairs, cached after the first run.
- **The review queue** (4,154 pairs) needs P2's Identity Review page; until then decisions can be added to the CSV by hand.
- 10,106 bureau files and 1,283 deposit + bureau pairs have no CRM match. Most have no name or no match within the blocking rules.
- One rare Splink level (masked phone digits that fit) keeps a default u value: too few random pairs reach it.
- The consumer views, the contract and the publish gate are built: see [`consumer_views.md`](consumer_views.md). Identity and C360 tables are built into `gold_stage` and reach `gold` only through the gate.
