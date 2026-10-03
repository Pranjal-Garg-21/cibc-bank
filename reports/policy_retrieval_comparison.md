# Policy retrieval: mode comparison

16 questions with a known correct section (6 benchmark questions, 10 paraphrases). Cell = rank of the first correct section among the sections returned (top 10 shown; the model reads the top 5). Built by `python scripts/compare_policy_modes.py`.

| mode | hit@5 | MRR | search time per question |
|---|---:|---:|---:|
| bm25 | 13/16 | 0.61 | 2 ms |
| hybrid | 13/16 | 0.74 | 22 ms |
| hybrid_rerank | 15/16 | 0.82 | 1993 ms |
| whole_corpus | 16/16 | - | - |

| question | bm25 | hybrid | hybrid_rerank | whole_corpus |
|---|---:|---:|---:|---:|
| BQ-002 How many call attempts can we make on a mid-stage account in... | 4 | 2 | 1 | all 40 |
| BQ-005 Does the National Do Not Call List stop collection calls? | 1 | 1 | 1 | all 40 |
| BQ-019 A customer says their hours were cut at work. What must the ... | 1 | 1 | 1 | all 40 |
| BQ-026 How long does the 3-month reduced payment programme last, an... | 2 | 2 | 2 | all 40 |
| BQ-033 What is the grace period before a promise to pay is consider... | 1 | 1 | 1 | all 40 |
| BQ-009 A customer tells us they filed a consumer proposal. What sho... | 1 | 1 | 1 | all 40 |
| P-01 What time of day am I allowed to phone someone about their o... | 2 | 1 | 1 | all 40 |
| P-02 The client says the money they sent never showed up on their... | 2 | 1 | 3 | all 40 |
| P-03 Someone is unhappy with how we treated them on the phone. Wh... | miss | 9 | 6 | all 40 |
| P-04 Do I need proof of earnings before putting someone on a shor... | miss | 8 | 1 | all 40 |
| P-05 A woman just lost her husband and is very upset on the call.... | miss | 8 | 5 | all 40 |
| P-06 We charged people late fees because our app was down. Do the... | 1 | 1 | 1 | all 40 |
| P-07 Why did the dialler place so few outbound calls late last mo... | 2 | 1 | 1 | all 40 |
| P-08 A lawyer representing the customer wrote to us. Can we keep ... | 2 | 2 | 1 | all 40 |
| P-09 Peut-on appeler un client au Québec le dimanche? | 1 | 1 | 1 | all 40 |
| P-10 Can we use someone's marital status when deciding how to col... | 1 | 1 | 1 | all 40 |

## Answers

| question | mode | cited the correct section | not found | seconds | answer |
|---|---|---|---|---:|---|
| BQ-002 | hybrid_rerank | yes | False | 2.2 | A maximum of 3 outbound call attempts in any rolling 7 days per case. |
| BQ-002 | whole_corpus | yes | False | 1.5 | A maximum of 3 outbound call attempts in any rolling 7 days per case. |
| BQ-005 | hybrid_rerank | yes | False | 2.8 | No. It applies to telemarketing only. |
| BQ-005 | whole_corpus | yes | False | 1.0 | No. It applies to telemarketing only. |
| BQ-019 | hybrid_rerank | yes | False | 3.1 | The agent must acknowledge the hardship, record the hardship_flag with the stated reason, and offer at least one eligible hardship option before asking for paym |
| BQ-019 | whole_corpus | yes | False | 1.2 | Offer at least one eligible option from the hardship programmes before asking for payment. |
| BQ-026 | hybrid_rerank | no | False | 1.7 | The 3-month reduced payment programme lasts 3 months and reduces the payment by 50%. |
| BQ-026 | whole_corpus | yes | False | 1.9 | The 3-month reduced payment programme lasts 3 months and reduces the payment by 50%. |
| BQ-033 | hybrid_rerank | yes | False | 2.8 | Two business days after the promised date. |
| BQ-033 | whole_corpus | yes | False | 1.0 | Two business days after the promised date. |
| BQ-009 | hybrid_rerank | yes | False | 2.6 | All collection contact stops on the filing date. |
| BQ-009 | whole_corpus | yes | False | 1.0 | All collection contact stops on the filing date and the case moves to insolvency_hold. |
| P-01 | hybrid_rerank | yes | False | 2.4 | Calls and text messages are made between 07:00 and 21:00 in the customer's local time. Where Sunday contact is permitted, Sunday calls are limited to 13:00-17:0 |
| P-01 | whole_corpus | yes | False | 1.4 | Calls and text messages are made between 07:00 and 21:00 in the customer's local time. |
| P-02 | hybrid_rerank | yes | False | 2.8 | Open a dispute and pause payment requests for the disputed amount until it is resolved. |
| P-02 | whole_corpus | yes | False | 1.0 | Open a dispute and pause payment requests for the disputed amount until it is resolved. |
| P-03 | hybrid_rerank | no | False | 2.7 | Acknowledge the customer's concern, record the hardship flag with the stated reason, and offer at least one eligible hardship option before asking for payment. |
| P-03 | whole_corpus | yes | False | 0.9 | Record the complaint the same day and escalate to a team lead. |
| P-04 | hybrid_rerank | yes | False | 4.7 | No, proof of earnings is not required for a short relief plan of 3 months or less. |
| P-04 | whole_corpus | yes | False | 1.1 | No, proof of earnings is not required for a short relief plan of 3 months or less. |
| P-05 | hybrid_rerank | no | False | 5.3 | Acknowledge the bereavement, record the hardship_flag with the stated reason, offer at least one eligible hardship option before asking for payment, and do not  |
| P-05 | whole_corpus | yes | False | 1.4 | Acknowledge the bereavement, record the hardship reason, offer hardship options before asking for payment, and do not request payment the customer said they can |
| P-06 | hybrid_rerank | yes | False | 2.5 | No, they do not have to ask for a refund. |
| P-06 | whole_corpus | yes | False | 0.9 | No, they do not have to ask for a refund. Fees charged because of a bank system incident are reversed without the customer having to prove payment. |
| P-07 | hybrid_rerank | yes | False | 3.3 | The predictive dialer was degraded; outbound call capacity fell about 60%. |
| P-07 | whole_corpus | yes | False | 0.9 | The predictive dialer was degraded; outbound call capacity fell about 60%. |
| P-08 | hybrid_rerank | yes | False | 2.4 | No, we cannot keep phoning the customer. |
| P-08 | whole_corpus | yes | False | 1.0 | No |
| P-09 | hybrid_rerank | yes | False | 2.7 | Non, on ne peut pas appeler un client au Québec le dimanche. |
| P-09 | whole_corpus | yes | False | 4.5 | Non, il n'est pas permis d'appeler un client au Québec le dimanche. |
| P-10 | hybrid_rerank | yes | False | 3.5 | No, we cannot use someone's marital status when deciding how to collect. |
| P-10 | whole_corpus | yes | False | 1.4 | No, we cannot use someone's marital status when deciding how to collect. |