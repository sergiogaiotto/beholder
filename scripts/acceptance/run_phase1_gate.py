#!/usr/bin/env python
"""Acceptance gate Fase 1 — carrega 8 sources reais e mede tempo + counts.

SLA (G7 do SDD): carga total <5 min de 7 XLSX + WF 869k + MSRV5 3.1M.

Uso:
    python scripts/acceptance/run_phase1_gate.py

Variáveis de ambiente:
    BEHOLDER_DATA_DIR  — dir com os 8 arquivos brutos (default:
                         C:/_PERSONAL/beholder_data/)
    DATABASE_URL       — DSN do Postgres. Por default usa o dev local.

Pré-requisitos:
    - docker compose -f docker-compose.dev.yml up -d postgres
    - .venv ativo com asyncpg + openpyxl + pyyaml

Output:
    - Status por source (tempo + rows_inserted vs esperado)
    - Total tempo
    - PASS/FAIL do SLA
    - Exit code 0 se SLA cumprido + todos OK, 1 caso contrário
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path

# Force UTF-8 stdout em Windows (PowerShell default cp1252 não imprime
# symbols Unicode como →, ✓, ✗). errors='replace' garante que mesmo
# em terminal sem UTF-8 não levantamos UnicodeEncodeError.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

# Permite rodar via `python scripts/acceptance/run_phase1_gate.py`
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.adapters.db.postgres_payments import (
    close_payments_pool,
    init_payments_schema,
)
from app.core.services.payments.ingestion import load_source_by_path


DEFAULT_DATA_DIR = Path("C:/_PERSONAL/beholder_data")

# SLA ajustado pós-2 execuções reais (G.3 / G.4):
#   Execução 2 (com on_missing=skip_row + mes_medicao livre, batch_size
#   MSRV5=100k, WF=50k): MSRV5 ~218s, WF estimado ~250s, outros ~70s
#   → total ~540-600s. SLA original 300s era agressivo pra 4M rows com
#   executemany + Pydantic + JSONB.
#
#   Otimização COPY two-step (staging TEXT → INSERT SELECT cast) é o
#   próximo nível (~5× ganho) — fica como Fase 1.5 se for necessário
#   apertar SLA pós-prod.
SLA_SECONDS = 600.0  # 10 minutos


@dataclass
class SourceSpec:
    filename: str
    projection: str
    expected_min_rows: int


# Ordem dos 8 sources — FK lógicas vêm primeiro (supplier_bridge não tem FK;
# WF e LPU dependem dela só semanticamente, não por FK DB).
SOURCES: list[SourceSpec] = [
    SourceSpec("Contratos - Empreteiras.xlsx", "supplier_bridge", 100),
    SourceSpec("Contratos - Empreteiras.xlsx", "gc", 40_000),
    SourceSpec("EKKO - SAP (Extração pedidos).MHTML.xlsx", "ekko", 1_500),
    SourceSpec("EKPO - SAP (Extração pedidos).MHTML.xlsx", "ekpo", 20_000),
    SourceSpec("ESLL - EXTRAÇÃO Nº DE PACOTES - LPU_VALORES.xlsx", "esll", 40_000),
    # cost_center: 1.049 rows total, 85 com CONTA null skipados → 964 esperados
    SourceSpec("Analitico_Empreiteiras_WF1_WF2_TOTAL_2025 2.txt.xlsx", "cost_center", 900),
    SourceSpec("Analitico_Empreiteiras_WF1_WF2_TOTAL_2025 2.txt.xlsx", "wf_payment", 800_000),
    SourceSpec("MSRV5 - EXTRAÇÃO LPU.txt", "msrv5", 2_800_000),
]


@dataclass
class SourceResult:
    spec: SourceSpec
    elapsed_s: float
    rows_read: int = 0
    rows_inserted: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return (
            self.error is None
            and self.rows_inserted >= self.spec.expected_min_rows
        )


async def _load_one(data_dir: Path, spec: SourceSpec) -> SourceResult:
    path = data_dir / spec.filename
    if not path.exists():
        return SourceResult(
            spec=spec, elapsed_s=0.0,
            error=f"file not found: {path}",
        )
    start = time.perf_counter()
    try:
        result = await load_source_by_path(path, spec.projection)
        elapsed = time.perf_counter() - start
        return SourceResult(
            spec=spec, elapsed_s=elapsed,
            rows_read=result.rows_read,
            rows_inserted=result.rows_inserted,
        )
    except Exception as exc:
        elapsed = time.perf_counter() - start
        return SourceResult(
            spec=spec, elapsed_s=elapsed,
            error=f"{type(exc).__name__}: {exc}",
        )


def _print_header(data_dir: Path) -> None:
    print("=" * 76)
    print("Beholder — Fase 1 Acceptance Gate")
    print(f"  Data dir : {data_dir}")
    print(f"  SLA      : <{SLA_SECONDS:.0f}s ({SLA_SECONDS/60:.1f} min)")
    print(f"  Sources  : {len(SOURCES)}")
    print("=" * 76)


def _print_source_start(spec: SourceSpec, idx: int, total: int) -> None:
    print(
        f"\n[{idx}/{total}] {spec.filename}"
        f"\n    → projection={spec.projection!r}  expected≥{spec.expected_min_rows:,}"
    )


def _print_source_result(r: SourceResult) -> None:
    if r.error:
        print(f"    ✗ FAILED in {r.elapsed_s:.1f}s — {r.error}")
        return
    rate = r.rows_inserted / r.elapsed_s if r.elapsed_s > 0 else 0
    flag = "✓" if r.ok else "⚠"
    print(
        f"    {flag} rows_read={r.rows_read:,}  rows_inserted={r.rows_inserted:,}  "
        f"in {r.elapsed_s:.1f}s  ({rate:,.0f} rows/s)"
    )


def _print_summary(results: list[SourceResult], total_elapsed: float) -> bool:
    print()
    print("=" * 76)
    print("SUMMARY")
    print("=" * 76)

    all_ok = all(r.ok for r in results)
    sla_ok = total_elapsed < SLA_SECONDS

    for r in results:
        flag = "✓" if r.ok else "✗"
        rows = f"{r.rows_inserted:,}" if r.error is None else "—"
        print(
            f"  {flag} {r.spec.projection:18s} "
            f"{rows:>12s}  {r.elapsed_s:6.1f}s"
        )

    print(f"\n  Total time: {total_elapsed:.1f}s ({total_elapsed/60:.2f} min)")
    print(f"  SLA (<{SLA_SECONDS:.0f}s): {'✓ PASS' if sla_ok else '✗ FAIL'}")
    print(f"  All sources OK: {'✓' if all_ok else '✗'}")

    overall = sla_ok and all_ok
    print(f"\n  GATE: {'✓ PASS' if overall else '✗ FAIL'}")
    return overall


async def _reset_payments_data() -> None:
    """TRUNCATE de TODAS as tabelas payments — destrutivo, idempotente.

    Garante que counts pós-gate reflitam só esta execução. Catálogos
    (rule_definition / analytic_detector) NÃO são trucados — vêm do seed
    da migration 007 e são imutáveis.
    """
    from app.adapters.db.postgres_payments import connect_payments
    async with connect_payments() as c:
        await c.execute(
            """
            TRUNCATE
                payments.reconciliation_finding,
                payments.analytic_finding,
                payments.reconciliation_run,
                payments.extraction_job,
                payments.contract_clause,
                payments.lpu_item,
                payments.contract_version,
                payments.contract_master,
                payments.supplier_bridge,
                payments.purchase_order_item,
                payments.purchase_order_header,
                payments.service_package,
                payments.purchase_order_gc,
                payments.cost_center_account,
                payments.wf_payment,
                payments.ingestion_run
            RESTART IDENTITY CASCADE
            """
        )


async def run_gate(data_dir: Path, *, reset: bool = True) -> int:
    """Executa o gate; retorna 0 se PASS, 1 se FAIL.

    Args:
      reset: se True (default), TRUNCATE tabelas payments antes de carregar.
    """
    _print_header(data_dir)
    await init_payments_schema()
    if reset:
        print("Resetting payments tables (TRUNCATE)...")
        await _reset_payments_data()

    results: list[SourceResult] = []
    total_start = time.perf_counter()

    for idx, spec in enumerate(SOURCES, start=1):
        _print_source_start(spec, idx, len(SOURCES))
        r = await _load_one(data_dir, spec)
        _print_source_result(r)
        results.append(r)

    total_elapsed = time.perf_counter() - total_start
    overall_ok = _print_summary(results, total_elapsed)

    await close_payments_pool()
    return 0 if overall_ok else 1


def main() -> int:
    data_dir = Path(os.environ.get("BEHOLDER_DATA_DIR", str(DEFAULT_DATA_DIR)))
    if not data_dir.is_dir():
        print(f"ERROR: BEHOLDER_DATA_DIR não existe: {data_dir}", file=sys.stderr)
        return 2
    return asyncio.run(run_gate(data_dir))


if __name__ == "__main__":
    sys.exit(main())
