# Beholder — SDD: Implementação Pendente

**Versão**: 1.0
**Data**: 2026-05-17
**Status**: Aprovado para execução
**Escopo**: Fase 0 (infra de escala) + Fases 1-7 (vertical Empreiteiras-WF)
**Precedente**: este documento sucede o `sdd.md` legado da raiz (herdado do fork Vértice). O legado fica como referência histórica e não é mais autoritário.

---

## Índice

0. [Sumário Executivo](#0-sumario-executivo)
1. [Goals & Non-Goals](#1-goals--non-goals)
2. [Arquitetura — Peças Novas](#2-arquitetura--pecas-novas)
3. [Domain Model — Empreiteiras-WF](#3-domain-model--empreiteiras-wf)
4. [Subsistemas](#4-subsistemas)
5. [Data Flow End-to-End](#5-data-flow-end-to-end)
6. [API Spec](#6-api-spec)
7. [Database Schema (DDL)](#7-database-schema-ddl)
8. [File System Layout](#8-file-system-layout)
9. [Rules Catalog](#9-rules-catalog)
10. [Skills & Prompts Catalog](#10-skills--prompts-catalog)
11. [Test Strategy](#11-test-strategy)
12. [Phase Plan (8 fases)](#12-phase-plan-8-fases)
13. [Riscos & Tradeoffs](#13-riscos--tradeoffs)
14. [Anexos](#14-anexos)

---

## 0. Sumário Executivo

Beholder é uma aplicação derivada da plataforma Vértice (forkada em 2026-05-16, SHA-source `e49bc3c`) para suportar monitoramento contínuo de pagamentos a empreiteiras da Claro em escala (~80.000 pagamentos/mês). Este SDD especifica **exclusivamente o que falta implementar** após o fork — a plataforma (auth, RBAC, audit, FinOps, guardrails, OPA, prompts, modules, skills, text2sql, multi-LLM adapters, arquitetura hexagonal) já está intacta e estável.

Output produzido: detecção **automática** de divergências entre **fato** (pagamento real registrado em SAP EKKO/EKPO/ESLL + Workflow WF) e **verdade contratada** (PDF jurídico + Lista de Preços Unitários + tabela-âncora Contratos-Empreteiras), com trilha auditável de cada decisão e dashboard executivo de KPIs (mockup `Monitoramento de Pagamentos para Empreiteiras - WF`).

Verticais futuros (Fornecedores-NDI, Pagamentos-Recorrentes) ficam **fora** deste SDD — serão tratados em documentos próprios reutilizando a infra de escala da Fase 0.

---

## 1. Goals & Non-Goals

### 1.1. Goals (com acceptance verificáveis)

| ID | Goal | Critério verificável |
|---|---|---|
| G1 | Ingerir 7 XLSX SAP (EKKO/EKPO/ESLL × guarda-chuva e pedidos + Contratos-Empreteiras) idempotentemente | Load completo (~120k rows) em <60s; queries de outros domínios mantêm SLO p95 durante load |
| G2 | Extrair de PDF jurídico: folha de rosto (15 campos), LPU (linhas tipadas), cláusulas (texto + embedding pgvector) | ≥85% campos corretos pós-HITL em conjunto de 5 PDFs reais; custo LLM ≤R$15/PDF |
| G3 | Executar regras determinísticas (REGRA 1, 2, 6, LPU) sobre 261 OS em <30s | Cobertura ≥90% no rules engine; cada regra com ≥5 fixtures (positivos + negativos) |
| G4 | Detectar divergências semânticas (REGRA 5 — OBJETO_DO_CONTRATO, TECNOLOGIA, ATIVIDADE, UF, CIDADE) | Precisão ≥80%, recall ≥70% em 50 amostras anotadas manualmente |
| G5 | Dashboard executivo com 9 KPIs do mockup, atualizados em <1s | Materialized view refresh <5s pós-ingestão; carga inicial do dashboard <1s |
| G6 | Inbox de Alertas com bulk actions e workflow HITL completo | Analista resolve 20 findings em <30min em teste de usabilidade com 3 perfis (analista/gestor/admin) |
| G7 | SLO p95 <500ms em endpoints críticos sob carga combinada | Load test k6 passa: ingestão de 100k rows + 50 usuários simultâneos + 10 uploads paralelos de PDF |
| G8 | Versionamento de contratos (aditivos) com query temporal | "Esse pagamento de março/2024 estava válido segundo a versão vigente naquele mês?" responde em ≤200ms |

### 1.2. Non-Goals (explicitamente fora)

- Outros verticais futuros — virão em SDDs próprios reaproveitando Fase 0
- Multi-tenancy / multi-cliente
- Mobile-first UI (dashboard desktop com layout responsivo basta)
- Real-time streaming de pagamentos (batch com latência ≤15min é OK)
- Substituição de SAP/WF (Beholder lê e valida; não escreve)
- Integração direta com API SAP / RFC (consumimos export XLSX nesta versão)
- ML treinado custom (usamos LLMs prontos para extração e validação semântica)
- Internacionalização (PT-BR único)

---

## 2. Arquitetura — Peças Novas

### 2.1. Vista de blocos

```
┌──────────────────────────────────────────────────────────────────┐
│ Browser (analista, gestor, admin, auditor)                       │
└────────┬─────────────────────────────────────────────────────────┘
         │ HTTPS
         ▼
┌──────────────────────────────────────────────────────────────────┐
│ FastAPI (uvicorn — processo principal)                           │
│ ─ Auth, RBAC, Audit (existe — intacto)                           │
│ ─ Endpoints /api/payments/* (NOVO)                               │
│ ─ Páginas /payments/empreiteiras-wf/* (NOVO)                     │
│ ─ Upload endpoints: enqueue job + 202 Accepted                   │
│ ─ Dashboard: lê matview (cached)                                 │
│ ─ NÃO faz extração, NÃO faz bulk insert (envia pro worker)       │
└──────┬────────────────────────────────────────────┬──────────────┘
       │ enqueue dramatiq                            │ read matview
       ▼                                             ▼
┌──────────────────────┐                 ┌────────────────────────┐
│ Redis (NOVO)         │                 │ PostgreSQL             │
│ ─ broker dramatiq    │                 │ ─ schema `public`      │
│ ─ cache de KPIs      │                 │   (intacto)            │
│   (TTL 60s)          │                 │ ─ schema `payments`    │
└──────┬───────────────┘                 │   (NOVO, isolado)      │
       │ pull job                        │ ─ pool dedicado        │
       ▼                                 │   workers (max=10)     │
┌──────────────────────────────┐         │ ─ matviews KPI (NOVO)  │
│ Worker (dramatiq) NOVO       │◄───────►│ ─ pgvector (NOVO)      │
│ ─ docling (PDF→markdown+tab) │         └─────────┬──────────────┘
│ ─ Instructor (LLM tipado)    │                   │
│ ─ Polars lazy (XLSX→rows)    │                   │ stores raw
│ ─ Schema Projector           │                   │
│ ─ Reconciliation engine      │                   │
│ ─ N replicas horizontais     │                   │
└──────┬───────────────────────┘                   │
       │                                            │
       ▼                                            │
┌──────────────────────┐                 ┌──────────┴─────────────┐
│ LLMs (existe)        │                 │ Storage (NOVO)         │
│ ─ Maritaca Sabiá-4   │                 │ ─ Port LLM-agnostic    │
│   (default, PT-BR)   │                 │ ─ FilesystemStorage    │
│ ─ ClaroHub on-prem   │                 │   (dev)                │
│   (cheap/fallback)   │                 │ ─ S3MinIOStorage       │
└──────────────────────┘                 │   (prod)               │
                                         │ ─ Stores PDFs/XLSX     │
                                         │   originais            │
                                         └────────────────────────┘
```

### 2.2. Componentes — origens

**EXISTEM** (intactos, forkados da Vértice):

| Camada | Componentes |
|---|---|
| Auth + RBAC | `auth_service`, `user_repo`, `feature_access_service`, `access_router` |
| Audit | `audit_service`, `audit_router`, middleware `AuditMiddleware`, tabela `audit_events` |
| FinOps | `finops_service`, `finops_repo`, tabelas `finops_ledger`/`finops_budgets`/`finops_alerts`/`finops_model_policies` |
| Guardrails | `input_sanitizer`, `output_validator` |
| Policy | `opa_adapter` (placeholder) |
| Prompts | `prompt_service`, `prompt_repo`, tabela `prompts` |
| Modules | `module_repo`, `module_wizard_service`, tabela `modules` |
| Skills | `skill_service`, `skill_wizard_service`, `skills_router`, padrão SKILL.md |
| Text2SQL | `text2sql_service` (já refatorado para usar ClaroHub) |
| LLM | `claro_hub_adapter`, `maritaca_adapter`, `mock_adapter`, `model_router`, `factory` |
| Observability | `composite_tracer`, integração LangFuse/MLflow/OTEL |
| Web infra | FastAPI, Jinja2, HTMX, Alpine.js, Tailwind |

**NOVOS** (este SDD):

| Camada | Componentes |
|---|---|
| Worker infra | Redis broker, dramatiq actors, container worker, retry/backoff policy |
| Storage | Port `DocumentStore` + adapters FilesystemStore (dev) e S3MinIOStore (prod) |
| Schema Projector | YAML declarativo + service Python que projeta SAP raw → 12-15 campos semânticos |
| Document Ingestion | docling pipeline para PDF → markdown + tabelas estruturadas |
| Extraction | Instructor + Pydantic schemas tipados; pgvector para embeddings de cláusulas |
| Reconciliation Engine | Registry de `RuleDefinition` + runner async + producer de `ReconciliationFinding` |
| Empreiteiras-WF UI | 6 páginas + componentes (KPI cards, alertas inbox, finding detail) |
| Schema PG `payments` | 10 tabelas novas + matviews + indexes |
| Empreiteiras-WF domain | entidades dataclass, repos, services, schemas API |

### 2.3. Princípios de isolamento (cumprem G7 — SLO sob carga)

1. **Schema PG isolado** — `CREATE SCHEMA payments`. Tabelas novas vão para `payments.*`. Sem refs cross-schema exceto `users.id` (FK leve).
2. **Pool de conexões dedicado** — config `pg_pool_payments_max=10`, separado do `pg_pool_max=20`. Soma <30, dentro de `max_connections` do PG.
3. **Worker em processo/container separado** — dramatiq não compete com event loop do FastAPI.
4. **Resource limits Docker** — app: 2GB RAM/1 vCPU, worker: 4GB RAM/2 vCPU (configurável via env).
5. **Matviews para KPI** — refresh disparado pós-ingestão; dashboard nunca executa agregação em tempo real.
6. **Polars lazy + chunks de 5k linhas** — XLSX nunca todo em memória; bulk insert via `COPY FROM STDIN`.
7. **Rate limit LLM por domínio** — reusa `finops_ledger` + `failsafe` para circuit breaker quando ClaroHub indisponível.

---

## 3. Domain Model — Empreiteiras-WF

### 3.1. Diagrama de entidades

```
SupplierBridge (147 rows — DE-PARA)
   │
   │ 1..N
   ▼
ContractMaster ───── 1..N ─── ContractVersion (aditivos)
   │                                │
   │                                │ 1..N
   │                                ▼
   │                          LPUItem (tabela de preços extraída)
   │
   │ 1..N
   ▼
PurchaseOrderHeader (EKKO ───── 1..N ─── PurchaseOrderItem (EKPO)
  pedidos)                                  │
                                            │ 1..N
                                            ▼
                                      ServicePackage (ESLL: serviço x preço x qtd)

ReconciliationRun (1 execução)
   │
   │ 1..N
   ▼
ReconciliationFinding ──── refs ──── PurchaseOrderItem
                                      ContractMaster (versão vigente na data)
                                      RuleDefinition

RuleDefinition (catálogo — 7 regras inicialmente)

ExtractionJob (worker async, status pending/extracting/review/done/failed)
   │
   │ refs
   ▼
ContractMaster (alvo da extração)
```

### 3.2. Entidades — campos completos

#### 3.2.1. `SupplierBridge` — tabela-âncora DE-PARA

Liga CONTRATO_NUM SAP ↔ REF WS Workflow ↔ CNPJ. Carregada da planilha `Contratos - Empreteiras.xlsx` (147 linhas, 6 colunas).

| Campo | Tipo Python | Tipo PG | Constraint | Origem |
|---|---|---|---|---|
| `id` | `UUID` | `UUID PK` | NOT NULL | gerado |
| `categoria` | `str` | `TEXT` | NOT NULL | `Contratos-Empreteiras.CATEGORIA` (ex.: "FIXO MENSAL", "RECUPERAÇÃO SITE") |
| `empreiteira` | `str` | `TEXT` | NOT NULL | `EMPREITEIRA` (ex.: "ABILITY") |
| `contrato_num_sap` | `str` | `TEXT` | NOT NULL, INDEX | `CONTRATO_NUM` (ex.: "5700017041") — match com `EKKO.Documento de compras` |
| `ref_ws` | `str` | `TEXT` | NOT NULL | `REF WS` (ex.: "CW149898") — match com PDF/Workflow |
| `numero_fornecedor_sap` | `str` | `TEXT` | NOT NULL | `NUMERO_FORNECEDOR SAP` (ex.: "140584") |
| `cnpj` | `str` | `TEXT` | NOT NULL, INDEX | `CNPJ` (ex.: "06127582000662") — match REGRA 1 |
| `created_at` | `datetime` | `TIMESTAMPTZ` | DEFAULT NOW() | sistema |

**Índices**: `(contrato_num_sap)`, `(cnpj)`, `(ref_ws)`.

#### 3.2.2. `ContractMaster` — contrato jurídico (cabeça)

Representa um contrato após extração e ativação. Cada contrato tem 1..N `ContractVersion` (aditivos), e o atual é determinado pela data de execução.

| Campo | Tipo Python | Tipo PG | Constraint | Notas |
|---|---|---|---|---|
| `id` | `UUID` | `UUID PK` | NOT NULL | |
| `supplier_bridge_id` | `UUID` | `UUID` | NOT NULL, FK → `payments.supplier_bridge(id)` | bridge para SAP/WS |
| `contrato_num_sap` | `str` | `TEXT` | NOT NULL | redundância indexada para join rápido |
| `ref_ws` | `str` | `TEXT` | NOT NULL | idem |
| `cnpj` | `str` | `TEXT` | NOT NULL | idem |
| `current_version_id` | `UUID` | `UUID` | NULL, FK → `payments.contract_version(id)` | versão vigente HOJE |
| `is_monitored` | `bool` | `BOOLEAN` | DEFAULT TRUE | gestor pode pausar monitoramento |
| `created_by_id` | `UUID` | `UUID` | NOT NULL, FK → `users(id)` | quem cadastrou |
| `created_at` | `datetime` | `TIMESTAMPTZ` | DEFAULT NOW() | |
| `updated_at` | `datetime` | `TIMESTAMPTZ` | DEFAULT NOW() | |

#### 3.2.3. `ContractVersion` — versão temporal do contrato

Cada extração de PDF (original ou aditivo) cria uma `ContractVersion`. Permite query "qual versão estava vigente em DATA X?".

| Campo | Tipo Python | Tipo PG | Constraint | Notas |
|---|---|---|---|---|
| `id` | `UUID` | `UUID PK` | NOT NULL | |
| `contract_master_id` | `UUID` | `UUID` | NOT NULL, FK | parent |
| `version_number` | `int` | `INTEGER` | NOT NULL | sequencial: 1, 2, 3 |
| `valid_from` | `date` | `DATE` | NOT NULL | "Início per.validade" do PDF |
| `valid_to` | `date` | `DATE` | NOT NULL | "Fim da validade" |
| `val_fix_cab` | `decimal` | `NUMERIC(15,2)` | NULL | "ValFix.(nível cab.)" — valor fixo na cabeça (REGRA 2) |
| `objeto_contrato` | `str` | `TEXT` | NULL | texto extraído da cláusula de objeto |
| `tecnologia` | `str` | `TEXT` | NULL | ex.: "FIBRA ÓPTICA", "HFC" |
| `atividade` | `str` | `TEXT` | NULL | ex.: "MANUTENÇÃO PREVENTIVA" |
| `uf` | `str[]` | `TEXT[]` | NULL | regiões cobertas: `["RJ", "ES"]` |
| `cidade` | `str[]` | `TEXT[]` | NULL | cidades cobertas (opcional) |
| `pdf_storage_key` | `str` | `TEXT` | NULL | chave no DocumentStore (FS/S3) |
| `extracted_by_llm_model` | `str` | `TEXT` | NULL | ex.: "openai/gpt-oss-20b" |
| `extracted_cost_brl` | `decimal` | `NUMERIC(10,4)` | DEFAULT 0 | rastreabilidade FinOps |
| `confidence_avg` | `float` | `DOUBLE PRECISION` | NULL | média de confidence dos campos |
| `reviewed_by_id` | `UUID` | `UUID` | NULL, FK → `users(id)` | analista que aprovou (HITL) |
| `reviewed_at` | `datetime` | `TIMESTAMPTZ` | NULL | |
| `created_at` | `datetime` | `TIMESTAMPTZ` | DEFAULT NOW() | |

**Índices**: `(contract_master_id, version_number)` UNIQUE, `(contract_master_id, valid_from, valid_to)` para query temporal.

#### 3.2.4. `LPUItem` — linha da Lista de Preços Unitários

Cada `ContractVersion` tem N `LPUItem`s extraídos do anexo. Match contra `ServicePackage` (ESLL) na REGRA LPU.

| Campo | Tipo Python | Tipo PG | Constraint | Notas |
|---|---|---|---|---|
| `id` | `UUID` | `UUID PK` | NOT NULL | |
| `contract_version_id` | `UUID` | `UUID` | NOT NULL, FK | parent |
| `numero_servico` | `str` | `TEXT` | NOT NULL, INDEX | ex.: "9007504" — match com ESLL |
| `descricao` | `str` | `TEXT` | NOT NULL | "SERV MANUT FIBRA OPTICA FIXO FOP" |
| `unidade_medida` | `str` | `TEXT` | NULL | "UNI", "KM", "HORA", etc. |
| `preco_unitario` | `decimal` | `NUMERIC(15,4)` | NOT NULL | preço contratado |
| `moeda` | `str` | `TEXT` | DEFAULT 'BRL' | |
| `pagina_pdf` | `int` | `INTEGER` | NULL | rastreabilidade — número da página |
| `clausula_ref` | `str` | `TEXT` | NULL | "Anexo IV, item 12.3" |
| `extracted_by_llm` | `bool` | `BOOLEAN` | DEFAULT TRUE | distingue de inserção manual |
| `confidence` | `float` | `DOUBLE PRECISION` | NULL | |

**Índices**: `(contract_version_id)`, `(numero_servico)`.

#### 3.2.5. `ContractClause` — cláusulas (texto + embedding)

Trechos do PDF indexados para rastreabilidade (cita cláusula original ao reportar divergência).

| Campo | Tipo Python | Tipo PG | Constraint | Notas |
|---|---|---|---|---|
| `id` | `UUID` | `UUID PK` | NOT NULL | |
| `contract_version_id` | `UUID` | `UUID` | NOT NULL, FK | |
| `clausula_numero` | `str` | `TEXT` | NULL | "3.2", "5.1.4" |
| `secao` | `str` | `TEXT` | NULL | "OBJETO", "PREÇO", "FORÇA MAIOR" |
| `texto` | `str` | `TEXT` | NOT NULL | conteúdo bruto |
| `embedding` | `Vector[1536]` | `vector(1536)` | NULL | pgvector (OpenAI-compatible 1536d) |
| `pagina_pdf` | `int` | `INTEGER` | NULL | |
| `created_at` | `datetime` | `TIMESTAMPTZ` | DEFAULT NOW() | |

**Índices**: `(contract_version_id, secao)`, `USING ivfflat (embedding vector_cosine_ops)` para similarity search.

#### 3.2.6. `PurchaseOrderHeader` — EKKO (cabeça do pedido SAP)

Projeção semântica das 179 colunas do EKKO para 12 campos. Carregada de `EKKO - SAP (Extração pedidos).MHTML.xlsx` (1.894 rows) ou `EKKO - EXTRAÇÃO CONTRATOS GUARDA CHUVAS.xlsx` (138 rows).

| Campo | Tipo Python | Tipo PG | Constraint | Origem (EKKO) |
|---|---|---|---|---|
| `id` | `UUID` | `UUID PK` | NOT NULL | gerado |
| `documento_compras` | `str` | `TEXT` | NOT NULL, INDEX | "Documento de compras" |
| `empresa` | `str` | `TEXT` | NOT NULL | "Empresa" |
| `categoria_doc` | `str` | `TEXT` | NULL | "Ctg.doc.compras" (ex.: "K" = guarda-chuva, "F" = pedido) |
| `tipo_doc` | `str` | `TEXT` | NULL | "Tp.doc.compras" |
| `fornecedor` | `str` | `TEXT` | NOT NULL, INDEX | "Fornecedor" (match com `SupplierBridge.numero_fornecedor_sap`) |
| `contrato_basico` | `str` | `TEXT` | NULL, INDEX | "Contrato básico" — referência ao guarda-chuva (REGRA 6) |
| `data_documento` | `date` | `DATE` | NULL | "Data do documento" |
| `inicio_validade` | `date` | `DATE` | NULL | "Início per.validade" |
| `fim_validade` | `date` | `DATE` | NULL | "Fim da validade" |
| `val_fix_cab` | `decimal` | `NUMERIC(15,2)` | NULL | "ValFix.(nível cab.)" |
| `moeda` | `str` | `TEXT` | DEFAULT 'BRL' | "Moeda" |
| `status` | `str` | `TEXT` | NULL | "Status" |
| `raw_extra` | `dict` | `JSONB` | NULL | demais 165 colunas crus (debug/auditoria) |
| `imported_at` | `datetime` | `TIMESTAMPTZ` | DEFAULT NOW() | |

**Índices**: `(documento_compras)` UNIQUE, `(fornecedor)`, `(contrato_basico)`, `(inicio_validade, fim_validade)`.

#### 3.2.7. `PurchaseOrderItem` — EKPO (item do pedido)

Projeção semântica das 283 colunas do EKPO. Carregada de `EKPO - SAP (Extração pedidos).MHTML.xlsx` (25.067 rows) ou guarda-chuva (44.782 rows).

| Campo | Tipo Python | Tipo PG | Constraint | Origem (EKPO) |
|---|---|---|---|---|
| `id` | `UUID` | `UUID PK` | NOT NULL | gerado |
| `documento_compras` | `str` | `TEXT` | NOT NULL, FK | `EKKO.Documento de compras` |
| `item` | `str` | `TEXT` | NOT NULL | "Item" (ex.: "1", "2") |
| `texto_breve` | `str` | `TEXT` | NULL | "Texto breve" (ex.: "EQUIPES HIBRIDAS NORTE/SUL FLUMINENSE") — REGRA 5 |
| `material` | `str` | `TEXT` | NULL | "Material" |
| `grupo_mercadorias` | `str` | `TEXT` | NULL | "Grupo de mercadorias" (ex.: "MANUTENG") |
| `quantidade` | `decimal` | `NUMERIC(15,4)` | NULL | "Qtd.do pedido" |
| `unidade_medida` | `str` | `TEXT` | NULL | "UM pedido" |
| `preco_liquido` | `decimal` | `NUMERIC(15,4)` | NULL | "Preço líq.pedido" |
| `valor_liquido` | `decimal` | `NUMERIC(15,2)` | NULL | "Valor líquido pedido" |
| `centro` | `str` | `TEXT` | NULL | "Centro" |
| `categoria_item` | `str` | `TEXT` | NULL | "Categoria do item" |
| `raw_extra` | `dict` | `JSONB` | NULL | demais ~270 colunas |
| `imported_at` | `datetime` | `TIMESTAMPTZ` | DEFAULT NOW() | |

**Índices**: `(documento_compras, item)` UNIQUE, `(grupo_mercadorias)`.

#### 3.2.8. `ServicePackage` — ESLL (linha de serviço)

Carregada de `ESLL - EXTRAÇÃO Nº DE PACOTES - LPU_VALORES.xlsx` (44.782 rows). É AQUI que a math da LPU acontece: `qtd_solicitada × preco_bruto ≈ valor_solicitado`.

| Campo | Tipo Python | Tipo PG | Constraint | Origem (ESLL) |
|---|---|---|---|---|
| `id` | `UUID` | `UUID PK` | NOT NULL | gerado |
| `pacote` | `str` | `TEXT` | NOT NULL, INDEX | "Nº pacote" (ex.: "1024777329") |
| `linha` | `int` | `INTEGER` | NOT NULL | "Linha" |
| `numero_servico` | `str` | `TEXT` | NOT NULL, INDEX | "Nº de serviço" (match com `LPUItem.numero_servico`) |
| `texto_breve` | `str` | `TEXT` | NULL | "Texto breve" |
| `preco_bruto` | `decimal` | `NUMERIC(15,4)` | NULL | "Preço bruto" — match REGRA LPU |
| `qtd_solicitada` | `decimal` | `NUMERIC(15,4)` | NULL | "Qtd.solicitada" |
| `valor_solicitado` | `decimal` | `NUMERIC(15,2)` | NULL | "Valor solicitado" |
| `ekpo_documento` | `str` | `TEXT` | NULL, INDEX | join com EKPO (via tabela `ESLL - EXTRAÇÃO EKPO_ESLL`) |
| `ekpo_item` | `str` | `TEXT` | NULL | |
| `imported_at` | `datetime` | `TIMESTAMPTZ` | DEFAULT NOW() | |

**Índices**: `(pacote, linha)`, `(numero_servico)`, `(ekpo_documento, ekpo_item)`.

#### 3.2.9. `RuleDefinition` — catálogo de regras

7 linhas iniciais (REGRA 1, 2, 3, 4, 5, 6, LPU). Parametrizável via UI (threshold, ativo/inativo, severidade).

| Campo | Tipo Python | Tipo PG | Constraint | Notas |
|---|---|---|---|---|
| `id` | `UUID` | `UUID PK` | NOT NULL | |
| `code` | `str` | `TEXT` | UNIQUE NOT NULL | "REGRA_1", "REGRA_2", ..., "REGRA_LPU" |
| `name` | `str` | `TEXT` | NOT NULL | "CNPJ match base ↔ PDF" |
| `description` | `str` | `TEXT` | NOT NULL | descrição operacional |
| `severity` | `str` | `TEXT` | NOT NULL | "low" / "medium" / "high" |
| `is_active` | `bool` | `BOOLEAN` | DEFAULT TRUE | |
| `threshold_params` | `dict` | `JSONB` | DEFAULT {} | ex.: `{"lpu_tolerance_pct": 1.0, "date_tolerance_days": 0}` |
| `engine_type` | `str` | `TEXT` | NOT NULL | "sql_deterministic" / "fuzzy" / "embedding" / "llm_judge" / "math_tolerance" |
| `python_handler` | `str` | `TEXT` | NOT NULL | dotted path, ex.: `app.core.services.payments.rules.regra_1_cnpj` |
| `version` | `int` | `INTEGER` | DEFAULT 1 | bump quando handler muda |
| `created_at` | `datetime` | `TIMESTAMPTZ` | DEFAULT NOW() | |
| `updated_at` | `datetime` | `TIMESTAMPTZ` | DEFAULT NOW() | |

#### 3.2.10. `ReconciliationRun` — execução do engine

Cada batch de reconciliação cria um run. Útil para auditoria ("qual run produziu esse finding?") e replay.

| Campo | Tipo Python | Tipo PG | Constraint | Notas |
|---|---|---|---|---|
| `id` | `UUID` | `UUID PK` | NOT NULL | |
| `triggered_by` | `str` | `TEXT` | NOT NULL | "manual" / "post_ingestion" / "scheduled" |
| `triggered_by_user_id` | `UUID` | `UUID` | NULL, FK → `users(id)` | quando manual |
| `rules_executed` | `str[]` | `TEXT[]` | NOT NULL | códigos: `["REGRA_1", "REGRA_2", ...]` |
| `scope_filter` | `dict` | `JSONB` | NULL | filtros: `{"empreiteira": "ABILITY", "since": "2024-01-01"}` |
| `status` | `str` | `TEXT` | NOT NULL | "running" / "completed" / "failed" |
| `started_at` | `datetime` | `TIMESTAMPTZ` | DEFAULT NOW() | |
| `finished_at` | `datetime` | `TIMESTAMPTZ` | NULL | |
| `findings_created` | `int` | `INTEGER` | DEFAULT 0 | |
| `error_message` | `str` | `TEXT` | NULL | |

#### 3.2.11. `ReconciliationFinding` — divergência detectada

Output principal do sistema. Cada finding = 1 violação de 1 regra contra 1 pagamento.

| Campo | Tipo Python | Tipo PG | Constraint | Notas |
|---|---|---|---|---|
| `id` | `UUID` | `UUID PK` | NOT NULL | |
| `run_id` | `UUID` | `UUID` | NOT NULL, FK | |
| `rule_id` | `UUID` | `UUID` | NOT NULL, FK | |
| `rule_code` | `str` | `TEXT` | NOT NULL | denormalizado para query rápida |
| `severity` | `str` | `TEXT` | NOT NULL | herdada da rule |
| `status` | `str` | `TEXT` | NOT NULL | "open" / "in_analysis" / "accepted_fp" / "escalated" / "blocked" |
| `purchase_order_documento` | `str` | `TEXT` | NOT NULL | join key SAP |
| `purchase_order_item` | `str` | `TEXT` | NULL | item específico (se aplicável) |
| `contract_master_id` | `UUID` | `UUID` | NULL, FK | contrato afetado |
| `contract_version_id` | `UUID` | `UUID` | NULL, FK | versão vigente na data do pagamento |
| `supplier_id` | `UUID` | `UUID` | NULL, FK → `supplier_bridge(id)` | |
| `expected_value` | `dict` | `JSONB` | NOT NULL | o que o contrato/regra esperava |
| `actual_value` | `dict` | `JSONB` | NOT NULL | o que o SAP/WF tem |
| `delta_pct` | `float` | `DOUBLE PRECISION` | NULL | desvio % (quando aplicável — LPU) |
| `value_at_risk_brl` | `decimal` | `NUMERIC(15,2)` | NULL | exposição financeira |
| `evidence_clause_ids` | `UUID[]` | `UUID[]` | NULL | refs a `contract_clause(id)` para rastreabilidade |
| `evidence_pages` | `int[]` | `INTEGER[]` | NULL | páginas do PDF citadas |
| `analyst_id` | `UUID` | `UUID` | NULL, FK → `users(id)` | quem está analisando |
| `decision_reason` | `str` | `TEXT` | NULL | comentário do analista |
| `decided_by_id` | `UUID` | `UUID` | NULL, FK → `users(id)` | quem fechou (gestor para "blocked") |
| `decided_at` | `datetime` | `TIMESTAMPTZ` | NULL | |
| `detected_at` | `datetime` | `TIMESTAMPTZ` | DEFAULT NOW() | created_at, renomeado para semântica |

**Índices**: `(status, severity, detected_at DESC)` (Inbox query), `(supplier_id)` (top suppliers), `(rule_code, detected_at)` (alertas por tipo).

#### 3.2.12. `ExtractionJob` — job assíncrono de extração PDF

Worker dramatiq cria um para cada upload PDF. UI mostra progresso.

| Campo | Tipo Python | Tipo PG | Constraint | Notas |
|---|---|---|---|---|
| `id` | `UUID` | `UUID PK` | NOT NULL | |
| `contract_master_id` | `UUID` | `UUID` | NULL, FK | NULL no início; setado após criação do master |
| `pdf_storage_key` | `str` | `TEXT` | NOT NULL | chave no DocumentStore |
| `pdf_filename` | `str` | `TEXT` | NOT NULL | |
| `pdf_size_bytes` | `int` | `BIGINT` | NOT NULL | |
| `pdf_pages` | `int` | `INTEGER` | NULL | preenchido pós-docling |
| `status` | `str` | `TEXT` | NOT NULL | "pending" / "extracting" / "review" / "approved" / "failed" |
| `extraction_started_at` | `datetime` | `TIMESTAMPTZ` | NULL | |
| `extraction_finished_at` | `datetime` | `TIMESTAMPTZ` | NULL | |
| `extracted_fields` | `dict` | `JSONB` | NULL | folha de rosto + array de LPU items, antes de aprovar |
| `confidence_per_field` | `dict` | `JSONB` | NULL | `{"valid_from": 0.95, "objeto": 0.78}` |
| `llm_model_used` | `str` | `TEXT` | NULL | |
| `cost_brl` | `decimal` | `NUMERIC(10,4)` | DEFAULT 0 | |
| `error_message` | `str` | `TEXT` | NULL | |
| `uploaded_by_id` | `UUID` | `UUID` | NOT NULL, FK → `users(id)` | |
| `created_at` | `datetime` | `TIMESTAMPTZ` | DEFAULT NOW() | |

---

## 4. Subsistemas

### 4.1. Worker Infrastructure (Fase 0)

**Stack**: `dramatiq` + `redis` + processo separado.

#### Componentes a implementar

| Arquivo | Função |
|---|---|
| `app/adapters/queue/__init__.py` | port `JobQueue` |
| `app/adapters/queue/dramatiq_adapter.py` | adapter usando dramatiq |
| `app/workers/__init__.py` | bootstrap do worker process |
| `app/workers/ingestion_actor.py` | actor: processa upload XLSX |
| `app/workers/extraction_actor.py` | actor: processa upload PDF (docling + Instructor) |
| `app/workers/reconciliation_actor.py` | actor: dispara rules engine |
| `Dockerfile.worker` | imagem específica do worker |

#### Configuração

`app/config.py` (acrescentar):

```python
redis_url: str = "redis://localhost:6379/0"
dramatiq_broker_url: str = ""  # se vazio, usa redis_url
dramatiq_worker_processes: int = 2
dramatiq_worker_threads: int = 4

pg_pool_payments_min_size: int = 2
pg_pool_payments_max_size: int = 10

storage_backend: str = "filesystem"   # "filesystem" | "s3"
storage_filesystem_root: str = "./data/storage"
storage_s3_bucket: str = ""
storage_s3_endpoint: str = ""         # MinIO ou S3 real
storage_s3_access_key: str = ""
storage_s3_secret_key: str = ""
```

#### Docker compose (acrescentar serviço worker)

```yaml
worker:
  build:
    context: .
    dockerfile: Dockerfile.worker
  command: dramatiq app.workers --processes 2 --threads 4
  environment:
    DATABASE_URL: ${DATABASE_URL}
    REDIS_URL: redis://redis:6379/0
    PG_POOL_PAYMENTS_MAX_SIZE: 10
  depends_on:
    redis: { condition: service_started }
    postgres: { condition: service_healthy }
  deploy:
    resources:
      limits: { cpus: '2', memory: 4G }
      reservations: { cpus: '1', memory: 2G }

redis:
  image: redis:7-alpine
  command: redis-server --maxmemory 256mb --maxmemory-policy allkeys-lru
  ports: ["6379:6379"]
```

#### Acceptance G7-fase0

- 10 uploads paralelos (PDF) não degradam p95 dos endpoints existentes em >10%
- Reset/restart do worker container não derruba o FastAPI principal
- Falha em LLM API loga erro mas não derruba o worker (retry exponential 3x)

### 4.2. Storage Adapter

Port hexagonal LLM-agnostic. PDFs/XLSX originais sobem para storage; banco guarda só `storage_key`.

```python
# app/core/ports/storage.py
from abc import ABC, abstractmethod

class DocumentStore(ABC):
    @abstractmethod
    async def put(self, key: str, content: bytes, content_type: str) -> str: ...

    @abstractmethod
    async def get(self, key: str) -> tuple[bytes, str]: ...

    @abstractmethod
    async def url(self, key: str, ttl_seconds: int = 3600) -> str: ...

    @abstractmethod
    async def delete(self, key: str) -> bool: ...
```

Adapters:
- `app/adapters/storage/filesystem_store.py` — desenvolvimento, escreve em `${STORAGE_FILESYSTEM_ROOT}/payments/`
- `app/adapters/storage/s3_minio_store.py` — produção, usa boto3 contra S3 ou MinIO

Convenção de chaves: `contracts/{contract_master_id}/v{version_number}/{filename}` para PDFs; `sap_imports/{yyyy-mm-dd}/{filename}` para XLSX.

### 4.3. Schema Projector

Mapeia colunas raw do SAP (179 EKKO, 283 EKPO) para os ~12 campos semânticos.

#### Formato declarativo (`app/adapters/sap/projections/ekko.yaml`):

```yaml
target_table: payments.purchase_order_header
source_format: xlsx
column_mapping:
  documento_compras: "Documento de compras"
  empresa: "Empresa"
  categoria_doc: "Ctg.doc.compras"
  tipo_doc: "Tp.doc.compras"
  fornecedor: "Fornecedor"
  contrato_basico: "Contrato básico"
  data_documento: "Data do documento"
  inicio_validade: "Início per.validade"
  fim_validade: "Fim da validade"
  val_fix_cab: "ValFix.(nível cab.)"
  moeda: "Moeda"
  status: "Status"
type_coercion:
  data_documento: "date"
  inicio_validade: "date"
  fim_validade: "date"
  val_fix_cab: "decimal"
  documento_compras: "text"
raw_columns_to_jsonb: true     # demais colunas vão em raw_extra
validation:
  required: [documento_compras, fornecedor]
  unique: documento_compras
```

`app/core/services/payments/schema_projector.py`:

```python
class SchemaProjector:
    def __init__(self, projection_yaml: str): ...

    def project_rows(self, raw_rows: Iterator[dict]) -> Iterator[dict]:
        """Aplica mapping + type coercion. Falhas viram exception com row_num."""
```

### 4.4. Ingestion Service (XLSX SAP)

#### Fluxo

1. Usuário admin faz POST `/api/payments/ingestion/sap` com arquivo XLSX + tipo (`ekko_pedidos` | `ekko_guarda_chuva` | `ekpo_pedidos` | `ekpo_guarda_chuva` | `esll_lpu` | `esll_pacotes` | `contratos_empreteiras`).
2. FastAPI valida (tamanho, MIME), faz `DocumentStore.put()`, enfileira dramatiq job, retorna `202 Accepted` + `job_id`.
3. Worker:
   - Lê XLSX com Polars lazy (chunks de 5000 linhas)
   - Aplica `SchemaProjector` para o tipo
   - Bulk insert via `asyncpg COPY FROM STDIN` em staging table
   - MERGE da staging para tabela final
   - Dispara `REFRESH MATERIALIZED VIEW CONCURRENTLY payments.mv_kpis`
   - Enfileira `reconciliation_actor` para rodar regras sobre os novos rows
4. Audit event registrado.

#### Acceptance G1

- Carga dos 7 XLSX (~120k rows totais) <60s
- Idempotente: re-upload do mesmo arquivo não duplica rows (ON CONFLICT DO UPDATE)
- Falha parcial não corrompe — staging table garante atomicidade

### 4.5. Document Ingestion Service (PDF)

#### Stack

- **docling** ([github.com/DS4SD/docling](https://github.com/DS4SD/docling)) — PDF → markdown + tabelas estruturadas, com OCR fallback
- **Instructor** ([github.com/jxnl/instructor](https://github.com/jxnl/instructor)) — LLM com schema Pydantic (saída tipada e validada)
- **pgvector** — embeddings de cláusulas para retrieval

#### Pipeline

1. Upload via POST `/api/payments/contracts/upload` (multipart PDF) → cria `ExtractionJob` com `status='pending'`
2. Worker pega o job:
   - `status='extracting'`
   - `docling.DocumentConverter().convert(pdf_path)` → produz `result` com `document.export_to_markdown()` e `document.tables`
   - Chama LLM (ClaroHub) com Instructor + Pydantic schema `FolhaDeRosto`:

     ```python
     class FolhaDeRosto(BaseModel):
         cnpj: str = Field(description="CNPJ da empreiteira")
         contrato_juridico_ref: str = Field(description="referência do contrato (ex.: CW149898)")
         valid_from: date
         valid_to: date
         val_fix_cab: Decimal | None
         objeto_contrato: str = Field(description="cláusula de objeto, max 500 chars")
         tecnologia: str = Field(description="ex.: FIBRA ÓPTICA, HFC")
         atividade: str = Field(description="ex.: MANUTENÇÃO PREVENTIVA")
         uf: list[str] = Field(description="lista de UFs cobertas")
         cidade: list[str] = Field(default_factory=list)
     ```
   - Para LPU: detecta tabelas no `result.document.tables`, mapeia colunas heurísticamente (numero_servico, descricao, preco_unitario), produz lista de `LPUItem`s
   - Para cláusulas: divide o markdown em parágrafos por header (`# OBJETO`, `## PREÇO`), gera embeddings via ClaroHub (ou Maritaca)
   - Grava em `ExtractionJob.extracted_fields` e `confidence_per_field`
   - `status='review'`
3. HITL: admin abre `/payments/empreiteiras-wf/contratos/extracao/{job_id}` → revisa campos (com confidence scores destacados) → corrige se necessário → aprova
4. Aprovação cria `ContractMaster` + `ContractVersion` + `LPUItem`s + `ContractClause`s. `status='approved'`. Audit event.

#### Custos esperados

- PDF ~100 páginas: ~400k tokens input + 1k output
- ClaroHub on-prem: R$ 0 (sem custo direto)
- Maritaca (se usar como fallback): ~R$ 0,32 input + R$ 0,002 output = ~R$ 0,33/PDF
- 50 PDFs/mês × R$ 0,33 = R$ 16,50/mês (manejável)

#### Acceptance G2

- 5 PDFs reais (fornecidos pelo user) extraídos sem erro fatal
- ≥85% dos campos da `FolhaDeRosto` corretos pós-HITL (humano corrige em ≤15%)
- LPU: ≥80% das linhas extraídas com `numero_servico` + `preco_unitario` corretos
- Cost ledger registrado em `finops_ledger` com `domain='payments', product='empreiteiras_wf', agent='pdf_extractor'`

### 4.6. Reconciliation Engine

Núcleo do produto. Roda regras determinísticas + semânticas. Output: findings.

#### Registry pattern

```python
# app/core/services/payments/rules/__init__.py
RULES_REGISTRY: dict[str, Callable[[ReconciliationContext], Iterator[FindingDraft]]] = {}

def register(code: str):
    def decorator(fn):
        RULES_REGISTRY[code] = fn
        return fn
    return decorator
```

```python
# app/core/services/payments/rules/regra_1.py
@register("REGRA_1")
async def regra_1_cnpj(ctx: ReconciliationContext) -> AsyncIterator[FindingDraft]:
    """CNPJ da base Contratos-Empreteiras deve bater com o do PDF."""
    async with ctx.db.transaction():
        rows = await ctx.db.fetch("""
            SELECT cm.id AS contract_master_id, cm.cnpj AS pdf_cnpj,
                   sb.cnpj AS base_cnpj, sb.id AS supplier_id
            FROM payments.contract_master cm
            JOIN payments.supplier_bridge sb ON sb.id = cm.supplier_bridge_id
            WHERE cm.cnpj <> sb.cnpj
        """)
        for r in rows:
            yield FindingDraft(
                rule_code="REGRA_1",
                contract_master_id=r["contract_master_id"],
                supplier_id=r["supplier_id"],
                expected_value={"cnpj": r["base_cnpj"]},
                actual_value={"cnpj": r["pdf_cnpj"]},
                severity="high",
                value_at_risk_brl=None,
            )
```

#### Engine runner

```python
# app/core/services/payments/reconciliation_engine.py
class ReconciliationEngine:
    async def run(
        self,
        rule_codes: list[str],
        scope_filter: dict | None = None,
        triggered_by: str = "manual",
        triggered_by_user_id: UUID | None = None,
    ) -> UUID:  # returns run_id
        ...
```

Execução paralela (asyncio.gather) por regra, com timeout individual. Findings inseridos em batch a cada N=100 ou no fim de cada regra.

#### Acceptance G3

- 4 regras determinísticas (1, 2, 6, LPU) com ≥5 fixtures cada (positivos + negativos)
- Cobertura ≥90%
- 261 OS processadas em <30s (escopo da POC)

### 4.7. Empreiteiras-WF UI

Reaproveita FastAPI + Jinja2 + HTMX + Alpine + Tailwind (já no fork). Adições:

#### Páginas

| Rota | Template | Persona alvo |
|---|---|---|
| `/payments/empreiteiras-wf` | `payments/empreiteiras_wf/visao_geral.html` | gestor (mockup #2) |
| `/payments/empreiteiras-wf/alertas` | `.../alertas/inbox.html` | analista (mockup #1) |
| `/payments/empreiteiras-wf/alertas/{finding_id}` | `.../alertas/detail.html` | analista |
| `/payments/empreiteiras-wf/contratos` | `.../contratos/lista.html` | admin |
| `/payments/empreiteiras-wf/contratos/upload` | `.../contratos/upload.html` | admin |
| `/payments/empreiteiras-wf/contratos/extracao/{job_id}` | `.../contratos/revisao_extracao.html` | admin (HITL) |
| `/payments/empreiteiras-wf/contratos/{contract_id}` | `.../contratos/detalhe.html` | admin |
| `/payments/empreiteiras-wf/ingestao` | `.../ingestao/sap.html` | admin |
| `/payments/empreiteiras-wf/regras` | `.../regras/lista.html` | admin |
| `/payments/empreiteiras-wf/exploracao` | reusa template do `text2sql` existente | analista sênior |

#### Componentes Jinja reutilizáveis

- `partials/kpi_card.html` — card para os 9 KPIs (icon + valor + delta + ação)
- `partials/finding_row.html` — linha de finding no Inbox
- `partials/finding_detail_split.html` — vista 2 colunas (pagamento × contrato)
- `partials/donut_chart.html` — wrapper Chart.js para Alertas por Tipo
- `partials/bar_chart.html` — Top Fornecedores
- `partials/horizontal_bar.html` — Risco Financeiro por Fornecedor

#### Atualização do `nav_left.html`

Inserir novo grupo entre "Configurações" e "Monitoramento":

```python
{'label': 'Pagamentos', 'allowed_roles': ['root', 'admin', 'supervisor', 'finops', 'analista_n3', 'analista_n2', 'analista_n1'], 'entries': [
  ('empreiteiras_wf_visao', 'Empreiteiras-WF', '/payments/empreiteiras-wf', 'SVG path...', None),
  ('empreiteiras_wf_alertas', 'Alertas', '/payments/empreiteiras-wf/alertas', '...', None),
  ('empreiteiras_wf_contratos', 'Contratos', '/payments/empreiteiras-wf/contratos', '...', ['admin', 'supervisor']),
  ('empreiteiras_wf_ingestao', 'Ingestão SAP', '/payments/empreiteiras-wf/ingestao', '...', ['admin']),
  ('empreiteiras_wf_regras', 'Regras', '/payments/empreiteiras-wf/regras', '...', ['admin']),
]},
```

#### Acceptance G5 + G6

- Visão Geral carrega em <1s (matview)
- Inbox responde a filtros (empreiteira, severidade, regra) em <500ms
- Detalhe abre 2 colunas (pagamento × contrato) sem scroll inicial em 1080p

---

## 5. Data Flow End-to-End

### 5.1. Onboarding de novo contrato (admin)

```
1. Admin abre /payments/empreiteiras-wf/contratos/upload
2. Submete PDF (multipart/form-data) + escolhe supplier_bridge da DE-PARA
3. FastAPI:
   a. Valida MIME = application/pdf, tamanho <50MB
   b. AuditMiddleware registra POST
   c. DocumentStore.put() → retorna storage_key
   d. INSERT ExtractionJob status=pending
   e. dramatiq.send(extract_pdf, job_id)
   f. retorna 202 + Location: /contratos/extracao/{job_id}
4. Browser polling GET /api/payments/extraction-jobs/{job_id} a cada 2s
5. Worker:
   a. UPDATE status='extracting'
   b. docling.convert() → markdown + tables
   c. Instructor.create_from_messages(FolhaDeRosto, ...) → folha estruturada
   d. para tabela de LPU: detecta + extrai linhas
   e. para cláusulas: divide markdown → embeddings (ClaroHub)
   f. UPDATE extracted_fields, confidence_per_field, status='review'
   g. dramatiq retorna sucesso
6. Browser detecta status=review → abre tela de revisão
7. Admin revisa campos (com cores: vermelho confidence<0.6, amarelo<0.85, verde>=0.85)
   a. Edita campos errados, adiciona LPU items faltantes
   b. POST /api/payments/extraction-jobs/{job_id}/approve
8. FastAPI:
   a. Transação:
      INSERT contract_master, contract_version (v1), lpu_items, contract_clauses
      UPDATE contract_master.current_version_id
      UPDATE extraction_job.status='approved', .contract_master_id
   b. AuditEvent: extraction_approved
   c. dramatiq.send(reconciliation_actor, {scope: {contract_master_id}})
9. Aparece na lista /contratos com status "ativo"
```

### 5.2. Ingestão de XLSX SAP (admin / batch)

```
1. Admin abre /payments/empreiteiras-wf/ingestao
2. Submete XLSX + escolhe tipo (ekko_pedidos, etc.)
3. FastAPI: 202 Accepted + job_id
4. Worker:
   a. DocumentStore.get(key) → bytes
   b. polars.scan_excel(...).collect_in_chunks(5000)
   c. SchemaProjector(yaml=ekko.yaml).project_rows()
   d. asyncpg COPY FROM STDIN → staging table
   e. INSERT ... ON CONFLICT DO UPDATE (idempotente)
   f. DROP staging
   g. REFRESH MATERIALIZED VIEW CONCURRENTLY mv_kpis
5. dramatiq.send(reconciliation_actor, {rule_codes: [REGRA_LPU, REGRA_6]})
6. Worker (reconciliation):
   a. INSERT reconciliation_run
   b. para cada rule_code, executa handler
   c. INSERT findings em batch
   d. UPDATE run status='completed', findings_created=N
7. Notificação na UI (HTMX SSE? polling? — fica como detalhe da Fase 6)
```

### 5.3. Análise de divergência (analista — jornada diária)

```
1. Analista loga → cai em /payments/empreiteiras-wf/alertas (Inbox)
2. Vê lista de findings status=open, ordenada por (severity DESC, detected_at DESC)
3. Filtros: empreiteira, regra, valor mínimo, data
4. Clica num finding → /alertas/{finding_id}
5. UI renderiza split view 2 colunas:
   ESQ: pagamento (de purchase_order_item + service_package + supplier_bridge)
   DIR: contrato (de contract_master + contract_version vigente em detected_at)
        + citação da cláusula (de contract_clause, com link "ver PDF página X")
6. Analista escolhe ação:
   - "Aceitar (falso positivo)" → status=accepted_fp, decision_reason, AuditEvent
   - "Escalar p/ Compras" → status=escalated, notifica supervisor (Slack? email? Fase 6)
   - "Bloquear pagamento" → SE role inclui supervisor/finops/admin:
       status=blocked, decided_by_id, AuditEvent
     SE role é analista_n*:
       403 — analista escala, não bloqueia (OPA policy)
7. Próximo finding (botão "Próximo" no detalhe pula pro next na fila)
```

---

## 6. API Spec

### 6.1. Convenções

- Base: `/api/payments/`
- Auth: JWT bearer (reaproveita `oauth2_scheme` existente)
- RBAC: `Depends(require_roles(...))` por endpoint
- Audit: automático via `AuditMiddleware`
- Erros: padrão `{"detail": "..."}` 400/401/403/404/422/500

### 6.2. Endpoints

#### Contratos (Master/Version)

| Método | Path | Roles | Notas |
|---|---|---|---|
| POST | `/api/payments/contracts/upload` | admin | multipart PDF + supplier_bridge_id → 202 + job_id |
| GET | `/api/payments/contracts` | admin, supervisor | lista paginada, filtros: empreiteira, is_monitored |
| GET | `/api/payments/contracts/{id}` | admin, supervisor | detalhe + versions + LPU + clauses |
| PATCH | `/api/payments/contracts/{id}` | admin | toggle is_monitored, change supplier_bridge |
| DELETE | `/api/payments/contracts/{id}` | root | soft-delete (audit) |

#### Extraction Jobs (HITL)

| Método | Path | Roles | Notas |
|---|---|---|---|
| GET | `/api/payments/extraction-jobs/{id}` | admin | status, extracted_fields, confidence |
| POST | `/api/payments/extraction-jobs/{id}/approve` | admin | finaliza com correções aplicadas |
| POST | `/api/payments/extraction-jobs/{id}/reject` | admin | descarta (PDF stays for audit) |

#### Ingestão SAP

| Método | Path | Roles | Notas |
|---|---|---|---|
| POST | `/api/payments/ingestion/sap` | admin | multipart XLSX + tipo → 202 + job_id |
| GET | `/api/payments/ingestion/history` | admin, finops | lista de ingestões |

#### Reconciliation

| Método | Path | Roles | Notas |
|---|---|---|---|
| POST | `/api/payments/reconciliation/run` | admin, supervisor | trigger manual com filtros |
| GET | `/api/payments/reconciliation/runs` | admin, supervisor | histórico |
| GET | `/api/payments/reconciliation/runs/{id}` | admin, supervisor | detalhe + findings |

#### Findings (Inbox)

| Método | Path | Roles | Notas |
|---|---|---|---|
| GET | `/api/payments/findings` | analista_n*, supervisor, admin | lista paginada com filtros |
| GET | `/api/payments/findings/{id}` | analista_n*, supervisor, admin | detalhe completo |
| POST | `/api/payments/findings/{id}/accept` | analista_n*, supervisor, admin | status=accepted_fp |
| POST | `/api/payments/findings/{id}/escalate` | analista_n*, supervisor, admin | status=escalated |
| POST | `/api/payments/findings/{id}/block` | supervisor, admin, finops | status=blocked (OPA gateado) |
| POST | `/api/payments/findings/bulk` | analista_n*, supervisor, admin | array de IDs + ação |

#### Rules (catálogo + config)

| Método | Path | Roles | Notas |
|---|---|---|---|
| GET | `/api/payments/rules` | admin | lista as 7 |
| PATCH | `/api/payments/rules/{code}` | admin | toggle is_active, edit threshold_params |

#### KPIs (matview-backed)

| Método | Path | Roles | Notas |
|---|---|---|---|
| GET | `/api/payments/kpis/visao-geral` | all auth | os 9 KPIs do mockup |
| GET | `/api/payments/kpis/alertas-por-tipo` | all auth | donut |
| GET | `/api/payments/kpis/top-fornecedores` | all auth | bar |
| GET | `/api/payments/kpis/risco-por-fornecedor` | all auth | horizontal bar |

---

## 7. Database Schema (DDL)

### 7.1. Schema isolado

```sql
CREATE SCHEMA IF NOT EXISTS payments;
CREATE EXTENSION IF NOT EXISTS vector;  -- pgvector
```

### 7.2. DDL completo

```sql
-- ============================================================
-- SupplierBridge: tabela-âncora DE-PARA
-- ============================================================
CREATE TABLE IF NOT EXISTS payments.supplier_bridge (
    id                       UUID PRIMARY KEY,
    categoria                TEXT NOT NULL,
    empreiteira              TEXT NOT NULL,
    contrato_num_sap         TEXT NOT NULL,
    ref_ws                   TEXT NOT NULL,
    numero_fornecedor_sap    TEXT NOT NULL,
    cnpj                     TEXT NOT NULL,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (contrato_num_sap, ref_ws)
);
CREATE INDEX idx_supplier_contrato ON payments.supplier_bridge(contrato_num_sap);
CREATE INDEX idx_supplier_cnpj     ON payments.supplier_bridge(cnpj);
CREATE INDEX idx_supplier_ref_ws   ON payments.supplier_bridge(ref_ws);

-- ============================================================
-- ContractMaster + ContractVersion (temporal)
-- ============================================================
CREATE TABLE IF NOT EXISTS payments.contract_master (
    id                    UUID PRIMARY KEY,
    supplier_bridge_id    UUID NOT NULL REFERENCES payments.supplier_bridge(id),
    contrato_num_sap      TEXT NOT NULL,
    ref_ws                TEXT NOT NULL,
    cnpj                  TEXT NOT NULL,
    current_version_id    UUID,  -- FK adicionada após contract_version criada
    is_monitored          BOOLEAN NOT NULL DEFAULT TRUE,
    created_by_id         UUID NOT NULL REFERENCES public.users(id),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_contract_master_supplier ON payments.contract_master(supplier_bridge_id);
CREATE INDEX idx_contract_master_monitored ON payments.contract_master(is_monitored) WHERE is_monitored;

CREATE TABLE IF NOT EXISTS payments.contract_version (
    id                       UUID PRIMARY KEY,
    contract_master_id       UUID NOT NULL REFERENCES payments.contract_master(id) ON DELETE CASCADE,
    version_number           INTEGER NOT NULL,
    valid_from               DATE NOT NULL,
    valid_to                 DATE NOT NULL,
    val_fix_cab              NUMERIC(15,2),
    objeto_contrato          TEXT,
    tecnologia               TEXT,
    atividade                TEXT,
    uf                       TEXT[],
    cidade                   TEXT[],
    pdf_storage_key          TEXT,
    extracted_by_llm_model   TEXT,
    extracted_cost_brl       NUMERIC(10,4) NOT NULL DEFAULT 0,
    confidence_avg           DOUBLE PRECISION,
    reviewed_by_id           UUID REFERENCES public.users(id),
    reviewed_at              TIMESTAMPTZ,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (contract_master_id, version_number)
);
CREATE INDEX idx_contract_version_temporal
    ON payments.contract_version(contract_master_id, valid_from, valid_to);

ALTER TABLE payments.contract_master
    ADD CONSTRAINT fk_cm_current_version
    FOREIGN KEY (current_version_id) REFERENCES payments.contract_version(id);

-- ============================================================
-- LPUItem
-- ============================================================
CREATE TABLE IF NOT EXISTS payments.lpu_item (
    id                       UUID PRIMARY KEY,
    contract_version_id      UUID NOT NULL REFERENCES payments.contract_version(id) ON DELETE CASCADE,
    numero_servico           TEXT NOT NULL,
    descricao                TEXT NOT NULL,
    unidade_medida           TEXT,
    preco_unitario           NUMERIC(15,4) NOT NULL,
    moeda                    TEXT NOT NULL DEFAULT 'BRL',
    pagina_pdf               INTEGER,
    clausula_ref             TEXT,
    extracted_by_llm         BOOLEAN NOT NULL DEFAULT TRUE,
    confidence               DOUBLE PRECISION
);
CREATE INDEX idx_lpu_version ON payments.lpu_item(contract_version_id);
CREATE INDEX idx_lpu_servico ON payments.lpu_item(numero_servico);

-- ============================================================
-- ContractClause + pgvector
-- ============================================================
CREATE TABLE IF NOT EXISTS payments.contract_clause (
    id                       UUID PRIMARY KEY,
    contract_version_id      UUID NOT NULL REFERENCES payments.contract_version(id) ON DELETE CASCADE,
    clausula_numero          TEXT,
    secao                    TEXT,
    texto                    TEXT NOT NULL,
    embedding                vector(1536),
    pagina_pdf               INTEGER,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_clause_version ON payments.contract_clause(contract_version_id, secao);
CREATE INDEX idx_clause_embedding
    ON payments.contract_clause
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- ============================================================
-- PurchaseOrderHeader (EKKO) — projetado
-- ============================================================
CREATE TABLE IF NOT EXISTS payments.purchase_order_header (
    id                       UUID PRIMARY KEY,
    documento_compras        TEXT NOT NULL UNIQUE,
    empresa                  TEXT NOT NULL,
    categoria_doc            TEXT,
    tipo_doc                 TEXT,
    fornecedor               TEXT NOT NULL,
    contrato_basico          TEXT,
    data_documento           DATE,
    inicio_validade          DATE,
    fim_validade             DATE,
    val_fix_cab              NUMERIC(15,2),
    moeda                    TEXT NOT NULL DEFAULT 'BRL',
    status                   TEXT,
    raw_extra                JSONB,
    imported_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_ekko_fornecedor    ON payments.purchase_order_header(fornecedor);
CREATE INDEX idx_ekko_contrato_basico ON payments.purchase_order_header(contrato_basico);
CREATE INDEX idx_ekko_validade      ON payments.purchase_order_header(inicio_validade, fim_validade);

-- ============================================================
-- PurchaseOrderItem (EKPO)
-- ============================================================
CREATE TABLE IF NOT EXISTS payments.purchase_order_item (
    id                       UUID PRIMARY KEY,
    documento_compras        TEXT NOT NULL,
    item                     TEXT NOT NULL,
    texto_breve              TEXT,
    material                 TEXT,
    grupo_mercadorias        TEXT,
    quantidade               NUMERIC(15,4),
    unidade_medida           TEXT,
    preco_liquido            NUMERIC(15,4),
    valor_liquido            NUMERIC(15,2),
    centro                   TEXT,
    categoria_item           TEXT,
    raw_extra                JSONB,
    imported_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (documento_compras, item)
);
CREATE INDEX idx_ekpo_grupo ON payments.purchase_order_item(grupo_mercadorias);

-- ============================================================
-- ServicePackage (ESLL)
-- ============================================================
CREATE TABLE IF NOT EXISTS payments.service_package (
    id                       UUID PRIMARY KEY,
    pacote                   TEXT NOT NULL,
    linha                    INTEGER NOT NULL,
    numero_servico           TEXT NOT NULL,
    texto_breve              TEXT,
    preco_bruto              NUMERIC(15,4),
    qtd_solicitada           NUMERIC(15,4),
    valor_solicitado         NUMERIC(15,2),
    ekpo_documento           TEXT,
    ekpo_item                TEXT,
    imported_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (pacote, linha)
);
CREATE INDEX idx_esll_servico ON payments.service_package(numero_servico);
CREATE INDEX idx_esll_ekpo    ON payments.service_package(ekpo_documento, ekpo_item);

-- ============================================================
-- RuleDefinition
-- ============================================================
CREATE TABLE IF NOT EXISTS payments.rule_definition (
    id                       UUID PRIMARY KEY,
    code                     TEXT NOT NULL UNIQUE,
    name                     TEXT NOT NULL,
    description              TEXT NOT NULL,
    severity                 TEXT NOT NULL CHECK (severity IN ('low','medium','high')),
    is_active                BOOLEAN NOT NULL DEFAULT TRUE,
    threshold_params         JSONB NOT NULL DEFAULT '{}'::jsonb,
    engine_type              TEXT NOT NULL,
    python_handler           TEXT NOT NULL,
    version                  INTEGER NOT NULL DEFAULT 1,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- ReconciliationRun
-- ============================================================
CREATE TABLE IF NOT EXISTS payments.reconciliation_run (
    id                       UUID PRIMARY KEY,
    triggered_by             TEXT NOT NULL,
    triggered_by_user_id     UUID REFERENCES public.users(id),
    rules_executed           TEXT[] NOT NULL,
    scope_filter             JSONB,
    status                   TEXT NOT NULL CHECK (status IN ('running','completed','failed')),
    started_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at              TIMESTAMPTZ,
    findings_created         INTEGER NOT NULL DEFAULT 0,
    error_message            TEXT
);

-- ============================================================
-- ReconciliationFinding (output principal)
-- ============================================================
CREATE TABLE IF NOT EXISTS payments.reconciliation_finding (
    id                          UUID PRIMARY KEY,
    run_id                      UUID NOT NULL REFERENCES payments.reconciliation_run(id),
    rule_id                     UUID NOT NULL REFERENCES payments.rule_definition(id),
    rule_code                   TEXT NOT NULL,
    severity                    TEXT NOT NULL,
    status                      TEXT NOT NULL CHECK (status IN
        ('open','in_analysis','accepted_fp','escalated','blocked')) DEFAULT 'open',
    purchase_order_documento    TEXT NOT NULL,
    purchase_order_item         TEXT,
    contract_master_id          UUID REFERENCES payments.contract_master(id),
    contract_version_id         UUID REFERENCES payments.contract_version(id),
    supplier_id                 UUID REFERENCES payments.supplier_bridge(id),
    expected_value              JSONB NOT NULL,
    actual_value                JSONB NOT NULL,
    delta_pct                   DOUBLE PRECISION,
    value_at_risk_brl           NUMERIC(15,2),
    evidence_clause_ids         UUID[],
    evidence_pages              INTEGER[],
    analyst_id                  UUID REFERENCES public.users(id),
    decision_reason             TEXT,
    decided_by_id               UUID REFERENCES public.users(id),
    decided_at                  TIMESTAMPTZ,
    detected_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_finding_inbox      ON payments.reconciliation_finding(status, severity, detected_at DESC);
CREATE INDEX idx_finding_supplier   ON payments.reconciliation_finding(supplier_id);
CREATE INDEX idx_finding_rule_date  ON payments.reconciliation_finding(rule_code, detected_at DESC);

-- ============================================================
-- ExtractionJob (worker async)
-- ============================================================
CREATE TABLE IF NOT EXISTS payments.extraction_job (
    id                          UUID PRIMARY KEY,
    contract_master_id          UUID REFERENCES payments.contract_master(id),
    pdf_storage_key             TEXT NOT NULL,
    pdf_filename                TEXT NOT NULL,
    pdf_size_bytes              BIGINT NOT NULL,
    pdf_pages                   INTEGER,
    status                      TEXT NOT NULL CHECK (status IN
        ('pending','extracting','review','approved','failed')),
    extraction_started_at       TIMESTAMPTZ,
    extraction_finished_at      TIMESTAMPTZ,
    extracted_fields            JSONB,
    confidence_per_field        JSONB,
    llm_model_used              TEXT,
    cost_brl                    NUMERIC(10,4) NOT NULL DEFAULT 0,
    error_message               TEXT,
    uploaded_by_id              UUID NOT NULL REFERENCES public.users(id),
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_extraction_status ON payments.extraction_job(status, created_at DESC);
```

### 7.3. Materialized Views — KPIs do dashboard

```sql
-- KPI agregado pra Visão Geral (mockup #2). Refresh disparado pós-ingestão.
CREATE MATERIALIZED VIEW payments.mv_kpis_empreiteiras_wf AS
SELECT
    -- Contratos monitorados (total e ativos)
    (SELECT COUNT(*) FROM payments.contract_master WHERE is_monitored) AS contratos_monitorados,
    (SELECT COUNT(*) FROM payments.contract_master) AS contratos_total,
    -- OS analisadas (purchase_order_item únicos com pelo menos 1 reconciliação)
    (SELECT COUNT(DISTINCT (documento_compras, item))
        FROM payments.purchase_order_item) AS os_analisadas,
    -- Total alertas open
    (SELECT COUNT(*) FROM payments.reconciliation_finding WHERE status = 'open') AS total_alertas,
    -- Risco exposição financeira
    (SELECT COALESCE(SUM(value_at_risk_brl), 0)
        FROM payments.reconciliation_finding WHERE status = 'open') AS risco_brl,
    (SELECT COALESCE(SUM(valor_liquido), 0)
        FROM payments.purchase_order_item) AS valor_total_brl,
    -- Comparativo LPU: total bruto solicitado e desvio médio
    (SELECT COALESCE(SUM(valor_solicitado), 0)
        FROM payments.service_package) AS comparativo_lpu_brl,
    (SELECT AVG(delta_pct)
        FROM payments.reconciliation_finding
        WHERE rule_code = 'REGRA_LPU') AS delta_medio_lpu_pct,
    -- Taxa de recorrência (% fornecedores com 3+ findings)
    (SELECT 100.0 * COUNT(*) FILTER (WHERE finding_count >= 3) / NULLIF(COUNT(*), 0)
        FROM (
            SELECT supplier_id, COUNT(*) AS finding_count
            FROM payments.reconciliation_finding
            WHERE supplier_id IS NOT NULL
            GROUP BY supplier_id
        ) sub) AS taxa_recorrencia_pct,
    -- Tempo médio detecção (em dias) entre payment posted e finding created
    (SELECT EXTRACT(EPOCH FROM AVG(
            f.detected_at - poh.data_documento::timestamptz
        )) / 86400.0
        FROM payments.reconciliation_finding f
        JOIN payments.purchase_order_header poh ON poh.documento_compras = f.purchase_order_documento)
        AS tempo_medio_deteccao_dias,
    -- Acuracidade = regras executadas com sucesso / total
    (SELECT COUNT(*) FROM payments.rule_definition WHERE is_active) AS regras_ativas,
    NOW() AS refreshed_at;

CREATE UNIQUE INDEX idx_mv_kpis_singleton ON payments.mv_kpis_empreiteiras_wf((1));

-- Refresh helper (chamado pelo worker pós-ingestão)
CREATE OR REPLACE FUNCTION payments.refresh_kpis() RETURNS void AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY payments.mv_kpis_empreiteiras_wf;
END;
$$ LANGUAGE plpgsql;
```

### 7.4. Seed inicial (RuleDefinition)

Carregado no `init_db()` via `app/adapters/db/seed_payments.sql`:

```sql
INSERT INTO payments.rule_definition (id, code, name, description, severity, engine_type, python_handler, threshold_params)
VALUES
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
    (gen_random_uuid(), 'REGRA_3', 'Outros campos base ↔ PDF',
     'Campos auxiliares da base que devem bater com PDF',
     'medium', 'sql_deterministic',
     'app.core.services.payments.rules.regra_3_outros',
     '{}'::jsonb),
    (gen_random_uuid(), 'REGRA_4', 'Variáveis extraídas por contrato',
     'Validação de presença das variáveis extraídas no contrato',
     'medium', 'sql_deterministic',
     'app.core.services.payments.rules.regra_4_variaveis',
     '{}'::jsonb),
    (gen_random_uuid(), 'REGRA_5', 'Objeto/Tecnologia/Atividade/UF/Cidade',
     'Match semântico do escopo: OBJETO_CONTRATO, TECNOLOGIA, ATIVIDADE entre EKPO.texto_breve e contract_version',
     'medium', 'embedding',
     'app.core.services.payments.rules.regra_5_escopo',
     '{"fuzzy_threshold": 0.85, "embedding_threshold": 0.75, "llm_judge_threshold": 0.6}'::jsonb),
    (gen_random_uuid(), 'REGRA_6', 'WF CONTRATO_NUM × EKPO Contrato básico',
     'Match entre contrato_num em WF e EKPO.contrato_basico',
     'high', 'sql_deterministic',
     'app.core.services.payments.rules.regra_6_wf_x_ekpo',
     '{}'::jsonb),
    (gen_random_uuid(), 'REGRA_LPU', 'Preço aplicado ↔ LPU',
     'ESLL.preco_bruto deve bater com LPUItem.preco_unitario do contract_version vigente',
     'high', 'math_tolerance',
     'app.core.services.payments.rules.regra_lpu_preco',
     '{"tolerance_pct": 1.0}'::jsonb)
ON CONFLICT (code) DO NOTHING;
```

---

## 8. File System Layout

```
beholder/
├── app/
│   ├── core/
│   │   ├── domain/
│   │   │   └── payments/                       (NOVO)
│   │   │       ├── __init__.py
│   │   │       ├── supplier_bridge.py          # dataclass SupplierBridge
│   │   │       ├── contract.py                 # ContractMaster, ContractVersion, LPUItem, ContractClause
│   │   │       ├── purchase_order.py           # PurchaseOrderHeader, PurchaseOrderItem, ServicePackage
│   │   │       ├── rule.py                     # RuleDefinition
│   │   │       ├── reconciliation.py           # ReconciliationRun, ReconciliationFinding
│   │   │       └── extraction.py               # ExtractionJob
│   │   ├── ports/
│   │   │   ├── storage.py                      (NOVO) DocumentStore
│   │   │   ├── queue.py                        (NOVO) JobQueue
│   │   │   └── payments_repositories.py        (NOVO) abstract repos
│   │   └── services/
│   │       └── payments/                       (NOVO)
│   │           ├── __init__.py
│   │           ├── ingestion_service.py        # XLSX → tabelas
│   │           ├── schema_projector.py
│   │           ├── extraction_service.py       # PDF → ExtractionJob
│   │           ├── reconciliation_engine.py
│   │           ├── kpi_service.py              # lê matview
│   │           └── rules/
│   │               ├── __init__.py             # REGISTRY
│   │               ├── regra_1.py
│   │               ├── regra_2.py
│   │               ├── regra_3.py
│   │               ├── regra_4.py
│   │               ├── regra_5.py              # fuzzy + embedding + llm-judge
│   │               ├── regra_6.py
│   │               └── regra_lpu.py            # math_tolerance
│   ├── adapters/
│   │   ├── db/
│   │   │   ├── schema_payments.sql             (NOVO) DDL do schema payments
│   │   │   ├── seed_payments.sql               (NOVO) rules seed
│   │   │   └── repositories/payments/          (NOVO)
│   │   │       ├── supplier_bridge_repo.py
│   │   │       ├── contract_repo.py
│   │   │       ├── purchase_order_repo.py
│   │   │       ├── rule_repo.py
│   │   │       ├── reconciliation_repo.py
│   │   │       ├── extraction_repo.py
│   │   │       └── kpi_repo.py
│   │   ├── storage/                            (NOVO)
│   │   │   ├── __init__.py
│   │   │   ├── filesystem_store.py
│   │   │   └── s3_minio_store.py
│   │   ├── queue/                              (NOVO)
│   │   │   ├── __init__.py
│   │   │   └── dramatiq_adapter.py
│   │   └── sap/                                (NOVO)
│   │       ├── __init__.py
│   │       └── projections/
│   │           ├── ekko.yaml
│   │           ├── ekpo.yaml
│   │           ├── esll_lpu.yaml
│   │           ├── esll_pacotes.yaml
│   │           └── contratos_empreteiras.yaml
│   ├── workers/                                (NOVO)
│   │   ├── __init__.py
│   │   ├── ingestion_actor.py
│   │   ├── extraction_actor.py
│   │   └── reconciliation_actor.py
│   ├── api/
│   │   ├── routers/
│   │   │   ├── payments_router.py              (NOVO) /api/payments/*
│   │   │   └── pages.py                        (MODIFICAR) adicionar /payments/empreiteiras-wf/*
│   │   └── schemas/
│   │       └── payments.py                     (NOVO) Pydantic schemas
│   ├── templates/
│   │   ├── partials/
│   │   │   ├── nav_left.html                   (MODIFICAR) novo grupo
│   │   │   ├── kpi_card.html                   (NOVO)
│   │   │   ├── finding_row.html                (NOVO)
│   │   │   ├── finding_detail_split.html       (NOVO)
│   │   │   ├── donut_chart.html                (NOVO)
│   │   │   ├── bar_chart.html                  (NOVO)
│   │   │   └── horizontal_bar.html             (NOVO)
│   │   └── payments/empreiteiras_wf/           (NOVO)
│   │       ├── visao_geral.html
│   │       ├── alertas/
│   │       │   ├── inbox.html
│   │       │   └── detail.html
│   │       ├── contratos/
│   │       │   ├── lista.html
│   │       │   ├── upload.html
│   │       │   ├── revisao_extracao.html
│   │       │   └── detalhe.html
│   │       ├── ingestao/
│   │       │   └── sap.html
│   │       └── regras/
│   │           └── lista.html
│   ├── skills/                                 (acrescentar — todos NOVOS)
│   │   ├── extracao_folha_de_rosto.md
│   │   ├── extracao_lpu_anexo.md
│   │   ├── extracao_clausulas.md
│   │   ├── reconciliacao_escopo_semantico.md
│   │   └── reconciliacao_lpu_matematica.md
│   └── config.py                               (MODIFICAR) campos Redis/Storage/Pool payments
├── docker-compose.yml                          (MODIFICAR) +redis, +worker
├── docker-compose.dev.yml                      (MODIFICAR) idem
├── Dockerfile.worker                           (NOVO)
└── tests/
    └── payments/                               (NOVO)
        ├── conftest.py
        ├── fixtures/                           # XLSX e PDFs sintéticos
        ├── test_schema_projector.py
        ├── test_ingestion_service.py
        ├── test_extraction_service.py
        ├── test_rules/
        │   ├── test_regra_1_cnpj.py
        │   ├── test_regra_2_validade.py
        │   ├── test_regra_5_escopo.py
        │   ├── test_regra_6_wf_x_ekpo.py
        │   └── test_regra_lpu.py
        └── test_reconciliation_engine.py
```

---

## 9. Rules Catalog

Para cada regra: input, lógica, threshold, output (FindingDraft).

### REGRA 1 — CNPJ match base ↔ PDF

- **Engine**: `sql_deterministic`
- **Severity**: high
- **Input**: `payments.supplier_bridge`, `payments.contract_master`
- **Lógica**:
  ```sql
  SELECT cm.id, cm.cnpj AS pdf_cnpj, sb.cnpj AS base_cnpj
  FROM payments.contract_master cm
  JOIN payments.supplier_bridge sb ON sb.id = cm.supplier_bridge_id
  WHERE cm.cnpj <> sb.cnpj AND cm.is_monitored
  ```
- **Threshold**: nenhum (match exato)
- **Output**: `expected_value={"cnpj": base}, actual_value={"cnpj": pdf}`

### REGRA 2 — Validade + ValFix

- **Engine**: `sql_deterministic`
- **Severity**: high
- **Input**: `payments.contract_version`, `payments.purchase_order_header`
- **Lógica**: para cada pagamento (EKKO header), checar se há `contract_version` vigente em `data_documento` com `inicio_validade ≤ data ≤ fim_validade` e `val_fix_cab` matching (se preenchido).
- **Threshold**: `date_tolerance_days` (default 0)
- **Output**: `expected_value={"valid_from": ..., "valid_to": ..., "val_fix_cab": ...}, actual_value=EKKO data`

### REGRA 3 — Outros campos base ↔ PDF

- **Engine**: `sql_deterministic`
- **Severity**: medium
- **Input**: campos auxiliares de `supplier_bridge` (categoria, empreiteira, etc.) vs metadata do `contract_version`
- **Lógica**: simples join, sinaliza divergência
- **Output**: ad-hoc

### REGRA 4 — Variáveis extraídas (cobertura)

- **Engine**: `sql_deterministic`
- **Severity**: medium
- **Input**: `contract_version` — campos `objeto_contrato`, `tecnologia`, `atividade`, `uf`, `cidade`, `val_fix_cab`
- **Lógica**: alerta se `current_version` tem >2 campos NULL (extração incompleta)
- **Output**: lista de campos faltantes

### REGRA 5 — Escopo: OBJETO/TECNOLOGIA/ATIVIDADE/UF/CIDADE

- **Engine**: `embedding` (híbrida)
- **Severity**: medium
- **Input**: `purchase_order_item.texto_breve`, `contract_version.objeto_contrato/tecnologia/atividade/uf/cidade`
- **Lógica**:
  1. **UF/Cidade**: normalizar e match exato. Mismatch → finding direto (alta confiança).
  2. **OBJETO/TECNOLOGIA/ATIVIDADE**: cascata híbrida (decidido na conversa):
     - **Etapa 1 — fuzzy (RapidFuzz)**: `partial_ratio(texto_breve_normalizado, objeto_normalizado)` — se ≥85, considera match. Se 50-85, vai etapa 2.
     - **Etapa 2 — embedding (pgvector)**: cosine similarity entre embedding(texto_breve) e embedding(objeto). Se ≥0.75, match. Se 0.5-0.75, etapa 3.
     - **Etapa 3 — LLM-judge (ClaroHub)**: pergunta direto "este item de pagamento ('TEXTO BREVE') está dentro do escopo de objeto/tecnologia/atividade do contrato ('OBJETO')? Responda SIM/NÃO + 1 linha justificando." Score 0..1.
  3. Decisão final: usa o método com **maior score** (parametrizável via `RuleDefinition.threshold_params`).
- **Threshold**: `fuzzy_threshold` (0.85), `embedding_threshold` (0.75), `llm_judge_threshold` (0.6) — todos parametrizáveis
- **Output**: `expected_value={"objeto": ..., "scope_method": "fuzzy"|"embedding"|"llm_judge", "score": 0.42}, actual_value={"texto_breve": ...}`
- **Cost ledger**: cada LLM-judge call registra em `finops_ledger` com `domain='payments', agent='regra_5_judge'`

### REGRA 6 — WF CONTRATO_NUM × EKPO contrato_basico

- **Engine**: `sql_deterministic`
- **Severity**: high
- **Input**: `purchase_order_header.contrato_basico`, `supplier_bridge.contrato_num_sap`, `purchase_order_item.documento_compras`
- **Lógica**: para cada `PurchaseOrderHeader` que é pedido (`categoria_doc <> 'K'`), o `contrato_basico` deve apontar para um documento `K` cujo `documento_compras` está em `supplier_bridge.contrato_num_sap`.
- **Output**: indica pedido órfão (sem guarda-chuva monitorado)

### REGRA LPU — Preço aplicado ↔ LPU

- **Engine**: `math_tolerance`
- **Severity**: high
- **Input**: `service_package` (ESLL), `lpu_item`, `contract_version` (vigente)
- **Lógica**:
  ```python
  for esll in service_packages:
      # encontra contract_version vigente na data do pagamento (via EKPO → EKKO → data_documento)
      cv = resolve_vigente(esll, on_date=...)
      # encontra LPUItem desse serviço naquela versão
      lpu = lpu_items.where(contract_version_id=cv.id, numero_servico=esll.numero_servico).first()
      if lpu is None:
          # finding: serviço não está na LPU
          yield FindingDraft(rule_code="REGRA_LPU", reason="servico_fora_da_lpu", ...)
          continue
      # math: ESLL.preco_bruto deve = LPUItem.preco_unitario com tolerância
      delta_pct = abs(esll.preco_bruto - lpu.preco_unitario) / lpu.preco_unitario * 100
      if delta_pct > params["tolerance_pct"]:
          yield FindingDraft(
              rule_code="REGRA_LPU",
              expected_value={"preco_unitario_lpu": float(lpu.preco_unitario)},
              actual_value={"preco_bruto_esll": float(esll.preco_bruto)},
              delta_pct=delta_pct,
              value_at_risk_brl=abs(esll.preco_bruto - lpu.preco_unitario) * esll.qtd_solicitada,
              evidence_clause_ids=[lpu.clausula_ref_id],
              evidence_pages=[lpu.pagina_pdf],
          )
  ```
- **Threshold**: `tolerance_pct` (default 1.0) — parametrizável
- **Output**: detalhado, com value_at_risk_brl calculado

---

## 10. Skills & Prompts Catalog

Cada skill é um `SKILL.md` em `app/skills/` (padrão Vértice herdado). Prompts versionados no `prompts` registry.

### 10.1. Skills

#### `extracao_folha_de_rosto.md`

Identidade: "Especialista em extração de folha de rosto de contrato jurídico de empreiteira (telecom Brasil)."

Inputs:
- `markdown_pdf` (string) — output de docling
- `tables_pdf` (json) — tabelas detectadas

Saída esperada: JSON conforme schema `FolhaDeRosto` (Pydantic). Cada campo com `value` e `confidence` (0..1).

Política de roteamento: Default ClaroHub `openai/gpt-oss-20b` (raciocínio); fallback `sabia-4`.

Guardrails:
- Entrada: max_chars=200000 (truncate antes do prompt)
- Saída: schema válido obrigatório; se LLM retornar fora do schema, retry 2x

Failsafe: confidence médio <0.6 → marca para HITL prioritário.

#### `extracao_lpu_anexo.md`

Identidade: "Especialista em extração de tabelas de preços (LPU) de anexos de contrato."

Inputs: `tables_pdf` (tabelas detectadas pelo docling).

Saída esperada: JSON list de `LPUItem`.

Política: ClaroHub default (tabelas → estruturação literal).

#### `extracao_clausulas.md`

Identidade: "Identificador de seções e cláusulas em contrato jurídico."

Inputs: `markdown_pdf`.

Saída esperada: JSON list de `{secao, clausula_numero, texto, pagina}` para gerar embeddings depois.

#### `reconciliacao_escopo_semantico.md`

Identidade: "Juiz semântico para validar escopo de pagamento × cláusula contratual."

Inputs: `texto_breve_ekpo`, `objeto_contrato`, `tecnologia`, `atividade`.

Saída esperada: `{match: bool, score: float, rationale: string}`.

Política: ClaroHub default (raciocínio), Maritaca fallback.

Guardrails: rationale max 200 chars; score em [0,1].

#### `reconciliacao_lpu_matematica.md`

Identidade: "(Não-LLM) Validador determinístico de preço × LPU."

Não usa LLM — documenta a regra matemática para auditoria de método.

### 10.2. Prompts versionados

Cada skill vira ≥1 entry em `prompts` table com `module_names=['empreiteiras_wf']`. Versionados via `version`, com bump quando o prompt muda.

---

## 11. Test Strategy

### 11.1. Pirâmide

```
                    ┌─────────────┐
                    │   E2E (k6)  │   ← Fase 7
                    └─────────────┘
              ┌──────────────────────┐
              │ Integration (pytest) │  ← cobre rotas, db, worker
              └──────────────────────┘
       ┌─────────────────────────────────┐
       │ Unit (pytest)                   │  ← rules engine, projector, schemas
       └─────────────────────────────────┘
```

### 11.2. Unit tests

| Módulo | Cobertura mínima |
|---|---|
| `app/core/services/payments/rules/*` | 90% — cada regra com ≥5 fixtures (positivos + negativos + edge cases como NULL) |
| `app/core/services/payments/schema_projector.py` | 90% — coerção de tipos, missing columns, validação |
| `app/core/services/payments/reconciliation_engine.py` | 80% — registry, ordering, error handling |
| `app/core/services/payments/extraction_service.py` | 70% — mocks de LLM (Instructor), pipeline completo |
| `app/adapters/storage/*` | 80% — FS adapter integral, S3 mockado |

### 11.3. Integration tests

| Cenário | Setup |
|---|---|
| Upload XLSX → tabelas populadas + matview refreshed | XLSX sintético com 100 linhas |
| Upload PDF → ExtractionJob → review → approve → ContractMaster criado | PDF mockado + LLM mockado retornando JSON canônico |
| Trigger reconciliation → finding aparece no Inbox | seed completo, fixtures de divergência conhecida |
| Bulk action no Inbox (10 findings → status batch) | 10 findings seed, audit events verificados |
| RBAC: analista_n1 tenta bloquear → 403 | fixture de usuário com role específica |

### 11.4. E2E / Load (Fase 7)

Conforme cenários A/B/C já validados no plano (não repetir aqui).

### 11.5. Fixtures

- `tests/payments/fixtures/sintéticos/` — XLSX e PDFs gerados programaticamente para testes determinísticos
- `tests/payments/fixtures/reais/` — 5 PDFs reais fornecidos (gitignored, mas referenciados em `fixtures/reais/README.md` com instruções de download)
- 50 amostras anotadas manualmente para acceptance de REGRA 5 (CSV: texto_breve_ekpo, objeto_contrato, expected_match=bool)

---

## 12. Phase Plan (8 fases)

Cada fase tem entregáveis + acceptance + gate (só avança se passar).

| Fase | Duração | Entregáveis | Acceptance gate |
|---|---|---|---|
| **0 — Fundação de isolamento** | 1-2 sem | Schema `payments`, pool dedicado, Redis, worker dramatiq, port `DocumentStore` + 2 adapters, telemetria por domínio, materialized view stub | 10 uploads paralelos não degradam p95 dos endpoints existentes >10% (k6) |
| **1 — Modelo + Ingestão XLSX** | 2 sem | 12 entidades + migrations, 7 projetions YAML, dlt/Polars loader, repos | Carga dos 7 XLSX (~120k rows) em <60s; carga idempotente |
| **2 — Rules engine MVP** | 1-2 sem | `reconciliation_engine.py`, regras 1, 2, 6, LPU implementadas, tabela `reconciliation_finding` | 261 OS processadas em <30s; cada regra com ≥5 fixtures; cobertura ≥90% |
| **3 — Dashboard MVP** | 1 sem | Página `/payments/empreiteiras-wf` com 9 KPIs do mockup + 3 charts + tabela | Dashboard carrega <1s mesmo com 100k linhas; matview refresh <5s |
| **4 — Extração PDF + HITL** | 2-3 sem | `extraction_service.py` (docling + Instructor + pgvector), tela de revisão com confidence | 5 PDFs reais extraídos; ≥85% campos pós-HITL; cost ≤R$15/PDF |
| **5 — Reconciliação semântica (REGRA 5)** | 1-2 sem | RapidFuzz + pgvector + LLM-judge com score híbrido (escolha automática do método com maior score) | UF/Cidade exato = 100%; Objeto/Tec/Ativ: precisão ≥80%, recall ≥70% em 50 amostras anotadas |
| **6 — UX completa (Inbox + ações + bulk)** | 2 sem | Jornada J1 end-to-end com 3 perfis (RBAC), bulk actions, comentários, audit | Analista resolve 20 findings em <30min em teste de usabilidade |
| **7 — Validação de concorrência (gate final)** | 1 sem | Load tests k6: cenários A/B/C combinados | SLO p95 <500ms sob carga combinada em **todos** os domínios |

**Total: 11-15 semanas** para POC validada com dados reais.

### 12.1. Dependências entre fases

```
Fase 0 ──→ Fase 1 ──→ Fase 2 ──→ Fase 3
              │           │          │
              └──→ Fase 4 ──→ Fase 5 ──→ Fase 6 ──→ Fase 7
                                          ↑
                                   tudo converge aqui
```

Fase 4 (extração PDF) e Fase 2 (rules engine) podem rodar em paralelo APÓS Fase 1.

### 12.2. Stop conditions (replan se atingir)

- Custo LLM por PDF > R$30 → revisar prompts ou trocar para Sabiá-4
- p95 endpoint Radar/Raio-X degrada >20% após Fase 0 → split em app separada
- Extração HITL exige correção >30% dos campos → mudar técnica (LlamaParse pago, ou treino fine-tuned)
- Findings com falso positivo >40% → afrouxar thresholds + revisar regra (talvez REGRA 5 precisa de tuning maior)

---

## 13. Riscos & Tradeoffs

### 13.1. Matriz de risco

| Risco | Prob | Impacto | Mitigação |
|---|---|---|---|
| Custo LLM explode com extração em massa | Média | Alto (R$ mensal cresce 10x) | ClaroHub on-prem (R$0/call) como default; Maritaca só em fallback; budget Hard-Stop no `finops_budgets` |
| Falsos positivos REGRA 5 (semântica) | Alta | Médio (analista desconfia) | Fila "borderline" para revisão humana antes de criar finding; thresholds parametrizáveis |
| ClaroHub indisponível (proxy/rede Claro) | Média | Alto (extração para) | Failsafe service (existe) + circuit breaker; fallback para Maritaca; queue continua, retry exponencial |
| Performance materialized view sob 80k pagamentos/mês | Média | Alto (dashboard fica lento) | Refresh CONCURRENTLY + index covering; particionar `purchase_order_item` por mês se passar de 1M rows |
| Versionamento de contratos pega cenário não previsto | Baixa | Médio | Coverage temporal nos testes; query temporal explicitamente testada (G8) |
| Coexistência com outros domínios futuros vira gargalo | Baixa | Médio | Schema isolado + pool dedicado já reservam capacidade; review arquitetura a cada novo vertical |
| Schema SAP muda (colunas novas no EKKO/EKPO) | Baixa | Baixo (raw_extra absorve) | `raw_extra::jsonb` guarda tudo; SchemaProjector parametrizável; bump versão do YAML |

### 13.2. Tradeoffs aceitos (decisões já tomadas)

- **Determinístico para reconciliação, LLM só na extração** — perde flexibilidade em casos cinzentos, ganha reprodutibilidade auditável (decisão da conversa com user)
- **Híbrido textual+vectorDB** — não é só texto puro nem só vectorDB; combina campos tipados em SQL com clauses indexadas para rastreabilidade (decisão da conversa)
- **Modular monolith inside Beholder, não microservices** — pré-escala-asymétrica não justifica overhead operacional de microservices ainda
- **Schema PG isolado, não DB separado** — coexistência de pool/processo é suficiente, full separation é prematura
- **Worker em dramatiq (não Celery)** — menos features, mas zero ops overhead vs Celery; suficiente para volume previsto

### 13.3. Decisões pendentes (precisam de input do user no início de cada fase)

| Decisão | Quando | Onde |
|---|---|---|
| Thresholds finais de cada regra | Antes de Fase 2 | UI `/regras` ou config inicial |
| Modelo LLM padrão para extração (ClaroHub vs Maritaca) | Antes de Fase 4 | comparar 5 PDFs em cada, medir custo/qualidade |
| Mecanismo de notificação (email, Slack, webhook) | Antes de Fase 6 | depende de integração externa |
| Mobile/responsive scope | Após Fase 7 | depende de adoção real |

---

## 14. Anexos

### 14.1. Glossário SAP

| Termo | Significado |
|---|---|
| EKKO | tabela SAP "Purchasing Document Header" — cabeça do pedido/contrato |
| EKPO | "Purchasing Document Item" — item do pedido/contrato (1:N com EKKO) |
| ESLL | "Service Line" — linha de serviço de uma EKPO (1:N com EKPO via package) |
| LPU | "Lista de Preços Unitários" — anexo do contrato com preço por serviço |
| WF | Workflow — sistema operacional onde OS é aberta |
| OS | Ordem de Serviço |
| Guarda-chuva | contrato master que cobre múltiplos pedidos derivados |
| ValFix.(cab) | valor fixo no nível do cabeçalho do contrato |
| CONTRATO_NUM | identificador do contrato no SAP (ex.: 5700017041) |
| REF WS | referência do contrato no Workflow (ex.: CW149898) |

### 14.2. XLSX schemas confirmados (mapeamento sumário)

| Arquivo | Rows × Cols | Para tabela |
|---|---|---|
| Contratos - Empreteiras.xlsx | 147 × 6 | `supplier_bridge` |
| EKKO - EXTRAÇÃO CONTRATOS GUARDA CHUVAS.xlsx | 138 × 179 | `purchase_order_header` (filter categoria_doc='K') |
| EKKO - SAP (Extração pedidos).MHTML.xlsx | 1.894 × 179 | `purchase_order_header` (filter pedidos) |
| EKPO - EXTRAÇÃO CONTRATOS GUARDA CHUVAS.xlsx | 44.782 × 283 | `purchase_order_item` (guarda-chuva) |
| EKPO - SAP (Extração pedidos).MHTML.xlsx | 25.067 × 283 | `purchase_order_item` (pedidos) |
| ESLL - EXTRAÇÃO Nº DE PACOTES - LPU_VALORES.xlsx | 44.782 × 10 | `service_package` (com preços) |
| ESLL - EXTRAÇÃO EKPO_ESLL Nº DE PACOTES.xlsx | 44.782 × 3 | enriquecimento (join EKPO ↔ ESLL) |

### 14.3. Referências externas

- [docling (IBM)](https://github.com/DS4SD/docling) — PDF extraction
- [Instructor](https://github.com/jxnl/instructor) — structured LLM outputs
- [pgvector](https://github.com/pgvector/pgvector) — vector search no Postgres
- [dramatiq](https://github.com/Bogdanp/dramatiq) — task queue
- [Polars](https://github.com/pola-rs/polars) — DataFrames
- [RapidFuzz](https://github.com/maxbachmann/RapidFuzz) — fuzzy string matching
- [DeepEval](https://github.com/confident-ai/deepeval) — LLM evals (G2, G4)
- [k6](https://github.com/grafana/k6) — load testing (G7)

### 14.4. Comandos para começar Fase 0

```bash
# 1. Cria estrutura de diretórios
mkdir -p app/core/{domain,services,ports}/payments
mkdir -p app/adapters/{storage,queue,sap/projections}
mkdir -p app/adapters/db/repositories/payments
mkdir -p app/workers
mkdir -p app/templates/payments/empreiteiras_wf/{alertas,contratos,ingestao,regras}
mkdir -p tests/payments/{fixtures,test_rules}

# 2. Adiciona deps
echo "dramatiq[redis]>=1.16.0" >> requirements.txt
echo "polars>=0.20.0" >> requirements.txt
echo "rapidfuzz>=3.5.0" >> requirements.txt
echo "instructor>=1.0.0" >> requirements.txt
echo "docling>=1.0.0" >> requirements.txt
echo "boto3>=1.34.0" >> requirements.txt
echo "pgvector>=0.2.5" >> requirements.txt

# 3. Cria branch e começa
git checkout -b feature/fase-0-infra
```

---

**Fim do SDD v1.0.** Atualizações a este documento devem bump a versão e ser registradas no histórico do git.
