-- Koramangala Projects database schema (SQLite) — Phase 2 "Project Truth" model

CREATE TABLE sources (
  id TEXT PRIMARY KEY,            -- S01.. CSV files, KPPP, OC-TENDERS
  name TEXT NOT NULL,
  kind TEXT NOT NULL,
  publisher TEXT,
  dataset_page TEXT,
  download_url TEXT,
  original_portal TEXT,
  local_file TEXT
);

-- One row per normalised project. Flat columns mirror the facts table for listing/search.
CREATE TABLE projects (
  id INTEGER PRIMARY KEY,
  slug TEXT UNIQUE NOT NULL,
  title TEXT NOT NULL,
  category TEXT,
  location TEXT,
  wards TEXT,
  department TEXT,
  office TEXT,
  job_numbers TEXT,
  tender_numbers TEXT,
  status TEXT,
  estimated_cost REAL,
  contract_value REAL,
  contractor TEXT,
  payments_gross REAL,
  tender_published TEXT,
  awarded_date TEXT,
  work_order_date TEXT,
  completion_date TEXT,
  expected_completion TEXT,
  last_payment TEXT,
  first_date TEXT,
  last_activity TEXT,
  n_bills INTEGER,
  lifecycle INTEGER,              -- number of stages (planned/awarded/money/outcome) with evidence, 0-4
  stages TEXT,                    -- JSON {stage: bool}
  link_confidence TEXT,           -- best tender<->bills link: exact | high | medium | none
  contractor_entity TEXT,         -- resolved contractor entity (contractors.entity)
  n_signals INTEGER,
  signal_level TEXT,              -- highest signal severity
  scope_reason TEXT,
  search_text TEXT
);

-- Every important fact, classified: confirmed | inferred | unknown, with evidence.
CREATE TABLE facts (
  project_id INTEGER REFERENCES projects(id),
  field TEXT,
  stage TEXT,                     -- planned | awarded | money | outcome
  value TEXT,
  confidence TEXT CHECK (confidence IN ('confirmed','inferred','unknown')),
  explanation TEXT,
  via TEXT,                       -- how the record carrying this fact was linked (if inferred by matching)
  evidence TEXT                   -- JSON list of {source_id,label,url,rows?,note?}
);

-- Tender <-> job-number links (accepted and merely possible), with the evidence used.
CREATE TABLE links (
  project_id INTEGER REFERENCES projects(id),
  tender_base TEXT,
  tender_source TEXT,
  status TEXT,                    -- accepted | possible
  confidence TEXT,                -- high | medium | possible
  score REAL,
  text_similarity REAL,
  support TEXT,                   -- JSON list of corroborating evidence
  conflict TEXT,                  -- JSON list of contradicting evidence
  tender_title TEXT,
  tender_url TEXT
);

CREATE TABLE signals (
  project_id INTEGER REFERENCES projects(id),
  code TEXT,
  severity TEXT,                  -- info | notice | attention
  title TEXT,
  detail TEXT,
  confidence TEXT,
  evidence TEXT,
  related TEXT                    -- JSON list of related project slugs
);

-- Bills and tender notices merged into each project.
CREATE TABLE records (
  id INTEGER PRIMARY KEY,
  project_id INTEGER REFERENCES projects(id),
  record_type TEXT,               -- bill | tender
  link TEXT,                      -- exact (same id) | high | medium (matched)
  source_id TEXT,
  source_row INTEGER,
  job_number TEXT,
  tender_number TEXT,
  description TEXT,
  ward TEXT,
  contractor TEXT,
  bill_type TEXT,
  gross REAL,
  deduction REAL,
  nett REAL,
  br_no TEXT,
  br_date TEXT,
  payment_ref TEXT,
  payment_date TEXT,
  payment_status TEXT,
  wo_no TEXT,
  wo_date TEXT,
  start_date TEXT,
  end_date TEXT,
  dedupe_key TEXT,
  contractor_entity TEXT,
  source_url TEXT,
  extra TEXT
);

-- Resolved contractor entities (see ingest/contractors.py). basis = confirmed only when every
-- join inside the entity is an official identifier (IFMS code, KPPP supplierId/display name).
CREATE TABLE contractors (
  entity TEXT PRIMARY KEY, name TEXT, aliases TEXT, ids TEXT, basis TEXT, rules TEXT, evidence TEXT, systems TEXT,
  projects INTEGER, amount REAL, share REAL, bids INTEGER, wins INTEGER, single_wins INTEGER, slugs TEXT
);

CREATE TABLE co_bidding (
  a_entity TEXT, b_entity TEXT, a_name TEXT, b_name TEXT, tenders INTEGER, a_wins INTEGER, b_wins INTEGER, tender_list TEXT
);

-- Phase 5: every Koramangala work bill found in the public IFMS payment grid (one row per bill)
CREATE TABLE discovery (
  bill_id TEXT PRIMARY KEY, job_number TEXT, bill_no TEXT, bill_date TEXT, description TEXT, ward TEXT, regime TEXT,
  ddo TEXT, contractor TEXT, bill_type TEXT, gross REAL, nett REAL, deduction REAL, rtgs_no TEXT, rtgs_dates TEXT,
  cbr_no TEXT, cbr_date TEXT, scope_basis TEXT, scope_reason TEXT, job_previously_known INTEGER, bill_previously_known INTEGER,
  in_project TEXT, source_url TEXT
);

-- Phase 5: planned vs billed / measured line items (ingest.quantities). Every row keeps its provenance:
-- attachment URL, page, raw OCR row, method, confidence, cross-validation (quantity x rate = amount).
CREATE TABLE work_items (
  project_id INTEGER, description TEXT, normalized_category TEXT, code TEXT, unit TEXT, plan_basis TEXT,
  estimated_quantity REAL, estimated_rate REAL, estimated_amount REAL, measured_quantity REAL,
  billed_quantity REAL, billed_rate REAL, billed_amount REAL, compared_quantity REAL, compared_basis TEXT,
  variance_pct REAL, n_bills INTEGER, min_confidence TEXT, cross_validated INTEGER, provenance TEXT
);

-- Phase 6: investigation cases (ingest.cases). The full case — issues, evidence, questions, RTI — is the JSON
-- column; it references facts / work_items / provenance rather than restating them.
-- Phase 7: explorer filters (what kinds of evidence a project has)
CREATE TABLE project_flags (
  project_id INTEGER PRIMARY KEY, has_contract INTEGER, has_bills INTEGER, has_completion INTEGER,
  has_photos INTEGER, has_quantities INTEGER, has_signals INTEGER, has_case INTEGER
);

CREATE TABLE cases (
  case_id TEXT PRIMARY KEY, project_id INTEGER, job_number TEXT, rank INTEGER, retained INTEGER, status TEXT,
  confidence TEXT, case_score REAL, primary_issue TEXT, why TEXT, data TEXT
);

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);

CREATE INDEX idx_facts_project ON facts(project_id);
CREATE INDEX idx_records_project ON records(project_id);
CREATE INDEX idx_signals_project ON signals(project_id);
CREATE INDEX idx_links_project ON links(project_id);
