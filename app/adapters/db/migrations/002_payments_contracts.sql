-- ============================================================
-- Migration 002 — Contratos (Fase 1)
--
-- Cria as 5 entidades de contratos/preços:
--   SupplierBridge    — tabela-âncora DE-PARA (147 rows)
--   ContractMaster    — contrato jurídico (cabeça)
--   ContractVersion   — versão temporal (aditivos)
--   LPUItem           — Lista de Preços Unitários (particionada por ano)
--   ContractClause    — cláusulas com embedding (pgvector)
--
-- Particionamento de lpu_item:
--   Volume real (Pré-B): 2.909.412 rows do MSRV5 + crescimento.
--   Distribuição: 296k(2018) → 339k(2019) → ... → 560k(2022, pico) → 343k(2025).
--   Partições anuais 2018-2026 + DEFAULT pra cobrir tudo.
--
-- Idempotente.
-- ============================================================

-- ============================================================
-- SupplierBridge — tabela-âncora DE-PARA
-- ============================================================
CREATE TABLE IF NOT EXISTS payments.supplier_bridge (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    categoria                TEXT NOT NULL,
    empreiteira              TEXT NOT NULL,
    contrato_num_sap         TEXT NOT NULL,
    ref_ws                   TEXT NOT NULL,
    numero_fornecedor_sap    TEXT NOT NULL,
    cnpj                     TEXT NOT NULL,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (contrato_num_sap, ref_ws)
);
CREATE INDEX IF NOT EXISTS idx_supplier_contrato ON payments.supplier_bridge(contrato_num_sap);
CREATE INDEX IF NOT EXISTS idx_supplier_cnpj     ON payments.supplier_bridge(cnpj);
CREATE INDEX IF NOT EXISTS idx_supplier_ref_ws   ON payments.supplier_bridge(ref_ws);
CREATE INDEX IF NOT EXISTS idx_supplier_empreiteira ON payments.supplier_bridge(empreiteira);

COMMENT ON TABLE payments.supplier_bridge IS
    'DE-PARA contrato SAP ↔ REF WS ↔ CNPJ. 147 linhas iniciais do XLSX Contratos-Empreteiras.';

-- ============================================================
-- ContractMaster + ContractVersion (temporal)
-- ============================================================
CREATE TABLE IF NOT EXISTS payments.contract_master (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    supplier_bridge_id    UUID NOT NULL REFERENCES payments.supplier_bridge(id),
    contrato_num_sap      TEXT NOT NULL,
    ref_ws                TEXT NOT NULL,
    cnpj                  TEXT NOT NULL,
    -- current_version_id é populado após contract_version criada; FK adicionada abaixo
    current_version_id    UUID,
    is_monitored          BOOLEAN NOT NULL DEFAULT TRUE,
    created_by_id         UUID NOT NULL REFERENCES users(id),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_contract_master_supplier  ON payments.contract_master(supplier_bridge_id);
CREATE INDEX IF NOT EXISTS idx_contract_master_contrato  ON payments.contract_master(contrato_num_sap);
CREATE INDEX IF NOT EXISTS idx_contract_master_monitored ON payments.contract_master(is_monitored)
    WHERE is_monitored;

CREATE TABLE IF NOT EXISTS payments.contract_version (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contract_master_id       UUID NOT NULL REFERENCES payments.contract_master(id) ON DELETE CASCADE,
    version_number           INTEGER NOT NULL,
    valid_from               DATE NOT NULL,
    valid_to                 DATE NOT NULL,
    val_fix_cab              NUMERIC(15,2),
    objeto_contrato          TEXT,
    tecnologia               TEXT,
    atividade                TEXT,
    uf                       TEXT[],                       -- regiões: ['RJ','ES']
    cidade                   TEXT[],
    pdf_storage_key          TEXT,                         -- chave no DocumentStore
    extracted_by_llm_model   TEXT,                         -- 'sabia-4', 'openai/gpt-oss-20b'
    extracted_cost_brl       NUMERIC(10,4) NOT NULL DEFAULT 0,
    confidence_avg           DOUBLE PRECISION,
    reviewed_by_id           UUID REFERENCES users(id),
    reviewed_at              TIMESTAMPTZ,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (contract_master_id, version_number)
);
CREATE INDEX IF NOT EXISTS idx_contract_version_temporal
    ON payments.contract_version(contract_master_id, valid_from, valid_to);

-- FK circular master → version (resolve após version criada)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_cm_current_version'
    ) THEN
        ALTER TABLE payments.contract_master
            ADD CONSTRAINT fk_cm_current_version
            FOREIGN KEY (current_version_id) REFERENCES payments.contract_version(id);
    END IF;
END $$;

-- ============================================================
-- LPUItem — particionada por ano (data_documento)
-- ============================================================
-- v1.1: 3.1M linhas reais do MSRV5 (Pré-B). Particionamento anual evita
-- index bloat e permite drop de partições antigas (Pré-B confirmou
-- distribuição 2018-2026).
CREATE TABLE IF NOT EXISTS payments.lpu_item (
    id                       BIGSERIAL,
    contract_version_id      UUID REFERENCES payments.contract_version(id) ON DELETE CASCADE,
    documento_compras        TEXT NOT NULL,
    item                     INTEGER,
    numero_servico           TEXT NOT NULL,
    data_documento           DATE NOT NULL,
    preco_unitario           NUMERIC(18,4) NOT NULL,
    qtd_solicitada           NUMERIC(18,3),
    moeda                    TEXT NOT NULL DEFAULT 'BRL',
    descricao                TEXT,
    texto_breve              TEXT,
    pagina_pdf               INTEGER,
    clausula_ref             TEXT,
    extracted_by_llm         BOOLEAN NOT NULL DEFAULT FALSE,
    confidence               DOUBLE PRECISION,
    source                   TEXT NOT NULL DEFAULT 'msrv5'
                             CHECK (source IN ('msrv5','pdf','manual','xlsx')),
    raw_extra                JSONB NOT NULL DEFAULT '{}'::jsonb,
    ingestion_run_id         UUID REFERENCES payments.ingestion_run(id),
    PRIMARY KEY (id, data_documento)
) PARTITION BY RANGE (data_documento);

-- Partições anuais 2018-2026 (volume real MSRV5: pico 2022 com 560k linhas)
CREATE TABLE IF NOT EXISTS payments.lpu_item_2018 PARTITION OF payments.lpu_item
    FOR VALUES FROM ('2018-01-01') TO ('2019-01-01');
CREATE TABLE IF NOT EXISTS payments.lpu_item_2019 PARTITION OF payments.lpu_item
    FOR VALUES FROM ('2019-01-01') TO ('2020-01-01');
CREATE TABLE IF NOT EXISTS payments.lpu_item_2020 PARTITION OF payments.lpu_item
    FOR VALUES FROM ('2020-01-01') TO ('2021-01-01');
CREATE TABLE IF NOT EXISTS payments.lpu_item_2021 PARTITION OF payments.lpu_item
    FOR VALUES FROM ('2021-01-01') TO ('2022-01-01');
CREATE TABLE IF NOT EXISTS payments.lpu_item_2022 PARTITION OF payments.lpu_item
    FOR VALUES FROM ('2022-01-01') TO ('2023-01-01');
CREATE TABLE IF NOT EXISTS payments.lpu_item_2023 PARTITION OF payments.lpu_item
    FOR VALUES FROM ('2023-01-01') TO ('2024-01-01');
CREATE TABLE IF NOT EXISTS payments.lpu_item_2024 PARTITION OF payments.lpu_item
    FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
CREATE TABLE IF NOT EXISTS payments.lpu_item_2025 PARTITION OF payments.lpu_item
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE IF NOT EXISTS payments.lpu_item_2026 PARTITION OF payments.lpu_item
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
CREATE TABLE IF NOT EXISTS payments.lpu_item_default PARTITION OF payments.lpu_item DEFAULT;

CREATE INDEX IF NOT EXISTS idx_lpu_version    ON payments.lpu_item(contract_version_id);
CREATE INDEX IF NOT EXISTS idx_lpu_servico    ON payments.lpu_item(numero_servico);
CREATE INDEX IF NOT EXISTS idx_lpu_doc_item   ON payments.lpu_item(documento_compras, item);
CREATE INDEX IF NOT EXISTS idx_lpu_source     ON payments.lpu_item(source);
CREATE INDEX IF NOT EXISTS idx_lpu_ingestion  ON payments.lpu_item(ingestion_run_id);

-- ============================================================
-- ContractClause — texto + embedding pgvector
-- ============================================================
CREATE TABLE IF NOT EXISTS payments.contract_clause (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contract_version_id      UUID NOT NULL REFERENCES payments.contract_version(id) ON DELETE CASCADE,
    clausula_numero          TEXT,
    secao                    TEXT,
    texto                    TEXT NOT NULL,
    embedding                vector(1536),                 -- OpenAI-compatible 1536d
    pagina_pdf               INTEGER,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_clause_version ON payments.contract_clause(contract_version_id, secao);
-- ivfflat para similarity search (cosine). lists=100 é razoável para até ~100k clauses.
-- Habilita só se a tabela tem >1k rows (ivfflat falha se < lists*30).
CREATE INDEX IF NOT EXISTS idx_clause_embedding
    ON payments.contract_clause
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

COMMENT ON TABLE payments.contract_clause IS
    'Cláusulas do contrato com embedding para similarity search. Fase 4.';
