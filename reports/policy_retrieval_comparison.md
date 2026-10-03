# Policy retrieval: mode comparison

16 questions with a known correct section (6 benchmark questions, 10 paraphrases). Cell = rank of the first correct section among the sections returned (top 10 shown; the model reads the top 5). Built by `python scripts/compare_policy_modes.py`.

| mode | hit@5 | MRR | search time per question |
|---|---:|---:|---:|
| bm25 | 13/16 | 0.61 | 2 ms |
| hybrid | 13/16 | 0.74 | 198 ms |
| hybrid_rerank | 15/16 | 0.82 | 1709 ms |
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
