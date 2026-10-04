-- Interface stub tables I1-I7 (empty, right columns). Proposed for team review; see docs/interfaces.md.
-- Each owner builds against these before the real gold build lands. P1 replaces them with real views/tables
-- of the SAME shape. Changing a column is an interface change: agree it first, then edit this file.
-- Types are DuckDB types. Dates are DATE, timestamps TIMESTAMP.

CREATE SCHEMA IF NOT EXISTS gold;

-- ---------------------------------------------------------------------------------------------
-- Identity crosswalk and C360 (P1). Read by P2 (Ask schema graph), P4 (timeline), P3 (joins).
-- ---------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gold.customer_xref (
    golden_customer_id  VARCHAR,   -- 'G-000000'
    source_system       VARCHAR,   -- crm | card | loan | deposit | bureau
    source_key          VARCHAR,   -- the source customer key
    match_method        VARCHAR,   -- national_id | name_dob_phone | dob_name_postal | splink | llm_confirmed | human
    match_confidence    DOUBLE,
    status              VARCHAR    -- linked | in_review | unresolved | blocked_conflict | singleton
);

CREATE TABLE IF NOT EXISTS gold.c360_customer (
    golden_customer_id  VARCHAR,
    source_keys         VARCHAR,   -- JSON map {system: [keys]}
    match_method        VARCHAR,
    match_confidence    DOUBLE,    -- lowest link score in the cluster
    full_name           VARCHAR,   -- PII
    date_of_birth       DATE,      -- PII
    segment             VARCHAR,
    province            VARCHAR,
    consent_phone       VARCHAR,   -- most restrictive value across sources
    consent_sms         VARCHAR,
    consent_email       VARCHAR,
    consent_mail        VARCHAR,
    cease_contact       BOOLEAN,
    deceased            BOOLEAN,
    bankruptcy          BOOLEAN,
    open_dispute        BOOLEAN,
    hardship_flag       BOOLEAN,
    total_balance       DOUBLE,
    total_overdue       DOUBLE,
    max_dpd             INTEGER,
    worst_bucket        VARCHAR,
    active_promise_status VARCHAR,
    source_systems      VARCHAR,
    refresh_ts          TIMESTAMP
);

CREATE TABLE IF NOT EXISTS gold.c360_account (
    account_id          VARCHAR,
    golden_customer_id  VARCHAR,
    product             VARCHAR,
    snapshot_date       DATE,
    balance             DOUBLE,
    overdue_amount      DOUBLE,
    dpd                 INTEGER,
    bucket              VARCHAR,
    status              VARCHAR
);

-- ---------------------------------------------------------------------------------------------
-- I1: P1 -> P3
-- ---------------------------------------------------------------------------------------------
-- Allow-list view. NO protected attributes, NO direct PII, NO geo, NO age, NO accent/interpreter use.
-- GRAIN (proposed, P1+P3 to confirm): a HISTORY table, one row per (golden_customer_id, valid_from).
-- A row is valid from valid_from until the next row's valid_from. Training joins as-of ptp_created_ts using
-- available_from (when the bank/pipeline could have known the values), NOT valid_from.
CREATE TABLE IF NOT EXISTS gold.decisioning_customer (
    golden_customer_id  VARCHAR,
    segment             VARCHAR,
    province            VARCHAR,
    tenure_months       INTEGER,
    employment_status   VARCHAR,
    occupation_category VARCHAR,
    industry_naics2     VARCHAR,
    pay_frequency       VARCHAR,
    declared_annual_income DOUBLE,
    income_verified_flag BOOLEAN,
    housing_status      VARCHAR,
    monthly_housing_cost DOUBLE,
    product_count       INTEGER,
    total_balance       DOUBLE,
    total_overdue       DOUBLE,
    max_dpd             INTEGER,
    worst_bucket        VARCHAR,
    bureau_score        DOUBLE,
    bureau_score_model  VARCHAR,   -- scale differs by source (300-900 vs 300-850)
    valid_from          TIMESTAMP, -- when these values became true in the real world
    available_from      TIMESTAMP  -- when they became known to us (>= valid_from). Point-in-time join key.
);

-- One row per promise; the point-in-time spine for training. Label is derived here, one definition.
-- Columns are in two groups. CREATION-TIME columns are known when the promise is made and may feed features.
-- OUTCOME columns (below the marker) are labels/evaluation only and must NEVER be a feature input.
-- The machine-readable split is PROMISE_OUTCOME_COLUMNS in common/interfaces.py (a test keeps them in sync).
CREATE TABLE IF NOT EXISTS gold.promises_enriched (
    ptp_id              VARCHAR,
    golden_customer_id  VARCHAR,
    case_id             VARCHAR,
    account_id          VARCHAR,
    product             VARCHAR,
    agent_id            VARCHAR,
    ptp_channel         VARCHAR,
    ptp_created_ts      TIMESTAMP, -- features must use only data known at this time
    ptp_amount          DOUBLE,
    ptp_due_date        DATE,
    days_to_due_at_creation INTEGER,
    ptp_type            VARCHAR,
    ptp_sequence_in_case INTEGER,
    prior_broken_ptp_count INTEGER,
    overdue_at_creation DOUBLE,
    ptp_to_overdue_ratio DOUBLE,
    dpd_at_creation     INTEGER,
    payment_method_promised VARCHAR,
    grace_days          INTEGER,
    customer_stated_income_date DATE,
    -- ===== OUTCOME columns: label / evaluation only, never features =====
    ptp_status          VARCHAR,   -- kept | broken | partially_kept | open
    status_ts           TIMESTAMP,
    amount_paid_against DOUBLE,
    paid_date           DATE,
    broken_reason_agent_coded VARCHAR,
    label_broken        BOOLEAN    -- NULL while open; definition follows metric_definitions
);

-- Protective flags. Used by rules and hardship routing, NEVER as model inputs.
CREATE TABLE IF NOT EXISTS gold.customer_safeguards (
    golden_customer_id  VARCHAR,
    cease_contact       BOOLEAN,
    deceased            BOOLEAN,
    bankruptcy          BOOLEAN,
    open_dispute        BOOLEAN,
    hardship_flag       BOOLEAN,
    vulnerability_flag  BOOLEAN,
    accessibility_flag  BOOLEAN,
    safety_flag         BOOLEAN,
    consent_phone       VARCHAR,
    consent_sms         VARCHAR,
    consent_email       VARCHAR,
    consent_mail        VARCHAR,
    language_of_service VARCHAR,
    refresh_ts          TIMESTAMP
);

-- ---------------------------------------------------------------------------------------------
-- I2: P1 -> P2 (also referenced by P3 -> P2 via nba_decisions below)
-- ---------------------------------------------------------------------------------------------
-- semantic_* views are NOT stubbed yet: one view per certified-metric shape, agreed by P1 and P2
-- once the 15 metric_definitions are read (TBC in docs/interfaces.md).
CREATE TABLE IF NOT EXISTS gold.bucket_transitions (
    account_id          VARCHAR,
    golden_customer_id  VARCHAR,
    product             VARCHAR,
    period_start        DATE,
    period_end          DATE,
    bucket_start        VARCHAR,
    bucket_end          VARCHAR,
    dpd_start           INTEGER,
    dpd_end             INTEGER
);

-- ---------------------------------------------------------------------------------------------
-- P1 -> P4 (not numbered in the plan): redacted, eligible text only. CRM duplicates merged via xref.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gold.customer_timeline (
    golden_customer_id  VARCHAR,
    event_ts            TIMESTAMP,
    event_type          VARCHAR,   -- contact | promise | note | transcript | offer | payment
    source_table        VARCHAR,
    source_id           VARCHAR,
    account_id          VARCHAR,
    agent_id            VARCHAR,
    channel             VARCHAR,
    text                VARCHAR,   -- redacted text only; NULL when not eligible (see text_controls)
    voice_sample_id     VARCHAR    -- when a recording exists
);

-- ---------------------------------------------------------------------------------------------
-- I3: P4 -> P3
-- ---------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gold.text_features (
    golden_customer_id  VARCHAR,
    source_id           VARCHAR,   -- note or transcript id
    source_ts           TIMESTAMP, -- point-in-time key: only usable after this
    hardship_signal     BOOLEAN,
    stated_delay_reason VARCHAR,
    ptp_intent_strength VARCHAR,   -- strong | moderate | weak | none (TBC with P4)
    dispute_mentioned   BOOLEAN,
    evidence_sentence   VARCHAR,
    confidence          DOUBLE,
    prompt_version      VARCHAR
);

-- ---------------------------------------------------------------------------------------------
-- P3 -> P2 / app, and I7: App -> P3
-- ---------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gold.nba_decisions (
    recommendation_id   VARCHAR,
    golden_customer_id  VARCHAR,
    case_id             VARCHAR,
    agent_id            VARCHAR,   -- assigned agent, NULL for digital batch
    as_of_date          DATE,
    action              VARCHAR,   -- includes 'wait'
    channel             VARCHAR,
    risk_score          DOUBLE,    -- probability promise breaks
    expected_value      DOUBLE,
    requires_approval   BOOLEAN,
    approval_reason     VARCHAR,   -- hardship | vulnerability | high_balance | digital_batch
    decision_card       VARCHAR,   -- JSON, shape = DecisionCard in common/interfaces.py
    model_version       VARCHAR,
    feature_snapshot_id VARCHAR,
    created_ts          TIMESTAMP
);

CREATE TABLE IF NOT EXISTS gold.nba_overrides (
    recommendation_id   VARCHAR,
    agent_id            VARCHAR,
    decision            VARCHAR,   -- agent: accepted | overridden; approver: approved | rejected; agent: completed (a call made) (nba/review.py)
    reason_code         VARCHAR,   -- required for overridden / rejected: nba/review.py REASON_CODES; for completed: CALL_OUTCOMES
    ts                  TIMESTAMP
);

-- ---------------------------------------------------------------------------------------------
-- Cross-cutting: every LLM call and every recommendation (common/audit.py, P4)
-- ---------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gold.audit_log (
    ts                  TIMESTAMP,
    user_role           VARCHAR,
    user_id             VARCHAR,
    kind                VARCHAR,   -- ask | rag | text_feature | identity_pair | nba_reword
    input_text          VARCHAR,   -- question / recommendation id
    sources             VARCHAR,   -- JSON list of ids
    prompt              VARCHAR,
    answer              VARCHAR,
    model               VARCHAR,
    prompt_version      VARCHAR
);
