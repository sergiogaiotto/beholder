# PRE_B_FINDINGS — Sondagem profunda dos dados reais

**Gerado**: 2026-05-17
**Escopo**: Pré-B do plano Empreiteiras-WF — sondagem profunda dos 3 alvos críticos antes de comprometer a Fase 1.
**Status**: ✅ completo (3 de 3 sondagens executadas)

---

## Sumário executivo

| Alvo | Resultado | Impacto no SDD v1.1 |
|---|---|---|
| **Casos Selecionados (339 × 12)** | É **pivot table do Excel**, não ground truth de casos | ❌ D4 inválida — remover do mapping §14.2; não vai pra `tests/fixtures/` |
| **MSRV5 LPU (3,1M linhas)** | Limpo, cp1252, 2.909.414 data rows, 7 cols, 0 malformed | ✅ Parser direto na Fase 1; particionamento por ano confirmado |
| **Analítico WF (869k × 81)** | Ver §3 abaixo | (resultado pós-sample) |

---

## 1. Casos Selecionados — **NÃO é ground truth**

**Achado:** a sheet `Casos Selecionados` do `Analitico_Empreiteiras_WF1_WF2_TOTAL_2025 2.txt.xlsx` é uma **pivot table do Excel** (cross-tabulation com 4 vistas lado a lado), não uma lista de casos selecionados para análise pela Controladoria.

**Estrutura observada:**

| Cols | Vista | Dimensão | Métrica |
|---|---|---|---|
| 0-1 | View A | Empreiteira | Soma de VALOR_TOTAL |
| 2-3 | (vazio) | — | — |
| 4-5 | View B | Ano + meses (jan/fev/mar/...) | Soma de VALOR_TOTAL |
| 6-6 | (vazio) | — | — |
| 7-8 | View C | Código de sistema (ex: 87SPSP4R32, 10SPSP4R32) | Soma de VALOR_TOTAL |
| 9-9 | (vazio) | — | — |
| 10-11 | View D | Código contábil (ex: 5132322020, 5132424290) | Soma de VALOR_TOTAL |

**Headers literais lidos:** `["STATUS_ITEM", "PED EMITIDO", "", "", "STATUS_ITEM", "PED EMITIDO", "", "STATUS_ITEM", "PED EMITIDO", "", "STATUS_ITEM", "PED EMITIDO"]` — repetição é artefato Excel.

**Linhas pós-cabeçalho** começam com `["Rótulos de Linha", "Soma de VALOR_TOTAL", ..., "Rótulos de Linha", "Soma de VALOR_TOTAL", ...]` e seguem com agregados por categoria (ex: `"EQS ENGENHARIA", "155541839.63"` na coluna 0-1).

**Conclusão:** este sheet **não tem casos individuais selecionados manualmente pela Controladoria**. É um dashboard agregado para validação visual dos números totais (top empreiteiras, evolução mensal, decomposição por código de sistema/contábil).

**Implicação para SDD v1.1:**
- Reverter D4: linha "Casos Selecionados → tests/fixtures/" no §14.2 deve sair
- Sheet é **subproduto da matview KPI** (Fase 3) — gera-se automaticamente após carregar `wf_payment`
- **Não há ground truth nos dados** — fixtures de teste vão ter que ser anotadas manualmente (Fase 2)

---

## 2. MSRV5 LPU — Parser direto, dados limpos

**Arquivo:** `MSRV5 - EXTRAÇÃO LPU.txt` (352 MB, 3.103.381 linhas totais)
**Fonte canônica:** `docs/probe_msrv5.json`

### 2.1 Estrutura confirmada

| Aspecto | Valor |
|---|---|
| Encoding | **cp1252** (Latin-1 estendido) |
| Total linhas | 3.103.381 |
| Data rows válidas | **2.909.414** (bate com cabeçalho SAP "2.909.412 transferidos" + 2 metadata) |
| Separadores (`---`) | 145.476 (4,7%) |
| Header repetidos (page breaks SAP) | 48.491 |
| Linhas malformadas | **0** (✅) |
| Colunas data | 7 (consistente em todas) |

### 2.2 Schema fixo (7 cols)

```
| Data doc.  | N° de docu | Item | Serviço | Qtd.   | Preço bruto | Texto breve |
| 06.09.2022 | 5700012782 | 7913 | 9000507 | 0,000  |  2,71       | SERV CONFECCAO MATERIAL GRAFICO |
```

- `Data doc.`: formato dd.mm.yyyy
- `N° de docu`: número SAP (corresponde a `documento_compras` no EKKO/EKPO)
- `Item`: número sequencial dentro do documento
- `Serviço`: código do serviço (corresponde a `numero_servico`)
- `Qtd.`: **sempre 0,000** nas amostras — LPU é tabela de preços, não pedidos (qtd vem com OS)
- `Preço bruto`: decimal com vírgula (R$, formato pt-BR)
- `Texto breve`: descrição do serviço (padding com espaços, trim necessário)

### 2.3 Distribuição temporal (impacto no particionamento)

| Ano | Linhas |
|---:|---:|
| 2018 | 296.327 |
| 2019 | 338.780 |
| 2020 | 361.568 |
| 2021 | 384.361 |
| **2022** | **560.010** ← pico |
| 2023 | 278.857 |
| 2024 | 345.788 |
| 2025 | 343.174 |
| 2026 | 547 (só janeiro até a data do export) |
| **Total** | **2.909.412** ✅ |

**Confirmação do particionamento**: partições anuais em `payments.lpu_item` (SDD §7.2 v1.1) estão dimensionadas corretamente — 2022 é a maior partição com ~560k linhas, ainda muito tratável (sub-100 MB com índices).

### 2.4 Receita do parser de produção (para Fase 1)

```python
# Pseudo-código já calibrado pelo probe
def parse_msrv5(path: Path) -> Iterable[LpuRow]:
    with path.open('r', encoding='cp1252', errors='replace') as f:
        for line in f:
            stripped = line.strip()
            if not stripped or SEPARATOR_RE.match(stripped):
                continue  # 4.7% de skip — esperado
            if not (stripped.startswith('|') and stripped.endswith('|')):
                continue
            cols = [c.strip() for c in stripped[1:-1].split('|')]
            if len(cols) != 7:
                continue  # observamos 0 dessas mas defensivo
            if cols[0] == 'Data doc.' or 'Estat' in cols[0]:
                continue  # 1.6% de header/metadata
            yield LpuRow(
                data_documento=parse_date_br(cols[0]),     # dd.mm.yyyy
                documento_compras=cols[1],
                item=int(cols[2]),
                numero_servico=cols[3],
                qtd_solicitada=parse_decimal_br(cols[4]),  # vírgula
                preco_unitario=parse_decimal_br(cols[5]),
                texto_breve=cols[6],
                source='msrv5',
            )
```

**Performance estimada**: cp1252 streaming + parse em ~5 min para 3,1M linhas em hardware comum. Compatível com Fase 1 acceptance (<5 min carga total).

---

## 3. Analítico WF — Schema completo + achados disruptivos

**Arquivo:** `Analitico_Empreiteiras_WF1_WF2_TOTAL_2025 2.txt.xlsx` / sheet `Analitico_Empreiteiras_WF1_WF2_`
**Volume confirmado:** 869.663 linhas × 81 colunas (todos os headers lidos, varredura completa)
**Fonte canônica:** `docs/probe_analitico_wf.json`

### 3.1 ACHADO DISRUPTIVO — OBJETO_DO_CONTRATO é taxonomia, não texto livre

Esperado pelo SDD v1.1: `OBJETO_DO_CONTRATO` é texto livre extraído do PDF → REGRA 5.f precisa cascata `fuzzy → embedding → LLM-judge`.

**Realidade:** `OBJETO_DO_CONTRATO` tem **598 valores únicos** em 869.662 linhas populated (99% de cobertura). É uma **taxonomia controlada** (ex.: "INSTALAÇÃO EMPRESARIAL", "MANUTENÇÃO PREVENTIVA", etc.).

**Implicação:** R5.f vira **SQL puro + fuzzy** (igual a 5.c/5.d/5.e). **Toda a REGRA 5 fica determinística — ZERO chamadas LLM-judge.** Economia massiva de custo + latência.

### 3.2 Distribuição de cardinalidade das 81 colunas

Análise sobre 869.662 linhas escaneadas (1 não-data row no header):

| Faixa | Colunas | Exemplos |
|---:|---|---|
| **0% populated** (4 cols vestigiais) | DEGRAU, ID_CONTRATO_ITEM, STATUS_DEGRAU, ITEM_PARA | herança de schema antigo — vão pra `raw_extra::jsonb`, sem indexar |
| 0-10% populated (8 cols raras) | ACAO (9%), DATA_FIM_EXECUCAO (9%), DATA_CANCELAMENTO (1%), PAGAMENTO (1%), REQ_NUM/ITEM/DATA (0,0005%), ATIVIDADE_INFRA/TIPO/SEGMENTO (4%) | filtros/contexto |
| 35-90% (cols opcionais) | CLIENTE (35%), PEP (53%), UNIDADE_DE_NEGOCIO (62%), SERVICO (65%), ID_SAP (72%), PEDIDO_NUM (86%), DATA_EXECUCAO (88%), DATA_APROVACAO_HORA (95%), STATUS_ITEM (97%), TIPO_SER_MAT (74%) | populadas conforme estágio da OS |
| **>95% (cols core)** | ~50 cols incluindo SISTEMA, OS, CONTRATO_NUM, ITEM_NUM, ITEM_DESCRICAO, UF, CIDADE, EMPREITEIRA, VALOR_TOTAL, OBJETO_DO_CONTRATO (99%) | base das regras |

### 3.3 Taxonomias confirmadas (subset categórico)

| Coluna | Cardinalidade | Valores |
|---|---:|---|
| `SISTEMA` | **2** | `WF1`, `WF2` |
| `MALOGRO` | 3 | `BACKLOG`, `ERROR`, `NAO` |
| `TIPO_DE_LPU` | **3** | `FIXO MENSAL`, `LPU MEDIÇÃO`, `LPU REFERENCIAL` |
| `TIPO_DE_DESPESA` | 2 | `CAPEX`, `OPEX` |
| `ACAO` | 4 | `ALTERAR`, `ATIVAR`, `DESATIVAR`, `MIGRAR` |
| `STATUS_OS` | 5 | `CANCELADO`, `DEVOLVIDO`, `EM EXECUÇÃO`, `ERRO - AVALIAR OS`, `EXECUTADO` |
| `STATUS_ITEM` | 5 | `APROVADO`, `CADASTRADO`, `EXPIRADO`, `PED EMITIDO`, `VALIDADO` |
| `NIVEL_GERENCIAL` | 5 | `ERRO - AVALIAR OS`, `Em Correção`, `Em Pagamento`, `Medido`, `Orçado` |
| `SITUACAO` | 2 | `MEDIÇÃO AGRUPAMENTO EMITIDA`, `MEDIÇÃO AGRUPAMENTO PENDENTE` |
| `REGIONAL_SOE_NOVA` | 6 | CONO, MG, NE, RJ/ES, SP, SUL |
| `CATEGORIA` | 11 | ATIVAÇÃO, CONSTRUÇÃO, CONTRATAÇÃO 55, DESATIVAÇÃO, FIXO MENSAL, PLANTA EXTERNA, PLANTA INTERNA, PREPARAÇÃO, RECUPERAÇÃO CLIENTE, RECUPERAÇÃO REDE, RECUPERAÇÃO SITE |
| `UF` | 27 | todos os estados BR (válidos) |
| `TECNOLOGIA` | 35 | 3G, 3G/4G, ACESSO TERCEIRO, GPON, HFC, FIBRA ÓPTICA, ... |
| `ATIVIDADE` | 56 | ATIVAÇÃO DE DADOS, MANUTENÇÃO PREVENTIVA, ... |
| `FASE_ATUAL` | 34 | ABERTO, AGENDADO, EM EXECUÇÃO, CANCELADO, ... |
| `OBJETO_DO_CONTRATO` | **598** | INSTALAÇÃO EMPRESARIAL, ... |
| `MATERIAL_SERVICO_NUM` | **912** | códigos serviço (9000507, 9011710, ...) — **link direto pra `lpu_item.numero_servico`** |
| `EMPREITEIRA` | **210** | (vs 147 no `supplier_bridge.xlsx` — 63 não monitoradas) |
| `CONTA_RAZAO` | 25 | códigos contábeis (5132...) |

### 3.4 Outras chaves de negócio importantes

| Coluna | Tipo | Notas |
|---|---|---|
| `OS` | string | chave de negócio principal (≥1000 únicos) |
| `CONTRATO_NUM` | string | join com `EKKO.documento_compras` (R6.3) — 100% populated |
| `PEDIDO_NUM` | string | join com `EKKO.documento_compras` (R6.1) — 86% (só após emissão pedido SAP) |
| `ITEM_NUM` | int | item dentro do pedido — 100% |
| `ITEM_DESCRICAO` | string | descrição livre (≥1000 únicas) — usada na R6.8 vs GC |
| `MATERIAL_SERVICO_NUM` | string | **912 únicos** — chave pra LPU (cruzamento R LPU) |
| `VALOR_TOTAL`, `VALOR_TOTAL_FINAL` | decimal | distinção: VALOR_TOTAL = orçado; VALOR_TOTAL_FINAL = pago (após DE-PARA) |
| `VALOR_UNITARIO`, `VALOR_UNITARIO_PARA` | decimal | idem — necessárias pra R6.9 (`valor_unitario` × GC.preco_bruto_lpu) |

### 3.5 Datas (várias — escolher a certa pra cada regra)

| Coluna | Populated | Uso na regra |
|---|---:|---|
| `DATA_CADASTRO` | 100% | quando OS foi criada |
| `DATA_EXECUCAO` | 88% | quando serviço foi executado (R7 lag) |
| `DATA_FIM_EXECUCAO` | 9% | execução final (raro — usar `DATA_EXECUCAO`) |
| `DATA_INCLUSAO` | 100% | quando OS entrou no WF |
| `DATA_APROVACAO_HORA` | 95% | aprovação manual (HITL) |
| `DATA_PEDIDO` | 86% | pedido SAP emitido — **chave pra R6.2** |
| `DATA_CANCELAMENTO` | 1% | OS canceladas |
| `MES_MEDICAO`, `PERIODO` | 100% | categóricas (janelas mensais) |

**Decisão sugerida:** **`DATA_PEDIDO`** vira partição-key de `wf_payment` (já no SDD v1.1) — adequa.

### 3.6 Filtragem sugerida para o universo "candidato a divergência"

Não rodar regras sobre OS canceladas/erradas — gera ruído. Filtro padrão:

```sql
WHERE wf.status_os IN ('EXECUTADO', 'EM EXECUÇÃO')  -- exclui CANCELADO, ERRO, DEVOLVIDO
  AND wf.nivel_gerencial IN ('Em Pagamento', 'Medido')  -- exclui Orçado, ERRO
  AND wf.malogro <> 'ERROR'  -- exclui malograms
```

Quantidades aproximadas no universo filtrado (a confirmar):
- Sem filtro: 869k linhas
- Com filtro acima: ~600-700k (estimativa baseada em STATUS_OS distribution não medida ainda)

### 3.7 Mismatch supplier_bridge × WF empreiteiras

| Fonte | Empreiteiras únicas |
|---|---:|
| `Contratos - Empreteiras.xlsx` (supplier_bridge) | **147** |
| Analítico WF 2025 (col EMPREITEIRA) | **210** |
| Diferença | **63** empreiteiras existem em WF mas NÃO estão na base monitorada |

**Implicação:** queries de reconciliação devem ser **LEFT JOIN** `wf_payment ← supplier_bridge`, com flag `is_monitored_supplier` no resultado. Findings só são criados para `is_monitored_supplier = TRUE` (escopo POC), mas analytics da R7 podem incluir todos.

---

## 4. Decisões a aplicar no SDD v1.1 — patch v1.1.1

Achados Pré-B → propostas concretas:

| # | Mudança | Origem |
|---|---|---|
| P1 | **Remover linha "Casos Selecionados → tests/fixtures/" em §14.2** | §1 |
| P2 | **REGRA 5.f vira SQL+fuzzy (cardinalidade 598, não texto livre)** — toda R5 fica determinística | §3.1 |
| P3 | **§3.2.13 WFPayment**: adicionar campos `material_servico_num`, `status_os`, `nivel_gerencial`, `malogro`, `tipo_de_lpu`, `tipo_de_despesa`, `valor_total_final`, `valor_unitario_para`, `mes_medicao`, `regional`, `centro_de_custo`, `data_execucao` (12 cols extras das core) | §3.2 |
| P4 | **§3.2.13 raw_extra::jsonb absorve 4 cols vestigiais + ~30 cols opcionais** sem indexar | §3.2 |
| P5 | **§9 prefácio**: filtro padrão `status_os IN ('EXECUTADO','EM EXECUÇÃO') AND nivel_gerencial IN ('Em Pagamento','Medido')` aplicado a todas as regras (parametrizável) | §3.6 |
| P6 | **§9 R6.x**: usar `wf_payment.valor_total_final` (não `valor_total`) — é o pago, após DE-PARA | §3.4 |
| P7 | **§3.2.13 + §9**: queries LEFT JOIN `supplier_bridge`; flag `is_monitored_supplier` no Finding output | §3.7 |
| P8 | **§3.2.13 + ingestão**: campo `data_pedido` é o chave de partição (já é v1.1, confirmado) | §3.5 |

Aplicar como `docs/SDD.md` v1.1.1 em commit dedicado após Pré-C.

---

## 4. Decisões a aplicar no SDD v1.1 (patch v1.1.1)

Após Pré-B completo:

| # | Decisão | Origem |
|---|---|---|
| P1 | **Remover entrada `tests/fixtures/casos_selecionados.csv` do §14.2** | §1 acima — não é ground truth |
| P2 | **Acrescentar nota no §14.2**: "Casos Selecionados é pivot Excel — não ingerir" | idem |
| P3 | **Adicionar ground truth como item explícito no Fase 2** — anotar 50 amostras de findings reais (positivos + negativos) durante Fase 2 | substitui D4 |
| P4 | (Analítico WF — TBD) | §3 acima |

Aplicação: commit dedicado pós-spike PDF (Pré-C) para juntar todas as correções v1.1.1 em um pacote.
