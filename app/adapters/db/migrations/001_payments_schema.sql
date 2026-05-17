-- ============================================================
-- Migration 001 — Payments schema (Fase 0)
--
-- Cria:
--   1. Schema isolado `payments` (todas as tabelas de Empreiteiras-WF
--      ficam aqui, separadas do schema `public` dos outros domínios)
--   2. Extension `vector` (pgvector) — usada por contract_clause embeddings
--      (Fase 4) e analytics R7 (Fase 2.5)
--   3. Tabela `ingestion_run` — rastreabilidade de cada carga XLSX/TXT
--      (referenciada por wf_payment.ingestion_run_id na Fase 1)
--   4. Materialized view stub `mv_kpis_empreiteiras_wf` — placeholder pro
--      dashboard da Fase 3; refreshable desde já
--
-- Idempotente: pode ser aplicada múltiplas vezes sem erro.
-- ============================================================

CREATE SCHEMA IF NOT EXISTS payments;
-- pgvector instalado SEMPRE em `public` (não no search_path corrente). Razão:
-- testes usam schemas isolados que podem ser dropados (CASCADE). Se a extension
-- cair junto, pg_extension fica órfão e migrations futuras veem `type vector
-- not found` mesmo com IF NOT EXISTS. Em `public`, persiste — e public é
-- sempre o fallback do search_path, então `vector(1536)` resolve normalmente.
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;

-- ============================================================
-- IngestionRun — rastreabilidade de cargas de XLSX/TXT/PDF
-- ============================================================
-- Cada job de ingestão (Polars carregando EKPO.xlsx, parser do MSRV5,
-- upload de PDF) cria um row aqui. Toda linha em wf_payment/lpu_item/etc
-- aponta de volta via ingestion_run_id — permite reverter/auditar uma
-- carga inteira sem afetar outras.
CREATE TABLE IF NOT EXISTS payments.ingestion_run (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type              TEXT NOT NULL,                  -- 'xlsx'|'msrv5_txt'|'analitico_wf'|'pdf'
    source_filename          TEXT NOT NULL,
    source_sha256            TEXT,                           -- hash do arquivo (DATA_INVENTORY)
    source_size_bytes        BIGINT,
    target_table             TEXT NOT NULL,                  -- 'payments.wf_payment', etc.
    status                   TEXT NOT NULL DEFAULT 'pending'
                             CHECK (status IN ('pending','running','completed','failed','rolled_back')),
    rows_read                BIGINT NOT NULL DEFAULT 0,
    rows_inserted            BIGINT NOT NULL DEFAULT 0,
    rows_skipped             BIGINT NOT NULL DEFAULT 0,
    rows_failed              BIGINT NOT NULL DEFAULT 0,
    started_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at              TIMESTAMPTZ,
    error_message            TEXT,
    triggered_by_user_id     UUID REFERENCES users(id),
    metadata                 JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_ingestion_status_started
    ON payments.ingestion_run(status, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_ingestion_target
    ON payments.ingestion_run(target_table, started_at DESC);

-- ============================================================
-- Materialized view stub — placeholder pro dashboard Fase 3
-- ============================================================
-- Real KPIs serão calculados na Fase 3 a partir de wf_payment +
-- reconciliation_finding (ainda não existem). Por ora, retornamos zero
-- com a SHAPE correta para que o app possa montar o template do
-- dashboard sem erro mesmo antes da Fase 1 carregar dados.
--
-- `singleton_key` é uma coluna real (não expressão) para que REFRESH
-- CONCURRENTLY funcione — Postgres exige unique index em coluna(s) real(is)
-- da matview, não em expressão constante.
CREATE MATERIALIZED VIEW IF NOT EXISTS payments.mv_kpis_empreiteiras_wf AS
SELECT
    'kpis'::text           AS singleton_key,
    0::bigint              AS contratos_monitorados,
    0::bigint              AS contratos_total,
    0::bigint              AS os_analisadas,
    0::bigint              AS total_alertas,
    0::numeric(18,2)       AS risco_brl,
    0::numeric(18,2)       AS valor_total_brl,
    0::numeric(18,2)       AS comparativo_lpu_brl,
    0::numeric(10,4)       AS delta_medio_lpu_pct,
    0::numeric(5,2)        AS taxa_recorrencia_pct,
    0::numeric(10,2)       AS tempo_medio_deteccao_dias,
    0::bigint              AS regras_ativas,
    NOW()                  AS refreshed_at;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_kpis_singleton
    ON payments.mv_kpis_empreiteiras_wf(singleton_key);

-- Helper de refresh (chamado pelo worker pós-ingestão / pós-reconciliação).
-- CONCURRENTLY exige unique index em coluna real — `singleton_key` cobre.
CREATE OR REPLACE FUNCTION payments.refresh_kpis() RETURNS void AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY payments.mv_kpis_empreiteiras_wf;
END;
$$ LANGUAGE plpgsql;

-- Comentários para auditoria
COMMENT ON SCHEMA payments
    IS 'Empreiteiras-WF — pagamentos, contratos, regras, analytics R7. Isolado do schema public.';
COMMENT ON TABLE payments.ingestion_run
    IS 'Rastreabilidade de cada carga de dados. ingestion_run_id em wf_payment/lpu_item/etc aponta aqui.';
COMMENT ON MATERIALIZED VIEW payments.mv_kpis_empreiteiras_wf
    IS 'KPIs do dashboard de Empreiteiras-WF. Stub Fase 0 (zeros); preenchida na Fase 3 com queries reais.';
