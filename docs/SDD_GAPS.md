# SDD_GAPS — Empreiteiras-WF

**Gerado:** 2026-05-17
**Base de comparação:**
- `docs/SDD.md` v1.0 (commit `11f48ab`)
- `docs/DATA_INVENTORY.md` (commit `ed697cb`) — schemas reais dos 68 arquivos
- `C:\_PERSONAL\beholder_data\Regras - POC - Automation AnyWhere - IA na Monitoria de Pagamentos.docx` — regras originais da Controladoria Operacional (Janeiro 2026)

Este documento é o **patch proposto** para SDD v1.1. Após sua aprovação, atualizo `docs/SDD.md` e prossigo com Pré-B (parser MSRV5 + sondagem profunda) e Pré-C (spike PDF).

---

## 0. Sumário executivo — 7 gaps em ordem de severidade

| # | Gap | Seções SDD afetadas | Severidade | Bloqueia |
|---|---|---|:---:|---|
| G1 | Analítico WF (869.663 × 81) é entidade central ausente | §3, §7, §9 (R6, R7) | 🔴 alta | Fase 1, 2 |
| G2 | REGRA 6 do DOCX tem **9 sub-regras** (6.1–6.9); SDD modelou só 1 | §9 | 🔴 alta | Fase 2 |
| G3 | REGRA 7 (análises de desvios/anomalias) **totalmente ausente** | §9, §12 (phase plan) | 🔴 alta | Fase 2.5 nova |
| G4 | MSRV5 LPU = 3.103.381 linhas (não 44.782) | §3 (`lpu_item`), §14.2 | 🟠 média | Fase 1 |
| G5 | Sheet "Contratos Guarda Chuvas" (44.782 × 13) não mapeada | §3, §14.2 | 🟠 média | Fase 1, 2 (R6 sub-regras) |
| G6 | REGRA 4 do DOCX é "extração", não "cobertura" | §9 | 🟡 baixa | Fase 2 |
| G7 | REGRA 5 do SDD over-engineered vs DOCX + Analítico WF | §9 | 🟡 baixa | Fase 5 (vira muito mais simples) |

---

## 1. Gaps de schema — §3 Domain Model + §7 DDL

### 1.1 [G1] Falta entidade `wf_payment` — Analítico WF (869k linhas × 81 cols)

**Sheet:** `Analitico_Empreiteiras_WF1_WF2_` em `Analitico_Empreiteiras_WF1_WF2_TOTAL_2025 2.txt.xlsx`
**Volume:** 869.663 linhas declaradas, 81 colunas
**Granularidade:** 1 linha = 1 OS paga (a confirmar na Pré-B com sample)
**Primeiras 15 colunas confirmadas:**

```
SISTEMA, CATEGORIA, OS, SOLID, CIDADE, UF, REGIONAL_SOE_NOVA, PROJETO,
PROJETO_GERENCIAL, TECNOLOGIA, EMPREITEIRA, ACAO, ATIVIDADE, FASE_ATUAL, STATUS_OS
```

**Por que é central:** o DOCX original referencia explicitamente `Base "Analítico Empreteiras_WF1_WF2"` como uma das 3 fontes da REGRA 6 (`WF`), e a REGRA 7 inteira opera sobre essa base.

**Proposta:** nova tabela `payments.wf_payment` com `raw_extra::jsonb` absorvendo as 66 colunas adicionais até sondagem completa.

```sql
CREATE TABLE payments.wf_payment (
    id BIGSERIAL PRIMARY KEY,
    -- chaves de negócio (a confirmar com sample real)
    os_num TEXT NOT NULL,                    -- "OS"
    sistema TEXT,                            -- "SISTEMA" (WF1/WF2)
    pedido_num TEXT,                         -- "PEDIDO_NUM" (R6.1)
    contrato_num TEXT,                       -- "CONTRATO_NUM" (R6.3, R6.6)
    item_num TEXT,                           -- "ITEM_NUM" (R6.4, R6.7)
    item_descricao TEXT,                     -- "ITEM_DESCRICAO" (R6.8)
    data_pedido DATE,                        -- "DATA_PEDIDO" (R6.2)
    valor_total_final NUMERIC(18,2),         -- "VALOR_TOTAL_FINAL" (R6.5)
    valor_unitario NUMERIC(18,4),            -- "VALOR_UNITARIO" (R6.9)
    -- escopo (estruturado, simplifica R5)
    uf TEXT,
    cidade TEXT,
    tecnologia TEXT,
    atividade TEXT,
    categoria TEXT,
    -- contexto
    empreiteira TEXT,
    fase_atual TEXT,
    status_os TEXT,
    raw_extra JSONB NOT NULL DEFAULT '{}'::jsonb,
    ingestion_run_id UUID REFERENCES payments.ingestion_run(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_wf_payment_os ON payments.wf_payment(os_num);
CREATE INDEX idx_wf_payment_contrato ON payments.wf_payment(contrato_num);
CREATE INDEX idx_wf_payment_pedido ON payments.wf_payment(pedido_num);
CREATE INDEX idx_wf_payment_data ON payments.wf_payment(data_pedido);
CREATE INDEX idx_wf_payment_empreiteira ON payments.wf_payment(empreiteira);
```

**Impacto modelo:**
- 12 entidades → **13 entidades**
- `purchase_order_item` (EKPO) **não vira** fonte primária dos pagamentos; é o lado SAP do batimento com WF
- relação `wf_payment.contrato_num` ↔ `purchase_order_item.documento_compras` é central pra REGRA 6

---

### 1.2 [G5] Falta entidade `purchase_order_gc` — sheet "Contratos Guarda Chuvas"

**Sheet:** `Contratos Guarda Chuvas` em `Contratos - Empreteiras.xlsx` (44.782 × 13)
**Headers confirmados:**

```
Documento de compras, Item, Últ.modif.no dia, Texto breve, Empresa,
Nº pacote (EKPO), Pacote (ESLL), Início per.validade, Fim da validade,
ValFix.(nível cab.), Preço bruto (LPU), Nº de serviço, Texto breve
```

**Por que é central:** O DOCX cita `Base "Contratos – Empreteiras" = GC` como uma das 3 fontes da REGRA 6 (sub-regras 6.6 a 6.9). Isso **não é** o supplier_bridge (que é a sheet `Empreiteiras` com 147 linhas) — é uma sheet **separada** no mesmo arquivo, que já é o **cruzamento pré-processado** de EKPO + ESLL + LPU para os contratos guarda-chuva monitorados.

**Decisão necessária:** 2 caminhos.

| Opção | Descrição | Trade-off |
|---|---|---|
| (a) Tabela `payments.purchase_order_gc` espelhando a sheet | armazena pronto, sem recálculo | redundância — dados derivam de EKPO+ESLL+LPU |
| (b) Materialized view `mv_purchase_order_gc` calculada de EKPO+ESLL+LPU | fonte única, sem redundância | precisa refresh; depende de carga MSRV5 completa primeiro |

**Recomendação:** **(a)** na Fase 1 como ingest direto da sheet (rápido, permite REGRA 6 funcionar); **(b)** como evolução na Fase 3 quando matview KPI já estiver pronta. A sheet vira fonte de validação cruzada da matview.

---

### 1.3 [G4] `lpu_item` precisa rever fonte e escala

**SDD atual** (§14.2): `ESLL - EXTRAÇÃO Nº DE PACOTES - LPU_VALORES.xlsx` (44.782 × 10) → `lpu_item`
**Realidade:**
- O XLSX é o **subset filtrado** por guarda-chuvas monitorados
- `MSRV5 - EXTRAÇÃO LPU.txt` (352 MB, **3.103.381 linhas**, 2.909.412 registros + paginação SAP) é a LPU completa do SAP

**Proposta:**
- Fonte autoritativa = MSRV5 TXT (parser dedicado)
- XLSX = sanity check (todos os 44.782 do XLSX devem existir no MSRV5)
- `lpu_item` precisa partitioning desde criação:

```sql
CREATE TABLE payments.lpu_item (
    id BIGSERIAL,
    contract_version_id UUID,                  -- pode ser NULL inicialmente (carga full SAP)
    numero_servico TEXT NOT NULL,
    documento_compras TEXT NOT NULL,
    item INTEGER,
    data_documento DATE,
    preco_unitario NUMERIC(18,4) NOT NULL,
    qtd_solicitada NUMERIC(18,3),
    texto_breve TEXT,
    raw_extra JSONB DEFAULT '{}'::jsonb,
    PRIMARY KEY (id, data_documento)
) PARTITION BY RANGE (data_documento);

-- Particionar por ano (volume estimado: ~500k/ano)
CREATE TABLE payments.lpu_item_2022 PARTITION OF payments.lpu_item
  FOR VALUES FROM ('2022-01-01') TO ('2023-01-01');
-- ... 2023, 2024, 2025, 2026, default
```

---

## 2. Gaps de regras — §9 Rules Catalog ↔ DOCX original

### 2.1 Matriz comparativa completa

| # | DOCX Janeiro 2026 (Controladoria) | SDD v1.0 §9 | Status |
|---|---|---|---|
| R1 | Bater "Contratos-Empreteiras" campo CNPJ com PDF | REGRA 1 — CNPJ base ↔ PDF | ✅ match |
| R2 | Bater "Contratos-Empreteiras" campos `ValFix(cab)`, `Início per.validade`, `Fim da validade` com PDF | REGRA 2 — Validade + ValFix | ✅ match |
| R3 | Bater "Contratos-Empreteiras" campos `Texto Breve` e `Preço bruto (LPU)` com PDF | REGRA 3 — Outros campos base ↔ PDF (genérica) | ⚠️ SDD generalizou demais — **especificar** |
| R4 | **Memorizar** escopo/região/valores fixos+variáveis para usar como base de avaliação | REGRA 4 — Variáveis extraídas (cobertura NULL) | ❌ DOCX é **diretriz de extração**, não check; SDD virou alerta de cobertura |
| R5 | Comparar com PDF do ARIBA: OBJETO_DO_CONTRATO, CATEGORIA, TECNOLOGIA, ATIVIDADE, UF, CIDADE | REGRA 5 — Escopo cascata fuzzy→embedding→LLM | ⚠️ SDD over-engineered (DOCX só lista campos) |
| R6 | **9 sub-regras** (6.1 a 6.9) — batimento WF×EKPO em 5 campos + WF×GC em 4 campos | REGRA 6 — WF CONTRATO_NUM × EKPO contrato_basico (1 sub-regra) | ❌ **faltam 8 sub-regras** |
| R7 | Histórico Analítico WF 2025 + PDF — detecção de desvios/anomalias (11 tipos) | (ausente) | ❌ **regra inteira ausente** |
| LPU | (embutida na R3 do DOCX) | REGRA LPU — Preço aplicado ↔ LPU | ✅ match parcial |
| Evidências | Próximo passo — fotos, geo, imagens repetidas | (ausente) | 🟡 ambos diferem — Fase 9 futura |

### 2.2 [G2] REGRA 6 — 9 sub-regras

DOCX especifica 3 fontes (`WF` = Analítico, `EKPO` = SAP pedidos, `GC` = sheet Contratos Guarda Chuvas) e 9 batimentos:

**Validar itens do pedido (WF × EKPO):**

| Sub | WF (Analítico) | × | EKPO | Severidade |
|---|---|---|---|:---:|
| 6.1 | `PEDIDO_NUM` | × | `Documento de compras` | high |
| 6.2 | `DATA_PEDIDO` | × | `Últ.modif.no dia` | medium |
| 6.3 | `CONTRATO_NUM` | × | `Contrato básico` | high (= R6 do SDD atual) |
| 6.4 | `ITEM_NUM` | × | `Item` | medium |
| 6.5 | `VALOR_TOTAL_FINAL` | × | `Valor líquido pedido` | high |

**Validar informações de contrato (WF × GC = Contratos Guarda Chuvas):**

| Sub | WF | × | GC | Severidade |
|---|---|---|---|:---:|
| 6.6 | `CONTRATO_NUM` | × | `Documento de compras` | high |
| 6.7 | `ITEM_NUM` | × | `Item` | medium |
| 6.8 | `ITEM_DESCRICAO` | × | `Texto breve` | medium |
| 6.9 | `VALOR_UNITARIO` | × | `Preço bruto (LPU)` | high (tolerância) |

**Proposta:** transformar REGRA 6 em **família** de 9 regras independentes (`REGRA_6_1` a `REGRA_6_9`), cada uma um `RuleDefinition` no catálogo. Todas determinísticas (engine `sql_deterministic` exceto 6.9 que é `math_tolerance`).

### 2.3 [G3] REGRA 7 — análises de desvios/anomalias

DOCX cita 11 tipos de desvio (em ordem do texto original):

1. Diferenças relevantes de custo e volume para mesma LPU
2. Números quebrados em qtd. de serviço dentro da OS
3. Variações atípicas em valores fixos e variáveis
4. Picos de consumo no fim de períodos contratuais (fechamento orçamento)
5. Empreiteiras com comportamento fora do padrão histórico
6. Intervalos anormais entre execução do serviço e pagamento
7. Concentração de pagamentos em períodos atípicos
8. Recorrência excessiva de serviços variáveis incompatível com escopo
9. Consumo incompatível com perfil jurídico (proporção fixo/variável)
10. Uso recorrente de LPU fora do padrão para o serviço executado
11. Uso do contrato após validade + uso orçamentário acima do contrato

**Natureza:** essas não são checks determinísticos — são **análises estatísticas / detecção de anomalias** sobre histórico (Analítico WF 2025).

**Proposta:** novo módulo `app/core/payments/services/analytics_engine.py` separado do `reconciliation_engine.py`. Cada análise vira um `AnalyticDetector` (interface) que produz `AnalyticFinding` (com `score`, `expected_range`, `actual_value`, `evidence_records[]`).

**Fase proposta:** Fase 2.5 nova (entre 2 e 3), 1-2 semanas, dependente apenas de Fase 1 (Analítico WF carregado).

Técnicas por desvio (proposta):

| Desvio | Técnica |
|---|---|
| 1, 10 | distribuição da LPU por serviço — Z-score / IQR |
| 2 | regex/heurística — números fracionários atípicos |
| 3, 9 | proporção fixo/variável vs perfil contratual — desvio absoluto |
| 4, 7 | série temporal — outliers no time series |
| 5 | comparação empreiteira × pares do mesmo segmento — clustering |
| 6 | distribuição de `lag(execução, pagamento)` por empreiteira |
| 8 | razão variável/fixo > threshold contratual |
| 11 | join temporal — pagamento fora da `contract_version` vigente |

### 2.4 [G6] REGRA 4 — reorientar

DOCX: "Memorizar escopo, região, valores fixos e variáveis para cada contrato para usar como base de avaliação."

Isso é uma **diretriz de extração PDF + persistência** (Fase 4), não uma regra de check. A "REGRA 4" do SDD atual (alerta se >2 campos NULL) é útil mas é uma regra DERIVADA da R4 do DOCX.

**Proposta:**
- Renomear SDD `REGRA 4 — Cobertura` → `REGRA 4 — Cobertura extração` (mantém)
- Adicionar nota em §10 (Skills & Prompts) que a R4 do DOCX é executada pelo `SKILL_pdf_extraction.md` (extrai e persiste em `contract_version` — não gera finding por si só)

### 2.5 [G7] REGRA 5 — simplificar drasticamente

DOCX: "Campos para comparar escopo com PDF do ARIBA: OBJETO_DO_CONTRATO (validar escopo), CATEGORIA, TECNOLOGIA (validar escopo), ATIVIDADE (validar escopo), UF (validar região), CIDADE (validar região)."

**Insight crítico:** o Analítico WF tem `UF`, `CIDADE`, `TECNOLOGIA`, `ATIVIDADE`, `CATEGORIA` **estruturados**. Não há texto livre nessas colunas. Logo:
- 5 dos 6 campos: **match exato** ou **fuzzy 95%+** (typos)
- Só `OBJETO_DO_CONTRATO` é texto longo livre do PDF — precisa NLP

**Proposta:**

```
REGRA 5 simplificada:
  - 5.a UF — match exato (normalize uppercase)
  - 5.b CIDADE — match exato após normalização (lowercase, sem acento, sem hífen)
  - 5.c TECNOLOGIA — match exato + fuzzy ≥90 para typos
  - 5.d ATIVIDADE — match exato + fuzzy ≥90
  - 5.e CATEGORIA — match exato + fuzzy ≥90
  - 5.f OBJETO_DO_CONTRATO — pgvector embedding similarity + LLM-judge para borderline
```

A cascata fuzzy→embedding→LLM fica só na 5.f. As outras 5 são SQL determinístico — economia massiva de custo LLM.

---

## 3. Gaps de volume — §14.2

| Arquivo | SDD v1.0 (§14.2) | Realidade | Δ |
|---|---:|---:|---:|
| Contratos - Empreteiras.xlsx | 147 × 6 | sheet Empreiteiras: 147 × 6 ✅ + sheet Contratos GC: **44.782 × 13** | sheet faltando |
| EKKO Guarda-Chuvas | 138 × 179 | 138 × 179 | ✅ |
| EKKO Pedidos | 1.894 × 179 | 1.894 × 179 | ✅ |
| EKPO Guarda-Chuvas | 44.782 × 283 | 44.782 × 283 | ✅ |
| EKPO Pedidos | 25.067 × 283 | 25.067 × 283 | ✅ |
| ESLL LPU_VALORES | 44.782 × 10 | 44.782 × 10 | ✅ |
| ESLL EKPO_ESLL | 44.782 × 3 | 44.782 × 3 | ✅ |
| (ausente) | — | **Analítico WF: 869.663 × 81 (sheet1) + 1.049 × 2 + 339 × 12** | ⚠️ não previsto |
| (ausente) | — | **MSRV5 TXT: 3.103.381 linhas** | ⚠️ não previsto |

**Estimativa de tamanho final do PG `payments` schema:**

| Tabela | Linhas | Storage (rough) |
|---|---:|---:|
| `wf_payment` | 869k atual + ~80k/mês → ~1,8M em 12m | ~700 MB |
| `lpu_item` | 3,1M atual + crescimento | ~800 MB + idx |
| `purchase_order_item` | 70k (EKPO consolidado) | ~80 MB |
| `purchase_order_gc` | 44k | ~10 MB |
| `service_package` | 44k | ~10 MB |
| outras | <10k cada | <50 MB total |
| **Total** | ~5M linhas | **~1,7 GB com índices** |

Cabe em PG single-node sem dor. Particionamento de `lpu_item` por ano + `wf_payment` por mês é prudente desde o início.

---

## 4. Mapeamento source-to-target consolidado (proposto v1.1)

| Arquivo origem | Sheet/Seção | Linhas | Tabela destino | Notes |
|---|---|---:|---|---|
| `Contratos - Empreteiras.xlsx` | Empreiteiras | 147 | `supplier_bridge` | ✅ SDD original |
| `Contratos - Empreteiras.xlsx` | Contratos Guarda Chuvas | 44.782 | `purchase_order_gc` **(nova)** | G5 |
| `EKKO - EXTRAÇÃO CONTRATOS GUARDA CHUVAS.xlsx` | Sheet1 | 138 | `purchase_order_header` (filter categoria_doc='K') | ✅ |
| `EKKO - SAP (Extração pedidos).MHTML.xlsx` | Sheet1 | 1.894 | `purchase_order_header` (filter pedidos) | ✅ |
| `EKPO - EXTRAÇÃO CONTRATOS GUARDA CHUVAS.xlsx` | Sheet1 | 44.782 | `purchase_order_item` | ✅ |
| `EKPO - SAP (Extração pedidos).MHTML.xlsx` | Sheet1 | 25.067 | `purchase_order_item` | ✅ |
| `ESLL - EXTRAÇÃO Nº DE PACOTES - LPU_VALORES.xlsx` | Sheet1 | 44.782 | `service_package` + sanity check `lpu_item` | ✅ |
| `ESLL - EXTRAÇÃO EKPO_ESLL Nº DE PACOTES.xlsx` | Sheet1 | 44.782 | enriquece join EKPO↔ESLL | ✅ |
| `MSRV5 - EXTRAÇÃO LPU.txt` | (texto SAP) | 3.103.381 | `lpu_item` **(fonte autoritativa)** | G4 |
| `Analitico_Empreiteiras_WF1_WF2_TOTAL_2025 2.txt.xlsx` | Analitico_Empreiteiras_WF1_WF2_ | 869.663 | `wf_payment` **(nova)** | G1 |
| `Analitico_…` | CC + CONTA | 1.049 | `cost_center_account` **(nova)** | menor |
| `Analitico_…` | Casos Selecionados | 339 | `eval_ground_truth` **(nova, em `tests/fixtures/`)** | usado para validar regras |
| `CONTRATOS/*/CW*.zip` → PDFs | — | 60 PDFs | `contract_version` (via extração PDF Fase 4) | ✅ |
| `Regras - POC … .docx` | — | — | (referência humana, não ingerido) | meta-doc |

---

## 5. Fases atualizadas (patch sobre §12)

| Fase | SDD v1.0 | Proposta v1.1 |
|---|---|---|
| 0 — Fundação isolamento | 1-2 sem | (sem mudança) |
| 1 — Modelo + Ingestão XLSX | 2 sem | **+0,5 sem** — adiciona `wf_payment` (parser do XLSX 306MB streaming) + `purchase_order_gc` + parser MSRV5 TXT |
| 2 — Rules engine MVP | 1-2 sem | (sem mudança nas R1–R3, R6.1–6.9, LPU; total 11 regras determinísticas) |
| **2.5 — Analytics engine (nova)** | — | **+1-2 sem** — REGRA 7 com 11 detectores |
| 3 — Dashboard MVP | 1 sem | (sem mudança) |
| 4 — Extração PDF + HITL | 2-3 sem | (sem mudança, mas com R4 do DOCX como diretriz) |
| 5 — REGRA 5 semântica | 1-2 sem | **−0,5 sem** — só 5.f (OBJETO) precisa cascata, outros 5 campos são SQL |
| 6 — UX completa | 2 sem | (sem mudança) |
| 7 — Validação concorrência | 1 sem | (sem mudança) |

**Total v1.0**: 11-15 semanas
**Total v1.1**: 12-16 semanas (+1 semana para regra de anomalias)

---

## 6. O que NÃO muda

| Decisão SDD §13.2 | Mantém? | Por quê |
|---|:---:|---|
| Determinístico para reconciliação, LLM só na extração | ✅ | reforçado — R5 fica ainda menos LLM-dependente |
| Híbrido textual + vectorDB | ✅ | só usado em R5.f (OBJETO) |
| Modular monolith inside Beholder | ✅ | analytics_engine é módulo, não microservice |
| Schema PG isolado, não DB separado | ✅ | volume ~1,7 GB cabe folgado |
| Worker dramatiq | ✅ | jobs de carga MSRV5 + parsing Analítico WF são fit perfeito |
| ClaroHub default cheap / Maritaca cloud | ✅ | extração PDF default Maritaca (decisão #2) |
| Versionamento temporal contratos | ✅ | R7.11 depende disso |

---

## 7. Decisões pendentes pra você (antes de eu aplicar SDD v1.1)

| # | Decisão | Recomendação | Alternativa |
|---|---|---|---|
| D1 | `purchase_order_gc` — tabela física na Fase 1 ou matview na Fase 3? | **tabela na Fase 1** (destrava R6.6–6.9 antes da matview) | matview-only (mais elegante, mas atrasa R6) |
| D2 | REGRA 7 — Fase 2.5 separada? | **sim** (analytics_engine separado de rules_engine) | enxertar em Fase 2 (mistura paradigmas) |
| D3 | REGRA 5 — manter cascata só na 5.f (OBJETO)? | **sim** (5 das 6 campos viram SQL puro) | manter cascata em todas as 6 (over-engineering) |
| D4 | "Casos Selecionados" 339 linhas — `eval_ground_truth` em DB ou só em `tests/fixtures/`? | **em `tests/fixtures/`** (acoplado ao código de teste, versionado) | DB (acoplado a runtime, permite Cockpit acessar) |
| D5 | Aplicar patch como `docs/SDD.md` v1.1 ou manter SDD.md v1.0 + diff em `SDD_GAPS.md` como living-doc? | **patch para v1.1** (fonte única) | living-doc (preserva intenção original como histórico) |

---

## 8. Próximos passos após sua decisão

Se você aprovar **D1–D5 conforme recomendado**:

1. Aplico patch no `docs/SDD.md` → v1.1 (commit dedicado)
2. Sigo Pré-B execução:
   - Parser MSRV5 (`scripts/parse_msrv5.py`)
   - Sample real do Analítico WF (81 colunas completas, granularidade, qualidade)
   - Probe das 339 linhas de "Casos Selecionados"
3. Sigo Pré-C (spike PDF com Maritaca em 5 contratos)
4. Fase 0 começa com SDD v1.1 estável

ETA Pré-B + Pré-C: 5-7 dias depois de aprovação.
