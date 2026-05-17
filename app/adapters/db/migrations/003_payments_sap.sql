-- ============================================================
-- Migration 003 — SAP (Fase 1)
--
-- Cria as 5 entidades projetadas dos XLSX SAP:
--   PurchaseOrderHeader  — EKKO (1.894+138 rows)
--   PurchaseOrderItem    — EKPO (25k+44.7k rows)
--   ServicePackage       — ESLL (44.7k rows)
--   PurchaseOrderGc      — sheet "Contratos Guarda Chuvas" (44.7k rows) [v1.1]
--   CostCenterAccount    — sheet "CC + CONTA" do Analítico WF (1.049 rows) [v1.1]
--
-- Idempotente.
-- ============================================================

-- ============================================================
-- PurchaseOrderHeader — EKKO (179 cols → 13 tipadas + raw_extra)
-- ============================================================
CREATE TABLE IF NOT EXISTS payments.purchase_order_header (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    documento_compras        TEXT NOT NULL UNIQUE,
    empresa                  TEXT NOT NULL,
    categoria_doc            TEXT,                           -- 'K' = guarda-chuva, 'F' = pedido
    tipo_doc                 TEXT,
    fornecedor               TEXT NOT NULL,                  -- match SupplierBridge.numero_fornecedor_sap
    contrato_basico          TEXT,                           -- refer ao guarda-chuva (R6.3)
    data_documento           DATE,
    inicio_validade          DATE,
    fim_validade             DATE,
    val_fix_cab              NUMERIC(15,2),
    moeda                    TEXT NOT NULL DEFAULT 'BRL',
    status                   TEXT,
    raw_extra                JSONB NOT NULL DEFAULT '{}'::jsonb,
    ingestion_run_id         UUID REFERENCES payments.ingestion_run(id),
    imported_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ekko_fornecedor       ON payments.purchase_order_header(fornecedor);
CREATE INDEX IF NOT EXISTS idx_ekko_contrato_basico  ON payments.purchase_order_header(contrato_basico);
CREATE INDEX IF NOT EXISTS idx_ekko_validade         ON payments.purchase_order_header(inicio_validade, fim_validade);
CREATE INDEX IF NOT EXISTS idx_ekko_categoria        ON payments.purchase_order_header(categoria_doc);
CREATE INDEX IF NOT EXISTS idx_ekko_ingestion        ON payments.purchase_order_header(ingestion_run_id);

-- ============================================================
-- PurchaseOrderItem — EKPO (283 cols → 12 tipadas + raw_extra)
-- ============================================================
CREATE TABLE IF NOT EXISTS payments.purchase_order_item (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    documento_compras        TEXT NOT NULL,                  -- FK lógica → purchase_order_header.documento_compras
    item                     TEXT NOT NULL,
    texto_breve              TEXT,
    material                 TEXT,
    grupo_mercadorias        TEXT,
    quantidade               NUMERIC(15,4),
    unidade_medida           TEXT,
    preco_liquido            NUMERIC(15,4),
    valor_liquido            NUMERIC(15,2),                  -- usado por R6.5
    centro                   TEXT,
    categoria_item           TEXT,
    raw_extra                JSONB NOT NULL DEFAULT '{}'::jsonb,
    ingestion_run_id         UUID REFERENCES payments.ingestion_run(id),
    imported_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (documento_compras, item)
);
CREATE INDEX IF NOT EXISTS idx_ekpo_grupo      ON payments.purchase_order_item(grupo_mercadorias);
CREATE INDEX IF NOT EXISTS idx_ekpo_doc        ON payments.purchase_order_item(documento_compras);
CREATE INDEX IF NOT EXISTS idx_ekpo_ingestion  ON payments.purchase_order_item(ingestion_run_id);

-- ============================================================
-- ServicePackage — ESLL (10 cols)
-- ============================================================
CREATE TABLE IF NOT EXISTS payments.service_package (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pacote                   TEXT NOT NULL,
    linha                    INTEGER NOT NULL,
    numero_servico           TEXT NOT NULL,                  -- match LPUItem.numero_servico
    texto_breve              TEXT,
    preco_bruto              NUMERIC(15,4),                  -- usado por R LPU (bate com LPUItem.preco_unitario)
    qtd_solicitada           NUMERIC(15,4),
    valor_solicitado         NUMERIC(15,2),
    ekpo_documento           TEXT,                           -- join com EKPO
    ekpo_item                TEXT,
    raw_extra                JSONB NOT NULL DEFAULT '{}'::jsonb,
    ingestion_run_id         UUID REFERENCES payments.ingestion_run(id),
    imported_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (pacote, linha)
);
CREATE INDEX IF NOT EXISTS idx_esll_servico   ON payments.service_package(numero_servico);
CREATE INDEX IF NOT EXISTS idx_esll_ekpo      ON payments.service_package(ekpo_documento, ekpo_item);
CREATE INDEX IF NOT EXISTS idx_esll_ingestion ON payments.service_package(ingestion_run_id);

-- ============================================================
-- PurchaseOrderGc — sheet "Contratos Guarda Chuvas" [v1.1]
-- ============================================================
-- Cruzamento pré-processado EKPO+ESLL+LPU para os guarda-chuvas monitorados.
-- Referência "GC" nas sub-regras 6.6-6.9 do DOCX original.
CREATE TABLE IF NOT EXISTS payments.purchase_order_gc (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    documento_compras        TEXT NOT NULL,                  -- R6.6 × WF.contrato_num
    item                     TEXT NOT NULL,                  -- R6.7 × WF.item_num
    ult_modif_dia            DATE,
    texto_breve              TEXT,                           -- R6.8 × WF.item_descricao
    empresa                  TEXT,
    numero_pacote_ekpo       TEXT,
    pacote_esll              TEXT,
    inicio_validade          DATE,
    fim_validade             DATE,
    val_fix_cab              NUMERIC(15,2),
    preco_bruto_lpu          NUMERIC(15,4),                  -- R6.9 × WF.valor_unitario (tolerância)
    numero_servico           TEXT,
    texto_breve_servico      TEXT,
    raw_extra                JSONB NOT NULL DEFAULT '{}'::jsonb,
    ingestion_run_id         UUID REFERENCES payments.ingestion_run(id),
    imported_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (documento_compras, item)
);
CREATE INDEX IF NOT EXISTS idx_gc_servico   ON payments.purchase_order_gc(numero_servico);
CREATE INDEX IF NOT EXISTS idx_gc_doc       ON payments.purchase_order_gc(documento_compras);
CREATE INDEX IF NOT EXISTS idx_gc_ingestion ON payments.purchase_order_gc(ingestion_run_id);

COMMENT ON TABLE payments.purchase_order_gc IS
    'Cruzamento pré-processado EKPO+ESLL+LPU para guarda-chuvas (44.782 rows). v1.1: tabela física na Fase 1 (D1 aprovada); Fase 3 reavalia matview derivada.';

-- ============================================================
-- CostCenterAccount — sheet "CC + CONTA" do Analítico WF [v1.1]
-- ============================================================
CREATE TABLE IF NOT EXISTS payments.cost_center_account (
    id                       SERIAL PRIMARY KEY,
    centro_de_custo          TEXT NOT NULL,
    conta_razao              TEXT NOT NULL,
    ingestion_run_id         UUID REFERENCES payments.ingestion_run(id),
    imported_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (centro_de_custo, conta_razao)
);
CREATE INDEX IF NOT EXISTS idx_cca_cc ON payments.cost_center_account(centro_de_custo);

COMMENT ON TABLE payments.cost_center_account IS
    'Mapping centro_de_custo ↔ conta_razao (1.049 rows). Apoio para R7 análises orçamentárias.';
