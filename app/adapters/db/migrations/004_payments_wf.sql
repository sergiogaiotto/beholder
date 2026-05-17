-- ============================================================
-- Migration 004 — WFPayment particionado (Fase 1)
--
-- Fonte primária dos pagamentos WF1/WF2. 869.663 linhas iniciais do
-- Analítico WF (Pré-B confirmou cardinalidades e taxonomias).
--
-- v1.1.1: 30 colunas tipadas (após Pré-B descobrir 12 cores adicionais
-- além das previstas na v1.1). raw_extra absorve ~50 cols restantes
-- (vestigiais com 0% populated + opcionais).
--
-- Particionamento: por trimestre de data_pedido — pico ~250k/trim;
-- permite arquivamento de partições antigas. 2024Q4 → 2026Q2 cobre
-- janela operacional; DEFAULT cobre dados fora dessa janela.
--
-- Idempotente.
-- ============================================================

CREATE TABLE IF NOT EXISTS payments.wf_payment (
    id                       BIGSERIAL,
    -- chaves de negócio
    os_num                   TEXT NOT NULL,
    sistema                  TEXT,                            -- WF1 ou WF2 (taxonomia 2 valores)
    pedido_num               TEXT,
    contrato_num             TEXT,
    item_num                 TEXT,
    item_descricao           TEXT,
    material_servico_num     TEXT,                            -- 912 únicos; chave LPU
    data_pedido              DATE NOT NULL,
    data_execucao            DATE,
    -- valores
    valor_total_final        NUMERIC(18,2),                   -- pago após DE-PARA (R6.5)
    valor_unitario           NUMERIC(18,4),
    valor_unitario_para      NUMERIC(18,4),
    -- escopo estruturado (R5) — todas taxonomias controladas
    categoria                TEXT,                            -- 11 vals
    uf                       TEXT,                            -- 27 vals (estados BR)
    cidade                   TEXT,                            -- ≥1k vals
    tecnologia               TEXT,                            -- 35 vals
    atividade                TEXT,                            -- 56 vals
    objeto_do_contrato       TEXT,                            -- 598 vals (taxonomia, não texto livre!)
    -- tipos contratuais (R7 / R LPU)
    tipo_de_lpu              TEXT,                            -- FIXO MENSAL / LPU MEDIÇÃO / LPU REFERENCIAL
    tipo_de_despesa          TEXT,                            -- CAPEX / OPEX
    -- contexto operacional (filtro universal)
    empreiteira              TEXT,                            -- 210 únicos (vs 147 monitoradas)
    fase_atual               TEXT,                            -- 34 vals
    status_os                TEXT,                            -- 5 vals; filtro: ('EXECUTADO','EM EXECUÇÃO')
    nivel_gerencial          TEXT,                            -- 5 vals; filtro: ('Em Pagamento','Medido')
    malogro                  TEXT,                            -- filtro: != 'ERROR'
    -- contexto financeiro/temporal
    mes_medicao              TEXT,                            -- "YYYY/MM"
    regional_soe_nova        TEXT,                            -- 6 vals (CONO, MG, NE, RJ/ES, SP, SUL)
    centro_de_custo          TEXT,                            -- 360 únicos
    -- catchall
    raw_extra                JSONB NOT NULL DEFAULT '{}'::jsonb,
    ingestion_run_id         UUID REFERENCES payments.ingestion_run(id),
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id, data_pedido)
) PARTITION BY RANGE (data_pedido);

-- Partições por trimestre — cobertura operacional 2024Q4 → 2026Q2
CREATE TABLE IF NOT EXISTS payments.wf_payment_2024_q4 PARTITION OF payments.wf_payment
    FOR VALUES FROM ('2024-10-01') TO ('2025-01-01');
CREATE TABLE IF NOT EXISTS payments.wf_payment_2025_q1 PARTITION OF payments.wf_payment
    FOR VALUES FROM ('2025-01-01') TO ('2025-04-01');
CREATE TABLE IF NOT EXISTS payments.wf_payment_2025_q2 PARTITION OF payments.wf_payment
    FOR VALUES FROM ('2025-04-01') TO ('2025-07-01');
CREATE TABLE IF NOT EXISTS payments.wf_payment_2025_q3 PARTITION OF payments.wf_payment
    FOR VALUES FROM ('2025-07-01') TO ('2025-10-01');
CREATE TABLE IF NOT EXISTS payments.wf_payment_2025_q4 PARTITION OF payments.wf_payment
    FOR VALUES FROM ('2025-10-01') TO ('2026-01-01');
CREATE TABLE IF NOT EXISTS payments.wf_payment_2026_q1 PARTITION OF payments.wf_payment
    FOR VALUES FROM ('2026-01-01') TO ('2026-04-01');
CREATE TABLE IF NOT EXISTS payments.wf_payment_2026_q2 PARTITION OF payments.wf_payment
    FOR VALUES FROM ('2026-04-01') TO ('2026-07-01');
CREATE TABLE IF NOT EXISTS payments.wf_payment_default PARTITION OF payments.wf_payment DEFAULT;

-- Índices principais
CREATE INDEX IF NOT EXISTS idx_wf_os               ON payments.wf_payment(os_num);
CREATE INDEX IF NOT EXISTS idx_wf_pedido           ON payments.wf_payment(pedido_num);
CREATE INDEX IF NOT EXISTS idx_wf_contrato         ON payments.wf_payment(contrato_num);
CREATE INDEX IF NOT EXISTS idx_wf_servico          ON payments.wf_payment(material_servico_num);
CREATE INDEX IF NOT EXISTS idx_wf_empreiteira_data ON payments.wf_payment(empreiteira, data_pedido);
CREATE INDEX IF NOT EXISTS idx_wf_ingestion        ON payments.wf_payment(ingestion_run_id);

-- Índice parcial para o filtro universal (R1–R6.9): otimiza queries que
-- aplicam o filtro `status_os IN (...) AND nivel_gerencial IN (...) AND malogro <> 'ERROR'`
-- (ver SDD §9 v1.1.1 prefácio).
CREATE INDEX IF NOT EXISTS idx_wf_universe ON payments.wf_payment(empreiteira, data_pedido)
    WHERE status_os IN ('EXECUTADO', 'EM EXECUÇÃO')
      AND nivel_gerencial IN ('Em Pagamento', 'Medido')
      AND malogro <> 'ERROR';

COMMENT ON TABLE payments.wf_payment IS
    'Pagamentos analíticos WF1+WF2 (869.663 rows iniciais). Fonte primária após DE-PARA. v1.1.1: 30 cols tipadas + raw_extra para 50+ opcionais.';
