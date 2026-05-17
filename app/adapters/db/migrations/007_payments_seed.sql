-- ============================================================
-- Migration 007 — Seed RuleDefinitions + AnalyticDetectors (Fase 1)
--
-- 16 RuleDefinitions (rules_engine): R1, R2, R3, R4, R5.UF/CIDADE/
--   TECNOLOGIA/ATIVIDADE/CATEGORIA/OBJETO, R6.1–6.9, REGRA_LPU
-- 11 AnalyticDetectors (analytics_engine): R7_*
--
-- Idempotente via ON CONFLICT (code) DO NOTHING.
-- ============================================================

INSERT INTO payments.rule_definition (id, code, name, description, severity, engine_type, python_handler, threshold_params)
VALUES
    -- ============================================================
    -- R1, R2 — base ↔ PDF (CNPJ + Validade/ValFix)
    -- ============================================================
    (gen_random_uuid(), 'REGRA_1', 'CNPJ match base ↔ PDF',
     'CNPJ da base Contratos-Empreteiras deve bater com o do PDF extraído',
     'high', 'sql_deterministic',
     'app.core.services.payments.rules.regra_1_cnpj',
     '{}'::jsonb),
    (gen_random_uuid(), 'REGRA_2', 'Validade + ValFix',
     'Início/fim de validade e ValFix(cab) entre Contratos-Empreteiras e PDF',
     'high', 'sql_deterministic',
     'app.core.services.payments.rules.regra_2_validade',
     '{"date_tolerance_days": 0}'::jsonb),
    -- ============================================================
    -- R3 — Texto Breve + Preço LPU (especifica DOCX original)
    -- ============================================================
    (gen_random_uuid(), 'REGRA_3', 'Texto Breve + Preço LPU base ↔ PDF',
     'wf_payment.item_descricao ≈ contract_version.objeto E preço bruto LPU dentro de tolerância',
     'medium', 'sql_deterministic',
     'app.core.services.payments.rules.regra_3_texto_preco',
     '{"tolerance_pct": 1.0}'::jsonb),
    -- ============================================================
    -- R4 — Cobertura de extração (derivada da diretriz "memorizar")
    -- ============================================================
    (gen_random_uuid(), 'REGRA_4', 'Cobertura de extração',
     'Alerta se contract_version tem >2 campos NULL entre objeto/tec/ativ/uf/cidade/val_fix_cab',
     'medium', 'sql_deterministic',
     'app.core.services.payments.rules.regra_4_cobertura',
     '{}'::jsonb),
    -- ============================================================
    -- R5 — Escopo (família 5.a–5.f) [v1.1.1: 100% determinístico/fuzzy]
    -- OBJETO_DO_CONTRATO tem 598 valores únicos no WF — é taxonomia,
    -- não texto livre. Cascata fuzzy→embedding→LLM eliminada.
    -- ============================================================
    (gen_random_uuid(), 'REGRA_5_UF', 'UF — match exato',
     'wf_payment.uf deve = ANY(contract_version.uf[]) (após uppercase)',
     'medium', 'sql_deterministic',
     'app.core.services.payments.rules.regra_5a_uf',
     '{}'::jsonb),
    (gen_random_uuid(), 'REGRA_5_CIDADE', 'Cidade — match normalizado',
     'normalize(wf.cidade) deve estar em normalize(cv.cidade[]) (lower, sem acento/hífen)',
     'medium', 'sql_deterministic',
     'app.core.services.payments.rules.regra_5b_cidade',
     '{}'::jsonb),
    (gen_random_uuid(), 'REGRA_5_TECNOLOGIA', 'Tecnologia — fuzzy',
     'partial_ratio(wf.tecnologia, cv.tecnologia) >= fuzzy_threshold',
     'medium', 'fuzzy',
     'app.core.services.payments.rules.regra_5c_tecnologia',
     '{"fuzzy_threshold": 0.90}'::jsonb),
    (gen_random_uuid(), 'REGRA_5_ATIVIDADE', 'Atividade — fuzzy',
     'partial_ratio(wf.atividade, cv.atividade) >= fuzzy_threshold',
     'medium', 'fuzzy',
     'app.core.services.payments.rules.regra_5d_atividade',
     '{"fuzzy_threshold": 0.90}'::jsonb),
    (gen_random_uuid(), 'REGRA_5_CATEGORIA', 'Categoria — fuzzy',
     'partial_ratio(wf.categoria, supplier_bridge.categoria) >= fuzzy_threshold',
     'medium', 'fuzzy',
     'app.core.services.payments.rules.regra_5e_categoria',
     '{"fuzzy_threshold": 0.90}'::jsonb),
    (gen_random_uuid(), 'REGRA_5_OBJETO', 'Objeto — fuzzy contra taxonomia',
     'partial_ratio(wf.objeto_do_contrato, cv.objeto_contrato) >= fuzzy_threshold (598 valores, taxonomia)',
     'medium', 'fuzzy',
     'app.core.services.payments.rules.regra_5f_objeto',
     '{"fuzzy_threshold": 0.85}'::jsonb),
    -- ============================================================
    -- R6 — Família 6.1-6.9 (WF×EKPO + WF×GC) [v1.1: 9 sub-regras]
    -- DOCX original: 5 sub-regras WF×EKPO (pedido) + 4 sub-regras WF×GC (contrato)
    -- ============================================================
    (gen_random_uuid(), 'REGRA_6_1', 'WF PEDIDO_NUM × EKPO Documento de compras',
     'wf_payment.pedido_num deve existir em purchase_order_header.documento_compras',
     'high', 'sql_deterministic',
     'app.core.services.payments.rules.regra_6_1_pedido',
     '{}'::jsonb),
    (gen_random_uuid(), 'REGRA_6_2', 'WF DATA_PEDIDO × EKPO data_documento',
     'wf.data_pedido próxima de EKKO.data_documento (tolerância em dias)',
     'medium', 'sql_deterministic',
     'app.core.services.payments.rules.regra_6_2_data',
     '{"date_tolerance_days": 7}'::jsonb),
    (gen_random_uuid(), 'REGRA_6_3', 'WF CONTRATO_NUM × EKPO contrato_basico',
     'wf.contrato_num deve = EKKO.contrato_basico do pedido',
     'high', 'sql_deterministic',
     'app.core.services.payments.rules.regra_6_3_contrato',
     '{}'::jsonb),
    (gen_random_uuid(), 'REGRA_6_4', 'WF ITEM_NUM × EKPO Item',
     'wf.item_num deve = purchase_order_item.item correspondente',
     'medium', 'sql_deterministic',
     'app.core.services.payments.rules.regra_6_4_item',
     '{}'::jsonb),
    (gen_random_uuid(), 'REGRA_6_5', 'WF VALOR_TOTAL_FINAL × EKPO valor_liquido',
     'wf.valor_total_final ≈ purchase_order_item.valor_liquido (tolerância)',
     'high', 'math_tolerance',
     'app.core.services.payments.rules.regra_6_5_valor',
     '{"tolerance_pct": 0.5}'::jsonb),
    (gen_random_uuid(), 'REGRA_6_6', 'WF CONTRATO_NUM × GC documento_compras',
     'wf.contrato_num deve existir em purchase_order_gc.documento_compras',
     'high', 'sql_deterministic',
     'app.core.services.payments.rules.regra_6_6_gc_contrato',
     '{}'::jsonb),
    (gen_random_uuid(), 'REGRA_6_7', 'WF ITEM_NUM × GC Item',
     'wf.item_num deve = purchase_order_gc.item do guarda-chuva',
     'medium', 'sql_deterministic',
     'app.core.services.payments.rules.regra_6_7_gc_item',
     '{}'::jsonb),
    (gen_random_uuid(), 'REGRA_6_8', 'WF ITEM_DESCRICAO × GC Texto breve',
     'wf.item_descricao ≈ purchase_order_gc.texto_breve (fuzzy)',
     'medium', 'fuzzy',
     'app.core.services.payments.rules.regra_6_8_gc_descricao',
     '{"fuzzy_threshold": 0.85}'::jsonb),
    (gen_random_uuid(), 'REGRA_6_9', 'WF VALOR_UNITARIO × GC preço bruto LPU',
     'wf.valor_unitario ≈ purchase_order_gc.preco_bruto_lpu (tolerância)',
     'high', 'math_tolerance',
     'app.core.services.payments.rules.regra_6_9_gc_preco',
     '{"tolerance_pct": 1.0}'::jsonb),
    -- ============================================================
    -- REGRA LPU — Preço aplicado em ESLL ↔ LPU do contract_version vigente
    -- ============================================================
    (gen_random_uuid(), 'REGRA_LPU', 'Preço aplicado ↔ LPU',
     'service_package.preco_bruto deve bater com lpu_item.preco_unitario do contract_version vigente',
     'high', 'math_tolerance',
     'app.core.services.payments.rules.regra_lpu_preco',
     '{"tolerance_pct": 1.0}'::jsonb)
ON CONFLICT (code) DO NOTHING;

-- ============================================================
-- AnalyticDetectors — 11 detectores R7
-- ============================================================
INSERT INTO payments.analytic_detector (id, code, name, description, technique, severity, python_handler, threshold_params)
VALUES
    (gen_random_uuid(), 'R7_LPU_OUTLIER', 'LPU outlier por serviço',
     'Diferenças relevantes de custo/volume para a mesma LPU',
     'iqr', 'medium',
     'app.core.services.payments.analytics.r7_lpu_outlier',
     '{"iqr_factor": 1.5, "min_samples": 30}'::jsonb),
    (gen_random_uuid(), 'R7_QTD_QUEBRADA', 'Números quebrados na qtd. de serviço',
     'Quantidades fracionárias atípicas dentro da OS',
     'heuristic', 'low',
     'app.core.services.payments.analytics.r7_qtd_quebrada',
     '{"decimal_places_max": 2}'::jsonb),
    (gen_random_uuid(), 'R7_FIXO_VARIAVEL_ATIPICO', 'Variações atípicas em valores fixos/variáveis',
     'Desvio entre proporção fixo/variável observada e contratada',
     'zscore', 'medium',
     'app.core.services.payments.analytics.r7_fixo_variavel',
     '{"zscore_threshold": 2.0}'::jsonb),
    (gen_random_uuid(), 'R7_PICO_FIM_PERIODO', 'Picos de consumo no fim de período',
     'Concentração de pagamentos no último mês de validade do contrato',
     'timeseries_outlier', 'medium',
     'app.core.services.payments.analytics.r7_pico_fim_periodo',
     '{"last_n_days": 30, "spike_threshold": 2.0}'::jsonb),
    (gen_random_uuid(), 'R7_EMPREITEIRA_OUT_PADRAO', 'Empreiteira fora do padrão histórico',
     'Comparação por empreiteira × pares do mesmo segmento (clustering)',
     'clustering', 'medium',
     'app.core.services.payments.analytics.r7_empreiteira_padrao',
     '{"min_pairs": 5, "isolation_threshold": 0.7}'::jsonb),
    (gen_random_uuid(), 'R7_LAG_EXECUCAO_PAGTO', 'Intervalo anormal execução × pagamento',
     'Distribuição de lag por empreiteira; outliers individuais',
     'zscore', 'low',
     'app.core.services.payments.analytics.r7_lag_pagto',
     '{"zscore_threshold": 2.5}'::jsonb),
    (gen_random_uuid(), 'R7_PERIODOS_ATIPICOS', 'Concentração de pagamentos em períodos atípicos',
     'Spikes temporais sem correlação com execução',
     'timeseries_outlier', 'low',
     'app.core.services.payments.analytics.r7_periodos_atipicos',
     '{"window_days": 7, "spike_threshold": 3.0}'::jsonb),
    (gen_random_uuid(), 'R7_RECORR_VARIAVEL', 'Recorrência excessiva de serviços variáveis',
     'Razão variável/fixo > threshold contratual',
     'ratio', 'medium',
     'app.core.services.payments.analytics.r7_recorr_variavel',
     '{"ratio_threshold": 1.5}'::jsonb),
    (gen_random_uuid(), 'R7_CONSUMO_PERFIL', 'Consumo incompatível com perfil jurídico',
     'Proporção fixo/variável agregada diverge do contrato vigente',
     'ratio', 'medium',
     'app.core.services.payments.analytics.r7_consumo_perfil',
     '{"ratio_delta_threshold": 0.30}'::jsonb),
    (gen_random_uuid(), 'R7_LPU_PADRAO_SERVICO', 'LPU fora do padrão para a atividade',
     'Uso recorrente de LPU divergente da norma do tipo de serviço',
     'zscore', 'medium',
     'app.core.services.payments.analytics.r7_lpu_padrao',
     '{"zscore_threshold": 2.0, "group_by": "atividade"}'::jsonb),
    (gen_random_uuid(), 'R7_VALIDADE_VENCIDA', 'Uso de contrato após validade ou acima do orçado',
     'wf.data_pedido > contract_version.valid_to OU soma > val_fix_cab × meses',
     'sql_temporal', 'high',
     'app.core.services.payments.analytics.r7_validade_vencida',
     '{}'::jsonb)
ON CONFLICT (code) DO NOTHING;
