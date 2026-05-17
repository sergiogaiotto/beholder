-- ============================================================
-- Migration 005 — Rules engine + extração (Fase 1)
--
-- Cria as 4 entidades de execução de regras e extração:
--   RuleDefinition         — catálogo das 16 regras (R1, R2, R3, R4,
--                            R5.UF/CIDADE/TECNOLOGIA/ATIVIDADE/CATEGORIA/OBJETO,
--                            R6.1–6.9, LPU)
--   ReconciliationRun      — 1 execução do engine
--   ReconciliationFinding  — output principal: 1 violação de 1 regra
--   ExtractionJob          — job async de extração PDF (Fase 4)
--
-- Idempotente.
-- ============================================================

-- ============================================================
-- RuleDefinition — catálogo de regras
-- ============================================================
CREATE TABLE IF NOT EXISTS payments.rule_definition (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code                     TEXT NOT NULL UNIQUE,
    name                     TEXT NOT NULL,
    description              TEXT NOT NULL,
    severity                 TEXT NOT NULL CHECK (severity IN ('low','medium','high')),
    is_active                BOOLEAN NOT NULL DEFAULT TRUE,
    threshold_params         JSONB NOT NULL DEFAULT '{}'::jsonb,
    engine_type              TEXT NOT NULL,                    -- 'sql_deterministic' | 'fuzzy' | 'math_tolerance' | 'embedding' (deprecated v1.1.1)
    python_handler           TEXT NOT NULL,                    -- dotted path
    version                  INTEGER NOT NULL DEFAULT 1,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- ReconciliationRun — 1 execução do engine
-- ============================================================
CREATE TABLE IF NOT EXISTS payments.reconciliation_run (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    triggered_by             TEXT NOT NULL,                    -- 'manual' | 'post_ingestion' | 'scheduled'
    triggered_by_user_id     UUID REFERENCES users(id),
    rules_executed           TEXT[] NOT NULL,                  -- ['REGRA_1','REGRA_2',...]
    scope_filter             JSONB,                            -- {'empreiteira': 'ABILITY', 'since': '2024-01-01'}
    status                   TEXT NOT NULL CHECK (status IN ('running','completed','failed')),
    started_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at              TIMESTAMPTZ,
    findings_created         INTEGER NOT NULL DEFAULT 0,
    error_message            TEXT
);
CREATE INDEX IF NOT EXISTS idx_recon_run_status ON payments.reconciliation_run(status, started_at DESC);

-- ============================================================
-- ReconciliationFinding — output principal
-- ============================================================
CREATE TABLE IF NOT EXISTS payments.reconciliation_finding (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id                      UUID NOT NULL REFERENCES payments.reconciliation_run(id),
    rule_id                     UUID NOT NULL REFERENCES payments.rule_definition(id),
    rule_code                   TEXT NOT NULL,                  -- denormalizado para query rápida
    severity                    TEXT NOT NULL,
    status                      TEXT NOT NULL DEFAULT 'open'
                                CHECK (status IN ('open','in_analysis','accepted_fp','escalated','blocked')),
    -- referências ao pagamento
    purchase_order_documento    TEXT NOT NULL,                  -- join SAP
    purchase_order_item         TEXT,
    wf_payment_id               BIGINT,                         -- v1.1.1: ref WFPayment
    wf_payment_data_pedido      DATE,                           -- denormalizado p/ join particionado
    -- referências ao contrato
    contract_master_id          UUID REFERENCES payments.contract_master(id),
    contract_version_id         UUID REFERENCES payments.contract_version(id),
    supplier_id                 UUID REFERENCES payments.supplier_bridge(id),
    is_monitored_supplier       BOOLEAN NOT NULL DEFAULT TRUE,  -- v1.1.1: flag (63 empreiteiras NÃO monitoradas)
    -- corpo do finding
    expected_value              JSONB NOT NULL,
    actual_value                JSONB NOT NULL,
    delta_pct                   DOUBLE PRECISION,
    value_at_risk_brl           NUMERIC(15,2),
    evidence_clause_ids         UUID[],
    evidence_pages              INTEGER[],
    -- workflow (HITL)
    analyst_id                  UUID REFERENCES users(id),
    decision_reason             TEXT,
    decided_by_id               UUID REFERENCES users(id),
    decided_at                  TIMESTAMPTZ,
    detected_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_finding_inbox      ON payments.reconciliation_finding(status, severity, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_finding_supplier   ON payments.reconciliation_finding(supplier_id);
CREATE INDEX IF NOT EXISTS idx_finding_rule_date  ON payments.reconciliation_finding(rule_code, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_finding_wf_payment ON payments.reconciliation_finding(wf_payment_id);
CREATE INDEX IF NOT EXISTS idx_finding_monitored  ON payments.reconciliation_finding(is_monitored_supplier, detected_at DESC);

COMMENT ON TABLE payments.reconciliation_finding IS
    'Findings determinísticos/fuzzy (R1-R6.9, LPU). Análogos estatísticos (R7) vão em analytic_finding.';

-- ============================================================
-- ExtractionJob — extração assíncrona de PDF (Fase 4)
-- ============================================================
CREATE TABLE IF NOT EXISTS payments.extraction_job (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contract_master_id          UUID REFERENCES payments.contract_master(id),
    pdf_storage_key             TEXT NOT NULL,                  -- chave no DocumentStore
    pdf_filename                TEXT NOT NULL,
    pdf_size_bytes              BIGINT NOT NULL,
    pdf_pages                   INTEGER,
    status                      TEXT NOT NULL
                                CHECK (status IN ('pending','extracting','review','approved','failed')),
    extraction_started_at       TIMESTAMPTZ,
    extraction_finished_at      TIMESTAMPTZ,
    extracted_fields            JSONB,                          -- folha de rosto + LPU items (pré-aprove)
    confidence_per_field        JSONB,                          -- {'val_fix_cab': 0.95, 'objeto': 0.78}
    llm_model_used              TEXT,                           -- 'sabia-4' default v1.1
    cost_brl                    NUMERIC(10,4) NOT NULL DEFAULT 0,
    error_message               TEXT,
    uploaded_by_id              UUID NOT NULL REFERENCES users(id),
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_extraction_status ON payments.extraction_job(status, created_at DESC);

COMMENT ON TABLE payments.extraction_job IS
    'Job assíncrono de extração PDF — alimentado pelo worker dramatiq. Fase 4.';
