# TrustIssues: Architecture

One overview, then one close-up per layer. Diagrams are Mermaid; GitHub renders them in place.
Sources: `src/trustissues/`, `ui/`, `config/`, `contracts/`, `Makefile`.

## Full architecture

Everything in one view. Image: [full.png](architecture/full.png), PDF: [full.pdf](architecture/full.pdf).

```mermaid
flowchart TB
    classDef src fill:#f3f4f6,stroke:#6b7280,color:#111827
    classDef l1 fill:#e0f2fe,stroke:#0284c7,color:#0c4a6e
    classDef l2 fill:#ede9fe,stroke:#7c3aed,color:#3b0764
    classDef l3 fill:#dcfce7,stroke:#16a34a,color:#14532d
    classDef l4 fill:#ffedd5,stroke:#ea580c,color:#7c2d12
    classDef ui fill:#fef9c3,stroke:#ca8a04,color:#713f12
    classDef gate fill:#fee2e2,stroke:#dc2626,color:#7f1d1d
    classDef store fill:#ffffff,stroke:#374151,color:#111827

    SRC["<b>Maple Bank release</b> · 1M customers · Oct 2016 – Sep 2026<br/>31 tables (CSV, Parquet) · call transcripts (JSON) · policy documents · data dictionary"]:::src

    subgraph L1["LAYER 1 · DATA PRODUCT FACTORY  (make pipeline)"]
        direction LR
        I1["<b>1 Ingest</b><br/>raw.*<br/>manifest · dictionary"]:::l1
        IG{"Schema-drift<br/>gate"}:::gate
        I2["<b>2 Curate</b><br/>curated.*<br/>dates · IDs · dedupe"]:::l1
        I3["<b>3 Quality</b><br/>DQ checks · anomaly scan<br/>derived columns"]:::l1
        I4["<b>4 Text controls</b><br/>eligible, masked<br/>notes + transcripts"]:::l1
        I1 --> IG --> I2 --> I3 --> I4
    end

    subgraph ID["IDENTITY RESOLUTION  (5 source systems → one person)"]
        direction LR
        D1["<b>Exact rules</b><br/>CRM pointer · national ID<br/>CIF · name+DOB+phone"]:::l1
        D2["<b>Splink</b><br/>match probability"]:::l1
        D3["<b>LLM second opinion</b><br/>grey zone 0.80–0.95<br/>(off by default)"]:::l1
        D4{{"<b>Human review</b><br/>identity queue"}}:::gate
        D5["<b>Cluster</b><br/>golden_customer_id<br/>+ match confidence"]:::l1
        D1 --> D2 --> D3 --> D4 --> D5
    end

    subgraph GOLD["GOLDEN C360"]
        direction LR
        G1["<b>Gold build</b><br/>c360_customer · c360_account<br/>case_customer · views"]:::l1
        GG{"Contract gate<br/>c360_customer.yaml"}:::gate
        G2[("<b>DuckDB · gold.*</b><br/>customer_xref · c360<br/>timeline · promises<br/>safeguards · text_features")]:::store
        G1 --> GG --> G2
    end

    subgraph L3["LAYER 3 · FEATURE STORE  (one definition, training and live scoring)"]
        direction LR
        F1["<b>Structured features</b><br/>features/sql/*.sql<br/>delinquency · liquidity · payday<br/>channel response · promise history"]:::l3
        F2["<b>Text features</b><br/>LLM reads masked notes<br/>evidence quote must match<br/>point-in-time safe"]:::l3
        F3[("<b>Feast offline store</b><br/>feature_views · training sets")]:::l3
        F1 --> F3
        F2 --> F3
    end

    subgraph L4["LAYER 4 · MODELS AND DECISIONING  (next-best-action)"]
        direction LR
        N1["<b>LightGBM</b><br/>case risk · promise break risk<br/>+ SHAP"]:::l4
        N2["<b>Rules first</b><br/>allowed actions<br/>hardship / vulnerable:<br/>support only"]:::l4
        N3["<b>Action value</b><br/>effect × risk × reach<br/>× amount − cost"]:::l4
        N4["<b>Capacity</b><br/>agents · channels"]:::l4
        N5["<b>Decision card</b><br/>plain-English reasons<br/>blocked actions · evidence"]:::l4
        N6{{"<b>Human review</b><br/>agent accepts / overrides<br/>team lead approves"}}:::gate
        N1 --> N2 --> N3 --> N4 --> N5 --> N6
    end

    subgraph L2["LAYER 2 · INSIGHT AND NLP  (ask/service.py, one entry point)"]
        direction LR
        A1{"<b>Guard</b><br/>injection · refusal<br/>access check"}:::gate
        A2["<b>Router</b><br/>rules first,<br/>LLM if unplaced"]:::l2
        A3["<b>Answer paths</b><br/>certified metric SQL · generated SQL<br/>(sqlglot, read-only) · drivers<br/>customer path · policy RAG (BM25)"]:::l2
        A4["<b>Answer card</b><br/>answer + SQL or sources<br/>disclosure rules · or refusal"]:::l2
        A1 --> A2 --> A3 --> A4
    end

    subgraph SV["SERVING AND GOVERNANCE"]
        direction LR
        U1["<b>React + TypeScript</b><br/>Ask · Queue · Decision card<br/>Identity · Overview · Status · Audit"]:::ui
        U2["<b>FastAPI</b><br/>session · MFA · rate limit<br/>RBAC · security headers"]:::ui
        U3[("<b>Audit + access log</b><br/>overrides append-only")]:::store
        U1 --> U2 --> U3
    end

    OUT["<b>Outputs</b> · data contract · data quality report · identity report<br/>fairness report · monitoring · benchmark answers CSV"]:::src

    SRC --> L1
    L1 --> ID
    ID --> GOLD
    GOLD --> L3
    L3 --> L4
    GOLD --> L2
    L4 --> SV
    L2 --> SV
    SV --> OUT

    style L1 fill:#f0f9ff,stroke:#0284c7
    style ID fill:#f0f9ff,stroke:#0284c7
    style GOLD fill:#f0f9ff,stroke:#0284c7
    style L2 fill:#f5f3ff,stroke:#7c3aed
    style L3 fill:#f0fdf4,stroke:#16a34a
    style L4 fill:#fff7ed,stroke:#ea580c
    style SV fill:#fefce8,stroke:#ca8a04
```

## 1. Overview

```mermaid
flowchart TB
    classDef src fill:#f3f4f6,stroke:#9ca3af,color:#111827
    classDef l1 fill:#e0f2fe,stroke:#0284c7,color:#0c4a6e
    classDef l2 fill:#ede9fe,stroke:#7c3aed,color:#3b0764
    classDef l3 fill:#dcfce7,stroke:#16a34a,color:#14532d
    classDef l4 fill:#ffedd5,stroke:#ea580c,color:#7c2d12
    classDef ui fill:#fef9c3,stroke:#ca8a04,color:#713f12

    SRC["<b>Maple Bank release</b><br/>31 tables · transcripts · policy documents · data dictionary"]:::src

    subgraph L1["Layer 1 · Data Product Factory"]
        direction LR
        F1["Raw → Curated<br/>+ quality checks"]:::l1 --> F2["Identity matching<br/>with match confidence"]:::l1 --> F3[("Golden C360<br/>DuckDB · gold")]:::l1
    end

    subgraph L3["Layer 3 · Feature Store"]
        direction LR
        FS1["Structured features<br/>(SQL, Feast)"]:::l3
        FS2["Text features<br/>(notes, transcripts)"]:::l3
    end

    subgraph L4["Layer 4 · Models and Decisioning"]
        direction LR
        M1["Risk models<br/>+ rules + hardship"]:::l4 --> M2["Decision card<br/>plain-English reasons"]:::l4 --> M3{{"Human review<br/>agent · team lead"}}:::l4
    end

    subgraph L2["Layer 2 · Insight and NLP (Ask)"]
        direction LR
        A1["Guard and router"]:::l2 --> A2["Metrics · SQL · Policy RAG"]:::l2 --> A3["Answer + SQL shown<br/>or refusal"]:::l2
    end

    UI["<b>FastAPI + React</b> · RBAC · audit log"]:::ui

    SRC --> L1
    L1 --> L3
    L3 --> L4
    L1 --> L2
    L4 --> UI
    L2 --> UI

    style L1 fill:#f0f9ff,stroke:#0284c7
    style L2 fill:#f5f3ff,stroke:#7c3aed
    style L3 fill:#f0fdf4,stroke:#16a34a
    style L4 fill:#fff7ed,stroke:#ea580c
```

## 2. Layer 1: Data Product Factory

Run with `make pipeline` (`factory/pipeline.py`). A stage can resume with `--from <stage>`.

```mermaid
flowchart LR
    classDef step fill:#e0f2fe,stroke:#0284c7,color:#0c4a6e
    classDef gate fill:#fee2e2,stroke:#dc2626,color:#7f1d1d
    classDef out fill:#dcfce7,stroke:#16a34a,color:#14532d

    A["Source files<br/>CSV · Parquet · JSON"] --> B["<b>Ingest</b><br/>raw.*"]:::step
    B --> G1{"Schema drift<br/>gate"}:::gate
    G1 --> C["<b>Curate</b><br/>curated.*<br/>dates · IDs · dedupe"]:::step
    C --> D["<b>Quality</b><br/>DQ flags · anomalies<br/>text controls"]:::step
    D --> E["<b>Identity</b><br/>customer_xref"]:::step
    E --> H["<b>Gold build</b><br/>c360 · views"]:::step
    H --> G2{"Contract gate<br/>c360_customer.yaml"}:::gate
    G2 --> I[("gold.*")]:::out

    D -.-> R1["data_quality_report.md<br/>anomaly_report.md"]
    E -.-> R2["identity_report.md"]
```

### Identity matching, cheapest and surest first

```mermaid
flowchart LR
    classDef step fill:#e0f2fe,stroke:#0284c7,color:#0c4a6e
    classDef human fill:#ffedd5,stroke:#ea580c,color:#7c2d12

    N["Records from<br/>five source systems"] --> R["<b>Exact rules</b><br/>CRM pointer · national ID<br/>CIF · name+DOB+phone"]:::step
    R --> S["<b>Splink</b><br/>probabilistic score"]:::step
    S -->|"≥ 0.95"| K["Cluster<br/>golden_customer_id"]:::step
    S -->|"0.80 – 0.95"| L["LLM second opinion<br/>(off by default)"]:::step
    L -->|agrees| K
    L -->|unsure| Q{{"Human review queue"}}:::human
    S -->|"< 0.80"| X["Kept separate"]:::step
    Q --> K
```

## 3. Layer 3 and 4: features to decisions

Every feature comes from one SQL file in `features/sql/`, read by both training and live scoring.

```mermaid
flowchart TB
    classDef l3 fill:#dcfce7,stroke:#16a34a,color:#14532d
    classDef l4 fill:#ffedd5,stroke:#ea580c,color:#7c2d12
    classDef human fill:#fee2e2,stroke:#dc2626,color:#7f1d1d

    G[("gold.*")] --> SQL["<b>Feature SQL</b><br/>delinquency · liquidity · payday<br/>channel response · promise history"]:::l3
    G --> TX["<b>Text extraction</b><br/>LLM reads masked notes<br/>evidence quote must match"]:::l3
    SQL --> FE["Feast offline store<br/>one definition,<br/>training and scoring"]:::l3
    TX --> FE

    FE --> MD["<b>LightGBM</b><br/>case risk · promise break risk<br/>SHAP contributions"]:::l4
    MD --> RL["<b>Rules first</b><br/>allowed actions<br/>hardship / vulnerable → support only"]:::l4
    RL --> VAL["<b>Action value</b><br/>effect × risk × reach<br/>× amount − cost"]:::l4
    VAL --> CAP["<b>Capacity</b><br/>agents · channels"]:::l4
    CAP --> CARD["<b>Decision card</b><br/>reason in plain English"]:::l4
    CARD --> HR{{"<b>Human review</b><br/>agent accepts or overrides<br/>team lead approves"}}:::human
    HR --> OV[("gold.nba_overrides<br/>append-only")]:::l4
```

## 4. Layer 2: Ask

One entry point, `ask/service.py`, used by the app and the benchmark runner. Nothing bypasses the guard.

```mermaid
flowchart LR
    classDef step fill:#ede9fe,stroke:#7c3aed,color:#3b0764
    classDef gate fill:#fee2e2,stroke:#dc2626,color:#7f1d1d
    classDef out fill:#dcfce7,stroke:#16a34a,color:#14532d

    Q["Question"] --> GD{"<b>Guard</b><br/>injection · refusal rules<br/>access check"}:::gate
    GD -->|blocked| RF["Refused, with reason"]:::gate
    GD --> RT["<b>Router</b><br/>rules first, LLM if unplaced"]:::step

    RT --> P1["Certified metric<br/>SQL templates"]:::step
    RT --> P2["Generated SQL<br/>sqlglot validator · read-only"]:::step
    RT --> P3["Policy RAG<br/>filter → BM25 → sections"]:::step
    RT --> P4["Drivers<br/>why did X change"]:::step
    RT --> P5["Customer path<br/>one case, one customer"]:::step

    P1 --> DB[("DuckDB · gold")]
    P2 --> DB
    P4 --> DB
    P5 --> DB
    P3 --> DOC[("Policy index<br/>warehouse/rag")]

    DB --> AC["<b>Answer card</b><br/>answer · SQL or sources<br/>disclosure rules applied"]:::out
    DOC --> AC
    AC --> AU[("Audit log")]
```

## 5. Serving and governance

```mermaid
flowchart LR
    classDef ui fill:#fef9c3,stroke:#ca8a04,color:#713f12
    classDef gov fill:#f3f4f6,stroke:#6b7280,color:#111827

    U["Browser<br/>React + TypeScript"]:::ui --> API["FastAPI<br/><code>ui/tiui</code>"]:::ui
    API --> SEC["Session · MFA · rate limit<br/>RBAC · security headers"]:::gov

    SEC --> R1["/ask"]:::ui
    SEC --> R2["/nba"]:::ui
    SEC --> R3["/identity"]:::ui
    SEC --> R4["/overview · /status<br/>/tables · /oversight"]:::ui

    R1 --> C1["ask.service"]
    R2 --> C2["nba.queue · review"]
    R3 --> C3["identity review queue"]
    R4 --> C4["gold views · reports"]

    C1 & C2 & C3 & C4 --> W[("Warehouse<br/>DuckDB, read-only")]
    API --> AL[("Access log")]:::gov
```
