-- ============================================================
-- Migration 006 — Analytics R7 (Fase 1)
--
-- Catálogo + findings do analytics_engine (REGRA 7 — 11 detectores
-- de desvios/anomalias estatísticos). Paradigma separado do rules
-- determinístico/fuzzy (R1–R6.9, LPU).
--
-- D2 aprovada: tabelas físicas separadas (não reutiliza rule_definition
-- nem reconciliation_finding) porque granularidade e semântica diferem.
--
-- Idempotente.
-- ============================================================

-- ============================================================
-- AnalyticDetector — catálogo dos 11 detectores R7
-- ============================================================
CREATE TABLE IF NOT EXISTS payments.analytic_detector (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code                     TEXT NOT NULL UNIQUE,
    name                     TEXT NOT NULL,
    description              TEXT NOT NULL,
    technique                TEXT NOT NULL,                     -- 'zscore' | 'iqr' | 'timeseries_outlier' | 'clustering' | 'sql_temporal' | 'ratio' | 'heuristic'
    severity                 TEXT NOT NULL CHECK (severity IN ('low','medium','high')),
    is_active                BOOLEAN NOT NULL DEFAULT TRUE,
    threshold_params         JSONB NOT NULL DEFAULT '{}'::jsonb,
    python_handler           TEXT NOT NULL,
    version                  INTEGER NOT NULL DEFAULT 1,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- AnalyticFinding — output dos detectores estatísticos
-- ============================================================
CREATE TABLE IF NOT EXISTS payments.analytic_finding (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    detector_id              UUID NOT NULL REFERENCES payments.analytic_detector(id),
    detector_code            TEXT NOT NULL,                      -- denormalizado
    severity                 TEXT NOT NULL,                      -- herdada do detector
    -- payment alvo (pode ser NULL em findings agregados — ex.: empreiteira fora do padrão)
    wf_payment_id            BIGINT,
    wf_payment_data_pedido   DATE,                               -- denormalizado p/ join particionado
    supplier_id              UUID REFERENCES payments.supplier_bridge(id),
    -- corpo do finding estatístico
    score                    DOUBLE PRECISION NOT NULL,          -- z-score, distância, ratio, etc.
    expected_range           JSONB NOT NULL,                     -- {'min': 100, 'max': 500, 'method': 'iqr'}
    actual_value             JSONB NOT NULL,
    evidence_payment_ids     BIGINT[],                           -- demais payments que sustentam o finding (clustering)
    -- workflow (compatível com reconciliation_finding)
    status                   TEXT NOT NULL DEFAULT 'open'
                             CHECK (status IN ('open','in_analysis','accepted_fp','escalated','blocked')),
    analyst_id               UUID REFERENCES users(id),
    decision_reason          TEXT,
    decided_by_id            UUID REFERENCES users(id),
    decided_at               TIMESTAMPTZ,
    detected_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_af_inbox       ON payments.analytic_finding(status, severity, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_af_supplier    ON payments.analytic_finding(supplier_id);
CREATE INDEX IF NOT EXISTS idx_af_detector    ON payments.analytic_finding(detector_code, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_af_wf_payment  ON payments.analytic_finding(wf_payment_id);

COMMENT ON TABLE payments.analytic_detector IS
    '11 detectores R7 (REGRA 7 do DOCX). Análises estatísticas sobre histórico WF.';
COMMENT ON TABLE payments.analytic_finding IS
    'Output dos detectores estatísticos. Inbox separado: /payments/empreiteiras-wf/desvios.';
