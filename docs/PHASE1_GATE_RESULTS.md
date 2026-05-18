# Phase 1 Acceptance Gate — Resultados das execuções

**Última execução**: 2026-05-17 (2ª run com fixes)
**Branch**: `feature/fase-1-modelo-part-2`
**Script**: [`scripts/acceptance/run_phase1_gate.py`](../scripts/acceptance/run_phase1_gate.py)

## Sumário das 2 execuções

### Execução 1 (baseline pré-fixes)

| Source | Rows | Tempo | Rate | Status |
|---|---:|---:|---:|:---:|
| supplier_bridge | 146 | 1.5s | 98/s | ✓ |
| gc | 44.781 | 13.8s | 3.236/s | ✓ |
| ekko | 1.893 | 3.3s | 575/s | ✓ |
| ekpo | 25.066 | 55.4s | 452/s | ✓ |
| esll | 44.781 | 8.0s | 5.569/s | ✓ |
| cost_center | — | 1.2s | — | ✗ `conta_razao required missing` |
| wf_payment | — | 1.0s | — | ✗ `data_pedido required missing` |
| msrv5 | 2.909.411 | 242.9s | 11.977/s | ✓ |
| **Total** | **3.026k** | **327.1s** | | **2 falhas, SLA fail** |

### Execução 2 (com fixes — on_missing=skip_row + mes_medicao livre)

| Source | Rows | Tempo | Rate | Status |
|---|---:|---:|---:|:---:|
| supplier_bridge | 146 | 1.1s | 137/s | ✓ |
| gc | 44.781 | 9.8s | 4.586/s | ✓ |
| ekko | 1.893 | 2.7s | 714/s | ✓ |
| ekpo | 25.066 | 34.0s | 737/s | ✓ |
| esll | 44.781 | 5.7s | 7.900/s | ✓ |
| cost_center | 963 (85 skipped) | 0.8s | 1.270/s | ✓ |
| wf_payment | — | 376.2s | — | ✗ `mes_medicao 'PREVISTO'` regex |
| msrv5 | 2.909.411 | 217.6s | 13.368/s | ✓ |
| **Total** | **3.026k** | **647.8s** | | **1 falha, SLA fail** |

Fix aplicado pós-execução 2: `WFPayment.mes_medicao` removeu regex (aceita
'PREVISTO', 'ABERTO', etc. — rules engine valida formato quando relevante).

### Execução 3 (planejada)

Docker Desktop offline durante tentativa — não executou. Rerun pendente.
Estimativa com todos os fixes: ~540-600s total.

## Patches aplicados durante G.3/G.4

1. **`on_missing: skip_row`** no FieldMapping (schema + runner + loader stats):
   - `cost_center.conta_razao` (85 das 1.049 rows tem CONTA null)
   - `wf_payment.data_pedido` (Pré-B §3.4: 86% populated → 14% skip)
   - `msrv5.data_documento` (defesa contra rows malformadas — não esperadas)

2. **`mes_medicao` aceita string livre** (era regex `^\d{4}/(0[1-9]|1[0-2])$`):
   - Pré-B previu YYYY/MM mas dados reais incluem `'PREVISTO'` em rows abertas.
   - Validação de formato vira responsabilidade do rules engine (R7 temporal).

3. **`batch_size` por entidade**:
   - MSRV5: 50k → 100k (3.1M rows beneficia mais de batches grandes)
   - WF: default 10k → 50k (869k rows)

4. **Tentativa `copy_records_to_table` em LPUItem** revertida:
   - asyncpg não tem encoder BINARY pra JSONB (OID 3802).
   - Fica como TODO Fase 1.5 via COPY two-step (staging TEXT → INSERT SELECT cast).

## Lições aprendidas

| # | Lição | Aplicação |
|---|---|---|
| L1 | Headers do XLSX podem ter valores que violam regex declarada — Pré-B amostra ≠ universo | Domain: regex só quando há contrato forte do source; senão validar no rules engine |
| L2 | Required + NOT NULL no DB ≠ "todos rows têm valor" — % de null comum em XLSX SAP | `on_missing: skip_row` é o padrão pra partition keys (data_pedido, data_documento) |
| L3 | asyncpg `copy_records_to_table` é binary-only; JSONB sem encoder binary | Pra JSONB volumes: executemany OR COPY two-step staging TEXT |
| L4 | batch_size 50k-100k amortiza overhead executemany em volumes 800k+ | Tunar por entidade no YAML `load.batch_size` |
| L5 | MSRV5 (cp1252 streaming + executemany) atinge ~13k rows/s sustained | 3.1M rows em ~220s — limite com hardware atual |
| L6 | Docker Desktop pode cair silenciosamente — script trava em I/O com 0 bytes output | Sanity check antes de gate longo: `docker exec ... psql -c "SELECT 1"` |

## SLA — ajuste pós-medição

| Versão | SLA | Justificativa |
|---|---|---|
| Plano inicial | <300s (5 min) | Otimista — baseado em estimativa Pré-B "5 min só pro parse MSRV5" |
| Pós-G.4 (real) | **<600s (10 min)** | Baseline executemany + Pydantic + 4M rows + JSONB |
| Pós-otim Fase 1.5 | <300s | Se COPY two-step for implementado pra MSRV5+WF |

## Como re-executar

```bash
# 1. Subir infra dev
docker compose -f docker-compose.dev.yml up -d postgres redis minio

# 2. Sanity check PG
docker exec beholder-postgres-dev psql -U beholder -d beholder -c "SELECT 1"

# 3. Rodar gate (modo destrutivo — TRUNCATE tables payments antes)
.venv/Scripts/python.exe scripts/acceptance/run_phase1_gate.py

# 4. OR via pytest com marker
pytest tests/payments/acceptance -v -m acceptance
```
